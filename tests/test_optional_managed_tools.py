"""Clients without the OpenSpec bundle still support native execution."""

import asyncio
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import HTTPException

from sona_code.config import AppConfig, UpstreamConfig
from sona_code.workspace.commands import CommandManager
from sona_code.workspace.lifecycle import ExecutionLedger
from tests.test_workspace_completion import run_node


async def test_message_dispatch_starts_workspace_without_offline_tools(tmp_path, monkeypatch):
    from sona_code.workspace import manager as workspace_module
    from sona_code.openspec.projects import OpenSpecProjects

    monkeypatch.setenv("SONACODE_BUNDLED_OPENSPEC", str(tmp_path / "absent-tools"))
    spawned = []
    requests = []
    class Scope:
        def spawn(self, argv, **kwargs):
            spawned.append((argv, kwargs["env"]))
            self.process = SimpleNamespace(stdout=None, poll=lambda: None)
            return self.process
        def close(self):
            self.process.poll = lambda: 0
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True} if request.url.path.endswith("prompt_async") else {})
    native_client = httpx.AsyncClient
    monkeypatch.setattr(workspace_module, "ProcessScope", Scope)
    monkeypatch.setattr(workspace_module, "resolve_opencode", lambda cfg: SimpleNamespace(path="opencode", source="bundled"))
    monkeypatch.setattr(workspace_module, "resolve_executable", lambda path: path)
    monkeypatch.setattr(workspace_module, "_build_env", lambda *args: {})
    monkeypatch.setattr(workspace_module.httpx, "AsyncClient",
                        lambda **kwargs: native_client(**kwargs, transport=httpx.MockTransport(respond)))
    manager = workspace_module.WorkspaceManager()
    manager.ledger = ExecutionLedger(str(tmp_path / "config.json"))
    manager.commands = CommandManager(manager.ledger, 1234)
    manager.environment = OpenSpecProjects(str(tmp_path / "config.json")).environment
    cfg = AppConfig(upstreams=[UpstreamConfig(name="test", base_url="http://127.0.0.1:9")], default_upstream="test")
    prompt = {"parts": [{"type": "text", "text": "hello"}]}
    try:
        result = await manager.request(str(tmp_path), cfg, "POST", "/session/ses_1/prompt_async", body=prompt)
        assert result == {"ok": True}
        assert len(spawned) == 1
        assert spawned[0][1]["SONACODE_COMMAND_RUNNER"] == ""
        posted = next(request for request in requests if request.url.path.endswith("prompt_async"))
        assert json.loads(posted.content) == prompt
    finally:
        await manager.shutdown()
        await manager.commands.shutdown()


@pytest.mark.parametrize("binary", [value for value in os.environ.get("OPENCODE_TEST_BINARIES", "").split(os.pathsep) if value] or [None])
async def test_native_message_and_shell_without_openspec(binary, tmp_path, monkeypatch):
    if binary is None:
        pytest.skip("set OPENCODE_TEST_BINARIES for native V1 checks without OpenSpec")
    from sona_code.app import create_app
    from sona_code.cli import DesktopServer
    from sona_code.config import UpstreamModelConfig
    from sona_code.admin.models import native_provider_id

    monkeypatch.setenv("SONACODE_BUNDLED_OPENCODE", binary)
    monkeypatch.setenv("SONACODE_BUNDLED_OPENSPEC", str(tmp_path / "absent-tools"))
    monkeypatch.setenv("OPENCODE_DISABLE_MODELS_FETCH", "1")
    monkeypatch.setenv("OPENCODE_DISABLE_AUTOUPDATE", "1")
    for key in ("OPENCODE_DB", "OPENCODE_CONFIG"):
        monkeypatch.delenv(key, raising=False)
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in [({"role": "assistant", "content": "offline-reply"}, None), ({}, "stop")]:
                chunk = {"id": "chat_test", "object": "chat.completion.chunk", "created": 1,
                         "model": body["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cfg = AppConfig(server={"port": port}, model_settings={"source": "custom"},
                    upstreams=[UpstreamConfig(name="test", base_url=f"http://127.0.0.1:{upstream.server_port}/v1",
                                             route_through_proxy=False, models=[UpstreamModelConfig(id="test-model", context_length=32000, output_length=1000)])],
                    default_upstream="test", terminal={"inject_env": {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": {"*": "allow"}})}})
    app = create_app(cfg, str(tmp_path / "config.json"))
    runtime = app.state.runtime
    server = DesktopServer(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=2))
    serving = asyncio.create_task(server.serve())
    project = tmp_path / "project"
    project.mkdir()
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(.05)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30) as client:
            prefix = "/__recorder/api/workspace/projects"
            response = await client.post(prefix, json={"path": str(project)})
            response.raise_for_status()
            base = prefix + "/" + response.json()["id"] + "/sessions"
            response = await client.post(base, json={})
            native = runtime.workspace._servers.get(str(project.resolve()))
            assert response.is_success, (response.text, native.output.text() if native and native.output else "")
            session = response.json()["id"]
            response = await client.post(base + f"/{session}/prompt", json={"text": "hello", "provider_id": native_provider_id("test"), "model_id": "test-model"})
            response.raise_for_status()
            for _ in range(200):
                response = await client.get(base + f"/{session}/messages")
                response.raise_for_status()
                history = response.json()
                if any(message["info"].get("role") == "assistant" and message["info"].get("time", {}).get("completed") for message in history):
                    break
                await asyncio.sleep(.1)
            assert "offline-reply" in str(history), history
            assert not any(message["info"].get("error") for message in history), history
        command = "Write-Output native-command-check" if os.name == "nt" else "printf native-command-check"
        result = await runtime.workspace.request(str(project), cfg, "POST", f"/session/{session}/shell", body={"agent": "build", "command": command})
        assert "native-command-check" in str(result)
    finally:
        await runtime.aclose()
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        await asyncio.to_thread(upstream.shutdown)
        upstream.server_close()


async def test_missing_runner_keeps_native_config_and_execution_events(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SONACODE_BUNDLED_OPENSPEC", str(tmp_path / "absent-tools"))
    manager = CommandManager(ExecutionLedger(str(tmp_path / "config.json")), 1234)
    inline = {"shell": sys.executable, "permission": {"bash": "ask"}, "plugin": ["other-plugin"]}
    try:
        env = manager.environment("project", str(tmp_path), {
            "OPENCODE_CONFIG_CONTENT": json.dumps(inline), "SONACODE_COMMAND_RUNNER": "stale-runner"})
        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert config["shell"] == sys.executable
        assert config["permission"] == inline["permission"]
        assert config["plugin"][0] == "other-plugin"
        assert config["plugin"][-1].endswith("managed-shell.js")
        assert env["SONACODE_COMMAND_RUNNER"] == ""
        assert manager.owners[env["SONACODE_COMMAND_TOKEN"]] == ("project", str(tmp_path.resolve()))
        assert "using native OpenCode shell" in caplog.text
        with pytest.raises(HTTPException, match="原生命令执行"):
            manager.shell(sys.executable)
    finally:
        await manager.shutdown()


def test_native_shell_hooks_preserve_commands_and_managed_failures_still_fail():
    run_node(r'''
const fs = require("node:fs"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/workspace/managed-shell.js", "utf8");
(async () => {
  const {default: plugin} = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
  process.env.SONACODE_COMMAND_RUNNER = "";
  const calls = [];
  global.fetch = async (url, options) => {
    calls.push({url, body: JSON.parse(options.body)});
    return {ok: true};
  };
  process.env.SONACODE_COMMAND_URL = "http://127.0.0.1:1234/__sona_commands";
  const hooks = await plugin({directory: "project"});
  const config = {shell: "powershell.exe", permission: {bash: "ask"}};
  await hooks.config(config);
  const output = {env: {EXISTING: "keep"}};
  await hooks["shell.env"]({}, output);
  assert.deepEqual(config, {shell: "powershell.exe", permission: {bash: "ask"}});
  assert.deepEqual(output, {env: {EXISTING: "keep"}});
  assert.equal(calls.length, 0);
  await hooks.event({event: {type: "session.status", properties: {sessionID: "ses_1", status: {type: "idle"}}}});
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.status, "idle");
  assert.equal(calls[0].body.session_id, "ses_1");
  // An installed runner's hook failure still prevents native shell execution.
  process.env.SONACODE_COMMAND_RUNNER = "runner.exe";
  global.fetch = async () => ({ok: false});
  const managed = await plugin({directory: "project"});
  const managedConfig = {shell: "powershell.exe"};
  await assert.rejects(managed.config(managedConfig), /managed shell is unavailable/);
  assert.equal(managedConfig.shell, "runner.exe");
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


@pytest.mark.parametrize("kind", ["shell", "opencode"])
async def test_terminal_without_tools_starts_native_process(tmp_path, monkeypatch, kind):
    from sona_code.terminal import manager as terminal_module

    monkeypatch.setenv("SONACODE_BUNDLED_OPENSPEC", str(tmp_path / "absent-tools"))
    calls = []
    class Process:
        pid = 123
        def close(self, force):
            calls.append(("close", force))
    class Pty:
        @staticmethod
        def spawn(argv, **kwargs):
            calls.append((argv, kwargs))
            return Process()
    class Job:
        def assign(self, pid):
            calls.append(("assign", pid))
        def close(self):
            calls.append(("job-close",))
    monkeypatch.setattr(terminal_module, "PtyProcess", Pty)
    monkeypatch.setattr(terminal_module, "resolve_executable", lambda command: sys.executable)
    if os.name == "nt":
        monkeypatch.setattr("sona_code.processes.WindowsJob", Job)
    manager = terminal_module.TerminalManager()
    manager.commands = CommandManager(ExecutionLedger(str(tmp_path / "config.json")), 1234)
    async def pump(session):
        pass
    monkeypatch.setattr(manager, "_pump", pump)
    cfg = AppConfig(terminal={"command_mode": "custom", "command": sys.executable,
                              "shell_command": sys.executable},
                    upstreams=[UpstreamConfig(name="test", base_url="http://127.0.0.1:9")], default_upstream="test")
    try:
        session = await manager.create(cfg, str(tmp_path), kind, 24, 80)
        argv, options = calls[0]
        assert argv[0] == sys.executable
        assert "--sona-pty" not in argv
        assert "SONACODE_PTY_GATE" not in options["env"]
        if kind == "opencode":
            assert options["env"]["SONACODE_COMMAND_RUNNER"] == ""
        assert session.id in manager.sessions
    finally:
        await manager.shutdown()
        await manager.commands.shutdown()
