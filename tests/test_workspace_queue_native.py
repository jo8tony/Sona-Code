"""Opt-in queue contract against real V1 servers and a local streaming model."""

import asyncio
import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from llm_api_proxy_recorder.admin.models import compile_providers, native_provider_id
from llm_api_proxy_recorder.config import AppConfig, UpstreamConfig, UpstreamModelConfig
from llm_api_proxy_recorder.workspace.queue import WorkspaceQueue

BINARIES = [path for path in os.environ.get("OPENCODE_TEST_BINARIES", "").split(os.pathsep) if path]


@pytest.mark.parametrize("binary", BINARIES or [None])
async def test_native_v1_queue_waits_for_stream_completion(binary, tmp_path):
    if binary is None:
        pytest.skip("set OPENCODE_TEST_BINARIES for real V1 queue compatibility checks")
    started, release = threading.Event(), threading.Event()
    seen = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            text = payload["messages"][-1]["content"]
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text)
            # Native title generation also sees the first prompt, but has no task tools.
            task_request = bool(payload.get("tools"))
            if task_request and text in {"first", "second", "third"}:
                seen.append(text)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"id": "chat_local", "object": "chat.completion.chunk", "created": 1, "model": "queue-model",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": "done"}, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.flush()
            if task_request and text == "first":
                started.set()
                release.wait(20)
            chunk["choices"] = [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    cfg = AppConfig(upstreams=[UpstreamConfig(name="queue-test", base_url=f"http://127.0.0.1:{upstream.server_port}/v1",
        route_through_proxy=False, models=[UpstreamModelConfig(id="queue-model", context_length=32000, output_length=1000)])],
        default_upstream="queue-test")
    provider = native_provider_id("queue-test")
    inline = {"provider": compile_providers(cfg), "enabled_providers": [provider],
              "model": f"{provider}/queue-model", "small_model": f"{provider}/queue-model"}
    env = {**os.environ, "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
           "OPENCODE_CONFIG_CONTENT": json.dumps(inline), "OPENCODE_SERVER_PASSWORD": "local-test-password"}
    for name in ("CONFIG", "CACHE", "DATA", "STATE"):
        env[f"XDG_{name}_HOME"] = str(tmp_path / name.lower())
    env.pop("OPENCODE_CONFIG", None)
    env.pop("OPENCODE_SERVER_USERNAME", None)
    project = tmp_path / "project"
    project.mkdir()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (tmp_path / "native.log").open("w")
    process = subprocess.Popen([binary, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                               cwd=project, env=env, stdout=log, stderr=log)
    queue = WorkspaceQueue(str(tmp_path / "config.json"))
    queue._start = lambda key: None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", auth=("opencode", "local-test-password"),
                                     trust_env=False, timeout=20) as client:
            for _ in range(100):
                assert process.poll() is None, (tmp_path / "native.log").read_text()[-1000:]
                try:
                    if (await client.get("/global/health")).status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                await asyncio.sleep(.1)
            doc = (await client.get("/doc")).json()
            assert {"/session/status", "/session/{sessionID}/prompt_async", "/session/{sessionID}/command"} <= doc["paths"].keys()
            session_id = (await client.post("/session", json={})).json()["id"]
            base = f"/session/{session_id}"

            async def inspect(entry):
                statuses = (await client.get("/session/status")).json()
                messages = (await client.get(base + "/message")).json()
                return statuses, messages

            async def dispatch(entry, item):
                response = await client.post(base + "/prompt_async", json={"messageID": item["message_id"],
                    "model": {"providerID": provider, "modelID": "queue-model"},
                    "parts": [{"type": "text", "text": item["payload"]["text"]}]})
                response.raise_for_status()

            queue.inspect, queue.dispatch = inspect, dispatch
            response = await client.post(base + "/prompt_async", json={"parts": [{"type": "text", "text": "first"}]})
            response.raise_for_status()
            assert await asyncio.to_thread(started.wait, 15)
            await queue.add("p", str(project), session_id, "prompt", {"text": "second"})
            await queue.add("p", str(project), session_id, "prompt", {"text": "third"})
            key = f"p:{session_id}"
            await queue._step(key)
            assert seen == ["first"]
            assert len((await client.get(base + "/message")).json()) == 2
            release.set()
            for _ in range(150):
                if not queue.snapshot("p", session_id)["items"]:
                    break
                await queue._step(key)
                await asyncio.sleep(.1)
            assert not queue.snapshot("p", session_id)["items"], queue.snapshot("p", session_id)
            assert seen == ["first", "second", "third"]
    finally:
        release.set()
        await queue.shutdown()
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 5)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait)
        log.close()
        await asyncio.to_thread(upstream.shutdown)
        upstream.server_close()
