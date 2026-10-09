"""Browser handoff cancellation, timeout and retry behavior."""

from tests.test_workspace_interactions import run_node


def test_chat_logout_relogin_and_late_catalog_response():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/models.js", "utf8"));
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
vm.runInThisContext(source);
const oldProvider = {id: "old-provider", source: "sona", models: {old: {name: "Old model", source: "sona"}}};
const newProvider = {id: "new-provider", source: "sona", models: {fresh: {name: "New model", source: "sona"}}};
const catalogue = connected => ({source: "sona", sona_environment: "prod", sona_connected: connected,
  providers: connected ? [newProvider] : [], default_model: null});
global.state = {projectId: null, modelSource: "sona", sonaEnvironment: "prod", sonaConnected: true,
  providers: [oldProvider], connectedProviders: new Set(), chosenModels: new Map([[null, "old-provider\u0000old"]]),
  chosenVariants: new Map(), modelLoadError: ""};
const node = props => ({...props, textContent: props?.text || "", children: [], dataset: {
  sonaLoginLabel: props?.["data-sona-login-label"]}, append(...children) {this.children.push(...children);},
  replaceChildren(...children) {this.children = children;}, setAttribute(k, v) {this[k] = v;}, removeAttribute(k) {delete this[k];}});
global.el = (tag, props, ...children) => {const result = node(props); result.append(...children); return result;};
global.modelList = node({}); global.modelSearch = {value: ""}; global.modelPicker = {hidden: false}; global.modelButton = {};
const logout = node({text: "退出 Sona"}), refresh = node({});
global.view = {querySelector: id => id === "#wsp-sona-logout" ? logout : refresh};
const descendants = item => [item, ...(item.children || []).flatMap(descendants)];
global.document = {querySelectorAll: () => descendants(modelList).filter(n => n.dataset?.sonaLoginLabel)};
global.modelLoadVersion = 0; const notices = [];
global.toast = text => notices.push(text); global.detail = error => error.detail || error.message;
global.localStorage = {removeItem() {}};
global.alive = () => true; global.positionPicker = global.updateModelButton = global.renderStatsLine = () => {};
global.window = {__TAURI__: {core: {invoke: async () => {}}}, dispatchEvent: () => {void loadModels(null);}};
let busy = true, connected = true, delayRead = false, releaseOld;
global.api = async (path, options) => {
  if (path === "models/sona/logout") {
    if (busy) throw Object.assign(new Error("任务运行中"), {status: 409});
    connected = false; return {connected: false};
  }
  if (path === "workspace/models") {
    if (delayRead) return new Promise(resolve => {releaseOld = () => resolve({...catalogue(true), providers: [oldProvider]});});
    return catalogue(connected);
  }
  if (path.endsWith("/start")) return {state: "new-login", url: "https://site.test"};
  if (path === "models/source") return {source: "sona", environment: "prod", connected};
  connected = true; return {status: "completed"};
};
vm.runInThisContext(source.slice(source.indexOf("  function renderModelPicker("), source.indexOf("  function openModelPicker(")));
vm.runInThisContext(source.slice(source.indexOf("  async function loadModels("), source.indexOf("  async function refreshSonaModels(")));
vm.runInThisContext(source.slice(source.indexOf("  async function logoutSona("), source.indexOf("  async function loadAgents(")));
(async () => {
  renderModelPicker(); assert.equal(logout.hidden, false);
  await logoutSona(); assert.equal(state.sonaConnected, true); assert.equal(logout.disabled, false);
  assert(notices.some(text => text.includes("退出 Sona 失败")));
  busy = false; delayRead = true;
  const stale = loadModels(null); await new Promise(setImmediate); delayRead = false;
  await logoutSona(); await new Promise(setImmediate);
  assert.equal(logout.hidden, true); assert.equal(refresh.hidden, true);
  assert.equal(state.sonaConnected, false); assert.deepEqual(state.providers, []);
  assert.equal(state.chosenModels.get(null), "");
  releaseOld(); await stale;
  assert.equal(state.sonaConnected, false, "A late read must not restore the old account");
  assert.deepEqual(state.providers, []);
  const login = descendants(modelList).find(n => n.dataset?.sonaLoginLabel === "登录 Sona");
  assert(login, "Chat must offer login immediately after logout");
  await login.onclick(); await new Promise(setImmediate);
  assert.equal(state.sonaConnected, true); assert.equal(logout.hidden, false);
  assert.deepEqual(state.providers.map(p => p.id), ["new-provider"]);
  assert(!notices.some(text => text.includes("原模型已不可用")));
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_cancel_unresponsive_native_opener_and_retry_from_new_button():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/models.js", "utf8"));
const buttons = [], calls = [], events = [], errors = [];
global.document = {querySelectorAll: () => buttons};
global.el = (tag, props) => {
  const node = {...props, dataset: {sonaLoginLabel: props["data-sona-login-label"]},
    setAttribute(k, v) {this[k] = v;}, removeAttribute(k) {delete this[k];}};
  buttons.push(node); return node;
};
let finishOldOpen, generation = 0;
global.window = {__TAURI__: {core: {invoke: () => new Promise(resolve => {finishOldOpen = resolve;})}},
  dispatchEvent: event => events.push(event.type)};
global.api = async (path, options) => {
  calls.push([path, options?.method]);
  if (path.endsWith("/start")) return {state: "attempt-" + ++generation, url: "https://site.test"};
  if (path === "models/source") return {source: "sona", environment: "prod", connected: true};
  if (options?.method === "DELETE") return {ok: true};
  return {status: generation === 1 ? "pending" : "completed"};
};
let completed = 0;
(async () => {
  const first = sonaLoginButton("prod", "btn", "登录", () => completed++, error => {if (error) errors.push(error);});
  const waiting = first.onclick();
  await new Promise(setImmediate);
  assert.equal(first.textContent, "取消登录"); assert(!first.disabled);
  // Navigating away and back creates a new button which can still cancel.
  const replacement = sonaLoginButton("prod", "btn", "登录", () => completed++, () => {});
  assert.equal(replacement.textContent, "取消登录");
  await replacement.onclick(); await waiting;
  assert.equal(sonaLoginPending, null); assert.equal(replacement.textContent, "登录");
  assert.equal(completed, 0); assert.deepEqual(errors, []);
  assert(calls.some(([path, method]) => path.endsWith("attempt-1") && method === "DELETE"));
  finishOldOpen();
  window.__TAURI__.core.invoke = async () => {};
  await replacement.onclick();
  assert.equal(completed, 1); assert.equal(sonaLoginPending, null);
  assert.deepEqual(events, ["sona-models-changed"]);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_browser_open_and_callback_failures_allow_immediate_retry():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/models.js", "utf8"));
global.document = {querySelectorAll: () => []};
let mode = "opener", generation = 0, callbacks = 0;
global.window = {__TAURI__: {core: {invoke: async () => {if (mode === "opener") throw "打开失败";}}},
  dispatchEvent: () => callbacks++};
global.api = async (path, options) => {
  if (path.endsWith("/start")) return {state: "attempt-" + ++generation, url: "https://site.test"};
  if (path === "models/source") return {source: "sona", environment: "prod", connected: true};
  if (options?.method === "DELETE") return {ok: true};
  return mode === "callback" ? {status: "error", detail: "无权访问该空间"} : {status: "pending"};
};
(async () => {
  await assert.rejects(startSonaLogin("prod"), /打开失败/);
  assert.equal(sonaLoginPending, null);
  mode = "callback";
  await assert.rejects(startSonaLogin("prod"), /无权访问该空间/);
  assert.equal(sonaLoginPending, null); assert.equal(callbacks, 0);
  window.__TAURI__ = null; window.open = () => null;
  await assert.rejects(startSonaLogin("prod"), /阻止了登录窗口/);
  assert.equal(sonaLoginPending, null);
  const tab = {opener: {}, closed: true, location: {replace() {}}, close() {this.closed = true;}};
  mode = "closed"; window.open = () => tab;
  await assert.rejects(startSonaLogin("prod"), /登录窗口已关闭/);
  assert.equal(sonaLoginPending, null); assert.equal(tab.opener, null);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_native_opener_timeout_does_not_leave_login_waiting():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
vm.runInThisContext(fs.readFileSync("sona_code/web/static/models.js", "utf8"));
const realTimeout = setTimeout;
global.setTimeout = (callback, duration) => realTimeout(callback, duration === 15000 ? 5 : 100);
global.document = {querySelectorAll: () => []};
global.window = {__TAURI__: {core: {invoke: () => new Promise(() => {})}}, dispatchEvent() {}};
let cancelled = false;
global.api = async (path, options) => {
  if (path.endsWith("/start")) return {state: "attempt", url: "https://site.test"};
  if (options?.method === "DELETE") {cancelled = true; return {ok: true};}
  return {status: "pending"};
};
(async () => {
  await assert.rejects(startSonaLogin("prod"), /打开浏览器超时/);
  assert.equal(sonaLoginPending, null); assert(cancelled);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')
