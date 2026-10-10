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
  assert.equal(calls[0].path, process.platform === "win32" ? `"${process.execPath}"` : process.execPath);
  assert.deepEqual(calls[0].args, []);
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


def test_windows_electron36_autostart_custom_name_spaces_and_portable_launcher():
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
    // Electron 36 writes path verbatim, so an unquoted Run value is parsed incorrectly.
    const launchPath = settings.path.match(/^"([^"]+)"/)?.[1] || settings.path.split(" ")[0];
    if (settings.openAtLogin) entries.set(settings.name, {name: settings.name, scope: "user",
      path: launchPath, args: settings.args, enabled: settings.enabled});
    else entries.delete(settings.name);
  },
};
try {
  const settings = createDesktopSettings(app, root, runtime);
  settings.initialize();
  assert.equal(settings.getAutostart(), true, "Custom name and spaced path must not cause a false failure");
  assert.equal(calls[0].path, `"${runtime.execPath}"`); assert.deepEqual(calls[0].args, []);
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
  const diagnostic = JSON.parse(fs.readFileSync(path.join(root, "autostart-diagnostics.json")));
  assert.equal(diagnostic.implementation, "autostart-v2"); assert.equal(diagnostic.stage, "apply");
  assert.equal(diagnostic.requested, true); assert.equal(diagnostic.previous, false);
  assert.equal(diagnostic.targetPath, runtime.execPath); assert.deepEqual(diagnostic.launchItems, []);
  assert.equal(diagnostic.rollbackError, null);
  assert.equal(settings.getAutostart(), false);
  assert.equal(JSON.parse(fs.readFileSync(path.join(root, "desktop-settings.json"))).autostart, false);
  refuseWrite = false;
  createDesktopSettings(app, root, runtime).initialize();
  assert.equal(settings.getAutostart(), false, "Restart must preserve opt-out");
  runtime.execPath = "C:\\Users\\用户\\AppData\\Local\\Temp\\sona-123\\Sona Code.exe";
  runtime.env.PORTABLE_EXECUTABLE_FILE = "D:\\绿色软件\\Sona Code Portable.exe";
  const portable = createDesktopSettings(app, root, runtime);
  portable.setAutostart(true);
  assert.equal(calls.at(-1).path, `"${runtime.env.PORTABLE_EXECUTABLE_FILE}"`);
  assert.equal(portable.getAutostart(), true);
  runtime.execPath = "C:\\Users\\用户\\AppData\\Local\\Temp\\sona-456\\Sona Code.exe";
  const restarted = createDesktopSettings(app, root, runtime);
  restarted.initialize(); assert.equal(restarted.getAutostart(), true);
  assert.equal(calls.at(-1).path, `"${runtime.env.PORTABLE_EXECUTABLE_FILE}"`, "Portable restart must retain the original launcher");
  restarted.setAutostart(false);
  restarted.initialize(); assert.equal(restarted.getAutostart(), false);
} finally {
  assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
  fs.rmSync(root, {recursive: true, force: true});
}
''')


def test_electron_autostart_logs_failed_state_before_rollback_and_keeps_original_error():
    run_node(r'''
const fs = require("node:fs"), os = require("node:os"), path = require("node:path"), assert = require("node:assert/strict");
const {createDesktopSettings} = require("./electron/desktop-settings.cjs");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "sona-desktop-diagnostics-test-"));
const runtime = {platform: "win32", execPath: "C:\\Apps\\Sona Code.exe", env: {}, versions: {electron: "44.4.5"}};
const childProcess = require("node:child_process"), originalSpawn = childProcess.spawnSync;
childProcess.spawnSync = (command, args, options) => {
  assert.equal(item?.enabled, false, "Raw registry state must be captured before rollback");
  assert.equal(command, "powershell.exe"); assert.equal(options.windowsHide, true); assert.equal(options.timeout, 5000);
  const script = Buffer.from(args.at(-1), "base64").toString("utf16le");
  assert(script.includes("OpenSubKey") && script.includes("QueryValues"));
  assert(!script.includes("SetValue") && !script.includes("DeleteValue"), "Registry diagnostics must be read-only");
  return {status: 0, stdout: JSON.stringify({run: {exists: true, value: `"${runtime.execPath}"`},
    startupApproved: {exists: true, value: [3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]}})};
};
let item = null, writes = 0;
const app = {isPackaged: true, getVersion: () => "2.3.1",
  getLoginItemSettings() {return {openAtLogin: false, executableWillLaunchAtLogin: false, launchItems: item ? [item] : []};},
  setLoginItemSettings(options) {
    if (options.openAtLogin) item = {name: "Sona Code", scope: "user", path: runtime.execPath, args: [], enabled: false};
    else item = null;
    writes++;
  },
};
try {
  const settings = createDesktopSettings(app, root, runtime);
  assert.throws(() => settings.initialize(), /启动项仍被系统禁用.*autostart-v2.*诊断日志/);
  assert.equal(item, null, "Rollback removes the failed startup entry");
  const report = JSON.parse(fs.readFileSync(path.join(root, "autostart-diagnostics.json")));
  assert.equal(report.launchItems[0].enabled, false, "Diagnostics retain the state before rollback");
  assert.equal(report.electronVersion, "44.4.5"); assert.equal(report.appVersion, "2.3.1");
  assert.equal(report.launchItems[0].argsCount, 0); assert.equal(report.rollbackError, null);
  assert.equal(report.registry.run.exists, true); assert.equal(report.registry.startupApproved.value[0], 3);
  assert.equal(fs.existsSync(path.join(root, "desktop-settings.json")), false);
  app.setLoginItemSettings = () => {throw Error(++writes % 2 === 1 ? "Apply denied" : "Rollback denied");};
  childProcess.spawnSync = () => ({status: 1, stderr: "PowerShell blocked"});
  writes = 0;
  assert.throws(() => settings.setAutostart(true), /Apply denied/);
  const failed = JSON.parse(fs.readFileSync(path.join(root, "autostart-diagnostics.json")));
  assert.equal(failed.error, "Apply denied"); assert.equal(failed.rollbackError, "Rollback denied");
  assert.equal(failed.registry.error, "PowerShell blocked");
} finally {
  childProcess.spawnSync = originalSpawn;
  assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
  fs.rmSync(root, {recursive: true, force: true});
}
''')


def test_electron_package_verification_rejects_missing_or_stale_autostart_code():
    run_node(r'''
const fs = require("node:fs"), os = require("node:os"), path = require("node:path"), assert = require("node:assert/strict");
let asar;
try {asar = require("@electron/asar");}
catch (error) {if (error.code === "MODULE_NOT_FOUND") process.exit(0); throw error;}
const {verifyDesktopPackage} = require("./electron/verify-package.cjs");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "sona-desktop-package-test-"));
(async () => {
  try {
    for (const scenario of ["current", "missing", "stale"]) {
      const source = path.join(root, scenario, "source"), out = path.join(root, scenario, "output");
      fs.mkdirSync(path.join(source, "electron"), {recursive: true});
      fs.mkdirSync(path.join(out, "resources"), {recursive: true});
      for (const file of ["main.js", "desktop-settings.cjs", "preload.js"]) {
        if (scenario === "missing" && file === "desktop-settings.cjs") continue;
        const destination = path.join(source, "electron", file);
        fs.copyFileSync(path.join("electron", file), destination);
        if (scenario === "stale" && file === "desktop-settings.cjs") fs.appendFileSync(destination, "\n// Old build\n");
      }
      await asar.createPackage(source, path.join(out, "resources", "app.asar"));
      if (scenario === "current") verifyDesktopPackage(out);
      else assert.throws(() => verifyDesktopPackage(out), scenario === "missing" ? /缺少.*desktop-settings/ : /desktop-settings.*源码不同/);
    }
    assert(fs.readFileSync("electron/after-pack.cjs", "utf8").includes("verifyDesktopPackage(appOutDir)"));
  } finally {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    fs.rmSync(root, {recursive: true, force: true});
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
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
