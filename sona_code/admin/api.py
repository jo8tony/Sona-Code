"""管理 API 实现：overview / calls / trajectory / settings / meta 等，挂载到 {admin_prefix}/api。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from sona_code import __version__
from sona_code.admin.models import public_config
from sona_code.config import AppConfig, resolved_records_dir
from sona_code.admin.opencode_config import (
    import_global_config,
    preview_global_import,
    read_global_config,
    validate_jsonc,
    write_global_config,
)
from sona_code.recording.parse import diff_new_messages, message_digest, system_text

logger = logging.getLogger("sona_code")

router = APIRouter()

# 需重启才能生效的字段（server 段持久化但不热应用）
RESTART_FIELDS = ["server.host", "server.port", "server.admin_prefix"]


# ------------------------------------------------------------------ 行级容错读取
def _load_index_rows(store, dates: list[str]) -> list[tuple[str, dict]]:
    """逐行读取多个日期的索引 JSONL，返回 (来源日期, 行)；坏行/坏文件跳过并 warning。"""
    pairs: list[tuple[str, dict]] = []
    for d in dates:
        path = store.index_dir / f"{d}.jsonl"
        try:
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        logger.warning("索引 %s 第 %d 行 JSON 损坏，已跳过", path.name, lineno)
                        continue
                    if not isinstance(row, dict):
                        logger.warning("索引 %s 第 %d 行非 JSON 对象，已跳过", path.name, lineno)
                        continue
                    pairs.append((d, row))
        except FileNotFoundError:
            continue
        except Exception:
            logger.warning("读取索引文件 %s 失败，已跳过", path, exc_info=True)
    return pairs


def _num(row: dict, key: str) -> float:
    """缺字段/类型不符按 0 计。"""
    v = row.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    return v


def _sort_key(row: dict) -> str:
    """按 started_at 字符串排序（ISO8601 可字典序比较，缺失排最后）。"""
    return str(row.get("started_at") or "")


# ---------------------------------------------------------------------- /overview
@router.get("/overview")
def overview(request: Request) -> dict:
    store = request.app.state.runtime.store
    pairs = _load_index_rows(store, store.available_dates())
    rows = [r for _, r in pairs]

    total = len(rows)
    errors = sum(1 for r in rows if r.get("status") == "error")
    durations = [
        d
        for d in (r.get("duration_ms") for r in rows)
        if isinstance(d, (int, float)) and not isinstance(d, bool)
    ]

    by_model: dict[str, dict] = {}
    for r in rows:
        m = str(r.get("model") or "")
        agg = by_model.setdefault(
            m, {"model": m, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        )
        agg["calls"] += 1
        agg["prompt_tokens"] += int(_num(r, "prompt_tokens"))
        agg["completion_tokens"] += int(_num(r, "completion_tokens"))

    # 近 14 天窗口（升序、空日期补零）；窗口外的行只计入总计
    today = datetime.now().astimezone().date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    by_day = {
        d: {"date": d, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "errors": 0}
        for d in days
    }
    for d, r in pairs:
        slot = by_day.get(d)
        if slot is None:
            continue
        slot["calls"] += 1
        slot["prompt_tokens"] += int(_num(r, "prompt_tokens"))
        slot["completion_tokens"] += int(_num(r, "completion_tokens"))
        if r.get("status") == "error":
            slot["errors"] += 1

    return {
        "total_calls": total,
        "total_prompt_tokens": int(sum(_num(r, "prompt_tokens") for r in rows)),
        "total_completion_tokens": int(sum(_num(r, "completion_tokens") for r in rows)),
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "error_rate": round(errors / total, 4) if total else 0.0,
        "by_model": list(by_model.values()),
        "by_day": list(by_day.values()),
        "recent": sorted(rows, key=_sort_key, reverse=True)[:10],
    }


# ------------------------------------------------------------------------- /calls
@router.get("/calls")
def list_calls(
    request: Request,
    date: str = Query("all", pattern=r"^(all|\d{4}-\d{2}-\d{2})$"),
    model: str | None = Query(None),
    status: str | None = Query(None, pattern=r"^(ok|error|client_aborted)$"),
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    store = request.app.state.runtime.store
    dates = store.available_dates() if date == "all" else [date]
    rows = [r for _, r in _load_index_rows(store, dates)]

    if model is not None:
        rows = [r for r in rows if r.get("model") == model]
    if status is not None:
        rows = [r for r in rows if r.get("status") == status]
    if q:
        needle = q.lower()
        rows = [
            r
            for r in rows
            if any(needle in str(r.get(k) or "").lower() for k in ("id", "path", "model"))
        ]

    rows.sort(key=_sort_key, reverse=True)
    start = (page - 1) * page_size
    return {
        "items": rows[start : start + page_size],
        "total": len(rows),
        "page": page,
        "page_size": page_size,
    }


@router.get("/calls/{call_id}")
def get_call(call_id: str, request: Request):
    rec = request.app.state.runtime.store.load_call(call_id)
    if rec is None:
        return JSONResponse(status_code=404, content={"detail": "call not found"})
    return rec


@router.delete("/calls/{call_id}")
def delete_call(call_id: str, request: Request):
    store = request.app.state.runtime.store
    if not store.delete_call(call_id):
        return JSONResponse(status_code=404, content={"detail": "call not found"})
    return {"ok": True, "deleted": 1}


# ------------------------------------------------------------------ /records 清理
@router.get("/records/stats")
def records_stats(request: Request) -> dict:
    """存储统计：每日期记录数 / 文件数 / 磁盘占用。"""
    return request.app.state.runtime.store.stats()


@router.delete("/records/date/{date}")
def records_delete_date(date: str, request: Request):
    store = request.app.state.runtime.store
    if date not in store.available_dates():
        return JSONResponse(status_code=404, content={"detail": f"日期 {date} 无记录"})
    n = store.delete_date(date)
    return {"ok": True, "deleted": n}


@router.delete("/records/all")
def records_delete_all(request: Request):
    r = request.app.state.runtime.store.delete_all()
    return {"ok": True, **r}


@router.post("/records/cleanup")
def records_cleanup(request: Request) -> dict:
    """手动执行保留策略清理（retention_days>0 时删除更早日期）。"""
    cfg: AppConfig = request.app.state.runtime.config
    days = cfg.recording.retention_days
    if days <= 0:
        return {"ok": True, "retention_days": days, "removed_dates": [], "deleted": 0}
    removed = request.app.state.runtime.store.cleanup_older_than(days)
    return {"ok": True, "retention_days": days, "removed_dates": removed, "deleted": len(removed)}


# ------------------------------------------------------------------ /trajectory
def _session_group_key(row: dict) -> str:
    """无会话归属的旧记录/非对话调用：每条自成一组的 solo 键。"""
    return str(row.get("session_key") or f"solo:{row.get('id')}")


@router.get("/trajectory/sessions")
def trajectory_sessions(
    request: Request,
    date: str = Query("all", pattern=r"^(all|\d{4}-\d{2}-\d{2})$"),
    q: str | None = Query(None),
) -> dict:
    """会话列表：按 session_key 聚合 index 行。"""
    store = request.app.state.runtime.store
    dates = store.available_dates() if date == "all" else [date]
    rows = [r for _, r in _load_index_rows(store, dates)]

    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(_session_group_key(r), []).append(r)

    sessions = []
    for key, grp in groups.items():
        grp.sort(key=_sort_key)
        first, last = grp[0], grp[-1]
        preview = next((str(r.get("preview")) for r in grp if r.get("preview")), "")
        if not preview:
            preview = str(first.get("path") or first.get("id") or "")
        sessions.append(
            {
                "session_key": key,
                "solo": key.startswith("solo:"),
                "model": last.get("model"),
                "calls": len(grp),
                "prompt_tokens": sum(int(_num(r, "prompt_tokens")) for r in grp),
                "completion_tokens": sum(int(_num(r, "completion_tokens")) for r in grp),
                "total_tokens": sum(int(_num(r, "total_tokens")) for r in grp),
                "first_started_at": first.get("started_at"),
                "last_started_at": last.get("started_at"),
                "last_status": last.get("status"),
                "preview": preview[:80],
            }
        )

    if q:
        needle = q.lower()
        sessions = [
            s
            for s in sessions
            if needle in s["preview"].lower()
            or needle in str(s.get("model") or "").lower()
            or needle in s["session_key"].lower()
        ]
    sessions.sort(key=lambda s: str(s.get("last_started_at") or ""), reverse=True)
    return {"sessions": sessions, "total": len(sessions)}


def _turn_search_text(turn: dict) -> str:
    parts: list[str] = [
        json.dumps(turn.get("new_messages") or [], ensure_ascii=False),
        json.dumps(turn.get("response_message") or {}, ensure_ascii=False),
        str(turn.get("model") or ""),
    ]
    return " ".join(parts).lower()


@router.get("/trajectory/sessions/{key}")
def trajectory_session_detail(key: str, request: Request, q: str | None = Query(None)) -> dict:
    """会话轨迹：调用序列 → Turn 账本（增量消息 + 响应 + 指标 + 累计用量）。"""
    store = request.app.state.runtime.store
    rows = [r for _, r in _load_index_rows(store, store.available_dates())]
    if key.startswith("solo:"):
        rows = [r for r in rows if f"solo:{r.get('id')}" == key]
    else:
        rows = [r for r in rows if r.get("session_key") == key]
    rows.sort(key=_sort_key)

    turns: list[dict] = []
    prev_digests: list[str] | None = None
    cum = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for row in rows:
        rec = store.load_call(str(row.get("id")))
        if rec is None:
            continue
        req = rec.get("request") or {}
        parsed_req = req.get("parsed") or {}
        messages = parsed_req.get("messages")
        new_msgs = diff_new_messages(prev_digests, messages)
        if isinstance(messages, list):
            prev_digests = [message_digest(m) for m in messages]

        resp = rec.get("response") or {}
        parsed_resp = resp.get("parsed") or {}
        usage = rec.get("usage") or {}
        for k in cum:
            v = usage.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cum[k] += int(v)

        turns.append(
            {
                "protocol": rec.get("protocol"),
                "history_incomplete": rec.get("history_incomplete", False),
                "previous_response_id": rec.get("previous_response_id"),
                "turn_no": len(turns) + 1,
                "call_id": rec.get("id"),
                "started_at": rec.get("started_at"),
                "status": rec.get("status"),
                "status_code": resp.get("status_code"),
                "model": rec.get("model"),
                "stream": rec.get("stream"),
                "duration_ms": rec.get("duration_ms"),
                "ttft_ms": resp.get("ttft_ms"),
                "decode_ms": resp.get("decode_ms"),
                "first_byte_ms": resp.get("first_byte_ms"),
                "tokens_per_sec": resp.get("decode_tokens_per_sec"),
                "new_messages": new_msgs,
                "response_message": parsed_resp.get("message"),
                "finish_reason": parsed_resp.get("finish_reason"),
                "parse_error": parsed_resp.get("parse_error"),
                "usage": usage or None,
                "cumulative_usage": dict(cum),
                "tools": parsed_req.get("tools"),
                "params": parsed_req.get("params"),
                # —— Inspector Tab 化所需的小字段（数据已在手，边际成本极低）——
                "system": system_text(messages),
                "messages_count": len(messages) if isinstance(messages, list) else 0,
                "upstream_name": rec.get("upstream_name"),
                "method": req.get("method"),
                "path": req.get("path"),
                "request_headers": req.get("headers"),
                "response_headers": resp.get("headers"),
                "chunk_count": resp.get("chunk_count"),
                "error": rec.get("error"),
            }
        )

    if q:
        needle = q.lower()
        turns = [t for t in turns if needle in _turn_search_text(t)]

    first = rows[0].get("started_at") if rows else None
    last = rows[-1].get("started_at") if rows else None
    return {
        "session_key": key,
        "model": rows[-1].get("model") if rows else None,
        "calls": len(turns),
        "first_started_at": first,
        "last_started_at": last,
        "preview": next((str(r.get("preview")) for r in rows if r.get("preview")), ""),
        "cumulative_usage": dict(cum),
        "turns": turns,
    }


# ---------------------------------------------------------------------- /settings
@router.get("/settings")
def get_settings(request: Request) -> dict:
    cfg: AppConfig = request.app.state.runtime.config
    data = public_config(cfg)
    data["server"] = request.app.state.runtime.pending_server.model_dump()
    return {
        "config": data,
        "config_path": request.app.state.config_path,
        "records_dir": str(resolved_records_dir(cfg)),
        "restart_fields": RESTART_FIELDS,
    }


@router.put("/settings")
async def put_settings(new_cfg: dict, request: Request) -> dict:
    from sona_code.admin.models import merge_providers, validate_config
    from sona_code.admin.model_routes import commit_config
    from sona_code.workspace.manager import WorkspaceError

    runtime = request.app.state.runtime
    async with runtime.config_lock:
        async def apply() -> dict:
            data = runtime.config.model_dump()
            for section in ("server", "outbound", "recording", "terminal", "model_settings"):
                if section in new_cfg:
                    if not isinstance(new_cfg[section], dict):
                        raise HTTPException(422, "配置段必须是对象")
                    updates = dict(new_cfg[section])
                    if section == "terminal" and isinstance(updates.get("inject_env"), dict):
                        updates["inject_env"] = {k: data["terminal"]["inject_env"].get(k, "") if v == "[REDACTED]" else v
                                                 for k, v in updates["inject_env"].items()}
                    data[section].update(updates)
            if "upstreams" in new_cfg:
                if not isinstance(new_cfg["upstreams"], list):
                    raise HTTPException(422, "提供商必须是列表")
                data["upstreams"] = merge_providers(new_cfg["upstreams"], runtime.config)
            if "default_upstream" in new_cfg:
                data["default_upstream"] = new_cfg["default_upstream"]
            result = await commit_config(request, validate_config(data), server_explicit="server" in new_cfg)
            return {"ok": result["ok"], "restart_required": result["restart_required"]}
        try:
            return await runtime.workspace.update_configuration(apply, "设置")
        except WorkspaceError as exc:
            raise HTTPException(exc.status, exc.detail) from None


@router.get("/settings/opencode-config")
def get_opencode_config() -> dict:
    path, content, revision = read_global_config()
    return {"path": str(path), "content": content, "revision": revision}


class OpenCodeConfigUpdate(BaseModel):
    content: str = Field(max_length=1024 * 1024)
    revision: str


@router.put("/settings/opencode-config")
def put_opencode_config(body: OpenCodeConfigUpdate) -> dict:
    from sona_code.admin.opencode_config import CONFIG_LOCK

    with CONFIG_LOCK:
        path, _, revision = read_global_config()
        if body.revision != revision:
            raise HTTPException(status_code=409, detail="配置文件已在别处修改，请重新加载后再保存")
        try:
            validate_jsonc(body.content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": True, "path": str(path), "revision": write_global_config(path, body.content)}



@router.get("/settings/opencode-import")
def get_opencode_import() -> dict:
    """预览可从用户全局 OpenCode 目录导入的允许列表，不返回文件内容。"""
    return preview_global_import()


@router.post("/settings/opencode-import")
def post_opencode_import() -> dict:
    """复制缺失的配置、扩展与凭据；已有目标永不覆盖。"""
    return import_global_config()


class TestUpstreamBody(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


@router.post("/settings/test-upstream")
async def test_upstream(body: TestUpstreamBody, request: Request) -> dict:
    cfg: AppConfig = request.app.state.runtime.config
    base_url = body.base_url
    api_key = body.api_key
    if body.name is not None:
        up = next((u for u in cfg.upstreams if u.name == body.name), None)
        if up is None:
            return JSONResponse(status_code=400, content={"detail": f"upstream '{body.name}' 不存在"})
        base_url = base_url or up.base_url
        if api_key is None:
            api_key = up.api_key
    if not base_url:
        return JSONResponse(status_code=400, content={"detail": "缺少 name 或 base_url"})

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    url = base_url.rstrip("/") + "/models"
    t0 = time.perf_counter()
    status_code = None
    error = None
    try:
        # 独立直连客户端：不受出站代理配置影响
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.get(url, headers=headers)
        status_code = resp.status_code
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    return {
        "ok": error is None,
        "status_code": status_code,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        "error": error,
    }


# ------------------------------------------------------------------------- /meta
@router.get("/meta")
def meta(request: Request) -> dict:
    cfg: AppConfig = request.app.state.runtime.config
    return {
        "version": __version__,
        "admin_prefix": cfg.server.admin_prefix,
        "config_path": request.app.state.config_path,
        "records_dir": str(resolved_records_dir(cfg)),
    }
