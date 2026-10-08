"""Offline resources, project ownership, recovery, native verification and isolation."""
import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.config import AppConfig, UpstreamConfig
from sona_code.openspec.bundle import OpenSpecBundle, OpenSpecError, WORKFLOWS, safe_path, sha256
from sona_code.openspec.projects import OpenSpecProjects
from sona_code.workspace.manager import WorkspaceError, WorkspaceManager


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    files = {"bin/openspec.exe" if os.name == "nt" else "bin/openspec": b"launcher",
             "templates/openspec/config.yaml": b"schema: spec-driven\n"}
    for command, skill in WORKFLOWS.items():
        files[f"templates/.opencode/commands/{command}.md"] = f"---\ndescription: {command}\n---\nExecute {command}\n".encode()
        files[f"templates/.opencode/skills/{skill}/SKILL.md"] = f"---\nname: {skill}\ndescription: workflow\n---\nExecute {skill}\n".encode()
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {"version": "1.14.1", "node_version": "22.23.3", "workflows": WORKFLOWS,
                "files": {name: sha256(root / name) for name in files}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    result = OpenSpecBundle(root)
    result.run = lambda args, *_: "1.14.1" if args == ["--version"] else '{"valid":true}'
    return result


@pytest.fixture
def store(tmp_path, bundle):
    return OpenSpecProjects(str(tmp_path / "app/config.json"), bundle)


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "中文 project"
    path.mkdir()
    return path


def test_enable_idempotent_disable_preserves_business_files(store, project):
    assert store.status(project)["state"] == "not_enabled"
    assert store.enable(project)["state"] == "prepared"
    config = project / "openspec/config.yaml"
    config.write_text("schema: spec-driven\ncontext: custom business rules\n")
    spec = project / "openspec/specs/customer/spec.md"
    spec.parent.mkdir()
    spec.write_text("Customer requirements")
    command = project / ".opencode/commands/opsx-propose.md"
    before = command.stat().st_mtime_ns
    store.enable(project)
    assert command.stat().st_mtime_ns == before
    assert "custom business" in config.read_text()
    assert store.disable(project)["state"] == "disabled"
    assert spec.read_text() == "Customer requirements"
    inline = store.environment(str(project), "opencode", {"PATH": "original"})
    assert json.loads(inline["OPENCODE_CONFIG_CONTENT"])["permission"]["skill"]["openspec-propose"] == "deny"
    assert inline["PATH"].split(os.pathsep)[-1] == "original"
    assert store.enable(project)["state"] == "prepared"


def test_conflict_preflight_writes_nothing(store, project):
    path = project / ".opencode/commands/opsx-propose.md"
    path.parent.mkdir(parents=True)
    path.write_text("My custom command")
    with pytest.raises(OpenSpecError) as error:
        store.enable(project)
    assert error.value.status == 409
    assert error.value.conflicts == [".opencode/commands/opsx-propose.md"]
    assert not (project / "openspec").exists()
    assert list(project.rglob("*.md")) == [path]
    assert not store.path.exists()


def test_modified_owned_templates_block_update(store, project):
    store.enable(project)
    path = project / ".opencode/commands/opsx-propose.md"
    path.write_text("User edit")
    assert store.status(project)["state"] == "conflict"
    with pytest.raises(OpenSpecError):
        store.enable(project)
    assert path.read_text() == "User edit"


def test_older_owned_templates_upgrade_from_bundle_only(store, project):
    store.enable(project)
    from sona_code.openspec.projects import project_key
    data = store._read()
    record = data[project_key(project)]
    record["version"] = "1.6.0"
    relative = ".opencode/commands/opsx-propose.md"
    path = project / relative
    path.write_text("Old owned template")
    record["files"][relative] = sha256(path)
    store._save(data)
    config = project / "openspec/config.yaml"
    config.write_text("schema: spec-driven\ncontext: preserve local business rules\n")
    assert store.status(project)["state"] == "upgrade_available"
    assert store.enable(project)["version"] == "1.14.1"
    assert "Execute opsx-propose" in path.read_text()
    assert "preserve local business" in config.read_text()


def test_existing_identical_files_remain_external(store, project, bundle):
    command = ".opencode/commands/opsx-propose.md"
    path = project / command
    path.parent.mkdir(parents=True)
    path.write_bytes((bundle.root / "templates" / command).read_bytes())
    store.enable(project)
    assert command not in store.record(project)["files"]


def test_invalid_yaml_or_missing_schema_preserved(store, project):
    path = project / "openspec/config.yaml"
    path.parent.mkdir()
    for data in ("schema: [invalid", "context: custom"):
        path.write_text(data)
        with pytest.raises(OpenSpecError):
            store.enable(project)
        assert path.read_text() == data
        assert not (project / ".opencode").exists()


def test_failed_state_commit_rolls_back_files(store, project, monkeypatch):
    def fail(_):
        raise OSError("injected disk failure")
    monkeypatch.setattr(store, "_save", fail)
    with pytest.raises(OSError):
        store.enable(project)
    assert not list(project.rglob("*.md"))
    assert not (project / "openspec/config.yaml").exists()
    assert not store._journal_path(project).exists()


def test_crash_journal_recovers_only_expected_files(store, project):
    path = project / "file.md"
    path.write_text("transaction output")
    journal = store._journal_path(project)
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({"id": "uncommitted", "writes": {"file.md": {
        "before": None, "before_hash": None, "after": sha256(path)}}}))
    path.write_text("user edited after crash")
    with pytest.raises(OpenSpecError) as error:
        store.status(project)
    assert error.value.status == 409
    assert path.read_text() == "user edited after crash"
    path.write_text("transaction output")
    store.status(project)
    assert not path.exists() and not journal.exists()


@pytest.mark.parametrize("relative", ["../escape", "a/../b", "/absolute", "C:/escape", "a\\b", "a/./b", "a//b"])
def test_reject_unsafe_paths(project, relative):
    with pytest.raises(OpenSpecError):
        safe_path(project, relative)


def test_reject_directory_junction(store, project, tmp_path):
    target = tmp_path / "external"
    target.mkdir()
    link = project / ".opencode"
    if os.name == "nt":
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    with pytest.raises(OpenSpecError):
        store.enable(project)
    assert not list(target.iterdir())


def test_resource_corruption_and_manifest_change_fail_validation(bundle):
    bundle.validate()
    path = bundle.root / "templates/.opencode/commands/opsx-propose.md"
    path.write_text("corrupt")
    with pytest.raises(OpenSpecError):
        bundle.validate()
    assert not OpenSpecBundle(bundle.root / "missing").info()["available"]


@pytest.mark.asyncio
async def test_project_change_blocks_only_target_dispatch(project, tmp_path):
    manager = WorkspaceManager()
    other = str(tmp_path / "other")
    async def apply():
        with pytest.raises(WorkspaceError):
            async with manager.task_dispatch(str(project)):
                pytest.fail("target dispatch was permitted")
        async with manager.task_dispatch(other):
            assert manager._task_requests == 1
        return {"ok": True}
    assert await manager.update_project_configuration(str(project), apply) == {"ok": True}
    async with manager.task_dispatch(str(project)):
        with pytest.raises(WorkspaceError):
            await manager.update_project_configuration(str(project), apply)
    assert not manager._changing and manager._task_requests == 0


def test_routes_verify_native_templates_locations_and_disable_aliases(store, project, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "isolated"))
    monkeypatch.setenv("SONACODE_ORIGINAL_XDG_CONFIG_HOME", str(tmp_path / "original"))
    app = create_app(AppConfig(default_upstream="test", upstreams=[UpstreamConfig(name="test", base_url="http://127.0.0.1:9001")]), str(tmp_path / "app/config.json"))
    app.state.runtime.openspec = store
    app.state.runtime.config.model_settings.source = "native"
    app.state.runtime.config.model_settings.show_native_models = True
    changed = {"template": False, "location": False}
    commands = [{"name": command, "template": store.bundle.command_template(command)} for command in WORKFLOWS]
    skills = [{"name": skill, "description": "Workflow", "location": str(project / ".opencode/skills" / skill / "SKILL.md")}
              for skill in WORKFLOWS.values()]
    async def request(path, cfg, method, endpoint, **kwargs):
        if endpoint == "/command":
            return [{**item, "template": "wrong" if changed["template"] else item["template"]} for item in commands] + [
                {"name": skill, "source": "skill"} for skill in WORKFLOWS.values()]
        if endpoint == "/skill":
            return [{**item, "location": str(tmp_path / "global/SKILL.md") if changed["location"] else item["location"]} for item in skills]
        if endpoint == "/agent":
            return []
        return {}
    app.state.runtime.workspace.request = request
    with TestClient(app) as client:
        prefix = "/__recorder/api/workspace/projects"
        pid = client.post(prefix, json={"path": str(project)}).json()["id"]
        endpoint = f"{prefix}/{pid}/openspec"
        assert client.post(endpoint + "/enable").json()["state"] == "ready"
        for field in changed:
            changed[field] = True
            result = client.get(endpoint).json()
            assert result["state"] == "error" and len(result["unavailable"]) == 12
            changed[field] = False
        changed["location"] = True
        assert client.post(f"{prefix}/{pid}/sessions/ses_1/command", json={"command": "openspec-propose"}).status_code == 409
        changed["location"] = False
        app.state.runtime.skills._permission("openspec-propose", False)
        assert client.post(f"{prefix}/{pid}/sessions/ses_1/command", json={"command": "opsx-propose"}).status_code == 409
        app.state.runtime.skills._permission("openspec-propose", True)
        assert len(client.get(f"{prefix}/{pid}/commands").json()) == 24
        assert client.post(endpoint + "/disable").json()["state"] == "disabled"
        assert client.get(f"{prefix}/{pid}/commands").json() == []
        assert client.get(f"{prefix}/{pid}/skills").json()["items"] == []
        assert client.post(f"{prefix}/{pid}/sessions/ses_1/command", json={"command": "opsx-propose"}).status_code == 409
