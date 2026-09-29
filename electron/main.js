"use strict";

const { app, BrowserWindow, dialog, ipcMain, shell, Tray, Menu, nativeImage } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const origin = "http://127.0.0.1:8117";
const workspaceUrl = `${origin}/__recorder/#/workspace`;
let window;
let tray;
let sidecar;
let sidecarError;
let quitting = false;

function showWindow() {
  if (!window || window.isDestroyed()) return;
  if (window.isMinimized()) window.restore();
  window.show();
  window.focus();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", showWindow);

  app.whenReady().then(async () => {
    const dataRoot = path.join(app.getPath("appData"), "SonaCode");
    const localAppData = process.env.LOCALAPPDATA || path.resolve(app.getPath("appData"), "..", "Local");
    const cacheRoot = path.join(localAppData, "SonaCode");
    const stateDir = path.join(dataRoot, "state");
    const logDir = path.join(cacheRoot, "logs");
    for (const dir of [dataRoot, cacheRoot, stateDir, logDir]) fs.mkdirSync(dir, { recursive: true });

    const iconPath = app.isPackaged
      ? path.join(process.resourcesPath, "icon.ico")
      : path.join(__dirname, "..", "src-tauri", "icons", "icon.ico");
    tray = new Tray(nativeImage.createFromPath(iconPath));
    tray.setToolTip("Sona Code");
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: "打开 Sona Code", click: showWindow },
      { type: "separator" },
      { label: "退出", click: () => { quitting = true; app.quit(); } },
    ]));
    tray.on("click", showWindow);

    const allowedSender = (event) => event.sender === window?.webContents
      && event.senderFrame?.url.startsWith(`${origin}/__recorder/`);
    ipcMain.handle("sona:choose-directory", async (event, options = {}) => {
      if (!allowedSender(event)) throw new Error("Invalid desktop request origin");
      const result = await dialog.showOpenDialog(window, {
        title: typeof options.title === "string" ? options.title : "选择目录",
        defaultPath: typeof options.defaultPath === "string" ? options.defaultPath : undefined,
        properties: ["openDirectory"],
      });
      return result.canceled ? null : result.filePaths[0];
    });
    ipcMain.handle("sona:open-website-login", async (event, value) => {
      if (!allowedSender(event)) throw new Error("Invalid desktop request origin");
      const url = new URL(value);
      if (!((url.protocol === "https:") ||
        (url.protocol === "http:" && ["127.0.0.1", "localhost"].includes(url.hostname))) ||
        url.username || url.password) throw new Error("登录网站必须使用 HTTPS");
      await shell.openExternal(url.toString());
    });

    window = new BrowserWindow({
      title: "Sona Code", width: 1280, height: 820, minWidth: 900, minHeight: 620,
      icon: iconPath, backgroundColor: "#111827", show: false, autoHideMenuBar: true,
      webPreferences: {
        preload: path.join(__dirname, "preload.js"),
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });
    window.on("close", (event) => {
      if (!quitting) { event.preventDefault(); window.hide(); }
    });
    window.webContents.on("will-navigate", (event, url) => {
      if (!url.startsWith(`${origin}/__recorder/`)) event.preventDefault();
    });
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.loadFile(path.join(__dirname, "..", "desktop", "index.html"));
    window.once("ready-to-show", showWindow);

    const instanceId = `${process.pid}-${Date.now()}`;
    const configPath = path.join(dataRoot, "config.json");
    const resourceDir = app.isPackaged ? process.resourcesPath : path.join(__dirname, "..", "src-tauri", "binaries");
    const sidecarFile = app.isPackaged
      ? path.join(resourceDir, "sona-code-sidecar.exe")
      : path.join(resourceDir, "sona-code-sidecar-x86_64-pc-windows-msvc.exe");
    const opencodeFile = app.isPackaged
      ? path.join(resourceDir, "opencode.exe")
      : path.join(resourceDir, "opencode-x86_64-pc-windows-msvc.exe");
    const env = { ...process.env };
    for (const key of ["XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"]) {
      env[`SONACODE_ORIGINAL_${key}`] = env[key] || "";
    }
    Object.assign(env, {
      PYTHONIOENCODING: "utf-8", SONACODE_DESKTOP_INSTANCE_ID: instanceId,
      SONACODE_BUNDLED_OPENCODE: opencodeFile,
      XDG_CONFIG_HOME: dataRoot, XDG_DATA_HOME: dataRoot,
      XDG_CACHE_HOME: cacheRoot, XDG_STATE_HOME: stateDir,
    });
    try {
      let existing = await probeBackend(configPath);
      if (existing.state === "sona" && await stopOrphanedBackend(existing.instanceId)) {
        existing = await probeBackend(configPath);
      }
      if (existing.state === "sona" && existing.version !== app.getVersion()) {
        throw new Error("127.0.0.1:8117 正由其他版本的 Sona Code 使用。请先退出旧版应用。");
      }
      if (existing.state === "conflict") {
        throw new Error("127.0.0.1:8117 已被其他程序占用。请退出占用程序后重试。");
      }
      if (existing.state === "free") {
        const log = fs.openSync(path.join(logDir, "electron-desktop.log"), "a");
        try {
          sidecar = spawn(sidecarFile, [
            "--host", "127.0.0.1", "--port", "8117",
            "--config", configPath,
            "--records-dir", path.join(dataRoot, "records"),
          ], { env, windowsHide: true, stdio: ["ignore", log, log] });
        } finally { fs.closeSync(log); }
        sidecar.on("error", (error) => { sidecarError = error; });
        try {
          await waitForBackend(instanceId);
        } catch (error) {
          // Another Sona Code shell may have claimed the port while this
          // sidecar was starting. Its server is safe to use if it owns the
          // same config and speaks the same application version.
          const replacement = await probeBackend(configPath);
          if (replacement.state !== "sona" || replacement.version !== app.getVersion()) throw error;
          sidecar = null;
        }
      }
      await window.loadURL(workspaceUrl);
    } catch (error) {
      dialog.showErrorBox("Sona Code 启动失败", `${error}\n诊断日志：${path.join(logDir, "electron-desktop.log")}`);
      app.quit();
    }
  }).catch((error) => { dialog.showErrorBox("Sona Code 启动失败", String(error)); app.quit(); });

  app.on("before-quit", () => {
    quitting = true;
    if (sidecar?.pid) {
      spawnSync("taskkill", ["/F", "/T", "/PID", String(sidecar.pid)], { windowsHide: true, timeout: 10000 });
      sidecar = null;
    }
  });
}

function requestJson(route) {
  return new Promise((resolve) => {
    const request = http.get(`${origin}/__recorder/api/${route}`, { timeout: 700 }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        body += chunk;
        if (body.length > 16384) request.destroy();
      });
      response.on("end", () => {
        try { resolve(response.statusCode === 200 ? JSON.parse(body) : null); }
        catch { resolve(null); }
      });
    });
    request.on("timeout", () => request.destroy());
    request.on("error", () => resolve(null));
  });
}

function portIsOpen() {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port: 8117 });
    socket.once("connect", () => { socket.destroy(); resolve(true); });
    socket.once("error", () => resolve(false));
    socket.setTimeout(700, () => { socket.destroy(); resolve(true); });
  });
}

async function probeBackend(configPath) {
  if (!await portIsOpen()) return { state: "free" };
  const [ping, meta] = await Promise.all([requestJson("ping"), requestJson("meta")]);
  const sameConfig = typeof meta?.config_path === "string" &&
    path.win32.normalize(meta.config_path).toLowerCase() === path.win32.normalize(configPath).toLowerCase();
  return ping?.ok === true && sameConfig && typeof meta?.version === "string"
    ? { state: "sona", instanceId: ping.instance_id, version: meta.version }
    : { state: "conflict" };
}

function processIsAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch { return false; }
}

function listeningProcessId() {
  const result = spawnSync("netstat", ["-ano", "-p", "tcp"], {
    encoding: "utf8", windowsHide: true, timeout: 5000,
  });
  if (result.status !== 0) return null;
  const match = result.stdout.match(/^\s*TCP\s+127\.0\.0\.1:8117\s+\S+\s+LISTENING\s+(\d+)\s*$/im);
  return match ? Number(match[1]) : null;
}

async function stopOrphanedBackend(instanceId) {
  const match = typeof instanceId === "string" && instanceId.match(/^(\d+)-\d+$/);
  if (!match || processIsAlive(Number(match[1]))) return false;
  const pid = listeningProcessId();
  if (!pid) return false;
  const pathResult = spawnSync("powershell.exe", [
    "-NoProfile", "-NonInteractive", "-Command",
    `(Get-CimInstance Win32_Process -Filter 'ProcessId = ${pid}').ExecutablePath`,
  ], { encoding: "utf8", windowsHide: true, timeout: 5000 });
  if (pathResult.status !== 0 ||
    path.win32.basename(pathResult.stdout.trim()).toLowerCase() !== "sona-code-sidecar.exe") return false;
  const killed = spawnSync("taskkill", ["/F", "/T", "/PID", String(pid)], {
    windowsHide: true, timeout: 10000,
  });
  if (killed.status !== 0) return false;
  for (let attempt = 0; attempt < 25; attempt++) {
    if (!await portIsOpen()) return true;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return false;
}

async function waitForBackend(instanceId) {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    if ((await requestJson("ping"))?.instance_id === instanceId) return;
    if (sidecarError) throw sidecarError;
    if (sidecar && sidecar.exitCode !== null) throw new Error(`后端进程退出：${sidecar.exitCode}`);
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("等待本地服务超时；可能有其他程序占用了 8117 端口");
}
