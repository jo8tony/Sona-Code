"""Model source selection and browser-to-local website login handoff."""

from __future__ import annotations

import html
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from llm_api_proxy_recorder.admin.model_routes import commit_config
from llm_api_proxy_recorder.config import AppConfig
from llm_api_proxy_recorder.sona_site import SonaSiteError
from llm_api_proxy_recorder.workspace.manager import WorkspaceError

router = APIRouter()


class SourceUpdate(BaseModel):
    source: str
    environment: str
    uat_url: str
    prod_url: str


def _source_view(request: Request) -> dict:
    runtime = request.app.state.runtime
    cfg = runtime.config
    return {
        "source": cfg.model_settings.source,
        "environment": cfg.sona_site.environment,
        "uat_url": cfg.sona_site.uat_url,
        "prod_url": cfg.sona_site.prod_url,
        **runtime.sona_site.status(cfg.sona_site),
    }


@router.get("/models/source")
def get_source(request: Request) -> dict:
    return _source_view(request)


@router.put("/models/source")
async def put_source(body: SourceUpdate, request: Request) -> dict:
    runtime = request.app.state.runtime
    async with runtime.config_lock:
        async def apply() -> dict:
            data = runtime.config.model_dump()
            data["model_settings"]["source"] = body.source
            data["sona_site"] = {
                "environment": body.environment,
                "uat_url": body.uat_url,
                "prod_url": body.prod_url,
            }
            try:
                cfg = AppConfig.model_validate(data)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            await commit_config(request, cfg)
            return _source_view(request)
        try:
            return await runtime.workspace.update_configuration(apply, "模型来源")
        except WorkspaceError as exc:
            raise HTTPException(exc.status, exc.detail) from None


@router.post("/models/sona/login/start")
def start_login(request: Request) -> dict:
    runtime = request.app.state.runtime
    cfg = runtime.config
    callback = (f"http://127.0.0.1:{cfg.server.port}{cfg.server.admin_prefix}"
                "/api/models/sona/callback")
    try:
        url = runtime.sona_site.start_login(cfg.sona_site, callback)
    except SonaSiteError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    return {"url": url}


def _callback_page(message: str, success: bool) -> HTMLResponse:
    title = "连接成功" if success else "连接失败"
    content = ("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
               "<meta name='viewport' content='width=device-width, initial-scale=1'>"
               f"<title>{title}</title></head><body style='font:16px sans-serif;"
               "max-width:38rem;margin:12vh auto;padding:0 1.5rem'>"
               f"<h1>{title}</h1><p>{html.escape(message)}</p></body></html>")
    return HTMLResponse(content, status_code=200 if success else 400,
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.post("/models/sona/callback")
async def login_callback(request: Request) -> HTMLResponse:
    if not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        return _callback_page("无效的登录提交方式", False)
    raw = await request.body()
    if len(raw) > 20_000:
        return _callback_page("登录数据过大", False)
    try:
        form = parse_qs(raw.decode("utf-8"), strict_parsing=True)
    except (ValueError, UnicodeError):
        return _callback_page("登录数据无效", False)
    states, tokens = form.get("state", []), form.get("token", [])
    if len(states) != 1 or len(tokens) != 1 or not states[0] or not tokens[0]:
        return _callback_page("缺少登录数据", False)
    runtime = request.app.state.runtime
    try:
        environment, origin = runtime.sona_site.consume_login(states[0], runtime.config.sona_site)
        session = await runtime.sona_site.load_catalog(origin, tokens[0], environment)

        async def apply() -> dict:
            runtime.sona_site.set_session(environment, origin, session)
            return {"ok": True}

        await runtime.workspace.update_configuration(apply, "网站模型")
    except (SonaSiteError, WorkspaceError) as exc:
        return _callback_page(exc.detail, False)
    return _callback_page("已连接 Sona Code，可以返回桌面 App。", True)


@router.post("/models/sona/refresh")
async def refresh_catalog(request: Request) -> dict:
    runtime = request.app.state.runtime
    cfg = runtime.config.sona_site
    current = runtime.sona_site.session(cfg)
    if current is None:
        raise HTTPException(401, "请先登录网站")
    try:
        updated = await runtime.sona_site.load_catalog(cfg.active_url(), current.token, cfg.environment)

        async def apply() -> dict:
            runtime.sona_site.set_session(cfg.environment, cfg.active_url(), updated)
            return _source_view(request)

        return await runtime.workspace.update_configuration(apply, "网站模型")
    except SonaSiteError as exc:
        if exc.status == 401:
            try:
                await runtime.workspace.update_configuration(
                    lambda: _clear_expired_session(runtime), "网站登录")
            except WorkspaceError as blocked:
                raise HTTPException(blocked.status, blocked.detail) from None
        raise HTTPException(exc.status, exc.detail) from None
    except WorkspaceError as exc:
        raise HTTPException(exc.status, exc.detail) from None


async def _clear_expired_session(runtime) -> dict:
    runtime.sona_site.clear(runtime.config.sona_site)
    return {"ok": True}


@router.post("/models/sona/logout")
async def logout(request: Request) -> dict:
    runtime = request.app.state.runtime
    try:
        async def apply() -> dict:
            runtime.sona_site.clear(runtime.config.sona_site)
            return _source_view(request)
        return await runtime.workspace.update_configuration(apply, "网站登录")
    except WorkspaceError as exc:
        raise HTTPException(exc.status, exc.detail) from None
