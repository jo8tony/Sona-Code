"""Build a verified, offline OpenSpec bundle using pinned upstream archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()


def download(asset: dict, base: str, cache: Path, *, offline: bool = False) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / asset["name"]
    if not archive.exists() or digest(archive) != asset["sha256"]:
        if offline:
            raise RuntimeError(f"Offline build requires a verified cached archive: {asset['name']}")
        print(f"Downloading {asset['name']}", flush=True)
        temporary = archive.with_suffix(archive.suffix + ".download")
        with urllib.request.urlopen(base + asset["name"], timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if digest(temporary) != asset["sha256"]:
            raise RuntimeError(f"SHA-256 mismatch: {asset['name']}")
        temporary.replace(archive)
    return archive


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    resolved = destination.resolve()
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as source:
            for name in source.namelist():
                if not (destination / name).resolve().is_relative_to(resolved):
                    raise RuntimeError("Archive path escapes build directory")
            source.extractall(destination)
    else:
        with tarfile.open(archive) as source:
            # Official verified archives still get path/link validation.
            for member in source.getmembers():
                if not (destination / member.name).resolve().is_relative_to(resolved):
                    raise RuntimeError("Archive path escapes build directory")
                if member.issym() or member.islnk():
                    target = (destination / member.name).parent / member.linkname
                    if not target.resolve().is_relative_to(resolved):
                        raise RuntimeError("Archive link escapes build directory")
            source.extractall(destination, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))


def run(arguments: list[str], **kwargs) -> None:
    subprocess.run(arguments, check=True, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "openspec")
    parser.add_argument("--offline", action="store_true", help="Use verified archives and npm cache only; never download")
    options = parser.parse_args()
    manifest = json.loads((ROOT / "packaging/openspec.json").read_text())
    system = "windows" if os.name == "nt" else platform.system().lower()
    arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
    key = f"{system}_{arch}"
    if key not in manifest["node_assets"]:
        raise RuntimeError(f"Unsupported OpenSpec platform: {key}")
    bundle = options.output.resolve()
    if not bundle.is_relative_to((ROOT / "build").resolve()):
        raise RuntimeError("Bundle output must be inside this repository's build directory")
    cache = ROOT / "build" / "openspec-cache"
    node_asset = manifest["node_assets"][key]
    archive = download(node_asset, f"https://nodejs.org/dist/v{manifest['node_version']}/", cache, offline=options.offline)
    node_home = cache / node_asset["name"].removesuffix(".zip").removesuffix(".tar.gz")
    if not node_home.exists():
        extract(archive, cache)
    suffix = ".exe" if os.name == "nt" else ""
    node = node_home / ("node.exe" if os.name == "nt" else "bin/node")
    npm_cli = node_home / ("node_modules/npm/bin/npm-cli.js" if os.name == "nt" else "lib/node_modules/npm/bin/npm-cli.js")
    bundle.mkdir(parents=True, exist_ok=True)
    package = bundle / "package"
    package.mkdir(exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(ROOT / "packaging/openspec-package" / name, package / name)
    build_env = dict(os.environ, OPENSPEC_TELEMETRY="0", OPENSPEC_NO_UPDATE_CHECK="1", CI="true")
    build_env["PATH"] = str(node.parent) + os.pathsep + build_env.get("PATH", "")
    run([str(node), str(npm_cli), "ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund",
         *(["--offline"] if options.offline else [])], cwd=package, env=build_env)
    runtime = bundle / "runtime"
    runtime.mkdir(exist_ok=True)
    shutil.copy2(node, runtime / ("node" + suffix))
    shutil.copy2(ROOT / "packaging/openspec-launcher/offline.cjs", runtime / "offline.cjs")
    licenses = bundle / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copy2(node_home / "LICENSE", licenses / "Node-LICENSE.txt")
    cli_package = package / "node_modules/@fission-ai/openspec"
    shutil.copy2(cli_package / "LICENSE", licenses / "OpenSpec-LICENSE.txt")
    # Preserve each transitive dependency's license in the distributed package.
    notices = []
    for metadata in sorted((package / "node_modules").rglob("package.json")):
        data = json.loads(metadata.read_text(encoding="utf-8"))
        notices.append({"name": data.get("name"), "version": data.get("version"), "license": data.get("license")})
    (licenses / "dependencies.json").write_text(json.dumps(notices, ensure_ascii=False, indent=2), encoding="utf-8")
    config = {"profile": "custom", "delivery": "both", "workflows": list(manifest["workflows"])}
    defaults = bundle / "defaults"
    defaults.mkdir(exist_ok=True)
    (defaults / "openspec-config.json").write_text(json.dumps(config), encoding="utf-8")
    staging = cache / "templates" / manifest["version"]
    if staging.exists():
        if not staging.resolve().is_relative_to(cache.resolve()):
            raise RuntimeError("Invalid staging directory")
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    global_root = cache / "config"
    (global_root / "openspec").mkdir(parents=True, exist_ok=True)
    (global_root / "openspec/config.json").write_text(json.dumps(config), encoding="utf-8")
    build_env["XDG_CONFIG_HOME"] = str(global_root)
    build_env["XDG_DATA_HOME"] = str(cache / "data")
    # Upstream init discovers legacy prompts under the user's home even when
    # only OpenCode is selected. Keep generation away from real user files.
    isolated_home = cache / "home"
    isolated_home.mkdir(exist_ok=True)
    build_env.update(HOME=str(isolated_home), USERPROFILE=str(isolated_home),
                     CODEX_HOME=str(isolated_home / ".codex"),
                     APPDATA=str(isolated_home / "AppData/Roaming"),
                     LOCALAPPDATA=str(isolated_home / "AppData/Local"))
    cli = cli_package / "bin/openspec.js"
    run([str(node), str(cli), "init", "--tools", "opencode", "--profile", "custom"], cwd=staging, env=build_env)
    templates = bundle / "templates"
    if templates.exists():
        shutil.rmtree(templates)
    # Empty business directories are created transactionally by the app.
    # Electron's resource copier excludes .gitkeep; don't fingerprint markers
    # that are neither workflow resources nor needed at runtime.
    shutil.copytree(staging, templates, ignore=shutil.ignore_patterns(".gitkeep"))
    workflows = {}
    for workflow, skill in manifest["workflows"].items():
        command = f"opsx-{workflow}"
        if not (templates / f".opencode/commands/{command}.md").is_file() or not (templates / f".opencode/skills/{skill}/SKILL.md").is_file():
            raise RuntimeError(f"Official generator did not produce {workflow}")
        workflows[command] = skill
    go_asset = manifest["go_assets"][key]
    compiler_archive = download(go_asset, "https://go.dev/dl/", cache, offline=options.offline)
    compiler_root = cache / f"compiler-{manifest['go_version']}-{key}"
    if not (compiler_root / "go/bin" / ("go" + suffix)).exists():
        extract(compiler_archive, compiler_root)
    go = compiler_root / "go/bin" / ("go" + suffix)
    executable_dir = bundle / "bin"
    executable_dir.mkdir(exist_ok=True)
    compiler_env = dict(os.environ, CGO_ENABLED="0", GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off")
    compiler_env["GOROOT"] = str(compiler_root / "go")
    run([str(go), "build", "-trimpath", "-ldflags=-s -w", "-o", str(executable_dir / ("openspec" + suffix)), str(ROOT / "packaging/openspec-launcher/main.go")], env=compiler_env)
    run([str(go), "build", "-trimpath", "-ldflags=-s -w", "-o", str(executable_dir / ("sona-command-runner" + suffix)), str(ROOT / "packaging/command-runner/main.go")], env=compiler_env)
    # Include the Go runtime license used by the native launcher.
    shutil.copy2(compiler_root / "go/LICENSE", licenses / "Go-LICENSE.txt")
    hashes = {file.relative_to(bundle).as_posix(): digest(file) for file in sorted(bundle.rglob("*")) if file.is_file() and file.name != "manifest.json"}
    generated = {"version": manifest["version"], "node_version": manifest["node_version"], "platform": key,
                 "workflows": workflows, "files": hashes}
    (bundle / "manifest.json").write_text(json.dumps(generated, ensure_ascii=False, indent=2), encoding="utf-8")
    check_env = dict(build_env, SONACODE_OPENSPEC_CONFIG_HOME=str(global_root))
    run([str(executable_dir / ("openspec" + suffix)), "--version"], env=check_env)
    print(f"Offline OpenSpec bundle: {bundle}", flush=True)


if __name__ == "__main__":
    main()
