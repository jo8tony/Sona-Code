"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const {isDeepStrictEqual} = require("node:util");

const languageInputs = ["packaging/language-services.json", "packaging/language-services-package/package.json",
  "packaging/language-services-package/package-lock.json", "scripts/prepare-language-services.py", "THIRD_PARTY_NOTICES.md"];
const languageRequired = ["runtime/jdk/bin/server/jvm.dll", "runtime/jdk/lib/modules", "runtime/jdk/release",
  "servers/jdtls/config_win/config.ini",
  "servers/node/node_modules/pyright/dist/pyright-langserver.js", "servers/node/node_modules/typescript/lib/_tsserver.js",
  "servers/node/node_modules/typescript/lib/typescript.js", "licenses/node/LICENSE", "licenses/temurin/java.base/LICENSE",
  "licenses/jdtls/LICENSE", "licenses/pyright/LICENSE.txt", "licenses/typescript/LICENSE.txt",
  "licenses/typescript-language-server/LICENSE", "licenses/SONA_THIRD_PARTY_NOTICES.md", "sources/manifest.json"];

function fileDigest(target) {
  const checksum = crypto.createHash("sha256"), buffer = Buffer.alloc(1024 * 1024);
  const fd = fs.openSync(target, "r");
  try { let size; while ((size = fs.readSync(fd, buffer, 0, buffer.length, null))) checksum.update(buffer.subarray(0, size)); }
  finally { fs.closeSync(fd); }
  return checksum.digest("hex");
}

function verifyLanguageServicesBundle(root, sourceRoot = path.resolve(__dirname, "..")) {
  const fail = message => {throw new Error("桌面打包校验失败：语言服务 " + message);};
  const base = path.resolve(root);
  function checked(relative) {
    if (typeof relative !== "string" || relative.split("/").some(part => !part || part === "." || part === ".." || /[\\:\0]/.test(part))) fail("无效路径 " + relative);
    let current = base;
    for (const part of relative.split("/")) {
      current = path.join(current, part);
      let stat; try {stat = fs.lstatSync(current);} catch (_) {fail("缺少 " + relative + "；请检查安全软件隔离记录");}
      if (stat.isSymbolicLink()) fail("资源不允许链接：" + relative);
    }
    return current;
  }
  function read(relative) {
    try {return JSON.parse(fs.readFileSync(checked(relative), "utf8"));}
    catch (error) {fail(relative + " 无效：" + error.message);}
  }
  const manifest = read("manifest.json"), components = manifest.components, files = manifest.files;
  const object = value => value && typeof value === "object" && !Array.isArray(value);
  const pins = {node: ["22.23.3", "runtime/node.exe"], jdk: ["21.0.12.1+1", "runtime/jdk/bin/java.exe"],
    jdtls: ["1.61.0", null], pyright: ["1.1.414", "servers/node/node_modules/pyright/langserver.index.js"],
    typescript_server: ["6.0.2", "servers/node/node_modules/typescript-language-server/lib/cli.mjs"],
    typescript: ["6.0.3", "servers/node/node_modules/typescript/lib/tsserver.js"]};
  if (manifest.schema !== 1 || manifest.platform !== "windows-x64" || !object(files) || !object(components) ||
      !isDeepStrictEqual(Object.keys(components).sort(), Object.keys(pins).sort())) fail("组件清单无效");
  for (const [name, [version, entry]] of Object.entries(pins)) {
    const item = components[name];
    if (!object(item) || item.version !== version || (entry ? item.entry !== entry :
        typeof item.entry !== "string" || !/^servers\/jdtls\/plugins\/org\.eclipse\.equinox\.launcher_[^/]+\.jar$/.test(item.entry))) fail(name + " 固定版本或入口不符");
    if (!Object.hasOwn(files, item.entry)) fail("清单缺少 " + item.entry);
  }
  for (const name of languageRequired) if (!Object.hasOwn(files, name)) fail("清单缺少 " + name);
  for (const [relative, expected] of Object.entries(files)) {
    if (typeof expected !== "string" || !/^[a-f0-9]{64}$/.test(expected)) fail("校验值无效：" + relative);
    const target = checked(relative);
    if (!fs.lstatSync(target).isFile() || fileDigest(target) !== expected) fail(relative + " 的 SHA-256 不符");
  }
  if (!Array.isArray(manifest.sources) || !object(manifest.source_coverage)) fail("对应源码清单缺失");
  for (const [name, [version]] of Object.entries(pins)) {
    const matches = manifest.sources.filter(item => item?.component === name && item.version === version);
    if (!matches.length) fail(name + " 对应源码或版本缺失");
    for (const item of matches) if (!Object.hasOwn(files, item.path) || files[item.path] !== item.sha256) fail("对应源码缺失：" + item.path);
  }
  for (const relative of Object.keys(files)) if (/^servers\/jdtls\/plugins\/(org\.eclipse\.|ch\.qos\.logback\.)/.test(relative)) {
    const component = relative.split("/").slice(0,4).join("/"), source = manifest.source_coverage[component];
    if (!source || !Object.hasOwn(files, source)) fail("对应 Eclipse 源码缺失：" + component);
  }
  const index = read("sources/manifest.json");
  if (!isDeepStrictEqual(index.sources, manifest.sources) || !isDeepStrictEqual(index.coverage, manifest.source_coverage) || !Array.isArray(index.eclipse)) fail("sources/manifest.json 与组件清单不一致");
  if (!object(manifest.inputs) || !isDeepStrictEqual(Object.keys(manifest.inputs).sort(), languageInputs.slice().sort())) fail("构建输入清单不完整");
  for (const name of languageInputs) if (fileDigest(path.join(sourceRoot, name)) !== manifest.inputs[name]) fail("构建输入已变化：" + name);
  return manifest;
}

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
  verifyLanguageServicesBundle(path.join(appOutDir, "resources", "tools", "language-services"));
}

module.exports = {verifyDesktopPackage, verifyOpenSpecBundle, verifyLanguageServicesBundle};

if (require.main === module) {
  try {
    if (!process.argv[2]) throw new Error("用法：node electron/verify-package.cjs <打包输出目录>");
    verifyDesktopPackage(path.resolve(process.argv[2]));
    console.log("桌面打包校验通过：桌面源码、命令启动器和离线语言服务及对应源码均完整且 SHA-256 匹配");
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
