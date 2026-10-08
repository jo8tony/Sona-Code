"""Build the pinned Windows OpenCode CLI with embedded yargs language resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "packaging/opencode-locale-plugin.mjs"


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def recipe_hash(manifest: dict) -> str:
    recipe = {"version": manifest["version"], "build": manifest["build"],
              "plugin_sha256": digest(PATCH), "preparer_sha256": digest(Path(__file__))}
    return hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()


def validate_release(manifest: dict, recipe: str) -> None:
    validation = manifest.get("release_validation", {})
    evidence = validation.get("affected_windows")
    if (validation.get("recipe_sha256") != recipe or not isinstance(evidence, dict)
            or evidence.get("normal_user") is not True
            or evidence.get("startup_and_message") is not True
            or not evidence.get("tested_on") or not evidence.get("windows_version")
            or not evidence.get("original_error")):
        raise RuntimeError("Release blocked: this OpenCode build recipe requires verification on an "
                           "affected Windows computer as a normal user (packaging/opencode.json)")


def download(asset: dict, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / asset["name"]
    if archive.is_file() and digest(archive) == asset["sha256"]:
        return archive
    print(f"Downloading {asset['name']}", flush=True)
    temporary = archive.with_suffix(".download")
    with urllib.request.urlopen(asset["url"], timeout=120) as response, temporary.open("wb") as stream:
        shutil.copyfileobj(response, stream)
    if digest(temporary) != asset["sha256"]:
        temporary.unlink()
        raise RuntimeError(f"OpenCode build input checksum mismatch: {asset['name']}")
    temporary.replace(archive)
    return archive


def extract_zip(archive: Path, destination: Path, *, strip_root: bool = False) -> None:
    """Materialize verified in-tree links on Windows without symlink privileges."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    links = []
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            parts = member.filename.replace("\\", "/").split("/")
            if strip_root:
                parts = parts[1:]
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(root):
                raise RuntimeError("OpenCode archive escapes build directory")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif stat.S_ISLNK(member.external_attr >> 16):
                linked = (target.parent / source.read(member).decode()).resolve()
                if not linked.is_relative_to(root):
                    raise RuntimeError("OpenCode archive link escapes build directory")
                links.append((target, linked))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as stream, target.open("wb") as output:
                    shutil.copyfileobj(stream, output)
    while links:
        pending = []
        for target, linked in links:
            if linked.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(linked, target)
            elif linked.is_dir():
                shutil.copytree(linked, target, dirs_exist_ok=True)
            else:
                pending.append((target, linked))
        if len(pending) == len(links):
            raise RuntimeError("Unresolved OpenCode source archive links")
        links = pending


def patch_build(source: str, plugin: Path) -> str:
    markers = ["const plugin = createSolidTransformPlugin()", "    plugins: [plugin],",
               "  // Smoke test: only run if binary is for current platform"]
    if any(source.count(marker) != 1 for marker in markers):
        raise RuntimeError("OpenCode build patch does not match the pinned upstream script")
    # Copy the plugin into the disposable source tree; never patch installed CLI files.
    specifier = json.dumps(plugin.as_posix())
    source = source.replace(markers[0],
                            f"import localePlugin, {{ assertLocalePatchApplied }} from {specifier}\n{markers[0]}")
    source = source.replace(markers[1],
                            '    plugins: [plugin, ...(item.os === "win32" ? [localePlugin] : [])],')
    return source.replace(markers[2], '  if (item.os === "win32") assertLocalePatchApplied()\n\n' + markers[2])


def run(arguments: list[str], **kwargs) -> None:
    print("Running " + " ".join(arguments), flush=True)
    subprocess.run(arguments, check=True, **kwargs)


def install_dependencies(bun: Path, source: Path, env: dict[str, str]) -> None:
    arguments = [str(bun), "install", "--frozen-lockfile", "--linker", "hoisted",
                 "--registry=https://registry.npmjs.org", "--ignore-scripts"]
    print("Installing pinned OpenCode dependencies", flush=True)
    for attempt in range(2):
        result = subprocess.run(arguments, cwd=source, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        print(result.stdout, end="", flush=True)
        print(result.stderr, end="", flush=True)
        if result.returncode == 0:
            return
        # Bun 1.3.14 can race while materializing duplicate patched variants on
        # Windows. The next frozen install reuses the completed patched cache.
        conflict = ("renaming changes to cache dir: ENOTEMPTY:" in result.stderr
                    and "failed to apply patchfile" in result.stderr)
        if attempt or not conflict:
            result.check_returncode()
        print("Retrying the frozen install after Bun's patched-cache rename conflict", flush=True)


def smoke(binary: Path, version: str, env: dict[str, str]) -> None:
    for flag in ("--version", "--help"):
        result = subprocess.run([str(binary), flag], env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or (flag == "--version" and result.stdout.strip() != version):
            raise RuntimeError(f"Patched OpenCode {flag} smoke test failed: {result.stdout}{result.stderr}")


def build(manifest: dict, recipe: str) -> Path:
    cache = ROOT / "build/opencode"
    work = cache / recipe[:16]
    binary = work / "opencode.exe"
    receipt_path = work / "receipt.json"
    spec = manifest["build"]
    env = dict(os.environ, OPENCODE_DISABLE_AUTOUPDATE="1")
    env.pop("BUN_BE_BUN", None)
    if binary.is_file() and receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("recipe_sha256") == recipe and receipt.get("binary_sha256") == digest(binary):
            smoke(binary, manifest["version"], env)
            return binary
    source_archive = download(spec["source"], cache / "archives")
    compiler_archive = download(spec["bun"], cache / "archives")
    source = work / "source"
    source_receipt_path = cache / "installed-source.json"
    inputs = {"source_sha256": spec["source"]["sha256"], "bun_version": spec["bun_version"],
              "install_recipe": "frozen-hoisted-noscripts-v1"}
    reuse_source = False
    if source_receipt_path.is_file():
        cached = json.loads(source_receipt_path.read_text(encoding="utf-8"))
        candidate = cache / cached.get("source_relative", "")
        if (all(cached.get(key) == value for key, value in inputs.items())
                and candidate.resolve().is_relative_to(cache.resolve())
                and candidate.resolve() != cache.resolve() and (candidate / "bun.lock").is_file()
                and digest(candidate / "bun.lock") == cached.get("lockfile_sha256")):
            source = candidate
            reuse_source = True
    compiler = work / "compiler"
    # Every recursive removal is confined to the known build directory.
    for directory in ([compiler] if reuse_source else [source, compiler]):
        if not directory.resolve().is_relative_to(cache.resolve()) or directory.resolve() == cache.resolve():
            raise RuntimeError("Invalid OpenCode build directory")
        if directory.exists():
            shutil.rmtree(directory)
    extract_zip(source_archive, source, strip_root=True)
    extract_zip(compiler_archive, compiler)
    bun = compiler / "bun-windows-x64-baseline/bun.exe"
    actual_version = subprocess.check_output([str(bun), "--version"], text=True, env=env).strip()
    if actual_version != spec["bun_version"]:
        raise RuntimeError(f"Unexpected Bun compiler version: {actual_version}")
    env["PATH"] = str(bun.parent) + os.pathsep + env.get("PATH", "")
    env["BUN_INSTALL_CACHE_DIR"] = str(cache / "dependencies")
    env.update(OPENCODE_VERSION=manifest["version"], OPENCODE_CHANNEL="latest", CI="true")
    env.pop("OPENCODE_RELEASE", None)
    # Preserve the first upstream models snapshot for repeat builds of this recipe.
    models = work / "models.json"
    if not models.is_file():
        request = urllib.request.Request("https://models.dev/api.json", headers={"User-Agent": "Mozilla/5.0 Sona-Code-Builder"})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        json.loads(data)
        models.write_bytes(data)
    env["MODELS_DEV_API_JSON"] = str(models)
    lock = source / "bun.lock"
    original_lock = digest(lock)
    install_dependencies(bun, source, env)
    if digest(lock) != original_lock:
        raise RuntimeError("OpenCode dependency install changed the pinned lockfile")
    source_receipt = {**inputs, "lockfile_sha256": original_lock,
                      "source_relative": source.relative_to(cache).as_posix()}
    source_receipt_path.write_text(json.dumps(source_receipt, indent=2) + "\n", encoding="utf-8")
    # The Bun build uses shipped WASM grammars, not the optional Node.js C++
    # bindings built by dependency postinstalls. No machine-wide MSVC install.
    for grammar in ("bash", "powershell"):
        wasm = source / f"node_modules/tree-sitter-{grammar}/tree-sitter-{grammar}.wasm"
        if not wasm.is_file():
            raise RuntimeError(f"Missing shipped tree-sitter grammar: {grammar}")
    plugin = source / "sonacode-locale-plugin.mjs"
    shutil.copy2(PATCH, plugin)
    build_script = source / "packages/opencode/script/build.ts"
    build_script.write_text(patch_build(build_script.read_text(encoding="utf-8"), plugin), encoding="utf-8")
    run([str(bun), "run", str(build_script), "--single", "--baseline", "--skip-install"], cwd=source, env=env)
    produced = source / "packages/opencode/dist" / spec["target"] / "bin/opencode.exe"
    smoke(produced, manifest["version"], env)
    shutil.copy2(produced, binary)
    receipt = {"recipe_sha256": recipe, "binary_sha256": digest(binary),
               "source_sha256": spec["source"]["sha256"], "lockfile_sha256": original_lock,
               "models_sha256": digest(models), "bun_version": actual_version,
               "patch": spec["patch"]}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return binary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-recipe", action="store_true")
    parser.add_argument("--release-check", action="store_true", help="Require affected-machine validation")
    options = parser.parse_args()
    manifest = json.loads((ROOT / "packaging/opencode.json").read_text(encoding="utf-8"))
    recipe = recipe_hash(manifest)
    if options.print_recipe:
        print(recipe)
        return
    if options.release_check:
        validate_release(manifest, recipe)
        print(f"Affected Windows verification recorded for {recipe}")
        return
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Patched bundled OpenCode currently supports Windows x64 only")
    binary = build(manifest, recipe)
    destination = ROOT / "src-tauri/binaries/opencode-x86_64-pc-windows-msvc.exe"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, destination)
    print(f"Patched OpenCode {manifest['version']}: {destination} (recipe {recipe})")


if __name__ == "__main__":
    main()
