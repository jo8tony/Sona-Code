"""Web 终端模块测试：manager PTY 生命周期 / REST 会话 CRUD / 目录浏览 / WebSocket。"""

import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.config import AppConfig, TerminalConfig, UpstreamConfig, UpstreamModelConfig

pytestmark = pytest.mark.skipif(
    sys.platform not in ("win32", "darwin") or os.environ.get("SONACODE_SKIP_TERMINAL_TESTS"),
    reason="终端模块仅支持 Windows ConPTY 或 macOS PTY",
)


def make_cfg(**terminal_overrides) -> AppConfig:
    return AppConfig(
        upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
        default_upstream="main",
        terminal=TerminalConfig(**terminal_overrides),
    )


def make_client(cfg: AppConfig, config_path: str = "term-test-config.json") -> TestClient:
    app = create_app(cfg, config_path=config_path)
    return TestClient(app)


# ------------------------------------------------------------------ 配置
class TestTerminalConfig:
    def test_defaults(self):
        t = TerminalConfig()
        assert t.enabled is True
        assert t.command_mode == "auto"
        assert t.command == "opencode"
        assert t.max_sessions == 8
        assert t.route_through_proxy is True

    def test_app_config_has_terminal_section(self):
        assert make_cfg().terminal.scrollback_kb == 256


# ------------------------------------------------------------------ 命令解析
class TestResolve:
    def test_resolve_cmd_shim_wrapping(self):
        from sona_code.terminal.manager import _build_argv

        argv = _build_argv(r"C:\npm\opencode.CMD", ["--x"])
        assert argv[0] == "cmd.exe"
        assert argv[1] == "/c"
        assert argv[2] == r"C:\npm\opencode.CMD"
        assert argv[3] == "--x"

    def test_resolve_exe_direct(self):
        from sona_code.terminal.manager import _build_argv

        argv = _build_argv(r"C:\bin\opencode.exe", [])
        assert argv == [r"C:\bin\opencode.exe"]

    def test_resolve_executable(self):
        from sona_code.terminal.manager import resolve_executable

        assert resolve_executable("") is None
        assert resolve_executable("definitely-not-exist-xyz") is None
        # 完整路径探测
        assert resolve_executable(os.path.join(os.sep, "definitely", "not", "exist")) is None

    def test_build_env_proxy_injection(self, monkeypatch):
        from sona_code.terminal.manager import _build_env
        for key in ("OPENAI_BASE_URL", "ANTHROPIC_BASE_URL", "OPENCODE_CONFIG_CONTENT"):
            monkeypatch.delenv(key, raising=False)

        from sona_code.admin.models import native_provider_id
        cfg = make_cfg()
        cfg.upstreams[0].models = [UpstreamModelConfig(id="one")]
        env = _build_env(cfg, "opencode")
        assert env["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
        assert "OPENAI_BASE_URL" not in env
        provider = json.loads(env["OPENCODE_CONFIG_CONTENT"])["provider"][native_provider_id("main")]
        assert provider["models"]["one"]["provider"]["api"].startswith("http://127.0.0.1:8117/managed/")
        assert provider["options"]["apiKey"] == ""
        cfg.upstreams[0].route_through_proxy = False
        direct = json.loads(_build_env(cfg, "opencode")["OPENCODE_CONFIG_CONTENT"])["provider"][native_provider_id("main")]
        assert direct["models"]["one"]["provider"]["api"] == "http://127.0.0.1:9001"

        # shell 会话不注入代理变量
        env5 = _build_env(make_cfg(), "shell")
        assert "OPENAI_BASE_URL" not in env5
        assert "OPENCODE_DISABLE_AUTOUPDATE" not in env5

        # inject_env 优先级最高
        env6 = _build_env(make_cfg(inject_env={"OPENAI_BASE_URL": "http://override"}), "opencode")
        assert env6["OPENAI_BASE_URL"] == "http://override"
        env7 = _build_env(
            make_cfg(inject_env={"OPENCODE_DISABLE_AUTOUPDATE": "0"}), "opencode"
        )
        assert env7["OPENCODE_DISABLE_AUTOUPDATE"] == "1"

    def test_shell_restores_original_xdg_environment(self, monkeypatch):
        from sona_code.terminal.manager import _build_env

        monkeypatch.setenv("XDG_CONFIG_HOME", "/app/config")
        monkeypatch.setenv("XDG_DATA_HOME", "/app/data")
        monkeypatch.setenv("SONACODE_ORIGINAL_XDG_CONFIG_HOME", "/user/config")
        monkeypatch.setenv("SONACODE_ORIGINAL_XDG_DATA_HOME", "")
        shell_env = _build_env(make_cfg(), "shell")
        assert shell_env["XDG_CONFIG_HOME"] == "/user/config"
        assert "XDG_DATA_HOME" not in shell_env
        assert "SONACODE_ORIGINAL_XDG_CONFIG_HOME" not in shell_env

    def test_opencode_resolution_precedence(self, tmp_path, monkeypatch):
        import sona_code.terminal.manager as manager

        bundled = tmp_path / "opencode.exe"
        bundled.write_bytes(b"binary")
        monkeypatch.setattr(manager.sys, "platform", "win32")
        monkeypatch.setenv(manager.BUNDLED_OPENCODE_ENV, str(bundled))
        resolved = manager.resolve_opencode(make_cfg())
        assert resolved.path == str(bundled)
        assert resolved.source == "bundled"

        custom_exe = tmp_path / "custom-opencode.exe"
        custom_exe.write_bytes(b"custom")
        custom = make_cfg(command_mode="custom", command=str(custom_exe))
        resolved = manager.resolve_opencode(custom)
        assert resolved.path == str(custom_exe)
        assert resolved.source == "custom"

        custom = make_cfg(command_mode="custom", command=str(tmp_path / "missing-opencode.exe"))
        resolved = manager.resolve_opencode(custom)
        assert resolved.path is None
        assert resolved.source == "missing"

    def test_macos_gui_finds_opencode_from_login_shell_path(self, tmp_path, monkeypatch):
        import sona_code.terminal.manager as manager

        executable = tmp_path / "opencode"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        monkeypatch.setattr(manager.sys, "platform", "darwin")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setattr(manager, "_macos_login_path", lambda: str(tmp_path))

        resolved = manager.resolve_opencode(make_cfg())
        assert resolved.path == str(executable)
        assert str(tmp_path) in manager._build_env(make_cfg(), "opencode")["PATH"].split(os.pathsep)

    def test_version_probe_timeout_is_non_fatal(self, monkeypatch):
        import subprocess
        import sona_code.terminal.manager as manager

        manager._version_cache.clear()

        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("opencode", 0.01)

        monkeypatch.setattr(manager.subprocess, "run", timeout)
        assert manager.executable_version("opencode", timeout=0.01) is None

    def test_version_probe_is_cached_and_hidden_on_windows(self, monkeypatch):
        import sona_code.terminal.manager as manager

        manager._version_cache.clear()
        calls = []

        class Result:
            returncode = 0
            stdout = "1.18.32\n"
            stderr = ""

        def run(*args, **kwargs):
            calls.append((args, kwargs))
            return Result()

        monkeypatch.setattr(manager.sys, "platform", "win32")
        monkeypatch.setattr(manager.subprocess, "run", run)
        assert manager.executable_version(r"C:\Program Files\OpenCode\opencode.exe") == "1.18.32"
        assert manager.executable_version(r"C:\Program Files\OpenCode\opencode.exe") == "1.18.32"
        assert len(calls) == 1
        assert calls[0][1]["creationflags"] == 0x08000000

    def test_manual_models_and_image_support_in_offline_opencode(self, monkeypatch):
        from sona_code.terminal.manager import _build_env

        monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
        monkeypatch.delenv("OPENCODE_DISABLE_MODELS_FETCH", raising=False)
        cfg = AppConfig(
            upstreams=[UpstreamConfig(name="private", base_url="http://127.0.0.1:9001", models=[
                UpstreamModelConfig(id="text-only"),
                UpstreamModelConfig(id="vision", input_modalities=["image", "pdf"]),
            ])],
            default_upstream="private",
        )
        env = _build_env(cfg, "opencode")
        from sona_code.admin.models import native_provider_id
        provider = json.loads(env["OPENCODE_CONFIG_CONTENT"])["provider"][native_provider_id("private")]
        assert env["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
        assert provider["npm"] == "@ai-sdk/openai-compatible"
        assert provider["models"]["text-only"]["attachment"] is False
        assert provider["models"]["vision"]["attachment"] is True
        assert provider["models"]["vision"]["modalities"] == {
            "input": ["text", "image", "pdf"], "output": ["text"],
        }
        assert "OPENCODE_DISABLE_MODELS_FETCH" not in _build_env(cfg, "shell")
        cfg.upstreams[0].route_through_proxy = False
        direct = json.loads(_build_env(cfg, "opencode")["OPENCODE_CONFIG_CONTENT"])["provider"][native_provider_id("private")]
        assert direct["models"]["vision"]["provider"]["api"] == "http://127.0.0.1:9001"


# ------------------------------------------------------------------ REST + WS
@pytest.fixture
def client(tmp_path):
    with make_client(make_cfg(), str(tmp_path / "config.json")) as c:
        c._tmp = str(tmp_path)
        yield c


class TestSessionsApi:
    def test_create_list_delete_shell_session(self, client):
        r = client.post(
            "/__recorder/api/terminal/sessions",
            json={"cwd": client._tmp, "kind": "shell"},
        )
        assert r.status_code == 201, r.text
        info = r.json()
        assert info["kind"] == "shell"
        assert info["alive"] is True

        lst = client.get("/__recorder/api/terminal/sessions").json()
        assert lst["total"] == 1

        assert client.delete(f"/__recorder/api/terminal/sessions/{info['id']}").status_code == 200
        assert client.get("/__recorder/api/terminal/sessions").json()["total"] == 0
        assert client.delete(f"/__recorder/api/terminal/sessions/{info['id']}").status_code == 404

    def test_create_invalid_cwd(self, client):
        r = client.post(
            "/__recorder/api/terminal/sessions",
            json={"cwd": str(client._tmp) + os.sep + "definitely-not-exist", "kind": "shell"},
        )
        assert r.status_code == 400
        assert "不存在" in r.json()["detail"]

    def test_create_relative_cwd_rejected(self, client):
        r = client.post(
            "/__recorder/api/terminal/sessions",
            json={"cwd": "relative/path", "kind": "shell"},
        )
        assert r.status_code == 400

    def test_create_unknown_kind_rejected(self, client):
        r = client.post(
            "/__recorder/api/terminal/sessions",
            json={"cwd": client._tmp, "kind": "vim"},
        )
        assert r.status_code == 422  # Literal 校验

    def test_disabled_terminal(self, tmp_path):
        with make_client(make_cfg(enabled=False), str(tmp_path / "config.json")) as c:
            r = c.post(
                "/__recorder/api/terminal/sessions",
                json={"cwd": str(tmp_path), "kind": "shell"},
            )
            assert r.status_code == 400

    def test_max_sessions_limit(self, tmp_path):
        from sona_code.terminal.manager import detect_shell
        with make_client(make_cfg(max_sessions=2, shell_command=detect_shell()), str(tmp_path / "config.json")) as c:
            for _ in range(2):
                r = c.post(
                    "/__recorder/api/terminal/sessions",
                    json={"cwd": str(tmp_path), "kind": "shell"},
                )
                assert r.status_code == 201
            r = c.post(
                "/__recorder/api/terminal/sessions",
                json={"cwd": str(tmp_path), "kind": "shell"},
            )
            assert r.status_code == 400
            assert "上限" in r.json()["detail"]


class TestWebSocket:
    def test_attach_input_output_exit(self, client):
        r = client.post(
            "/__recorder/api/terminal/sessions",
            json={"cwd": client._tmp, "kind": "shell", "cols": 100, "rows": 30},
        )
        sid = r.json()["id"]
        with client.websocket_connect(f"/__recorder/api/terminal/ws/{sid}") as ws:
            first = json.loads(ws.receive_text())
            assert first["type"] == "attached"
            assert first["alive"] is True

            ws.send_text(json.dumps({"type": "input", "data": "echo hello_ws\r"}))
            buf = b""
            deadline = time.time() + 20
            while time.time() < deadline and b"hello_ws" not in buf:
                buf += ws.receive_bytes()
            assert b"hello_ws" in buf  # 回显可见

        client.delete(f"/__recorder/api/terminal/sessions/{sid}")

    def test_unknown_session_close_code(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/__recorder/api/terminal/ws/nope123"):
                pass

    async def test_attach_replay_then_live_without_duplicate(self):
        from sona_code.terminal.manager import TerminalManager, TerminalSession

        class Socket:
            def __init__(self):
                self.messages = []

            async def send_text(self, text):
                self.messages.append(("text", json.loads(text)["type"]))

            async def send_bytes(self, data):
                self.messages.append(("bytes", data))

        session = TerminalSession("id", "shell", "/tmp", "tmp", "now", object(), 1024)
        session.append_buffer(b"previous")
        ws = Socket()
        manager = TerminalManager()
        await manager.attach(ws, session)
        await manager._broadcast(session, b"next")
        assert ws.messages == [
            ("text", "attached"), ("bytes", b"previous"), ("bytes", b"next")
        ]

    async def test_input_write_failure_is_reported_without_raising(self):
        from sona_code.terminal.manager import TerminalManager, TerminalSession

        class BrokenProcess:
            def write(self, _text):
                raise OSError("pty input pipe closed")

        session = TerminalSession(
            "id", "opencode", "/tmp", "tmp", "now", BrokenProcess(), 1024
        )
        error = await TerminalManager().write(session, "hello")
        assert error == "终端输入写入失败，请重新启动会话"


class TestFsApi:
    def test_roots(self, client):
        r = client.get("/__recorder/api/terminal/fs").json()
        assert r["entries"]
        assert all(e["path"] for e in r["entries"])

    def test_list_subdirs(self, client, tmp_path):
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        (tmp_path / "file.txt").write_text("x", encoding="utf-8")

        r = client.get("/__recorder/api/terminal/fs", params={"path": str(tmp_path)}).json()
        names = [e["name"] for e in r["entries"]]
        assert names == ["alpha", "beta"]  # 仅目录，排序，无文件
        assert r["parent"] is not None

    def test_invalid_path(self, client):
        r = client.get("/__recorder/api/terminal/fs", params={"path": r"D:\no\such\dir"})
        assert r.status_code == 400


class TestCheckApi:
    def test_check(self, client):
        r = client.get("/__recorder/api/terminal/check").json()
        assert "opencode_found" in r
        assert r["opencode_source"] in {"bundled", "path", "custom", "missing"}
        assert "opencode_version" in r
        assert "bundled_version" in r
        assert "git_bash_path" in r
        assert "shell_command" in r
        assert r["enabled"] is True
        assert r["pty_available"] is True


class TestProjectsApi:
    def test_project_survives_session_and_server_restart(self, tmp_path):
        config_path = str(tmp_path / "config.json")
        with make_client(make_cfg(), config_path) as c:
            result = c.post("/__recorder/api/terminal/sessions", json={"cwd": str(tmp_path), "kind": "shell"})
            assert result.status_code == 201, result.text
            assert result.json()["project_saved"] is True
            sid = result.json()["id"]
            assert c.delete(f"/__recorder/api/terminal/sessions/{sid}").status_code == 200
        with make_client(make_cfg(), config_path) as c:
            projects = c.get("/__recorder/api/terminal/projects").json()["items"]
            assert [p["path"] for p in projects] == [str(tmp_path)]
            assert projects[0]["kind"] == "shell"
            assert c.request("DELETE", "/__recorder/api/terminal/projects", json={"path": str(tmp_path)}).status_code == 200
            assert c.get("/__recorder/api/terminal/projects").json()["total"] == 0
