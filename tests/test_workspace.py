"""Workspace project persistence and OpenCode API delegation."""

import base64

import pytest

from fastapi.testclient import TestClient

from llm_api_proxy_recorder.app import create_app
from llm_api_proxy_recorder.config import AppConfig, TerminalConfig, UpstreamConfig


def test_open_project_directory_uses_registered_path(tmp_path, monkeypatch):
    config = AppConfig(
        upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
        default_upstream="main",
    )
    app = create_app(config, config_path=str(tmp_path / "config.json"))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    app.state.runtime.terminal_projects.add(str(project_dir), "opencode")
    from llm_api_proxy_recorder.workspace import routes
    launched = []
    monkeypatch.setattr(routes.subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    with TestClient(app) as client:
        registered_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        assert client.post(f"/__recorder/api/workspace/projects/{registered_id}/open").json() == {"ok": True}
        assert launched[0][-1] == str(project_dir)
        assert client.post("/__recorder/api/workspace/projects/missing/open").status_code == 404


def test_workspace_projects_and_session_routes(tmp_path):
    config = AppConfig(
        upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
        default_upstream="main",
        terminal=TerminalConfig(route_through_proxy=False),
        model_settings={"show_native_models": True},
    )
    app = create_app(config, config_path=str(tmp_path / "config.json"))
    calls = []

    async def fake_request(project, cfg, method, endpoint, *, body=None, params=None):
        calls.append((project, method, endpoint, body))
        if endpoint == "/session" and method == "GET":
            return [{"id": "ses_123", "title": "existing"}]
        if endpoint == "/session" and method == "POST":
            return {"id": "ses_new", "title": body.get("title")}
        if endpoint.endswith("/message"):
            return [{"info": {"id": "msg_1", "role": "user"}, "parts": [{"type": "text", "text": "hello"}]}]
        if endpoint == "/agent":
            return [{"name": "build", "mode": "primary"}]
        if endpoint == "/command":
            return [{"name": "review", "description": "Review changes"}]
        if endpoint == "/config/providers":
            return {"providers": [{"id": "main", "models": {"model-x": {}}}], "default": {}}
        if endpoint == "/provider":
            return {"connected": ["main"]}
        if endpoint == "/question":
            return [{"id": "que_123", "sessionID": "ses_123", "questions": []}]
        return {"ok": True}

    app.state.runtime.workspace.request = fake_request
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    app.state.runtime.terminal_projects.add(str(project_dir), "shell")
    with TestClient(app) as client:
        prefix = "/__recorder/api/workspace"
        assert 'data-nav="workspace"' in client.get("/__recorder/").text
        assert client.get("/__recorder/workspace.js").status_code == 200
        assert client.get("/__recorder/workspace.css").status_code == 200
        added = client.post(f"{prefix}/projects", json={"path": str(project_dir)})
        assert added.status_code == 201
        project_id = added.json()["id"]
        assert app.state.runtime.terminal_projects.list()[0]["kind"] == "shell"
        assert client.get(f"{prefix}/projects").json()["items"][0]["id"] == project_id
        renamed = client.patch(f"{prefix}/projects/{project_id}", json={"name": "我的工作区"})
        assert renamed.json()["name"] == "我的工作区"
        assert renamed.json()["path"] == str(project_dir)
        assert client.get(f"{prefix}/projects").json()["items"][0]["name"] == "我的工作区"
        repeated = client.post(f"{prefix}/projects", json={"path": str(project_dir)})
        assert repeated.status_code == 201
        assert repeated.json()["name"] == "我的工作区"
        assert client.get(f"{prefix}/projects").json()["total"] == 1
        assert client.patch(f"{prefix}/projects/{project_id}", json={"name": "   "}).status_code == 400
        fresh_dir = tmp_path / "fresh"
        fresh_dir.mkdir()
        assert client.post(f"{prefix}/projects", json={"path": str(fresh_dir)}).status_code == 201
        assert client.post(f"{prefix}/projects", json={"path": str(fresh_dir)}).status_code == 201
        assert client.post(f"{prefix}/projects", json={"path": str(tmp_path / 'missing')}).status_code == 400
        fresh_id = next(item["id"] for item in client.get(f"{prefix}/projects").json()["items"] if item["name"] == "fresh")
        assert client.delete(f"{prefix}/projects/{fresh_id}").json() == {"ok": True}
        assert client.get(f"{prefix}/projects/{project_id}/sessions").json()["items"][0]["id"] == "ses_123"
        assert client.post(f"{prefix}/projects/{project_id}/sessions", json={"title": "new"}).json()["id"] == "ses_new"
        assert client.get(f"{prefix}/projects/{project_id}/sessions/ses_123").status_code == 200
        assert client.patch(f"{prefix}/projects/{project_id}/sessions/ses_123", json={"title": "renamed"}).status_code == 200
        assert calls[-1][1:] == ("PATCH", "/session/ses_123", {"title": "renamed"})
        assert client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/fork", json={"message_id": "msg_1"}).status_code == 200
        assert calls[-1][1:] == ("POST", "/session/ses_123/fork", {})
        assert client.get(f"{prefix}/projects/{project_id}/sessions/ses_123/todo").status_code == 200
        assert calls[-1][2] == "/session/ses_123/todo"
        assert client.get(f"{prefix}/projects/{project_id}/sessions/ses_123/children").status_code == 200
        assert calls[-1][2] == "/session/ses_123/children"
        assert client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/share").status_code == 200
        assert calls[-1][2] == "/session/ses_123/share"
        assert client.delete(f"{prefix}/projects/{project_id}/sessions/ses_123/share").status_code == 200
        assert calls[-1][1] == "DELETE"
        assert client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/revert",
                           json={"message_id": "msg_1"}).status_code == 200
        assert calls[-1][3] == {"messageID": "msg_1"}
        assert client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/unrevert").status_code == 200
        assert calls[-1][2] == "/session/ses_123/unrevert"
        assert client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/summarize",
                           json={"provider_id": "main", "model_id": "model-x"}).status_code == 200
        assert calls[-1][3] == {"providerID": "main", "modelID": "model-x"}
        assert client.get(f"{prefix}/projects/{project_id}/sessions/ses_123/messages").json()[0]["parts"][0]["text"] == "hello"
        models = client.get(f"{prefix}/projects/{project_id}/models").json()
        assert models["providers"][0]["id"] == "main"
        assert models["connected"] == ["main"]
        saved_key = client.post(f"{prefix}/projects/{project_id}/providers/main/api-key",
                                json={"key": "test-secret"})
        assert saved_key.json() == {"ok": True, "provider_id": "main", "configured": True}
        assert calls[-1][1:] == ("PUT", "/auth/main", {"type": "api", "key": "test-secret"})
        assert client.post(f"{prefix}/projects/{project_id}/providers/main/api-key",
                           json={"key": ""}).status_code == 422
        assert client.get(f"{prefix}/projects/{project_id}/agents").json()[0]["name"] == "build"
        assert client.get(f"{prefix}/projects/{project_id}/commands").json()[0]["name"] == "review"
        assert client.get(f"{prefix}/projects/{project_id}/questions").json()[0]["id"] == "que_123"
        assert client.post(f"{prefix}/projects/{project_id}/questions/que_123/reply",
                           json={"answers": [["Yes"]]}).status_code == 200
        assert calls[-1][1:] == ("POST", "/question/que_123/reply", {"answers": [["Yes"]]})
        assert client.post(f"{prefix}/projects/{project_id}/questions/que_123/reject").status_code == 200
        assert calls[-1][1:] == ("POST", "/question/que_123/reject", {})
        assert client.get(f"{prefix}/projects/{project_id}/permissions").status_code == 200
        assert client.post(
            f"{prefix}/projects/{project_id}/permissions/per_123/reply",
            json={"reply": "once"},
        ).status_code == 200
        assert calls[-1][1:] == ("POST", "/permission/per_123/reply", {"reply": "once"})
        prompt = client.post(
            f"{prefix}/projects/{project_id}/sessions/ses_123/prompt",
            json={"text": "fix it", "provider_id": "main", "model_id": "model-x"},
        )
        assert prompt.status_code == 200
        assert calls[-1][1:] == (
            "POST", "/session/ses_123/prompt_async",
            {"parts": [{"type": "text", "text": "fix it"}],
             "model": {"providerID": "main", "modelID": "model-x"}},
        )
        file_url = "data:image/png;base64," + base64.b64encode(b"fake-image").decode("ascii")
        attachment = {"filename": "image.png", "mime": "image/png", "url": file_url}
        attached = client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/prompt",
                               json={"text": "", "files": [attachment], "variant": "high"})
        assert attached.status_code == 200
        assert calls[-1][3] == {"parts": [{"type": "file", **attachment}], "variant": "high"}
        assert client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/prompt",
                           json={"text": ""}).status_code == 400
        assert client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/prompt",
                           json={"files": [{**attachment, "url": "https://example.com/image.png"}]}).status_code == 400
        assert client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/prompt",
                           json={"files": [{**attachment, "filename": "../image.png"}]}).status_code == 400
        client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/prompt",
                    json={"text": "plan it", "agent": "plan"})
        assert calls[-1][3]["agent"] == "plan"
        command = client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/command",
                              json={"command": "review", "arguments": "HEAD", "agent": "build",
                                    "provider_id": "main", "model_id": "model-x", "variant": "low"})
        assert command.status_code == 200
        assert calls[-1][1:] == (
            "POST", "/session/ses_123/command",
            {"command": "review", "arguments": "HEAD", "agent": "build", "variant": "low",
             "model": "main/model-x"},
        )
        shell = client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/shell",
                            json={"command": "pwd", "agent": "build"})
        assert shell.status_code == 200
        assert calls[-1][1:] == (
            "POST", "/session/ses_123/shell", {"command": "pwd", "agent": "build"},
        )
        assert client.post(f"{prefix}/projects/{project_id}/sessions/ses_123/command",
                           json={"command": "bad/name"}).status_code == 422
        assert client.get(f"{prefix}/projects/missing/sessions").status_code == 404
        assert client.get(f"{prefix}/projects/{project_id}/sessions/bad.id/messages").status_code == 400
        assert client.delete(f"{prefix}/projects/{project_id}/sessions/ses_123").status_code == 200
        assert calls[-1][1:3] == ("DELETE", "/session/ses_123")
        assert client.delete(f"{prefix}/projects/{project_id}").json() == {"ok": True}
        assert client.get(f"{prefix}/projects").json()["items"] == []


@pytest.mark.parametrize("message_id, expected_ids, boundary", [
    ("msg_2", ["msg_1", "msg_2"], "msg_3"),
    ("msg_4", ["msg_1", "msg_2", "msg_3", "msg_4"], None),
    (None, ["msg_1", "msg_2", "msg_3", "msg_4"], None),
])
def test_message_fork_includes_selected_reply(tmp_path, message_id, expected_ids, boundary):
    config = AppConfig(
        upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
        default_upstream="main",
    )
    app = create_app(config, config_path=str(tmp_path / "config.json"))
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    app.state.runtime.terminal_projects.add(str(project_dir), "opencode")
    messages = [{"info": {"id": f"msg_{i}", "role": "user" if i % 2 else "assistant"}}
                for i in range(1, 5)]
    fork_payloads = []

    async def fake_request(project, cfg, method, endpoint, *, body=None, params=None):
        if method == "GET" and endpoint.endswith("/message"):
            return messages
        if method == "POST" and endpoint.endswith("/fork"):
            fork_payloads.append(body)
            # Match OpenCode's exclusive boundary semantics.
            cutoff = next((i for i, item in enumerate(messages)
                           if item["info"]["id"] == body.get("messageID")), len(messages))
            return {"id": "ses_fork", "messages": messages[:cutoff]}
        raise AssertionError((method, endpoint))

    app.state.runtime.workspace.request = fake_request
    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        endpoint = f"/__recorder/api/workspace/projects/{project_id}/sessions/ses_original/fork"
        response = client.post(endpoint, json={"message_id": message_id})
        assert response.status_code == 200
        assert [item["info"]["id"] for item in response.json()["messages"]] == expected_ids
        assert fork_payloads == ([{"messageID": boundary}] if boundary else [{}])
        assert len(messages) == 4
        missing = client.post(endpoint, json={"message_id": "msg_missing"})
        assert missing.status_code == 404
        invalid = client.post(endpoint, json={"message_id": "bad.id"})
        assert invalid.status_code == 400
        assert len(fork_payloads) == 1


@pytest.mark.parametrize("failed", [False, True])
def test_withdraw_last_turn_deletes_all_replies_only(tmp_path, failed):
    config = AppConfig(default_upstream="", recording={"dir": str(tmp_path / "records")})
    app = create_app(config, config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    project.mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    messages = [
        {"info": {"id": "msg_old", "role": "user"}, "parts": []},
        {"info": {"id": "msg_old_reply", "role": "assistant"}, "parts": []},
        {"info": {"id": "msg_last", "role": "user"}, "parts": [{"type": "text", "text": "retry me"}]},
        {"info": {"id": "msg_tool", "role": "assistant"}, "parts": [{"type": "tool"}]},
        {"info": {"id": "msg_reply", "role": "assistant", **({"error": {"data": {"message": "raw error"}}} if failed else {})}, "parts": []},
    ]
    deleted = []
    status = "idle"

    async def native(project, cfg, method, endpoint, **kwargs):
        if endpoint == "/session/status":
            return {"ses_test": {"type": status}}
        if method == "GET" and endpoint.endswith("/message"):
            return messages
        assert method == "DELETE" and "/message/" in endpoint
        deleted.append(endpoint.rsplit("/", 1)[-1])
        messages[:] = [m for m in messages if m["info"]["id"] != deleted[-1]]
        return True

    app.state.runtime.workspace.request = native
    with TestClient(app) as client:
        pid = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        url = f"/__recorder/api/workspace/projects/{pid}/sessions/ses_test/withdraw"
        assert client.post(url, json={"message_id": "msg_old"}).status_code == 409
        assert client.post(url, json={"message_id": "bad/id"}).status_code == 400
        for status in ("busy", "retry"):
            assert client.post(url, json={"message_id": "msg_last"}).status_code == 409
        assert not deleted
        status = "idle"
        assert client.post(url, json={"message_id": "msg_last"}).json() == {"ok": True}
        assert deleted == ["msg_reply", "msg_tool", "msg_last"]
        assert [m["info"]["id"] for m in messages] == ["msg_old", "msg_old_reply"]


def test_workspace_trajectory_reuses_recorded_session_ledger(tmp_path):
    from llm_api_proxy_recorder.config import RecordingConfig
    from llm_api_proxy_recorder.recording.parse import session_key_from_header

    config = AppConfig(
        upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
        default_upstream="main",
        recording=RecordingConfig(dir=str(tmp_path / "records"), record_request_headers=False),
    )
    app = create_app(config, config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    project.mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    store = app.state.runtime.store
    key = session_key_from_header("ses_current")
    for number, session in enumerate(["ses_current", "ses_other", "ses_current"]):
        store.finalize({
            "id": f"c20260927_12000{number}_abcdef",
            "started_at": f"2026-09-27T12:00:0{number}+08:00",
            "session_key": session_key_from_header(session),
            "model": "model-test", "status": "ok",
            "request": {"parsed": {"messages": [{"role": "user", "content": "hello"}]}},
            "response": {"status_code": 200, "parsed": {"message": {"role": "assistant", "content": "hi"}}},
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        })
    with TestClient(app) as client:
        pid = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        base = f"/__recorder/api/workspace/projects/{pid}/sessions"
        result = client.get(f"{base}/ses_current/trajectory")
        assert result.status_code == 200
        assert result.json() == client.get(f"/__recorder/api/trajectory/sessions/{key}").json()
        assert [turn["call_id"] for turn in result.json()["turns"]] == [
            "c20260927_120000_abcdef", "c20260927_120002_abcdef",
        ]
        assert result.json()["cumulative_usage"]["total_tokens"] == 24
        assert client.get(f"{base}/ses_empty/trajectory").json()["turns"] == []
        assert client.get(f"{base}/bad.id/trajectory").status_code == 400
        assert client.get("/__recorder/api/workspace/projects/missing/sessions/ses_current/trajectory").status_code == 404


def test_workspace_activity_call_ownership_handles_retries_and_ambiguity():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace activity regression coverage")
    script = r'''
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("llm_api_proxy_recorder/web/static/workspace.js", "utf8"));
const assistant = (id, created, completed) => ({info: {id, time: {created, completed}}});
const call = (id, time) => ({call_id: id, started_at: new Date(time).toISOString()});
const messages = [assistant("first", 1000, 3000), assistant("second", 4000, 5000), assistant("pending", 6000)];
const turns = [call("retry1", 1500), call("retry2", 2500), call("second-call", 4500), call("pending-call", 6500), call("unmatched", 3500)];
assert.deepEqual([...workspaceCallOwners(messages, turns)], [
  ["retry1", "first"], ["retry2", "first"], ["second-call", "second"], ["pending-call", "pending"],
]);
assert.equal(workspaceCallOwners([assistant("a", 1000, 4000), assistant("b", 2000, 5000)], [call("ambiguous", 2500)]).size, 0);
assert.equal(workspaceCallOwners([assistant("a", undefined, undefined)], turns).size, 0);
assert.equal(workspaceCallOwners(messages, [{call_id: "invalid", started_at: "invalid"}]).size, 0);
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_workspace_diff_delegates_optional_user_message(tmp_path):
    config = AppConfig(
        upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
        default_upstream="main",
    )
    app = create_app(config, config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    project.mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    calls = []
    diff = [{"file": "src/app.py", "before": "old", "after": "new", "additions": 1, "deletions": 1}]

    async def fake_request(project, cfg, method, endpoint, *, body=None, params=None):
        calls.append((method, endpoint, params))
        return diff

    app.state.runtime.workspace.request = fake_request
    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        endpoint = f"/__recorder/api/workspace/projects/{project_id}/sessions/ses_test/diff"
        assert client.get(endpoint).json() == diff
        assert calls[-1] == ("GET", "/session/ses_test/diff", None)
        assert client.get(endpoint, params={"message_id": "msg_first"}).json() == diff
        assert calls[-1] == ("GET", "/session/ses_test/diff", {"messageID": "msg_first"})
        count = len(calls)
        assert client.get(endpoint, params={"message_id": "bad/id"}).status_code == 400
        assert client.get(endpoint, params={"message_id": ""}).status_code == 400
        assert len(calls) == count


def test_workspace_turn_changes_preserve_ownership_and_native_totals():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace changes regression coverage")
    script = r'''
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const source = fs.readFileSync("llm_api_proxy_recorder/web/static/workspace.js", "utf8");
vm.runInThisContext(source);
vm.runInThisContext(source.slice(source.indexOf("  function diffRows("), source.indexOf("  function diffLineCounts(")));
assert.deepEqual(diffRows({before: "", after: "new"}), [{type: "added", number: 1, text: "+new"}]);
assert.deepEqual(diffRows({before: "old", after: ""}), [{type: "removed", number: 1, text: "-old"}]);
const firstDiff = {file: "src/app.py", before: "a", after: "b", additions: 1, deletions: 1};
const secondDiff = {...firstDiff, after: "c", additions: 7, deletions: 2};
const user = (id, diffs) => ({info: {id, role: "user", ...(diffs ? {summary: {diffs}} : {})}});
const tool = (path, status = "completed") => ({type: "tool", tool: "edit",
  state: {status, input: {filePath: path, oldString: "old", newString: "new"}}});
const reply = (id, parentID, parts = []) => ({info: {id, role: "assistant", parentID}, parts});
const a = reply("a", "u1", [tool("/project/ignored.py")]);
const b = reply("b", "u1");
const c = reply("c", "u2");
const messages = [user("u1", [firstDiff]), a, b, user("u2", [secondDiff]), c];
assert.deepEqual(workspaceTurnDiffs(messages, a), [firstDiff]);
assert.deepEqual(workspaceTurnDiffs(messages, b), [firstDiff]);
assert.deepEqual(workspaceTurnDiffs(messages, c), [secondDiff]);
assert.deepEqual(workspaceTurnDiffs(messages, reply("orphan")), []);
assert.deepEqual(workspaceTurnDiffs([user("u1", []), a], a), []);
const working = reply("working", "u3", [tool("/project/src/app.py"), tool("/project/pending.py", "running"),
  tool("/project/failed.py", "error"), {type: "tool", tool: "read", state: {status: "completed", input: {filePath: "read.py"}}}]);
assert.deepEqual(workspaceTurnDiffs([user("u3"), working], working, "/project").map(diff => diff.file), ["src/app.py"]);
const patch = reply("patch", "u3", [{type: "tool", tool: "apply_patch", state: {status: "completed",
  metadata: {files: [{relativePath: "new.py", diff: "+new", additions: 1, deletions: 0},
    {filePath: "/project/gone.py", diff: "-old", additions: 0, deletions: 1}]}}}]);
assert.deepEqual(workspaceTurnDiffs([user("u3"), patch], patch, "/project").map(diff => diff.file), ["new.py", "gone.py"]);
assert.equal(workspaceTurnDiffs([user("u3"), working, reply("repeat", "u3", [tool("/project/src/app.py")])], working, "/project")[0].countsUnknown, true);
assert.equal(workspaceRelativeFile("C:\\project\\src\\app.js", "C:\\project"), "src/app.js");
assert.equal(workspaceRelativeFile("/project-other/app.js", "/project"), "/project-other/app.js");
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_workspace_placeholder_hides_during_ime_composition():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for composer regression coverage")
    script = r'''
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const source = fs.readFileSync("llm_api_proxy_recorder/web/static/workspace.js", "utf8");
const update = source.slice(source.indexOf("  function updateSkillInput() {"), source.indexOf("  function skillForTool("));
const input = {dataset: {}, setAttribute() {}};
let highlights = 0;
const context = {
  input, composingInput: false, state: {skills: [], commands: []}, builtInCommands: [],
  composer: {value: "", fileReferences: [], highlightSkill() {highlights++;}},
  view: {querySelector() {return {};}}
};
vm.createContext(context);
vm.runInContext(update, context);
const refresh = () => vm.runInContext("updateSkillInput()", context);
refresh();
assert.equal(input.dataset.empty, "true");
context.composingInput = true;
refresh();
assert.equal(input.dataset.empty, "false"); // Composition starts before any DOM text exists.
assert.equal(highlights, 1); // Do not rewrite mentions while the IME owns the edit.
context.composer.value = "ni";
refresh();
assert.equal(input.dataset.empty, "false");
context.composingInput = false;
context.composer.value = "你好";
refresh();
assert.equal(input.dataset.empty, "false");
context.composer.value = "";
refresh();
assert.equal(input.dataset.empty, "true");
context.composer.value = "a";
refresh();
assert.equal(input.dataset.empty, "false");
const css = fs.readFileSync("llm_api_proxy_recorder/web/static/workspace.css", "utf8");
assert.equal(css.includes(":empty::before"), false);
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_workspace_remembers_selection_and_pages_project_sessions():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace sidebar regression coverage")
    script = r'''
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const source = fs.readFileSync("llm_api_proxy_recorder/web/static/workspace.js", "utf8");
let stored = JSON.stringify({projectId: "project", sessionId: "session-8"});
const storage = {getItem() {return stored;}, setItem(key, value) {stored = value;}};
const selectionSource = source.slice(0, source.indexOf("// Link only calls"));
const restored = vm.createContext({localStorage: storage});
vm.runInContext(selectionSource, restored);
assert.equal(vm.runInContext("workspaceSelection.sessionId", restored), "session-8");
vm.runInContext('workspaceSelection.sessionId = "session-9"; persistWorkspaceSelection()', restored);
const restarted = vm.createContext({localStorage: storage});
vm.runInContext(selectionSource, restarted);
assert.equal(vm.runInContext("workspaceSelection.sessionId", restarted), "session-9");
for (const value of ["invalid json", "null", '{"projectId":5}']) {
  stored = value;
  const context = vm.createContext({localStorage: storage});
  vm.runInContext(selectionSource, context);
  assert.equal(vm.runInContext("workspaceSelection.projectId", context), null);
}
const blocked = vm.createContext({localStorage: {
  getItem() {throw Error("blocked");}, setItem() {throw Error("blocked");}
}});
vm.runInContext(selectionSource + "persistWorkspaceSelection();", blocked);

function el(tag, props = {}, ...children) {
  const node = {tag, ...props, children: [], get childNodes() {return this.children;},
    append(...items) {for (const item of items) this.insertBefore(item, null);},
    insertBefore(item, before) {item.remove(); const index = before ? this.children.indexOf(before) : this.children.length;
      this.children.splice(index,0,item); item.parent=this;},
    remove() {if (this.parent) {this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}},
    setAttribute(key,value) {this[key]=value;},
    replaceChildren(...items) {for (const item of [...this.children]) item.remove(); this.append(...items);}};
  node.append(...children); return node;
}
const sideList = el("div");
const project = {id: "project", name: "Project", path: "/project"};
const state = {
  projects: [project], sessions: new Map([[project.id, Array.from({length: 14}, (_, i) => ({id: `session-${i}`, title: `Session ${i}`}))]]),
  projectId: project.id, sessionId: "session-9", search: "", collapsedProjects: new Set(),
  sessionLimits: new Map(), errors: new Map(), projectStatuses: new Map(),
};
const context = vm.createContext({state, sideList, el, closeRowMenus() {},
  sidebarSections: new Map(), sidebarRows: new Map(), workspaceConversationKey: (p,s) => JSON.stringify([p,s]), statusIcon: () => el("svg"),
  view: {querySelector() {return {}; }}, rowMenu: () => el("div"),
  sessionTitle: s => s.title, shortStamp: () => "", stamp: () => ""});
const sidebarSource = source.slice(source.indexOf("  function renderSidebar() {"), source.indexOf("  function renderHeader() {"));
vm.runInContext(source.slice(source.indexOf("function workspaceSyncChildren("), source.indexOf("function renderWorkspace(")) + sidebarSource, context);
const render = () => vm.runInContext("renderSidebar()", context);
const threads = () => sideList.children[0].children[1];
const rows = () => threads().children.filter(x => x.class === "wsp-thread-row");
const more = () => threads().children.find(x => x.class === "wsp-show-more");
render();
assert.equal(rows().length, 6);
state.projectStatuses.set(project.id, {"session-0": {type:"busy"}}); render();
const firstRow = rows()[0], spinner = firstRow.children[0].children[1];
render();
assert.equal(rows()[0], firstRow); assert.equal(rows()[0].children[0].children[1], spinner);
assert.equal(spinner.parent, firstRow.children[0]);
assert.equal(firstRow.children[0].children[0].children.length, 2); // Title and single timestamp.
state.projectStatuses.set(project.id, {"session-0": {type:"retry"}}); render();
assert.equal(firstRow.children[0].children[1], spinner); assert.equal(spinner.title, "正在重试");
state.projectStatuses.set(project.id, {}); render(); assert.equal(spinner.parent, null);
more().onclick();
assert.equal(rows().length, 12);
more().onclick();
assert.equal(rows().length, 14);
assert.equal(more(), undefined);
const heading = () => sideList.children[0].children[0].children[0];
heading().onclick();
assert.equal(state.collapsedProjects.has(project.id), true);
heading().onclick();
assert.equal(state.collapsedProjects.has(project.id), false);
assert.equal(rows().length, 6);
assert.ok(more());
state.search = "Session 13";
render();
assert.equal(rows().length, 1);
assert.equal(more(), undefined);

let refreshes = 0;
const loadContext = vm.createContext({state, workspaceConversationKey: (p,s) => JSON.stringify([p,s]), workspaceSelection: {sessionId: "session-9"},
  api: async () => ({items: state.sessions.get(project.id)}), alive: () => true,
  scrollToLatestOnLoad: false, lastSessionListRefresh: 0,
  saveDraft() {}, restoreDraft() {}, persistWorkspaceSelection() {}, refreshSelected() {refreshes++;}, renderSidebar() {}, renderHeader() {},
});
state.sessionDetails = new Map();
const loadSource = source.slice(source.indexOf("  async function loadSessions(project) {"), source.indexOf("  function modelDisplayName("));
vm.runInContext(loadSource, loadContext);
(async () => {
  await vm.runInContext('loadSessions({id: "project"})', loadContext);
  assert.equal(state.sessionId, "session-9");
  assert.equal(refreshes, 1);
  state.sessionId = "deleted";
  await vm.runInContext('loadSessions({id: "project"})', loadContext);
  assert.equal(state.sessionId, "session-0");
  assert.equal(refreshes, 2);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_project_directory_browser_navigation_and_stale_results():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for browser interaction coverage")
    script = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const source = fs.readFileSync("llm_api_proxy_recorder/web/static/workspace.js", "utf8");
const nodes = [];
function el(tag, props = {}, ...children) {
  const classes = new Set((props.class || "").split(" "));
  const node = {tag, ...props, children, value: props.value || "", isConnected: true,
    classList: {toggle(c, on) {if (on) classes.add(c); else classes.delete(c);}, remove(c) {classes.delete(c);}},
    append(...items) {this.children.push(...items);}, replaceChildren(...items) {this.children = items;},
    addEventListener(name, fn) {this[name] = fn;}, focus() {}, remove() {this.isConnected = false;}};
  nodes.push(node); return node;
}
let pending = [], timers = [];
const context = vm.createContext({el, window: {}, document: {body: {append() {}}},
  setTimeout(fn) {timers.push(fn); return fn;}, clearTimeout(fn) {timers = timers.filter(x => x !== fn);},
  api(url) {return new Promise((resolve, reject) => pending.push({url, resolve, reject}));},
  detail: e => e.message, encodeURIComponent});
vm.runInContext(source.slice(source.indexOf("  function openAddProject() {"), source.indexOf("  function openRenameProject(")), context);
vm.runInContext("openAddProject()", context);
const input = nodes.find(n => n.tag === "input");
const browser = nodes.find(n => n.class === "wsp-directory-browser hidden");
const flush = async () => {await new Promise(resolve => setImmediate(resolve));};
const type = path => {input.value = path; input.input(); timers.shift()();};
(async () => {
  type("/old"); type("/projects");
  pending[1].resolve({path: "/projects", parent: "/", entries: [{name: "alpha", path: "/projects/alpha"}]}); await flush();
  pending[0].resolve({path: "/old", parent: "/", entries: []}); await flush();
  assert.equal(browser.children[0].children[0].text, "/projects");
  browser.children[1].children[0].onclick(); timers.shift()();
  assert.equal(input.value, "/projects/alpha");
  pending[2].resolve({path: input.value, parent: "/projects", entries: []}); await flush();
  browser.children[0].children[1].onclick(); timers.shift()();
  assert.equal(input.value, "/projects");
  pending[3].resolve({path: input.value, parent: "/", entries: []}); await flush();
  type("/projects/al"); pending[4].reject(new Error("missing")); await flush();
  assert.ok(pending[5].url.endsWith(encodeURIComponent("/projects/")));
  pending[5].resolve({path: "/projects/", parent: "/", entries: [{name: "alpha", path: "/projects/alpha"}, {name: "beta", path: "/projects/beta"}]}); await flush();
  assert.equal(browser.children[1].children.length, 1);
  assert.equal(browser.children[1].children[0].text, "📁 alpha");
  type("/closed"); nodes.find(n => n.class === "wsp-modal-mask").remove();
  pending[6].resolve({path: "/closed", parent: "/", entries: []}); await flush();
  assert.equal(browser.children[0].text, "正在读取目录…");
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)
