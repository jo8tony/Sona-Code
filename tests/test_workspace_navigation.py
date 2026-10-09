"""Conversation navigation must reveal hidden sessions and retain history scroll."""

import shutil
import subprocess
from pathlib import Path

import pytest


def run_node(script: str) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace navigation coverage")
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_prompt_index_only_targets_rendered_user_messages():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/workspace.js", "utf8"));
vm.runInThisContext(fs.readFileSync("sona_code/web/static/workspace-navigation.js", "utf8"));
const user = (id, parts, extra = {}) => ({info: {id, role: "user", time: {created: 1000}}, parts, ...extra});
const messages = [
  user("question", [{type: "text", text: "请检查问题 😀"},
    {type: "text", synthetic: true, text: "Internal context"},
    {type: "text", text: "\n引用项目目录 @src。请按需查看此目录下的文件。"}]),
  user("internal", [{type: "text", synthetic: true, text: "Continue"}]),
  {info: {id: "reply", role: "assistant"}, parts: [{type: "text", text: "answer"}]},
  user("compact", [{type: "compaction"}]),
  user("skill", [{type: "text", synthetic: true, text: "Skill instructions"}],
    {skillUse: {name: "review", arguments: "检查代码"}}),
  user("image", [{type: "file", filename: "screen.png"}]),
  user("empty", []),
];
const rows = ["question", "skill", "image", "empty"].map(workspaceUserMessageId => ({workspaceUserMessageId}));
const entries = workspacePromptEntries(messages, rows);
assert.deepEqual(entries.map(entry => entry.id), ["question", "skill", "image", "empty"]);
assert.deepEqual(entries.map(entry => entry.summary), ["请检查问题 😀", "/review 检查代码", "screen.png", "消息内容暂不可用"]);
assert.equal(entries[0].node, rows[0]); assert.equal(entries[0].created, 1000);
// Refreshed rows replace targets even when message IDs and summaries are unchanged.
const replacement = {workspaceUserMessageId: "question"};
assert.equal(workspacePromptEntries(messages, [replacement])[0].node, replacement);
assert.equal(workspacePromptEntries(messages, []).length, 0);
''')


def test_locate_session_reveals_collapsed_filtered_and_truncated_sidebar():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const project = {id: "p", name: "Project"};
const sessions = Array.from({length: 15}, (_, i) => ({id: `s${i}`, title: `Session ${i}`}));
const state = {projectId: "p", sessionId: "s12", search: "unrelated", sessions: new Map([["p", sessions]]),
  collapsedProjects: new Set(["p"]), sessionLimits: new Map()};
function classes(initial = []) {const values = new Set(initial); return {
  add: value => values.add(value), remove: value => values.delete(value), has: value => values.has(value)};}
const root = {classList: classes(["side-collapsed"])};
const row = {isConnected: true, classList: classes(), getBoundingClientRect: () => ({top: 600, height: 30}),
  focus() {this.focused = true;}};
const sidebar = {scrollTop: 20, clientHeight: 400, getBoundingClientRect: () => ({top: 100})};
const search = {value: "unrelated"}, locate = {addEventListener() {}};
const sidebarRows = new Map(), notices = [], cleanups = [];
let renders = 0, mobile = false, loadResolve;
const context = vm.createContext({state, root, sidebarRows, setTimeout, clearTimeout,
  activeProject: () => project, activeSession: () => sessions[12], alive: () => true,
  sessionTitle: session => session.title, workspaceConversationKey: (p,s) => JSON.stringify([p,s]),
  window: {matchMedia: () => ({matches: mobile})}, localStorage: {setItem() {}},
  view: {querySelector: selector => selector === "#wsp-search" ? search : selector === ".wsp-side-list" ? sidebar : locate},
  updateSidebarButton() {}, toast: message => notices.push(message), addCleanup: fn => cleanups.push(fn),
  loadSessions: () => new Promise(resolve => {loadResolve = resolve;}),
  renderSidebar() {renders++; assert(!state.collapsedProjects.has("p"));
    assert(state.sessionLimits.get("p") >= 13); assert.equal(state.search, "");
    sidebarRows.set(JSON.stringify(["p", "s12"]), {button: row});},
});
vm.runInContext(source.slice(source.indexOf("  let locatingSession ="), source.indexOf("  function renderSidebar(")), context);
(async () => {
  await context.locateCurrentSession();
  assert.equal(state.sessionId, "s12"); assert.equal(renders, 1);
  assert.equal(search.value, ""); assert(!root.classList.has("side-collapsed"));
  assert(row.classList.has("wsp-thread-located")); assert(row.focused);
  assert.equal(sidebar.scrollTop, 335); assert.equal(notices.length, 0);
  mobile = true; await context.locateCurrentSession();
  assert(root.classList.has("show-side"));
  // Late list results cannot reveal the previously selected session after switching.
  state.sessions.clear(); const pending = context.locateCurrentSession();
  state.sessionId = "different"; loadResolve(); await pending;
  assert.equal(renders, 2); assert.equal(state.sessionId, "different");
  cleanups.forEach(fn => fn());
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_prompt_jump_keeps_near_bottom_history_pinned_during_refresh():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const scroll = {scrollTop: 440, scrollHeight: 1000, clientHeight: 500}; // Inside the 80px follow threshold.
const content = {children: [], childNodes: [], classList: {remove() {}}};
const state = {projectId: "p", sessionId: "s", tab: "chat"};
const frames = [], row = {};
const context = vm.createContext({state, scroll, content, composerDock: {},
  browsingHistory: true, followLatest: false, navigation: {render() {}},
  document: {activeElement: null}, view: {querySelector: () => ({})},
  trajectoryView: null, changePopover: null, changeTriggers: new Map(),
  renderTodoPanel() {}, renderStatsLine() {}, positionChangePopover() {}, changedFiles: () => [],
  renderMessages: target => target.append(row), workspaceSyncChildren: (target, rows) => {target.children = rows;},
  requestAnimationFrame: callback => frames.push(callback), alive: () => true,
});
vm.runInContext(source.slice(source.indexOf("  function renderMain("), source.indexOf("  async function loadSessions(")), context);
context.renderMain(); assert.equal(scroll.scrollTop, 440); assert.equal(frames.length, 0);
scroll.scrollHeight += 30; context.renderMain(); assert.equal(scroll.scrollTop, 440);
assert.equal(context.followLatest, false);
// Explicit return to latest restores following; deferred frames also honor a later jump.
context.browsingHistory = false; context.followLatest = true; scroll.scrollTop = 530;
context.renderMain(); assert.equal(scroll.scrollTop, 1030); assert.equal(frames.length, 1);
context.browsingHistory = true; context.followLatest = false; scroll.scrollTop = 70;
frames[0](); assert.equal(scroll.scrollTop, 70);
context.renderMain(true); assert.equal(context.browsingHistory, false); assert.equal(scroll.scrollTop, 1030);
''')
