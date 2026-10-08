"""Local project management for the bundled OpenSpec integration."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from sona_code.openspec.bundle import OpenSpecError
from sona_code.openspec.projects import project_key
from sona_code.workspace.manager import WorkspaceError
from sona_code.workspace.routes import _opencode, _project_path

router = APIRouter()


def _error(exc: OpenSpecError | WorkspaceError) -> HTTPException:
    if isinstance(exc, OpenSpecError) and exc.conflicts:
        return HTTPException(exc.status, {"message": exc.detail, "conflicts": exc.conflicts})
    return HTTPException(exc.status, exc.detail)


async def native_check(request: Request, path: str) -> list[str]:
    """Check the actual V1 command templates and skill locations, not just names."""
    store = request.app.state.runtime.openspec
    commands, skills = await asyncio.gather(_opencode(request, path, "GET", "/command"),
                                           _opencode(request, path, "GET", "/skill"))
    command_map = {item["name"]: item for item in commands}
    locations = {item["name"]: item.get("location", "") for item in skills}
    unavailable = []
    for command, skill in store.record(path)["workflows"].items():
        item = command_map.get(command, {})
        actual = item.get("template")
        expected = await asyncio.to_thread(store.bundle.command_template, command)
        location = locations.get(skill, "")
        expected_location = Path(path) / ".opencode/skills" / skill / "SKILL.md"
        if (not isinstance(actual, str) or actual.strip() != expected or not location
                or os.path.normcase(str(Path(location).resolve())) != os.path.normcase(str(expected_location.resolve()))):
            unavailable.append(command)
    return unavailable


async def project_status(request: Request, path: str) -> dict:
    runtime = request.app.state.runtime
    result = await asyncio.to_thread(runtime.openspec.status, path)
    result["terminal_restart_required"] = any(project_key(session.cwd) == project_key(path) and session.kind == "opencode"
                                               for session in runtime.terminal.sessions.values())
    if result["state"] == "prepared" and result["available"]:
        try:
            unavailable = await native_check(request, path)
            result.update(state="error" if unavailable else "ready", unavailable=unavailable)
            if unavailable:
                result["error"] = "OpenCode 未加载预期的 OpenSpec 命令或技能，请检查同名配置与技能权限"
        except (OpenSpecError, HTTPException) as exc:
            result.update(state="error", error=str(exc.detail))
    return result


@router.get("/openspec/info")
def openspec_info(request: Request) -> dict:
    return request.app.state.runtime.openspec.bundle.info()


@router.get("/workspace/projects/{project_id}/openspec")
async def status(project_id: str, request: Request) -> dict:
    try:
        return await project_status(request, _project_path(request, project_id))
    except OpenSpecError as exc:
        raise _error(exc) from exc


async def _change(project_id: str, request: Request, action: str) -> dict:
    path = _project_path(request, project_id)
    runtime = request.app.state.runtime
    # A TUI owns a separate native process whose active state cannot be vouched
    # for by the workspace's HTTP server. Require closing it before file mutation.
    if any(project_key(session.cwd) == project_key(path) and session.kind == "opencode"
           for session in runtime.terminal.sessions.values()):
        raise HTTPException(409, "请先关闭当前项目的 OpenCode 终端，准备完成后重新打开")

    async def apply() -> dict:
        method = runtime.openspec.disable if action == "disable" else runtime.openspec.enable
        return await asyncio.to_thread(method, path)

    try:
        await runtime.workspace.update_project_configuration(path, apply)
        return await project_status(request, path)
    except (OpenSpecError, WorkspaceError) as exc:
        raise _error(exc) from exc


@router.post("/workspace/projects/{project_id}/openspec/enable")
async def enable(project_id: str, request: Request) -> dict:
    return await _change(project_id, request, "enable")


@router.post("/workspace/projects/{project_id}/openspec/update")
async def update(project_id: str, request: Request) -> dict:
    return await _change(project_id, request, "update")


@router.post("/workspace/projects/{project_id}/openspec/disable")
async def disable(project_id: str, request: Request) -> dict:
    return await _change(project_id, request, "disable")
