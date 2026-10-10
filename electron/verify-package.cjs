"use strict";

const fs = require("node:fs");
const path = require("node:path");

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
}

module.exports = {verifyDesktopPackage};

if (require.main === module) {
  try {
    if (!process.argv[2]) throw new Error("用法：node electron/verify-package.cjs <打包输出目录>");
    verifyDesktopPackage(path.resolve(process.argv[2]));
    console.log("桌面打包校验通过：启动、自启和 preload 模块均与当前源码一致");
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
