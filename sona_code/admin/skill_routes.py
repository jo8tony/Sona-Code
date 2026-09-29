"""Skill management endpoints for the local application."""

from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from sona_code.workspace.manager import WorkspaceError

router = APIRouter()


class ImportSkillBody(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class EnableSkillBody(BaseModel):
    enabled: bool


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, WorkspaceError):
        return HTTPException(exc.status, exc.detail)
    status = 409 if isinstance(exc, FileExistsError) else (
        404 if isinstance(exc, FileNotFoundError) else 400
    )
    return HTTPException(status, str(exc))


@router.get("/skills")
def list_skills(request: Request) -> dict:
    try:
        return request.app.state.runtime.skills.list()
    except (OSError, ValueError) as exc:
        raise _error(exc) from exc


async def _change(request: Request, operation: Callable[[], dict]) -> dict:
    try:
        return await request.app.state.runtime.workspace.update_skills(operation)
    except (OSError, ValueError, WorkspaceError) as exc:
        raise _error(exc) from exc


@router.post("/skills", status_code=201)
async def import_skill(body: ImportSkillBody, request: Request) -> dict:
    return await _change(request, lambda: request.app.state.runtime.skills.add(body.path))


@router.patch("/skills/{skill_id}")
async def enable_skill(skill_id: str, body: EnableSkillBody, request: Request) -> dict:
    return await _change(request, lambda: request.app.state.runtime.skills.set_enabled(skill_id, body.enabled))


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str, request: Request) -> dict:
    return await _change(request, lambda: request.app.state.runtime.skills.delete(skill_id))
