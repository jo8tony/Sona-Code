"""持久化终端项目目录，与短暂的 PTY 会话分开存储。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path


class TerminalProjectStore:
    def __init__(self, config_path: str):
        self.path = Path(config_path).expanduser().resolve().parent / "terminal-projects.json"
        self._lock = threading.Lock()

    def _read(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("终端项目文件格式错误")
        return [item for item in data if isinstance(item, dict) and isinstance(item.get("path"), str)]

    def list(self) -> list[dict[str, str]]:
        with self._lock:
            return self._read()

    def _write(self, items: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".terminal-projects-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def add(self, path: str, kind: str) -> dict[str, str]:
        resolved = str(Path(path).expanduser().resolve())
        item = {"path": resolved, "name": Path(resolved).name or resolved, "kind": kind}
        with self._lock:
            items = self._read()
            previous = next((old for old in items if old["path"] == resolved), None)
            if previous:
                item["name"] = previous.get("name") or item["name"]
            items = [old for old in items if old["path"] != resolved]
            items.insert(0, item)
            self._write(items)
        return item

    def rename(self, path: str, name: str) -> dict[str, str] | None:
        resolved = str(Path(path).expanduser().resolve())
        with self._lock:
            items = self._read()
            for item in items:
                if item["path"] == resolved:
                    item["name"] = name
                    self._write(items)
                    return item
        return None

    def delete(self, path: str) -> bool:
        resolved = str(Path(path).expanduser().resolve())
        with self._lock:
            items = self._read()
            kept = [item for item in items if item["path"] != resolved]
            if len(kept) == len(items):
                return False
            self._write(kept)
            return True
