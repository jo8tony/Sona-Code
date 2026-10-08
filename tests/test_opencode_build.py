"""Patched CLI locale behavior and build/release integrity checks."""

import importlib.util
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_opencode", ROOT / "scripts/prepare-opencode.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


def test_patch_uses_real_y18n_and_preserves_locale_fallback_and_plurals():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the standalone locale patch regression")
    script = r'''
import fs from "node:fs";
import util from "node:util";
import assert from "node:assert/strict";
import { rewriteLocaleReader } from "./packaging/opencode-locale-plugin.mjs";
const source = fs.readFileSync("tests/fixtures/y18n-5.0.8/index.mjs", "utf8");
const locales = { en: { greet: "hello", count: {one: "%d item", other: "%d items"}},
  zh_CN: {greet: "你好"} };
let reads = 0;
const shim = code => ({format: util.format, resolve: (...parts) => parts.join("/"), exists: () => false,
  fs: {readFileSync() {reads++; throw Object.assign(new Error(code), {code});},
    writeFile(file, text, encoding, callback) { callback(Object.assign(new Error(code), {code}));}}});
const original = await import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
const patched = await import("data:text/javascript;base64," + Buffer.from(rewriteLocaleReader(source, locales)).toString("base64"));
for (const code of ["EPERM", "EUNKNOWN"]) {
  assert.throws(() => original.y18n({locale: "en_US", updateFiles: false}, shim(code)).__("greet"), {code});
  const before = reads;
  const translate = patched.y18n({locale: "en_US", updateFiles: false}, shim(code));
  assert.equal(translate.__("greet"), "hello");
  assert.equal(translate.__n("count", "counts", 1), "1 item");
  assert.equal(translate.__n("count", "counts", 3), "3 items");
  translate.setLocale("zh_CN"); assert.equal(translate.__("greet"), "你好");
  translate.setLocale("unknown"); assert.equal(translate.__("missing %s", "text"), "missing text");
  translate.updateLocale({custom: "customized"}); assert.equal(translate.__("custom"), "customized");
  assert.equal(reads, before);
  const noFallback = patched.y18n({locale: "en_US", updateFiles: false, fallbackToLanguage: false}, shim(code));
  assert.equal(noFallback.__("greet"), "greet");
  // Writes outside the embedded read path retain their original error behavior.
  const writable = patched.y18n({locale: "en", updateFiles: true}, shim(code));
  let writeError; writable.__("new", error => {writeError = error;});
  assert.equal(writeError.code, code);
}
assert.throws(() => rewriteLocaleReader(source.replace("err.code === 'ENOENT'", "false"), locales));
assert.throws(() => rewriteLocaleReader(source + source, locales));
assert.throws(() => rewriteLocaleReader(source, {}));
'''
    subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT, check=True, timeout=10)


def test_recipe_changes_with_patch_but_not_validation(monkeypatch, tmp_path):
    manifest = json.loads((ROOT / "packaging/opencode.json").read_text())
    original = prepare.recipe_hash(manifest)
    manifest["release_validation"] = {"recipe_sha256": original, "affected_windows": {}}
    assert prepare.recipe_hash(manifest) == original
    manifest["build"]["bun_version"] = "different"
    assert prepare.recipe_hash(manifest) != original
    plugin = tmp_path / "plugin.mjs"
    plugin.write_text("changed")
    monkeypatch.setattr(prepare, "PATCH", plugin)
    assert prepare.recipe_hash(manifest) != original


def test_release_requires_current_recipe_and_real_affected_machine_evidence():
    manifest = {"release_validation": {"recipe_sha256": "recipe", "affected_windows": {
        "normal_user": True, "startup_and_message": True, "tested_on": "2026-10-08",
        "windows_version": "Windows 11", "original_error": "EPERM B:\\~BUN\\locales\\en_US.json"}}}
    prepare.validate_release(manifest, "recipe")
    with pytest.raises(RuntimeError, match="Release blocked"):
        prepare.validate_release(manifest, "changed-recipe")
    manifest["release_validation"]["affected_windows"]["normal_user"] = False
    with pytest.raises(RuntimeError, match="Release blocked"):
        prepare.validate_release(manifest, "recipe")
    manifest["release_validation"]["affected_windows"] = None
    with pytest.raises(RuntimeError, match="Release blocked"):
        prepare.validate_release(manifest, "recipe")


def test_verified_archive_links_are_materialized_and_traversal_is_rejected(tmp_path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("repo/resources/locale.json", '{}')
        link = zipfile.ZipInfo("repo/locale.json")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        source.writestr(link, "resources/locale.json")
    output = tmp_path / "source"
    prepare.extract_zip(archive, output, strip_root=True)
    assert (output / "locale.json").read_text() == '{}'
    assert not (output / "locale.json").is_symlink()
    with zipfile.ZipFile(archive, "w") as source:
        source.writestr("repo/../../outside.txt", "escape")
    with pytest.raises(RuntimeError, match="escapes"):
        prepare.extract_zip(archive, output, strip_root=True)
    assert not (tmp_path / "outside.txt").exists()


def test_build_patch_rejects_unexpected_upstream_and_only_targets_windows():
    source = ('#!/usr/bin/env bun\nconst plugin = createSolidTransformPlugin()\n'
              '    plugins: [plugin],\n'
              '  // Smoke test: only run if binary is for current platform\n')
    result = prepare.patch_build(source, Path("plugin.mjs"))
    assert result.startswith("#!/usr/bin/env bun\n")
    assert 'item.os === "win32"' in result
    assert "assertLocalePatchApplied()" in result
    with pytest.raises(RuntimeError, match="does not match"):
        prepare.patch_build(source.replace("plugins: [plugin]", "plugins: []"), Path("plugin.mjs"))


@pytest.mark.parametrize("error, expected_calls", [
    ("renaming changes to cache dir: ENOTEMPTY: package\nfailed to apply patchfile", 2),
    ("integrity check failed", 1),
])
def test_dependency_install_only_retries_known_cache_conflict(monkeypatch, error, expected_calls):
    calls = []

    def run(arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 1 if len(calls) == 1 else 0, "", error)

    monkeypatch.setattr(prepare.subprocess, "run", run)
    if expected_calls == 1:
        with pytest.raises(subprocess.CalledProcessError):
            prepare.install_dependencies(Path("bun.exe"), Path("source"), {})
    else:
        prepare.install_dependencies(Path("bun.exe"), Path("source"), {})
    assert len(calls) == expected_calls
    assert all("--frozen-lockfile" in arguments for arguments in calls)
