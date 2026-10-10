"""Opt-in Windows login item checks against actual Electron runtime versions."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest


BINARIES = [value for value in os.environ.get("ELECTRON_TEST_BINARIES", "").split(os.pathsep) if value]
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt" or not BINARIES, reason="Set ELECTRON_TEST_BINARIES to Windows Electron EXEs")
@pytest.mark.parametrize("binary", BINARIES or [""])
def test_native_windows_autostart_quoted_paths_toggle_restart_and_rollback(binary, tmp_path):
    import winreg

    source = Path(binary).resolve()
    assert source.is_file(), f"Electron executable not found: {source}"
    runtime = tmp_path / "绿色 Native App"
    runtime.mkdir()
    executable = runtime / "Sona Native Test.exe"
    for item in source.parent.rglob("*"):
        relative = item.relative_to(source.parent)
        if relative.parts[0] == "resources":
            continue
        target = executable if item == source else runtime / relative
        if item.is_dir():
            target.mkdir(exist_ok=True)
        else:
            try:
                os.link(item, target)
            except OSError:
                shutil.copy2(item, target)
    app_dir = runtime / "resources/app"
    app_dir.mkdir(parents=True)
    (app_dir / "package.json").write_text(json.dumps({
        "name": "sona-native-autostart-test", "version": "1.0.0", "main": "main.cjs",
    }), encoding="utf-8")
    token = uuid.uuid4().hex
    entry_name = f"Sona Code Autostart Test {token}"
    result_file = tmp_path / "result.json"
    script = r'''
const {app} = require("electron");
const fs = require("node:fs"), path = require("node:path"), assert = require("node:assert/strict");
const config = CONFIG;
app.setAppUserModelId(`sona.native.autostart.${config.token}`);
app.whenReady().then(() => {
  const options = {name: config.entryName, path: `"${process.execPath}"`, args: []};
  const read = () => app.getLoginItemSettings({path: options.path, args: []});
  let forceDisabled = false;
  const facade = {
    get isPackaged() {return app.isPackaged;},
    getVersion: () => app.getVersion(),
    setLoginItemSettings(settings) {app.setLoginItemSettings({...settings, name: config.entryName,
      enabled: forceDisabled && settings.openAtLogin ? false : settings.enabled});},
    getLoginItemSettings(settings) {
      const observed = app.getLoginItemSettings(settings);
      return {...observed, launchItems: observed.launchItems.map(item =>
        item.name === config.entryName ? {...item, name: "Sona Code"} : item)};
    },
  };
  try {
    assert.equal(app.isPackaged, true);
    assert(process.execPath.includes(" "));
    if (process.versions.electron === "36.2.0") {
      app.setLoginItemSettings({...options, path: process.execPath, openAtLogin: true, enabled: true});
      assert.equal(read().launchItems.some(item => item.name === config.entryName), false,
        "Electron 36 writes unquoted paths verbatim and Windows truncates them at spaces");
      app.setLoginItemSettings({...options, openAtLogin: false});
    }
    const data = path.join(config.root, "data");
    fs.mkdirSync(data);
    const {createDesktopSettings} = require(config.module);
    const settings = createDesktopSettings(facade, data);
    settings.initialize(); assert.equal(settings.getAutostart(), true, "First launch must enable autostart");
    const entry = read().launchItems.find(item => item.name === config.entryName);
    assert.equal(entry.path, process.execPath); assert.deepEqual(entry.args, []); assert.equal(entry.enabled, true);
    app.setLoginItemSettings({...options, openAtLogin: true, enabled: false});
    assert.equal(settings.getAutostart(), false, "Native StartupApproved disablement is visible");
    settings.setAutostart(true); assert.equal(settings.getAutostart(), true);
    settings.setAutostart(false); assert.equal(settings.getAutostart(), false);
    createDesktopSettings(facade, data).initialize(); assert.equal(settings.getAutostart(), false);
    forceDisabled = true;
    assert.throws(() => settings.setAutostart(true), /系统禁用/);
    const diagnostic = JSON.parse(fs.readFileSync(path.join(data, "autostart-diagnostics.json")));
    assert.equal(diagnostic.launchItems[0].enabled, false); assert.equal(diagnostic.rollbackError, null);
    forceDisabled = false;
    const portableRuntime = {platform: "win32", execPath: process.execPath,
      env: {PORTABLE_EXECUTABLE_FILE: config.portable}, versions: process.versions};
    const portable = createDesktopSettings(facade, data, portableRuntime);
    portable.setAutostart(true); assert.equal(portable.getAutostart(), true);
    const portableState = app.getLoginItemSettings({path: `"${config.portable}"`, args: []});
    assert.equal(portableState.launchItems.find(item => item.name === config.entryName).path, config.portable);
    const write = fs.writeFileSync;
    fs.writeFileSync = () => {throw Error("Disk failure");};
    try {assert.throws(() => portable.setAutostart(false), /Disk failure/);}
    finally {fs.writeFileSync = write;}
    assert.equal(portable.getAutostart(), true, "Persistence failure restores the previous registry state");
    portable.setAutostart(false);
    fs.writeFileSync(config.result, JSON.stringify({ok: true, electron: process.versions.electron}));
  } catch (error) {
    fs.writeFileSync(config.result, JSON.stringify({ok: false, electron: process.versions.electron, error: error.stack}));
  } finally {
    app.setLoginItemSettings({...options, openAtLogin: false});
    app.quit();
  }
}).catch(error => {fs.writeFileSync(config.result, JSON.stringify({ok: false, error: error.stack})); app.quit();});
'''
    portable = tmp_path / "便携 Sona Code.exe"
    shutil.copy2(source, portable)
    config = {
        "token": token, "entryName": entry_name, "root": str(tmp_path),
        "result": str(result_file), "portable": str(portable),
        "module": str(ROOT / "electron/desktop-settings.cjs"),
    }
    (app_dir / "main.cjs").write_text(script.replace("CONFIG", json.dumps(config, ensure_ascii=False)), encoding="utf-8")
    try:
        completed = subprocess.run(
            [str(executable)], cwd=runtime, capture_output=True, timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result_file.exists(), f"Electron exit {completed.returncode}: {completed.stderr.decode(errors='replace')}"
        result = json.loads(result_file.read_text(encoding="utf-8"))
        assert result["ok"], result.get("error")
    finally:
        # Remove only this test's unique values even if Electron exits unexpectedly.
        for subkey in (
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
        ):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
                    try:
                        winreg.DeleteValue(key, entry_name)
                    except FileNotFoundError:
                        pass
            except FileNotFoundError:
                pass
