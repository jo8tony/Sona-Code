"""Locate and execute the fixed OpenSpec resources shipped with the desktop app."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path, PurePosixPath

BUNDLE_ENV = "SONACODE_BUNDLED_OPENSPEC"
WORKFLOWS = {
    "opsx-propose": "openspec-propose", "opsx-explore": "openspec-explore",
    "opsx-new": "openspec-new-change", "opsx-continue": "openspec-continue-change",
    "opsx-ff": "openspec-ff-change", "opsx-apply": "openspec-apply-change",
    "opsx-update": "openspec-update-change", "opsx-sync": "openspec-sync-specs",
    "opsx-archive": "openspec-archive-change", "opsx-bulk-archive": "openspec-bulk-archive-change",
    "opsx-verify": "openspec-verify-change", "opsx-onboard": "openspec-onboard",
}


class OpenSpecError(Exception):
    def __init__(self, detail: str, status: int = 400, conflicts: list[str] | None = None):
        super().__init__(detail)
        self.detail, self.status, self.conflicts = detail, status, conflicts or []


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
        return hasher.hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if parts.is_absolute() or not parts.parts or any(part in {"", ".", ".."} or "\\" in part or ":" in part for part in relative.split("/")):
        raise OpenSpecError("OpenSpec 资源路径无效")
    path = root
    for part in parts.parts:
        path = path / part
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
            raise OpenSpecError("OpenSpec 目录不能包含符号链接或目录联接")
    if not path.resolve().is_relative_to(root.resolve()):
        raise OpenSpecError("OpenSpec 文件不能位于项目或资源目录之外")
    return path


class OpenSpecBundle:
    def __init__(self, root: Path | None = None):
        self.root = (root or Path(os.environ.get(BUNDLE_ENV) or Path(__file__).resolve().parents[2] / "build/openspec")).expanduser().resolve()
        self._lock = threading.RLock()
        self._verified: tuple | None = None

    @property
    def executable(self) -> Path:
        return self.root / "bin" / ("openspec.exe" if os.name == "nt" else "openspec")

    def manifest(self) -> dict:
        try:
            path = safe_path(self.root, "manifest.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(data.get("files"), dict) or data.get("workflows") != WORKFLOWS
                    or not isinstance(data.get("version"), str) or not isinstance(data.get("node_version"), str)):
                raise ValueError("invalid bundle manifest")
            return data
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise OpenSpecError("当前客户端缺少有效的 OpenSpec 内置资源，请更新或重新安装客户端", 503) from exc

    def validate(self) -> dict:
        with self._lock:
            manifest = self.manifest()
            paths = {relative: safe_path(self.root, relative) for relative in manifest["files"]}
            try:
                fingerprint = tuple((name, manifest["files"][name], path.stat().st_size, path.stat().st_mtime_ns) for name, path in paths.items())
                if fingerprint != self._verified:
                    for name, path in paths.items():
                        if not path.is_file() or sha256(path) != manifest["files"][name]:
                            raise OpenSpecError("OpenSpec 内置资源校验失败，请更新或重新安装客户端", 503)
                    self._verified = fingerprint
                if self.executable.relative_to(self.root).as_posix() not in paths:
                    raise OpenSpecError("OpenSpec 启动器缺失", 503)
            except OSError as exc:
                raise OpenSpecError("OpenSpec 内置资源不完整，请更新或重新安装客户端", 503) from exc
            return manifest

    def info(self) -> dict:
        try:
            data = self.manifest()
            available = self.executable.is_file()
            return {"available": available, "version": data["version"], "node_version": data["node_version"],
                    "workflows": data["workflows"], "error": "" if available else "OpenSpec 启动器缺失"}
        except OpenSpecError as exc:
            return {"available": False, "version": None, "workflows": WORKFLOWS, "error": exc.detail}

    def run(self, arguments: list[str], project: Path, config_root: Path, *, timeout: float = 30) -> str:
        self.validate()
        env = dict(os.environ, SONACODE_OPENSPEC_CONFIG_HOME=str(config_root / "config"),
                   SONACODE_OPENSPEC_DATA_HOME=str(config_root / "data"), OPENSPEC_TELEMETRY="0",
                   OPENSPEC_NO_UPDATE_CHECK="1")
        env.pop("NODE_OPTIONS", None)
        try:
            result = subprocess.run([str(self.executable), *arguments], cwd=project, env=env,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        except (OSError, subprocess.SubprocessError) as exc:
            raise OpenSpecError("OpenSpec 内置运行时启动失败或超时", 503) from exc
        if result.returncode:
            raise OpenSpecError("OpenSpec 执行失败：" + (result.stderr or result.stdout).strip()[:1000], 409)
        return result.stdout.strip()

    def command_template(self, command: str) -> str:
        relative = f"templates/.opencode/commands/{command}.md"
        data = self.manifest()
        path = safe_path(self.root, relative)
        if sha256(path) != data["files"].get(relative):
            raise OpenSpecError("OpenSpec 命令模板校验失败", 503)
        content = path.read_text(encoding="utf-8")
        if content.startswith("---\n"):
            content = content.split("\n---\n", 1)[1]
        return content.strip()
