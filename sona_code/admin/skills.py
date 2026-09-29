"""Import and manage local OpenCode skill directories."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

import yaml

from sona_code.admin.opencode_config import (
    CONFIG_LOCK, isolated_config_dir, import_source_dirs, parse_jsonc, patch_jsonc, write_global_config,
)

SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
WORKSPACE_COMMANDS = {
    "help", "new", "compact", "summarize", "models", "agents", "skills", "stop", "settings",
}
WINDOWS_RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


def _safe_name(name: str) -> str:
    if len(name) > 64 or not SKILL_NAME.fullmatch(name):
        raise ValueError("技能名称须为 1–64 位小写字母、数字或单个连字符")
    # Windows reserves device names even when used as directory names.
    if name in WINDOWS_RESERVED_NAMES:
        raise ValueError("技能名称不能使用系统保留名称")
    return name


def read_skill(path: Path) -> dict:
    content = (path / "SKILL.md").read_text(encoding="utf-8-sig")
    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", content, re.S)
    if not match:
        raise ValueError("SKILL.md 必须包含 YAML frontmatter")
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError("SKILL.md 的 YAML frontmatter 格式错误") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
        raise ValueError("SKILL.md 缺少 name 字段")
    name = _safe_name(metadata["name"])
    description = metadata.get("description")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
        raise ValueError("SKILL.md 的 description 须为 1–1024 个字符")
    return {"name": name, "description": description.strip()}


class SkillStore:
    """App copies and read-only external sources, using native skill permissions."""

    def __init__(self, root: Path | None = None, external_root: Path | None = None):
        self.root = (root or isolated_config_dir()).expanduser().resolve()
        self.enabled_dir = self.root / "skills"
        self.disabled_dir = self.root / "skills-disabled"
        self.external_root = (external_root or import_source_dirs()[0]).expanduser().resolve()
        self._lock = threading.RLock()
        self.migration_warnings: list[str] = []

    def _config(self) -> tuple[Path, str, dict]:
        path = next((self.root / name for name in ("opencode.jsonc", "opencode.json")
                     if (self.root / name).exists()), self.root / "opencode.jsonc")
        content = path.read_bytes().decode("utf-8") if path.exists() else "{}\n"
        return path, content, parse_jsonc(content)

    def permission(self, name: str) -> str:
        _, _, config = self._config()
        permission = config.get("permission", {})
        rules = permission.get("skill", permission.get("*", "allow")) if isinstance(permission, dict) else permission
        if isinstance(rules, str):
            return rules
        if not isinstance(rules, dict):
            raise ValueError("OpenCode 的 permission.skill 配置无效")
        action = permission.get("*", "allow") if isinstance(permission, dict) else "allow"
        for pattern, value in rules.items():
            if fnmatch.fnmatchcase(name, pattern):
                action = value
        return action

    def _permission(self, name: str, enabled: bool) -> None:
        with CONFIG_LOCK:
            path, content, _ = self._config()
            changed = patch_jsonc(content, ["permission", "skill", name], "allow" if enabled else "deny")
            validate = parse_jsonc(changed)
            if validate["permission"]["skill"][name] != ("allow" if enabled else "deny"):
                raise ValueError("技能权限配置写入失败")
            write_global_config(path, changed)

    def _prepare(self) -> None:
        if self.enabled_dir.is_symlink() or self.disabled_dir.is_symlink():
            raise ValueError("应用技能存放目录不能是符号链接")
        self.enabled_dir.mkdir(parents=True, exist_ok=True)

    def migrate(self) -> None:
        """Run before starting workspace servers; interrupted migrations are retryable."""
        with self._lock:
            self._prepare()
            self.migration_warnings = []
            if not self.disabled_dir.is_dir():
                return
            for path in sorted(self.disabled_dir.iterdir()):
                if path.is_symlink() or not path.is_dir():
                    continue
                try:
                    name = read_skill(path)["name"]
                except (OSError, ValueError) as exc:
                    self.migration_warnings.append(f"旧停用技能 {path.name} 无法迁移：{exc}")
                    continue
                destination = self.enabled_dir / name
                if destination.exists():
                    self.migration_warnings.append(f"旧停用技能 {name} 与应用技能重名，已保留旧副本，请处理冲突")
                    continue
                self._permission(name, False)
                path.rename(destination)
            if not any(self.disabled_dir.iterdir()):
                self.disabled_dir.rmdir()

    def list(self) -> dict:
        with self._lock:
            self._prepare()
            items = []
            roots = [(self.enabled_dir, "app"), (self.root / "skill", "app")]
            if self.external_root != self.root:
                roots.extend((self.external_root / folder, "external") for folder in ("skills", "skill"))
            for directory, source in roots:
                if not directory.is_dir() or directory.is_symlink():
                    continue
                for folder, dirs, files in os.walk(directory, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_symlink())
                    path = Path(folder)
                    if "SKILL.md" not in files or (path / "SKILL.md").is_symlink():
                        continue
                    managed = source == "app" and path.parent == self.enabled_dir
                    item = {"id": path.name if managed else "ext_" + hashlib.sha256(str(path).encode()).hexdigest()[:24],
                            "path": str(path), "source": source, "deletable": managed}
                    try:
                        item.update(read_skill(path))
                    except (OSError, ValueError) as exc:
                        item.update(name=path.name, description="", error=str(exc))
                    item["permission"] = self.permission(item["name"])
                    item["enabled"] = item["permission"] != "deny"
                    items.append(item)
            seen = set()
            for item in items:
                if item["name"] in seen:
                    item["conflict"] = "同名技能已由优先来源提供"
                else:
                    seen.add(item["name"])
            return {"items": sorted(items, key=lambda item: (item["name"], item["source"])),
                    "directory": str(self.enabled_dir), "external_directory": str(self.external_root), "total": len(items),
                    "warnings": list(self.migration_warnings)}

    def external_paths(self) -> list[str]:
        return [item["path"] for item in self.list()["items"]
                if item["source"] == "external" and not item.get("error") and not item.get("conflict")]

    def _find(self, skill_id: str) -> dict:
        if not skill_id.startswith("ext_"):
            _safe_name(skill_id)
        item = next((item for item in self.list()["items"] if item["id"] == skill_id), None)
        if item is None:
            raise FileNotFoundError("技能不存在")
        return item

    def add(self, source: str) -> dict:
        with self._lock:
            self._prepare()
            path = Path(source).expanduser()
            if not path.is_absolute() or not path.is_dir() or path.is_symlink():
                raise ValueError("请指定包含 SKILL.md 的技能目录绝对路径")
            path = path.resolve()
            if not (path / "SKILL.md").is_file():
                raise ValueError("技能目录中缺少 SKILL.md")
            for directory in (self.enabled_dir, self.disabled_dir):
                if path == directory.resolve() or directory.resolve().is_relative_to(path):
                    raise ValueError("源目录不能包含应用技能存放目录")
            # Preserve all resources, but never follow links outside the selected tree.
            for child in path.rglob("*"):
                if child.is_symlink() or not (child.is_dir() or child.is_file()):
                    raise ValueError("技能目录不能包含符号链接或特殊文件")
                if child.name == "SKILL.md" and child.parent != path:
                    raise ValueError("请选择单个技能目录，不能包含嵌套的 SKILL.md")
            metadata = read_skill(path)
            name = metadata["name"]
            if name in WORKSPACE_COMMANDS:
                raise ValueError("技能名称与工作区快捷命令重名，请修改技能 name")
            if any((item["name"] == name or item["id"] == name) and item["source"] == "app" for item in self.list()["items"]):
                raise FileExistsError("已存在同名技能，请先删除旧副本")
            destination = self.enabled_dir / name
            # Stage outside both scan roots so OpenCode never discovers a partial copy.
            with tempfile.TemporaryDirectory(prefix=".skill-import-", dir=self.root) as temporary:
                staging = Path(temporary) / name
                shutil.copytree(path, staging, symlinks=True)
                if any(child.is_symlink() for child in staging.rglob("*")):
                    raise ValueError("技能目录不能包含符号链接")
                if read_skill(staging) != metadata:
                    raise ValueError("复制时技能定义发生变化，请重试")
                if destination.exists() or (self.disabled_dir / name).exists():
                    raise FileExistsError("已存在同名技能")
                staging.rename(destination)
            try:
                self._permission(name, True)
            except (OSError, ValueError):
                shutil.rmtree(destination)
                raise
            return {"id": name, **metadata, "path": str(destination), "enabled": True, "source": "app"}

    def set_enabled(self, skill_id: str, enabled: bool) -> dict:
        with self._lock:
            item = self._find(skill_id)
            if item.get("conflict"):
                raise ValueError("同名技能的权限由优先来源控制")
            if enabled:
                read_skill(Path(item["path"]))
            self._permission(item["name"], enabled)
            return {"ok": True, "id": skill_id, "enabled": enabled}

    def delete(self, skill_id: str) -> dict:
        with self._lock:
            item = self._find(skill_id)
            if not item["deletable"]:
                raise ValueError("只能删除通过本应用导入的技能副本")
            shutil.rmtree(item["path"])
            return {"ok": True, "id": skill_id}

    def record_use(self, project: str, session: str, message_id: str, skill: dict, arguments: str) -> None:
        with self._lock:
            path = self._uses_path(project, session)
            uses = json.loads(path.read_text()) if path.exists() else {}
            uses[message_id] = {"name": skill["name"], "path": skill["path"], "arguments": arguments}
            write_global_config(path, json.dumps(uses, ensure_ascii=False))

    def _uses_path(self, project: str, session: str) -> Path:
        key = hashlib.sha256((project + "\0" + session).encode()).hexdigest()
        return self.root / ".skill-uses" / (key + ".json")

    def annotate_messages(self, project: str, session: str, messages: list) -> list:
        with self._lock:
            path = self._uses_path(project, session)
            uses = json.loads(path.read_text()) if path.exists() else {}
            for message in messages:
                info = message.get("info", {})
                if info.get("role") == "user" and info.get("id") in uses:
                    message["skillUse"] = uses[info["id"]]
            return messages
