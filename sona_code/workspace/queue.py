"""Durable V1 submissions with immediate steering and optional FIFO delivery."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Awaitable, Callable

from sona_code.workspace.manager import WorkspaceError

logger = logging.getLogger("sona_code")
_last_message_tick = 0


def native_message_id() -> str:
    global _last_message_tick
    # Native history sorts by ID; consecutive steering in the same millisecond
    # must retain submission order rather than relying on the random suffix.
    _last_message_tick = max(int(time.time() * 1000) << 12, _last_message_tick + 1)
    return "msg_" + format(_last_message_tick, "012x") + secrets.token_hex(7)


class WorkspaceQueue:
    def __init__(self, config_path: str) -> None:
        self.path = Path(config_path).expanduser().resolve().parent / "workspace-queue.json"
        self._queues: dict[str, dict] = {}
        self._lock = asyncio.Lock()
        self._workers: dict[str, asyncio.Task] = {}
        self._step_locks: dict[str, asyncio.Lock] = {}
        self._deliveries: dict[tuple[str, str], asyncio.Task] = {}
        self.dispatch: Callable[[dict, dict], Awaitable[object]] | None = None
        self.inspect: Callable[[dict], Awaitable[tuple[dict, list]]] | None = None
        self.interval = .8
        if self.path.exists():
            self._queues = json.loads(self.path.read_text(encoding="utf-8"))
            # Restoring drafts must never start unattended model work.
            for queue in self._queues.values():
                queue["paused"] = True
                if any(item["status"] == "sending" for item in queue["items"]):
                    queue["error"] = "上次发送状态待确认，继续后将核对对话，不会重复发送"

    @staticmethod
    def _key(project_id: str, session_id: str) -> str:
        return f"{project_id}:{session_id}"

    def snapshot(self, project_id: str, session_id: str) -> dict:
        queue = self._queues.get(self._key(project_id, session_id), {})
        return copy.deepcopy({"items": queue.get("items", []), "paused": queue.get("paused", False),
                              "error": queue.get("error", "")})

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".workspace-queue-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    async def _save(self) -> None:
        await asyncio.to_thread(self._write, copy.deepcopy(self._queues))

    def _start(self, key: str) -> None:
        if key not in self._workers or self._workers[key].done():
            self._workers[key] = asyncio.create_task(self._run(key))

    async def add(
        self, project_id: str, project: str, session_id: str, kind: str, payload: dict,
        *, dispatch_if_idle: bool = False,
    ) -> dict:
        key = self._key(project_id, session_id)
        async with self._lock:
            queue = self._queues.setdefault(key, {"project_id": project_id, "project": project,
                "session_id": session_id, "items": [], "paused": False, "error": ""})
            if len(queue["items"]) >= 20:
                raise WorkspaceError("最多排队 20 条消息，请先发送或移除部分消息", 409)
            item = {"id": native_message_id(),
                    "kind": kind, "payload": payload, "status": "pending"}
            queue["items"].append(item)
            await self._save()
        if dispatch_if_idle:
            try:
                # Retire a completed tracked turn, then immediately admit its successor.
                if await self._step(key, background=True):
                    await self._step(key, background=True)
            except Exception as exc:
                async with self._lock:
                    queue.update(paused=True, error=str(getattr(exc, "detail", exc))[:500])
                    await self._save()
        async with self._lock:
            result = self.snapshot(project_id, session_id)
            self._start(key)
            return result

    async def send_now(
        self, project_id: str, project: str, session_id: str, kind: str, payload: dict,
    ) -> dict:
        """Append to the native running loop without waiting for queued drafts."""
        key = self._key(project_id, session_id)
        async with self._step_locks.setdefault(key, asyncio.Lock()):
            async with self._lock:
                queue = self._queues.setdefault(key, {"project_id": project_id, "project": project,
                    "session_id": session_id, "items": [], "paused": False, "error": ""})
                if sum(item["status"] == "sending" for item in queue["items"]) >= 20:
                    raise WorkspaceError("消息正在提交，请稍后再发送", 409)
                message_id = native_message_id()
                item = {"id": message_id, "message_id": message_id, "kind": kind,
                        "payload": payload, "status": "sending", "delivery": "steer",
                        "sent_at": time.time()}
                queue["items"].append(item)
                # Save before dispatch: a lost response must never cause an automatic retry.
                await self._save()
                self._start_delivery(key, queue, item)
            await asyncio.sleep(0)
            async with self._lock:
                result = self.snapshot(project_id, session_id)
                self._start(key)
                return {**result, "submitted_id": item["id"], "message_id": message_id}

    def _start_delivery(self, key: str, queue: dict, item: dict) -> None:
        self._deliveries[(key, item["id"])] = asyncio.create_task(self._deliver(key, queue, item))

    def _cancel_deliveries(self, keys: list[str]) -> list[asyncio.Task]:
        return [self._deliveries.pop(identity) for identity in list(self._deliveries)
                if identity[0] in keys]

    async def update(self, project_id: str, session_id: str, item_id: str, kind: str, payload: dict) -> dict:
        async with self._lock:
            queue = self._queues.get(self._key(project_id, session_id), {})
            item = next((i for i in queue.get("items", []) if i["id"] == item_id), None)
            if item is None:
                raise WorkspaceError("排队消息已发送或已移除", 404)
            if item["status"] != "pending":
                raise WorkspaceError("消息已开始发送，无法编辑", 409)
            item.update(kind=kind, payload=payload)
            await self._save()
            return self.snapshot(project_id, session_id)

    async def remove(self, project_id: str, session_id: str, item_id: str) -> dict:
        async with self._lock:
            queue = self._queues.get(self._key(project_id, session_id), {})
            item = next((i for i in queue.get("items", []) if i["id"] == item_id), None)
            task = self._workers.get(self._key(project_id, session_id))
            if item and item["status"] != "pending" and not (queue.get("paused") and (not task or task.done())):
                raise WorkspaceError("消息已开始发送，无法移除", 409)
            if item:
                queue["items"].remove(item)
                await self._save()
            return self.snapshot(project_id, session_id)

    async def pause(self, project_id: str, session_id: str, paused: bool = True) -> dict:
        key = self._key(project_id, session_id)
        async with self._lock:
            queue = self._queues.get(key)
            if queue:
                queue["paused"] = paused
                if not paused:
                    queue["error"] = ""
                await self._save()
                self._start(key)
            return self.snapshot(project_id, session_id)

    async def discard(self, project_id: str, session_id: str | None = None) -> None:
        async with self._lock:
            keys = [key for key, queue in self._queues.items() if queue["project_id"] == project_id
                    and (session_id is None or queue["session_id"] == session_id)]
            tasks = [self._workers.pop(key) for key in keys if key in self._workers]
            tasks.extend(self._cancel_deliveries(keys))
            for task in tasks:
                task.cancel()
            for key in keys:
                self._queues.pop(key)
            await self._save()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self, project_id: str, session_id: str) -> None:
        """Stop tracking the interrupted turn while preserving unsent drafts."""
        key = self._key(project_id, session_id)
        async with self._lock:
            queue = self._queues.get(key)
            if queue:
                queue["paused"] = True
            worker = self._workers.pop(key, None)
            tasks = ([worker] if worker else []) + self._cancel_deliveries([key])
            for task in tasks:
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            queue = self._queues.get(key)
            if queue:
                queue["items"] = [item for item in queue["items"] if item["status"] == "pending"]
                queue["error"] = ""
                if not queue["items"]:
                    self._queues.pop(key)
                await self._save()

    async def shutdown(self) -> None:
        async with self._lock:
            for queue in self._queues.values():
                queue["paused"] = True
            tasks = [*self._workers.values(), *self._deliveries.values()]
            for task in tasks:
                task.cancel()
            if self._queues:
                await self._save()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()
        self._deliveries.clear()

    async def _run(self, key: str) -> None:
        try:
            while True:
                queue = self._queues.get(key)
                if not queue or not queue["items"] or (queue["paused"] and
                        (queue.get("error") or not self._has_steering(queue))):
                    return
                await self._step(key)
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Workspace queue paused", exc_info=True)
            async with self._lock:
                if key in self._queues:
                    self._queues[key].update(paused=True, error=str(getattr(exc, "detail", exc))[:500])
                    await self._save()

    async def _step(self, key: str, *, background: bool = False) -> bool:
        async with self._step_locks.setdefault(key, asyncio.Lock()):
            return bool(await self._step_locked(key, background=background))

    @staticmethod
    def _has_steering(queue: dict) -> bool:
        return any(item.get("delivery") == "steer" and item["status"] == "sending"
                   for item in queue["items"])

    async def _step_locked(self, key: str, *, background: bool) -> bool | None:
        queue = self._queues.get(key)
        if not queue or not queue["items"] or (queue["paused"] and not self._has_steering(queue)):
            return
        if any(identity[0] == key and not task.done() for identity, task in self._deliveries.items()):
            return
        assert self.inspect is not None and self.dispatch is not None
        statuses, messages = await self.inspect(queue)
        if not isinstance(statuses, dict) or not isinstance(messages, list):
            raise WorkspaceError("无法确认任务状态，队列已暂停", 503)
        status = statuses.get(queue["session_id"], {"type": "idle"})
        if not isinstance(status, dict) or status.get("type") != "idle":
            return
        async with self._lock:
            if not queue["items"]:
                return
            sending = [item for item in queue["items"] if item["status"] == "sending"]
            if sending:
                changed = self._settle(queue, sending, messages)
                if not queue["items"] and not queue["paused"]:
                    self._queues.pop(key)
                if changed:
                    await self._save()
                return changed and not any(item["status"] == "sending" for item in queue["items"])
            if queue["paused"]:
                return
            item = queue["items"][0]
            # Persist admission before HTTP dispatch. An ambiguous result is never retried automatically.
            # V1 orders history by native IDs. Mint it at delivery, after the
            # previous task's tool continuations, rather than at queue admission.
            item.update(status="sending", sent_at=time.time(), message_id=native_message_id())
            await self._save()
        if background:
            # V1 /command may wait for the entire answer. Admission must only
            # start delivery, so the composer becomes usable immediately.
            self._start_delivery(key, queue, item)
            await asyncio.sleep(0)
        else:
            await self._deliver(key, queue, item)

    @staticmethod
    def _settle(queue: dict, sending: list[dict], messages: list) -> bool:
        """V1 may answer several appended user messages in one continuation."""
        positions = {message.get("info", {}).get("id"): index for index, message in enumerate(messages)
                     if message.get("info", {}).get("role") == "user"}
        steering = [positions[item["message_id"]] for item in sending
                    if item.get("delivery") == "steer" and item["message_id"] in positions]
        boundary = max(steering, default=-1)
        continuation_ids = {identity for identity, index in positions.items() if boundary >= 0 and index >= boundary}
        continuation = [message.get("info", {}) for message in messages
                        if message.get("info", {}).get("parentID") in continuation_ids]
        final = continuation[-1] if continuation else {}
        changed = False
        for item in sending:
            message_id = item.get("message_id", item["id"])
            replies = [message.get("info", {}) for message in messages
                       if message.get("info", {}).get("parentID") == message_id]
            reply = replies[-1] if replies else {}
            if not reply.get("time", {}).get("completed") and positions.get(message_id, boundary + 1) <= boundary:
                reply = final
            if message_id in positions and reply.get("time", {}).get("completed"):
                queue["items"].remove(item)
                if reply.get("error"):
                    queue.update(paused=True, error="上一条消息执行失败，检查后继续队列")
                changed = True
            elif time.time() - item.get("sent_at", 0) > 30 and not queue.get("error"):
                queue.update(paused=True, error="发送结果尚未确认，请检查对话后继续队列")
                changed = True
        return changed

    async def _deliver(self, key: str, queue: dict, item: dict) -> None:
        assert self.dispatch is not None
        try:
            await self.dispatch(queue, item)
        except Exception as exc:
            async with self._lock:
                queue.update(paused=True, error=str(getattr(exc, "detail", exc))[:500])
                # Deterministic validation failures have not reached the native server.
                if getattr(exc, "status_code", getattr(exc, "status", None)) in {400, 404, 409, 413, 422}:
                    item["status"] = "pending"
                await self._save()
        finally:
            identity = (key, item["id"])
            if self._deliveries.get(identity) is asyncio.current_task():
                self._deliveries.pop(identity, None)
