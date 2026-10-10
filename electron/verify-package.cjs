"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

function verifyOpenSpecBundle(root) {
  const prefix = "桌面打包校验失败：";
  let manifest;
  try { manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8")); }
  catch (error) { throw new Error(`${prefix}OpenSpec 资源清单缺失或无效（${error.message}）`); }
  const files = manifest.files;
  if (!files || typeof files !== "object" || Array.isArray(files)) {
    throw new Error(`${prefix}OpenSpec 文件校验清单无效`);
  }
  for (const required of ["bin/openspec.exe", "bin/sona-command-runner.exe", "runtime/node.exe",
    "runtime/offline.cjs", "defaults/openspec-config.json",
    "package/node_modules/@fission-ai/openspec/bin/openspec.js"]) {
    if (!Object.hasOwn(files, required)) throw new Error(`${prefix}资源清单缺少 ${required}`);
  }
  const base = path.resolve(root);
  for (const [relative, expected] of Object.entries(files)) {
    const parts = relative.split("/");
    if (parts.some(part => !part || part === "." || part === ".." || /[\\:]/.test(part))
      || typeof expected !== "string" || !/^[a-f0-9]{64}$/.test(expected)) {
      throw new Error(`${prefix}OpenSpec 资源清单条目无效：${relative}`);
    }
    const target = path.join(base, ...parts);
    let data;
    try { data = fs.readFileSync(target); }
    catch (error) {
      throw new Error(`${prefix}缺少或无法读取 ${relative}；请检查安全软件隔离记录和构建资源（${error.message}）`);
    }
    if (crypto.createHash("sha256").update(data).digest("hex") !== expected) {
      throw new Error(`${prefix}${relative} 的 SHA-256 与资源清单不同`);
    }
  }
}

function verifyDesktopPackage(appOutDir) {
  const asar = require("@electron/asar");
  const archive = path.join(appOutDir, "resources", "app.asar");
  for (const relative of ["electron/main.js", "electron/desktop-settings.cjs", "electron/preload.js"]) {
    let bundled;
    try { bundled = asar.extractFile(archive, path.normalize(relative)); }
    catch (error) { throw new Error(`桌面打包校验失败：缺少 ${relative}（${error.message}）`); }
    const source = fs.readFileSync(path.resolve(__dirname, "..", relative));
    if (!bundled.equals(source)) throw new Error(`桌面打包校验失败：${relative} 与当前源码不同，请重新打包`);
  }
  verifyOpenSpecBundle(path.join(appOutDir, "resources", "tools", "openspec"));
}

module.exports = {verifyDesktopPackage, verifyOpenSpecBundle};

if (require.main === module) {
  try {
    if (!process.argv[2]) throw new Error("用法：node electron/verify-package.cjs <打包输出目录>");
    verifyDesktopPackage(path.resolve(process.argv[2]));
    console.log("桌面打包校验通过：桌面源码一致，OpenSpec 和 AI 命令启动器完整且 SHA-256 匹配");
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
