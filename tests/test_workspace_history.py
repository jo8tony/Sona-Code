"""Cold history browsing, native snapshots and project counts without processes."""

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.config import default_config
from sona_code.workspace import history
from sona_code.workspace.manager import WorkspaceError, WorkspaceManager


@pytest.fixture
def native_store(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    root = tmp_path / "data" / "opencode"
    root.mkdir(parents=True)
    path = root / "opencode.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT, slug TEXT,
            directory TEXT, title TEXT, version TEXT, workspace_id TEXT, path TEXT,
            agent TEXT, model TEXT, metadata TEXT, cost REAL DEFAULT 0,
            tokens_input INTEGER DEFAULT 0, tokens_output INTEGER DEFAULT 0,
            tokens_reasoning INTEGER DEFAULT 0, tokens_cache_read INTEGER DEFAULT 0,
            tokens_cache_write INTEGER DEFAULT 0, time_created INTEGER,
            time_updated INTEGER, time_compacting INTEGER, time_archived INTEGER,
            summary_additions INTEGER, summary_deletions INTEGER, summary_files INTEGER,
            summary_diffs TEXT, share_url TEXT, revert TEXT, permission TEXT
        );
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, session_id TEXT, message_id TEXT, data TEXT);
        CREATE TABLE todo (session_id TEXT, content TEXT, status TEXT, priority TEXT, position INTEGER);
    """)
    config = default_config()
    config.terminal.inject_env = {"XDG_DATA_HOME": str(root.parent), "OPENCODE_DB": str(path)}
    projects = []
    for name in ("one", "empty", "other"):
        directory = tmp_path / name
        directory.mkdir()
        projects.append(str(directory.resolve()))

    def session(session_id="ses_one", project=None, **values):
        row = {"id": session_id, "project_id": "shared-git-root", "slug": "test",
               "directory": (project or projects[0]).replace("\\", "/"), "title": session_id,
               "version": "1.18.32", "time_created": 1, "time_updated": 2, **values}
        columns = ",".join(row)
        db.execute(f"INSERT INTO session ({columns}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
        db.commit()

    session()
    yield SimpleNamespace(db=db, path=path, config=config, projects=projects, session=session)
    db.close()


def test_catalog_has_zero_counts_scopes_directories_and_excludes_archives(native_store):
    store = native_store
    store.session("ses_other", store.projects[2])
    store.session("ses_archived", time_archived=3)
    store.session("ses_child", parent_id="ses_one", time_updated=4)
    catalog = history.session_catalog(store.projects[:2], store.config)
    assert list(catalog) == store.projects[:2]
    assert [item["id"] for item in catalog[store.projects[0]]] == ["ses_child", "ses_one"]
    assert catalog[store.projects[1]] == []
    # More than native /session's default 100 rows must still give an exact count.
    for index in range(101):
        store.session(f"ses_{index}")
    assert len(history.session_catalog(store.projects[:1], store.config)[store.projects[0]]) == 103


def test_catalog_preserves_unicode_path_casing(native_store):
    store = native_store
    project = str(Path(store.projects[0]).parent / "É项目")
    store.session("ses_unicode", project)
    assert history.session_catalog([project], store.config)[project][0]["id"] == "ses_unicode"


def test_native_session_shape_and_ordered_history_include_tools_todos_children(native_store):
    store = native_store
    store.session("ses_details", parent_id="ses_one", workspace_id="wrk_test", path="subdir", agent="build",
                  model=json.dumps({"id": "model", "providerID": "provider", "variant": "high"}),
                  summary_additions=5, summary_diffs="[]", share_url="https://example.invalid/test",
                  revert=json.dumps({"messageID": "msg_2", "partID": "prt_2"}),
                  permission=json.dumps([{"permission": "edit", "pattern": "*", "action": "ask"}]),
                  metadata=json.dumps({"test": True}), time_compacting=7)
    store.db.executemany("INSERT INTO message VALUES (?, 'ses_one', ?, ?)", [
        ("msg_b", 2, json.dumps({"role": "assistant", "time": {"created": 2}})),
        ("msg_a", 1, json.dumps({"role": "user", "id": "untrusted", "sessionID": "wrong"})),
        ("msg_c", 2, json.dumps({"role": "assistant", "error": {"name": "TestError"}})),
    ])
    store.db.executemany("INSERT INTO part VALUES (?, 'ses_one', 'msg_b', ?)", [
        ("prt_b", json.dumps({"type": "tool", "tool": "skill", "state": {"status": "completed", "output": "test"}})),
        ("prt_a", json.dumps({"type": "text", "text": "保存的内容", "id": "wrong", "messageID": "wrong"})),
    ])
    store.db.executemany("INSERT INTO todo VALUES ('ses_one', ?, ?, 'high', ?)", [
        ("second", "completed", 1), ("first", "pending", 0),
    ])
    store.db.commit()
    info = history.read_history(store.projects[0], store.config, "/session/ses_details")
    assert info["parentID"] == "ses_one" and info["workspaceID"] == "wrk_test"
    assert info["directory"] == store.projects[0]
    assert info["summary"] == {"additions": 5, "deletions": 0, "files": 0, "diffs": []}
    assert info["model"]["variant"] == "high" and info["metadata"] == {"test": True}
    assert info["revert"] == {"messageID": "msg_2", "partID": "prt_2"}
    assert info["time"] == {"created": 1, "updated": 2, "compacting": 7}
    assert info["tokens"]["cache"] == {"read": 0, "write": 0}
    messages = history.read_history(store.projects[0], store.config, "/session/ses_one/message")
    assert [item["info"]["id"] for item in messages] == ["msg_a", "msg_b", "msg_c"]
    assert messages[0]["info"]["sessionID"] == "ses_one"
    assert [part["id"] for part in messages[1]["parts"]] == ["prt_a", "prt_b"]
    assert messages[1]["parts"][0]["messageID"] == "msg_b"
    assert messages[1]["parts"][1]["state"]["status"] == "completed"
    assert [todo["content"] for todo in history.read_history(store.projects[0], store.config, "/session/ses_one/todo")] == ["first", "second"]
    assert history.read_history(store.projects[0], store.config, "/session/ses_one/children") == [info]


def test_snapshot_reads_live_wal_and_never_mutates_native_database(native_store):
    store = native_store
    before = hashlib.sha256(store.path.read_bytes()).digest()
    assert history.read_history(store.projects[0], store.config, "/session/ses_one/message") == []
    store.db.execute("INSERT INTO message VALUES ('msg_new', 'ses_one', 4, ?)", (json.dumps({"role": "user"}),))
    store.db.commit()
    assert history.read_history(store.projects[0], store.config, "/session/ses_one/message")[0]["info"]["id"] == "msg_new"
    assert hashlib.sha256(store.path.read_bytes()).digest() == before
    with closing(history._connect(store.path)) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM session")


def test_live_status_endpoint_is_not_a_persisted_session(native_store):
    store = native_store
    assert history.read_history(store.projects[0], store.config, "/session/status") is None


@pytest.mark.parametrize("snapshot_source", ["matching", "missing", "other_directory"])
async def test_queue_reads_live_status_with_history_and_after_fork(native_store, snapshot_source, tmp_path, monkeypatch):
    store = native_store
    store.config.model_settings.source = "native"
    app = create_app(store.config, str(tmp_path / "config.json"))
    runtime = app.state.runtime
    runtime.terminal_projects.add(store.projects[0], "opencode")
    queue = runtime.workspace_queue
    queue._start = lambda key: None
    queue.interval = .01
    statuses = {"ses_one": {"type": "busy"}}
    delivered = []
    native_messages = {}
    if snapshot_source == "missing":
        store.db.execute("DELETE FROM session WHERE id = 'ses_one'")
    elif snapshot_source == "other_directory":
        store.db.execute("UPDATE session SET directory = ? WHERE id = 'ses_one'", (store.projects[2],))
    store.db.commit()

    async def respond(request):
        if request.url.path == "/session/status":
            return httpx.Response(200, json=statuses)
        if request.url.path == "/session/ses_one/fork":
            store.session("ses_fork")
            return httpx.Response(200, json={"id": "ses_fork"})
        if request.url.path in {"/session/ses_one", "/session/ses_fork"}:
            return httpx.Response(200, json={"id": request.url.path.split("/")[2], "directory": store.projects[0]})
        if request.url.path.endswith("/message"):
            return httpx.Response(200, json=native_messages.get(request.url.path.split("/")[2], []))
        if request.url.path.endswith("/prompt_async"):
            body = json.loads(request.content)
            session_id = request.url.path.split("/")[2]
            message_id = body["messageID"]
            delivered.append((session_id, body["parts"][0]["text"]))
            native_messages.setdefault(session_id, []).extend([
                {"info": {"id": message_id, "role": "user"}},
                {"info": {"id": message_id + "_reply", "role": "assistant", "parentID": message_id,
                          "time": {"completed": 11}}},
            ])
            store.db.executemany("INSERT INTO message VALUES (?, ?, ?, ?)", [
                (message_id, session_id, 10, json.dumps({"role": "user"})),
                (message_id + "_reply", session_id, 11, json.dumps({
                    "role": "assistant", "parentID": message_id, "time": {"completed": 11},
                })),
            ])
            store.db.commit()
            return httpx.Response(204)
        pytest.fail(f"Unexpected native request: {request.method} {request.url.path}")

    native_client = httpx.AsyncClient(base_url="http://native.test", transport=httpx.MockTransport(respond))

    async def ensure(project, config):
        return SimpleNamespace(client=native_client)

    monkeypatch.setattr(runtime.workspace, "ensure", ensure)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://app.test") as client:
            prefix = "/__recorder/api/workspace"
            project_id = (await client.get(f"{prefix}/projects")).json()["items"][0]["id"]
            base = f"{prefix}/projects/{project_id}"
            status = await client.get(f"{base}/status")
            assert status.status_code == 200
            assert status.json() == statuses
            original = f"{base}/sessions/ses_one"
            response = await client.post(original + "/queue", json={"payload": {"text": "next turn"}})
            assert response.status_code == 202
            await queue._step(f"{project_id}:ses_one")
            assert not delivered
            statuses.clear()  # Native V1 omits idle sessions from this live endpoint.
            await client.patch(original + "/queue", json={"paused": True})
            await client.patch(original + "/queue", json={"paused": False})
            await asyncio.wait_for(queue._run(f"{project_id}:ses_one"), 2)
            assert delivered == [("ses_one", "next turn")]
            assert (await client.get(original + "/queue")).json() == {"items": [], "paused": False, "error": ""}
            fork = await client.post(original + "/fork", json={})
            assert fork.status_code == 200
            fork_base = f"{base}/sessions/{fork.json()['id']}"
            response = await client.post(fork_base + "/queue", json={"payload": {"text": "fork turn"}})
            assert response.status_code == 202
            await asyncio.wait_for(queue._run(f"{project_id}:ses_fork"), 2)
            assert delivered == [("ses_one", "next turn"), ("ses_fork", "fork turn")]
            assert (await client.get(fork_base + "/queue")).json() == {"items": [], "paused": False, "error": ""}
    finally:
        await native_client.aclose()
        await runtime.aclose()


def test_empty_store_is_not_created_and_legacy_or_custom_channel_is_not_guessed(tmp_path, monkeypatch):
    config = default_config()
    monkeypatch.delenv("OPENCODE_DB", raising=False)
    monkeypatch.delenv("OPENCODE_DISABLE_CHANNEL_DB", raising=False)
    config.terminal.inject_env = {"XDG_DATA_HOME": str(tmp_path)}
    assert history.session_catalog(["empty"], config) == {"empty": []}
    assert not (tmp_path / "opencode").exists()
    root = tmp_path / "opencode"
    (root / "storage" / "session").mkdir(parents=True)
    assert history.session_catalog(["empty"], config) is None
    config.terminal.inject_env["OPENCODE_DB"] = ":memory:"
    assert history.session_catalog(["empty"], config) is None
    config.terminal.inject_env["OPENCODE_DB"] = "custom.db"
    assert history._database_path(config) == root / "custom.db"
    config.terminal.inject_env.pop("OPENCODE_DB")
    (root / "opencode-dev.db").touch()
    assert history._database_path(config) is None


@pytest.mark.parametrize("change", ["schema", "version", "corrupt_json", "missing_part_table"])
def test_incompatible_history_falls_back_to_native(native_store, change):
    store = native_store
    if change == "schema":
        store.db.execute("ALTER TABLE session RENAME COLUMN title TO incompatible_title")
    elif change == "version":
        store.db.execute("UPDATE session SET version='2.0.0'")
    elif change == "corrupt_json":
        store.db.execute("INSERT INTO message VALUES ('msg_bad', 'ses_one', 1, '{broken')")
    else:
        store.db.execute("DROP TABLE part")
    store.db.commit()
    assert history.read_history(store.projects[0], store.config, "/session/ses_one/message") is None


async def test_manager_browses_history_without_ensure_and_preserves_native_mutations(native_store, monkeypatch):
    store = native_store
    manager = WorkspaceManager(lambda: store.config)
    ensure_calls = []

    async def respond(request):
        return httpx.Response(200, json={"native": True})

    client = httpx.AsyncClient(base_url="http://native.test", transport=httpx.MockTransport(respond))

    async def ensure(project, config):
        ensure_calls.append((project, config))
        return SimpleNamespace(client=client)

    monkeypatch.setattr(manager, "ensure", ensure)
    try:
        assert await manager.request(store.projects[1], default_config(), "GET", "/session") == []
        assert await manager.request(store.projects[0], default_config(), "GET", "/session/ses_one/message") == []
        with pytest.raises(WorkspaceError) as missing:
            await manager.request(store.projects[1], default_config(), "GET", "/session/ses_one")
        assert missing.value.status == 404
        assert not ensure_calls
        assert await manager.request(store.projects[0], store.config, "POST", "/session") == {"native": True}
        assert await manager.request(store.projects[0], store.config, "GET", "/session", params={"limit": 1}) == {"native": True}
        store.db.execute("DROP TABLE part")
        store.db.commit()
        assert await manager.request(store.projects[0], store.config, "GET", "/session/ses_one/message") == {"native": True}
        assert len(ensure_calls) == 3
    finally:
        await client.aclose()


async def test_snapshot_miss_is_confirmed_by_native_before_reporting_not_found(native_store, monkeypatch):
    store = native_store
    manager = WorkspaceManager()
    calls = []
    session = {"id": "ses_native", "directory": store.projects[0]}

    async def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/session/ses_native":
            return httpx.Response(200, json=session)
        if request.url.path == "/session/ses_native/message":
            return httpx.Response(200, json=[{"info": {"id": "msg_live", "role": "user"}}])
        return httpx.Response(404, json={"name": "NotFoundError"})

    async with httpx.AsyncClient(base_url="http://native.test", transport=httpx.MockTransport(respond)) as client:
        async def ensure(project, config):
            return SimpleNamespace(client=client)

        monkeypatch.setattr(manager, "ensure", ensure)
        assert await manager.request(store.projects[0], store.config, "GET", "/session/ses_native") == session
        messages = await manager.request(store.projects[0], store.config, "GET", "/session/ses_native/message")
        assert messages[0]["info"]["id"] == "msg_live"
        with pytest.raises(WorkspaceError) as missing:
            await manager.request(store.projects[0], store.config, "GET", "/session/ses_deleted")
        assert missing.value.status == 404
        assert calls == ["/session/ses_native", "/session/ses_native/message", "/session/ses_deleted"]


async def test_running_server_history_is_authoritative_over_local_snapshot(native_store):
    store = native_store
    manager = WorkspaceManager()
    calls = []

    async def respond(request):
        calls.append(request.url.path)
        if request.url.path == "/session/ses_one/message":
            return httpx.Response(200, json=[{"info": {"id": "msg_live", "role": "user"}}])
        return httpx.Response(200, json={"id": "ses_live", "directory": store.projects[0]})

    async with httpx.AsyncClient(base_url="http://native.test", transport=httpx.MockTransport(respond)) as client:
        manager._servers[store.projects[0]] = SimpleNamespace(client=client, process=SimpleNamespace(poll=lambda: None))
        messages = await manager.request(store.projects[0], store.config, "GET", "/session/ses_one/message")
        assert messages[0]["info"]["id"] == "msg_live"
        session = await manager.request(store.projects[0], store.config, "GET", "/session/ses_live")
        assert session["id"] == "ses_live"
        assert calls == ["/session/ses_one/message", "/session/ses_live"]


def test_api_projects_include_collapsed_counts_and_history_keeps_skill_annotations(native_store, tmp_path, monkeypatch):
    store = native_store
    app = create_app(store.config, str(tmp_path / "config.json"))
    for project in store.projects[:2]:
        app.state.runtime.terminal_projects.add(project, "opencode")

    async def unexpected_start(*args):
        pytest.fail("browsing saved history must not start OpenCode")

    monkeypatch.setattr(app.state.runtime.workspace, "ensure", unexpected_start)
    store.db.execute("INSERT INTO message VALUES ('msg_user', 'ses_one', 1, ?)", (json.dumps({"role": "user"}),))
    store.db.commit()
    app.state.runtime.skills.record_use(store.projects[0], "ses_one", "msg_user", {"name": "example", "path": "example"}, "task")
    with TestClient(app) as client:
        prefix = "/__recorder/api/workspace"
        projects = {item["path"]: item for item in client.get(f"{prefix}/projects").json()["items"]}
        one, empty = (projects[path] for path in store.projects[:2])
        assert one["session_count"] == 1 and empty["session_count"] == 0
        assert one["sessions"][0]["id"] == "ses_one" and empty["sessions"] == []
        messages = client.get(f"{prefix}/projects/{one['id']}/sessions/ses_one/messages")
        assert messages.status_code == 200
        assert messages.json()[0]["skillUse"]["name"] == "example"
        assert client.get(f"{prefix}/projects/{empty['id']}/sessions").json()["items"] == []
        assert not app.state.runtime.workspace._servers
