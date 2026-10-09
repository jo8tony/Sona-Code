"""Model source selection and browser-to-local website login handoff."""

from __future__ import annotations

import html
import json
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from sona_code.admin.model_routes import commit_config
from sona_code.config import AppConfig
from sona_code.sona_site import SonaSiteError
from sona_code.workspace.manager import WorkspaceError

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
async def start_login(request: Request) -> dict:
    runtime = request.app.state.runtime
    if runtime.sona_site.session(runtime.config.sona_site):
        try:
            await runtime.refresh_sona_catalog()
            return {"connected": True}
        except SonaSiteError as exc:
            if exc.status != 401:
                raise HTTPException(exc.status, exc.detail) from None
        except WorkspaceError as exc:
            raise HTTPException(exc.status, exc.detail) from None
    cfg = runtime.config
    callback = (f"http://127.0.0.1:{cfg.server.port}{cfg.server.admin_prefix}"
                "/api/models/sona/callback")
    try:
        state, url = runtime.sona_site.start_login(cfg.sona_site, callback)
    except SonaSiteError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    return {"url": url, "state": state}


@router.get("/models/sona/login/{state}")
def login_status(state: str, request: Request) -> dict:
    runtime = request.app.state.runtime
    try:
        return runtime.sona_site.login_status(state, runtime.config.sona_site)
    except SonaSiteError as exc:
        raise HTTPException(exc.status, exc.detail) from None


@router.delete("/models/sona/login/{state}")
async def cancel_login(state: str, request: Request) -> dict:
    request.app.state.runtime.sona_site.cancel_login(state)
    return {"ok": True}


def _callback_page(message: str, success: bool, origin: str = "") -> HTMLResponse:
    title = "连接成功" if success else "连接失败"
    # External browser tabs may refuse script closing. Return to the validated
    # website origin in that case, without leaving the local callback address.
    script = ("<script>window.close();setTimeout(function(){location.replace("
              + json.dumps(origin + "/", ensure_ascii=True).replace("<", "\\u003c")
              + ");},100);</script>") if success else ""
    content = ("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
               "<meta name='viewport' content='width=device-width, initial-scale=1'>"
               f"<title>{title}</title>{script}</head><body style='font:16px sans-serif;"
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
    claimed = False
    try:
        environment, origin = runtime.sona_site.consume_login(states[0], runtime.config.sona_site)
        claimed = True
        session = await runtime.sona_site.load_catalog(origin, tokens[0], environment)

        async def apply() -> dict:
            runtime.sona_site.require_login(states[0], runtime.config.sona_site)
            if (runtime.config.sona_site.environment != environment
                    or runtime.config.sona_site.active_url() != origin):
                raise SonaSiteError("网站环境已变化，请重新登录", 409)
            runtime.sona_site.set_session(environment, origin, session)
            runtime.sona_site.finish_login(states[0])
            return {"ok": True}

        async with runtime.config_lock:
            await runtime.workspace.update_configuration(apply, "网站模型")
    except (SonaSiteError, WorkspaceError) as exc:
        if claimed:
            runtime.sona_site.finish_login(states[0], exc.detail)
        return _callback_page(exc.detail, False)
    return _callback_page("已连接 Sona Code，可以返回桌面 App。", True, origin)


@router.post("/models/sona/refresh")
async def refresh_catalog(request: Request) -> dict:
    runtime = request.app.state.runtime
    try:
        await runtime.refresh_sona_catalog()
        return _source_view(request)
    except SonaSiteError as exc:
        raise HTTPException(exc.status, exc.detail) from None
    except WorkspaceError as exc:
        raise HTTPException(exc.status, exc.detail) from None


@router.post("/models/sona/logout")
async def logout(request: Request) -> dict:
    runtime = request.app.state.runtime
    try:
        async def apply() -> dict:
            runtime.sona_site.clear(runtime.config.sona_site)
            return _source_view(request)
        async with runtime.config_lock:
            return await runtime.workspace.update_configuration(apply, "网站登录")
    except (SonaSiteError, WorkspaceError) as exc:
        raise HTTPException(exc.status, exc.detail) from None
