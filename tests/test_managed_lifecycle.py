"""Cancellation owns descendants and projects, and repairs interrupted display state."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
import httpx

from sona_code.app import create_app
from sona_code.config import AppConfig, UpstreamConfig, UpstreamModelConfig
from sona_code.processes import ProcessScope
from sona_code.workspace.commands import CommandManager
from sona_code.workspace.lifecycle import ExecutionLedger
from sona_code.workspace.manager import OpenCodeServer, WorkspaceManager
from sona_code.cli import DesktopServer
from sona_code.terminal.manager import TerminalManager


def test_ledger_recovery_only_ends_interrupted_execution(tmp_path):
    ledger = ExecutionLedger(str(tmp_path / "config.json"))
    ledger.begin(str(tmp_path), "session")
    created = ledger.runs[ledger.key(str(tmp_path), "session")][-1]["start"]
    recovered = ExecutionLedger(str(tmp_path / "config.json"))
    messages = [{"info": {"role": "assistant", "time": {"created": created}},
                 "parts": [{"type": "tool", "state": {"status": "running"}}]},
                {"info": {"role": "assistant", "time": {"created": created - 1000, "completed": created - 900}},
                 "parts": [{"type": "tool", "state": {"status": "completed", "output": "kept"}}]}]
    result = recovered.annotate(str(tmp_path), "session", messages)
    assert result[0]["info"]["time"]["completed"] >= created
    assert result[0]["parts"][0]["state"]["metadata"]["interrupted"]
    assert result[1] == messages[1]
    assert messages[0]["parts"][0]["state"]["status"] == "running"


async def test_stop_scope_failure_does_not_skip_other_sessions(tmp_path):
    commands = CommandManager(ExecutionLedger(str(tmp_path / "config.json")), 1234)
    seen = []
    class Scope:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail
        def close(self):
            seen.append(self.name)
            if self.fail:
                raise OSError("blocked")
    commands.scopes = {("owner", "one"): {Scope("one", True)}, ("owner", "two"): {Scope("two")}}
    with pytest.raises(OSError):
        await commands.shutdown()
    assert set(seen) == {"one", "two"}
    assert ("owner", "one") in commands.scopes


@pytest.mark.parametrize("fail", [False, True])
async def test_invalid_native_status_still_closes_commands_client_and_process(fail):
    seen = []
    class Commands:
        async def stop_owner(self, owner):
            seen.append("commands")
            if fail:
                raise OSError("cleanup blocked")
    class Scope:
        def close(self):
            seen.append("process")
    client = httpx.AsyncClient(base_url="http://native", transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
    manager = WorkspaceManager()
    manager.commands = Commands()
    server = OpenCodeServer(SimpleNamespace(), client, 1234, scope=Scope(), project="project")
    if fail:
        with pytest.raises(OSError):
            await manager._close_server(server)
    else:
        await manager._close_server(server)
    assert seen == ["commands", "process"]
    assert client.is_closed


async def test_recycled_server_does_not_reuse_revoked_tokens_or_stop_gates(tmp_path, monkeypatch):
    manager = CommandManager(ExecutionLedger(str(tmp_path / "config.json")), 1234)
    monkeypatch.setattr(manager, "helper", lambda: Path(sys.executable))
    try:
        first = manager.environment("project", str(tmp_path), {})
        manager.blocked.update({("project", "old-session"), ("other", "old-session")})
        await manager.stop_owner("project")
        second = manager.environment("project", str(tmp_path), {})
        assert first['SONACODE_COMMAND_TOKEN'] not in manager.owners
        assert second['SONACODE_COMMAND_TOKEN'] in manager.owners
        assert ("project", "old-session") not in manager.blocked
        assert ("other", "old-session") in manager.blocked
    finally:
        await manager.shutdown()


async def test_cancelled_terminal_start_reaps_the_eventual_process(tmp_path, monkeypatch):
    import sona_code.terminal.manager as terminal_module
    started, release = threading.Event(), threading.Event()
    closed = []
    class Process:
        def close(self, force):
            closed.append(force)
    class Pty:
        @staticmethod
        def spawn(*args, **kwargs):
            started.set()
            assert release.wait(5)
            return Process()
    monkeypatch.setattr(terminal_module, "PtyProcess", Pty)
    monkeypatch.setattr(terminal_module.sys, "platform", "win32")
    cfg = AppConfig(terminal={"shell_command": sys.executable}, upstreams=[UpstreamConfig(name="test", base_url="http://127.0.0.1:9")], default_upstream="test")
    manager = TerminalManager()
    task = asyncio.create_task(manager.create(cfg, str(tmp_path), "shell", 24, 80))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]
    assert not manager.sessions


@pytest.mark.skipif(os.name != "nt" or bool(os.environ.get("SONACODE_SKIP_TERMINAL_TESTS")), reason="Windows ConPTY ownership regression")
async def test_closing_terminal_reaps_its_background_command(tmp_path):
    from sona_code.processes import kernel
    from sona_code.terminal.manager import detect_shell
    import ctypes
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    manager = TerminalManager()
    manager.commands = CommandManager(ExecutionLedger(str(tmp_path / "config.json")), 1234)
    cfg = AppConfig(terminal={"shell_command": detect_shell()}, upstreams=[UpstreamConfig(name="test", base_url="http://127.0.0.1:9")], default_upstream="test")
    pid_file = tmp_path / "terminal-child.pid"
    child_script = tmp_path / "terminal-child.py"
    child_script.write_text("import os,time,pathlib\npathlib.Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()))\ntime.sleep(60)\n")
    handle = None
    try:
        session = await manager.create(cfg, str(tmp_path), "shell", 24, 80)
        assert await manager.write(session, f"Start-Process -FilePath '{sys.executable}' -ArgumentList @('{child_script}') -WindowStyle Hidden\r") is None
        for _ in range(100):
            if pid_file.exists():
                break
            await asyncio.sleep(.1)
        assert pid_file.exists()
        handle = kernel.OpenProcess(0x00100000, False, int(pid_file.read_text()))
        assert handle
        assert await manager.kill(session.id)
        assert kernel.WaitForSingleObject(handle, 1000) == 0
        assert session.pump_task.done()
        assert session.id not in manager.sessions
    finally:
        await manager.shutdown()
        await manager.commands.shutdown()
        if handle:
            kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows job ownership regression")
def test_job_reaps_background_child_after_parent_exits(tmp_path):
    from sona_code.processes import kernel
    child_file = tmp_path / "pid"
    script = ("import pathlib, subprocess, sys; "
              "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
              "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
              f"pathlib.Path({str(child_file)!r}).write_text(str(child.pid))")
    scope = ProcessScope()
    try:
        process = scope.spawn([sys.executable, "-c", script], stdout=subprocess.DEVNULL)
        process.wait(timeout=10)
        pid = int(child_file.read_text())
        handle = kernel.OpenProcess(0x00100000, False, pid)
        assert handle
        try:
            scope.close()
            import ctypes
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            assert kernel.WaitForSingleObject(handle, 1000) == 0
        finally:
            kernel.CloseHandle(handle)
    finally:
        scope.close()


BINARIES = [value for value in os.environ.get("OPENCODE_TEST_BINARIES", "").split(os.pathsep) if value]


@pytest.mark.parametrize("binary", BINARIES or [None])
async def test_native_managed_shell_abort_and_exit(binary, tmp_path, monkeypatch):
    if binary is None or os.name != "nt":
        pytest.skip("set OPENCODE_TEST_BINARIES for Windows native lifecycle checks")
    monkeypatch.setenv("SONACODE_BUNDLED_OPENCODE", binary)
    monkeypatch.setenv("OPENCODE_DISABLE_MODELS_FETCH", "1")
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        directory = tmp_path / key
        directory.mkdir()
        monkeypatch.setenv(key, str(directory))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    ai_command = "Write-Output AI-runner-check; Start-Sleep 60"
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            delta = {"content": "done"}
            finish = "stop"
            if payload.get("tools") and not any(message.get("role") == "tool" for message in payload['messages']):
                delta = {"tool_calls": [{"index": 0, "id": "managed-command", "type": "function", "function": {
                    "name": "bash", "arguments": json.dumps({"command": ai_command, "description": "Managed lifecycle regression"})}}]}
                finish = "tool_calls"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"id": "chat_test", "object": "chat.completion.chunk", "created": 1, "model": "test-model",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            chunk['choices'] = [{"index": 0, "delta": {}, "finish_reason": finish}]
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
    model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    cfg = AppConfig(server={"port": port}, upstreams=[UpstreamConfig(name="test", base_url=f"http://127.0.0.1:{model.server_port}/v1",
                    route_through_proxy=False, models=[UpstreamModelConfig(id="test-model", context_length=32000, output_length=1000)])], default_upstream="test",
                    terminal={"inject_env": {"OPENCODE_CONFIG_CONTENT": json.dumps({"permission": {"*": "allow"}})}})
    app = create_app(cfg, str(tmp_path / "config.json"))
    runtime = app.state.runtime
    runtime.workspace.environment = None
    server = DesktopServer(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=2))
    serving = asyncio.create_task(server.serve())
    project = tmp_path / "project"
    project.mkdir()
    handles = []
    tasks = []
    from sona_code.processes import kernel
    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(.05)
        native = await runtime.workspace.ensure(str(project), cfg)
        sessions = [(await runtime.workspace.request(str(project), cfg, "POST", "/session", body={}))['id'] for _ in range(2)]
        for index, session in enumerate(sessions):
            pid_file = tmp_path / f"child-{index}.pid"
            child_script = tmp_path / f"child-{index}.py"
            child_script.write_text("import os,time,pathlib\npathlib.Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()))\ntime.sleep(60)\n")
            # Independently started children must stay owned by their sessions.
            command = f"Start-Process -FilePath '{sys.executable}' -ArgumentList @('{child_script}') -WindowStyle Hidden; Start-Sleep 60"
            tasks.append(asyncio.create_task(runtime.workspace.request(str(project), cfg, "POST", f"/session/{session}/shell", body={"agent": "build", "command": command})))
            for _ in range(160):
                if pid_file.exists():
                    break
                if tasks[-1].done():
                    raise AssertionError(await tasks[-1])
                await asyncio.sleep(.1)
            assert pid_file.exists(), native.output.text()
            pid = int(pid_file.read_text())
            handle = kernel.OpenProcess(0x00100000, False, pid)
            assert handle
            handles.append(handle)
        import ctypes
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        await runtime.workspace.stop_session(str(project), cfg, sessions[0])
        assert kernel.WaitForSingleObject(handles[0], 1000) == 0
        assert kernel.WaitForSingleObject(handles[1], 0) == 258
        await asyncio.wait_for(tasks[0], 10)
        history = await runtime.workspace.request(str(project), cfg, "GET", f"/session/{sessions[0]}/message")
        tools = [part for message in history for part in message['parts'] if part['type'] == 'tool']
        assert tools and tools[-1]['state']['metadata'].get('interrupted'), history
        resumed = await runtime.workspace.request(str(project), cfg, "POST", f"/session/{sessions[0]}/shell", body={"agent": "build", "command": "Write-Output resumed"})
        assert "resumed" in str(resumed)
        pid_file = tmp_path / "background.pid"
        child_script = tmp_path / "background.py"
        child_script.write_text("import os,time,pathlib\npathlib.Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()))\ntime.sleep(60)\n")
        command = (f"Start-Process -FilePath '{sys.executable}' -ArgumentList @('{child_script}') -WindowStyle Hidden; "
                   f"while (-not (Test-Path '{pid_file}')) {{ Start-Sleep -Milliseconds 25 }}; Start-Sleep -Milliseconds 400")
        background = asyncio.create_task(runtime.workspace.request(str(project), cfg, "POST", f"/session/{sessions[0]}/shell", body={"agent": "build", "command": command}))
        tasks.append(background)
        for _ in range(160):
            if pid_file.exists():
                break
            await asyncio.sleep(.05)
        assert pid_file.exists()
        handle = kernel.OpenProcess(0x00100000, False, int(pid_file.read_text()))
        assert handle
        handles.append(handle)
        await asyncio.wait_for(background, 5)
        for _ in range(40):
            if kernel.WaitForSingleObject(handle, 0) == 0:
                break
            await asyncio.sleep(.05)
        assert kernel.WaitForSingleObject(handle, 0) == 0
        ai_session = (await runtime.workspace.request(str(project), cfg, "POST", "/session", body={}))['id']
        await runtime.workspace.request(str(project), cfg, "POST", f"/session/{ai_session}/prompt_async", body={"parts": [{"type": "text", "text": "Run the managed test command"}]})
        ai_tools = []
        for _ in range(200):
            history = await runtime.workspace.request(str(project), cfg, "GET", f"/session/{ai_session}/message")
            ai_tools = [part for message in history for part in message['parts'] if part['type'] == 'tool']
            if any("AI-runner-check" in str(part['state'].get('metadata', {}).get('output', '')) for part in ai_tools):
                break
            await asyncio.sleep(.1)
        assert ai_tools and ai_tools[-1]['state']['status'] == 'running', history
        assert (str(project.resolve()), ai_session) in runtime.commands.scopes
        await runtime.workspace.stop_session(str(project), cfg, ai_session)
        history = await runtime.workspace.request(str(project), cfg, "GET", f"/session/{ai_session}/message")
        ai_tools = [part for message in history for part in message['parts'] if part['type'] == 'tool']
        assert ai_tools[-1]['state']['status'] == 'error', history
        assert ai_tools[-1]['state']['metadata'].get('interrupted'), history
        await runtime.aclose()
        assert kernel.WaitForSingleObject(handles[1], 1000) == 0
        assert native.process.poll() is not None
        await asyncio.wait_for(tasks[1], 10)
    finally:
        await runtime.aclose()
        server.should_exit = True
        await asyncio.wait_for(serving, 10)
        await asyncio.gather(*tasks, return_exceptions=True)
        for handle in handles:
            kernel.CloseHandle(handle)
        await asyncio.to_thread(model.shutdown)
        model.server_close()


async def test_shutdown_requires_loopback_owner_token_and_blocks_new_requests(tmp_path, monkeypatch):
    cfg = AppConfig(upstreams=[UpstreamConfig(name="test", base_url="http://127.0.0.1:9")], default_upstream="test")
    app = create_app(cfg, str(tmp_path / "config.json"))
    monkeypatch.setenv("SONACODE_SHUTDOWN_TOKEN", "test-owner-token")
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)), base_url="http://local") as client:
            assert (await client.post("/__recorder/api/shutdown")).status_code == 403
            assert not app.state.runtime.closing
            assert (await client.post("/__recorder/api/shutdown", headers={"Authorization": "Bearer test-owner-token"})).status_code == 200
            assert (await client.get("/__recorder/api/ping")).status_code == 503
            assert (await client.post("/__recorder/api/shutdown", headers={"Authorization": "Bearer test-owner-token"})).status_code == 200
    finally:
        await app.state.runtime.aclose()


@pytest.mark.parametrize("binary", BINARIES or [None])
@pytest.mark.parametrize("exit_mode", ["graceful", "forced", "owner"])
async def test_desktop_backend_exit_reaps_commands(binary, exit_mode, tmp_path):
    if binary is None or os.name != "nt":
        pytest.skip("set OPENCODE_TEST_BINARIES for Windows desktop ownership checks")
    from sona_code.processes import kernel
    import ctypes
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cfg = AppConfig(server={"port": port}, recording={"dir": str(tmp_path / "records")},
                    model_settings={"source": "custom"},
                    upstreams=[UpstreamConfig(name="test", base_url="http://127.0.0.1:9", route_through_proxy=False,
                                             models=[UpstreamModelConfig(id="test-model")])], default_upstream="test")
    config_path = tmp_path / "config.json"
    config_path.write_text(cfg.model_dump_json(), encoding="utf-8")
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]) if exit_mode == "owner" else None
    env = {**os.environ, "SONACODE_DESKTOP_INSTANCE_ID": f"{owner.pid if owner else os.getpid()}-123456",
           "SONACODE_SHUTDOWN_TOKEN": "isolated-test-owner", "SONACODE_BUNDLED_OPENCODE": binary,
           "OPENCODE_DISABLE_MODELS_FETCH": "1"}
    for name in ("CONFIG", "CACHE", "DATA", "STATE"):
        env[f"XDG_{name}_HOME"] = str(tmp_path / name.lower())
    env.pop("OPENCODE_CONFIG", None)
    env.pop("OPENCODE_DB", None)
    log = (tmp_path / "backend.log").open("wb")
    backend = subprocess.Popen([sys.executable, "-m", "sona_code", "--config", str(config_path)],
                               env=env, stdout=log, stderr=log)
    task = None
    child_handle = None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=25) as client:
            for _ in range(120):
                assert backend.poll() is None, (tmp_path / "backend.log").read_text(encoding="utf-8", errors="replace")
                try:
                    response = await client.get("/__recorder/api/ping")
                    if response.status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                await asyncio.sleep(.1)
            root = "/__recorder/api/workspace/projects"
            project = (await client.post(root, json={"path": str(tmp_path)})).json()['id']
            session = (await client.post(f"{root}/{project}/sessions", json={})).json()['id']
            pid_file = tmp_path / "owned.pid"
            child_script = tmp_path / "owned.py"
            child_script.write_text("import os,time,pathlib\npathlib.Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()))\ntime.sleep(60)\n")
            command = f"Start-Process -FilePath '{sys.executable}' -ArgumentList @('{child_script}') -WindowStyle Hidden; Start-Sleep 60"
            task = asyncio.create_task(client.post(f"{root}/{project}/sessions/{session}/shell", json={"command": command}))
            for _ in range(160):
                if pid_file.exists():
                    break
                if task.done():
                    response = await task
                    raise AssertionError(response.text)
                await asyncio.sleep(.1)
            assert pid_file.exists()
            child_handle = kernel.OpenProcess(0x00100000, False, int(pid_file.read_text()))
            assert child_handle
            if exit_mode == "graceful":
                response = await client.post("/__recorder/api/shutdown", headers={"Authorization": "Bearer isolated-test-owner"})
                assert response.status_code == 200, response.text
            elif exit_mode == "forced":
                backend.kill()
            else:
                owner.kill()
                await asyncio.to_thread(owner.wait, 5)
            await asyncio.to_thread(backend.wait, 10)
            assert kernel.WaitForSingleObject(child_handle, 1000) == 0
            ledger = ExecutionLedger(str(config_path))
            assert ledger.runs[ledger.key(str(tmp_path), session)][-1]['state'] in {"stopped", "interrupted"}
            assert all(run['state'] not in {'running', 'stopping'} for runs in ledger.runs.values() for run in runs)
    finally:
        if backend.poll() is None:
            backend.kill()
            await asyncio.to_thread(backend.wait, 5)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if child_handle:
            kernel.CloseHandle(child_handle)
        if owner is not None and owner.poll() is None:
            owner.kill()
            await asyncio.to_thread(owner.wait, 5)
        log.close()
