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
  updateModelButton() {}, renderStatsLine() {}, renderModelPicker() {},
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
  now += 5 * 60 * 1000 + 1;
  const stale = context.loadModels("p");
  assert(state.providers[0].models.old, "Cached options must remain selectable while refreshing");
  requests[1].resolve(catalog("new")); await stale;
  assert(state.providers[0].models.new);
  const refresh = context.refreshSonaModels();
  assert.equal(requests[2].path, "models/sona/refresh"); assert.equal(button.disabled, true);
  requests[2].resolve({}); await tick();
  assert(requests[3].path.endsWith("/models")); requests[3].resolve(catalog("manual")); await refresh;
  assert.equal(button.disabled, false); assert(state.providers[0].models.manual);
  // A delayed response from another project may cache there, but must not change this picker.
  state.projectId = "other"; const other = context.loadModels("other");
  state.projectId = "p"; await context.loadModels("p");
  requests[4].resolve(catalog("other-model")); await other;
  assert(state.providers[0].models.manual); assert.equal(notices.at(-1), "模型已刷新");
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
