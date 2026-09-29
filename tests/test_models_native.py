"""Opt-in V1 contract and real SDK requests against isolated OpenCode binaries.

OPENCODE_TEST_BINARIES=/path/one:/path/two pytest tests/test_models_native.py -q
No real API credentials or external model endpoints are used.
"""
import json
import os
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from sona_code.admin.models import compile_providers, native_provider_id
from sona_code.config import AppConfig, UpstreamConfig, UpstreamModelConfig

BINARIES = [p for p in os.environ.get("OPENCODE_TEST_BINARIES", "").split(os.pathsep) if p]


@pytest.mark.parametrize("binary", BINARIES or [None])
@pytest.mark.parametrize("proxied", [False, True])
@pytest.mark.parametrize("provider_key", ["", "provider-test-key"])
def test_native_provider_protocol_keys_and_variants(binary, proxied, provider_key, tmp_path):
    if binary is None:
        pytest.skip("set OPENCODE_TEST_BINARIES to run isolated native V1 compatibility checks")
    captured = []
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append((self.path, self.headers.get("Authorization"), body))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            if self.path.endswith("/responses"):
                item = {"type": "message", "id": "msg_mock", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "done", "annotations": []}]}
                response = {"id": "resp_mock", "object": "response", "created_at": 1, "status": "completed", "model": body["model"],
                            "output": [item], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
                events = [{"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
                          {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}},
                          {"type": "response.content_part.added", "item_id": "msg_mock", "output_index": 0, "content_index": 0,
                           "part": {"type": "output_text", "text": "", "annotations": []}},
                          {"type": "response.output_text.delta", "item_id": "msg_mock", "output_index": 0, "content_index": 0, "delta": "done"},
                          {"type": "response.output_item.done", "output_index": 0, "item": item},
                          {"type": "response.completed", "response": response}]
                for i, event in enumerate(events):
                    event["sequence_number"] = i
                    self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
            else:
                for obj in [{"id": "chat_mock", "object": "chat.completion.chunk", "created": 1, "model": body["model"],
                             "choices": [{"index": 0, "delta": {"role": "assistant", "content": "done"}, "finish_reason": None}]},
                            {"id": "chat_mock", "object": "chat.completion.chunk", "created": 1, "model": body["model"],
                             "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}]:
                    self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    cfg = AppConfig(upstreams=[UpstreamConfig(name="test", base_url=f"http://127.0.0.1:{upstream.server_port}/v1",
        route_through_proxy=False, api_key=provider_key, models=[
            UpstreamModelConfig(id="chat-one", api_key="model-test-key", context_length=32000, output_length=1000,
                reasoning=True, reasoning_efforts=["low", "high"]),
            UpstreamModelConfig(id="deepseek-flash", context_length=32000, output_length=1000),
            UpstreamModelConfig(id="gpt-5-unconfigured", api_type="responses", reasoning=True,
                                context_length=32000, output_length=1000)])], default_upstream="test")
    pid = native_provider_id("test")
    proxy = None
    if proxied:
        from tests.test_integration import ServerThread
        from sona_code.app import create_app
        cfg.upstreams[0].route_through_proxy = True
        cfg.recording.dir = str(tmp_path / "records")
        proxy_app = create_app(cfg, str(tmp_path / "cfg.json"))
        proxy = ServerThread(proxy_app, "native-model-proxy")
        cfg.server.port = proxy.start()
    inline = {"provider": compile_providers(cfg), "enabled_providers": [pid], "model": pid + "/chat-one", "small_model": pid + "/chat-one"}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
           "OPENCODE_CONFIG_CONTENT": json.dumps(inline), "OPENAI_API_KEY": "wrong-environment-key", "OPENCODE_SERVER_PASSWORD": "local-test-password"}
    for name in ("CONFIG", "CACHE", "DATA", "STATE"):
        env[f"XDG_{name}_HOME"] = str(tmp_path / name.lower())
    env.pop("OPENCODE_CONFIG", None)
    project = tmp_path / "project"
    project.mkdir()
    log = (tmp_path / "native.log").open("w")
    process = subprocess.Popen([binary, "serve", "--hostname", "127.0.0.1", "--port", str(port)], env=env, cwd=project, stdout=log, stderr=log)
    try:
        # Fresh isolated state may install native SDK dependencies on first use.
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", auth=("opencode", "local-test-password"), timeout=20, trust_env=False) as client:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail("native startup failed: " + (tmp_path / "native.log").read_text()[-1000:])
                try:
                    if client.get("/global/health").status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(.1)
            doc = client.get("/doc").json()
            assert "/config/providers" in doc["paths"] and "/session/{sessionID}/prompt_async" in doc["paths"]
            providers = client.get("/config/providers").json()["providers"]
            provider = next(p for p in providers if p["id"] == pid)
            assert set(provider["models"]["chat-one"]["variants"]) == {"low", "high"}
            for model, endpoint, key, variant in [("chat-one", "/v1/chat/completions", "Bearer model-test-key", "high"),
                                                  ("deepseek-flash", "/v1/chat/completions", f"Bearer {provider_key}" if provider_key else None if proxied else "", None),
                                                  ("gpt-5-unconfigured", "/v1/responses", f"Bearer {provider_key}" if provider_key else None if proxied else "", None)]:
                session = client.post("/session", json={"title": "SDK contract"}).json()["id"]
                response = client.post(f"/session/{session}/prompt_async", json={"model": {"providerID": pid, "modelID": model},
                    "parts": [{"type": "text", "text": "say done"}], **({"variant": variant} if variant else {})})
                assert response.status_code < 300, response.text
                for _ in range(150):
                    messages = client.get(f"/session/{session}/message").json()
                    assistants = [m for m in messages if m.get("info", {}).get("role") == "assistant"]
                    if any(m["info"].get("error") or m["info"].get("time", {}).get("completed") for m in assistants):
                        break
                    time.sleep(.1)
                assert assistants and not any(m["info"].get("error") for m in assistants), json.dumps(assistants)
                assert any(p.get("type") == "text" and "done" in p.get("text", "") for m in assistants for p in m.get("parts", [])), assistants
                matching = [r for r in captured if r[2].get("model") == model]
                assert matching and all(r[0] == endpoint and r[1] == key for r in matching), matching
                if variant:
                    assert matching[0][2]["reasoning_effort"] == "high"
                else:
                    assert not matching[0][2].get("reasoning", {}).get("effort"), matching
                    assert not matching[0][2].get("reasoning_effort"), matching
                # V1 message deletion clears history without applying file reverts.
                assert "delete" in doc["paths"]["/session/{sessionID}/message/{messageID}"]
                for message in reversed(messages):
                    deleted = client.delete(f"/session/{session}/message/{message['info']['id']}")
                    assert deleted.status_code == 200, deleted.text
                assert client.get(f"/session/{session}/message").json() == []
            if proxied:
                store = proxy_app.state.runtime.store
                for _ in range(100):
                    rows = [r for date in store.available_dates() for r in store.read_index(date)]
                    if any(r.get("model") == "gpt-5-unconfigured" for r in rows):
                        break
                    time.sleep(.05)
                assert any(r.get("model") == "chat-one" for r in rows)
                response_record = store.load_call(next(r["id"] for r in rows if r.get("model") == "gpt-5-unconfigured"))
                assert response_record["protocol"] == "responses"
                assert response_record["usage"]["prompt_tokens"] == 1
                assert response_record["response"]["parsed"]["message"]["content"] == "done"
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        log.close()
        if proxy:
            proxy.stop()
        upstream.shutdown()
        upstream.server_close()
