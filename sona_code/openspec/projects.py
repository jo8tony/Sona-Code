"""Transactional project preparation, with ownership and drift protection."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path

import yaml

from sona_code.openspec.bundle import OpenSpecBundle, OpenSpecError, WORKFLOWS, safe_path, sha256


def project_key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sona-openspec-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class OpenSpecProjects:
    def __init__(self, config_path: str, bundle: OpenSpecBundle | None = None):
        self.root = Path(config_path).expanduser().resolve().parent
        self.path = self.root / "openspec-projects.json"
        self.runtime_root = self.root / "openspec-runtime"
        self.bundle = bundle or OpenSpecBundle()
        self._lock = threading.RLock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a mapping")
            for record in data.values():
                if (not isinstance(record, dict) or not isinstance(record.get("enabled"), bool)
                        or not isinstance(record.get("files"), dict) or record.get("workflows") != WORKFLOWS):
                    raise ValueError("invalid project record")
            return data
        except (OSError, ValueError) as exc:
            raise OpenSpecError("OpenSpec 项目记录无法读取，请检查应用配置目录", 503) from exc

    def _save(self, data: dict) -> None:
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    def record(self, project: str | Path) -> dict | None:
        with self._lock:
            return self._read().get(project_key(project))

    def _journal_path(self, project: Path) -> Path:
        key = hashlib.sha256(project_key(project).encode()).hexdigest()
        return self.root / "openspec-transactions" / f"{key}.json"

    def _recover(self, project: Path) -> None:
        journal_path = self._journal_path(project)
        if not journal_path.exists():
            return
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        data = self._read()
        key = project_key(project)
        if data.get(key, {}).get("transaction") == journal["id"]:
            journal_path.unlink()
            return
        for relative, entry in journal["writes"].items():
            path = safe_path(project, relative)
            if path.exists():
                current = sha256(path)
                if current not in {entry["after"], entry["before_hash"]}:
                    raise OpenSpecError("OpenSpec 更新中断后文件已被修改，请检查冲突", 409, [relative])
                if current == entry["before_hash"]:
                    continue
            elif entry["before"] is None:
                continue
            else:
                raise OpenSpecError("OpenSpec 更新中断后文件已被删除，请检查冲突", 409, [relative])
            if entry["before"] is None:
                path.unlink()
            else:
                atomic_write(path, base64.b64decode(entry["before"]))
        journal_path.unlink()

    def status(self, project: str | Path) -> dict:
        project = Path(project).resolve()
        with self._lock:
            self._recover(project)
            record = self.record(project)
            info = self.bundle.info()
            detected = safe_path(project, "openspec").is_dir()
            state = "detected" if detected else "not_enabled"
            conflicts = []
            if record:
                state = "prepared" if record["enabled"] else "disabled"
                for relative, expected in record.get("files", {}).items():
                    target = safe_path(project, relative)
                    if not target.is_file() or sha256(target) != expected:
                        conflicts.append(relative)
                if conflicts and record["enabled"]:
                    state = "conflict"
                elif record["enabled"] and record.get("version") != info.get("version"):
                    state = "upgrade_available"
            return {"state": state, "enabled": bool(record and record["enabled"]),
                    "version": (record or {}).get("version"), "bundled_version": info.get("version"),
                    "available": info["available"], "workflows": (record or {}).get("workflows", WORKFLOWS),
                    "conflicts": conflicts, "error": info.get("error", ""), "terminal_restart_required": False}

    def enable(self, project: str | Path) -> dict:
        project = Path(project).resolve()
        with self._lock:
            if not project.is_dir():
                raise OpenSpecError("项目目录不存在", 404)
            self._recover(project)
            manifest = self.bundle.validate()
            if self.bundle.run(["--version"], project, self.runtime_root).lstrip("v") != manifest["version"]:
                raise OpenSpecError("OpenSpec CLI 与模板版本不一致", 503)
            # Validate custom schemas/configuration using the bundled CLI before writing.
            for relative in ("openspec/config.yaml", "openspec/config.yml"):
                config = safe_path(project, relative)
                if config.exists():
                    try:
                        content = yaml.safe_load(config.read_text(encoding="utf-8"))
                    except (OSError, ValueError, yaml.YAMLError) as exc:
                        raise OpenSpecError("已有 OpenSpec 配置无法读取，请先修复配置", 409, [relative]) from exc
                    if not isinstance(content, dict) or not isinstance(content.get("schema"), str) or not content["schema"].strip():
                        raise OpenSpecError("已有 OpenSpec 配置缺少有效 schema，请先修复配置", 409, [relative])
                    result = json.loads(self.bundle.run(["schema", "validate", content["schema"], "--json"], project, self.runtime_root))
                    if not result.get("valid"):
                        raise OpenSpecError("已有 OpenSpec schema 未通过验证，请先修复配置", 409, [relative])
            data = self._read()
            key = project_key(project)
            previous = data.get(key, {})
            owned = dict(previous.get("files", {}))
            writes, conflicts, checked = {}, [], {}
            for name, expected in manifest["files"].items():
                if not name.startswith(("templates/.opencode/commands/", "templates/.opencode/skills/")):
                    continue
                relative = name.removeprefix("templates/")
                source = safe_path(self.bundle.root, name)
                target = safe_path(project, relative)
                if target.exists():
                    if not target.is_file():
                        conflicts.append(relative)
                        continue
                    current = sha256(target)
                    if current == expected:
                        continue
                    if owned.get(relative) != current:
                        conflicts.append(relative)
                        continue
                    checked[relative] = current
                else:
                    checked[relative] = None
                writes[relative] = source.read_bytes()
                owned[relative] = expected
            if conflicts:
                raise OpenSpecError("已有 OpenSpec 文件与内置版本冲突，已保留原文件", 409, conflicts)
            for relative in ("openspec", "openspec/specs", "openspec/changes"):
                target = safe_path(project, relative)
                if target.exists() and not target.is_dir():
                    raise OpenSpecError("OpenSpec 目录被同名文件占用", 409, [relative])
            yaml_path = safe_path(project, "openspec/config.yaml")
            yml_path = safe_path(project, "openspec/config.yml")
            if not yaml_path.exists() and not yml_path.exists():
                source = safe_path(self.bundle.root, "templates/openspec/config.yaml")
                writes["openspec/config.yaml"] = source.read_bytes()
                checked["openspec/config.yaml"] = None
                # Business configuration is never upgraded along with generated templates.
            transaction = uuid.uuid4().hex
            journal = {"id": transaction, "writes": {}}
            for relative, content in writes.items():
                target = safe_path(project, relative)
                before = target.read_bytes() if target.exists() else None
                before_hash = hashlib.sha256(before).hexdigest() if before is not None else None
                if before_hash != checked[relative]:
                    raise OpenSpecError("准备期间 OpenSpec 文件被修改，已保留原文件", 409, [relative])
                journal["writes"][relative] = {"before": base64.b64encode(before).decode() if before is not None else None,
                                               "before_hash": before_hash,
                                               "after": hashlib.sha256(content).hexdigest()}
            journal_path = self._journal_path(project)
            atomic_write(journal_path, json.dumps(journal).encode())
            try:
                for relative in ("openspec/specs", "openspec/changes"):
                    safe_path(project, relative).mkdir(parents=True, exist_ok=True)
                for relative, content in writes.items():
                    atomic_write(safe_path(project, relative), content)
                data[key] = {"path": str(project), "enabled": True, "version": manifest["version"],
                             "files": owned, "workflows": manifest["workflows"], "transaction": transaction}
                self._save(data)
            except BaseException:
                self._recover(project)
                raise
            journal_path.unlink()
            return self.status(project)

    def disable(self, project: str | Path) -> dict:
        with self._lock:
            data = self._read()
            key = project_key(project)
            if key not in data:
                raise OpenSpecError("该项目尚未启用内置 OpenSpec", 409)
            data[key]["enabled"] = False
            self._save(data)
            return self.status(project)

    def workflow(self, project: str, command: str) -> str | None:
        record = self.record(project)
        if not record:
            return None
        workflows = record["workflows"]
        return workflows.get(command) or (command if command in workflows.values() else None)

    def environment(self, project: str, kind: str, env: dict[str, str]) -> dict[str, str]:
        if self.bundle.executable.is_file():
            env["PATH"] = str(self.bundle.executable.parent) + os.pathsep + env.get("PATH", "")
            env["SONACODE_OPENSPEC_CONFIG_HOME"] = str(self.runtime_root / "config")
            env["SONACODE_OPENSPEC_DATA_HOME"] = str(self.runtime_root / "data")
        record = self.record(project)
        if kind == "opencode" and record and not record["enabled"]:
            inline = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
            permission = inline.setdefault("permission", {})
            if isinstance(permission, str):
                permission = {"*": permission}
                inline["permission"] = permission
            skills = permission.setdefault("skill", {})
            if isinstance(skills, str):
                skills = {"*": skills}
                permission["skill"] = skills
            skills.update({skill: "deny" for skill in record["workflows"].values()})
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline, ensure_ascii=False)
        return env
