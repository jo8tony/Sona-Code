"""Responses parsing, usage, arbitrary SSE chunking and history references."""
import json

from sona_code.recording.parse import parse_request_body, parse_nonsse_body, parse_sse_captured
from sona_code.recording.sse import SSEParser
from sona_code.recording.models import CallRecord, RequestInfo
from sona_code.recording.store import CallStore
from sona_code.proxy.handler import _process_and_finalize
from sona_code.config import RecordingConfig

OUTPUT = [
    {"type": "reasoning", "id": "r", "summary": [{"type": "summary_text", "text": "考虑一下"}]},
    {"type": "message", "id": "m", "role": "assistant", "content": [{"type": "output_text", "text": "你好"}]},
    {"type": "function_call", "id": "f", "call_id": "call_1", "name": "read", "arguments": '{"file":"a"}'},
]
USAGE = {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30,
         "input_tokens_details": {"cached_tokens": 5}, "output_tokens_details": {"reasoning_tokens": 3}}
RESPONSE = {"id": "resp_one", "object": "response", "status": "completed", "output": OUTPUT, "usage": USAGE}


def test_request_normalization_preserves_images_tools_and_original():
    obj = {"model": "test", "instructions": "system", "input": [
        {"role": "user", "content": [{"type": "input_text", "text": "看图片"}, {"type": "input_image", "image_url": "data:fake"}]},
        OUTPUT[2], {"type": "function_call_output", "call_id": "call_1", "output": "contents"}],
        "previous_response_id": "resp_previous"}
    parsed = parse_request_body(json.dumps(obj))
    assert parsed["protocol"] == "responses"
    assert parsed["messages"][0] == {"role": "system", "content": "system"}
    assert parsed["messages"][1]["content"][1]["image_url"]["url"] == "data:fake"
    assert parsed["messages"][2]["tool_calls"][0]["id"] == "call_1"
    assert parsed["messages"][3]["tool_call_id"] == "call_1"
    assert obj["input"][0]["content"][1]["type"] == "input_image"


def test_nonsse_and_sse_normalize_same_without_duplicate_final_output():
    result = parse_nonsse_body(json.dumps(RESPONSE).encode())
    events = [{"type": "response.created", "response": {"id": "resp_one"}},
              {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "delta": "你"},
              {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "delta": "好"},
              {"type": "response.reasoning_summary_text.delta", "output_index": 0, "summary_index": 0, "delta": "考虑一下"},
              {"type": "response.output_item.added", "output_index": 2, "item": {**OUTPUT[2], "arguments": ""}},
              {"type": "response.function_call_arguments.delta", "output_index": 2, "delta": '{"file":'},
              {"type": "response.function_call_arguments.delta", "output_index": 2, "delta": '"a"}'},
              {"type": "response.completed", "response": RESPONSE}]
    raw = b"".join(('event: ' + e['type'] + '\r\ndata: ' + json.dumps(e, ensure_ascii=False) + '\r\n\r\n').encode() for e in events)
    for step in (1, 3, 19, len(raw)):
        parsed = parse_sse_captured([(float(i), raw[i:i+step]) for i in range(0, len(raw), step)])
        parser = parsed["parser"]
        assert parser.assembled_message() == result["message"]
        assert parser.usage == {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30, "cached_tokens": 5, "reasoning_tokens": 3}
        assert parser.saw_done and parsed["ttft_at"] is not None
        assert parser.responses.response_id == "resp_one"


def test_partial_failed_and_unknown_events_remain_available():
    parser = SSEParser()
    for obj in [{"type": "response.output_text.delta", "delta": "partial"},
                {"type": "response.new_event", "unknown": "keep"},
                {"type": "response.failed", "response": {"status": "failed", "error": {"message": "bad"}}}]:
        parser.feed(("data: " + json.dumps(obj) + "\n\n").encode())
    assert parser.assembled_message()["content"] == "partial"
    assert parser.responses.unknown_events == [{"type": "response.new_event", "unknown": "keep"}]
    assert parser.responses.error == {"message": "bad"}
    assert parser.finish_reason == "failed"


def test_response_history_and_missing_parent(tmp_path):
    store = CallStore(tmp_path)
    cfg = RecordingConfig(dir=str(tmp_path))
    def finalize(call_id, previous=None, header=None):
        record = CallRecord(id=call_id, started_at="2026-09-26T00:00:00+08:00", status="ok", upstream_name="p", upstream_url="http://localhost/responses",
                            request=RequestInfo(method="POST", path="/responses", body=json.dumps({"model": "m", "input": "hello", "previous_response_id": previous})))
        response = {**RESPONSE, "id": call_id}
        _process_and_finalize(store, record, {"rc": cfg, "t_start": 1, "t_sent": 1, "t_end": 2, "chunks": [(2, json.dumps(response).encode())],
                              "is_sse": False, "status": "ok", "session_header": header})
        return store.load_call(call_id)
    first = finalize("c20260926_one")
    second = finalize("c20260926_two", "c20260926_one")
    assert second["session_key"] == first["session_key"]
    assert len(second["request"]["parsed"]["messages"]) == 3
    assert not second["history_incomplete"]
    third = finalize("c20260926_three", "missing", "native-session")
    assert third["history_incomplete"] and third["session_key"].startswith("h")


def test_finalize_reports_failed_and_interrupted_responses(tmp_path):
    store = CallStore(tmp_path)
    cfg = RecordingConfig(dir=str(tmp_path))
    partial = b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
    failed = json.dumps({"object": "response", "status": "failed", "output": []}).encode()
    for suffix, raw, is_sse in [("partial", partial, True), ("failed", failed, False)]:
        record = CallRecord(id=f"c20260926_{suffix}", started_at="2026-09-26T00:00:00+08:00",
                            status="ok", upstream_name="p", upstream_url="http://localhost/responses",
                            request=RequestInfo(method="POST", path="/responses", body='{"input":"hello"}'))
        _process_and_finalize(store, record, {"rc": cfg, "t_start": 1, "t_sent": 1, "t_end": 2,
                              "chunks": [(2, raw)], "is_sse": is_sse, "status": "ok"})
        saved = store.load_call(record.id)
        assert saved["status"] == "error" and saved["error"]
        if is_sse:
            assert saved["response"]["parsed"]["message"]["content"] == "partial"
            assert saved["error"]["type"] == "responses_incomplete_stream"
