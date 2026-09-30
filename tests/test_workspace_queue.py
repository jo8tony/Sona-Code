"""V1 queue ordering, durable drafts and HTTP admission validation."""

import asyncio
import copy

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.config import AppConfig, UpstreamConfig
from sona_code.workspace.manager import WorkspaceError
from sona_code.workspace.queue import WorkspaceQueue


@pytest.fixture
def queue(tmp_path):
    result = WorkspaceQueue(str(tmp_path / "config.json"))
    result._start = lambda key: None
    return result


@pytest.mark.parametrize("status", ["busy", "retry"])
async def test_queue_waits_for_idle_and_each_completed_reply(queue, status):
    statuses = {"ses_1": {"type": status}}
    messages, delivered = [], []

    async def inspect(entry):
        return statuses, messages

    async def dispatch(entry, item):
        delivered.append(copy.deepcopy(item))
        messages.append({"info": {"id": item["message_id"], "role": "user"}})

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.add("p", "/project", "ses_1", "prompt", {"text": "first"})
    await queue.add("p", "/project", "ses_1", "prompt", {"text": "second"})
    await queue._step("p:ses_1")
    assert not delivered
    statuses.clear()  # V1 removes idle sessions from /session/status.
    await queue._step("p:ses_1")
    assert len(delivered) == 1
    await queue._step("p:ses_1")
    assert len(delivered) == 1  # No busy event yet: do not dispatch the next message.
    messages.append({"info": {"role": "assistant", "parentID": delivered[0]["message_id"],
                              "time": {"completed": 1}}})
    await queue._step("p:ses_1")
    await queue._step("p:ses_1")
    assert [item["payload"]["text"] for item in delivered] == ["first", "second"]


async def test_edit_remove_pause_and_restore_preserve_session_scope(queue):
    first = (await queue.add("p", "/project", "s1", "prompt", {"text": "draft", "references": [{"path": "src/a.py"}]}))["items"][0]
    await queue.add("p", "/project", "s2", "command", {"command": "review", "arguments": "other"})
    edited = await queue.update("p", "s1", first["id"], "prompt", {"text": "edited", "references": first["payload"]["references"]})
    assert edited["items"][0]["id"] == first["id"]
    assert edited["items"][0]["payload"]["references"] == [{"path": "src/a.py"}]
    edited["items"].clear()
    assert queue.snapshot("p", "s1")["items"]
    with pytest.raises(WorkspaceError):
        await queue.update("p", "s2", first["id"], "prompt", {"text": "wrong session"})
    await queue.pause("p", "s1")
    await queue.add("p", "/project", "s1", "prompt", {"text": "still paused"})
    restored = WorkspaceQueue(str(queue.path.parent / "config.json"))
    assert restored.snapshot("p", "s1")["paused"]
    assert restored.snapshot("p", "s2")["paused"]
    assert restored.snapshot("p", "s1")["items"][0]["payload"]["text"] == "edited"
    await queue.remove("p", "s1", first["id"])
    assert len(queue.snapshot("p", "s1")["items"]) == 1
    await queue.discard("p", "s1")
    assert not queue.snapshot("p", "s1")["items"]
    assert queue.snapshot("p", "s2")["items"]


async def test_pause_during_inspection_prevents_dispatch(queue):
    delivered = []

    async def inspect(entry):
        await queue.pause("p", "s1")
        return {}, []

    async def dispatch(entry, item):
        delivered.append(item)

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.add("p", "/project", "s1", "prompt", {"text": "wait"})
    await queue._step("p:s1")
    assert not delivered


@pytest.mark.parametrize("has_pending", [False, True])
async def test_stop_removes_interrupted_turn_and_preserves_only_unsent_drafts(queue, has_pending):
    delivered = []

    async def inspect(entry):
        return {}, []

    async def dispatch(entry, item):
        delivered.append(item["id"])

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.add("p", "/project", "s1", "prompt", {"text": "running"})
    if has_pending:
        await queue.add("p", "/project", "s1", "prompt", {"text": "unsent"})
    await queue._step("p:s1")
    await queue.stop("p", "s1")
    snapshot = queue.snapshot("p", "s1")
    assert snapshot["error"] == ""
    assert snapshot["paused"] == has_pending
    assert [item["payload"]["text"] for item in snapshot["items"]] == (["unsent"] if has_pending else [])
    restored = WorkspaceQueue(str(queue.path.parent / "config.json"))
    assert restored.snapshot("p", "s1")["items"] == snapshot["items"]
    if has_pending:
        await queue._step("p:s1")
        assert len(delivered) == 1
        await queue.pause("p", "s1", False)
        await queue._step("p:s1")
        assert len(delivered) == 2


async def test_stop_cancels_dispatch_before_dropping_its_tracking(tmp_path):
    queue = WorkspaceQueue(str(tmp_path / "config.json"))
    sending = asyncio.Event()
    cancelled = asyncio.Event()

    async def inspect(entry):
        return {}, []

    async def dispatch(entry, item):
        sending.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.add("p", "/project", "s1", "prompt", {"text": "running"})
    await asyncio.wait_for(sending.wait(), 1)
    await queue.add("p", "/project", "s1", "prompt", {"text": "unsent"})
    await queue.stop("p", "s1")
    assert cancelled.is_set()
    assert "p:s1" not in queue._workers
    assert [item["payload"]["text"] for item in queue.snapshot("p", "s1")["items"]] == ["unsent"]
    await queue.shutdown()


async def test_failed_native_reply_pauses_remaining_queue(queue):
    messages = []

    async def inspect(entry):
        return {}, messages

    async def dispatch(entry, item):
        messages.extend([{"info": {"id": item["message_id"], "role": "user"}},
                         {"info": {"parentID": item["message_id"], "time": {"completed": 1}, "error": {"name": "APIError"}}}])

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.add("p", "/project", "s1", "prompt", {"text": "first"})
    await queue.add("p", "/project", "s1", "prompt", {"text": "next"})
    await queue._step("p:s1")
    await queue._step("p:s1")
    snapshot = queue.snapshot("p", "s1")
    assert snapshot["paused"] and snapshot["error"]
    assert snapshot["items"][0]["payload"]["text"] == "next"


@pytest.mark.parametrize("error,editable", [(HTTPException(409, "model removed"), True), (WorkspaceError("timeout", 502), False)])
async def test_dispatch_error_preserves_draft_without_automatic_retry(queue, error, editable):
    delivered = []

    async def inspect(entry):
        return {}, []

    async def dispatch(entry, item):
        delivered.append(item["id"])
        raise error

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.add("p", "/project", "s1", "prompt", {"text": "keep me"})
    await queue._step("p:s1")
    assert len(delivered) == 1
    snapshot = queue.snapshot("p", "s1")
    assert snapshot["paused"] and snapshot["items"][0]["payload"]["text"] == "keep me"
    assert (snapshot["items"][0]["status"] == "pending") == editable


async def test_background_queue_runs_without_browser_and_shutdown_keeps_drafts(tmp_path):
    queue = WorkspaceQueue(str(tmp_path / "config.json"))
    queue.interval = .01
    sent = asyncio.Event()

    async def inspect(entry):
        return {}, []

    async def dispatch(entry, item):
        sent.set()

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.add("p", "/project", "s1", "prompt", {"text": "background"})
    await asyncio.wait_for(sent.wait(), 1)
    await queue.shutdown()
    assert not queue._workers
    assert queue.snapshot("p", "s1")["paused"]
    assert WorkspaceQueue(str(tmp_path / "config.json")).snapshot("p", "s1")["items"]


async def test_native_id_is_minted_at_delivery_and_restored_submission_is_not_resent(queue, monkeypatch):
    from sona_code.workspace import queue as queue_module
    monkeypatch.setattr(queue_module.time, "time", lambda: 100)
    admitted = (await queue.add("p", "/project", "s1", "prompt", {"text": "queued early"}))["items"][0]
    delivered = []

    async def inspect(entry):
        return {}, []

    async def dispatch(entry, item):
        delivered.append(copy.deepcopy(item))

    queue.inspect, queue.dispatch = inspect, dispatch
    monkeypatch.setattr(queue_module.time, "time", lambda: 200)
    await queue._step("p:s1")
    assert delivered[0]["message_id"] > admitted["id"]
    await queue.shutdown()
    restored = WorkspaceQueue(str(queue.path.parent / "config.json"))
    restored._start = lambda key: None
    native_id = delivered[0]["message_id"]

    async def restored_inspect(entry):
        return {}, [{"info": {"id": native_id, "role": "user"}},
                    {"info": {"parentID": native_id, "time": {"completed": 1}}}]

    restored.inspect, restored.dispatch = restored_inspect, dispatch
    assert restored.snapshot("p", "s1")["error"]
    await restored.pause("p", "s1", False)
    await restored._step("p:s1")
    assert not restored.snapshot("p", "s1")["items"]
    assert len(delivered) == 1


async def test_queue_limit_and_ambiguous_submission_can_be_removed_after_pause(queue):
    for index in range(20):
        await queue.add("p", "/project", "s1", "prompt", {"text": str(index)})
    with pytest.raises(WorkspaceError, match="20"):
        await queue.add("p", "/project", "s1", "prompt", {"text": "overflow"})
    entry = queue._queues["p:s1"]
    entry["items"][0]["status"] = "sending"
    item_id = entry["items"][0]["id"]
    with pytest.raises(WorkspaceError):
        await queue.remove("p", "s1", item_id)
    await queue.pause("p", "s1")
    await queue.remove("p", "s1", item_id)
    assert len(queue.snapshot("p", "s1")["items"]) == 19


def test_queue_routes_validate_and_revalidate_v1_payloads(tmp_path):
    config = AppConfig(upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
                       default_upstream="main", model_settings={"source": "native", "show_native_models": True})
    app = create_app(config, str(tmp_path / "config.json"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "file.txt").write_text("example")
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    calls = []

    async def fake_request(project, cfg, method, endpoint, *, body=None, params=None):
        calls.append((method, endpoint, copy.deepcopy(body)))
        if endpoint.endswith("/message"):
            return []
        if endpoint == "/session/status":
            return {"ses_test": {"type": "busy"}}
        if endpoint == "/command":
            return [{"name": "review"}]
        if endpoint == "/skill":
            return []
        return {"id": "ses_test"}

    app.state.runtime.workspace.request = fake_request
    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        base = f"/__recorder/api/workspace/projects/{project_id}/sessions/ses_test"
        payload = {"text": "queued", "references": [{"path": "file.txt"}],
                   "provider_id": "native", "model_id": "model-a",
                   "agent": "build", "variant": "max"}
        response = client.post(base + "/queue", json={"kind": "prompt", "payload": payload})
        assert response.status_code == 202
        item = response.json()["items"][0]
        assert item["payload"]["variant"] == "max"
        assert not any(endpoint.endswith("prompt_async") for _, endpoint, _ in calls)
        assert client.post(base + "/queue", json={"payload": {"text": ""}}).status_code == 400
        assert client.post(base + "/queue", json={"payload": {"references": [{"path": "../outside"}]}}).status_code == 400
        assert client.post(base + "/queue", json={"kind": "shell", "payload": payload}).status_code == 422
        assert client.get(base + "/queue").json()["items"][0]["id"] == item["id"]
        assert client.patch(base + "/queue/" + item["id"], json={"kind": "prompt", "payload": {**payload, "text": "edited"}}).status_code == 200
        client.post(base + "/abort", json={})
        assert client.get(base + "/queue").json()["paused"]
        queue = app.state.runtime.workspace_queue
        entry = queue._queues[f"{project_id}:ses_test"]
        client.portal.call(queue.dispatch, entry, entry["items"][0])
        native = next(body for method, endpoint, body in reversed(calls) if endpoint.endswith("prompt_async"))
        assert native["messageID"] == item["id"]
        assert native["model"] == {"providerID": "native", "modelID": "model-a"}
        assert native["variant"] == "max"
        assert native["parts"][1]["url"] == (project / "file.txt").as_uri()
        (project / "file.txt").unlink()
        with pytest.raises(HTTPException):
            client.portal.call(queue.dispatch, entry, entry["items"][0])
        assert client.delete(base + "/queue/" + item["id"]).status_code == 200
        assert not client.get(base + "/queue").json()["items"]
        assert client.patch(base + "/queue/msg_missing", json={"payload": {"text": "gone"}}).status_code == 404
        for index in range(20):
            assert client.post(base + "/queue", json={"payload": {"text": str(index)}}).status_code == 202
        full = client.post(base + "/queue", json={"payload": {"text": "overflow"}})
        assert full.status_code == 409 and "20" in full.json()["detail"]
