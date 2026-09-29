"""parse.py 单元测试：请求/响应解析、SSE 捕获回放（含 gzip）、会话归属与增量 diff。"""

import gzip
import json
import zlib

from sona_code.recording.parse import (
    chunks_to_raw_texts,
    diff_new_messages,
    extract_session_header,
    first_user_preview,
    make_inflater,
    message_digest,
    parse_nonsse_body,
    parse_request_body,
    parse_sse_captured,
    session_key_from_header,
    session_key_of,
    system_text,
)


def _sse(obj) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


# ================================================================ 请求体解析
def test_parse_request_body_full():
    body = {
        "model": "deepseek-chat",
        "stream": True,
        "messages": [{"role": "user", "content": "你好"}],
        "tools": [{"type": "function", "function": {"name": "f"}}],
        "temperature": 0.7,
        "max_tokens": 1024,
    }
    out = parse_request_body(body)
    assert out["model"] == "deepseek-chat"
    assert out["stream"] is True
    assert out["messages"] == body["messages"]
    assert out["tools"] == body["tools"]
    assert out["params"] == {"temperature": 0.7, "max_tokens": 1024}


def test_parse_request_body_from_json_string():
    out = parse_request_body(json.dumps({"model": "m", "messages": []}, ensure_ascii=False))
    assert out["model"] == "m"
    assert out["stream"] is False
    assert out["tools"] is None
    assert out["params"] is None  # 无多余字段


def test_parse_request_body_invalid():
    assert parse_request_body("not-json") is None
    assert parse_request_body("[1,2]") is None  # 非 dict
    assert parse_request_body(None) is None


def test_parse_request_body_non_list_fields_normalized():
    out = parse_request_body({"model": "m", "messages": "oops", "tools": 5, "stream": "yes"})
    assert out["messages"] is None
    assert out["tools"] is None
    assert out["stream"] is False  # 仅 True 视为流式


def test_parse_request_body_non_string_model_coerced():
    assert parse_request_body({"model": 123})["model"] == "123"


# ================================================================ 响应体解析
def test_make_inflater_variants():
    gz, e1 = make_inflater("gzip")
    assert gz is not None and hasattr(gz, "decompress") and e1 is None
    df, e2 = make_inflater("deflate")
    assert df is not None and hasattr(df, "decompress") and e2 is None
    none1, e3 = make_inflater("br")
    assert none1 is None and e3 is not None
    none2, e4 = make_inflater("")
    assert none2 is None and e4 is None


def test_parse_nonsse_body_chat_completion():
    body = json.dumps({
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": "答", "reasoning_content": "思"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5,
                  "prompt_cache_hit_tokens": 1},
    }).encode("utf-8")
    out = parse_nonsse_body(body)
    assert out["message"] == {"role": "assistant", "content": "答", "reasoning_content": "思"}
    assert out["finish_reason"] == "stop"
    assert out["usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5,
                            "cached_tokens": 1, "reasoning_tokens": None}
    assert out["content"]["choices"][0]["message"]["content"] == "答"


def test_parse_nonsse_body_plain_text():
    out = parse_nonsse_body(b"Gateway Timeout")
    assert out["content"] == "Gateway Timeout"
    assert out["message"] is None and out["usage"] is None and out["finish_reason"] is None


# ============================================================ SSE 捕获回放
STREAM_BYTES = (
    _sse({"choices": [{"delta": {"role": "assistant"}}]})
    + _sse({"choices": [{"delta": {"content": "你"}}]})
    + _sse({"choices": [{"delta": {"content": "好"}}]})
    + _sse({"choices": [{"delta": {}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}})
    + b"data: [DONE]\n\n"
)


def test_parse_sse_captured_plain():
    mid = len(_sse({"choices": [{"delta": {"role": "assistant"}}]})) + 10
    chunks = [(1.0, STREAM_BYTES[:mid]), (2.0, STREAM_BYTES[mid:])]
    res = parse_sse_captured(chunks)
    p = res["parser"]
    assert p.content_text == "你好"
    assert p.finish_reason is None
    assert p.usage["total_tokens"] == 6
    assert p.saw_done is True
    # 首个 content 增量在第 2 块 → ttft_at 指向该块时间戳
    assert res["ttft_at"] == 2.0


def test_parse_sse_captured_ttft_in_first_chunk():
    chunks = [(1.5, STREAM_BYTES)]
    res = parse_sse_captured(chunks)
    assert res["ttft_at"] == 1.5


def test_parse_sse_captured_no_delta_ttft_none():
    chunks = [(1.0, _sse({"choices": [{"delta": {}}]}) + b"data: [DONE]\n\n")]
    res = parse_sse_captured(chunks)
    assert res["ttft_at"] is None
    assert res["parser"].content_text == ""


def test_parse_sse_captured_gzip_encoded():
    # 整个流 gzip 压缩后分 3 块回放：解压 + 解析 + TTFT 定位都正确
    gz = gzip.compress(STREAM_BYTES)
    cut1, cut2 = len(gz) // 3, 2 * len(gz) // 3
    chunks = [(1.0, gz[:cut1]), (2.0, gz[cut1:cut2]), (3.0, gz[cut2:])]
    res = parse_sse_captured(chunks, "gzip")
    p = res["parser"]
    assert p.content_text == "你好"
    assert p.saw_done is True
    assert p.usage["total_tokens"] == 6
    # 首 content 增量必然落在 gzip 流的某一已解压块中
    assert res["ttft_at"] in (1.0, 2.0, 3.0)


def test_parse_sse_captured_br_returns_parseable_empty():
    # br 不支持解压：inflater=None 时字节直接喂解析器 → 解析失败但不抛异常
    chunks = [(1.0, b"\x00\x00garbage")]
    res = parse_sse_captured(chunks, "br")
    assert res["parser"].content_text == ""
    assert res["ttft_at"] is None


def test_chunks_to_raw_texts():
    raw = "数据".encode("utf-8")  # 每个汉字 3 字节
    chunks = [(1.0, raw[:3]), (2.0, raw[3:])]
    texts = chunks_to_raw_texts(chunks)
    # 增量解码容忍多字节字符跨块
    assert texts == ["数", "据"]


# ================================================================ 会话归属
SYS = {"role": "system", "content": "你是助手"}
U1 = {"role": "user", "content": "第一问"}
A1 = {"role": "assistant", "content": "第一答"}


def test_session_key_stable_for_same_prefix():
    msgs_a = [SYS, U1]
    msgs_b = [SYS, U1, A1, {"role": "user", "content": "第二问"}]  # 追加不改变前缀
    assert session_key_of("m", msgs_a) == session_key_of("m", msgs_b)
    assert session_key_of("m", msgs_a).startswith("s")


def test_session_key_differs_by_model_or_content():
    k1 = session_key_of("m1", [SYS, U1])
    k2 = session_key_of("m2", [SYS, U1])
    k3 = session_key_of("m1", [SYS, {"role": "user", "content": "另一问"}])
    k4 = session_key_of("m1", [{"role": "system", "content": "另一个人设"}, U1])
    assert len({k1, k2, k3, k4}) == 4


def test_session_key_unidentifiable_returns_none():
    assert session_key_of("m", None) is None
    assert session_key_of("m", []) is None
    # 无 system 且无 user（仅 assistant）→ 无法识别
    assert session_key_of("m", [A1]) is None


def test_session_key_segmented_content():
    # 分段 content（视觉模型风格）与拼接文本等价 → 同一 key
    plain = [{"role": "user", "content": "看图"}]
    segs = [{"role": "user", "content": [{"type": "text", "text": "看图"},
                                         {"type": "image_url", "image_url": {"url": "x"}}]}]
    assert session_key_of("m", plain) == session_key_of("m", segs)


def test_extract_session_header_priority_case_and_blank():
    # 大小写不敏感
    assert extract_session_header({"X-Session-Id": "abc"}, ["x-session-id"]) == "abc"
    # 配置顺序即优先级：先命中的先用
    hdrs = {"a-session": "1", "b-session": "2"}
    assert extract_session_header(hdrs, ["a-session", "b-session"]) == "1"
    assert extract_session_header(hdrs, ["b-session", "a-session"]) == "2"
    # 空白值跳过，取下一个
    assert extract_session_header({"a-session": " ", "b-session": "2"}, ["a-session", "b-session"]) == "2"
    # 未配置 / 无头 / 无命中 → None
    assert extract_session_header({"x": "1"}, []) is None
    assert extract_session_header(None, ["x-session-id"]) is None
    assert extract_session_header({"x": "1"}, ["x-session-id"]) is None


def test_session_key_from_header():
    assert session_key_from_header(None) is None
    assert session_key_from_header("") is None
    assert session_key_from_header("   ") is None
    k = session_key_from_header("sess-1")
    assert k is not None and k.startswith("h") and len(k) == 17
    assert session_key_from_header("sess-1") == k  # 同值稳定
    assert session_key_from_header(" sess-1 ") == k  # 去除首尾空白
    assert session_key_from_header("sess-2") != k
    assert k != session_key_of("m", [SYS, U1])  # 与内容哈希键（s 前缀）不冲突


def test_first_user_preview():
    msgs = [SYS, {"role": "user", "content": "很长的\n多行问题" + "x" * 100}]
    pv = first_user_preview(msgs, limit=10)
    assert pv == "很长的 多行问题xx"  # 换行折叠为空格 + 按 10 字符截断
    assert first_user_preview(None) == ""
    assert first_user_preview([SYS]) == ""


def test_system_text():
    assert system_text(None) == ""
    assert system_text([]) == ""
    assert system_text([U1]) == ""  # 无 system 消息
    assert system_text([SYS, U1]) == "你是助手"
    # 分段数组 content
    seg = {"role": "system", "content": [{"type": "text", "text": "部分一"}, {"type": "text", "text": "部分二"}]}
    assert system_text([seg, U1]) == "部分一 部分二"
    # 多条 system 以空行连接；空白内容跳过；非法条目忽略
    s2 = {"role": "system", "content": "补充"}
    assert system_text([SYS, s2, U1]) == "你是助手\n\n补充"
    assert system_text([SYS, {"role": "system", "content": "   "}]) == "你是助手"
    assert system_text(["bad", SYS]) == "你是助手"


# ================================================================ 消息增量
def _digests(msgs):
    return [message_digest(m) for m in msgs]


def test_diff_first_request_returns_all():
    msgs = [SYS, U1]
    assert diff_new_messages(None, msgs) == msgs
    assert diff_new_messages([], msgs) == msgs


def test_diff_append_only_returns_tail():
    prev = [SYS, U1]
    msgs = [SYS, U1, A1, {"role": "user", "content": "第二问"}]
    assert diff_new_messages(_digests(prev), msgs) == msgs[2:]


def test_diff_identical_returns_empty():
    msgs = [SYS, U1]
    assert diff_new_messages(_digests(msgs), msgs) == []


def test_diff_changed_middle_returns_from_change():
    prev = [SYS, U1, A1]
    msgs = [SYS, {"role": "user", "content": "改写的问题"}, A1]
    assert diff_new_messages(_digests(prev), msgs) == msgs[1:]


def test_diff_shorter_prefix_returns_nothing():
    prev = [SYS, U1, A1]
    msgs = [SYS, U1]  # 回退（理论少见）
    assert diff_new_messages(_digests(prev), msgs) == []


def test_diff_invalid_messages():
    assert diff_new_messages(None, None) == []
    assert diff_new_messages(_digests([U1]), "oops") == []


def test_message_digest_deterministic_and_key_order_insensitive():
    a = {"role": "user", "content": "x", "extra": 1}
    b = {"extra": 1, "content": "x", "role": "user"}  # 键序不同
    assert message_digest(a) == message_digest(b)
    assert message_digest(a) != message_digest({"role": "user", "content": "y"})
