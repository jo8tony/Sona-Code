"""落盘存储：calls/{date}/{id}.json + index/{date}.jsonl。

写操作（write_partial/finalize）吞掉一切异常并 warning——记录失败
绝不影响代理转发；读操作异常正常抛出。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from sona_code.recording.models import date_from_call_id
from sona_code.recording.parse import first_user_preview

logger = logging.getLogger("sona_code")

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class CallStore:
    def __init__(self, records_dir: Path):
        self.records_dir = Path(records_dir)
        self.calls_dir = self.records_dir / "calls"
        self.index_dir = self.records_dir / "index"
        self.calls_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- internal
    @staticmethod
    def _date_of(rec: dict) -> str:
        d = date_from_call_id(str(rec.get("id", "")))
        if d:
            return d
        started = rec.get("started_at")
        if started:
            try:
                return datetime.fromisoformat(started).astimezone().strftime("%Y-%m-%d")
            except ValueError:
                pass
        return datetime.now().astimezone().strftime("%Y-%m-%d")

    def _calls_date_dir(self, rec: dict) -> Path:
        d = self.calls_dir / self._date_of(rec)
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ----------------------------------------------------------------- write
    def write_partial(self, rec: dict) -> None:
        """请求已发出前的占位记录：calls/{date}/{id}.partial.json。"""
        try:
            p = self._calls_date_dir(rec) / f"{rec['id']}.partial.json"
            _atomic_write(p, json.dumps(rec, indent=2, ensure_ascii=False))
        except Exception:
            logger.warning("写 partial 记录失败 id=%s", rec.get("id"), exc_info=True)

    def finalize(self, rec: dict) -> None:
        """定稿：写最终 JSON（原子替换）、清理 .partial、追加当日索引行。"""
        try:
            d = self._calls_date_dir(rec)
            _atomic_write(d / f"{rec['id']}.json", json.dumps(rec, indent=2, ensure_ascii=False))
            partial = d / f"{rec['id']}.partial.json"
            if partial.exists():
                partial.unlink()
            usage = rec.get("usage") or {}
            req_parsed = ((rec.get("request") or {}).get("parsed")) or {}
            line = {
                "id": rec.get("id"),
                "started_at": rec.get("started_at"),
                "model": rec.get("model"),
                "path": (rec.get("request") or {}).get("path"),
                "status_code": (rec.get("response") or {}).get("status_code"),
                "duration_ms": rec.get("duration_ms"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "upstream_name": rec.get("upstream_name"),
                "status": rec.get("status"),
                "session_key": rec.get("session_key"),
                "preview": first_user_preview(req_parsed.get("messages")),
            }
            for key in ("protocol", "response_id", "previous_response_id", "history_incomplete"):
                if rec.get(key) is not None:
                    line[key] = rec[key]
            date = self._date_of(rec)
            self.index_dir.mkdir(parents=True, exist_ok=True)
            with open(self.index_dir / f"{date}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except Exception:
            logger.warning("finalize 记录失败 id=%s", rec.get("id"), exc_info=True)

    # ------------------------------------------------------------------ read
    def find_response(self, response_id: str, upstream_name: str) -> dict | None:
        """Resolve captured Responses chains in the background, scoped to provider."""
        for date in self.available_dates():
            try:
                rows = self.read_index(date)
            except (OSError, ValueError):
                continue
            for row in reversed(rows):
                if row.get("response_id") == response_id and row.get("upstream_name") == upstream_name:
                    return self.load_call(row["id"], date)
        return None

    def load_call(self, call_id: str, date: str | None = None) -> dict | None:
        """读取单条记录；无 date 时扫描所有日期目录。"""
        if date is not None:
            p = self.calls_dir / date / f"{call_id}.json"
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        for d in self.available_dates():
            p = self.calls_dir / d / f"{call_id}.json"
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        return None

    def read_index(self, date: str) -> list[dict]:
        """当日索引行列表（读异常正常抛）。"""
        p = self.index_dir / f"{date}.jsonl"
        out: list[dict] = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def available_dates(self) -> list[str]:
        """有 calls 文件的日期，降序。"""
        if not self.calls_dir.exists():
            return []
        dates = [
            d.name
            for d in self.calls_dir.iterdir()
            if d.is_dir() and _DATE_RE.fullmatch(d.name)
        ]
        return sorted(dates, reverse=True)

    # ---------------------------------------------------------------- delete
    def _remove_index_line(self, date: str, call_id: str) -> None:
        """从当日索引 JSONL 中移除 id 匹配的行（原子重写）。"""
        path = self.index_dir / f"{date}.jsonl"
        if not path.exists():
            return
        kept: list[str] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                except Exception:
                    kept.append(stripped)  # 坏行原样保留
                    continue
                if isinstance(row, dict) and row.get("id") == call_id:
                    continue
                kept.append(stripped)
        _atomic_write(path, "\n".join(kept) + ("\n" if kept else ""))

    def delete_call(self, call_id: str, date: str | None = None) -> bool:
        """删除单条记录（final + partial + 索引行）；返回是否删到东西。"""
        dates = [date] if date is not None else self.available_dates()
        deleted = False
        for d in dates:
            dir_ = self.calls_dir / d
            for name in (f"{call_id}.json", f"{call_id}.partial.json"):
                p = dir_ / name
                if p.exists():
                    try:
                        p.unlink()
                        deleted = True
                    except OSError:
                        logger.warning("删除记录文件失败 %s", p, exc_info=True)
            if deleted:
                self._remove_index_line(d, call_id)
                # 目录空了顺手移除（index 由 available_dates 语义决定不删）
                try:
                    if dir_.exists() and not any(dir_.iterdir()):
                        dir_.rmdir()
                except OSError:
                    pass
                break
        return deleted

    def _count_calls(self, date: str) -> int:
        """当日最终记录数（.json 且非 .partial.json）。"""
        d = self.calls_dir / date
        if not d.exists():
            return 0
        return sum(
            1
            for p in d.iterdir()
            if p.is_file() and p.suffix == ".json" and not p.name.endswith(".partial.json")
        )

    def delete_date(self, date: str) -> int:
        """删除某日期全部记录（calls 目录 + 索引文件）；返回删除的记录数。"""
        if not _DATE_RE.fullmatch(date):
            raise ValueError(f"非法日期目录名: {date!r}")
        n = self._count_calls(date)
        for target in (self.calls_dir / date, self.index_dir / f"{date}.jsonl"):
            if not target.exists():
                continue
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            except OSError:
                logger.warning("删除 %s 失败", target, exc_info=True)
        return n

    def delete_all(self) -> dict:
        """清空全部记录；返回 {dates, calls}。"""
        dates = self.available_dates()
        calls = 0
        for d in dates:
            calls += self._count_calls(d)
            self.delete_date(d)
        return {"dates": len(dates), "calls": calls}

    def cleanup_older_than(self, retention_days: int) -> list[str]:
        """保留策略：删除早于今天 N 天前的全部日期记录；返回被删日期。"""
        if retention_days <= 0:
            return []
        cutoff = (datetime.now().astimezone() - timedelta(days=retention_days)).date()
        removed: list[str] = []
        for d in self.available_dates():
            try:
                day = date.fromisoformat(d)
            except ValueError:
                continue
            if day < cutoff:
                self.delete_date(d)
                removed.append(d)
        return removed

    # ----------------------------------------------------------------- stats
    def stats(self) -> dict:
        """存储统计：每日期文件数与磁盘占用。"""
        def _dir_size(p: Path) -> int:
            total = 0
            for f in p.rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
            return total

        per_date = []
        total_bytes = 0
        total_files = 0
        for d in self.available_dates():
            calls_dir = self.calls_dir / d
            index_p = self.index_dir / f"{d}.jsonl"
            size = (_dir_size(calls_dir) if calls_dir.exists() else 0) + (
                index_p.stat().st_size if index_p.exists() else 0
            )
            files = sum(1 for f in calls_dir.iterdir() if f.is_file()) if calls_dir.exists() else 0
            per_date.append(
                {
                    "date": d,
                    "calls": self._count_calls(d),
                    "files": files,
                    "bytes": size,
                }
            )
            total_bytes += size
            total_files += files
        return {"total_bytes": total_bytes, "total_files": total_files, "dates": per_date}
