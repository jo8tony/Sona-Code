"""Regression coverage for IME, nested diff scrolling, model caching and ordering."""

import shutil
import subprocess
from pathlib import Path

import pytest


def run_node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for workspace interaction coverage")
    subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True)


def test_fuzzy_file_search_and_verified_unambiguous_message_links():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/workspace.js", "utf8"));
const files = ["src/deep/components/Page.html", "src/a/config.json", "src/b/config.json", "AGENTS.md", "docs/my notes.md"];
assert.deepEqual(workspaceFuzzyFiles(files, "pght"), ["src/deep/components/Page.html"]);
assert.equal(workspaceFuzzyFiles(files, "PAGE")[0], files[0]);
assert.deepEqual(workspaceFuzzyFiles(files, "不存在"), []);
const lookup = workspaceFileLookup(files);
assert.equal(lookup.get("config.json"), null);
assert.equal(lookup.get("Page.html"), files[0]);
assert.equal(lookup.get("src/a/config.json"), files[1]);
const text = "修改 Page.html、AGENTS.md，并查看 ./src/a/config.json:12。config.json 和 missing.py 不应链接。";
assert.deepEqual(workspaceFileTextMatches(text, lookup).map(m => m.path), [files[0], files[3], files[1]]);
assert.deepEqual(workspaceFileTextMatches("docs/my notes.md", lookup).map(m => m.path), [files[4]]);
assert.deepEqual(workspaceFileTextMatches("C:\\repo\\src\\deep\\components\\Page.html", lookup, "C:/repo").map(m => m.path), [files[0]]);
assert.deepEqual(workspaceFileTextMatches("https://site.test/Page.html /other/Page.html", lookup), []);
assert.deepEqual(workspaceFileTextMatches("NotPage.html", lookup), []);
assert.deepEqual(workspaceFileTextMatches("请修改Page.html文件", lookup).map(m => m.text), ["Page.html"]);
''')


def test_streaming_reasoning_preserves_details_text_node_and_user_scroll():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/workspace.js", "utf8"));
global.el = (tag, props, ...children) => ({tag, ...props, children, textContent: props?.text || "", open: false,
  firstChild: tag === "div" ? {data: ""} : null, scrollTop: 0, clientHeight: 140, scrollHeight: 500,
  querySelector(selector) {return selector === "summary" ? this.children[0] : this.children[1];},
  addEventListener(type, fn) {this.toggle = fn;}});
const expanded = new Map();
const node = workspaceReasoningNode(null, "reason", "first", expanded, true);
node.open = true; node.toggle();
const cached = {querySelectorAll: () => [node]};
const body = node.children[1], textNode = body.firstChild;
body.scrollTop = 50;
assert.equal(workspaceReasoningNode(cached, "reason", "first\nsecond", expanded, true), node);
assert.equal(node.open, true); assert.equal(body.firstChild, textNode);
assert.equal(textNode.data, "first\nsecond"); assert.equal(body.scrollTop, 50);
body.scrollTop = 360;
workspaceReasoningNode(cached, "reason", "third", expanded, false);
assert.equal(body.scrollTop, 500); assert.equal(node.children[0].textContent, "思考过程");
node.open = false; node.toggle(); assert.equal(expanded.get("reason"), false);
''')


def test_reasoning_typewriter_batches_frames_catches_up_and_respects_collapse():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/workspace.js", "utf8"));
let next = 0; const frames = new Map();
global.requestAnimationFrame = callback => {frames.set(++next, callback); return next;};
global.cancelAnimationFrame = id => frames.delete(id);
const frame = () => {const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback());};
global.el = (tag, props, ...children) => ({tag, ...props, children, textContent: props?.text || "", open: false,
  isConnected: true, firstChild: tag === "div" ? {data: ""} : null, scrollTop: 0, clientHeight: 80, scrollHeight: 80,
  querySelector(selector) {return selector === "summary" ? this.children[0] : this.children[1];},
  addEventListener(type, fn) {this.toggle = fn;}});
const expanded = new Map(), text = "思考中文😀内容".repeat(200);
const node = workspaceReasoningNode(null, "live", text, expanded, true);
assert.equal(node.open, true, "Streaming reasoning should be visible by default");
const body = node.children[1], textNode = body.firstChild, cached = {querySelectorAll: () => [node]};
assert.equal(frames.size, 1); frame();
assert(textNode.data.length > 1 && textNode.data.length < text.length);
assert(!/[\uD800-\uDBFF]$/.test(textNode.data), "Frames cannot split an emoji");
workspaceReasoningNode(cached, "live", text + "新增内容", expanded, true);
assert.equal(frames.size, 1, "SSE updates must share a single pending frame");
for (let i = 0; i < 40 && frames.size; i++) frame();
assert.equal(textNode.data, text + "新增内容");
assert.equal(body.firstChild, textNode);
body.scrollTop = 10; body.clientHeight = 80; body.scrollHeight = 400;
workspaceReasoningNode(cached, "live", text + "新增内容继续", expanded, true); frame();
assert.equal(body.scrollTop, 10, "Reading older reasoning must not force scrolling");
node.open = false; node.toggle();
workspaceReasoningNode(cached, "live", text + "全部内容\n\n   ", expanded, true);
assert.equal(node.open, false); assert.equal(textNode.data, text + "全部内容"); assert.equal(frames.size, 0);
workspaceReasoningNode(cached, "live", "最终内容", expanded, false);
assert.equal(textNode.data, "最终内容"); assert.equal(node.children[0].textContent, "思考过程");
const historical = workspaceReasoningNode(null, "history", "过去的思考", expanded, false);
assert.equal(historical.open, false);
workspaceReasoningNode({querySelectorAll: () => [historical]}, "history", "刚开始生成", expanded, true);
assert.equal(historical.open, true, "A part arriving before the busy status must open when streaming starts");
''')


def test_waiting_reasoning_panel_becomes_live_without_losing_toggle_or_text_node():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/workspace.js", "utf8"));
global.el = (tag, props, ...children) => ({tag, ...props, children, textContent: props?.text || "", open: false,
  firstChild: tag === "div" ? {data: ""} : null, scrollTop: 0, clientHeight: 80, scrollHeight: 80,
  querySelector(selector) {return selector === "summary" ? this.children[0] : this.children[1];},
  addEventListener(type, fn) {this.toggle = fn;}});
const expanded = new Map(), pendingKey = "pending:u";
const node = workspaceReasoningNode(null, pendingKey, "", expanded, true, null, true);
const body = node.children[1], textNode = body.firstChild;
assert.equal(node.open, true);
assert.equal(node.children[0].textContent, "思考过程 · 等待输出");
assert.equal(textNode.data, "等待模型返回思考内容…");
const cached = {querySelectorAll: () => [node]};
node.open = false; node.toggle();
assert.equal(workspaceReasoningNode(cached, "reason:1", "正在检查项目", expanded, true, pendingKey), node);
assert.equal(node.open, false, "The user's collapse must survive the first native reasoning part");
assert.equal(body.firstChild, textNode); assert.equal(textNode.data, "正在检查项目");
assert.equal(node.children[0].textContent, "思考过程 · 生成中");
assert.equal(expanded.get("reason:1"), false); assert.equal(expanded.has(pendingKey), false);
node.open = true; node.toggle();
assert.equal(expanded.get("reason:1"), true, "Toggle handlers must use the adopted native key");
workspaceReasoningNode(cached, "reason:1", "正在检查项目，继续分析", expanded, true);
assert.equal(textNode.data, "正在检查项目，继续分析");
workspaceReasoningNode(cached, "reason:1", "完整思考", expanded, false);
assert.equal(node.open, true); assert.equal(textNode.data, "完整思考");
assert.equal(node.children[0].textContent, "思考过程");
// Providers that return their first reasoning part only at completion still
// replace the pending panel, preserving the user's expanded state.
const delayed = workspaceReasoningNode(null, "pending:delayed", "", expanded, true, null, true);
assert.equal(workspaceReasoningNode({querySelectorAll: () => [delayed]}, "reason:delayed", "完成后的内容",
  expanded, false, "pending:delayed"), delayed);
assert.equal(delayed.children[1].firstChild.data, "完成后的内容");
''')


def test_new_session_opens_immediately_migrates_draft_and_respects_navigation():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
vm.runInThisContext(source);
const requests = [], selected = [], notices = [];
global.creatingSessions = new Set(); global.conversationViews = new Map();
global.pendingImages = new Map(); global.creationDraftDestinations = new Map();
global.state = {projectId: "p", sessionId: "old", sessions: new Map(), sessionDetails: new Map(),
  attachments: [], fileReferences: [], pendingImageBytes: 0, pendingImageCount: 0};
let editor = "previous task", renders = 0;
global.alive = () => true;
global.saveDraft = () => workspaceWriteDraft(workspaceConversationKey(state.projectId, state.sessionId), {text: editor, attachments: [...state.attachments]});
global.selectSession = (projectId, sessionId) => {saveDraft(); state.projectId = projectId; state.sessionId = sessionId; editor = ""; selected.push(sessionId);};
global.applyLatestModel = global.persistWorkspaceSelection = global.refreshSelected = () => {};
global.renderHeader = global.renderMain = global.renderSidebar = global.renderAttachments = () => {renders++;};
global.input = {focus() {}};
global.toast = msg => notices.push(msg); global.detail = error => error.message;
global.api = () => new Promise((resolve, reject) => requests.push({resolve, reject}));
vm.runInThisContext(source.slice(source.indexOf("  function migrateCreationDraft("), source.indexOf("  async function ensureSessionForSend(")));
vm.runInThisContext(source.slice(source.indexOf("  async function addImageFiles("), source.indexOf("  function detail(")));
let imageResolved; global.readImage = () => new Promise(resolve => {imageResolved = resolve;});
(async () => {
  const first = createSession("p"), placeholder = state.sessionId;
  assert(creatingSessions.has(placeholder)); assert(renders > 0);
  assert.equal(state.messagesLoaded, true); assert.equal(editor, "");
  assert.equal(workspaceReadDraft(workspaceConversationKey("p", "old")).text, "previous task");
  editor = "draft typed during native startup";
  const image = addImageFiles([{name: "during-startup.png", type: "image/png", size: 5}]);
  requests[0].resolve({id: "native-1"}); await first;
  assert.equal(state.sessionId, "native-1"); assert.equal(editor, "draft typed during native startup");
  assert.equal(selected.length, 1, "Native creation must not clear or refocus the editor");
  assert.equal(workspaceReadDraft(workspaceConversationKey("p", "native-1")).text, editor);
  assert.equal(pendingImages.get(workspaceConversationKey("p", "native-1")).count, 1);
  imageResolved("data:image/png;base64,AAAA"); await image;
  assert.equal(state.attachments[0].filename, "during-startup.png");
  assert.equal(workspaceReadDraft(workspaceConversationKey("p", "native-1")).attachments.length, 1);
  assert.equal(creationDraftDestinations.size, 0);
  assert.equal(creatingSessions.size, 0);
  const second = createSession("p"), pending = state.sessionId;
  editor = "draft before leaving"; saveDraft();
  state.projectId = "other"; state.sessionId = "other-session"; editor = "other draft";
  requests[1].resolve({id: "native-2"}); await second;
  assert.equal(state.projectId, "other"); assert.equal(state.sessionId, "other-session"); assert.equal(editor, "other draft");
  assert.equal(workspaceReadDraft(workspaceConversationKey("p", "native-2")).text, "draft before leaving");
  assert.equal(workspaceReadDraft(workspaceConversationKey("p", pending)), undefined);
  const failed = createSession("p"); editor = "preserve failed creation draft";
  requests[2].reject(Error("native unavailable")); await failed;
  assert.equal(state.sessionId, null); assert.equal(editor, "preserve failed creation draft");
  assert.equal(workspaceReadDraft(workspaceConversationKey("p", null)).text, editor);
  assert.equal(creatingSessions.size, 0); assert.equal(notices.length, 1);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_question_refresh_preserves_live_input_and_custom_reply():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const content = {children: []};
const state = {projectId: "p", sessionId: "s", questionDrafts: new Map(), questionPages: new Map(), questionErrors: new Map()};
const replies = [];
const context = vm.createContext({content, state, el(tag, attrs, ...children) {
  return {tag, ...attrs, children: children.filter(Boolean), append(...nodes) {this.children.push(...nodes);}};
}, api: async (path, options) => replies.push(options.body), refreshSelected: async () => {}, detail: e => e.message,
  renderMain() {throw Error("Typing must not rebuild the question card");}});
vm.runInContext(source.slice(source.indexOf("  function questionCard("), source.indexOf("  function compactionCard(")), context);
const request = {id: "q", questions: [{question: "选择方案", options: [{label: "A"}, {label: "B"}]}]};
const find = (node, test) => test(node) ? node : (node.children || []).map(child => find(child, test)).find(Boolean);
(async () => {
  state.questionErrors.set("q", "请先回答每个问题");
  const card = context.questionCard(request); content.children = [card];
  const input = find(card, node => node.class === "wsp-question-custom");
  // An uncommitted Chinese composition and selection must stay on the same input.
  input.value = "自定义中文回答"; input.selectionStart = 2; input.composing = true;
  input.oninput({target: input, isComposing: true});
  for (let i = 0; i < 10; i++) assert.equal(context.questionCard(JSON.parse(JSON.stringify(request))), card);
  assert.equal(find(card, node => node.class === "wsp-question-custom"), input);
  assert.equal(input.selectionStart, 2); assert.equal(input.composing, true);
  const submit = find(card, node => node.text === "提交回答");
  await submit.onclick();
  assert.equal(JSON.stringify(replies), JSON.stringify([{answers: [["自定义中文回答"]]}]));
  // Different questions/pages must still update rather than reuse stale handlers.
  const changed = context.questionCard({...request, questions: [{question: "另一个问题"}]});
  assert.notEqual(changed, card);
  state.questionDrafts.clear();
  assert.notEqual(context.questionCard(request), card, "Restored cached cards must not retain handlers for discarded drafts");
})();
''')


def test_diff_refresh_keeps_inner_and_outer_scroll():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
vm.runInThisContext(source);
const body = () => ({scrollTop: 0, scrollLeft: 0});
const file = () => ({dataset: {changeFile: "large.py"}, body: body(), querySelector() {return this.body;}});
global.content = {children: [], classList: {remove() {}},
  querySelectorAll(selector) {return selector === ".wsp-diff-file" ? this.children : [];},
  get firstChild() {return this.children[0];}, replaceChildren() {throw Error("Diff content must not be cleared");}};
global.scroll = {scrollTop: 300, scrollHeight: 2000, clientHeight: 500};
global.state = {tab: "changes", sessionId: "s", projectId: "p", selectedChange: null};
global.composerDock = {}; global.document = {activeElement: null};
global.view = {querySelector: () => ({})}; global.changeTriggers = new Map();
global.trajectoryView = null; global.changePopover = null; global.followLatest = false;
global.browsingHistory = false; global.navigation = {render() {}};
global.changesView = null; global.changesSignature = "";
global.renderTodoPanel = global.renderStatsLine = global.positionChangePopover = () => {};
global.workspaceSyncChildren = (container, nodes) => {container.children = nodes; nodes.forEach(node => node.parentNode = container);};
let revision = 1, renders = 0;
global.changedFiles = () => [{file: "large.py", revision}];
global.renderChanges = target => {renders++; target.append(file());};
vm.runInThisContext(source.slice(source.indexOf("  function renderMain("), source.indexOf("  async function loadSessions(")));
renderMain();
const original = content.firstChild;
original.body.scrollTop = 1700; original.body.scrollLeft = 150;
for (let i = 0; i < 10; i++) renderMain();
assert.equal(renders, 1); assert.equal(content.firstChild, original);
assert.equal(original.body.scrollTop, 1700); assert.equal(scroll.scrollTop, 300);
revision++; renderMain();
assert.equal(renders, 2); assert.equal(content.firstChild.body.scrollTop, 1700);
assert.equal(content.firstChild.body.scrollLeft, 150); assert.equal(scroll.scrollTop, 300);
''')


def test_model_cache_coalesces_requests_refreshes_and_rejects_late_selection():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
let now = 1000; const requests = [], notices = [];
const state = {projectId: "p", providers: [], chosenModels: new Map(), chosenVariants: new Map()};
const button = {disabled: false, setAttribute() {}, removeAttribute() {}};
const context = vm.createContext({state, modelLoadVersion: 0, alive: () => true,
  Date: class extends Date {static now() {return now;}},
  view: {querySelector: () => button}, modelPicker: {hidden: true},
  updateModelButton() {}, renderStatsLine() {}, renderModelPicker() {}, refreshSidebarAccount() {},
  detail: e => e.message, toast: msg => notices.push(msg), localStorage: {getItem() {}, removeItem() {}},
  api(path, options) {return new Promise((resolve, reject) => requests.push({path, options, resolve, reject}));},
});
vm.runInContext(source, context);
vm.runInContext(source.slice(source.indexOf("  async function loadModels("), source.indexOf("  async function loadAgents(")), context);
const catalog = id => ({source: "sona", providers: [{id: "site", models: {[id]: {}}}]});
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const a = context.loadModels("p"), b = context.loadModels("p");
  assert.equal(requests.length, 1, "Concurrent opens should share one request");
  requests[0].resolve(catalog("old")); await Promise.all([a, b]);
  await context.loadModels("p"); assert.equal(requests.length, 1);
  now += 365 * 24 * 60 * 60 * 1000;
  await context.loadModels("p");
  assert.equal(requests.length, 1, "Sona subscriptions must have no automatic expiry");
  assert(state.providers[0].models.old);
  const refresh = context.refreshSonaModels();
  assert.equal(requests[1].path, "models/sona/refresh"); assert.equal(button.disabled, true);
  requests[1].resolve({}); await tick();
  assert(requests[2].path.endsWith("/models")); requests[2].resolve(catalog("manual")); await refresh;
  assert.equal(button.disabled, false); assert(state.providers[0].models.manual);
  // A delayed response from another project may cache there, but must not change this picker.
  state.projectId = "other"; const other = context.loadModels("other");
  state.projectId = "p"; await context.loadModels("p");
  requests[3].resolve(catalog("other-model")); await other;
  assert(state.providers[0].models.manual); assert.equal(notices.at(-1), "模型已刷新");
  // Expired website login must invalidate subscriptions and expose login state.
  const expired = context.refreshSonaModels();
  requests[4].reject(Object.assign(new Error("expired"), {status: 401})); await tick();
  requests[5].resolve({source: "sona", sona_connected: false, providers: []}); await expired;
  assert.equal(state.sonaConnected, false); assert.equal(state.providers.length, 0);
  assert(state.modelLoadError.includes("登录"));
  // Native providers keep their existing periodic cache refresh.
  state.projectId = "native"; const native = context.loadModels("native");
  requests[6].resolve({source: "native", providers: []}); await native;
  now += 5 * 60 * 1000 + 1;
  const nativeRefresh = context.loadModels("native"); assert.equal(requests.length, 8);
  requests[7].resolve({source: "native", providers: []}); await nativeRefresh;
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_manual_order_survives_native_recency_order_and_new_items():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
const saved = new Map(); global.localStorage = {getItem: key => saved.get(key)};
vm.runInThisContext(source);
let items = [{id: "a"}, {id: "b"}, {id: "c"}];
items = workspaceMoveItem(items, "c", "a", false);
assert.deepEqual(items.map(item => item.id), ["c", "a", "b"]);
items = workspaceMoveItem(items, "c", "b", true);
assert.deepEqual(items.map(item => item.id), ["a", "b", "c"]);
saved.set("order", JSON.stringify(["b", "a", "deleted"]));
assert.deepEqual(workspaceOrderItems([{id: "c"}, {id: "a"}, {id: "b"}, {id: "d"}], "order").map(i => i.id), ["b", "a", "c", "d"]);
assert.equal(workspaceMoveItem(items, "unknown", "a", false), items);
saved.set("order", "bad json"); assert.deepEqual(workspaceOrderItems(items, "order"), items);
''')
