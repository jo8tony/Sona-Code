"""OpenCode startup output, precise failure classification, and secret handling."""

import asyncio
import base64
import errno
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import httpx
import pytest

from sona_code.config import UpstreamModelConfig, default_config
from sona_code.workspace import manager
from sona_code.workspace.diagnostics import OUTPUT_LIMIT, ProcessOutput, StartupRedactor, is_locale_error


@pytest.mark.parametrize("text", [
    r"EPERM: operation not permitted, open 'B:\~BUN\locales\en_US.json'",
    'EUNKNOWN: unknown error, open "b:/~bun/locales/zh_CN.json"',
])
def test_locale_error_matches_known_windows_paths(text):
    assert is_locale_error(text)


@pytest.mark.parametrize("text", [
    r"ENOENT: no such file, open 'B:\~BUN\locales\en_US.json'",
    r"EPERM: operation not permitted, open 'C:\project\config.json'",
    r"EPERM: operation not permitted, open 'B:\~BUN\root\index.js'",
    r"EPERM: operation not permitted, open 'B:\~BUN\locales\en_US.json.bak'",
    "EPERM: open C:/private.json\nstack B:/~BUN/locales/en_US.json",
])
def test_unrelated_permissions_are_not_locale_errors(text):
    assert not is_locale_error(text)


def test_output_drains_more_than_pipe_capacity_and_keeps_bounded_tail():
    proc = subprocess.Popen([sys.executable, "-c",
        "import sys; sys.stdout.buffer.write(b'x'*180000+b'\\nlast line\\n'); sys.stdout.flush()"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    output = ProcessOutput(proc.stdout)
    try:
        assert proc.wait(timeout=5) == 0
        output.close()
        assert len(output._buffer) <= OUTPUT_LIMIT
        assert output.text() == "last line\n"
        assert not output._thread.is_alive()
        assert proc.stdout.closed
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        output.close()


def test_output_cleanup_does_not_wait_for_inherited_pipe():
    read_fd, write_fd = os.pipe()
    output = ProcessOutput(os.fdopen(read_fd, "rb", buffering=0))
    try:
        output.close()
        assert not output._thread.is_alive()
    finally:
        os.close(write_fd)


def test_output_cleanup_is_bounded_even_if_descendant_continues_writing():
    read_fd, write_fd = os.pipe()

    def write():
        try:
            while True:
                os.write(write_fd, b"child output\n" * 300)
        except OSError as exc:
            if exc.errno not in (errno.EPIPE, errno.EINVAL, errno.EBADF):
                raise
        finally:
            os.close(write_fd)

    writer = threading.Thread(target=write, daemon=True)
    output = ProcessOutput(os.fdopen(read_fd, "rb", buffering=0))
    writer.start()
    output.close()
    writer.join(timeout=2)
    assert not output._thread.is_alive()
    assert not writer.is_alive()


def test_redaction_covers_keys_headers_inline_credentials_and_basic_password():
    cfg = default_config()
    cfg.upstreams[0].api_key = "provider-sensitive-key"
    cfg.upstreams[0].models = [UpstreamModelConfig(id="model", api_key="model-sensitive-key")]
    cfg.upstreams[0].extra_headers = {"cookie": "session-sensitive-cookie"}
    env = {"OPENCODE_SERVER_USERNAME": "sona-code", "OPENCODE_SERVER_PASSWORD": "sensitive-password",
           "NATIVE_TOKEN": "native-sensitive-token",
           "OPENCODE_CONFIG_CONTENT": '{"provider":{"options":{"apiKey":"inline-sensitive-key"}}}'}
    basic = base64.b64encode(b"sona-code:sensitive-password").decode()
    source = ("provider-sensitive-key model-sensitive-key session-sensitive-cookie sensitive-password "
              f"native-sensitive-token inline-sensitive-key Basic {basic}\n"
              '"Authorization": "Bearer unknown-sensitive-key",\nX-API-Key: arbitrary-secret\nCookie=private-cookie')
    result = StartupRedactor(cfg, env).redact(source)
    for value in ("provider-sensitive-key", "model-sensitive-key", "session-sensitive-cookie", "sensitive-password",
                  "native-sensitive-token", "inline-sensitive-key", basic, "unknown-sensitive-key",
                  "arbitrary-secret", "private-cookie"):
        assert value not in result


@pytest.fixture
def startup(tmp_path, monkeypatch):
    workspace = manager.WorkspaceManager()
    outputs, processes, clients = [], [], []
    state = SimpleNamespace(text="", source="bundled", exit_code=1)

    class Output:
        def __init__(self, stream):
            self.error = None
            self.closed = False
            outputs.append(self)

        def close(self):
            self.closed = True

        def text(self):
            return state.text

    def spawn(*args, **kwargs):
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.STDOUT
        proc = SimpleNamespace(stdout=object(), poll=lambda: state.exit_code)
        processes.append(proc)
        return proc

    native_client = httpx.AsyncClient

    def client(**kwargs):
        instance = native_client(**kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(503)))
        clients.append(instance)
        return instance

    async def stop(proc):
        proc.stopped = True

    async def sleep(seconds):
        return

    monkeypatch.setattr(manager, "resolve_opencode", lambda cfg: SimpleNamespace(path="opencode", source=state.source))
    monkeypatch.setattr(manager, "resolve_executable", lambda path: path)
    monkeypatch.setattr(manager, "executable_version", lambda path: "1.18.32")
    monkeypatch.setattr(manager, "_build_env", lambda *args: {})
    monkeypatch.setattr(manager, "ProcessOutput", Output)
    monkeypatch.setattr(manager.subprocess, "Popen", spawn)
    monkeypatch.setattr(manager.httpx, "AsyncClient", client)
    monkeypatch.setattr(manager.asyncio, "sleep", sleep)
    monkeypatch.setattr(workspace, "_stop_process", stop)
    monkeypatch.setattr(manager.secrets, "token_urlsafe", lambda n: "service-sensitive-password")
    return SimpleNamespace(workspace=workspace, path=str(tmp_path), state=state, outputs=outputs,
                           processes=processes, clients=clients, native_client=native_client)


@pytest.mark.parametrize("source", ["bundled", "custom", "path"])
async def test_locale_failure_stops_retrying_and_suggests_correct_source(startup, source, caplog):
    startup.state.source = source
    startup.state.text = r"EPERM: operation not permitted, open 'B:\~BUN\locales\en_US.json'"
    with pytest.raises(manager.WorkspaceError) as error:
        await startup.workspace.ensure(startup.path, default_config())
    assert error.value.status == 503
    assert "语言资源加载失败" in error.value.detail
    assert ("升级 Sona Code" if source == "bundled" else "切换到应用随包修补版") in error.value.detail
    assert len(startup.processes) == 1
    assert startup.outputs[0].closed and startup.processes[0].stopped
    assert all(client.is_closed for client in startup.clients)
    assert not startup.workspace._starting
    assert "exit=1" in caplog.text and "version=1.18.32" in caplog.text


async def test_error_classification_survives_credential_redaction(startup):
    startup.state.text = r"EPERM: operation not permitted, open 'B:\~BUN\locales\en_US.json'"
    cfg = default_config()
    cfg.upstreams[0].api_key = "EPERM"
    with pytest.raises(manager.WorkspaceError, match="语言资源加载失败"):
        await startup.workspace.ensure(startup.path, cfg)
    assert len(startup.processes) == 1


async def test_version_probe_encoding_failure_keeps_startup_diagnostics(startup, monkeypatch):
    def version(path):
        raise UnicodeDecodeError("cp936", b"\xff", 0, 1, "invalid diagnostic")

    monkeypatch.setattr(manager, "executable_version", version)
    startup.state.text = "original startup error"
    with pytest.raises(manager.WorkspaceError) as error:
        await startup.workspace.ensure(startup.path, default_config())
    assert error.value.status == 503
    assert "original startup error" in error.value.detail
    assert len(startup.processes) == 3


@pytest.mark.parametrize("exit_code", [1, None])
async def test_other_failures_keep_three_attempts_and_include_safe_details(startup, exit_code, caplog):
    startup.state.exit_code = exit_code
    startup.state.text = "ERROR provider-sensitive-key service-sensitive-password\nAuthorization: Bearer unknown-secret"
    cfg = default_config()
    cfg.upstreams[0].api_key = "provider-sensitive-key"
    with pytest.raises(manager.WorkspaceError) as error:
        await startup.workspace.ensure(startup.path, cfg)
    assert len(startup.processes) == 3
    assert all(proc.stopped for proc in startup.processes)
    assert all(output.closed for output in startup.outputs)
    assert all(client.is_closed for client in startup.clients)
    assert ("退出码 1" if exit_code == 1 else "启动超时") in error.value.detail
    for secret in ("provider-sensitive-key", "service-sensitive-password", "unknown-secret"):
        assert secret not in caplog.text + error.value.detail


async def test_cancelling_start_task_releases_output_and_process(startup, monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()
    startup.state.exit_code = None
    native_client = startup.native_client

    async def respond(request):
        entered.set()
        await release.wait()
        return httpx.Response(200)

    def client(**kwargs):
        instance = native_client(**kwargs, transport=httpx.MockTransport(respond))
        startup.clients.append(instance)
        return instance

    monkeypatch.setattr(manager.httpx, "AsyncClient", client)
    caller = asyncio.create_task(startup.workspace.ensure(startup.path, default_config()))
    await asyncio.wait_for(entered.wait(), 2)
    startup.workspace._starting[startup.path].cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller
    assert startup.outputs[0].closed and startup.processes[0].stopped
    assert all(client.is_closed for client in startup.clients)
    assert not startup.workspace._starting
