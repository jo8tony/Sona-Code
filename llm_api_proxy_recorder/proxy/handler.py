"""透明转发核心：无损转发（含 SSE 流式）+ 旁路捕获，解析与落盘全部在后台完成。

最高优先级是无损透明：转发循环内只做逐块转发与字节捕获（带时间戳），
不做任何解析/序列化；记录逻辑绝不改变转发语义，记录失败绝不影响响应。
"""

from __future__ import annotations

import asyncio
import logging
import time
import zlib
from typing import AsyncIterator

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from llm_api_proxy_recorder.config import RecordingConfig, UpstreamConfig
from llm_api_proxy_recorder.proxy.router import build_upstream_url, resolve_upstream
from llm_api_proxy_recorder.recording.models import (
    CallRecord,
    ClientInfo,
    ErrorInfo,
    ParsedRequestInfo,
    ParsedResponseInfo,
    RequestInfo,
    UsageInfo,
    new_call_id,
    now_iso,
)
from llm_api_proxy_recorder.recording.parse import (
    chunks_to_raw_texts,
    extract_session_header,
    parse_nonsse_body,
    parse_request_body,
    parse_sse_captured,
    session_key_from_header,
    session_key_of,
)
from llm_api_proxy_recorder.recording.redact import redact_headers
from llm_api_proxy_recorder.recording.store import CallStore

logger = logging.getLogger("llm_api_proxy_recorder")

# 请求与响应两侧都剔除的逐跳/长度头（长度由 httpx 按实际 body 重新计算）。
# 这是 HTTP 代理的规范行为；除此之外头原样透传，凭据头默认不做任何改写。
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _build_forward_headers(request: Request, upstream: UpstreamConfig) -> list[tuple[str, str]]:
    """客户端头剔除逐跳头 → （可选）key 注入 → 合并 extra_headers（同名覆盖）。

    key_strategy 默认 keep：头原样透传；仅显式配置 replace 时才改写/注入凭据。
    """
    headers = [
        (k.decode("latin-1"), v.decode("latin-1"))
        for k, v in request.headers.raw
        if k.decode("latin-1").lower() not in HOP_BY_HOP_HEADERS
    ]
    if upstream.key_strategy == "replace" and upstream.api_key:
        lowered = {k.lower() for k, _ in headers}
        if "authorization" in lowered:
            headers = [
                (k, f"Bearer {upstream.api_key}") if k.lower() == "authorization" else (k, v)
                for k, v in headers
            ]
        for hname in ("x-api-key", "api-key"):
            if hname in lowered:
                headers = [
                    (k, upstream.api_key) if k.lower() == hname else (k, v) for k, v in headers
                ]
        if not lowered & {"authorization", "x-api-key", "api-key"}:
            headers.append(("authorization", f"Bearer {upstream.api_key}"))
    for ek, ev in upstream.extra_headers.items():
        headers = [(k, v) for k, v in headers if k.lower() != ek.lower()]
        headers.append((ek, ev))
    if request.url.path.startswith("/managed/"):
        # Managed traffic has deterministic credentials; never forward a native
        # placeholder, client credential or legacy auth header to the upstream.
        headers = [(k, v) for k, v in headers if k.lower() not in {"authorization", "x-api-key", "api-key"}]
        if upstream.api_key:
            headers.append(("authorization", f"Bearer {upstream.api_key}"))
    return headers


def _capture_body_text(body: bytes, rc: RecordingConfig) -> tuple[str, bool]:
    """请求体旁路捕获：超限截断；只存文本，解析交给后台。"""
    limit = int(rc.max_capture_mb * 1024 * 1024)
    if len(body) > limit:
        return body[:limit].decode("utf-8", errors="replace"), True
    return body.decode("utf-8", errors="replace"), False


async def _safe_write_partial(store: CallStore, record: CallRecord) -> None:
    try:
        await asyncio.to_thread(store.write_partial, record.model_dump())
    except Exception:
        logger.warning("写 partial 记录失败 id=%s", record.id, exc_info=True)


# 后台任务引用（防 GC，保证解析落盘完成）
_pending_tasks: set[asyncio.Task] = set()


def _spawn_process_finalize(store: CallStore, record: CallRecord, ctx: dict) -> None:
    """启动后台任务：解析捕获数据 → 组装最终记录 → 落盘。
    绝不阻塞转发/响应路径。"""

    async def _run() -> None:
        try:
            await asyncio.to_thread(_process_and_finalize, store, record, ctx)
        except Exception:
            logger.warning("后台解析/落盘失败 id=%s", record.id, exc_info=True)

    task = asyncio.get_running_loop().create_task(_run())
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


def _process_and_finalize(store: CallStore, record: CallRecord, ctx: dict) -> None:
    """同步后台处理：请求/响应解析、指标计算、会话归属、定稿落盘。"""
    rc: RecordingConfig = ctx["rc"]
    r = record.response

    # ---- 请求体解析 ----
    req_parsed = parse_request_body(record.request.body)
    if req_parsed is not None:
        record.request.parsed = ParsedRequestInfo(**req_parsed)
        record.protocol = req_parsed.get("protocol") or "chat_completions"
        record.previous_response_id = req_parsed.get("previous_response_id")
        if req_parsed["model"]:
            record.model = req_parsed["model"]
        record.stream = req_parsed["stream"]
        record.session_key = session_key_of(req_parsed["model"], req_parsed["messages"])

    # 会话归属头优先（客户端显式强标识）：覆盖内容哈希；
    # 头值取自原始未脱敏 headers（仅内存传递，不落盘），body 不可解析时也能归属。
    header_key = session_key_from_header(ctx.get("session_header"))
    if header_key is not None:
        record.session_key = header_key

    # ---- 时间指标 ----
    t_start = ctx["t_start"]
    t_sent = ctx["t_sent"]
    t_first_byte = ctx.get("t_first_byte")
    t_end = ctx.get("t_end") or time.perf_counter()
    record.finished_at = now_iso()
    record.duration_ms = (t_end - t_start) * 1000
    r.first_byte_ms = (t_first_byte - t_sent) * 1000 if t_first_byte is not None else None

    # ---- 响应体解析 ----
    chunks: list[tuple[float, bytes]] = ctx.get("chunks") or []
    if ctx.get("is_sse"):
        res = parse_sse_captured(chunks, ctx.get("encoding") or "")
        parser = res["parser"]
        r.chunk_count = parser.chunk_count
        r.content = parser.assembled_message()
        record.usage = UsageInfo(**parser.usage) if parser.usage else None
        ttft_at = res["ttft_at"]
        if ttft_at is not None:
            r.ttft_ms = (ttft_at - t_sent) * 1000
            r.decode_ms = (t_end - ttft_at) * 1000
            ct = (parser.usage or {}).get("completion_tokens")
            r.decode_tokens_per_sec = (
                ct / r.decode_ms * 1000
                if isinstance(ct, (int, float)) and r.decode_ms and r.decode_ms > 0
                else None
            )
        if rc.record_raw_chunks:
            r.raw_chunks = chunks_to_raw_texts(chunks)
        if parser.responses:
            record.protocol = "responses"
            record.response_id = parser.responses.response_id
            if not parser.saw_done and not r.body_truncated:
                parser.parse_error = parser.parse_error or "Responses stream ended without a terminal event"
                if record.error is None:
                    record.error = ErrorInfo(type="responses_incomplete_stream", message=parser.parse_error)
                if ctx["status"] == "ok":
                    ctx["status"] = "error"
            if parser.responses.error or parser.responses.status == "failed":
                record.error = ErrorInfo(type="responses_error", message=str((parser.responses.error or {}).get("message", "Responses 请求失败")))
                ctx["status"] = "error"
        r.parsed = ParsedResponseInfo(
            protocol=record.protocol, response_id=record.response_id,
            unknown_events=parser.responses.unknown_events if parser.responses else None,
            message=r.content,
            finish_reason=parser.finish_reason,
            parse_error=parser.parse_error,
        )
        if parser.parse_error and record.error is None:
            record.error = ErrorInfo(type="sse_parse_error", message=parser.parse_error)
    else:
        data = b"".join(c for _, c in chunks)
        encoding = ctx.get("encoding") or ""
        if data and encoding in ("gzip", "x-gzip", "deflate"):
            try:
                d = (
                    zlib.decompressobj(16 + zlib.MAX_WBITS)
                    if encoding != "deflate"
                    else zlib.decompressobj()
                )
                data = d.decompress(data)
            except zlib.error:
                logger.warning("响应体解压失败 id=%s encoding=%s", record.id, encoding)
        res = parse_nonsse_body(data)
        r.chunk_count = ctx.get("net_chunk_count") or len(chunks)
        r.content = res["content"]
        record.usage = UsageInfo(**res["usage"]) if res["usage"] else None
        record.response_id = res.get("response_id")
        record.protocol = res.get("protocol") or record.protocol
        if res.get("error") or (record.protocol == "responses" and res.get("finish_reason") == "failed"):
            record.error = ErrorInfo(type="responses_error", message=str((res.get("error") or {}).get("message", "Responses 请求失败")))
            ctx["status"] = "error"
        r.parsed = ParsedResponseInfo(
            protocol=record.protocol, response_id=record.response_id,
            message=res["message"], finish_reason=res["finish_reason"], parse_error=None
        )

    if record.previous_response_id:
        parent = store.find_response(record.previous_response_id, record.upstream_name)
        if parent and record.request.parsed:
            previous_messages = ((parent.get("request") or {}).get("parsed") or {}).get("messages") or []
            previous_reply = ((parent.get("response") or {}).get("parsed") or {}).get("message")
            current_messages = record.request.parsed.messages or []
            if (previous_messages and current_messages and current_messages[0].get("role") == "system"
                    and current_messages[0] == previous_messages[0]):
                current_messages = current_messages[1:]
            record.request.parsed.messages = [*previous_messages, *([previous_reply] if previous_reply else []), *current_messages]
            if not header_key:
                record.session_key = parent.get("session_key") or record.session_key
            record.history_incomplete = bool(parent.get("history_incomplete"))
        else:
            record.history_incomplete = True
    record.status = ctx["status"]
    store.finalize(record.model_dump())


async def proxy_endpoint(request: Request):
    runtime = request.app.state.runtime
    cfg = runtime.provider_config()  # 每次请求现取，支持远端目录热更新
    store: CallStore = runtime.store
    rc = cfg.recording
    t_start = time.perf_counter()
    limit_bytes = int(rc.max_capture_mb * 1024 * 1024)

    upstream, upstream_path = resolve_upstream(request.url.path, cfg)
    url = build_upstream_url(upstream.base_url, upstream_path, request.url.query)
    fwd_headers = _build_forward_headers(request, upstream)
    # 会话归属头旁路提取（原始值仅进内存 ctx，不落盘；不参与转发）
    session_header_val = extract_session_header(request.headers, rc.session_id_headers)
    body = await request.body()  # 字节原样转发，不重序列化

    # ---- 构建记录（partial：只存原始文本，不解析） ----
    req_body_text, req_truncated = _capture_body_text(body, rc)
    record = CallRecord(
        id=new_call_id(),
        status="ok",  # 占位，后台定稿时覆盖
        started_at=now_iso(),
        upstream_name=upstream.name,
        upstream_url=url,
        request=RequestInfo(
            method=request.method,
            path=request.url.path,
            query=dict(request.query_params),
            headers=redact_headers(dict(request.headers), rc) if rc.record_request_headers else None,
            body=req_body_text,
            body_truncated=req_truncated,
        ),
        client=ClientInfo(user_agent=request.headers.get("user-agent", "")),
    )
    await _safe_write_partial(store, record)

    # ---- 发起上游请求 ----
    client = runtime.upstream_client.get()
    t_sent = time.perf_counter()
    try:
        upstream_request = client.build_request(
            request.method, url, headers=fwd_headers, content=body
        )
        resp = await client.send(upstream_request, stream=True)
    except asyncio.CancelledError:
        record.status = "client_aborted"
        record.error = ErrorInfo(type="client_aborted", message="客户端在等待上游时断开")
        _spawn_process_finalize(store, record, {
            "rc": rc, "t_start": t_start, "t_sent": t_sent, "t_first_byte": None,
            "t_end": time.perf_counter(), "chunks": [], "is_sse": False,
            "encoding": "", "net_chunk_count": 0, "status": "client_aborted",
            "session_header": session_header_val,
        })
        raise
    except Exception as e:
        record.status = "error"
        record.error = ErrorInfo(type="upstream_unreachable", message=str(e))
        _spawn_process_finalize(store, record, {
            "rc": rc, "t_start": t_start, "t_sent": t_sent, "t_first_byte": None,
            "t_end": time.perf_counter(), "chunks": [], "is_sse": False,
            "encoding": "", "net_chunk_count": 0, "status": "error",
            "session_header": session_header_val,
        })
        return JSONResponse(
            status_code=502,
            content={"error": {"type": "upstream_unreachable", "message": str(e)}},
        )

    # ---- 响应头透传 ----
    record.response.status_code = resp.status_code
    if rc.record_response_headers:
        record.response.headers = redact_headers(dict(resp.headers), rc)
    fwd_resp_headers = [
        (k, v) for k, v in resp.headers.multi_items() if k.lower() not in HOP_BY_HOP_HEADERS
    ]

    content_type = resp.headers.get("content-type", "")
    encoding = resp.headers.get("content-encoding", "").split(";")[0].strip().lower()
    is_sse = "text/event-stream" in content_type

    # ---- 旁路捕获状态（转发循环内只做 append，绝不解析） ----
    chunks: list[tuple[float, bytes]] = []
    captured_bytes = 0
    capture_truncated = False
    t_first_byte: float | None = None
    t_end: float | None = None
    net_chunk_count = 0

    def _spawn(status: str) -> None:
        _spawn_process_finalize(store, record, {
            "rc": rc, "t_start": t_start, "t_sent": t_sent, "t_first_byte": t_first_byte,
            "t_end": t_end if t_end is not None else time.perf_counter(),
            "chunks": chunks, "is_sse": is_sse, "encoding": encoding,
            "net_chunk_count": net_chunk_count, "status": status,
            "session_header": session_header_val,
        })

    async def stream_gen() -> AsyncIterator[bytes]:
        nonlocal t_first_byte, t_end, net_chunk_count, captured_bytes, capture_truncated
        try:
            async for chunk in resp.aiter_raw():
                now = time.perf_counter()
                if t_first_byte is None:
                    t_first_byte = now
                net_chunk_count += 1
                yield chunk  # 逐块立即转发给客户端——流式无损的关键
                # ---- 旁路捕获（只 append 字节+时间戳，绝不抛出） ----
                try:
                    if capture_truncated:
                        continue
                    if captured_bytes + len(chunk) > limit_bytes:
                        take = max(0, limit_bytes - captured_bytes)
                        if take:
                            chunks.append((now, chunk[:take]))
                            captured_bytes += take
                        capture_truncated = True
                        record.response.body_truncated = True
                        continue
                    chunks.append((now, chunk))
                    captured_bytes += len(chunk)
                except Exception:
                    logger.warning("旁路捕获块处理失败 id=%s", record.id, exc_info=True)
            t_end = time.perf_counter()
            _spawn("ok" if resp.status_code < 400 else "error")
        except asyncio.CancelledError:
            t_end = time.perf_counter()
            record.error = ErrorInfo(type="client_aborted", message="客户端中途断开")
            _spawn("client_aborted")
            raise
        except Exception as e:
            # 上游中途断开/网络错误：已捕获多少定稿多少，绝不丢这条记录
            t_end = time.perf_counter()
            record.error = ErrorInfo(type="upstream_stream_error", message=str(e))
            _spawn("error")
            raise  # 重新抛出以保持透明：上游断了，客户端连接同样断
        finally:
            try:
                await resp.aclose()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("关闭上游响应失败 id=%s", record.id, exc_info=True)

    response = StreamingResponse(stream_gen(), status_code=resp.status_code)
    # 直接替换原始头，保留重复头（如多个 set-cookie）；不设 media_type（content-type 已在头里）
    response.raw_headers = [
        (k.encode("latin-1"), v.encode("latin-1")) for k, v in fwd_resp_headers
    ]
    return response
