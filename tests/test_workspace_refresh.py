"""Refresh latency, cancellation, event scope and bounded conversation caching."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_workspace_refresh_does_not_block_switches_or_discard_streamed_parts():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace refresh coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const requests = [], rendered = [];
const state = {projectId: "p", sessionId: "a", tab: "chat", messages: [],
  sessionDetails: new Map(), projectStatuses: new Map(), statuses: {}, permissions: [], questions: [],
  questionDrafts: new Map(), questionPages: new Map(), questionErrors: new Map(), diffs: [], todos: [], children: []};
const context = vm.createContext({state, AbortController, setTimeout, clearTimeout,
  selectedRefresh: null, refreshTimer: null, renderTimer: null, queueUpdateVersion: 0,
  messageVersion: 0, messageInfoVersions: new Map(), statusVersions: new Map(), conversationViews: new Map(),
  alive: () => true, sessionPath: (p,s) => `${p}/${s}`, detail: e => e.message,
  api(path, options) { return new Promise((resolve, reject) => requests.push({path, options, resolve, reject})); },
  renderMain() {}, renderSidebar() {}, renderHeader() {},
  workspaceConversationKey: (p,s) => JSON.stringify([p,s]),
  content: {childNodes: []}, scroll: {scrollTop: 15}, scrollToLatestOnLoad: true,
  workspaceSyncChildren: (node, children) => {node.childNodes = children;},
});
vm.runInContext(source.slice(source.indexOf("  function scheduleRefresh("), source.indexOf("  async function refreshWorkspaceStatuses(")), context);
context.scheduleSelectedRender = () => rendered.push(state.messages.map(m => m.info?.id));
vm.runInContext(source.slice(source.indexOf("  async function refreshSelected("), source.indexOf("  function selectProject(")), context);
vm.runInContext(source.slice(source.indexOf("  function applyMessageEvent("), source.indexOf("  function connectEvents(")), context);
const tick = () => new Promise(resolve => setImmediate(resolve));
const request = path => requests.findLast(r => r.path === path);
const info = {id: "reply", role: "assistant", sessionID: "a", tokens: {output: 1}};
(async () => {
  const a = context.refreshSelected();
  // A message can render while the diff request is still unresolved.
  request("p/a/messages").resolve([{info, parts: [{type: "text", text: "first"}]}]);
  await tick();
  assert.equal(state.messages[0].parts[0].text, "first");
  assert(rendered.length > 0);
  assert(context.selectedRefresh);
  context.cancelSelectedRefresh();
  assert(request("p/a/diff").options.signal.aborted);
  state.sessionId = "b";
  const b = context.refreshSelected();
  assert(request("p/b/messages"), "The new conversation must start before A's diff completes");
  request("p/b/messages").resolve([{info: {id: "b"}, parts: []}]);
  await tick();
  assert.equal(state.messages[0].info.id, "b");
  // Return to A before A's first request completes: identity guards must reject it.
  context.cancelSelectedRefresh(); state.sessionId = "a";
  const again = context.refreshSelected();
  const newRequests = requests.slice(-7);
  request("p/a/messages").resolve([{info, parts: [{type: "text", text: "fresh parts"}]}]);
  context.applyMessageEvent("p", {type: "message.updated", properties: {info: {...info, tokens: {output: 25}}}});
  await tick();
  assert.equal(state.messages[0].parts[0].text, "fresh parts", "Fresh metadata must not discard fetched parts");
  assert.equal(state.messages[0].info.tokens.output, 25);
  const oldDiff = requests.find(r => r.path === "p/a/diff");
  oldDiff.resolve([{file: "stale.txt"}]);
  await tick();
  assert.deepEqual(state.diffs, []);
  for (const r of newRequests) if (!r.path.endsWith("/messages")) r.resolve([]);
  await again;
  assert.equal(context.selectedRefresh, null);
  for (const r of requests) r.resolve([]);
  await Promise.all([a, b]);
  assert.equal(state.messages[0].info.tokens.output, 25);
  // Cancellation also stops deferred renders and refreshes.
  context.refreshTimer = setTimeout(() => {throw Error("stale refresh");}, 10);
  context.renderTimer = setTimeout(() => {throw Error("stale render");}, 10);
  context.cancelSelectedRefresh();
  // A bounded cache reuses actual DOM nodes and keeps equal IDs in different projects isolated.
  state.messagesLoaded = true; state.messages = [{info: {id: "saved"}}]; state.tab = "chat";
  const row = {saved: true}; context.content.childNodes = [row];
  context.saveConversationView();
  state.projectId = "other"; context.restoreConversationView();
  assert.equal(state.messages.length, 0);
  state.projectId = "p"; context.restoreConversationView();
  assert.equal(state.messages[0].info.id, "saved");
  assert.equal(context.content.childNodes[0], row);
  assert.equal(context.scroll.scrollTop, 15);
  for (let i = 0; i < 10; i++) { state.sessionId = String(i); state.messagesLoaded = true; context.saveConversationView(); }
  assert.equal(context.conversationViews.size, 6);
  assert.equal(context.conversationViews.has(JSON.stringify(["p", "a"])), false);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_workspace_events_refresh_only_the_selected_conversation():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace event coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
let refreshes = 0, sidebars = 0;
const context = vm.createContext({
  events: null, eventProjectId: null, EventSource: class {},
  alive: () => true, scheduleRefresh: () => refreshes++, applyMessageEvent() {},
  state: {projectId: "p", sessionId: "a", statuses: {}, projectStatuses: new Map()},
  statusVersions: new Map(), renderSidebar: () => sidebars++, renderHeader() {}, renderMain() {},
  observeSessionStatus() {},
  lastSessionListRefresh: Date.now(), activeProject: () => null, loadSessions() {}, loadModels() {},
});
vm.runInContext(source.slice(source.indexOf("  function connectEvents("), source.indexOf("  function scheduleRefresh(")), context);
context.connectEvents("p");
const emit = (type, properties = {}) => context.events.onmessage({data: JSON.stringify({type, properties})});
for (let i = 0; i < 500; i++) emit("message.part.updated", {part: {sessionID: "b"}});
emit("message.part.delta", {sessionID: "b"});
emit("server.heartbeat");
assert.equal(refreshes, 0);
emit("session.status", {sessionID: "b", status: {type: "busy"}});
assert.equal(refreshes, 0); assert.equal(sidebars, 1);
assert.equal(context.state.projectStatuses.get("p").b.type, "busy");
emit("message.part.updated", {part: {sessionID: "a"}});
assert.equal(refreshes, 1);
emit("permission.asked", {sessionID: "a"});
assert.equal(refreshes, 2);
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_completion_notification_requires_blur_finished_reply_and_empty_queue():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace notification coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const notices = [];
let focused = false, queue = {items: []}, answerError = null;
const key = JSON.stringify(["p", "a"]);
const context = vm.createContext({
  completionWatches: new Map(), workspaceConversationKey: (p, s) => JSON.stringify([p, s]),
  setTimeout: () => 1, clearTimeout() {}, Date,
  document: {hasFocus: () => focused}, alive: () => true,
  sessionPath: (p, s) => `${p}/${s}`,
  api(path) {
    if (path.endsWith("/status")) return Promise.resolve({});
    if (path.endsWith("/queue")) return Promise.resolve(queue);
    return Promise.resolve([{info: {role: "assistant", time: {completed: Date.now() + 1000}, error: answerError}}]);
  },
  timestamp: value => value, state: {projects: [{id: "p", name: "示例项目"}]},
  workspaceNotifyAnswerComplete: body => {notices.push(body);},
});
vm.runInContext(source.slice(source.indexOf("  function observeSessionStatus("),
  source.indexOf("  function connectEvents(")), context);
async function complete() {
  context.observeSessionStatus("p", "a", undefined, "busy");
  context.observeSessionStatus("p", "a", "busy", "idle");
  const watch = context.completionWatches.get(key);
  if (watch) await context.confirmAnswerComplete("p", "a", watch);
}
(async () => {
  await complete();
  assert.deepEqual(notices, ["示例项目的 AI 回复已完成"]);
  focused = true;
  await complete();
  assert.equal(notices.length, 1);
  focused = false; queue = {items: [{id: "pending"}]};
  await complete();
  assert.equal(notices.length, 1);
  queue = {items: []}; answerError = {name: "APIError"};
  await complete();
  assert.equal(notices.length, 1);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)
