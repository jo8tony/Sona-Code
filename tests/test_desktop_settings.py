"""Desktop startup choices persist and are restored on subsequent launches."""

from tests.test_workspace_completion import run_node


def test_electron_autostart_default_toggle_restart_and_failure_rollback():
    run_node(r'''
const fs = require("node:fs"), os = require("node:os"), path = require("node:path"), assert = require("node:assert/strict");
const {createDesktopSettings} = require("./electron/desktop-settings.cjs");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "sona-desktop-test-"));
let enabled = false;
const calls = [], app = {isPackaged: true,
  getLoginItemSettings(options) {return {openAtLogin: enabled, executableWillLaunchAtLogin: enabled,
    launchItems: enabled ? [{name: "Sona Code", scope: "user", path: process.execPath, args: [], enabled}] : []};},
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
  assert.throws(() => settings.getAutostart(), /打包后/);
} finally {
  assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
  fs.rmSync(root, {recursive: true, force: true});
}
''')


def test_windows_autostart_custom_name_spaces_and_portable_launcher():
    run_node(r'''
const fs = require("node:fs"), os = require("node:os"), path = require("node:path"), assert = require("node:assert/strict");
const {createDesktopSettings} = require("./electron/desktop-settings.cjs");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "sona-desktop-windows-test-"));
const runtime = {platform: "win32", execPath: "C:\\Users\\用户\\Apps\\Sona Code\\Sona Code.exe", env: {}};
const entries = new Map(), calls = [];
let refuseWrite = false;
const app = {isPackaged: true,
  getLoginItemSettings(options) {
    // Native openAtLogin reads only AppUserModelID; launchItems parses a command line.
    const lookup = options.path.match(/^"([^"]+)"/)?.[1] || options.path.split(" ")[0];
    const launchItems = [...entries.values()].filter(item => item.path.toLowerCase() === lookup.toLowerCase());
    return {openAtLogin: false, launchItems, executableWillLaunchAtLogin: launchItems.some(item => item.enabled)};
  },
  setLoginItemSettings(settings) {
    calls.push(settings);
    if (refuseWrite) return;
    if (settings.openAtLogin) entries.set(settings.name, {name: settings.name, scope: "user",
      path: settings.path, args: settings.args, enabled: settings.enabled});
    else entries.delete(settings.name);
  },
};
try {
  const settings = createDesktopSettings(app, root, runtime);
  settings.initialize();
  assert.equal(settings.getAutostart(), true, "Custom name and spaced path must not cause a false failure");
  assert.equal(calls[0].path, runtime.execPath); assert.deepEqual(calls[0].args, []);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, "desktop-settings.json"))).autostart, true);
  // Neither an unrelated entry nor another command for this executable is our setting.
  entries.get("Sona Code").enabled = false;
  entries.set("Other", {name: "Other", scope: "user", path: runtime.execPath, args: [], enabled: true});
  assert.equal(settings.getAutostart(), false, "Task Manager disablement must be visible");
  entries.get("Sona Code").enabled = true;
  entries.get("Sona Code").args = ["--other"];
  assert.equal(settings.getAutostart(), false);
  entries.get("Sona Code").args = [];
  entries.get("Sona Code").scope = "machine";
  assert.equal(settings.getAutostart(), false);
  entries.get("Sona Code").scope = "user";
  assert.equal(settings.setAutostart(false), false, "An unrelated enabled entry must not prevent disabling ours");
  refuseWrite = true;
  assert.throws(() => settings.setAutostart(true), /系统未应用/);
  assert.equal(settings.getAutostart(), false);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, "desktop-settings.json"))).autostart, false);
  refuseWrite = false;
  createDesktopSettings(app, root, runtime).initialize();
  assert.equal(settings.getAutostart(), false, "Restart must preserve opt-out");
  runtime.execPath = "C:\\Users\\用户\\AppData\\Local\\Temp\\sona-123\\Sona Code.exe";
  runtime.env.PORTABLE_EXECUTABLE_FILE = "D:\\绿色软件\\Sona Code Portable.exe";
  const portable = createDesktopSettings(app, root, runtime);
  portable.setAutostart(true);
  assert.equal(calls.at(-1).path, runtime.env.PORTABLE_EXECUTABLE_FILE);
  assert.equal(portable.getAutostart(), true);
  runtime.execPath = "C:\\Users\\用户\\AppData\\Local\\Temp\\sona-456\\Sona Code.exe";
  const restarted = createDesktopSettings(app, root, runtime);
  restarted.initialize(); assert.equal(restarted.getAutostart(), true);
  assert.equal(calls.at(-1).path, runtime.env.PORTABLE_EXECUTABLE_FILE, "Portable restart must retain the original launcher");
  restarted.setAutostart(false);
  restarted.initialize(); assert.equal(restarted.getAutostart(), false);
} finally {
  assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
  fs.rmSync(root, {recursive: true, force: true});
}
''')


def test_macos_autostart_uses_native_state_and_ignores_windows_portable_path():
    run_node(r'''
const fs = require("node:fs"), os = require("node:os"), path = require("node:path"), assert = require("node:assert/strict");
const {createDesktopSettings} = require("./electron/desktop-settings.cjs");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "sona-desktop-macos-test-"));
const runtime = {platform: "darwin", execPath: "/Applications/Sona Code.app/Contents/MacOS/Sona Code",
  env: {PORTABLE_EXECUTABLE_FILE: "C:\\Sona Code.exe"}};
let enabled = false;
const app = {isPackaged: true,
  getLoginItemSettings(options) {assert.equal(options.path, runtime.execPath); return {openAtLogin: enabled};},
  setLoginItemSettings(options) {assert.equal(options.path, runtime.execPath); enabled = options.openAtLogin;},
};
try {
  const settings = createDesktopSettings(app, root, runtime);
  settings.initialize(); assert.equal(settings.getAutostart(), true);
  settings.setAutostart(false); settings.initialize(); assert.equal(settings.getAutostart(), false);
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
