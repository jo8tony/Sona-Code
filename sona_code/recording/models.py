"""调用记录数据模型（Pydantic）。

时间字段统一存 ISO8601 字符串（本地时区带偏移），保证 model_dump()
的结果可直接 json.dumps。
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def now_local() -> datetime:
    """当前本地时间（带时区偏移）。"""
    return datetime.now().astimezone()


def now_iso() -> str:
    """当前本地时间 ISO8601 字符串。"""
    return now_local().isoformat()


def new_call_id() -> str:
    """call_id：c{YYYYMMDD}_{HHMMSS}_{6位hex}（本地时间）。"""
    now = now_local()
    return f"c{now:%Y%m%d}_{now:%H%M%S}_{secrets.token_hex(3)}"


def date_from_call_id(call_id: str) -> str | None:
    """从 call_id 解析 YYYY-MM-DD；格式不符返回 None。"""
    if len(call_id) >= 9 and call_id.startswith("c") and call_id[1:9].isdigit():
        d = call_id[1:9]
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return None


class ParsedRequestInfo(BaseModel):
    """请求体解析结果（后台解析，不影响转发）。"""

    model: str | None = None
    stream: bool = False
    messages: list[Any] | None = None
    tools: list[Any] | None = None
    protocol: str | None = None
    previous_response_id: str | None = None
    params: dict[str, Any] | None = None  # temperature/max_tokens 等其余字段


class ParsedResponseInfo(BaseModel):
    """响应体解析结果（后台解析，不影响转发）。"""

    protocol: str | None = None
    response_id: str | None = None
    unknown_events: list[dict] | None = None
    message: dict[str, Any] | None = None  # 组装后的 assistant 消息
    finish_reason: str | None = None
    parse_error: str | None = None


class RequestInfo(BaseModel):
    method: str
    path: str
    query: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] | None = None
    body: Any = None  # JSON 对象或字符串
    body_truncated: bool = False
    parsed: ParsedRequestInfo | None = None


class ResponseInfo(BaseModel):
    status_code: int | None = None
    headers: dict[str, str] | None = None
    first_byte_ms: float | None = None
    ttft_ms: float | None = None
    chunk_count: int = 0
    decode_ms: float | None = None
    decode_tokens_per_sec: float | None = None
    content: Any = None
    raw_chunks: list[str] | None = None
    body_truncated: bool = False
    parsed: ParsedResponseInfo | None = None


class UsageInfo(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None


class ErrorInfo(BaseModel):
    type: str
    message: str


class ClientInfo(BaseModel):
    user_agent: str = ""


class CallRecord(BaseModel):
    id: str
    status: Literal["ok", "error", "client_aborted"]
    started_at: str
    finished_at: str | None = None
    duration_ms: float | None = None
    upstream_name: str
    upstream_url: str
    model: str | None = None
    stream: bool = False
    protocol: str | None = None
    response_id: str | None = None
    previous_response_id: str | None = None
    history_incomplete: bool = False
    session_key: str | None = None  # 会话归属（后台解析计算）
    request: RequestInfo
    response: ResponseInfo = Field(default_factory=ResponseInfo)
    usage: UsageInfo | None = None
    error: ErrorInfo | None = None
    client: ClientInfo = Field(default_factory=ClientInfo)
