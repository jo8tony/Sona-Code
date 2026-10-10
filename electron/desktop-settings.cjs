"use strict";

const fs = require("node:fs");
const path = require("node:path");

function readWindowsRegistry() {
  // Read only our value, independently of Electron's path filtering and write permissions.
  const script = `
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$result = @{}
foreach ($entry in @{
  run = 'Software\\Microsoft\\Windows\\CurrentVersion\\Run'
  startupApproved = 'Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\StartupApproved\\Run'
}.GetEnumerator()) {
  $key = $null
  try {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($entry.Value,
      [Microsoft.Win32.RegistryKeyPermissionCheck]::ReadSubTree,
      [System.Security.AccessControl.RegistryRights]::QueryValues)
    $value = if ($null -ne $key) { $key.GetValue('Sona Code', $null,
      [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) } else { $null }
    $result[$entry.Key] = @{exists = $null -ne $value; value = $value}
  } catch { $result[$entry.Key] = @{error = $_.Exception.Message} }
  finally { if ($null -ne $key) { $key.Dispose() } }
}
$result | ConvertTo-Json -Depth 4 -Compress
`;
  try {
    const result = require("node:child_process").spawnSync("powershell.exe", [
      "-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64"),
    ], {encoding: "utf8", windowsHide: true, timeout: 5000});
    if (result.error || result.status !== 0) {
      return {error: result.error?.message || result.stderr.trim() || `PowerShell exit ${result.status}`};
    }
    return JSON.parse(result.stdout.replace(/^\uFEFF/, "").trim());
  } catch (error) { return {error: error.message || String(error)}; }
}

function createDesktopSettings(app, configDir, runtime = process,
  diagnosticFile = path.join(configDir, "autostart-diagnostics.json")) {
  const file = path.join(configDir, "desktop-settings.json");
  const windows = runtime.platform === "win32";
  // electron-builder's portable launcher extracts process.execPath into TEMP.
  const executable = (windows && runtime.env.PORTABLE_EXECUTABLE_FILE) || runtime.execPath;
  const name = "Sona Code";
  // Electron 36 writes path verbatim and parses it as a command line on lookup.
  // Quote both operations so paths containing spaces work across Electron versions.
  const options = {path: windows ? `"${executable}"` : executable, args: []};
  function readSettings() {
    if (!app.isPackaged) throw new Error("开机自启仅在打包后的桌面应用中可用");
    return app.getLoginItemSettings(options);
  }
  function isEnabled(settings) {
    if (windows) {
      // openAtLogin reads the default AppUserModelID, not our custom value name.
      return settings.launchItems.some(item => item.name === name && item.scope === "user"
        && path.win32.normalize(item.path).toLowerCase() === path.win32.normalize(executable).toLowerCase()
        && item.args.length === 0 && item.enabled);
    }
    return settings.openAtLogin && settings.executableWillLaunchAtLogin !== false;
  }
  function getAutostart() { return isEnabled(readSettings()); }
  function setAutostart(enabled) {
    if (typeof enabled !== "boolean") throw new Error("无效的开机自启设置");
    const previous = getAutostart();
    let observed = null, rollbackError = null, stage = "apply";
    try {
      app.setLoginItemSettings({...options, openAtLogin: enabled, enabled, name});
      observed = readSettings();
      if (isEnabled(observed) !== enabled) {
        const item = observed.launchItems?.find(item => item.name === name && item.scope === "user");
        const reason = windows && !item ? "未读到当前用户的 Sona Code 启动项"
          : windows && !item.enabled ? "启动项仍被系统禁用"
            : "读回的启动路径或状态与设置不一致";
        throw new Error(`系统未应用开机自启设置：${reason} [autostart-v2]`);
      }
      stage = "persist";
      const temporary = file + ".tmp";
      fs.writeFileSync(temporary, JSON.stringify({autostart: enabled}) + "\n");
      fs.renameSync(temporary, file);
    } catch (error) {
      const registry = windows ? readWindowsRegistry() : undefined;
      try {
        app.setLoginItemSettings({...options, openAtLogin: previous, enabled: previous, name});
        if (getAutostart() !== previous) rollbackError = "未能恢复之前的系统设置";
      } catch (failure) { rollbackError = failure.message || String(failure); }
      // Capture the failed read before rollback; packaged Electron has no visible console.
      try {
        fs.writeFileSync(diagnosticFile, JSON.stringify({
          implementation: "autostart-v2", time: new Date().toISOString(), stage,
          appVersion: app.getVersion?.(), electronVersion: runtime.versions?.electron,
          platform: runtime.platform, execPath: runtime.execPath, targetPath: executable,
          portablePath: runtime.env.PORTABLE_EXECUTABLE_FILE || null,
          requested: enabled, previous, openAtLogin: observed?.openAtLogin,
          executableWillLaunchAtLogin: observed?.executableWillLaunchAtLogin,
          launchItems: observed?.launchItems?.filter(item => item.name === name).map(item => ({
            name: item.name, scope: item.scope, path: item.path, argsCount: item.args.length, enabled: item.enabled,
          })),
          registry,
          error: error.message || String(error), rollbackError,
        }, null, 2) + "\n");
        error.message += `；诊断日志：${diagnosticFile}`;
      } catch { /* Keep the original failure when diagnostics cannot be written. */ }
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
