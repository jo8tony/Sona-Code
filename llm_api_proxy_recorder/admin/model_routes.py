"""Versioned model catalog mutations, shared by settings and model management."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from llm_api_proxy_recorder.admin.models import catalog_revision, catalog_view, merge_providers, validate_config
from llm_api_proxy_recorder.config import AppConfig, save_config
from llm_api_proxy_recorder.workspace.manager import WorkspaceError

router = APIRouter()


class CatalogUpdate(BaseModel):
    revision: str
    providers: list[dict] = Field(max_length=200)
    default_upstream: str = ""
    default_model: dict | None = None
    show_native_models: bool = False


async def commit_config(request: Request, cfg: AppConfig, *, server_explicit: bool = False) -> dict:
    runtime = request.app.state.runtime
    if not server_explicit:
        cfg = cfg.model_copy(update={"server": runtime.pending_server})
    restart_required = cfg.server != runtime.config.server
    await asyncio.to_thread(save_config, cfg, request.app.state.config_path)
    runtime.pending_server = cfg.server.model_copy()
    # Listening fields become active only on application restart.
    applied = cfg.model_copy(update={"server": runtime.config.server}) if restart_required else cfg
    await runtime.apply_config(applied, restart_workspace=False)
    request.app.state.config = applied
    return {"ok": True, "restart_required": restart_required,
            "terminal_restart_required": bool(runtime.terminal.sessions)}


@router.get("/models/config")
def get_catalog(request: Request) -> dict:
    return catalog_view(request.app.state.runtime.config)


@router.put("/models/config")
async def put_catalog(body: CatalogUpdate, request: Request) -> dict:
    runtime = request.app.state.runtime
    async with runtime.config_lock:
        async def apply() -> dict:
            current = runtime.config
            if body.revision != catalog_revision(current):
                raise HTTPException(409, "模型配置已在别处修改，请重新加载后再保存")
            data = current.model_dump()
            data["upstreams"] = merge_providers(body.providers, current)
            names = [p.get("name") for p in data["upstreams"]]
            data["default_upstream"] = body.default_upstream or (names[0] if names else "")
            data["model_settings"] = {"default_model": body.default_model,
                                      "show_native_models": body.show_native_models,
                                      "source": current.model_settings.source}
            cfg = validate_config(data)
            result = await commit_config(request, cfg)
            return {**catalog_view(cfg), **result}
        try:
            return await runtime.workspace.update_configuration(apply)
        except WorkspaceError as exc:
            raise HTTPException(exc.status, exc.detail) from None
