"""Native child-session grouping, status evidence and task navigation."""

import shutil
import subprocess
from pathlib import Path

import pytest


def run_node(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace subagent coverage")
    subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True,
    )


def test_subagent_tree_preserves_order_orphans_and_nested_children():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/workspace.js", "utf8"));
const sessions = [
  {id: "nested", parentID: "child"}, {id: "child", parentID: "main"},
  {id: "other"}, {id: "main"}, {id: "orphan", parentID: "missing"},
  {id: "self", parentID: "self"}, {id: "cycle-a", parentID: "cycle-b"},
  {id: "cycle-b", parentID: "cycle-a"},
];
const tree = workspaceSessionTree(sessions);
assert.deepEqual(tree.roots.map(item => item.id), ["other", "main", "orphan", "self", "cycle-a", "cycle-b"]);
assert.deepEqual(tree.descendants.get("main").map(item => item.id), ["nested", "child"]);
assert.equal(tree.rootIds.get("nested"), "main");
assert.equal(sessions[0].id, "nested", "Grouping must not reorder native data or user drag order");
assert.equal(tree.roots.length + [...tree.descendants.values()].flat().length, sessions.length);
assert.equal(workspaceSessionTree([]).roots.length, 0);
''')


def test_subagent_status_requires_native_evidence_of_completion():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/workspace.js", "utf8"));
const task = (status, metadata = {}) => ({type: "tool", tool: "task", state: {status, metadata: {sessionId: "s", ...metadata}}});
const completed = task("completed");
assert.equal(workspaceTaskSessionId(completed), "s");
assert.equal(workspaceTaskSessionId(task("running", {sessionId: null, sessionID: "legacy"})), "legacy");
assert.equal(workspaceTaskSessionId({type: "tool", tool: "read", state: {metadata: {sessionId: "s"}}}), null);
assert.equal(workspaceTaskSessionId(task("pending", {sessionId: 42})), null);
assert.equal(workspaceSubagentStatus("s", {}, [], null).label, "空闲");
assert.equal(workspaceSubagentStatus("s", {s: {type: "idle"}}, [], null).label, "空闲");
assert.equal(workspaceSubagentStatus("s", {}, [], completed).label, "已完成");
assert.equal(workspaceSubagentStatus("s", {}, [], task("completed", {background: true})).label, "空闲");
assert.equal(workspaceSubagentStatus("s", {s: {type: "busy"}}, [], completed).label, "运行中");
assert.equal(workspaceSubagentStatus("s", {s: {type: "retry"}}, [], completed).label, "重试中");
assert.equal(workspaceSubagentStatus("s", {s: {type: "busy"}}, [{sessionID: "s"}], completed).label, "待确认");
assert.equal(workspaceSubagentStatus("s", {}, [{sessionID: "other"}], completed).label, "已完成");
assert.equal(workspaceSubagentStatus("s", {}, [], task("error")).label, "失败");
assert.equal(workspaceSubagentStatus("s", {}, [], task("error", {interrupted: true})).label, "已停止");
assert.equal(workspaceSubagentStatus("s", {}, [], task("error", {interrupted: true}),
  [{info: {role: "assistant", finish: "tool-calls", time: {completed: 1}}}]).label, "已停止",
  "Completing an intermediate model step must not hide an interrupted child task");
assert.equal(workspaceSubagentStatus("s", {}, [], null, [{info: {role: "assistant", time: {completed: 1}}}]).label, "已完成");
assert.equal(workspaceSubagentStatus("s", {}, [], null, [{info: {role: "assistant", error: {name: "APIError"}}}]).label, "失败");
assert.equal(workspaceSubagentStatus("s", {}, [], null, [], true).label, "已完成");
''')


def test_related_navigation_resolves_real_sessions_and_discards_stale_requests():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const requests = [], selections = [], errors = [];
const context = vm.createContext({state: {sessions: new Map(), sessionDetails: new Map()},
  relatedSessionRequest: 0, closeSubagentPopover() {}, alive: () => true,
  api: path => new Promise(resolve => requests.push({path, resolve})),
  sessionPath: (p,s) => `${p}/${s}`, workspaceConversationKey: (p,s) => JSON.stringify([p,s]),
  selectSession: (p,s) => selections.push([p,s]), toast: message => errors.push(message), detail: error => error.message,
});
vm.runInContext(source.slice(source.indexOf("  function knownSession("), source.indexOf("  function subagentStatus(")), context);
(async () => {
  const open = context.openRelatedSession("p", "child");
  assert.deepEqual(selections, [], "Do not invent a child session before native lookup");
  requests[0].resolve({id: "child", parentID: "parent"}); await open;
  assert.deepEqual(selections, [["p", "child"]]);
  assert.equal(context.state.sessionDetails.get('["p","child"]').parentID, "parent");
  await context.openRelatedSession("p", "child");
  assert.equal(requests.length, 1, "Known session navigation reuses metadata");
  const stale = context.openRelatedSession("p", "another");
  context.relatedSessionRequest++;
  requests[1].resolve({id: "another", parentID: "parent"}); await stale;
  assert.equal(selections.length, 2, "Late requests cannot interrupt a new selection");
  const invalid = context.openRelatedSession("p", "deleted");
  requests[2].resolve({id: "unrelated"}); await invalid;
  assert.equal(selections.length, 2); assert.equal(errors.length, 1);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_task_link_uses_metadata_and_captures_project_context():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
vm.runInThisContext(source);
const calls = [];
global.state = {projectId: "p"};
global.el = (tag, attrs = {}, ...children) => ({tag, ...attrs, children});
global.workspaceIcon = name => ({icon: name});
global.openRelatedSession = (p,s) => calls.push([p,s]);
vm.runInThisContext(source.slice(source.indexOf("  function taskSessionLink("), source.indexOf("  function toolGroup(")));
assert.equal(taskSessionLink({type: "tool", tool: "task", state: {input: {task_id: "guessed"}}}), null);
const link = taskSessionLink({type: "tool", tool: "task", state: {metadata: {sessionId: "native"}, input: {subagent_type: "explore"}}});
assert.equal(link.children[2].text, "explore");
state.projectId = "other"; link.onclick();
assert.deepEqual(calls, [["p", "native"]]);
''')
