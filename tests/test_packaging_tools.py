"""Offline tools are optional, but any included bundle must be complete."""

from tests.test_workspace_completion import run_node


def test_packaged_tools_reject_missing_outdated_and_corrupt_resources():
    run_node(r'''
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const crypto = require("node:crypto"), assert = require("node:assert/strict");
const {verifyToolResources} = require("./electron/verify-package.cjs");
const output = fs.mkdtempSync(path.join(os.tmpdir(), "sona-tool-package-test-"));
const root = path.join(output, "resources", "tools", "openspec");
try {
  const warnings = [];
  const warn = console.warn;
  try {
    console.warn = message => warnings.push(message);
    verifyToolResources(output);
    fs.mkdirSync(root, {recursive: true});
    verifyToolResources(output);
  } finally { console.warn = warn; }
  assert.equal(warnings.length, 2);
  assert(warnings.every(message => message.includes("原生命令执行")));
  fs.writeFileSync(path.join(root, "partial-resource"), "incomplete");
  assert.throws(() => verifyToolResources(output), /manifest.json/);
  fs.unlinkSync(path.join(root, "partial-resource"));
  const files = {};
  for (const relative of ["bin/sona-command-runner.exe", "bin/openspec.exe", "runtime/node.exe",
    "runtime/offline.cjs", "package/node_modules/@fission-ai/openspec/bin/openspec.js",
    "templates/.opencode/skills/example/SKILL.md"]) {
    const file = path.join(root, relative);
    fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, "resource");
    files[relative] = crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
  }
  const manifest = path.join(root, "manifest.json");
  fs.writeFileSync(manifest, "invalid-json");
  assert.throws(() => verifyToolResources(output), /manifest.json/);
  fs.writeFileSync(manifest, JSON.stringify({files}));
  verifyToolResources(output);
  const runner = "bin/sona-command-runner.exe";
  const old = {...files}; delete old[runner];
  fs.writeFileSync(manifest, JSON.stringify({files: old}));
  assert.throws(() => verifyToolResources(output), /清单缺少.*sona-command-runner/);
  fs.writeFileSync(manifest, JSON.stringify({files}));
  fs.unlinkSync(path.join(root, runner));
  assert.throws(() => verifyToolResources(output), /资源缺少.*sona-command-runner/);
  fs.writeFileSync(path.join(root, runner), "old-runner");
  assert.throws(() => verifyToolResources(output), /sona-command-runner.*校验不匹配/);
  fs.writeFileSync(path.join(root, runner), "resource");
  fs.unlinkSync(path.join(root, "templates/.opencode/skills/example/SKILL.md"));
  assert.throws(() => verifyToolResources(output), /资源缺少.*SKILL.md/);
  fs.writeFileSync(manifest, JSON.stringify({files: {...files, "../escape": "invalid"}}));
  fs.writeFileSync(path.join(root, "templates/.opencode/skills/example/SKILL.md"), "resource");
  assert.throws(() => verifyToolResources(output), /路径无效/);
} finally {
  assert.equal(path.dirname(path.resolve(output)), path.resolve(os.tmpdir()));
  fs.rmSync(output, {recursive: true, force: true});
}
''')


def test_application_versions_and_desktop_build_resources_are_aligned():
    import json
    import re
    from pathlib import Path

    import yaml
    from sona_code import __version__

    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / "package.json").read_text())
    lock = json.loads((root / "package-lock.json").read_text())
    tauri = json.loads((root / "src-tauri/tauri.conf.json").read_text())
    assert package["version"] == lock["version"] == lock["packages"][""]["version"] == tauri["version"] == __version__
    for filename in ("pyproject.toml", "src-tauri/Cargo.toml"):
        assert re.search(r'^version = "([^"]+)"', (root / filename).read_text(), re.M)[1] == __version__
    normal = yaml.safe_load((root / "electron-builder.yml").read_text())
    offline = yaml.safe_load((root / "packaging/electron-builder-offline.yml").read_text())
    assert offline["electronVersion"] == package["devDependencies"]["electron"]
    assert offline["extraResources"] == normal["extraResources"]
    assert {item["from"] for item in normal["extraResources"]} >= {
        "src-tauri/binaries/sona-code-sidecar-x86_64-pc-windows-msvc.exe",
        "src-tauri/binaries/opencode-x86_64-pc-windows-msvc.exe",
    }
