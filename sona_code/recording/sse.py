"""SSE 流式解析与 delta 组装（纯逻辑，无 IO）。

容忍任意位置的块切断：内部做增量 UTF-8 解码与行缓冲；
解析失败不中断，跳过该事件继续。
"""

from __future__ import annotations

import codecs
import json
from typing import Any
from sona_code.recording.responses import ResponsesAccumulator


def extract_usage(u: dict) -> dict:
    """从 usage 对象提取五元组；cached_tokens 兼容
    prompt_tokens_details.cached_tokens 与 DeepSeek 的 prompt_cache_hit_tokens；
    reasoning_tokens 兼容 completion_tokens_details.reasoning_tokens 与 reasoning_tokens。"""
    cached = None
    details = u.get("prompt_tokens_details") or u.get("input_tokens_details")
    if isinstance(details, dict) and details.get("cached_tokens") is not None:
        cached = details.get("cached_tokens")
    elif u.get("prompt_cache_hit_tokens") is not None:
        cached = u.get("prompt_cache_hit_tokens")
    reasoning = None
    cdetails = u.get("completion_tokens_details") or u.get("output_tokens_details")
    if isinstance(cdetails, dict) and cdetails.get("reasoning_tokens") is not None:
        reasoning = cdetails.get("reasoning_tokens")
    elif u.get("reasoning_tokens") is not None:
        reasoning = u.get("reasoning_tokens")
    return {
        "prompt_tokens": u.get("prompt_tokens", u.get("input_tokens")),
        "completion_tokens": u.get("completion_tokens", u.get("output_tokens")),
        "total_tokens": u.get("total_tokens"),
        "cached_tokens": cached,
        "reasoning_tokens": reasoning,
    }


class SSEParser:
    def __init__(self) -> None:
        self.responses: ResponsesAccumulator | None = None
        self._event_name = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._line_buf = ""
        self._data_lines: list[str] = []
        self._fed_chars = 0  # 已 feed 的解码字符总数
        self._consumed_chars = 0  # 已作为完整行消费的字符数（含换行）
        # —— 解析结果属性 ——
        self.content_text = ""
        self.reasoning_text = ""
        self.tool_calls: list[dict] = []
        self.chunk_count = 0
        self.usage: dict | None = None
        self.finish_reason: str | None = None
        self.saw_done = False
        self.parse_error: str | None = None
        self.saw_first_delta = False  # 是否出现过增量内容
        # 首个增量内容在解码字符流中的偏移（已消费行边界），
        # 供后台将 TTFT 映射回捕获块时间戳
        self.first_delta_char_offset: int | None = None

    # ------------------------------------------------------------------ feed
    def feed(self, raw: bytes) -> None:
        text = self._decoder.decode(raw)
        if not text:
            return
        self._fed_chars += len(text)
        self._line_buf += text
        lines = self._line_buf.split("\n")
        self._line_buf = lines.pop()  # 最后一段可能不完整，留缓冲
        for line in lines:
            self._handle_line(line.rstrip("\r"))
            self._consumed_chars += len(line) + 1  # 行内容 + 换行符

    def _handle_line(self, line: str) -> None:
        if line == "":  # 空行 = 事件结束
            if self._data_lines:
                self._handle_event("\n".join(self._data_lines))
                self._data_lines = []
            self._event_name = ""
            return
        if line.startswith(":"):  # 注释行
            return
        if line.startswith("event:"):
            self._event_name = line[6:].strip()
        if line.startswith("data:"):  # 容忍 data: 与 data:
            payload = line[5:]
            if payload.startswith(" "):
                payload = payload[1:]
            self._data_lines.append(payload)
        # 其余字段（event:/id:/retry:）与未知行忽略

    # ----------------------------------------------------------------- event
    def _handle_event(self, data: str) -> None:
        if data.strip() == "[DONE]":
            self.saw_done = True
            return
        try:
            obj = json.loads(data)
            if not isinstance(obj, dict):
                raise ValueError(f"事件不是 JSON 对象: {type(obj).__name__}")
        except Exception as e:  # 解析失败：记录首个错误并跳过
            if self.parse_error is None:
                self.parse_error = f"JSON 解析失败: {e}; data[:80]={data[:80]!r}"
            return
        self.chunk_count += 1
        kind = obj.get("type") or self._event_name
        if isinstance(kind, str) and (kind.startswith("response.") or (kind == "error" and self.responses)):
            obj.setdefault("type", kind)
            if self.responses is None:
                self.responses = ResponsesAccumulator()
            delta, response_usage = self.responses.consume(obj)
            if response_usage:
                self.usage = extract_usage(response_usage)
            if delta and not self.saw_first_delta:
                self.saw_first_delta = True
                self.first_delta_char_offset = self._consumed_chars
            if kind in {"response.completed", "response.failed", "response.incomplete", "error"}:
                self.saw_done = True
                self.finish_reason = self.responses.status or kind.split(".")[-1]
            return
        usage = obj.get("usage")
        if isinstance(usage, dict) and usage:
            self.usage = extract_usage(usage)
        choices = obj.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                fr = choice.get("finish_reason")
                if isinstance(fr, str) and fr:
                    self.finish_reason = fr
                if isinstance(choice.get("delta"), dict):
                    self._consume_delta(choice["delta"])

    def _consume_delta(self, delta: dict) -> None:
        content = delta.get("content")
        reasoning = delta.get("reasoning_content")
        tcs = delta.get("tool_calls")
        has_delta = (
            (isinstance(content, str) and content)
            or (isinstance(reasoning, str) and reasoning)
            or (isinstance(tcs, list) and tcs)
        )
        if has_delta and not self.saw_first_delta:
            self.saw_first_delta = True
            # 已作为完整行消费的字符位置（与后台的块级字符累计对齐）
            self.first_delta_char_offset = self._consumed_chars
        if isinstance(content, str) and content:
            self.content_text += content
        if isinstance(reasoning, str) and reasoning:
            self.reasoning_text += reasoning
        if isinstance(tcs, list) and tcs:
            self._merge_tool_calls(tcs)

    def _merge_tool_calls(self, tcs: list) -> None:
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            idx = tc.get("index") or 0
            slot = next((s for s in self.tool_calls if s["index"] == idx), None)
            if slot is None:
                slot = {
                    "index": idx,
                    "id": None,
                    "type": "function",
                    "function": {"name": None, "arguments": ""},
                }
                self.tool_calls.append(slot)
            if tc.get("id"):
                slot["id"] = tc["id"]
            if tc.get("type"):
                slot["type"] = tc["type"]
            fn = tc.get("function")
            if isinstance(fn, dict):
                if fn.get("name"):
                    slot["function"]["name"] = fn["name"]
                args = fn.get("arguments")
                if isinstance(args, str):
                    slot["function"]["arguments"] += args

    # ------------------------------------------------------------- assemble
    def assembled_message(self) -> dict | None:
        """组装为 assistant 消息 dict；仅含有值字段；全空返回 None。
        finish_reason 属响应元数据，不混入消息体（由调用方单独取）。"""
        if self.responses is not None:
            return self.responses.message()
        msg: dict[str, Any] = {"role": "assistant"}
        if self.reasoning_text:
            msg["reasoning_content"] = self.reasoning_text
        if self.content_text:
            msg["content"] = self.content_text
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": s["id"], "type": s["type"], "function": s["function"]}
                for s in self.tool_calls
            ]
        return msg if len(msg) > 1 else None
