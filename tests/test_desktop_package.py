"""A successful build must include the command bridge required by AI sessions."""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_workspace_completion import run_node


@pytest.mark.parametrize("scenario", ["missing", "unlisted", "changed"])
def test_windows_package_rejects_incomplete_or_corrupt_command_runner(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for desktop package verification")
    dependency = subprocess.run([node, "-e", "try { require.resolve('@electron/asar'); } "
        "catch (error) { if (error.code === 'MODULE_NOT_FOUND') process.exit(42); throw error; }"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10)
    if dependency.returncode == 42:
        pytest.skip("npm ci required for desktop package verification")
    assert dependency.returncode == 0, dependency.stderr.decode(errors="replace")
    run_node(r'''
const fs = require("node:fs"), os = require("node:os"), path = require("node:path");
const assert = require("node:assert/strict"), crypto = require("node:crypto");
const asar = require("@electron/asar");
const {verifyDesktopPackage} = require("./electron/verify-package.cjs");
const root = fs.mkdtempSync(path.join(os.tmpdir(), "sona-command-package-"));
(async () => {
  try {
    const source = path.join(root, "source"), out = path.join(root, "output");
    fs.mkdirSync(path.join(source, "electron"), {recursive: true});
    fs.mkdirSync(path.join(out, "resources"), {recursive: true});
    for (const file of ["main.js", "desktop-settings.cjs", "preload.js"]) {
      fs.copyFileSync(path.join("electron", file), path.join(source, "electron", file));
    }
    await asar.createPackage(source, path.join(out, "resources", "app.asar"));
    const bundle = path.join(out, "resources", "tools", "openspec");
    const files = {};
    for (const relative of ["bin/openspec.exe", "bin/sona-command-runner.exe",
      "runtime/node.exe", "runtime/offline.cjs", "defaults/openspec-config.json",
      "package/node_modules/@fission-ai/openspec/bin/openspec.js"]) {
      const target = path.join(bundle, relative), data = Buffer.from("fixture: " + relative);
      fs.mkdirSync(path.dirname(target), {recursive: true}); fs.writeFileSync(target, data);
      files[relative] = crypto.createHash("sha256").update(data).digest("hex");
    }
    const manifest = path.join(bundle, "manifest.json");
    fs.writeFileSync(manifest, JSON.stringify({platform: "windows_x64", files}));
    verifyDesktopPackage(out);
    const runner = path.join(bundle, "bin", "sona-command-runner.exe");
    const scenario = SCENARIO;
    if (scenario === "missing") fs.unlinkSync(runner);
    if (scenario === "unlisted") {
      delete files["bin/sona-command-runner.exe"];
      fs.writeFileSync(manifest, JSON.stringify({platform: "windows_x64", files}));
    }
    if (scenario === "changed") fs.appendFileSync(runner, "corrupt");
    assert.throws(() => verifyDesktopPackage(out), /sona-command-runner/,
      "Packaging must stop when the AI command bridge is missing, unlisted or changed");
  } finally {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(os.tmpdir()));
    fs.rmSync(root, {recursive: true, force: true});
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
'''.replace("SCENARIO", repr(scenario)))
