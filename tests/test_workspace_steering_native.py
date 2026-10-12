"""Opt-in steering contract against real V1 servers and an isolated local model."""

import asyncio
import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from sona_code.app import create_app
from sona_code.admin.models import compile_providers, native_provider_id
from sona_code.config import AppConfig, UpstreamConfig, UpstreamModelConfig
from sona_code.workspace.manager import OpenCodeServer
from sona_code.workspace.routes import _project_id

BINARIES = [path for path in os.environ.get("OPENCODE_TEST_BINARIES", "").split(os.pathsep) if path]


@pytest.mark.parametrize("binary", BINARIES or [None])
@pytest.mark.parametrize("stage", ["stream", "tool", "command"])
async def test_native_steering_joins_running_loop_and_fifo_follows(binary, stage, tmp_path):
    if binary is None:
        pytest.skip("set OPENCODE_TEST_BINARIES for real V1 steering compatibility checks")
    started, release = threading.Event(), threading.Event()
    requests = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            texts = []
            for message in payload["messages"]:
                if message["role"] != "user":
                    continue
                content = message["content"]
                texts.append(content if isinstance(content, str) else
                             "".join(part.get("text", "") for part in content))
            task = bool(payload.get("tools"))
            first = task and not requests
            if task:
                requests.append(texts)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = {"role": "assistant", "content": "answer:" + "|".join(texts)}
            finish = "stop"
            if first and stage == "tool":
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_wait",
                    "type": "function", "function": {"name": "bash", "arguments": json.dumps({
                        "command": "sleep 2", "description": "Wait for test steering"})}}]}
                finish = "tool_calls"
            for choice in [{"index": 0, "delta": delta, "finish_reason": None},
                           {"index": 0, "delta": {}, "finish_reason": finish}]:
                chunk = {"id": "chat_local", "object": "chat.completion.chunk", "created": 1,
                         "model": "steering-model", "choices": [choice]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.flush()
                if first and choice["finish_reason"] is None:
                    started.set()
                    if stage != "tool":
                        release.wait(20)
            self.wfile.write(b"data: [DONE]\n\n")

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    provider = native_provider_id("steering-test")
    config = AppConfig(upstreams=[UpstreamConfig(name="steering-test",
        base_url=f"http://127.0.0.1:{upstream.server_port}/v1", route_through_proxy=False,
        models=[UpstreamModelConfig(id="steering-model", context_length=32000, output_length=1000)])],
        default_upstream="steering-test", model_settings={"source": "custom"},
        recording={"dir": str(tmp_path / "records")})
    inline = {"provider": compile_providers(config), "enabled_providers": [provider],
              "model": f"{provider}/steering-model", "small_model": f"{provider}/steering-model",
              "permission": {"*": "allow"}, "command": {"review": {"template": "$ARGUMENTS"}}}
    env = {**os.environ, "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
           "OPENCODE_CONFIG_CONTENT": json.dumps(inline), "OPENCODE_SERVER_PASSWORD": "local-test-password"}
    for name in ("CONFIG", "CACHE", "DATA", "STATE"):
        env[f"XDG_{name}_HOME"] = str(tmp_path / name.lower())
        config.terminal.inject_env[f"XDG_{name}_HOME"] = env[f"XDG_{name}_HOME"]
    for name in ("OPENCODE_DB", "OPENCODE_CONFIG", "OPENCODE_SERVER_USERNAME"):
        env.pop(name, None)
    project = tmp_path / "project"
    project.mkdir()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (tmp_path / "native.log").open("w")
    process = subprocess.Popen([binary, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                               cwd=project, env=env, stdout=log, stderr=log)
    app = create_app(config, str(tmp_path / "config.json"))
    runtime = app.state.runtime
    runtime.terminal_projects.add(str(project), binary)
    queue = runtime.workspace_queue
    queue._start = lambda key: None
    event_task = None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=20,
                                     auth=("opencode", "local-test-password")) as native:
            for _ in range(100):
                assert process.poll() is None, (tmp_path / "native.log").read_text()[-1000:]
                try:
                    if (await native.get("/global/health")).status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                await asyncio.sleep(.1)
            doc = (await native.get("/doc")).json()
            assert {"/session/{sessionID}/prompt_async", "/session/{sessionID}/command"} <= doc["paths"].keys()
            runtime.workspace._servers[str(project)] = OpenCodeServer(process, native, port, project=str(project))
            session = (await native.post("/session", json={})).json()["id"]
            tool_running = asyncio.Event()
            events_ready = asyncio.Event()

            async def collect_events():
                async with native.stream("GET", "/event", timeout=None) as response:
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        events_ready.set()
                        event = json.loads(line[6:])
                        part = event.get("properties", {}).get("part", {})
                        if (part.get("sessionID") == session and part.get("tool") == "bash"
                                and part.get("state", {}).get("status") == "running"):
                            tool_running.set()

            event_task = asyncio.create_task(collect_events())
            await asyncio.wait_for(events_ready.wait(), 5)
            project_id = _project_id(str(project))
            base = f"/__recorder/api/workspace/projects/{project_id}/sessions/{session}"
            key = f"{project_id}:{session}"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://app") as client:
                first = await client.post(base + "/send", json={"payload": {"text": "first"}})
                assert first.status_code == 202
                assert await asyncio.to_thread(started.wait, 15)
                if stage == "tool":
                    await asyncio.wait_for(tool_running.wait(), 5)
                assert (await native.get("/session/status")).json()[session]["type"] == "busy"
                queued = await client.post(base + "/queue", json={"payload": {"text": "later"}})
                assert queued.status_code == 202
                second_body = ({"kind": "command", "payload": {"command": "review", "arguments": "second"}}
                               if stage == "command" else {"payload": {"text": "second"}})
                second = await asyncio.wait_for(client.post(base + "/send", json=second_body), 1)
                third = await asyncio.wait_for(client.post(base + "/send", json={"payload": {"text": "third"}}), 1)
                assert second.status_code == third.status_code == 202
                expected = {second.json()["message_id"], third.json()["message_id"]}
                for _ in range(100):
                    messages = (await client.get(base + "/messages")).json()
                    if expected <= {message["info"]["id"] for message in messages}:
                        break
                    await asyncio.sleep(.02)
                else:
                    pytest.fail("Busy V1 session did not persist steering messages")
                assert not any(part.get("text") == "later" for message in messages for part in message["parts"])
                assert (await native.get("/session/status")).json()[session]["type"] == "busy"
                release.set()
                for _ in range(150):
                    await queue._step(key)
                    if not queue.snapshot(project_id, session)["items"]:
                        break
                    await asyncio.sleep(.1)
                assert queue.snapshot(project_id, session) == {"items": [], "paused": False, "error": ""}
                assert len(requests) == 3, "Steering must share the running loop, then execute one queued turn"
                assert any("second" in text for text in requests[1])
                assert any("third" in text for text in requests[1])
                assert "later" not in "|".join(requests[1])
                assert "later" in "|".join(requests[2])
    finally:
        release.set()
        if event_task:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
        await queue.shutdown()
        runtime.workspace._servers.clear()
        await runtime.upstream_client.aclose()
        process.terminate()
        try:
            await asyncio.to_thread(process.wait, 5)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait)
        log.close()
        await asyncio.to_thread(upstream.shutdown)
        upstream.server_close()
