"""File-change regressions for V1 writes, net patches, and missing baselines."""

import copy
import difflib
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.config import AppConfig, UpstreamConfig
from sona_code.workspace.changes import annotate_changes, combine_changes, history_changes


def tool(name, input=None, metadata=None, status="completed"):
    return {"type": "tool", "tool": name, "state": {
        "status": status, "input": input or {}, "metadata": metadata or {},
    }}


def read(text, **display):
    lines = text.split("\n") if text else []
    return tool("read", {"filePath": "/project/README.md"}, {"display": {
        "type": "file", "path": "/project/README.md", "text": text,
        "lineStart": 1, "lineEnd": len(lines), "totalLines": len(lines),
        "truncated": False, **display,
    }})


def write(content, exists=True, status="completed"):
    return tool("write", {"filePath": "/project/README.md", "content": content}, {"exists": exists}, status)


def messages(*parts, id="u"):
    return [{"info": {"id": id, "role": "user"}},
            {"info": {"id": "a-" + id, "role": "assistant", "parentID": id}, "parts": list(parts)}]


def patch(before, after, file="README.md", **extra):
    old, new = before.splitlines(), after.splitlines()
    return {"file": file, "patch": "\n".join(difflib.unified_diff(old, new, file, file, lineterm="")), **extra}


def test_overwrite_360_lines_compares_read_content_and_keeps_native_history_untouched():
    before = [f"line {i}" for i in range(360)]
    after = [*before]
    after[50:52] = ["replacement one", "replacement two"]
    history = messages(read("\n".join(before)), write("\n".join(after) + "\n"))
    original = copy.deepcopy(history)
    enriched = annotate_changes(history, "/project")
    diff = enriched[0]["info"]["sonaDiffs"][0]
    assert (diff["additions"], diff["deletions"]) == (2, 2)
    assert "-line 50" in diff["patch"] and "+replacement one" in diff["patch"]
    assert diff["comparison"] == "read-write"
    assert history == original
    assert history_changes(history, "/project", "u") == [diff]


@pytest.mark.parametrize("display", [
    {"truncated": True}, {"lineStart": 2}, {"lineEnd": 1, "totalLines": 50},
    {"totalLines": 3, "lineEnd": 3},
])
def test_partial_read_does_not_invent_deletions(display):
    diff = history_changes(messages(read("old", **display), write("new\n")), "/project")[0]
    assert diff["countsUnknown"] is True
    assert diff["writtenLines"] == 1
    assert "deletions" not in diff


def test_long_line_truncation_shell_commands_and_other_turns_invalidate_read_baselines():
    for parts in [
        [read("old... (line truncated to 2000 chars)"), write("new")],
        [read("old"), tool("bash"), write("new")],
        [read("old"), tool("task"), write("new")],
        [read("old"), write("partial", status="error"), write("new")],
    ]:
        assert history_changes(messages(*parts), "/project")[0]["countsUnknown"]
    history = messages(read("old")) + messages(write("new"), id="next")
    assert history_changes(history, "/project", "next")[0]["countsUnknown"]
    assert history_changes(messages(write("pending", status="running")), "/project") == []
    overlapping_read, overlapping_write = read("old"), write("new")
    overlapping_read["state"]["time"] = {"start": 1, "end": 5}
    overlapping_write["state"]["time"] = {"start": 3, "end": 6}
    assert history_changes(messages(overlapping_read, overlapping_write), "/project")[0]["countsUnknown"]


def test_blank_lines_new_files_and_empty_file_operations():
    diff = history_changes(messages(read("keep\n"), write("keep\nnew\n")), "/project")[0]
    assert (diff["additions"], diff["deletions"]) == (1, 1)
    created = history_changes(messages(write("first\nsecond\n", exists=False)), "/project")[0]
    assert (created["additions"], created["deletions"], created["status"]) == (2, 0, "added")
    empty = history_changes(messages(write("", exists=False)), "/project")[0]
    assert empty["status"] == "added"
    removed = history_changes(messages(tool("apply_patch", metadata={"files": [
        {"relativePath": "README.md", "type": "delete", "patch": "", "additions": 0, "deletions": 0},
    ]})), "/project")[0]
    assert removed["status"] == "deleted"


def test_patch_composition_counts_final_changes_and_cancels_reverted_edits():
    original = "\n".join(f"line {i}" for i in range(40))
    first = original.replace("line 5\n", "first\nsecond\n")
    second = first.replace("line 30", "updated 30")
    combined = combine_changes([patch(original, first), patch(first, second)])[0]
    assert (combined["additions"], combined["deletions"]) == (3, 2)
    assert "cumulative" not in combined
    assert "\x00unchanged" not in combined["patch"]
    assert combine_changes([patch(original, first), patch(first, original)]) == []
    assert combine_changes([patch("", "new", status="added"), patch("new", "", status="deleted")]) == []
    assert combine_changes([patch("a\nb", "a\nx\nb"), patch("a\nx\nb", "a\ny\nb")])[0]["additions"] == 1


def test_conflicting_or_missing_patches_keep_partial_activity_counts():
    first = {**patch("old", "new"), "additions": 1, "deletions": 1}
    conflicting = {**patch("other", "final"), "additions": 1, "deletions": 1}
    combined = combine_changes([first, conflicting])[0]
    assert combined["cumulative"] and combined["additions"] == combined["deletions"] == 2
    unknown = {"file": "README.md", "countsUnknown": True, "writtenLines": 360}
    combined = combine_changes([first, unknown])[0]
    assert combined["countsPartial"] and combined["cumulative"]
    assert combined["writtenLines"] == 360
    # EOF newline changes affect bytes even when the visible line is identical.
    # Keep native counts instead of incorrectly cancelling this patch to zero.
    eof = {"file": "README.md", "patch": "--- README.md\n+++ README.md\n@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+old",
           "additions": 1, "deletions": 1}
    assert combine_changes([eof, first])[0]["cumulative"]


def test_native_turn_summaries_drive_session_changes_and_message_ownership():
    first = messages(write("one", exists=False))
    first[0]["info"]["summary"] = {"diffs": [patch("old", "new", additions=1, deletions=1)]}
    second = messages(write("two", exists=False), id="next")
    second[0]["info"]["summary"] = {"diffs": [patch("new", "old", additions=1, deletions=1)]}
    assert history_changes(first + second, "/project") == []
    assert history_changes(first + second, "/project", "u")[0]["patch"] == first[0]["info"]["summary"]["diffs"][0]["patch"]
    assert history_changes(first + second, "/project", "missing") == []
    binary = {"file": "image.png", "patch": "", "additions": 0, "deletions": 0, "status": "modified"}
    assert combine_changes([binary]) == [binary]


def test_diff_routes_recover_empty_native_v1_response_and_enrich_messages(tmp_path):
    config = AppConfig(upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")], default_upstream="main")
    app = create_app(config, config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    project.mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    history = messages(read("old"), write("new\n"))
    calls = []

    async def fake_request(project, cfg, method, endpoint, *, body=None, params=None):
        calls.append((endpoint, params))
        return history if endpoint.endswith("/message") else []

    app.state.runtime.workspace.request = fake_request
    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        base = f"/__recorder/api/workspace/projects/{project_id}/sessions/ses_test"
        for query in ("", "?message_id=u"):
            result = client.get(base + "/diff" + query).json()
            assert (result[0]["additions"], result[0]["deletions"]) == (1, 1)
        assert ("/session/ses_test/diff", {"messageID": "u"}) in calls
        assert client.get(base + "/messages").json()[0]["info"]["sonaDiffs"][0]["deletions"] == 1
        assert "sonaDiffs" not in history[0]["info"]


def test_changes_ui_distinguishes_unknown_counts_and_honors_empty_net_results():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for file changes rendering coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
vm.runInThisContext(source);
vm.runInThisContext(source.slice(source.indexOf("  function diffRows("), source.indexOf("  function changeFileIcon(")));
vm.runInThisContext(source.slice(source.indexOf("  function changedFiles("), source.indexOf("  function renderSessionTrajectory(")));
global.el = (tag, props = {}, ...children) => ({tag, ...props, children, append(...nodes) {this.children.push(...nodes);}});
global.workspaceIcon = () => el("svg");
global.activeProject = () => ({path: "/project"});
global.content = el("main");
global.empty = (title, text) => el("p", {text});
const unknown = {file: "README.md", countsUnknown: true, derived: true, writtenLines: 360, input: {content: "new"}};
global.state = {sessionId: "s", messages: [], diffs: [unknown], diffsLoaded: true};
const descendants = n => [n, ...n.children.filter(Boolean).flatMap(descendants)];
renderChanges();
const nodes = descendants(content);
assert(nodes.some(n => n.text === "不可用"));
assert(nodes.some(n => n.text === "增删不可用"));
assert(!nodes.some(n => n.text === "增删待确认"));
assert(!nodes.find(n => n.text === "写入 360 行").class?.includes("add"));
const withPatch = {...unknown, patch: "--- old\n+++ new\n@@ -1 +1 @@\n-old\n+new"};
assert(diffRows(withPatch).some(row => row.type === "removed" && row.text === "-old"));
const user = {info: {id: "u", role: "user", sonaDiffs: []}};
const reply = {info: {id: "a", role: "assistant", parentID: "u"}, parts: [
  {type: "tool", tool: "write", state: {status: "completed", input: {filePath: "README.md", content: "new"}}}]};
assert.deepEqual(workspaceTurnDiffs([user, reply], reply), []);
state.messages = [user, reply]; state.diffs = [];
assert.deepEqual(changedFiles(), []);
state.diffs = [{file: "README.md", cumulative: true, additions: 2, deletions: 2}];
content.children = []; renderChanges();
assert(descendants(content).some(n => n.text === "累计新增行"));
assert(descendants(content).some(n => n.text === "累计删除行"));
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)
