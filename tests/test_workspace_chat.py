"""Projectless conversations keep native persistence and deny project actions."""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from sona_code.admin.models import compile_providers, native_provider_id
from sona_code.app import create_app
from sona_code.config import AppConfig, UpstreamConfig, UpstreamModelConfig
from sona_code.workspace.chat import CHAT_AGENT, chat_environment
from sona_code.workspace.history import read_history


def test_chat_environment_preserves_providers_and_denies_tools():
    inline = {"provider": {"local": {"options": {"baseURL": "http://localhost"}}},
              "permission": {"*": "allow"}, "agent": {"build": {"mode": "primary"}}}
    original = {"OPENCODE_CONFIG_CONTENT": json.dumps(inline), "EXAMPLE": "unchanged"}
    env = chat_environment(original)
    assert json.loads(original["OPENCODE_CONFIG_CONTENT"]) == inline
    compiled = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert compiled["provider"] == inline["provider"]
    assert compiled["default_agent"] == CHAT_AGENT
    assert compiled["permission"] == {"*": "deny"}
    assert compiled["agent"][CHAT_AGENT]["permission"] == {"*": "deny"}
    assert env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert env["EXAMPLE"] == "unchanged"


def test_chat_routes_keep_projects_and_foreign_sessions_separate(tmp_path):
    config = AppConfig(upstreams=[UpstreamConfig(name="local", base_url="http://127.0.0.1:1")],
                       default_upstream="local", model_settings={"source": "native"})
    config.recording.dir = str(tmp_path / "records")
    app = create_app(config, str(tmp_path / "config.json"))
    runtime = app.state.runtime
    chat_path = runtime.workspace.chat_directory
    project = tmp_path / "project"
    project.mkdir()
    runtime.terminal_projects.add(str(project), "opencode")
    calls = []

    async def catalog(paths, cfg):
        return {path: [{"id": "ses_chat", "title": "聊天"}] if path == chat_path else []
                for path in paths}

    async def native(path, cfg, method, endpoint, *, body=None, params=None):
        calls.append((path, method, endpoint, body))
        if endpoint == "/session" and method == "GET":
            return [{"id": "ses_chat", "directory": chat_path},
                    {"id": "ses_foreign", "directory": str(project)}]
        if endpoint == "/session/ses_foreign":
            return {"id": "ses_foreign", "directory": str(project)}
        if endpoint == "/session/ses_chat":
            return {"id": "ses_chat", "directory": chat_path}
        if endpoint == "/session" and method == "POST":
            return {"id": "ses_chat", "directory": chat_path}
        if endpoint.endswith("/message"):
            return []
        return {"ok": True}

    runtime.workspace.session_catalog = catalog
    runtime.workspace.request = native
    root = "/__recorder/api/workspace"
    base = root + "/projects/chat"
    with TestClient(app) as client:
        scope = client.get(root + "/chats").json()
        assert scope["path"] == "" and scope["session_count"] == 1
        assert client.get(root + "/projects").json()["total"] == 1
        assert not Path(chat_path).exists(), "Listing conversations must not start a server"
        assert not calls
        assert client.post(base + "/sessions", json={}).status_code == 201
        assert calls[-1][3]["permission"] == [{"permission": "*", "pattern": "*", "action": "deny"}]
        assert client.get(base + "/sessions").json()["items"] == [{"id": "ses_chat", "directory": chat_path}]
        assert client.get(base + "/agents").json()[0]["name"] == CHAT_AGENT
        assert client.get(base + "/skills").json()["items"] == []
        for endpoint in ("files", "openspec"):
            assert client.get(base + "/" + endpoint).status_code == 403
        assert client.post(base + "/open").status_code == 403
        assert client.delete(base).status_code == 403
        assert client.get(base + "/sessions/ses_foreign/messages").status_code == 404
        assert client.get(base + "/sessions/ses_foreign/trajectory").status_code == 404
        assert client.post(base + "/sessions/ses_foreign/abort").status_code == 404
        assert client.post(base + "/sessions/ses_foreign/send", json={
            "kind": "prompt", "payload": {"text": "hello"},
        }).status_code == 404
        assert client.post(base + "/sessions/ses_chat/prompt", json={"text": "你好"}).status_code == 200
        assert calls[-1][3]["agent"] == CHAT_AGENT
        for payload in ({"text": "你好", "agent": "build"},
                        {"text": "读取文件", "references": [{"path": "README.md"}]}):
            assert client.post(base + "/sessions/ses_chat/prompt", json=payload).status_code == 403
        assert client.post(base + "/sessions/ses_chat/shell", json={"command": "echo hello"}).status_code == 403
        assert client.post(base + "/sessions/ses_chat/command", json={"command": "init"}).status_code == 403
        assert client.post(base + "/sessions/ses_chat/send", json={"kind": "command", "payload": {"command": "init"}}).status_code == 403


def test_empty_chat_snapshot_is_confirmed_with_live_native_history(tmp_path):
    app = create_app(AppConfig(default_upstream="", recording={"dir": str(tmp_path / "records")}),
                     str(tmp_path / "config.json"))
    runtime = app.state.runtime
    calls = []

    async def native(path, cfg, method, endpoint, *, body=None, params=None):
        calls.append((method, endpoint, params))
        assert method == "GET" and endpoint == "/session"
        return [{"id": "existing", "directory": path}] if params else []

    runtime.workspace.request = native
    with TestClient(app) as client:
        response = client.get("/__recorder/api/workspace/projects/chat/sessions")
        assert response.status_code == 200
        assert response.json()["items"][0]["id"] == "existing"
        assert calls == [("GET", "/session", None),
                         ("GET", "/session", {"directory": runtime.workspace.chat_directory})]


def test_scope_switch_restores_selection_search_and_position():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required")
    subprocess.run([node, "-e", r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const state = {sidebarMode: "project", projectId: "p", sessionId: "s", search: "project query", tab: "activity",
  projects: [{id: "p"}, {id: "chat"}], sessions: new Map([["p", [{id: "s"}]], ["chat", [{id: "c"}]]])};
const list = {scrollTop: 180}, input = {};
const context = vm.createContext({state, sidebarViews: new Map(), workspaceSelection: {},
  view: {querySelector: selector => selector === "#wsp-search" ? input : list},
  persistWorkspaceSelection() {}, renderHeader() {}, renderMain() {}});
vm.runInContext(source.slice(0, source.indexOf("function workspaceProjectMark(")), context);
vm.runInContext(source.slice(source.indexOf("  function syncSidebarScope("), source.indexOf("  let locatingSession =")), context);
context.selectProject = id => {context.syncSidebarScope(id); state.projectId = id; state.sessionId = null;};
context.selectSession = (id, session) => {context.syncSidebarScope(id); state.projectId = id; state.sessionId = session;};
context.switchSidebarScope("chat"); assert.equal(state.sidebarMode, "chat"); assert.equal(state.search, "");
state.sessionId = "c"; state.search = "chat query"; state.tab = "chat"; list.scrollTop = 42;
context.switchSidebarScope("project"); assert.equal(state.projectId, "p"); assert.equal(state.sessionId, "s");
assert.equal(state.search, "project query"); assert.equal(input.value, "project query");
assert.equal(list.scrollTop, 180); assert.equal(state.tab, "activity");
context.switchSidebarScope("chat"); assert.equal(state.sessionId, "c"); assert.equal(state.search, "chat query");
assert.equal(list.scrollTop, 42);
state.sessionId = null;
context.switchSidebarScope("project"); context.switchSidebarScope("chat");
assert.equal(state.sessionId, null, "A new-conversation draft must not select an older conversation");
assert.equal(context.workspaceAccountProfile({source: "sona", connected: true, user: {userName: "张三"}}).name, "张三");
assert.equal(context.workspaceAccountProfile({source: "custom"}).name, "我的工作区");
'''], cwd=Path(__file__).resolve().parents[1], check=True)


BINARIES = [p for p in os.environ.get("OPENCODE_TEST_BINARIES", "").split(os.pathsep) if p]


@pytest.mark.parametrize("binary", BINARIES or [None])
def test_native_chat_tools_history_and_ancestor_isolation(binary, tmp_path, monkeypatch):
    if binary is None:
        pytest.skip("set OPENCODE_TEST_BINARIES for native V1 checks")
    project = tmp_path / "chat-workspace"
    project.mkdir()
    (tmp_path / "AGENTS.md").write_text("ANCESTOR_INSTRUCTION_MUST_NOT_APPEAR", encoding="utf-8")
    captured = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append(body)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in [({"role": "assistant", "content": "你好，独立对话已保存。"}, None), ({}, "stop")]:
                chunk = {"id": "chat_mock", "object": "chat.completion.chunk", "created": 1,
                         "model": body["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    cfg = AppConfig(upstreams=[UpstreamConfig(name="test", base_url=f"http://127.0.0.1:{upstream.server_port}/v1",
        route_through_proxy=False, api_key="local-test-key", models=[UpstreamModelConfig(id="chat-one", context_length=32000, output_length=2000)])], default_upstream="test")
    provider = native_provider_id("test")
    inline = {"provider": compile_providers(cfg), "enabled_providers": [provider],
              "model": provider + "/chat-one", "small_model": provider + "/chat-one"}
    env = chat_environment({**os.environ, "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1",
           "OPENCODE_CONFIG_CONTENT": json.dumps(inline), "OPENCODE_SERVER_PASSWORD": "local-test-password"})
    for name in ("CONFIG", "CACHE", "DATA", "STATE"):
        env[f"XDG_{name}_HOME"] = str(tmp_path / name.lower())
    env.pop("OPENCODE_CONFIG", None)
    env.pop("OPENCODE_DB", None)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (tmp_path / "native.log").open("w") as log:
        process = subprocess.Popen([binary, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
            env=env, cwd=project, stdout=log, stderr=log)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", auth=("opencode", "local-test-password"), timeout=20, trust_env=False) as client:
                for _ in range(100):
                    assert process.poll() is None, (tmp_path / "native.log").read_text()[-1000:]
                    try:
                        if client.get("/global/health").status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(.1)
                response = client.post("/session", json={"title": "普通对话", "permission": [{"permission": "*", "pattern": "*", "action": "deny"}]})
                assert response.status_code == 200, response.text
                session = response.json()["id"]
                response = client.post(f"/session/{session}/prompt_async", json={"agent": CHAT_AGENT,
                    "model": {"providerID": provider, "modelID": "chat-one"}, "parts": [{"type": "text", "text": "你好"}]})
                assert response.status_code < 300, response.text
                replies = []
                for _ in range(200):
                    history = client.get(f"/session/{session}/message").json()
                    replies = [m for m in history if m["info"]["role"] == "assistant"]
                    if any(m["info"].get("error") or m["info"].get("finish") == "stop" for m in replies):
                        break
                    time.sleep(.1)
                assert replies and not any(m["info"].get("error") for m in replies), replies
                assert captured and all(not body.get("tools") for body in captured), captured
                assert "ANCESTOR_INSTRUCTION_MUST_NOT_APPEAR" not in json.dumps(captured)
                monkeypatch.setenv("XDG_DATA_HOME", env["XDG_DATA_HOME"])
                monkeypatch.delenv("OPENCODE_DB", raising=False)
                snapshot = read_history(str(project), cfg, "/session")
                assert snapshot is not None and any(item["id"] == session for item in snapshot)
        finally:
            process.terminate()
            process.wait(timeout=10)
            upstream.shutdown()
            upstream.server_close()
