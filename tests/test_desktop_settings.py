"""Desktop startup choices persist and are restored on subsequent launches."""

from tests.test_workspace_completion import run_node


def test_electron_autostart_default_toggle_restart_and_failure_rollback():
    run_node(r'''
const fs = require("node:fs"), os = require("node:os"), path = require("node:path"), assert = require("node:assert/strict");
const {createDesktopSettings} = require("./electron/desktop-settings.cjs");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "sona-desktop-test-"));
let enabled = false;
const calls = [], app = {isPackaged: true,
  getLoginItemSettings() {return {openAtLogin: enabled, executableWillLaunchAtLogin: enabled};},
  setLoginItemSettings(settings) {calls.push(settings); enabled = settings.openAtLogin;},
};
try {
  const settings = createDesktopSettings(app, root); settings.initialize();
  assert.equal(enabled, true); assert.equal(settings.getAutostart(), true);
  assert.equal(calls[0].path, process.execPath); assert.deepEqual(calls[0].args, []);
  assert.equal(settings.setAutostart(false), false);
  createDesktopSettings(app, root).initialize(); assert.equal(enabled, false, "Restart must preserve opt-out");
  assert.throws(() => settings.setAutostart("true")); assert.equal(enabled, false);
  settings.setAutostart(true);
  const write = fs.writeFileSync;
  fs.writeFileSync = () => {throw Error("Disk failure");};
  try {assert.throws(() => settings.setAutostart(false), /Disk failure/);}
  finally {fs.writeFileSync = write;}
  assert.equal(enabled, true, "Failed persistence restores the previous system setting");
  fs.writeFileSync(path.join(root, "desktop-settings.json"), "broken");
  assert.throws(() => createDesktopSettings(app, root).initialize()); assert.equal(enabled, true);
  app.isPackaged = false; const count = calls.length; settings.initialize(); assert.equal(calls.length, count);
  assert.throws(() => settings.getAutostart(), /安装版/);
} finally {
  assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
  fs.rmSync(root, {recursive: true, force: true});
}
''')


def test_application_settings_toggle_reads_system_state_and_reverts_on_error():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const calls = [], notices = [];
let fail = false;
function el(tag, attrs, ...children) {return {tag, ...attrs, children, handlers: {},
  addEventListener(name, handler) {this.handlers[name] = handler;}};}
const view = {replaceChildren(card) {this.card = card;}};
const context = vm.createContext({el, toast: message => notices.push(message), window: {__TAURI__: {core: {
  async invoke(command, args) {calls.push({command, args}); if (fail) throw Error("System failure");
    return command === "get_autostart" ? false : args.enabled;},
}}}});
vm.runInContext(fs.readFileSync("sona_code/web/static/settings.js", "utf8"), context);
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  context.renderApplicationSettings(view); await tick();
  const field = view.card.children[1], input = field.children[0].children[0], hint = field.children[1];
  assert.equal(input.disabled, false); assert.equal(input.checked, false);
  input.checked = true; await input.handlers.change();
  assert.equal(calls.at(-1).command, "set_autostart"); assert.equal(calls.at(-1).args.enabled, true);
  assert.equal(input.checked, true); assert.equal(notices.length, 1);
  fail = true; input.checked = false; await input.handlers.change();
  assert.equal(input.checked, true); assert.equal(input.disabled, false); assert(hint.textContent.includes("System failure"));
  context.window = {}; context.renderApplicationSettings(view);
  assert.equal(view.card.children[1].children[0].children[0].disabled, true);
})().catch(error => {console.error(error); process.exitCode = 1;});
''')


def test_electron_task_attention_stops_on_focus_and_checks_request_origin():
    run_node(r'''
const fs = require("node:fs"), vm = require("node:vm"), assert = require("node:assert/strict");
const source = fs.readFileSync("electron/main.js", "utf8"), handlers = new Map(), flashes = [];
let focused = false, focusHandler;
const window = {webContents: {}, isFocused: () => focused, flashFrame: value => flashes.push(value),
  on(name, handler) {assert.equal(name, "focus"); focusHandler = handler;}};
const context = vm.createContext({window, origin: "http://127.0.0.1:8117", process: {platform: "win32"},
  ipcMain: {handle: (name, handler) => handlers.set(name, handler)}, desktopSettings: {},
});
vm.runInContext(source.slice(source.indexOf("    const allowedSender ="), source.indexOf("    window = new BrowserWindow(")), context);
vm.runInContext(source.split(/\r?\n/).find(line => line.includes('window.on("focus"')), context);
const request = {sender: window.webContents, senderFrame: {url: "http://127.0.0.1:8117/__recorder/"}};
handlers.get("sona:request-task-attention")(request); assert.deepEqual(flashes, [true]);
focused = true; focusHandler(); assert.deepEqual(flashes, [true, false]);
handlers.get("sona:request-task-attention")(request); assert.equal(flashes.length, 2);
assert.throws(() => handlers.get("sona:request-task-attention")({...request, senderFrame: {url: "https://example.test/"}}));
''')
