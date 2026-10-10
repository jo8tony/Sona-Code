"use strict";

const fs = require("node:fs");
const path = require("node:path");

function createDesktopSettings(app, configDir, runtime = process) {
  const file = path.join(configDir, "desktop-settings.json");
  const windows = runtime.platform === "win32";
  // electron-builder's portable launcher extracts process.execPath into TEMP.
  const executable = (windows && runtime.env.PORTABLE_EXECUTABLE_FILE) || runtime.execPath;
  const name = "Sona Code";
  const options = {path: executable, args: []};
  // Electron parses the lookup path as a command line when listing launchItems.
  const lookupOptions = {...options, path: windows ? `"${executable}"` : executable};
  function getAutostart() {
    if (!app.isPackaged) throw new Error("开机自启仅在打包后的桌面应用中可用");
    const settings = app.getLoginItemSettings(lookupOptions);
    if (windows) {
      // openAtLogin reads the default AppUserModelID, not our custom value name.
      return settings.launchItems.some(item => item.name === name && item.scope === "user"
        && path.win32.normalize(item.path).toLowerCase() === path.win32.normalize(executable).toLowerCase()
        && item.args.length === 0 && item.enabled);
    }
    return settings.openAtLogin && settings.executableWillLaunchAtLogin !== false;
  }
  function setAutostart(enabled) {
    if (typeof enabled !== "boolean") throw new Error("无效的开机自启设置");
    const previous = getAutostart();
    app.setLoginItemSettings({...options, openAtLogin: enabled, enabled, name});
    try {
      if (getAutostart() !== enabled) throw new Error("系统未应用开机自启设置");
      const temporary = file + ".tmp";
      fs.writeFileSync(temporary, JSON.stringify({autostart: enabled}) + "\n");
      fs.renameSync(temporary, file);
    } catch (error) {
      app.setLoginItemSettings({...options, openAtLogin: previous, enabled: previous, name});
      throw error;
    }
    return enabled;
  }
  function initialize() {
    if (!app.isPackaged) return;
    let enabled = true;
    try {
      const saved = JSON.parse(fs.readFileSync(file, "utf8"));
      if (typeof saved.autostart !== "boolean") throw new Error("无效的桌面设置文件");
      enabled = saved.autostart;
    } catch (error) { if (error.code !== "ENOENT") throw error; }
    setAutostart(enabled);
  }
  return {getAutostart, setAutostart, initialize};
}

module.exports = {createDesktopSettings};
