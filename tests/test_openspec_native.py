"""Opt-in real OpenCode tools + bundled CLI, using only a local scripted provider.

Set OPENSPEC_TEST_BUNDLE and OPENCODE_TEST_BINARIES to enable.
"""
import json
import os
import socket
import subprocess
import threading
import time
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.admin.models import native_provider_id
from sona_code.config import AppConfig, TerminalConfig, UpstreamConfig, UpstreamModelConfig, save_config
from sona_code.openspec.bundle import WORKFLOWS


@pytest.mark.parametrize("binary", [p for p in os.environ.get("OPENCODE_TEST_BINARIES", "").split(os.pathsep) if p] or [None])
def test_native_offline_workflow_and_persisted_history(binary, tmp_path, monkeypatch):
    bundle = os.environ.get("OPENSPEC_TEST_BUNDLE")
    if not binary or not bundle:
        pytest.skip("set OPENSPEC_TEST_BUNDLE and OPENCODE_TEST_BINARIES")
    monkeypatch.setenv("SONACODE_BUNDLED_OPENSPEC", bundle)
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))
    monkeypatch.setenv("SONACODE_ORIGINAL_XDG_CONFIG_HOME", str(tmp_path / "original"))
    monkeypatch.setenv("OPENCODE_DISABLE_MODELS_FETCH", "1")
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    project = tmp_path / "中文 offline project"
    project.mkdir()
    change = project / "openspec/changes/offline-check"
    captured, steps = [], []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append(body)
            step = steps.pop(0) if steps else None
            delta = {"role": "assistant", "content": "done"} if step is None else {
                "role": "assistant", "tool_calls": [{"index": 0, "id": f"call_{len(captured)}", "type": "function",
                    "function": {"name": step[0], "arguments": json.dumps(step[1])}}]}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for content, reason in [(delta, None), ({}, "tool_calls" if step else "stop")]:
                chunk = {"id": "chat_mock", "object": "chat.completion.chunk", "created": 1, "model": body["model"],
                         "choices": [{"index": 0, "delta": content, "finish_reason": reason}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    cfg = AppConfig(default_upstream="test", upstreams=[UpstreamConfig(name="test",
        base_url=f"http://127.0.0.1:{upstream.server_port}/v1", route_through_proxy=False,
        api_key="local-test-key", models=[UpstreamModelConfig(id="chat-test", context_length=32000, output_length=2000)])],
        terminal=TerminalConfig(command_mode="custom", command=binary, route_through_proxy=False,
            inject_env={"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": "allow"})}))
    cfg.model_settings.source = "custom"
    config_path = tmp_path / "app/config.json"
    app = create_app(cfg, str(config_path))
    sidecar = os.environ.get("OPENSPEC_TEST_SIDECAR")
    process = log = None
    if sidecar:
        save_config(cfg, str(config_path))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        log = (tmp_path / "sidecar.log").open("w", encoding="utf-8")
        process = subprocess.Popen([sidecar, "--config", str(config_path), "--port", str(port)], stdout=log, stderr=log)
        connection = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=180, trust_env=False)
        for _ in range(150):
            try:
                if connection.get("/__recorder/api/ping").status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(.1)
        else:
            process.terminate()
            pytest.fail("packaged sidecar startup failed")
    else:
        connection = TestClient(app)
    def bash(command):
        return ("bash", {"command": command, "description": "Exercise bundled OpenSpec without system Node/npm"})
    def write(path, content):
        return ("write", {"filePath": str(path), "content": content})
    try:
        with (nullcontext(connection) if sidecar else connection) as client:
            prefix = "/__recorder/api/workspace/projects"
            pid = client.post(prefix, json={"path": str(project)}).json()["id"]
            base = f"{prefix}/{pid}"
            # Populate a native cache first; enable must restart it.
            assert client.get(base + "/commands").status_code == 200
            enabled = client.post(base + "/openspec/enable")
            assert enabled.status_code == 200 and enabled.json()["state"] == "ready", enabled.text
            assert {item["name"] for item in client.get(base + "/commands").json()} >= set(WORKFLOWS)
            proposal = "## Why\nVerify offline integration.\n\n## What Changes\nAdd an offline greeting.\n\n## Capabilities\n### New Capabilities\n- `greeting`: Provide greeting.\n### Modified Capabilities\nNone.\n\n## Impact\nLocal source only.\n"
            spec = "## ADDED Requirements\n\n### Requirement: Greeting\nThe system SHALL return a greeting.\n\n#### Scenario: Read greeting\n- **WHEN** a greeting is requested\n- **THEN** return hello\n"
            stages = [
                ("propose", [bash("openspec new change offline-check"), write(change / "proposal.md", proposal),
                    write(change / "design.md", "## Context\nOffline test.\n## Goals / Non-Goals\nGreeting.\n## Decisions\nUse a constant.\n## Risks / Trade-offs\nNone.\n"),
                    write(change / "specs/greeting/spec.md", spec), write(change / "tasks.md", "## 1. Implementation\n- [ ] 1.1 Add greeting\n")]),
                ("apply", [write(project / "greeting.txt", "hello\n"), write(change / "tasks.md", "## 1. Implementation\n- [x] 1.1 Add greeting\n")]),
                ("verify", [bash("openspec validate offline-check --strict")]),
                ("archive", [bash("openspec archive offline-check --yes")]),
            ]
            for workflow, operations in stages:
                steps[:] = operations
                session = client.post(base + "/sessions", json={"title": workflow}).json()["id"]
                start = len(captured)
                result = client.post(base + f"/sessions/{session}/command", json={"command": f"opsx-{workflow}",
                    "arguments": "offline-check", "provider_id": native_provider_id("test"), "model_id": "chat-test", "agent": "build"})
                assert result.status_code == 200, result.text
                messages = client.get(base + f"/sessions/{session}/messages").json()
                assert not steps and len(captured) > start
                assistant = result.json()
                assert not assistant.get("info", {}).get("error"), result.text
                # Tool cards carry actual native status, including bash CLI errors.
                parts = [part for message in messages for part in message.get("parts", [])]
                tools = [part for part in parts if part.get("type") == "tool"]
                assert tools and all(part["state"]["status"] == "completed" for part in tools), tools
                assert f"opsx-{workflow}" in json.dumps(messages), messages
            assert (project / "greeting.txt").read_text() == "hello\n"
            assert not change.exists()
            assert list((project / "openspec/changes/archive").glob("*-offline-check/proposal.md"))
            assert "Greeting" in (project / "openspec/specs/greeting/spec.md").read_text()
            assert client.post(base + "/openspec/disable").json()["state"] == "disabled"
            assert not any(item["name"] in WORKFLOWS for item in client.get(base + "/commands").json())
    finally:
        upstream.shutdown()
        upstream.server_close()
        if process:
            connection.close()
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
            else:
                process.terminate()
            process.wait(timeout=10)
            log.close()
