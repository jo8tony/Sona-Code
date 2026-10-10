"use strict";

const fs = require("node:fs");
const path = require("node:path");

function createDesktopSettings(app, configDir) {
  const file = path.join(configDir, "desktop-settings.json");
  const options = {path: process.execPath, args: []};
  function getAutostart() {
    if (!app.isPackaged) throw new Error("开机自启仅在安装版桌面应用中可用");
    const settings = app.getLoginItemSettings(options);
    return settings.openAtLogin && settings.executableWillLaunchAtLogin !== false;
  }
  function setAutostart(enabled) {
    if (typeof enabled !== "boolean") throw new Error("无效的开机自启设置");
    const previous = getAutostart();
    app.setLoginItemSettings({...options, openAtLogin: enabled, enabled, name: "Sona Code"});
    try {
      if (getAutostart() !== enabled) throw new Error("系统未应用开机自启设置");
      const temporary = file + ".tmp";
      fs.writeFileSync(temporary, JSON.stringify({autostart: enabled}) + "\n");
      fs.renameSync(temporary, file);
    } catch (error) {
      app.setLoginItemSettings({...options, openAtLogin: previous, enabled: previous, name: "Sona Code"});
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
