"""终端 API：会话、项目目录、文件浏览与 WebSocket 终端流。

挂载到 {admin_prefix}/api/terminal/*（app.py 中先于兜底代理路由注册）。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Literal

from sona_code.config import AppConfig
from sona_code.terminal.manager import (
    BUNDLED_OPENCODE_ENV,
    PtyProcess,
    TerminalError,
    detect_git_bash,
    detect_shell,
    executable_version,
    resolve_opencode,
    resolve_executable,
)

logger = logging.getLogger("sona_code")

router = APIRouter()


def _manager(request: Request):
    return request.app.state.runtime.terminal


def _cfg(request: Request) -> AppConfig:
    return request.app.state.runtime.provider_config()


def _err(e: TerminalError) -> JSONResponse:
    return JSONResponse(status_code=e.status, content={"detail": e.detail})


# ------------------------------------------------------------------ 会话 CRUD
@router.get("/terminal/sessions")
def list_sessions(request: Request) -> dict:
    return {"items": _manager(request).list(), "total": len(_manager(request).sessions)}


class CreateSessionBody(BaseModel):
    cwd: str
    kind: Literal["opencode", "shell"] = "opencode"
    cols: int = Field(80, ge=10, le=500)
    rows: int = Field(24, ge=4, le=200)


@router.post("/terminal/sessions", status_code=201)
async def create_session(body: CreateSessionBody, request: Request):
    try:
        session = await _manager(request).create(
            _cfg(request), body.cwd, body.kind, body.rows, body.cols
        )
    except TerminalError as e:
        return _err(e)
    info = session.info()
    try:
        request.app.state.runtime.terminal_projects.add(session.cwd, session.kind)
        info["project_saved"] = True
    except Exception:
        logger.warning("保存终端项目失败：%s", session.cwd, exc_info=True)
        info["project_saved"] = False
    return info


@router.get("/terminal/projects")
def list_projects(request: Request) -> dict:
    try:
        items = request.app.state.runtime.terminal_projects.list()
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return JSONResponse(status_code=500, content={"detail": f"读取项目列表失败：{e}"})
    return {"items": items, "total": len(items)}


class DeleteProjectBody(BaseModel):
    path: str


@router.delete("/terminal/projects")
def delete_project(body: DeleteProjectBody, request: Request):
    try:
        deleted = request.app.state.runtime.terminal_projects.delete(body.path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return JSONResponse(status_code=500, content={"detail": f"删除项目失败：{e}"})
    if not deleted:
        return JSONResponse(status_code=404, content={"detail": "project not found"})
    return {"ok": True}


@router.delete("/terminal/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    ok = await _manager(request).kill(session_id)
    if not ok:
        return JSONResponse(status_code=404, content={"detail": "session not found"})
    return {"ok": True, "deleted": 1}


# ------------------------------------------------------------------ 目录浏览
def _windows_drives() -> list[dict]:
    try:
        import ctypes

        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        drives = [f"{chr(65 + i)}:\\" for i in range(26) if bitmask >> i & 1]
        if drives:
            return [{"name": d, "path": d} for d in drives]
    except Exception:
        pass
    return [{"name": "C:\\", "path": "C:\\"}]


@router.get("/terminal/fs")
def browse_fs(request: Request, path: str | None = Query(None)):
    """无 path → 根（盘符）；有 path → 子目录列表（仅目录）。"""
    if not path:
        if os.name == "nt":
            return {"path": None, "parent": None, "entries": _windows_drives()}
        return {"path": None, "parent": None, "entries": [{"name": "/", "path": "/"}]}

    p = Path(path).expanduser()
    if not p.is_dir():
        return JSONResponse(status_code=400, content={"detail": f"目录不存在：{path}"})

    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: x.name.lower()):
            try:
                if child.is_dir():
                    entries.append({"name": child.name, "path": str(child)})
            except OSError:
                continue  # 无权限条目跳过
    except OSError as e:
        return JSONResponse(status_code=400, content={"detail": f"无法读取目录：{e}"})

    parent = str(p.parent) if str(p.parent) != str(p) else None
    return {"path": str(p), "parent": parent, "entries": entries}


# ------------------------------------------------------------------ 命令探测
@router.get("/terminal/check")
def terminal_check(request: Request) -> dict:
    t = _cfg(request).terminal
    resolution = resolve_opencode(_cfg(request))
    opencode_path = resolution.path
    git_bash_path = detect_git_bash()
    bundled_path = os.environ.get(BUNDLED_OPENCODE_ENV, "") if sys.platform == "win32" else ""
    bundled_path = bundled_path if bundled_path and Path(bundled_path).is_file() else ""
    opencode_version = executable_version(opencode_path)
    bundled_version = (
        opencode_version
        if bundled_path
        and opencode_path
        and os.path.normcase(bundled_path) == os.path.normcase(opencode_path)
        else executable_version(bundled_path)
    )
    shell_cmd = t.shell_command or detect_shell()
    shell_path = resolve_executable(shell_cmd)
    return {
        "opencode_found": bool(opencode_path),
        "opencode_path": opencode_path,
        "opencode_command": t.command,
        "opencode_source": resolution.source,
        "opencode_version": opencode_version,
        "bundled_version": bundled_version,
        "git_bash_path": git_bash_path,
        "compatibility_warning": (
            "未检测到 Git Bash；OpenCode 在 Windows 上仍可启动，"
            "但部分 AI shell 命令可能与 cmd.exe 语法不兼容。"
            if sys.platform == "win32" and not git_bash_path
            else None
        ),
        "shell_command": shell_cmd,
        "shell_found": bool(shell_path),
        "enabled": t.enabled,
        "platform": sys.platform,
        "pty_available": PtyProcess is not None,
    }


# ------------------------------------------------------------------ WebSocket
@router.websocket("/terminal/ws/{session_id}")
async def terminal_ws(websocket: WebSocket, session_id: str):
    manager = websocket.app.state.runtime.terminal
    session = manager.get(session_id)
    if session is None:
        await websocket.close(code=4404, reason="session not found")
        return

    await websocket.accept()
    await manager.attach(websocket, session)

    try:
        while True:
            msg = await websocket.receive_text()
            try:
                data = json.loads(msg)
            except (ValueError, TypeError):
                continue
            mtype = data.get("type")
            if mtype == "input":
                error = await manager.write(session, str(data.get("data") or ""))
                if error:
                    await websocket.send_text(
                        json.dumps({"type": "input_error", "detail": error})
                    )
            elif mtype == "resize":
                try:
                    cols = int(data.get("cols") or 80)
                    rows = int(data.get("rows") or 24)
                except (ValueError, TypeError):
                    continue
                manager.resize(session, cols, rows)
    except WebSocketDisconnect:
        pass
    finally:
        manager.detach(websocket, session)
