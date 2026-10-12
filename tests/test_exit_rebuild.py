"""Exit races and failures must retain ownership and permit cleanup retries."""

import asyncio
import base64
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest

from sona_code.app import create_app
from sona_code.config import default_config
from sona_code.terminal.manager import TerminalError, TerminalManager
from sona_code.workspace.manager import OpenCodeServer, WorkspaceError, WorkspaceManager
from tests.test_workspace_completion import run_node


def test_desktop_shutdown_waits_checks_force_and_enforces_http_deadline():
    run_node(r'''
const assert = require("node:assert/strict"), http = require("node:http");
const {shutdownOwnedChild, requestShutdown} = require("./electron/shutdown.cjs");
(async () => {
  const child = {pid: 123, exitCode: null, signalCode: null};
  let requests = 0, forces = 0;
  await shutdownOwnedChild(child, "http://local", "token", {
    waitMs: 100, request: async () => {requests++; setTimeout(() => child.exitCode = 0, 20);},
    force: async () => {forces++;},
  });
  assert.equal(requests, 1); assert.equal(forces, 0);
  await shutdownOwnedChild(null, "http://local", "token", {request: () => {throw Error("not owned");}});
  child.exitCode = null;
  await shutdownOwnedChild(child, "http://local", "token", {
    waitMs: 0, request: async () => false,
    force: async pid => {assert.equal(pid, 123); forces++; child.signalCode = "SIGTERM";},
  });
  assert.equal(forces, 1);
  child.signalCode = null;
  await assert.rejects(shutdownOwnedChild(child, "http://local", "token", {
    waitMs: 0, request: async () => false, force: async () => {},
  }), /尚未退出/);
  assert.equal(child.pid, 123);
  const server = http.createServer((req, res) => {
    assert.equal(req.headers.authorization, "Bearer token");
    res.writeHead(200);
    const ticker = setInterval(() => res.write("heartbeat"), 10);
    res.on("close", () => clearInterval(ticker));
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const start = Date.now();
    assert.equal(await requestShutdown(`http://127.0.0.1:${server.address().port}`, "token", 100), false);
    assert(Date.now() - start < 1000);
  } finally {server.closeAllConnections(); await new Promise(resolve => server.close(resolve));}
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


async def test_runtime_shared_cleanup_failure_is_retried_without_reopening(tmp_path, monkeypatch):
    app = create_app(default_config(), str(tmp_path / "config.json"))
    runtime = app.state.runtime
    attempts = 0
    entered, release = asyncio.Event(), asyncio.Event()

    async def cleanup():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            entered.set()
            await release.wait()
            raise OSError("temporarily blocked")

    original = runtime.workspace.shutdown
    monkeypatch.setattr(runtime.workspace, "shutdown", cleanup)
    first = asyncio.create_task(runtime.aclose())
    await entered.wait()
    second = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(result, RuntimeError) for result in results)
    assert attempts == 1 and runtime.closing
    await runtime.aclose()
    assert attempts == 2 and runtime.closing
    monkeypatch.setattr(runtime.workspace, "shutdown", original)


async def test_dead_project_stop_does_not_launch_native_server(tmp_path, monkeypatch):
    manager = WorkspaceManager()

    async def forbidden(*args):
        pytest.fail("Stopping an inactive session must not start a project server")

    monkeypatch.setattr(manager, "ensure", forbidden)
    assert await manager.stop_session(str(tmp_path), default_config(), "old-session") == {"ok": True}
    assert not manager._servers and not manager._stopping


@pytest.mark.parametrize("background_writer", [False, True])
async def test_command_output_finishes_after_shell_exit_without_losing_buffered_bytes(tmp_path, background_writer):
    app = create_app(default_config(), str(tmp_path / "config.json"))
    runtime = app.state.runtime
    project = str(tmp_path.resolve())
    runtime.commands.owners["test-owner"] = (project, project)
    script = "import sys; sys.stdout.buffer.write(b'x'*180000); sys.stdout.flush()"
    if background_writer:
        writer = "import sys,time\nwhile True:\n sys.stdout.write('child-output' * 1000); sys.stdout.flush(); time.sleep(.002)"
        script = "import subprocess,sys; subprocess.Popen([sys.executable,'-u','-c'," + repr(writer) + "]); print('parent-ended', flush=True)"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=("127.0.0.1", 1)), base_url="http://local") as client:
            response = await asyncio.wait_for(client.post("/__sona_commands/run", headers={"Authorization": "Bearer test-owner"},
                json={"session_id": "session", "shell": sys.executable, "args": ["-c", script],
                      "command": "isolated-test", "cwd": project, "env": dict(os.environ)}), 5)
        assert response.status_code == 200
        frames = [json.loads(line) for line in response.text.splitlines()]
        assert frames[-1] == {"exit": 0}
        output = b"".join(base64.b64decode(frame["data"]) for frame in frames if "data" in frame)
        if background_writer:
            assert b"parent-ended" in output
        else:
            assert output == b"x" * 180000
        assert (project, "session") in runtime.commands.scopes
    finally:
        await runtime.aclose()


async def test_shutdown_keeps_internal_finalizers_but_rejects_new_work(tmp_path):
    app = create_app(default_config(), str(tmp_path / "config.json"))
    runtime = app.state.runtime
    project = str(tmp_path.resolve())
    runtime.commands.owners["test-owner"] = (project, project)
    runtime.ledger.begin(project, "session")
    runtime.commands.blocked.add((project, "session"))
    runtime.closing = runtime.commands.closing = True
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app, client=("127.0.0.1", 1)), base_url="http://local") as client:
            response = await client.post("/__sona_commands/event", headers={"Authorization": "Bearer test-owner"},
                                         json={"session_id": "session", "status": "idle"})
            assert response.status_code == 200
            assert (project, "session") in runtime.commands.blocked
            assert (await client.post("/__sona_commands/run", json={})).status_code == 503
            assert (await client.post("/__recorder/api/terminal/sessions", json={})).status_code == 503
            assert (await client.post("/__sona_commands/event", json={})).status_code == 403
    finally:
        await runtime.aclose()


async def test_workspace_shutdown_retains_only_failed_servers_and_retries(tmp_path, monkeypatch):
    manager = WorkspaceManager()
    servers = [SimpleNamespace(project=str(tmp_path / name)) for name in ("one", "two")]
    manager._servers = {server.project: server for server in servers}
    seen = []

    async def close(server):
        seen.append(server.project)
        if server is servers[0] and seen.count(server.project) == 1:
            raise OSError("blocked")

    monkeypatch.setattr(manager, "_close_server", close)
    with pytest.raises(OSError):
        await manager.shutdown()
    assert set(seen) == {server.project for server in servers}
    assert manager._servers == {servers[0].project: servers[0]}
    await manager.shutdown()
    assert not manager._servers


async def test_failed_stop_preserves_other_sessions_and_retry_gate(tmp_path, monkeypatch):
    manager = WorkspaceManager()
    path = str(tmp_path.resolve())
    client = httpx.AsyncClient(base_url="http://native", transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))
    server = OpenCodeServer(SimpleNamespace(poll=lambda: None), client, 1, project=path)
    manager._servers[path] = server
    attempts = 0

    async def abort(server, session):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise WorkspaceError("native timeout", 503)

    async def never_close(server):
        pytest.fail("Stopping one session must not close their shared server")

    monkeypatch.setattr(manager, "_abort_native", abort)
    monkeypatch.setattr(manager, "_close_server", never_close)
    try:
        with pytest.raises(WorkspaceError):
            await manager.stop_session(path, default_config(), "one")
        assert manager._servers[path] is server and not client.is_closed
        assert (path, "one") in manager._stopping and (path, "two") not in manager._stopping
        await manager.stop_session(path, default_config(), "one")
        assert not manager._stopping
    finally:
        await client.aclose()


async def test_terminal_shutdown_waits_for_inflight_creation(tmp_path, monkeypatch):
    import sona_code.terminal.manager as module
    # Exercise asynchronous launch ownership. Darwin deliberately runs forkpty
    # on the event-loop thread, so a blocking fake spawn cannot model this case.
    monkeypatch.setattr(module.sys, "platform", "linux")
    started, release = threading.Event(), threading.Event()
    closed = []

    def spawn(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return SimpleNamespace(close=lambda force: closed.append(force))

    monkeypatch.setattr(module, "PtyProcess", SimpleNamespace(spawn=spawn))
    cfg = default_config()
    cfg.terminal.shell_command = sys.executable
    manager = TerminalManager()
    creation = asyncio.create_task(manager.create(cfg, str(tmp_path), "shell", 24, 80))
    assert await asyncio.to_thread(started.wait, 2)
    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(.02)
    assert not shutdown.done()
    release.set()
    await shutdown
    with pytest.raises(TerminalError, match="退出"):
        await creation
    assert closed == [True] and not manager.sessions


@pytest.mark.skipif(os.name != "nt", reason="Windows ConPTY launch ownership")
async def test_terminal_startup_script_waits_until_job_is_assigned(tmp_path, monkeypatch):
    from sona_code.processes import WindowsJob, kernel
    from sona_code.workspace.commands import CommandManager
    from sona_code.workspace.lifecycle import ExecutionLedger
    import ctypes
    import time

    pid_file = tmp_path / "startup-child.pid"
    script = tmp_path / "startup.py"
    script.write_text("import os,pathlib,time\npathlib.Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()))\ntime.sleep(60)\n")
    launcher = tmp_path / "startup.cmd"
    launcher.write_text(subprocess.list2cmdline([sys.executable, str(script)]), encoding="utf-8")

    class SlowJob(WindowsJob):
        def assign(self, pid):
            time.sleep(.2)
            assert not pid_file.exists(), "Shell ran before ownership was established"
            super().assign(pid)

    monkeypatch.setattr("sona_code.processes.WindowsJob", SlowJob)
    manager = TerminalManager()
    manager.commands = CommandManager(ExecutionLedger(str(tmp_path / "config.json")), 1234)
    cfg = default_config()
    cfg.terminal.shell_command = str(launcher)
    handle = None
    try:
        session = await manager.create(cfg, str(tmp_path), "shell", 24, 80)
        for _ in range(60):
            if pid_file.exists():
                break
            await asyncio.sleep(.05)
        assert pid_file.exists()
        handle = kernel.OpenProcess(0x00100000, False, int(pid_file.read_text()))
        assert handle
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        assert await manager.kill(session.id)
        assert kernel.WaitForSingleObject(handle, 1000) == 0
    finally:
        await manager.shutdown()
        await manager.commands.shutdown()
        if handle:
            kernel.CloseHandle(handle)




async def test_terminal_exit_notifies_clients_and_retains_ownership_after_cleanup_failure(tmp_path):
    from sona_code.terminal.manager import TerminalSession

    calls = []

    class Process:
        exitstatus = 0

        def read(self, size):
            raise EOFError

        def close(self, force):
            calls.append(("process", force))

    class Commands:
        async def stop_owner(self, owner):
            calls.append(("commands", owner))
            if sum(call[0] == "commands" for call in calls) == 1:
                raise OSError("temporarily blocked")

    class Client:
        async def send_text(self, message):
            calls.append(("exit", json.loads(message)))

    session = TerminalSession("terminal", "shell", str(tmp_path), "shell", "now", Process(), 0)
    session.scope = SimpleNamespace(close=lambda: calls.append(("scope", True)))
    session.clients.add(Client())
    manager = TerminalManager()
    manager.commands = Commands()
    manager.sessions[session.id] = session
    await manager._pump(session)
    assert calls == [("commands", "terminal:terminal"), ("scope", True),
                     ("exit", {"type": "exit", "code": 0})]
    assert not session.alive and manager.sessions[session.id] is session
    assert await manager.kill(session.id)
    assert ("process", True) in calls and not manager.sessions
