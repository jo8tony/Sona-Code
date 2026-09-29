"""Responses API normalization for background recording only."""
from __future__ import annotations

from typing import Any


def response_messages(obj: dict) -> list[dict]:
    messages = []
    instructions = obj.get("instructions")
    if isinstance(instructions, str) and instructions:
        messages.append({"role": "system", "content": instructions})
    items = obj.get("input", [])
    if isinstance(items, str):
        return [*messages, {"role": "user", "content": items}]
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type", "message")
        if kind == "message":
            content = item.get("content", "")
            if isinstance(content, list):
                parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") in {"input_text", "output_text"}:
                        parts.append({"type": "text", "text": part.get("text", "")})
                    elif part.get("type") == "input_image":
                        parts.append({"type": "image_url", "image_url": {"url": part.get("image_url", ""),
                                                                                  "detail": part.get("detail", "auto")}})
                    else:
                        parts.append(part)
                content = parts
            messages.append({"role": "system" if item.get("role") == "developer" else item.get("role", "user"),
                             "content": content})
        elif kind == "function_call":
            messages.append({"role": "assistant", "tool_calls": [{"id": item.get("call_id"), "type": "function",
                            "function": {"name": item.get("name"), "arguments": item.get("arguments", "")}}]})
        elif kind == "function_call_output":
            messages.append({"role": "tool", "tool_call_id": item.get("call_id"), "content": item.get("output", "")})
        else:
            # References and encrypted reasoning remain visible as opaque input items.
            messages.append({"role": "assistant", "response_item": item})
    return messages


def response_message(output: Any) -> dict | None:
    text, reasoning, calls, other = [], [], [], []
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content", []):
                if isinstance(part, dict):
                    value = part.get("text") if part.get("type") == "output_text" else part.get("refusal")
                    if isinstance(value, str) and value:
                        text.append(value)
        elif item.get("type") == "reasoning":
            for part in item.get("summary", []):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    reasoning.append(part["text"])
        elif item.get("type") == "function_call":
            calls.append({"id": item.get("call_id", item.get("id")), "type": "function",
                          "function": {"name": item.get("name"), "arguments": item.get("arguments", "")}})
        else:
            other.append(item)
    message: dict[str, Any] = {"role": "assistant"}
    if text:
        message["content"] = "\n".join(text)
    if reasoning:
        message["reasoning_content"] = "\n".join(reasoning)
    if calls:
        message["tool_calls"] = calls
    if other:
        message["response_items"] = other
    return message if len(message) > 1 else None


class ResponsesAccumulator:
    def __init__(self) -> None:
        self.items: dict[int, dict] = {}
        self.response_id: str | None = None
        self.error: dict | None = None
        self.status: str | None = None
        self.unknown_events: list[dict] = []

    def consume(self, obj: dict) -> tuple[bool, dict | None]:
        kind = obj.get("type", "")
        delta = False
        response = obj.get("response")
        usage = None
        if isinstance(response, dict):
            self.response_id = response.get("id") or self.response_id
            usage = response.get("usage")
            self.status = response.get("status") or self.status
            if kind in {"response.completed", "response.failed", "response.incomplete"}:
                for index, item in enumerate(response.get("output", [])):
                    self.items[index] = item
                self.error = response.get("error")
        index = obj.get("output_index", 0)
        if not isinstance(index, int):
            index = 0
        if kind in {"response.output_item.added", "response.output_item.done"}:
            item = obj.get("item")
            if isinstance(item, dict):
                self.items[index] = {**self.items.get(index, {}), **item}
        elif kind in {"response.content_part.added", "response.content_part.done"}:
            item = self.items.setdefault(index, {"type": "message", "content": []})
            self._part(item, "content", obj.get("content_index", 0)).update(obj.get("part") or {})
        elif kind in {"response.reasoning_summary_part.added", "response.reasoning_summary_part.done"}:
            item = self.items.setdefault(index, {"type": "reasoning", "summary": []})
            self._part(item, "summary", obj.get("summary_index", 0)).update(obj.get("part") or {})
        elif kind in {"response.output_text.delta", "response.output_text.done", "response.refusal.delta", "response.refusal.done",
                      "response.reasoning_summary_text.delta", "response.reasoning_summary_text.done"}:
            reasoning = "reasoning_summary" in kind
            item = self.items.setdefault(index, {"type": "reasoning" if reasoning else "message"})
            part = self._part(item, "summary" if reasoning else "content",
                              obj.get("summary_index" if reasoning else "content_index", 0))
            field = "refusal" if "refusal" in kind else "text"
            part["type"] = "summary_text" if reasoning else "refusal" if field == "refusal" else "output_text"
            if kind.endswith(".delta"):
                value = obj.get("delta", "")
                if isinstance(value, str):
                    part[field] = part.get(field, "") + value
                    delta = bool(value)
            else:
                part[field] = obj.get(field, part.get(field, ""))
        elif kind in {"response.function_call_arguments.delta", "response.function_call_arguments.done"}:
            item = self.items.setdefault(index, {"type": "function_call", "id": obj.get("item_id")})
            value = obj.get("delta", "")
            if kind.endswith(".delta") and isinstance(value, str):
                item["arguments"] = item.get("arguments", "") + value
                delta = bool(value)
            else:
                item["arguments"] = obj.get("arguments", item.get("arguments", ""))
        elif kind == "error":
            self.error = obj
            self.status = "failed"
        elif kind not in {"response.created", "response.in_progress", "response.completed", "response.failed", "response.incomplete"}:
            self.unknown_events.append(obj)
        return delta, usage

    @staticmethod
    def _part(item: dict, field: str, index: int) -> dict:
        index = index if isinstance(index, int) and 0 <= index <= 10000 else 0
        parts = item.setdefault(field, [])
        while len(parts) <= index:
            parts.append({})
        return parts[index]

    def message(self) -> dict | None:
        return response_message([self.items[i] for i in sorted(self.items)])
