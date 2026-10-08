"""Run authenticated OpenCode HTTP servers for saved projects."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import signal
import socket
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

import httpx

from sona_code.config import AppConfig
from sona_code.workspace.history import HistoryNotFound, read_history, session_catalog
from sona_code.terminal.manager import (
    _build_argv,
    _build_env,
    resolve_executable,
    resolve_opencode,
)

logger = logging.getLogger("sona_code")


class WorkspaceError(Exception):
    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


@dataclass
class OpenCodeServer:
    process: subprocess.Popen[bytes]
    client: httpx.AsyncClient
    port: int


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class WorkspaceManager:
    """One OpenCode service per project; sessions remain in OpenCode storage."""

    def __init__(self, config_supplier: Callable[[], AppConfig] | None = None) -> None:
        self._config_supplier = config_supplier
        self._servers: dict[str, OpenCodeServer] = {}
        self._starting: dict[str, asyncio.Task[OpenCodeServer]] = {}
        self._lock = asyncio.Lock()
        self._skill_changes = asyncio.Lock()
        self._task_requests = 0
        self._project_requests: dict[str | None, int] = {}
        self._project_changes: dict[str, asyncio.Lock] = {}
        self._changing: set[str] = set()
        self.environment = None

    async def ensure(self, project: str, config: AppConfig) -> OpenCodeServer:
        path = str(Path(project).expanduser().resolve())
        async with self._project_changes.setdefault(path, asyncio.Lock()):
            return await self._ensure(path, config)

    async def _ensure(self, project: str, config: AppConfig) -> OpenCodeServer:
        path = str(Path(project).expanduser().resolve())
        if not Path(path).is_dir():
            raise WorkspaceError(f"项目目录不存在：{path}", 404)
        async with self._lock:
            if self._config_supplier is not None:
                config = self._config_supplier()
            existing = self._servers.get(path)
            if existing and existing.process.poll() is None:
                return existing
            pending = self._starting.get(path)
            if pending is None:
                pending = asyncio.create_task(self._start_server(path, config))
                self._starting[path] = pending
                # A disconnected browser must not cancel a shared startup or
                # leave an unobserved failure when its request is cancelled.
                pending.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(pending)

    async def _start_server(self, path: str, config: AppConfig) -> OpenCodeServer:
        try:
            existing = self._servers.pop(path, None)
            if existing:
                await existing.client.aclose()

            resolution = await asyncio.to_thread(resolve_opencode, config)
            if not resolution.path:
                raise WorkspaceError("未找到 OpenCode 程序，请在设置中配置程序来源", 503)
            executable = await asyncio.to_thread(resolve_executable, resolution.path)
            if not executable:
                raise WorkspaceError("OpenCode 程序路径无效", 503)

            env = await asyncio.to_thread(_build_env, config, "opencode")
            if self.environment is not None:
                env = await asyncio.to_thread(self.environment, path, "opencode", env)
            password = secrets.token_urlsafe(32)
            env["OPENCODE_SERVER_USERNAME"] = "sona-code"
            env["OPENCODE_SERVER_PASSWORD"] = password
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            ) if os.name == "nt" else 0
            for attempt in range(3):
                port = _free_port()
                argv = [*_build_argv(executable, []), "serve", "--hostname", "127.0.0.1", "--port", str(port)]
                try:
                    process = subprocess.Popen(
                        argv, cwd=path, env=env, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, creationflags=creationflags,
                        start_new_session=os.name != "nt",
                    )
                except OSError as exc:
                    raise WorkspaceError(f"启动 OpenCode 失败：{exc}", 503) from exc
                client = httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}",
                    auth=("sona-code", password), trust_env=False, timeout=20,
                )
                for _ in range(50):
                    if process.poll() is not None:
                        break
                    try:
                        response = await client.get("/global/health", timeout=1)
                        if response.status_code == 200:
                            server = OpenCodeServer(process, client, port)
                            self._servers[path] = server
                            return server
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(.2)
                await client.aclose()
                await self._stop_process(process)
                logger.warning("OpenCode server did not become ready for %s (attempt %d)", path, attempt + 1)
            raise WorkspaceError("OpenCode 服务启动失败，请检查程序版本与应用日志", 503)
        finally:
            self._starting.pop(path, None)

    async def _wait_for_starts(self) -> None:
        """Called with the lifecycle lock held so no new starts can enter."""
        if self._starting:
            await asyncio.gather(*list(self._starting.values()), return_exceptions=True)

    async def request(
        self, project: str, config: AppConfig, method: str, endpoint: str,
        *, body: dict | None = None, params: dict[str, str | int] | None = None,
    ) -> object:
        # Exclude skill changes during task dispatch while keeping project tasks parallel.
        if method == "POST" and endpoint.endswith(("/prompt_async", "/command", "/shell", "/summarize")):
            async with self.task_dispatch(project):
                return await self._request(project, config, method, endpoint, body=body, params=params)
        return await self._request(project, config, method, endpoint, body=body, params=params)

    @asynccontextmanager
    async def task_dispatch(self, project: str | None = None) -> AsyncIterator[None]:
        """Protect preflight validation and dispatch from concurrent skill mutations."""
        path = str(Path(project).expanduser().resolve()) if project is not None else None
        async with self._skill_changes:
            if path in self._changing:
                raise WorkspaceError("当前项目正在准备 OpenSpec，请完成后重试", 409)
            self._task_requests += 1
            self._project_requests[path] = self._project_requests.get(path, 0) + 1
        try:
            yield
        finally:
            self._task_requests -= 1
            self._project_requests[path] -= 1

    async def update_project_configuration(self, project: str, operation: Callable[[], Awaitable[dict]]) -> dict:
        """Protect one project's files/caches without interrupting other projects."""
        path = str(Path(project).expanduser().resolve())
        async with self._project_changes.setdefault(path, asyncio.Lock()):
            async with self._skill_changes:
                if self._project_requests.get(path, 0) or self._project_requests.get(None, 0):
                    raise WorkspaceError("当前项目有任务正在派发，请任务结束后再准备 OpenSpec", 409)
                self._changing.add(path)
            try:
                pending = self._starting.get(path)
                if pending:
                    await asyncio.shield(pending)
                server = self._servers.get(path)
                if server and server.process.poll() is None:
                    try:
                        response = await server.client.get("/session/status", params={"directory": path})
                        response.raise_for_status()
                        statuses = response.json()
                    except (httpx.HTTPError, ValueError) as exc:
                        raise WorkspaceError("无法确认当前项目任务状态，请稍后重试", 503) from exc
                    if not isinstance(statuses, dict) or any(not isinstance(status, dict) or status.get("type") != "idle" for status in statuses.values()):
                        raise WorkspaceError("当前项目有任务正在运行，请任务结束后再准备 OpenSpec", 409)
                result = await operation()
                if server:
                    self._servers.pop(path, None)
                    await server.client.aclose()
                    await self._stop_process(server.process)
                return result
            finally:
                self._changing.discard(path)

    async def running_statuses(self) -> dict[str, dict]:
        """Read existing servers in parallel without starting unopened projects."""
        async def read(path: str, server: OpenCodeServer) -> tuple[str, dict]:
            if server.process.poll() is not None:
                return path, {"statuses": {}}
            try:
                response = await server.client.get("/session/status", params={"directory": path}, timeout=3)
                response.raise_for_status()
                statuses = response.json()
                if not isinstance(statuses, dict):
                    raise ValueError("invalid session statuses")
                return path, {"statuses": statuses}
            except (httpx.HTTPError, ValueError, RuntimeError):
                return path, {"error": "暂时无法读取运行状态"}

        return dict(await asyncio.gather(*(read(path, server) for path, server in list(self._servers.items()))))

    async def session_catalog(self, projects: list[str], config: AppConfig) -> dict[str, list[dict]] | None:
        if self._config_supplier is not None:
            config = self._config_supplier()
        return await asyncio.to_thread(session_catalog, projects, config)

    async def _request(
        self, project: str, config: AppConfig, method: str, endpoint: str,
        *, body: dict | None = None, params: dict[str, str | int] | None = None,
    ) -> object:
        if method == "GET" and not params:
            if self._config_supplier is not None:
                config = self._config_supplier()
            try:
                snapshot = await asyncio.to_thread(read_history, project, config, endpoint)
            except HistoryNotFound as exc:
                raise WorkspaceError("会话不存在", 404) from exc
            if snapshot is not None:
                return snapshot
        server = await self.ensure(project, config)
        try:
            response = await server.client.request(
                method, endpoint, params={"directory": project, **(params or {})}, json=body,
                timeout=180 if endpoint.endswith(("/command", "/shell", "/summarize")) else 20,
            )
        except httpx.RequestError as exc:
            raise WorkspaceError(f"连接 OpenCode 服务失败：{exc}", 502) from exc
        if response.is_error:
            detail = response.text[:500] or "OpenCode 请求失败"
            raise WorkspaceError(detail, response.status_code)
        if response.status_code == 204 or not response.content:
            return {"ok": True}
        try:
            return response.json()
        except ValueError as exc:
            raise WorkspaceError("OpenCode 返回了无法解析的数据", 502) from exc

    async def update_skills(self, operation: Callable[[], dict]) -> dict:
        async def apply() -> dict:
            return await asyncio.to_thread(operation)
        return await self.update_configuration(apply, "技能")

    async def update_configuration(self, operation: Callable[[], Awaitable[dict]], noun: str = "模型配置") -> dict:
        """Serialize config changes with task preflight; recycle idle native caches."""
        async with self._skill_changes, self._lock:
            if self._task_requests or self._changing:
                raise WorkspaceError(f"工作区有任务正在运行，请任务结束后再修改{noun}", 409)
            await self._wait_for_starts()
            active = [(path, server) for path, server in self._servers.items()
                      if server.process.poll() is None]
            for path, server in active:
                try:
                    response = await server.client.get("/session/status", params={"directory": path})
                    response.raise_for_status()
                    statuses = response.json()
                    if not isinstance(statuses, dict):
                        raise ValueError("invalid session statuses")
                except (httpx.HTTPError, ValueError) as exc:
                    raise WorkspaceError("无法确认 OpenCode 任务状态，请稍后重试", 503) from exc
                if any(not isinstance(status, dict) or status.get("type") != "idle"
                       for status in statuses.values()):
                    raise WorkspaceError(f"工作区有任务正在运行，请任务结束后再修改{noun}：{path}", 409)
            result = await operation()
            for path, server in active:
                # Inline skills.paths is captured at process start. Recreate idle
                # servers so imports/deletions also resolve duplicate sources afresh.
                await server.client.aclose()
                await self._stop_process(server.process)
                self._servers.pop(path, None)
            return result

    async def events(self, project: str, config: AppConfig) -> AsyncIterator[bytes]:
        server = await self.ensure(project, config)
        try:
            async with server.client.stream(
                "GET", "/event", params={"directory": project}, timeout=None,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk
        except httpx.HTTPError:
            logger.warning("OpenCode event stream ended for %s", project, exc_info=True)

    async def stop(self, project: str) -> None:
        path = str(Path(project).expanduser().resolve())
        async with self._lock:
            pending = self._starting.get(path)
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            server = self._servers.pop(path, None)
            if server:
                await server.client.aclose()
                await self._stop_process(server.process)

    async def shutdown(self) -> None:
        async with self._lock:
            await self._wait_for_starts()
            servers = list(self._servers.values())
            self._servers.clear()
            for server in servers:
                await server.client.aclose()
                await self._stop_process(server.process)

    @staticmethod
    async def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                await asyncio.to_thread(
                    subprocess.run, ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True, check=False, timeout=5,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError):
                # Restricted Windows installations may block taskkill. Still reap
                # the owned server rather than aborting shutdown of all projects.
                logger.warning("Windows process-tree cleanup failed for PID %s", process.pid)
            if process.poll() is None:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            await asyncio.to_thread(process.wait, 3)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.to_thread(process.wait)
