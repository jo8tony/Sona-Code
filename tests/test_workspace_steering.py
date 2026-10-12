"""Immediate V1 steering, coalesced replies and durable FIFO coexistence."""

import asyncio
import copy

import pytest
from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.config import AppConfig, UpstreamConfig
from sona_code.workspace.queue import WorkspaceQueue


def test_immediate_native_ids_preserve_order_in_the_same_millisecond(monkeypatch):
    from sona_code.workspace import queue as module
    monkeypatch.setattr(module.time, "time", lambda: 100)
    monkeypatch.setattr(module, "_last_message_tick", 0)
    identities = [module.native_message_id() for _ in range(20)]
    assert identities == sorted(set(identities))


@pytest.fixture
def queue(tmp_path):
    result = WorkspaceQueue(str(tmp_path / "config.json"))
    result._start = lambda key: None
    return result


def reply(identity, error=None):
    return {"info": {"role": "assistant", "parentID": identity,
                     "time": {"completed": 1}, **({"error": error} if error else {})}}


@pytest.mark.parametrize("status", ["busy", "retry"])
async def test_steering_bypasses_busy_and_pending_queue_with_exact_receipt(queue, status):
    messages, delivered = [], []

    async def inspect(entry):
        return {"s": {"type": status}}, messages

    async def dispatch(entry, item):
        delivered.append(copy.deepcopy(item))
        messages.append({"info": {"id": item["message_id"], "role": "user"}})

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.add("p", "/project", "s", "prompt", {"text": "later"})
    receipt = await queue.send_now("p", "/project", "s", "prompt", {"text": "change direction"})
    await queue._step("p:s")
    assert [item["payload"]["text"] for item in delivered] == ["change direction"]
    assert receipt["submitted_id"] == receipt["message_id"] == delivered[0]["message_id"]
    assert queue.snapshot("p", "s")["items"][0]["status"] == "pending"


async def test_coalesced_steering_retires_original_and_continues_fifo(queue):
    messages, delivered, statuses = [], [], {}

    async def inspect(entry):
        return statuses, messages

    async def dispatch(entry, item):
        delivered.append(copy.deepcopy(item))
        messages.append({"info": {"id": item["message_id"], "role": "user"}})

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.add("p", "/project", "s", "prompt", {"text": "original"})
    await queue._step("p:s")
    statuses["s"] = {"type": "busy"}
    await queue.add("p", "/project", "s", "prompt", {"text": "queued"})
    await queue.send_now("p", "/project", "s", "prompt", {"text": "first correction"})
    receipt = await queue.send_now("p", "/project", "s", "prompt", {"text": "second correction"})
    # V1 replies only to the latest user message, after considering all three.
    messages.append(reply(receipt["message_id"]))
    await queue._step("p:s")
    assert len(delivered) == 3
    statuses.clear()
    await queue._step("p:s")
    assert [item["payload"]["text"] for item in queue.snapshot("p", "s")["items"]] == ["queued"]
    await queue._step("p:s")
    assert [item["payload"]["text"] for item in delivered] == [
        "original", "first correction", "second correction", "queued"]


async def test_fifo_waits_for_latest_steering_even_when_old_reply_completed(queue):
    messages, delivered = [], []

    async def inspect(entry):
        return {}, messages

    async def dispatch(entry, item):
        delivered.append(item["payload"]["text"])
        messages.append({"info": {"id": item["message_id"], "role": "user"}})

    queue.inspect, queue.dispatch = inspect, dispatch
    first = await queue.send_now("p", "/project", "s", "prompt", {"text": "first"})
    messages.append(reply(first["message_id"]))
    second = await queue.send_now("p", "/project", "s", "prompt", {"text": "second"})
    await queue.add("p", "/project", "s", "prompt", {"text": "later"})
    await queue._step("p:s")
    await queue._step("p:s")
    assert delivered == ["first", "second"]
    messages.append(reply(second["message_id"]))
    await queue._step("p:s")
    await queue._step("p:s")
    assert delivered == ["first", "second", "later"]


async def test_steering_keeps_paused_drafts_and_can_confirm_its_own_reply(queue):
    messages = []

    async def inspect(entry):
        return {}, messages

    async def dispatch(entry, item):
        messages.extend([{"info": {"id": item["message_id"], "role": "user"}}, reply(item["message_id"])])

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.add("p", "/project", "s", "prompt", {"text": "paused draft"})
    await queue.pause("p", "s")
    await queue.send_now("p", "/project", "s", "prompt", {"text": "send now"})
    await queue._step("p:s")
    snapshot = queue.snapshot("p", "s")
    assert snapshot["paused"]
    assert [item["payload"]["text"] for item in snapshot["items"]] == ["paused draft"]


async def test_multiple_commands_release_admission_and_stop_only_their_session(queue):
    started, cancelled = [], []

    async def dispatch(entry, item):
        started.append(entry["session_id"])
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(entry["session_id"])

    queue.dispatch = dispatch
    for session in ["s", "other", "s"]:
        await asyncio.wait_for(queue.send_now("p", "/project", session, "command", {"command": "review"}), 1)
    assert started == ["s", "other", "s"]
    await queue.stop("p", "s")
    assert cancelled == ["s", "s"]
    assert len(queue._deliveries) == 1
    assert queue.snapshot("p", "other")["items"]
    await queue.shutdown()
    assert not queue._deliveries


async def test_missing_native_message_is_never_confirmed_by_a_later_reply_or_retried(queue):
    messages, dispatched = [], []

    async def inspect(entry):
        return {}, messages

    async def dispatch(entry, item):
        dispatched.append(item["message_id"])
        if item["payload"]["text"] == "received":
            messages.extend([{"info": {"id": item["message_id"], "role": "user"}}, reply(item["message_id"])])

    queue.inspect, queue.dispatch = inspect, dispatch
    missing = await queue.send_now("p", "/project", "s", "prompt", {"text": "uncertain"})
    await queue.send_now("p", "/project", "s", "prompt", {"text": "received"})
    queue._queues["p:s"]["items"][0]["sent_at"] = 0
    await queue._step("p:s")
    snapshot = queue.snapshot("p", "s")
    assert snapshot["paused"] and snapshot["error"]
    assert [item["message_id"] for item in snapshot["items"]] == [missing["message_id"]]
    await queue.pause("p", "s", False)
    await queue._step("p:s")
    assert len(dispatched) == 2


async def test_coalesced_error_pauses_unsent_drafts_and_restored_submissions_are_not_resent(queue):
    messages, delivered = [], []

    async def inspect(entry):
        return {}, messages

    async def dispatch(entry, item):
        delivered.append(item["message_id"])
        messages.append({"info": {"id": item["message_id"], "role": "user"}})

    queue.inspect, queue.dispatch = inspect, dispatch
    await queue.send_now("p", "/project", "s", "prompt", {"text": "first"})
    second = await queue.send_now("p", "/project", "s", "prompt", {"text": "second"})
    await queue.add("p", "/project", "s", "prompt", {"text": "draft"})
    await queue.shutdown()
    restored = WorkspaceQueue(str(queue.path.parent / "config.json"))
    restored._start = lambda key: None
    restored.inspect, restored.dispatch = inspect, dispatch
    messages.append(reply(second["message_id"], {"name": "APIError"}))
    await restored.pause("p", "s", False)
    await restored._step("p:s")
    assert len(delivered) == 2
    assert restored.snapshot("p", "s")["paused"]
    assert [item["payload"]["text"] for item in restored.snapshot("p", "s")["items"]] == ["draft"]


def test_send_routes_validate_and_preserve_native_payload_while_busy(tmp_path):
    config = AppConfig(upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
                       default_upstream="main", model_settings={"source": "native", "show_native_models": True},
                       recording={"dir": str(tmp_path / "records")})
    app = create_app(config, str(tmp_path / "config.json"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "file.txt").write_text("example")
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    calls = []
    command_sent = asyncio.Event()

    async def fake_request(project, cfg, method, endpoint, *, body=None, params=None):
        calls.append((method, endpoint, copy.deepcopy(body)))
        if method == "POST" and endpoint.endswith("/command"):
            command_sent.set()
        if endpoint == "/session/status":
            return {"s": {"type": "busy"}}
        if endpoint.endswith("/message"):
            return []
        if endpoint == "/command":
            return [{"name": "review"}]
        if endpoint == "/skill":
            return []
        return {"id": "s"}

    app.state.runtime.workspace.request = fake_request
    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        base = f"/__recorder/api/workspace/projects/{project_id}/sessions/s"
        payload = {"text": "correct direction", "references": [{"path": "file.txt"}],
                   "provider_id": "native", "model_id": "model-a", "agent": "build", "variant": "max"}
        receipt = client.post(base + "/send", json={"payload": payload})
        assert receipt.status_code == 202
        native = next(body for _, endpoint, body in calls if endpoint.endswith("prompt_async"))
        assert native["messageID"] == receipt.json()["message_id"]
        assert native["parts"][1]["url"] == (project / "file.txt").as_uri()
        assert native["variant"] == "max" and native["agent"] == "build"
        assert native["model"] == {"providerID": "native", "modelID": "model-a"}
        assert client.post(base + "/send", json={"payload": {"text": ""}}).status_code == 400
        assert client.post(base + "/send", json={"payload": {"references": [{"path": "../outside"}]}}).status_code == 400
        assert client.post(base + "/send", json={"kind": "shell", "payload": payload}).status_code == 422
        command = client.post(base + "/send", json={"kind": "command", "payload": {
            "command": "review", "arguments": "HEAD", "provider_id": "native", "model_id": "model-a"}})
        assert command.status_code == 202
        async def wait_for_command():
            await asyncio.wait_for(command_sent.wait(), 1)
        client.portal.call(wait_for_command)
        delegated = next(body for method, endpoint, body in calls if method == "POST" and endpoint.endswith("/command"))
        assert delegated["messageID"] == command.json()["message_id"]
        assert delegated["command"] == "review" and delegated["arguments"] == "HEAD"
