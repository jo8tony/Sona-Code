"""Refresh latency, cancellation, event scope and bounded conversation caching."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_native_part_events_stream_and_survive_older_history_response():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace refresh coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const info = {id: "a", role: "assistant", sessionID: "s", parentID: "u"};
const part = {id: "reason", messageID: "a", sessionID: "s", type: "reasoning", text: "first", time: {start: 1}};
const requests = [], state = {projectId: "p", sessionId: "s", messages: [{info, parts: [part]}], tab: "chat", projectStatuses: new Map(), statuses: {}, sessionDetails: new Map()};
let renders = 0;
const context = vm.createContext({state, AbortController, selectedRefresh: null, creatingSessions: new Set(),
  lastSelectedRefresh: 0, queueUpdateVersion: 0, statusVersions: new Map(), messageVersion: 0,
  messageInfoVersions: new Map(), messagePartVersions: new Map(),
  alive: () => true, sessionPath: () => "session", detail: e => e.message,
  api(path) {return new Promise(resolve => requests.push({path, resolve}));},
  scheduleSelectedRender() {renders++;}, renderHeader() {}, renderSidebar() {}, observeProjectStatuses() {},
});
vm.runInContext(source, context);
vm.runInContext(source.slice(source.indexOf("  function applyMessageEvent("), source.indexOf("  function observeSessionStatus(")), context);
vm.runInContext(source.slice(source.indexOf("  async function refreshSelected("), source.indexOf("  function saveConversationView(")), context);
const event = (type, properties) => context.applyMessageEvent("p", {type, properties});
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  event("message.part.updated", {part: {...part, text: "first full"}, delta: " full"});
  assert.equal(state.messages[0].parts[0].text, "first full", "Authoritative text must not double optional deltas");
  const refresh = context.refreshSelected();
  event("message.part.delta", {sessionID: "s", messageID: "a", partID: "reason", field: "text", delta: " streamed"});
  event("message.part.delta", {sessionID: "other", messageID: "a", partID: "reason", field: "text", delta: " wrong"});
  requests[0].resolve([{info, parts: [{...part, text: "older"}, {id: "new", type: "text", text: "fresh fetched response"}]}]);
  await tick();
  assert.equal(state.messages[0].parts[0].text, "first full streamed");
  assert.equal(state.messages[0].parts[1].text, "fresh fetched response");
  for (const request of requests.slice(1)) request.resolve([]);
  await refresh;
  assert(renders >= 3);
  event("message.part.removed", {sessionID: "s", messageID: "a", partID: "reason"});
  assert.equal(state.messages[0].parts.length, 1);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


@pytest.mark.parametrize("part_type", ["text", "reasoning"])
def test_history_refresh_preserves_streamed_text_until_authoritative_completion(part_type: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace refresh coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const info = {id: "a", role: "assistant", sessionID: "s", parentID: "u", time: {created: 1}};
const part = {id: "stream", messageID: "a", sessionID: "s", type: "PART_TYPE", text: "", time: {start: 1}};
const tool = {id: "tool", type: "tool", state: {status: "running"}};
const requests = [], state = {projectId: "p", sessionId: "s", messages: [{info, parts: [{...part}, tool]}],
  tab: "chat", projectStatuses: new Map(), statuses: {}, sessionDetails: new Map()};
const context = vm.createContext({state, AbortController, selectedRefresh: null, creatingSessions: new Set(),
  lastSelectedRefresh: 0, queueUpdateVersion: 0, statusVersions: new Map(), messageVersion: 0,
  messageInfoVersions: new Map(), messagePartVersions: new Map(),
  alive: () => true, sessionPath: () => "session", detail: e => e.message,
  api(path) {return new Promise(resolve => requests.push({path, resolve}));},
  scheduleSelectedRender() {}, renderHeader() {}, renderSidebar() {}, observeProjectStatuses() {},
});
vm.runInContext(source, context);
vm.runInContext(source.slice(source.indexOf("  function applyMessageEvent("), source.indexOf("  function observeSessionStatus(")), context);
vm.runInContext(source.slice(source.indexOf("  async function refreshSelected("), source.indexOf("  function saveConversationView(")), context);
const event = (type, properties) => context.applyMessageEvent("p", {type, properties});
const delta = text => event("message.part.delta", {sessionID: "s", messageID: "a", partID: "stream", field: "text", delta: text});
const displayed = () => state.messages[0].parts[0].text;
const tick = () => new Promise(resolve => setImmediate(resolve));
const history = (text = "", extra = {}) => [{info, parts: [{...part, text, ...extra}]}];
const refreshHistory = async (messages, duringRequest = () => {}) => {
  const start = requests.length;
  const refresh = context.refreshSelected();
  duringRequest();
  requests[start].resolve(JSON.parse(JSON.stringify(messages)));
  await tick();
  // Messages start before the auxiliary requests, so resolve the latter afterwards.
  for (const request of requests.slice(start + 1)) request.resolve([]);
  await refresh;
};
(async () => {
  // V1 persists text-start, but deltas are event-only until text-end. A pause with
  // no event during the request must not let that empty snapshot erase the reply.
  delta("First line\nSecond line");
  await refreshHistory([{info, parts: [{...part}, {...tool, state: {status: "completed"}}]}]);
  assert.equal(displayed(), "First line\nSecond line", "History must retain deltas received before the request");
  assert.equal(state.messages[0].parts[1].state.status, "completed", "Other fetched parts must still update");
  delta("\nThird line");
  await refreshHistory(history());
  await refreshHistory(history("First line", {metadata: {fetched: true}}));
  assert.equal(displayed(), "First line\nSecond line\nThird line", "Repeated polls must neither truncate nor duplicate tokens");
  assert.equal(state.messages[0].parts[0].metadata.fetched, true);

  // Restoring a cached conversation clears event versions; its live text must
  // still survive the first refresh on returning to that conversation.
  context.messageInfoVersions.clear(); context.messagePartVersions.clear(); context.messageVersion++;
  await refreshHistory(history());
  assert.equal(displayed(), "First line\nSecond line\nThird line");

  // A missed completion event is recovered by history, including a final result
  // that is shorter or rewritten by the native text completion hook.
  await refreshHistory(history("Final", {time: {start: 1, end: 2}}));
  assert.equal(displayed(), "Final");
  event("message.part.updated", {part: {...part, text: "Next"}});
  delta(" reply");
  await refreshHistory([{info: {...info, time: {created: 1, completed: 3}}, parts: [{...part, text: "Done"}]}]);
  assert.equal(displayed(), "Done", "Completed messages are authoritative even without a part end timestamp");

  // A completion event racing an older HTTP snapshot must remain authoritative.
  event("message.updated", {info});
  event("message.part.updated", {part: {...part, text: "Partial"}});
  await refreshHistory(history(), () => {
    event("message.part.updated", {part: {...part, text: "Complete", time: {start: 1, end: 4}}});
    event("message.updated", {info: {...info, time: {created: 1, completed: 4}}});
  });
  assert.equal(displayed(), "Complete");
  assert.equal(state.messages[0].parts[0].time.end, 4);
  assert.equal(state.messages[0].info.time.completed, 4);

  // A fetched user edit must not be mistaken for streamed assistant text.
  state.messages = [{info: {...info, role: "user"}, parts: [{...part, type: "text", text: "User message"}]}];
  await refreshHistory([{info: {...info, role: "user"}, parts: [{...part, type: "text", text: "User"}]}]);
  assert.equal(displayed(), "User");

  // Missing parts/messages are authoritative deletions, even during a stream.
  state.messages = history("Partial");
  delta(" reply");
  await refreshHistory([{info, parts: []}]);
  assert.equal(state.messages[0].parts.length, 0);
  state.messages = history("Partial");
  delta(" reply");
  await refreshHistory([]);
  assert.equal(state.messages.length, 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''.replace("PART_TYPE", part_type)
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_user_prompt_survives_empty_history_and_events_without_blocking_edits_or_deletion():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace refresh coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const info = {id: "u", role: "user", sessionID: "s", time: {created: 1}};
const part = {id: "prompt", messageID: "u", sessionID: "s", type: "text", text: "用户原文"};
const file = {id: "file", type: "file", filename: "example.py", url: "file:///example.py"};
const requests = [], state = {projectId: "p", sessionId: "s", messages: [{info, parts: [{...part}, file]}],
  tab: "chat", projectStatuses: new Map(), statuses: {}, sessionDetails: new Map()};
const context = vm.createContext({state, AbortController, selectedRefresh: null, creatingSessions: new Set(),
  lastSelectedRefresh: 0, queueUpdateVersion: 0, statusVersions: new Map(), messageVersion: 0,
  messageInfoVersions: new Map(), messagePartVersions: new Map(),
  alive: () => true, sessionPath: () => "session", detail: e => e.message,
  api(path) {return new Promise(resolve => requests.push({path, resolve}));},
  scheduleSelectedRender() {}, renderHeader() {}, renderSidebar() {}, observeProjectStatuses() {},
});
vm.runInContext(source, context);
vm.runInContext(source.slice(source.indexOf("  function applyMessageEvent("), source.indexOf("  function observeSessionStatus(")), context);
vm.runInContext(source.slice(source.indexOf("  async function refreshSelected("), source.indexOf("  function saveConversationView(")), context);
const event = (type, properties) => context.applyMessageEvent("p", {type, properties});
const displayed = () => state.messages[0].parts.find(item => item.id === "prompt")?.text;
const history = parts => [{info, parts}];
const refreshHistory = async (messages, duringRequest = () => {}) => {
  const start = requests.length;
  const refresh = context.refreshSelected();
  duringRequest();
  requests[start].resolve(JSON.parse(JSON.stringify(messages)));
  await new Promise(resolve => setImmediate(resolve));
  for (const request of requests.slice(start + 1)) request.resolve([]);
  await refresh;
};
(async () => {
  // The full prompt arrives BEFORE the request, so version-based race protection
  // alone cannot keep it when a later snapshot omits its content.
  event("message.part.updated", {part});
  for (const parts of [undefined, [], [{...part, text: ""}], [{...part, text: "  \n"}], [file]]) {
    await refreshHistory(history(parts));
    assert.equal(displayed(), "用户原文");
    assert.equal(state.messages[0].parts.filter(item => item.id === "prompt").length, 1);
  }
  assert.equal(state.messages[0].parts.find(item => item.id === "file").filename, "example.py");
  context.messageInfoVersions.clear(); context.messagePartVersions.clear(); context.messageVersion++;
  await refreshHistory(history([]));
  assert.equal(displayed(), "用户原文", "Cached conversations must retain user text after event versions reset");

  event("message.part.updated", {part: {...part, text: "", metadata: {fetched: true}}});
  assert.equal(displayed(), "用户原文", "An empty full-text event must not erase the prompt");
  assert.equal(state.messages[0].parts[0].metadata.fetched, true);
  event("message.part.updated", {part: {...part, text: "修改后的原文"}});
  assert.equal(displayed(), "修改后的原文");
  await refreshHistory(history([{...part, text: "短"}]));
  assert.equal(displayed(), "短", "Nonempty user edits must remain authoritative");
  await refreshHistory(history([]), () => event("message.part.updated", {part: {...part, text: "更新"}}));
  assert.equal(displayed(), "更新", "An event during the request must also survive empty history");
  // A replacement part with new text must not duplicate the old prompt.
  await refreshHistory(history([{...part, id: "replacement", text: "替换"}]));
  assert.equal(state.messages[0].parts.length, 1);
  assert.equal(displayed(), undefined);

  state.messages = history([{...part}]);
  await refreshHistory(history([{...part}]), () =>
    event("message.part.removed", {sessionID: "s", messageID: "u", partID: "prompt"}));
  assert.equal(state.messages[0].parts.length, 0, "Explicit removal must defeat an older in-flight snapshot");
  await refreshHistory(history([]));
  assert.equal(state.messages[0].parts.length, 0, "Refresh must not resurrect explicitly removed text");
  state.messages = history([{...part}]);
  await refreshHistory([]);
  assert.equal(state.messages.length, 0, "Withdrawal and whole-message deletion must still work");
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


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
  messageVersion: 0, lastSelectedRefresh: 0, messageInfoVersions: new Map(), messagePartVersions: new Map(), creatingSessions: new Set(), statusVersions: new Map(), conversationViews: new Map(),
  alive: () => true, sessionPath: (p,s) => `${p}/${s}`, detail: e => e.message,
  api(path, options) { return new Promise((resolve, reject) => requests.push({path, options, resolve, reject})); },
  renderMain() {}, renderSidebar() {}, renderHeader() {},
  workspaceConversationKey: (p,s) => JSON.stringify([p,s]),
  content: {childNodes: []}, scroll: {scrollTop: 15}, scrollToLatestOnLoad: true,
  browsingHistory: false, followLatest: true,
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
  const deadline = setTimeout(() => {console.error("Refresh did not finish"); process.exit(1);}, 3000);
  const a = context.refreshSelected();
  assert.equal(request("p/a/diff"), undefined, "Auxiliary requests must leave connections for history and sidebar");
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
  const requestStart = requests.length;
  const again = context.refreshSelected();
  request("p/a/messages").resolve([{info, parts: [{type: "text", text: "fresh parts"}]}]);
  context.applyMessageEvent("p", {type: "message.updated", properties: {info: {...info, tokens: {output: 25}}}});
  await tick();
  assert.equal(state.messages[0].parts[0].text, "fresh parts", "Fresh metadata must not discard fetched parts");
  assert.equal(state.messages[0].info.tokens.output, 25);
  const oldDiff = requests.find(r => r.path === "p/a/diff");
  oldDiff.resolve([{file: "stale.txt"}]);
  await tick();
  assert.deepEqual(state.diffs, []);
  for (const r of requests.slice(requestStart)) if (!r.path.endsWith("/messages")) r.resolve([]);
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
  context.browsingHistory = true; context.followLatest = false;
  context.saveConversationView();
  state.projectId = "other"; context.restoreConversationView();
  assert.equal(state.messages.length, 0);
  assert.equal(context.browsingHistory, false); assert.equal(context.followLatest, true);
  state.projectId = "p"; context.restoreConversationView();
  assert.equal(state.messages[0].info.id, "saved");
  assert.equal(context.content.childNodes[0], row);
  assert.equal(context.scroll.scrollTop, 15);
  assert.equal(context.browsingHistory, true); assert.equal(context.followLatest, false);
  for (let i = 0; i < 10; i++) { state.sessionId = String(i); state.messagesLoaded = true; context.saveConversationView(); }
  assert.equal(context.conversationViews.size, 6);
  assert.equal(context.conversationViews.has(JSON.stringify(["p", "a"])), false);
  clearTimeout(deadline);
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
let refreshes = 0, sidebars = 0, todoRenders = 0;
const context = vm.createContext({
  events: null, eventProjectId: null, EventSource: class {},
  alive: () => true, scheduleRefresh: () => refreshes++, applyMessageEvent() {},
  scheduleSelectedRender: () => todoRenders++,
  state: {projectId: "p", sessionId: "a", statuses: {}, projectStatuses: new Map(), todos: [], todoVersion: 0},
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
emit("todo.updated", {sessionID: "b", todos: [{content: "unrelated", status: "completed"}]});
assert.equal(todoRenders, 0); assert.equal(refreshes, 2);
emit("todo.updated", {sessionID: "a", todos: [{content: "first step", status: "in_progress"}]});
assert.equal(todoRenders, 1); assert.equal(refreshes, 3);
assert.equal(context.state.todoVersion, 1);
assert.equal(context.state.todos[0].content, "first step");
'''
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_todo_preview_only_appears_for_current_chat_with_native_todos():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace todo coverage")
    script = r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const fields = Object.fromEntries(["#wsp-todo-trigger-count", "#wsp-todo-summary", "#wsp-todo-progress",
  "#wsp-todo-progress-fill"].map(key => [key, {textContent: "", style: {}}]));
const trigger = {hidden: true, classList: {toggle() {}}, setAttribute() {}};
const panel = {hidden: true};
const list = {scrollTop: 0, children: [], replaceCount: 0,
  replaceChildren(...items) {this.children = items; this.replaceCount++;}};
const state = {projectId: "p", sessionId: "a", tab: "chat", todos: []};
const dismissedTodoPanels = new Set();
const context = vm.createContext({state, dismissedTodoPanels, todoPanelTurns: new Map(), todoTrigger: trigger,
  todoPanel: panel, todoList: list, renderedTodoSignature: "",
  root: {classList: {toggle() {}}}, view: {querySelector: key => fields[key]},
  workspaceConversationKey: (p, s) => JSON.stringify([p, s]),
  el: (tag, attrs, ...children) => ({tag, ...attrs, children}),
});
vm.runInContext(source.slice(source.indexOf("  function renderTodoPanel("),
  source.indexOf("  function renderTasks(")), context);
context.renderTodoPanel();
assert.equal(trigger.hidden, true); assert.equal(panel.hidden, true);
state.todos = [
  {content: "设计", status: "completed"},
  {content: "实现", status: "in_progress"},
  {content: "检查", status: "pending"},
];
context.renderTodoPanel();
assert.equal(trigger.hidden, false); assert.equal(panel.hidden, false);
assert.equal(fields["#wsp-todo-trigger-count"].textContent, "1/3");
assert.equal(fields["#wsp-todo-progress"].textContent, "1 / 3");
assert.equal(list.children[1].children[1].children[1].text, "进行中");
context.renderTodoPanel();
assert.equal(list.replaceCount, 1, "Unchanged message renders should preserve todo scroll position");
dismissedTodoPanels.add(JSON.stringify(["p", "a"]));
context.renderTodoPanel();
assert.equal(panel.hidden, true); assert.equal(trigger.hidden, false);
dismissedTodoPanels.delete(JSON.stringify(["p", "a"]));
context.renderTodoPanel();
assert.equal(panel.hidden, false);
state.messages = [{info: {role: "user", id: "new-turn"}}];
context.renderTodoPanel();
assert.equal(panel.hidden, true, "A new user turn should collapse the previous progress panel");
assert.equal(trigger.hidden, false);
state.todos = []; context.renderTodoPanel();
state.todos = [{content: "new plan", status: "pending"}]; context.renderTodoPanel();
assert.equal(panel.hidden, true, "Clearing and refreshing todos must not reopen the panel");
dismissedTodoPanels.delete(JSON.stringify(["p", "a"])); context.renderTodoPanel();
assert.equal(panel.hidden, false, "The user can still reopen progress manually");
state.tab = "tasks"; context.renderTodoPanel();
assert.equal(panel.hidden, true); assert.equal(trigger.hidden, true);
state.tab = "chat"; state.todos = []; context.renderTodoPanel();
assert.equal(panel.hidden, true); assert.equal(trigger.hidden, true);
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
  timestamp: value => value, state: {projects: [{id: "p", name: "示例项目"}], projectId: "p", sessionId: "a", tab: "chat"},
  renderSidebar() {},
  workspaceNotifyAnswerComplete: body => {notices.push(body);},
});
vm.runInContext(source.slice(0, source.indexOf("function workspaceRoundDuration(")), context);
vm.runInContext(source.slice(source.indexOf("  function unreadIndicator("),
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
