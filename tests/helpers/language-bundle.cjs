"use strict";
const fs = require("node:fs"), path = require("node:path"), crypto = require("node:crypto");
const hash = data => crypto.createHash("sha256").update(data).digest("hex");
function languageBundleFixture(root) {
  const manifest = {schema: 1, platform: "windows-x64", files: {}, inputs: {}, sources: [], source_coverage: {}, components: {}};
  function file(name, data = Buffer.from("fixture: " + name)) {
    const target = path.join(root, name); fs.mkdirSync(path.dirname(target), {recursive:true}); fs.writeFileSync(target, data); manifest.files[name] = hash(data);
  }
  for (const [name, version, entry] of [
    ["node", "22.23.3", "runtime/node.exe"], ["jdk", "21.0.12.1+1", "runtime/jdk/bin/java.exe"],
    ["jdtls", "1.61.0", "servers/jdtls/plugins/org.eclipse.equinox.launcher_1.jar"],
    ["pyright", "1.1.414", "servers/node/node_modules/pyright/langserver.index.js"],
    ["typescript_server", "6.0.2", "servers/node/node_modules/typescript-language-server/lib/cli.mjs"],
    ["typescript", "6.0.3", "servers/node/node_modules/typescript/lib/tsserver.js"]]) {
    file(entry); manifest.components[name] = {version, entry}; const source = `sources/${name}.zip`; file(source);
    manifest.sources.push({component:name, version, path:source, sha256:manifest.files[source]});
    if (name === "jdtls") manifest.source_coverage[entry] = source;
  }
  for (const name of ["runtime/jdk/bin/server/jvm.dll", "runtime/jdk/lib/modules", "runtime/jdk/release",
    "servers/jdtls/config_win/config.ini",
    "servers/node/node_modules/pyright/dist/pyright-langserver.js", "servers/node/node_modules/typescript/lib/_tsserver.js",
    "servers/node/node_modules/typescript/lib/typescript.js", "licenses/node/LICENSE", "licenses/temurin/java.base/LICENSE",
    "licenses/jdtls/LICENSE", "licenses/pyright/LICENSE.txt", "licenses/typescript/LICENSE.txt",
    "licenses/typescript-language-server/LICENSE", "licenses/SONA_THIRD_PARTY_NOTICES.md"]) file(name);
  file("sources/manifest.json",Buffer.from(JSON.stringify({sources:manifest.sources,coverage:manifest.source_coverage,eclipse:[]})));
  for (const name of ["packaging/language-services.json", "packaging/language-services-package/package.json",
    "packaging/language-services-package/package-lock.json", "scripts/prepare-language-services.py", "THIRD_PARTY_NOTICES.md"])
    manifest.inputs[name] = hash(fs.readFileSync(name));
  fs.writeFileSync(path.join(root,"manifest.json"),JSON.stringify(manifest)); return manifest;
}
module.exports = {languageBundleFixture};
