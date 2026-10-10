"""Desktop quit waits for cleanup, and interrupted tools display a stopped state."""

from tests.test_workspace_completion import run_node


def test_electron_quit_waits_once_and_retries_after_cleanup_failure():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("electron/main.js", "utf8").replace(/\r\n/g, "\n");
const begin = source.indexOf('app.on("before-quit",');
const end = source.indexOf('\n}\n\nasync function stopBackend', begin);
let handler, resolve, reject, calls = 0, quits = 0, failures = 0, prevented = 0;
const context = vm.createContext({quitting: false, quitReady: false, shutdownPromise: undefined,
  app: {on(name, fn) {handler = fn;}, quit() {quits++;}},
  stopBackend() {calls++; return new Promise((yes, no) => {resolve = yes; reject = no;});},
  dialog: {showErrorBox() {failures++;}},
});
vm.runInContext(source.slice(begin, end), context);
const event = {preventDefault() {prevented++;}};
(async () => {
  handler(event); handler(event);
  assert.equal(calls, 1); assert.equal(quits, 0); assert.equal(prevented, 2);
  reject(new Error("blocked")); await context.shutdownPromise;
  assert.equal(quits, 0); assert.equal(failures, 1); assert.equal(context.quitting, false);
  handler(event); assert.equal(calls, 2); resolve(); await context.shutdownPromise;
  assert.equal(quits, 1); assert.equal(context.quitReady, true);
  handler(event); assert.equal(calls, 2); assert.equal(prevented, 3);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_stopped_tool_cards_and_rows_do_not_show_running_or_success():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("sona_code/web/static/workspace.js", "utf8");
function el(tag, attrs = {}, ...children) {
  return {tag, ...attrs, children, style: {setProperty() {}}, classList: {add() {}},
    append(...items) {this.children.push(...items);}, addEventListener() {}};
}
function texts(node) {return [node.text || "", ...(node.children || []).flatMap(texts)].join(" ");}
const context = vm.createContext({el, Date, state: {expandedTools: new Map()},
  skillForTool: () => null, workspaceIcon: () => el("icon"), toolDuration: () => "1s"});
vm.runInContext(source.slice(source.indexOf("  function toolPart("), source.indexOf("  function questionCard(")), context);
const stopped = {id: "part", tool: "bash", state: {status: "error", error: "任务已停止", metadata: {interrupted: true}}};
const card = context.toolPart(stopped), group = context.toolGroup([stopped]);
assert.equal(card.class, "wsp-tool error");
assert(texts(card).includes("已停止")); assert(texts(group).includes("已停止 · 1s"));
assert(!texts(card).includes("运行中")); assert(!texts(group).includes("运行中"));
assert(texts(context.toolPart({...stopped, state: {status: "error", error: "other"}})).includes("失败"));
''')
