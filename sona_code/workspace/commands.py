"""Run native shell commands in scopes owned by their project and session."""

from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import BinaryIO

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from sona_code.processes import ProcessScope
from sona_code.workspace.lifecycle import ExecutionLedger

logger = logging.getLogger("sona_code")


def _available_bytes(stream: BinaryIO) -> int:
    """Snapshot buffered output without waiting for inherited write handles."""
    if os.name == "nt":
        import msvcrt
        from sona_code.processes import kernel
        peek = kernel.PeekNamedPipe
        peek.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                         ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
        available = ctypes.c_ulong()
        if not peek(msvcrt.get_osfhandle(stream.fileno()), None, 0, None, ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error in {109, 232}:
                return 0
            raise OSError(error, "Unable to read command output")
        return available.value
    import array
    import fcntl
    import termios
    available = array.array("i", [0])
    fcntl.ioctl(stream.fileno(), termios.FIONREAD, available, True)
    return available[0]


def _read_available(stream: BinaryIO) -> bytes | None:
    """Don't wait for EOF: a background descendant may inherit the pipe."""
    if os.name == "nt":
        available = _available_bytes(stream)
        return os.read(stream.fileno(), min(available, 65536)) if available else None
    os.set_blocking(stream.fileno(), False)
    try:
        return os.read(stream.fileno(), 65536)
    except BlockingIOError:
        return None


class CommandManager:
    def __init__(self, ledger: ExecutionLedger, port: int) -> None:
        self.ledger = ledger
        self.url = f"http://127.0.0.1:{port}/__sona_commands"
        self.owners: dict[str, tuple[str, str]] = {}
        self.scopes: dict[tuple[str, str], set[ProcessScope]] = {}
        self.blocked: set[tuple[str, str]] = set()
        self.sessions: dict[str, dict[str, str]] = {}
        self.closing = False
        self._stop_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.runners = tempfile.TemporaryDirectory(prefix="sona-shell-")

    def environment(self, owner: str, project: str, env: dict[str, str]) -> dict[str, str]:
        helper = self.helper()
        if self.closing:
            raise HTTPException(503, "Application is closing")
        if helper is None:
            logger.warning("Managed command runner unavailable; using native OpenCode shell for %s", project)
        self.blocked = {key for key in self.blocked if key[0] != owner}
        token = secrets.token_urlsafe(32)
        self.owners[token] = (owner, str(Path(project).resolve()))
        config = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
        config["plugin"] = [*config.get("plugin", []), Path(__file__).with_name("managed-shell.js").as_uri()]
        return {**env, "OPENCODE_CONFIG_CONTENT": json.dumps(config),
                "SONACODE_COMMAND_URL": self.url, "SONACODE_COMMAND_TOKEN": token,
                "SONACODE_COMMAND_RUNNER": str(helper) if helper is not None else ""}

    @staticmethod
    def helper() -> Path | None:
        bundle = Path(os.environ.get("SONACODE_BUNDLED_OPENSPEC", Path(__file__).resolve().parents[2] / "build/openspec"))
        helper = bundle / "bin" / ("sona-command-runner.exe" if os.name == "nt" else "sona-command-runner")
        return helper if helper.is_file() else None

    def authenticate(self, request: Request) -> tuple[str, str]:
        value = request.headers.get("authorization", "").removeprefix("Bearer ")
        owner = self.owners.get(value)
        if request.client is None or request.client.host != "127.0.0.1" or owner is None:
            raise HTTPException(403, "Invalid command owner")
        return owner

    def shell(self, shell: str) -> dict[str, str]:
        if not shell:
            shell = ((shutil.which("pwsh") or shutil.which("powershell") or os.environ.get("COMSPEC", "cmd.exe"))
                     if os.name == "nt" else os.environ.get("SHELL", "/bin/zsh"))
        executable = shutil.which(shell) or shell
        if not Path(executable).is_file():
            raise HTTPException(503, "Configured shell was not found")
        helper = self.helper()
        if helper is None:
            raise HTTPException(503, "当前客户端未附带托管命令执行器，请使用 OpenCode 原生命令执行")
        # Preserve shell basename: V1 uses it to select parser and argument syntax.
        directory = Path(self.runners.name) / secrets.token_hex(8)
        directory.mkdir()
        runner = directory / Path(executable).name
        shutil.copy2(helper, runner)
        return {"shell": executable, "runner": str(runner)}

    async def stop_session(self, owner: str, session: str) -> None:
        key = (owner, session)
        self.blocked.add(key)
        async with self._stop_locks.setdefault(key, asyncio.Lock()):
            scopes = list(self.scopes.get(key, set()))
            results = await asyncio.gather(*(asyncio.to_thread(scope.close) for scope in scopes), return_exceptions=True)
            for scope, result in zip(scopes, results):
                if not isinstance(result, BaseException):
                    self.scopes.get(key, set()).discard(scope)
            if not self.scopes.get(key):
                self.scopes.pop(key, None)
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    async def stop_owner(self, owner: str) -> None:
        for token, entry in list(self.owners.items()):
            if entry[0] == owner:
                self.owners.pop(token)
        keys = [key for key in self.scopes if key[0] == owner]
        results = await asyncio.gather(*(self.stop_session(*key) for key in keys), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        for session, project in self.sessions.pop(owner, {}).items():
            self.ledger.finish(project, session, "interrupted")

    async def shutdown(self) -> None:
        self.closing = True
        results = await asyncio.gather(*(self.stop_owner(owner) for owner in
            {entry[0] for entry in self.owners.values()} | {key[0] for key in self.scopes}), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        self.runners.cleanup()

    async def run(self, request: Request) -> StreamingResponse:
        owner, project = self.authenticate(request)
        body = await request.json()
        session = body.get("session_id")
        key = (owner, session)
        if (not isinstance(session, str) or not session or self.closing or key in self.blocked
                or self.authenticate(request) != (owner, project)):
            raise HTTPException(409, "Session is stopping")
        command, shell, cwd, env = (body.get(field) for field in ("command", "shell", "cwd", "env"))
        if not isinstance(command, str) or not isinstance(shell, str) or not isinstance(env, dict):
            raise HTTPException(400, "Invalid command")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise HTTPException(400, "Invalid environment")
        if not isinstance(cwd, str) or not Path(cwd).is_dir():
            raise HTTPException(400, "Invalid working directory")
        args = body.get("args")
        if not isinstance(args, list) or not args or not all(isinstance(arg, str) for arg in args):
            raise HTTPException(400, "Invalid shell arguments")
        env = {k: v for k, v in env.items() if not k.startswith("SONACODE_COMMAND_")}
        scope = ProcessScope()
        # Spawn and register without yielding; stop cannot miss an in-flight launch.
        try:
            process = scope.spawn([shell, *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except BaseException:
            scope.close()
            raise
        self.scopes.setdefault(key, set()).add(scope)
        self.sessions.setdefault(owner, {})[session] = project
        self.ledger.begin(project, session)

        async def frames():
            completed = False
            remaining = None
            try:
                while True:
                    if remaining is None and process.poll() is not None:
                        # Preserve all bytes buffered when the shell exits, without
                        # waiting forever for a background descendant's new output.
                        remaining = _available_bytes(process.stdout)
                    if remaining is not None:
                        if not remaining:
                            break
                        data = os.read(process.stdout.fileno(), min(remaining, 65536))
                        remaining -= len(data)
                    else:
                        data = _read_available(process.stdout)
                    if data == b"" or (data is None and process.poll() is not None):
                        break
                    if data is None:
                        await asyncio.sleep(.02)
                        continue
                    yield json.dumps({"data": base64.b64encode(data).decode()}) + "\n"
                code = await asyncio.to_thread(process.wait)
                completed = True
                yield json.dumps({"exit": code}) + "\n"
            finally:
                if not completed:
                    await asyncio.shield(asyncio.to_thread(scope.close))
                    self.scopes.get(key, set()).discard(scope)
                process.stdout.close()
                # Retain successful scopes: detached children still belong to this task.

        return StreamingResponse(frames(), media_type="application/x-ndjson")

    def install(self, app: FastAPI) -> None:
        app.add_api_route("/__sona_commands/run", self.run, methods=["POST"])

        @app.post("/__sona_commands/shell")
        async def shell(request: Request):
            self.authenticate(request)
            return self.shell((await request.json()).get("shell", ""))

        @app.post("/__sona_commands/event")
        async def event(request: Request):
            owner, project = self.authenticate(request)
            body = await request.json()
            session, status = body.get("session_id"), body.get("status")
            if not isinstance(session, str) or not session:
                raise HTTPException(400, "Invalid session")
            self.sessions.setdefault(owner, {})[session] = project
            if status == "aborted":
                self.ledger.cancel(project, session, body.get("time"))
            if status == "idle":
                already_blocked = (owner, session) in self.blocked
                await self.stop_session(owner, session)
                self.ledger.finish(project, session, "completed")
                if not already_blocked and not self.closing:
                    self.blocked.discard((owner, session))
            elif status in {"busy", "retry"} and not self.closing and (owner, session) not in self.blocked:
                self.ledger.begin(project, session)
            return {"ok": True}
