"""SSEParser 单元测试：增量 feed、跨块切割、tool_calls 合并、usage、容错。"""

import json
import random

from sona_code.recording.sse import SSEParser, extract_usage


def _sse(obj) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


STREAM = (
    _sse({"choices": [{"delta": {"role": "assistant"}}]})
    + _sse({"choices": [{"delta": {"content": "你好"}}]})
    + _sse({"choices": [{"delta": {"content": "Hello"}}]})
    + b"data: [DONE]\n\n"
)


def _feed_split(data: bytes, cuts: list[int]) -> SSEParser:
    """按给定切点把 data 切成多段依次 feed。"""
    p = SSEParser()
    prev = 0
    for c in cuts:
        p.feed(data[prev:c])
        prev = c
    p.feed(data[prev:])
    return p


# ------------------------------------------------------------------- 基本流
def test_basic_stream_content_accumulation():
    p = SSEParser()
    p.feed(STREAM)
    assert p.content_text == "你好Hello"
    assert p.chunk_count == 3  # role 块 + 2 个 content 块
    assert p.saw_done is True
    assert p.parse_error is None
    assert p.assembled_message() == {"role": "assistant", "content": "你好Hello"}


def test_comment_lines_ignored():
    p = SSEParser()
    p.feed(b": keep-alive comment\n\n" + STREAM + b": trailing comment\n")
    assert p.chunk_count == 3
    assert p.content_text == "你好Hello"
    assert p.saw_done is True


def test_data_without_space_tolerated():
    p = SSEParser()
    p.feed(b'data:{"choices":[{"delta":{"content":"a"}}]}\n\ndata:[DONE]\n\n')
    assert p.content_text == "a"
    assert p.saw_done is True


def test_crlf_line_endings():
    p = SSEParser()
    p.feed(b'data: {"choices":[{"delta":{"content":"a"}}]}\r\n\r\ndata: [DONE]\r\n\r\n')
    assert p.content_text == "a"
    assert p.saw_done is True


# ----------------------------------------------------------------- 跨块切割
def test_split_at_every_single_cut_point():
    # 任意单切点（含多字节 UTF-8 字符中间）都能正确解析
    for cut in range(1, len(STREAM)):
        p = _feed_split(STREAM, [cut])
        assert p.content_text == "你好Hello", f"切点 {cut} 解析失败"
        assert p.chunk_count == 3
        assert p.saw_done is True
        assert p.parse_error is None


def test_split_at_two_random_cut_points():
    rng = random.Random(42)
    n = len(STREAM)
    for _ in range(50):
        c1, c2 = sorted(rng.sample(range(1, n), 2))
        p = _feed_split(STREAM, [c1, c2])
        assert p.content_text == "你好Hello", f"切点 {c1},{c2} 解析失败"
        assert p.saw_done is True
        assert p.chunk_count == 3


# ------------------------------------------------------------------ tool_calls
def test_tool_calls_merged_by_index():
    events = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": ""}}
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"city\":"}}
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 1, "id": "call_2", "type": "function",
             "function": {"name": "now", "arguments": "{}"}}
        ]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": " \"北京\"}"}}
        ]}}]},
    ]
    p = SSEParser()
    p.feed(b"".join(_sse(e) for e in events))
    assert p.tool_calls == [
        {"index": 0, "id": "call_1", "type": "function",
         "function": {"name": "get_weather", "arguments": '{"city": "北京"}'}},
        {"index": 1, "id": "call_2", "type": "function",
         "function": {"name": "now", "arguments": "{}"}},
    ]
    msg = p.assembled_message()
    assert msg["role"] == "assistant"
    assert "content" not in msg
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"city": "北京"}'
    # id/name 只出现一次的块也正确合并（上面已隐式验证）


# ------------------------------------------------------------------- reasoning
def test_reasoning_content_accumulates():
    p = SSEParser()
    p.feed(_sse({"choices": [{"delta": {"reasoning_content": "先想"}}]}))
    p.feed(_sse({"choices": [{"delta": {"content": "后答"}}]}))
    p.feed(_sse({"choices": [{"delta": {"reasoning_content": "…"}}]}))
    assert p.reasoning_text == "先想…"
    assert p.content_text == "后答"
    assert p.assembled_message() == {
        "role": "assistant", "content": "后答", "reasoning_content": "先想…",
    }


# ---------------------------------------------------------------------- usage
def test_extract_usage_openai_cached_tokens():
    u = {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        "prompt_tokens_details": {"cached_tokens": 4},
    }
    assert extract_usage(u) == {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        "cached_tokens": 4, "reasoning_tokens": None,
    }


def test_extract_usage_deepseek_prompt_cache_hit():
    u = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
         "prompt_cache_hit_tokens": 6}
    assert extract_usage(u) == {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
        "cached_tokens": 6, "reasoning_tokens": None,
    }


def test_extract_usage_reasoning_tokens_variants():
    # OpenAI 风格：completion_tokens_details.reasoning_tokens
    u1 = {"prompt_tokens": 1, "completion_tokens": 9, "total_tokens": 10,
          "completion_tokens_details": {"reasoning_tokens": 7}}
    assert extract_usage(u1)["reasoning_tokens"] == 7
    # DeepSeek 风格：顶层 reasoning_tokens
    u2 = {"prompt_tokens": 1, "completion_tokens": 9, "total_tokens": 10,
          "reasoning_tokens": 8}
    assert extract_usage(u2)["reasoning_tokens"] == 8
    # 无该字段 → None
    u3 = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
    assert extract_usage(u3)["reasoning_tokens"] is None


def test_extract_usage_without_cached_field():
    u = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
    assert extract_usage(u)["cached_tokens"] is None


def test_usage_block_in_stream():
    p = SSEParser()
    p.feed(
        _sse({"choices": [{"delta": {"content": "hi"}}]})
        + _sse({"choices": [{"delta": {}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 8, "total_tokens": 15,
                          "prompt_cache_hit_tokens": 2}})
        + b"data: [DONE]\n\n"
    )
    assert p.usage == {"prompt_tokens": 7, "completion_tokens": 8,
                       "total_tokens": 15, "cached_tokens": 2, "reasoning_tokens": None}


def test_pure_usage_block_without_choices():
    p = SSEParser()
    p.feed(_sse({"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                           "prompt_tokens_details": {"cached_tokens": 1}}}))
    assert p.chunk_count == 1
    assert p.usage == {"prompt_tokens": 1, "completion_tokens": 1,
                       "total_tokens": 2, "cached_tokens": 1, "reasoning_tokens": None}


# ----------------------------------------------------------------------- 容错
def test_bad_json_records_error_and_continues():
    p = SSEParser()
    p.feed(
        b"data: not-json\n\n"
        + _sse({"choices": [{"delta": {"content": "ok"}}]})
        + b"data: [DONE]\n\n"
    )
    assert p.parse_error is not None
    assert "not-json" in p.parse_error
    assert p.content_text == "ok"  # 后续事件继续解析
    assert p.chunk_count == 1
    assert p.saw_done is True


def test_non_object_json_rejected():
    p = SSEParser()
    p.feed(b"data: [1,2]\n\n" + _sse({"choices": [{"delta": {"content": "x"}}]}))
    assert p.parse_error is not None
    assert p.content_text == "x"


def test_assembled_message_all_empty_returns_none():
    p = SSEParser()
    p.feed(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\ndata: [DONE]\n\n')
    assert p.assembled_message() is None


# ---------------------------------------------------- 首个增量内容偏移（TTFT 用）
def test_first_delta_char_offset_points_at_first_content_line():
    p = SSEParser()
    head = _sse({"choices": [{"delta": {"role": "assistant"}}]})  # role 块不计入
    first_content = _sse({"choices": [{"delta": {"content": "你好"}}]})
    p.feed(head + first_content + _sse({"choices": [{"delta": {"content": "Hi"}}]}))
    assert p.saw_first_delta is True
    # 事件在其空行（行分隔）处提交：偏移落在首个 content 事件块内（数据行末），
    # 大于 head 且不超过该事件块结束 → 可映射回承载该事件的捕获块
    head_len = len(head.decode("utf-8"))
    fc_len = len(first_content.decode("utf-8"))
    assert head_len < p.first_delta_char_offset < head_len + fc_len


def test_first_delta_char_offset_counts_reasoning_and_toolcalls():
    # reasoning_content / tool_calls 同样算"首个增量"，偏移落在首个事件块内
    ev1 = _sse({"choices": [{"delta": {"reasoning_content": "想"}}]})
    p = SSEParser()
    p.feed(ev1)
    assert 0 < p.first_delta_char_offset < len(ev1.decode("utf-8"))

    ev2 = _sse({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"name": "f", "arguments": "{}"}}]}}]})
    p2 = SSEParser()
    p2.feed(ev2)
    assert 0 < p2.first_delta_char_offset < len(ev2.decode("utf-8"))


def test_first_delta_char_offset_none_without_delta():
    p = SSEParser()
    p.feed(
        _sse({"choices": [{"delta": {"role": "assistant"}}]})
        + _sse({"choices": [{"delta": {}}], "usage": {"prompt_tokens": 1, "completion_tokens": 0,
                                                       "total_tokens": 1}})
        + b"data: [DONE]\n\n"
    )
    assert p.saw_first_delta is False
    assert p.first_delta_char_offset is None


def test_first_delta_char_offset_survives_split_feeds():
    # 跨块切割后偏移仍指向首个 content 行起点（与整块 feed 一致）
    whole = SSEParser()
    whole.feed(STREAM)
    for cut in range(1, len(STREAM)):
        p = _feed_split(STREAM, [cut])
        assert p.first_delta_char_offset == whole.first_delta_char_offset, f"切点 {cut} 偏移不一致"
