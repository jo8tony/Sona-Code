"""独立解析模块（纯逻辑、无 IO、无状态）。

在后台对捕获的原始请求/响应做解析：请求体结构化、SSE/JSON 响应组装、
TTFT 时间戳还原、会话归属（session_key）与消息增量（diff）计算。
绝不参与代理转发路径。
"""

from __future__ import annotations

import codecs
import hashlib
import json
import zlib
from typing import Any, Mapping

from sona_code.recording.responses import response_messages, response_message
from sona_code.recording.sse import SSEParser, extract_usage

# 请求体中不属于 messages/tools 的常见参数字段以外的部分全部归入 params，
# 这里列出已知字段仅用于排除；其余键值原样保留。
_REQUEST_KNOWN_KEYS = {"model", "stream", "messages", "tools"}


# ---------------------------------------------------------------- 请求体解析
def parse_request_body(body: Any) -> dict | None:
    """解析请求体（已捕获的 JSON 对象或原始文本）。

    返回 {model, stream, messages, tools, params}；无法解析返回 None。
    """
    parsed_obj: Any = body
    if isinstance(parsed_obj, str):
        try:
            parsed_obj = json.loads(parsed_obj)
        except Exception:
            return None
    if not isinstance(parsed_obj, dict):
        return None

    model = parsed_obj.get("model")
    if model is not None and not isinstance(model, str):
        model = str(model)
    stream = parsed_obj.get("stream") is True
    protocol = "responses" if "input" in parsed_obj or "previous_response_id" in parsed_obj else "chat_completions"
    messages = response_messages(parsed_obj) if protocol == "responses" else parsed_obj.get("messages")
    if not isinstance(messages, list):
        messages = None
    tools = parsed_obj.get("tools")
    if not isinstance(tools, list):
        tools = None
    known = _REQUEST_KNOWN_KEYS | ({"input", "instructions"} if protocol == "responses" else set())
    params = {k: v for k, v in parsed_obj.items() if k not in known} or None
    result = {"model": model, "stream": stream, "messages": messages, "tools": tools, "params": params}
    if protocol == "responses":
        result.update(protocol=protocol, previous_response_id=parsed_obj.get("previous_response_id"))
    return result


# ---------------------------------------------------------------- 响应体解析
def make_inflater(encoding: str):
    """gzip/deflate → zlib 增量解压器；br/zstd → (None, 原因)；无压缩 → (None, None)。"""
    if encoding in ("gzip", "x-gzip"):
        return zlib.decompressobj(16 + zlib.MAX_WBITS), None
    if encoding == "deflate":
        return zlib.decompressobj(), None
    if encoding in ("br", "zstd"):
        return None, f"content-encoding={encoding} 暂不支持旁路解压，已跳过解析"
    return None, None


def parse_nonsse_body(data: bytes) -> dict:
    """非流式响应体解析：返回 {content, message, usage, finish_reason}。"""
    out: dict[str, Any] = {"content": None, "message": None, "usage": None, "finish_reason": None}
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception:
        out["content"] = data.decode("utf-8", errors="replace")
        return out
    out["content"] = obj
    if isinstance(obj, dict):
        usage = obj.get("usage")
        if isinstance(usage, dict) and usage:
            out["usage"] = extract_usage(usage)
        if obj.get("object") == "response" or isinstance(obj.get("output"), list):
            out.update(message=response_message(obj.get("output")), finish_reason=obj.get("status"),
                       response_id=obj.get("id"), protocol="responses", error=obj.get("error"))
        choices = obj.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            msg = choices[0].get("message")
            if isinstance(msg, dict):
                out["message"] = msg
            fr = choices[0].get("finish_reason")
            if isinstance(fr, str):
                out["finish_reason"] = fr
    return out


def parse_sse_captured(
    chunks: list[tuple[float, bytes]], encoding: str = ""
) -> dict:
    """流式响应后台解析。

    chunks: [(perf_counter 时间戳, 原始字节)]（转发循环中旁路记录）。
    返回 {parser, ttft_at}：ttft_at 为首个增量内容所在块的时间戳（无则 None）。
    """
    parser = SSEParser()
    inflater, _ = make_inflater(encoding)
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    char_ends: list[tuple[float, int]] = []  # (块时间戳, 该块结束时的累计字符数)
    total_chars = 0
    for t, chunk in chunks:
        data = chunk if inflater is None else inflater.decompress(chunk)
        text = decoder.decode(data)
        total_chars += len(text)
        char_ends.append((t, total_chars))
        parser.feed(data)

    ttft_at: float | None = None
    off = parser.first_delta_char_offset
    if off is not None:
        for t, char_end in char_ends:
            if char_end >= off:
                ttft_at = t
                break
    return {"parser": parser, "ttft_at": ttft_at}


def chunks_to_raw_texts(chunks: list[tuple[float, bytes]]) -> list[str]:
    """原始分块文本（record_raw_chunks 用）。"""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    return [decoder.decode(c) for _, c in chunks]


# ---------------------------------------------------------------- 会话归属
def extract_session_header(
    headers: Mapping[str, str] | None, names: list[str] | None
) -> str | None:
    """按配置顺序（大小写不敏感）提取第一个非空会话头值。

    配置顺序即优先级：靠前的头命中即返回；空值/空白值跳过。
    """
    if not headers or not names:
        return None
    lowered = {str(k).lower(): v for k, v in headers.items()}
    for name in names:
        v = lowered.get(str(name).lower())
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def session_key_from_header(value: str | None) -> str | None:
    """请求头会话归属键：h 前缀（区别于内容哈希的 s 前缀），值做摘要保证 URL 安全。"""
    if not value or not value.strip():
        return None
    return "h" + hashlib.sha1(value.strip().encode("utf-8", errors="replace")).hexdigest()[:16]


def _content_text(content: Any) -> str:
    """消息 content → 纯文本（str 或分段数组）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for seg in content:
            if isinstance(seg, str):
                parts.append(seg)
            elif isinstance(seg, dict) and isinstance(seg.get("text"), str):
                parts.append(seg["text"])
        return " ".join(parts)
    return ""


def message_digest(msg: Any) -> str:
    """消息规范摘要（用于增量 diff 的逐条比对）。"""
    try:
        canon = json.dumps(msg, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        canon = repr(msg)
    return hashlib.sha1(canon.encode("utf-8", errors="replace")).hexdigest()


def session_key_of(model: str | None, messages: list[Any] | None) -> str | None:
    """会话归属键：模型 + system 消息 + 首条 user 消息 的摘要。

    LLM API 无状态，同一对话的后续请求会携带相同前缀，
    以此把多次调用聚合为同一"会话轨迹"。无法识别返回 None。
    """
    if not isinstance(messages, list) or not messages:
        return None
    system_part = ""
    if isinstance(messages[0], dict) and messages[0].get("role") == "system":
        system_part = _content_text(messages[0].get("content"))
    first_user = ""
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            first_user = _content_text(m.get("content"))
            break
    if not system_part and not first_user:
        return None
    key_src = "|".join((model or "", system_part, first_user))
    return "s" + hashlib.sha1(key_src.encode("utf-8", errors="replace")).hexdigest()[:16]


def system_text(messages: list[Any] | None) -> str:
    r"""提取 messages 中全部 role=system 消息的文本（str 或分段数组），多条以 \n\n 连接。

    供轨迹 System Prompt 展示与前后轮 diff 使用。
    """
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            text = _content_text(m.get("content")).strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def first_user_preview(messages: list[Any] | None, limit: int = 80) -> str:
    """首条 user 消息预览（会话列表展示用）。"""
    if isinstance(messages, list):
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "user":
                text = _content_text(m.get("content")).strip().replace("\n", " ")
                return text[:limit]
    return ""


# ---------------------------------------------------------------- 消息增量
def diff_new_messages(
    prev_digests: list[str] | None, messages: list[Any] | None
) -> list[Any]:
    """计算本次请求相对上一次的新增消息（轨迹增量）。

    从头逐条比对摘要：找到第一条不一致的位置，其后全部视为新增；
    无法比对（无历史）时返回全部消息。
    """
    if not isinstance(messages, list):
        return []
    if not prev_digests:
        return list(messages)
    digests = [message_digest(m) for m in messages]
    n = min(len(prev_digests), len(digests))
    for i in range(n):
        if prev_digests[i] != digests[i]:
            return list(messages[i:])
    if len(digests) > n:
        return list(messages[n:])
    return []  # 与上次完全一致（如重试），无新增
