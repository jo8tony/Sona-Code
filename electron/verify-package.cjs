"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

function verifyToolResources(appOutDir) {
  const root = path.resolve(appOutDir, "resources", "tools", "openspec");
  if (!fs.existsSync(root) || fs.readdirSync(root).length === 0) {
    console.warn("警告：未附带 tools/openspec 离线工具资源，将使用 OpenCode 原生命令执行；OpenSpec 功能不可用");
    return;
  }
  let manifest;
  try { manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8")); }
  catch { throw new Error("桌面打包校验失败：缺少有效的 tools/openspec/manifest.json，请先准备离线工具资源"); }
  const files = manifest.files;
  if (!files || typeof files !== "object" || Array.isArray(files)) {
    throw new Error("桌面打包校验失败：工具资源清单无效");
  }
  for (const required of ["bin/sona-command-runner.exe", "bin/openspec.exe", "runtime/node.exe",
    "runtime/offline.cjs", "package/node_modules/@fission-ai/openspec/bin/openspec.js"]) {
    if (!Object.hasOwn(files, required)) {
      throw new Error(`桌面打包校验失败：工具资源清单缺少 ${required}，请用当前源码重新准备工具资源`);
    }
  }
  for (const [relative, expected] of Object.entries(files)) {
    if (relative.split("/").some(part => !part || part === "." || part === ".." || /[\\:]/.test(part))) {
      throw new Error("桌面打包校验失败：工具资源清单路径无效");
    }
    const file = path.join(root, ...relative.split("/"));
    let actual;
    try { actual = crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex"); }
    catch { throw new Error(`桌面打包校验失败：工具资源缺少 ${relative}`); }
    if (actual !== expected) throw new Error(`桌面打包校验失败：工具资源 ${relative} 校验不匹配`);
  }
}

function verifyDesktopPackage(appOutDir) {
  const asar = require("@electron/asar");
  const archive = path.join(appOutDir, "resources", "app.asar");
  for (const relative of ["electron/main.js", "electron/desktop-settings.cjs", "electron/preload.js", "electron/shutdown.cjs"]) {
    let bundled;
    try { bundled = asar.extractFile(archive, path.normalize(relative)); }
    catch (error) { throw new Error(`桌面打包校验失败：缺少 ${relative}（${error.message}）`); }
    const source = fs.readFileSync(path.resolve(__dirname, "..", relative));
    if (!bundled.equals(source)) throw new Error(`桌面打包校验失败：${relative} 与当前源码不同，请重新打包`);
  }
  verifyToolResources(appOutDir);
}

module.exports = {verifyDesktopPackage, verifyToolResources};

if (require.main === module) {
  try {
    if (!process.argv[2]) throw new Error("用法：node electron/verify-package.cjs <打包输出目录>");
    verifyDesktopPackage(path.resolve(process.argv[2]));
    console.log("桌面打包校验通过：桌面模块与当前源码一致，已附带的离线工具资源校验通过");
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
