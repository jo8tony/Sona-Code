"""Skill copies, enablement, workspace refresh, and native command delegation."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from sona_code.admin.skills import SkillStore
from sona_code.app import create_app
from sona_code.config import default_config
from sona_code.workspace.manager import OpenCodeServer, WorkspaceError, WorkspaceManager


@pytest.fixture(autouse=True)
def isolated_skill_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setenv("OPENCODE_TEST_HOME", str(tmp_path / "home"))
    for flag in ("OPENCODE_DISABLE_EXTERNAL_SKILLS", "OPENCODE_DISABLE_CLAUDE_CODE", "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"):
        monkeypatch.delenv(flag, raising=False)


def make_skill(root: Path, name: str = "code-review") -> Path:
    source = root / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: >-\n  Review code and\n  explain findings.\n---\nUse references/checklist.md.\n",
        encoding="utf-8",
    )
    (source / "references").mkdir()
    (source / "references/checklist.md").write_text("checklist", encoding="utf-8")
    return source


def test_other_agent_skills_are_read_in_place_without_duplicate_injection(tmp_path):
    claude = make_skill(Path.home() / ".claude/skills", "claude-review")
    agents = make_skill(Path.home() / ".agents/skills/team", "agent-review")
    store = SkillStore(tmp_path / "app", external_root=tmp_path / "original")
    items = store.list()["items"]
    assert {item["origin"] for item in items} == {"Claude Code", "Agents"}
    assert all(not item["deletable"] and item["native_discovery"] for item in items)
    assert store.external_paths() == []
    assert list(store.enabled_dir.iterdir()) == []
    before = (claude / "SKILL.md").read_bytes()
    item = next(item for item in items if item["name"] == "claude-review")
    store.set_enabled(item["id"], False)
    assert store.permission("claude-review") == "deny"
    assert (claude / "SKILL.md").read_bytes() == before
    with pytest.raises(ValueError, match="只能删除"):
        store.delete(item["id"])
    assert agents.is_dir()
    store.add(str(claude))
    duplicate = [item for item in store.list()["items"] if item["name"] == "claude-review"]
    assert next(item for item in duplicate if item["source"] == "external")["conflict"]
    assert not next(item for item in duplicate if item["source"] == "app").get("conflict")


@pytest.mark.parametrize("flag,expected", [
    ("OPENCODE_DISABLE_EXTERNAL_SKILLS", set()),
    ("OPENCODE_DISABLE_CLAUDE_CODE", {"agent-review"}),
    ("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", {"agent-review"}),
])
def test_native_external_discovery_flags_are_respected(tmp_path, monkeypatch, flag, expected):
    make_skill(Path.home() / ".claude/skills", "claude-review")
    make_skill(Path.home() / ".agents/skills", "agent-review")
    monkeypatch.setenv(flag, "1")
    store = SkillStore(tmp_path / "app", external_root=tmp_path / "original")
    assert {item["name"] for item in store.list()["items"]} == expected


def test_external_metadata_matches_native_optional_description(tmp_path):
    skill = make_skill(Path.home() / ".agents/skills", "native-review")
    (skill / "SKILL.md").write_text("---\nname: native_review\n---\nReview")
    item = SkillStore(tmp_path / "app", external_root=tmp_path / "original").list()["items"][0]
    assert item["name"] == "native_review" and item["description"] == ""
    assert not item.get("error")


def test_native_external_precedence_uses_actual_loaded_location(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("SONACODE_ORIGINAL_XDG_CONFIG_HOME", str(tmp_path / "original"))
    make_skill(Path.home() / ".claude/skills")
    project = tmp_path / "project"
    selected = make_skill(project / ".agents/skills")
    app = create_app(default_config(), str(tmp_path / "proxy.json"))
    app.state.runtime.config.model_settings.source = "native"
    app.state.runtime.config.model_settings.show_native_models = True
    invoked = []

    async def request(project, config, method, endpoint, *, body=None, **kwargs):
        if endpoint == "/command":
            return [{"name": "code-review", "source": "skill"}]
        if endpoint == "/skill":
            return [{"name": "code-review", "location": str(selected / "SKILL.md"), "description": "Project skill"}]
        if endpoint == "/agent":
            return []
        invoked.append(body)
        return {"ok": True}

    app.state.runtime.workspace.request = request
    with TestClient(app) as client:
        prefix = "/__recorder/api/workspace/projects"
        project_id = client.post(prefix, json={"path": str(project)}).json()["id"]
        popup = client.get(f"{prefix}/{project_id}/skills").json()
        assert popup["items"][0]["path"] == str(selected)
        assert not popup["unavailable"]
        command = f"{prefix}/{project_id}/sessions/ses_1/command"
        assert client.post(command, json={"command": "code-review"}).status_code == 200
        app.state.runtime.skills._permission("code-review", False)
        assert client.get(f"{prefix}/{project_id}/skills").json()["items"] == []
        assert client.post(command, json={"command": "code-review"}).status_code == 409


def test_skill_copy_enable_disable_delete_and_restart(tmp_path):
    source = make_skill(tmp_path / "user")
    root = tmp_path / "app/opencode"
    store = SkillStore(root)
    added = store.add(str(source))
    assert added["description"] == "Review code and explain findings."
    installed = root / "skills/code-review"
    assert (installed / "references/checklist.md").read_text() == "checklist"
    (source / "references/checklist.md").write_text("source changed")
    assert (installed / "references/checklist.md").read_text() == "checklist"
    store.set_enabled("code-review", False)
    assert (installed / "SKILL.md").is_file()
    assert not (root / "skills-disabled").exists()
    assert '"code-review": "deny"' in (root / "opencode.jsonc").read_text()
    restarted = SkillStore(root)
    assert restarted.list()["items"][0]["enabled"] is False
    with pytest.raises(FileExistsError):
        restarted.add(str(source))
    restarted.set_enabled("code-review", True)
    assert installed.is_dir()
    restarted.delete("code-review")
    assert restarted.list()["items"] == []
    assert source.is_dir()
    with pytest.raises(FileNotFoundError):
        restarted.delete("code-review")


@pytest.mark.parametrize("name", ["../escape", "UPPER", "bad--name", "con", "a" * 65])
def test_invalid_skill_names_cannot_escape_storage(tmp_path, name):
    source = make_skill(tmp_path / "user")
    (source / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\nbody")
    store = SkillStore(tmp_path / "app")
    with pytest.raises(ValueError):
        store.add(str(source))
    with pytest.raises(ValueError):
        store.delete(name)
    assert not list(store.enabled_dir.iterdir())


def test_invalid_frontmatter_and_nested_skills_are_not_partially_copied(tmp_path):
    source = make_skill(tmp_path / "user")
    store = SkillStore(tmp_path / "app")
    for content in ["no frontmatter", "---\nname: [invalid\n---\nbody", "---\nname: code-review\n---\nbody"]:
        (source / "SKILL.md").write_text(content)
        with pytest.raises(ValueError):
            store.add(str(source))
        assert store.list()["items"] == []
    nested = source / "nested"
    nested.mkdir()
    (nested / "SKILL.md").write_text("nested")
    with pytest.raises(ValueError, match="嵌套"):
        store.add(str(source))


def test_missing_skill_file_and_reserved_command_names(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    store = SkillStore(tmp_path / "app")
    with pytest.raises(ValueError, match="缺少 SKILL.md"):
        store.add(str(source))
    (source / "SKILL.md").write_text("---\nname: skills\ndescription: test\n---\nbody")
    with pytest.raises(ValueError, match="重名"):
        store.add(str(source))


def test_symlink_resources_are_not_followed(tmp_path):
    source = make_skill(tmp_path / "user")
    secret = tmp_path / "outside.txt"
    secret.write_text("outside")
    try:
        (source / "secret.txt").symlink_to(secret)
    except OSError:
        pytest.skip("symlinks are unavailable")
    store = SkillStore(tmp_path / "app")
    with pytest.raises(ValueError, match="符号链接"):
        store.add(str(source))
    assert store.list()["items"] == []


def test_copy_failure_keeps_existing_skill_and_cleans_staging(tmp_path, monkeypatch):
    import sona_code.admin.skills as skills

    source = make_skill(tmp_path / "user")
    store = SkillStore(tmp_path / "app")
    original = skills.shutil.copytree

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("copy failure")

    monkeypatch.setattr(skills.shutil, "copytree", fail)
    with pytest.raises(OSError):
        store.add(str(source))
    assert store.list()["items"] == []
    assert not list(store.root.glob(".skill-import-*"))


@pytest.mark.asyncio
async def test_refresh_waits_for_idle_and_invalidates_native_cache(tmp_path, monkeypatch):
    manager = WorkspaceManager()
    statuses = {"ses_1": {"type": "busy"}}
    requests = []

    def respond(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json=statuses if request.url.path == "/session/status" else True)

    client = httpx.AsyncClient(base_url="http://opencode", transport=httpx.MockTransport(respond))
    manager._servers[str(tmp_path)] = OpenCodeServer(SimpleNamespace(poll=lambda: None), client, 1234)
    changes = []
    stopped = []
    async def stop(process):
        stopped.append(process)
    monkeypatch.setattr(manager, "_stop_process", stop)
    try:
        with pytest.raises(WorkspaceError) as error:
            await manager.update_skills(lambda: changes.append("changed") or {"ok": True})
        assert error.value.status == 409
        assert not changes
        assert ("POST", "/instance/dispose") not in requests
        statuses.clear()
        assert await manager.update_skills(lambda: changes.append("changed") or {"ok": True}) == {"ok": True}
        assert changes == ["changed"]
        assert requests[-1] == ("GET", "/session/status")
        assert stopped and not manager._servers
        assert client.is_closed
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_projects_run_commands_in_parallel_and_block_skill_mutations(monkeypatch):
    manager = WorkspaceManager()
    both_started = asyncio.Event()
    finish = asyncio.Event()
    started = []

    async def request(project, *_args, **_kwargs):
        started.append(project)
        if len(started) == 2:
            both_started.set()
        await finish.wait()
        return {"ok": True}

    monkeypatch.setattr(manager, "_request", request)
    tasks = [asyncio.create_task(manager.request(project, default_config(), "POST", "/session/ses_1/command"))
             for project in ("one", "two")]
    try:
        await asyncio.wait_for(both_started.wait(), timeout=1)
        with pytest.raises(WorkspaceError) as error:
            await manager.update_skills(lambda: pytest.fail("must not change skills during a command"))
        assert error.value.status == 409
    finally:
        finish.set()
        await asyncio.gather(*tasks)
    assert await manager.update_skills(lambda: {"ok": True}) == {"ok": True}


@pytest.mark.asyncio
async def test_idle_server_restart_fallback_when_dispose_is_unsupported(tmp_path, monkeypatch):
    manager = WorkspaceManager()
    process = SimpleNamespace(poll=lambda: None)
    client = httpx.AsyncClient(base_url="http://opencode", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={}) if request.url.path == "/session/status" else httpx.Response(404)))
    manager._servers[str(tmp_path)] = OpenCodeServer(process, client, 1234)
    stopped = []

    async def stop(item):
        stopped.append(item)

    monkeypatch.setattr(manager, "_stop_process", stop)
    assert await manager.update_skills(lambda: {"ok": True}) == {"ok": True}
    assert stopped == [process]
    assert str(tmp_path) not in manager._servers
    assert client.is_closed


def test_management_api_and_native_skill_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    app = create_app(default_config(), str(tmp_path / "proxy.json"))
    app.state.runtime.config.model_settings.show_native_models = True
    app.state.runtime.config.model_settings.source = "native"
    source = make_skill(tmp_path / "user")
    calls = []

    async def request(project, config, method, endpoint, *, body=None, params=None):
        calls.append((method, endpoint, body))
        if endpoint == "/command":
            return [{"name": item["name"], "source": "skill"} for item in app.state.runtime.skills.list()["items"] if item["enabled"]]
        if endpoint == "/skill":
            return [{"name": item["name"], "location": str(Path(item["path"]) / "SKILL.md")}
                    for item in app.state.runtime.skills.list()["items"] if item["enabled"]]
        return {"ok": True}

    app.state.runtime.workspace.request = request
    project = tmp_path / "project"
    project.mkdir()
    with TestClient(app) as client:
        prefix = "/__recorder/api"
        project_id = client.post(f"{prefix}/workspace/projects", json={"path": str(project)}).json()["id"]
        assert 'data-nav="skills"' in client.get("/__recorder/").text
        assert client.get("/__recorder/skills.js").status_code == 200
        assert client.post(f"{prefix}/skills", json={"path": str(source)}).status_code == 201
        assert client.post(f"{prefix}/skills", json={"path": str(source)}).status_code == 409
        assert client.get(f"{prefix}/skills").json()["total"] == 1
        popup = f"{prefix}/workspace/projects/{project_id}/skills"
        assert client.get(popup).json()["items"][0]["name"] == "code-review"
        command = f"{prefix}/workspace/projects/{project_id}/sessions/ses_1/command"
        payload = {"command": "code-review", "arguments": "review my changes", "agent": "plan",
                   "provider_id": "test", "model_id": "model", "variant": "high"}
        assert client.post(command, json=payload).status_code == 200
        message_id = calls[-1][2]["messageID"]
        assert message_id.startswith("msg_")
        assert calls[-1] == ("POST", "/session/ses_1/command", {
            "messageID": message_id,
            "command": "code-review", "arguments": "review my changes", "agent": "plan", "model": "test/model", "variant": "high"})
        assert client.patch(f"{prefix}/skills/code-review", json={"enabled": False}).status_code == 200
        assert client.get(popup).json()["items"] == []
        before = len(calls)
        assert client.post(command, json=payload).status_code == 409
        assert len(calls) == before
        assert client.patch(f"{prefix}/skills/code-review", json={"enabled": True}).status_code == 200
        assert client.get(popup).json()["items"][0]["enabled"] is True
        assert client.delete(f"{prefix}/skills/code-review").status_code == 200
        assert client.delete(f"{prefix}/skills/code-review").status_code == 404
        assert source.is_dir()


def test_queued_skill_records_native_message_only_at_dispatch_and_rechecks_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    app = create_app(default_config(), str(tmp_path / "proxy.json"))
    app.state.runtime.config.model_settings.show_native_models = True
    app.state.runtime.config.model_settings.source = "native"
    app.state.runtime.skills.add(str(make_skill(tmp_path / "user")))
    calls = []

    async def request(project, config, method, endpoint, *, body=None, params=None):
        calls.append((method, endpoint, body))
        if endpoint == "/command":
            return [{"name": "code-review", "source": "skill"}]
        if endpoint == "/skill":
            item = app.state.runtime.skills.list()["items"][0]
            return [{"name": "code-review", "location": str(Path(item["path"]) / "SKILL.md")}]
        if endpoint == "/agent":
            return [{"name": "build", "permission": []}]
        if endpoint == "/session/status":
            return {"ses_1": {"type": "busy"}}
        return {"id": "ses_1"}

    app.state.runtime.workspace.request = request
    async def stop_session(project, config, session):
        return await request(project, config, "POST", f"/session/{session}/abort", body={})
    app.state.runtime.workspace.stop_session = stop_session
    project = tmp_path / "project"
    project.mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    with TestClient(app) as client:
        prefix = "/__recorder/api/workspace/projects"
        project_id = client.get(prefix).json()["items"][0]["id"]
        base = f"{prefix}/{project_id}/sessions/ses_1"
        response = client.post(base + "/queue", json={"kind": "command", "payload": {
            "command": "code-review", "arguments": "review changes", "agent": "build",
            "provider_id": "test", "model_id": "model"}})
        assert response.status_code == 202
        item = response.json()["items"][0]
        native_message = {"info": {"id": item["id"], "role": "user"}, "parts": []}
        annotate = lambda: app.state.runtime.skills.annotate_messages(str(project), "ses_1", [native_message])[0]
        assert "skillUse" not in annotate()
        client.post(base + "/abort", json={})
        queue = app.state.runtime.workspace_queue
        entry = queue._queues[f"{project_id}:ses_1"]
        client.portal.call(queue.dispatch, entry, entry["items"][0])
        method, endpoint, body = calls[-1]
        assert (method, endpoint) == ("POST", "/session/ses_1/command")
        assert body["messageID"] == item["id"]
        assert body["model"] == "test/model"
        assert annotate()["skillUse"]["name"] == "code-review"
        app.state.runtime.skills.set_enabled("code-review", False)
        count = sum(method == "POST" and endpoint.endswith("/command") for method, endpoint, _ in calls)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            client.portal.call(queue.dispatch, entry, entry["items"][0])
        assert error.value.status_code == 409
        assert sum(method == "POST" and endpoint.endswith("/command") for method, endpoint, _ in calls) == count


def test_native_name_collisions_do_not_invoke_another_skill_or_command(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    app = create_app(default_config(), str(tmp_path / "proxy.json"))
    app.state.runtime.config.model_settings.show_native_models = True
    source = make_skill(tmp_path / "user")
    installed = app.state.runtime.skills.add(str(source))
    native = {"source": "skill", "location": str(tmp_path / "project/.opencode/skills/code-review/SKILL.md")}
    invoked = []

    async def request(project, config, method, endpoint, **kwargs):
        if endpoint == "/command":
            return [{"name": "code-review", "source": native["source"]}]
        if endpoint == "/skill":
            return [{"name": "code-review", "location": native["location"]}]
        invoked.append(endpoint)
        return {"ok": True}

    app.state.runtime.workspace.request = request
    project = tmp_path / "project"
    project.mkdir()
    with TestClient(app) as client:
        prefix = "/__recorder/api/workspace/projects"
        project_id = client.post(prefix, json={"path": str(project)}).json()["id"]
        popup = f"{prefix}/{project_id}/skills"
        command = f"{prefix}/{project_id}/sessions/ses_1/command"
        assert client.get(popup).json() == {"items": [], "unavailable": ["code-review"]}
        assert client.post(command, json={"command": "code-review"}).status_code == 409
        assert not invoked
        native["location"] = str(Path(installed["path"]) / "SKILL.md")
        native["source"] = "command"
        assert client.get(popup).json()["items"] == []
        assert client.post(command, json={"command": "code-review"}).status_code == 409
        assert not invoked
        native["source"] = "skill"
        assert client.get(popup).json()["items"][0]["name"] == "code-review"
        alias = tmp_path / "skills-alias"
        try:
            alias.symlink_to(Path(installed["path"]).parent, target_is_directory=True)
        except OSError:
            return  # Windows may not grant symlink creation privileges.
        native["location"] = str(alias / "code-review/SKILL.md")
        assert client.get(popup).json()["items"][0]["name"] == "code-review"


def test_external_sources_are_read_in_place_with_app_priority(tmp_path):
    external = tmp_path / "original/opencode"
    original = make_skill(external / "skills")
    nested = make_skill(external / "skill/team", "planning")
    store = SkillStore(tmp_path / "app", external_root=external)
    external_items = store.list()["items"]
    assert {i["name"] for i in external_items} == {"code-review", "planning"}
    review = next(i for i in external_items if i["name"] == "code-review")
    assert review["source"] == "external" and not review["deletable"]
    before = (original / "SKILL.md").read_bytes()
    store.set_enabled(review["id"], False)
    assert (original / "SKILL.md").read_bytes() == before
    assert not (external / "opencode.jsonc").exists()
    assert not next(i for i in store.list()["items"] if i["id"] == review["id"])["enabled"]
    with pytest.raises(ValueError, match="只能删除"):
        store.delete(review["id"])
    app = store.add(str(original))
    collision = next(i for i in store.list()["items"] if i["id"] == review["id"])
    assert collision["conflict"]
    assert store.external_paths() == [str(nested)]
    store.delete(app["id"])
    assert set(store.external_paths()) == {str(original), str(nested)}


def test_old_disabled_copy_migrates_once_with_permission(tmp_path):
    root = tmp_path / "app"
    old = make_skill(root / "skills-disabled")
    store = SkillStore(root, external_root=tmp_path / "original")
    store.migrate()
    store.migrate()
    assert not old.exists()
    assert (root / "skills/code-review/references/checklist.md").is_file()
    assert store.permission("code-review") == "deny"
    store.set_enabled("code-review", True)
    assert (root / "skills/code-review").is_dir()
    assert store.permission("code-review") == "allow"


@pytest.mark.parametrize("content", [
    '{}', '{"permission":"ask"}', '{"permission":{"skill":"deny"}}',
    '{// retain root\n "model":"test/model", /* root */}',
    '{"permission": {/* retain permission */ "skill": {"*": "ask", /* retain skill */}},}',
])
def test_official_permission_patch_preserves_other_config(tmp_path, content):
    from sona_code.admin.opencode_config import parse_jsonc
    root = tmp_path / "app"
    root.mkdir()
    config = root / "opencode.jsonc"
    config.write_text(content)
    store = SkillStore(root, external_root=tmp_path / "original")
    store.add(str(make_skill(tmp_path / "user")))
    store.set_enabled("code-review", False)
    changed = config.read_text()
    parsed = parse_jsonc(changed)
    assert parsed["permission"]["skill"]["code-review"] == "deny"
    assert parsed.get("model") == parse_jsonc(content).get("model")
    for comment in ("retain root", "retain permission", "retain skill", "root */"):
        if comment in content:
            assert comment in changed
    store.set_enabled("code-review", True)
    assert store.permission("code-review") == "allow"


def test_manual_skill_annotations_persist_and_do_not_modify_native_parts(tmp_path):
    root = tmp_path / "app"
    store = SkillStore(root, external_root=tmp_path / "original")
    skill = store.add(str(make_skill(tmp_path / "user")))
    store.record_use("project", "ses_1", "msg_1", skill, "review changes")
    messages = [{"info": {"id": "msg_1", "role": "user"}, "parts": [{"type": "text", "text": "native expanded content"}]},
                {"info": {"id": "msg_2", "role": "user"}, "parts": []}]
    restored = SkillStore(root, external_root=tmp_path / "original").annotate_messages("project", "ses_1", messages)
    assert restored[0]["skillUse"]["name"] == "code-review"
    assert restored[0]["skillUse"]["arguments"] == "review changes"
    assert restored[0]["parts"][0]["text"] == "native expanded content"
    assert "skillUse" not in restored[1]
    assert store.annotate_messages("project", "ses_other", [{"info": {"id": "msg_1", "role": "user"}}])[0].get("skillUse") is None


def test_agent_specific_skill_denial_blocks_picker_and_command(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("SONACODE_ORIGINAL_XDG_CONFIG_HOME", str(tmp_path / "original"))
    app = create_app(default_config(), str(tmp_path / "proxy.json"))
    app.state.runtime.config.model_settings.show_native_models = True
    skill = app.state.runtime.skills.add(str(make_skill(tmp_path / "user")))
    invoked = []
    async def request(project, config, method, endpoint, **kwargs):
        if endpoint == "/command":
            return [{"name": "code-review", "source": "skill"}]
        if endpoint == "/skill":
            return [{"name": "code-review", "location": str(Path(skill["path"]) / "SKILL.md")}]
        if endpoint == "/agent":
            return [{"name": "plan", "permission": [{"permission": "skill", "pattern": "*", "action": "allow"},
                    {"permission": "skill", "pattern": "code-*", "action": "deny"}]}]
        invoked.append(endpoint)
        return {"ok": True}
    app.state.runtime.workspace.request = request
    project = tmp_path / "project"
    project.mkdir()
    with TestClient(app) as client:
        prefix = "/__recorder/api/workspace/projects"
        project_id = client.post(prefix, json={"path": str(project)}).json()["id"]
        assert client.get(f"{prefix}/{project_id}/skills?agent=plan").json()["items"] == []
        assert client.post(f"{prefix}/{project_id}/sessions/ses_1/command", json={"command":"code-review", "agent":"plan"}).status_code == 409
        assert not invoked


def test_terminal_injects_external_paths_without_provider_changes(tmp_path, monkeypatch):
    import json
    from sona_code.terminal.manager import _build_env
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "app"))
    monkeypatch.setenv("SONACODE_ORIGINAL_XDG_CONFIG_HOME", str(tmp_path / "original"))
    monkeypatch.delenv("OPENCODE_CONFIG_CONTENT", raising=False)
    external = make_skill(tmp_path / "original/opencode/skills")
    cfg = default_config()
    cfg.terminal.route_through_proxy = False
    inline = json.loads(_build_env(cfg, "opencode")["OPENCODE_CONFIG_CONTENT"])
    assert inline == {"skills": {"paths": [str(external)]}}
    assert "OPENCODE_CONFIG_CONTENT" not in _build_env(cfg, "shell")


def test_migration_conflict_keeps_both_copies_and_reports_warning(tmp_path):
    store = SkillStore(tmp_path / "app", external_root=tmp_path / "original")
    active = make_skill(store.enabled_dir)
    disabled = make_skill(store.disabled_dir)
    store.migrate()
    assert active.is_dir() and disabled.is_dir()
    assert store.list()["warnings"]
    assert store.permission("code-review") == "allow"


def test_import_config_write_failure_rolls_back_new_copy(tmp_path, monkeypatch):
    store = SkillStore(tmp_path / "app", external_root=tmp_path / "original")
    def fail(*_args):
        raise OSError("config write failed")
    monkeypatch.setattr(store, "_permission", fail)
    with pytest.raises(OSError):
        store.add(str(make_skill(tmp_path / "user")))
    assert store.list()["items"] == []


@pytest.mark.asyncio
async def test_skill_changes_are_blocked_during_command_preflight():
    manager = WorkspaceManager()
    async with manager.task_dispatch():
        with pytest.raises(WorkspaceError) as error:
            await manager.update_skills(lambda: pytest.fail("must not mutate during preflight"))
        assert error.value.status == 409
    assert manager._task_requests == 0


def test_project_native_skills_keep_selection_and_manual_history(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("SONACODE_ORIGINAL_XDG_CONFIG_HOME", str(tmp_path / "original"))
    app = create_app(default_config(), str(tmp_path / "proxy.json"))
    app.state.runtime.config.model_settings.show_native_models = True
    app.state.runtime.config.model_settings.source = "native"
    skill = make_skill(tmp_path / "project/.opencode/skills", "project-review")
    calls = []
    async def request(project, config, method, endpoint, *, body=None, **kwargs):
        if endpoint == "/command":
            return [{"name": "project-review", "source": "skill"}]
        if endpoint == "/skill":
            return [{"name":"project-review", "description":"Project review", "location":str(skill / "SKILL.md")}]
        if endpoint == "/agent":
            return []
        calls.append(body)
        return {"ok":True}
    app.state.runtime.workspace.request = request
    with TestClient(app) as client:
        prefix = "/__recorder/api/workspace/projects"
        project_id = client.post(prefix,json={"path":str(tmp_path / "project")}).json()["id"]
        popup = client.get(f"{prefix}/{project_id}/skills").json()
        assert popup["items"][0]["name"] == "project-review"
        endpoint = f"{prefix}/{project_id}/sessions/ses_1/command"
        assert client.post(endpoint,json={"command":"project-review"}).status_code == 200
        assert calls[-1]["messageID"].startswith("msg_")
        app.state.runtime.skills._permission("project-review", False)
        assert client.get(f"{prefix}/{project_id}/skills").json()["items"] == []
        assert client.post(endpoint,json={"command":"project-review"}).status_code == 409
