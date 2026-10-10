"""Export or verify a Windows x64 dependency kit after an online desktop build."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ["package-lock.json", "pyproject.toml", "packaging/opencode.json", "packaging/openspec.json",
          "scripts/prepare-opencode.py", "packaging/opencode-locale-plugin.mjs", "scripts/prepare-openspec.py",
          "packaging/openspec-package/package.json", "packaging/openspec-package/package-lock.json",
          "packaging/openspec-launcher/main.go", "packaging/openspec-launcher/offline.cjs", "packaging/command-runner/main.go",
          "packaging/opencode-sdk/package.json", "packaging/opencode-sdk/package-lock.json", "scripts/prepare-native-sdk.py"]


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def checked_path(root: Path, name: str) -> Path:
    if "\\" in name or ":" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
        raise RuntimeError(f"Invalid manifest path: {name}")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RuntimeError(f"Invalid manifest path: {name}")
    return path


def verify_kit(kit: Path, source: Path) -> dict:
    manifest = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
    for section, base in (("files", kit), ("source_inputs", source)):
        if not manifest.get(section):
            raise RuntimeError(f"Empty offline kit section: {section}")
        for name, expected in manifest[section].items():
            path = checked_path(base, name)
            if not path.is_file() or digest(path) != expected:
                raise RuntimeError(f"Offline kit missing or mismatched: {name}")
    return manifest


def prepare(destination: Path, python: str) -> None:
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("This kit targets Windows x64; prepare it on Windows x64")
    destination = destination.resolve()
    if not destination.is_relative_to((ROOT / "build").resolve()) or destination == (ROOT / "build").resolve():
        raise RuntimeError("Kit output must be a subdirectory of the project build directory")
    if destination.exists():
        raise RuntimeError(f"Kit already exists; choose a new output directory: {destination}")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required for preparation")
    subprocess.run([node, str(ROOT / "scripts/build-editor.mjs"), "--check"], check=True)
    subprocess.run([python, str(ROOT / 'scripts/prepare-native-sdk.py'), '--check'], check=True)
    sources = {"node_modules": ROOT / "node_modules", "openspec": ROOT / "build/openspec",
               "binaries/opencode.exe": ROOT / "src-tauri/binaries/opencode-x86_64-pc-windows-msvc.exe",
               "tools/node.exe": Path(node), "electron-builder-cache": ROOT / ".runtime/electron-builder-cache",
               "opencode-sdk": ROOT / 'build/opencode-sdk'}
    required = [ROOT / "node_modules/electron/dist/electron.exe", ROOT / "node_modules/esbuild/bin/esbuild",
                ROOT / "build/openspec/manifest.json", ROOT / "build/openspec/bin/sona-command-runner.exe",
                sources["binaries/opencode.exe"], sources["electron-builder-cache"]]
    if any(not path.exists() for path in required):
        raise RuntimeError("Finish npm ci and the Windows Electron build with ELECTRON_BUILDER_CACHE=.runtime/electron-builder-cache first")
    destination.mkdir(parents=True)
    for name, source in sources.items():
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(".cache"))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    # Wheels are pinned from the environment that produced the verified sidecar.
    frozen = subprocess.check_output([python, "-m", "pip", "freeze", "--all"], text=True)
    requirements = [line for line in frozen.splitlines() if line and not line.startswith(("#", "-e ", "sona-code"))]
    if not any(line.lower().startswith("wheel==") for line in requirements):
        subprocess.run([python, "-m", "pip", "install", "wheel"], check=True)
        version = subprocess.check_output([python, "-c", "import importlib.metadata; print(importlib.metadata.version('wheel'))"], text=True).strip()
        requirements.append(f"wheel=={version}")
    requirement_file = destination / "requirements.txt"
    requirement_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    subprocess.run([python, "-m", "pip", "download", "--only-binary=:all:", "-r", str(requirement_file),
                    "--dest", str(destination / "wheels")], check=True)
    # The prepared tools are platform-specific; future app edits can reuse them until inputs change.
    manifest = {"platform": "windows-x64", "python": list(sys.version_info[:2]),
                "source_inputs": {name: digest(ROOT / name) for name in INPUTS},
                "files": {path.relative_to(destination).as_posix(): digest(path)
                          for path in sorted(destination.rglob("*")) if path.is_file()}}
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    verify_kit(destination, ROOT)
    archive = ROOT / "dist" / "Sona-Code-Windows-x64-offline-build.zip"
    archive.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                output.write(path, "sona-offline-kit/" + path.relative_to(destination).as_posix())
    archive.with_suffix(".zip.sha256").write_text(digest(archive) + "  " + archive.name + "\n")
    print(f"Offline kit: {archive} ({archive.stat().st_size / 1024 / 1024:.1f} MiB)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--kit", type=Path, default=ROOT / "build/offline-kit")
    parser.add_argument("--python", default=sys.executable)
    options = parser.parse_args()
    if options.prepare:
        prepare(options.kit, options.python)
    else:
        manifest = verify_kit(options.kit.resolve(), ROOT)
        print(f"Verified {len(manifest['files'])} offline dependency files", flush=True)


if __name__ == "__main__":
    main()
