"""应用工厂：RuntimeState + 管理 ping + 兜底透明代理路由。"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from llm_api_proxy_recorder.admin.api import router as admin_router
from llm_api_proxy_recorder.admin.skills import SkillStore
from llm_api_proxy_recorder.admin.model_routes import router as models_router
from llm_api_proxy_recorder.admin.skill_routes import router as skills_router
from llm_api_proxy_recorder.config import CONFIG_PATH, AppConfig, resolved_records_dir
from llm_api_proxy_recorder.proxy.client import UpstreamClient
from llm_api_proxy_recorder.proxy.handler import proxy_endpoint
from llm_api_proxy_recorder.recording.store import CallStore
from llm_api_proxy_recorder.terminal import TerminalManager
from llm_api_proxy_recorder.terminal.projects import TerminalProjectStore
from llm_api_proxy_recorder.terminal.routes import router as terminal_router
from llm_api_proxy_recorder.workspace import WorkspaceManager
from llm_api_proxy_recorder.workspace.routes import router as workspace_router, configure_workspace_queue
from llm_api_proxy_recorder.workspace.queue import WorkspaceQueue
from llm_api_proxy_recorder.sona_site import SonaSiteManager
from llm_api_proxy_recorder.admin.sona_routes import router as sona_router

logger = logging.getLogger("llm_api_proxy_recorder")

PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

# 保留策略自动清理间隔
RETENTION_SWEEP_SECONDS = 3600


async def _retention_sweep(runtime: "RuntimeState") -> None:
    """按 retention_days 清理过期日期记录；异常只记日志，绝不影响代理。"""
    days = runtime.config.recording.retention_days
    if days <= 0:
        return
    try:
        removed = await asyncio.to_thread(runtime.store.cleanup_older_than, days)
        if removed:
            logger.info("保留策略清理：retention=%d 天，删除日期 %s", days, ", ".join(removed))
    except Exception:
        logger.warning("保留策略清理失败", exc_info=True)


class NoCacheStaticFiles(StaticFiles):
    """Web UI 静态资源：允许缓存但每次强制重新验证（no-cache）。

    无此头时浏览器走启发式缓存，代码更新后页面可能长期停留在旧 JS。
    未变更文件仍可 304 快速返回，不影响性能。
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


class RuntimeState:
    """运行时共享状态：配置、上游客户端、落盘存储。"""

    def __init__(self, config: AppConfig, config_path: str):
        self.config = config
        self.config_lock = asyncio.Lock()
        self.pending_server = config.server.model_copy()
        self.upstream_client = UpstreamClient(config.outbound.proxy_url)
        self.store = CallStore(resolved_records_dir(config))
        self.terminal = TerminalManager()
        self.terminal_projects = TerminalProjectStore(config_path)
        self.sona_site = SonaSiteManager()
        self.workspace = WorkspaceManager(self.provider_config)
        self.workspace_queue = WorkspaceQueue(config_path)
        self.skills = SkillStore()

    def provider_config(self) -> AppConfig:
        """Combine editable local providers with the current website catalog in memory."""
        session = self.sona_site.session(self.config.sona_site)
        remote = session.providers if session and self.config.model_settings.source == "sona" else []
        return self.config.model_copy(update={"upstreams": [*self.config.upstreams, *remote]})

    async def apply_config(self, new_cfg: AppConfig, *, restart_workspace: bool = True) -> None:
        """热更新：换 config 引用；出站代理变化时重建客户端；记录目录变化时重建 store。"""
        old_cfg = self.config
        old_proxy = self.config.outbound.proxy_url
        old_dir = resolved_records_dir(self.config)
        self.config = new_cfg
        for environment in ("uat", "prod"):
            field = f"{environment}_url"
            if getattr(new_cfg.sona_site, field) != getattr(old_cfg.sona_site, field):
                self.sona_site.clear_environment(environment)
        if new_cfg.outbound.proxy_url != old_proxy:
            await self.upstream_client.rebuild(new_cfg.outbound.proxy_url)
        if resolved_records_dir(new_cfg) != old_dir:
            self.store = CallStore(resolved_records_dir(new_cfg))
        if restart_workspace and (
            new_cfg.model_settings != old_cfg.model_settings
            or new_cfg.terminal != old_cfg.terminal
            or new_cfg.upstreams != old_cfg.upstreams
            or new_cfg.default_upstream != old_cfg.default_upstream
            or new_cfg.sona_site != old_cfg.sona_site
            or new_cfg.server.port != old_cfg.server.port
        ):
            await self.workspace_queue.shutdown()
            await self.workspace.shutdown()

    async def aclose(self) -> None:
        await self.workspace_queue.shutdown()
        await self.workspace.shutdown()
        await self.upstream_client.aclose()


def create_app(cfg: AppConfig, config_path: str | None = None) -> FastAPI:
    runtime = RuntimeState(cfg, config_path or CONFIG_PATH)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(runtime.skills.migrate)
        # 启动即执行一次保留清理，此后每小时一次；失败不影响服务
        await _retention_sweep(runtime)

        async def _loop() -> None:
            while True:
                await asyncio.sleep(RETENTION_SWEEP_SECONDS)
                await _retention_sweep(runtime)

        sweeper = asyncio.create_task(_loop())
        try:
            yield
        finally:
            sweeper.cancel()
            # 终止全部终端会话进程，避免孤儿进程
            try:
                await runtime.terminal.shutdown()
            except Exception:
                logger.warning("终端会话清理失败", exc_info=True)
            await runtime.aclose()

    app = FastAPI(title="llm-api-proxy-recorder", lifespan=lifespan)
    app.state.runtime = runtime
    configure_workspace_queue(app)
    app.state.config = cfg  # 兼容旧引用
    app.state.config_path = config_path or CONFIG_PATH

    # 管理 API（必须先于兜底路由注册：Starlette 按注册顺序匹配）
    @app.get(f"{cfg.server.admin_prefix}/api/ping")
    def ping() -> dict:
        result = {"ok": True}
        instance_id = os.environ.get("LLMPR_DESKTOP_INSTANCE_ID")
        if instance_id:
            result["instance_id"] = instance_id
        return result

    # 管理 API 路由集（ping 之后、兜底代理路由之前）
    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request, exc):
        # Pydantic includes submitted input by default, including API keys.
        errors = [{k: v for k, v in error.items() if k not in {"input", "ctx"}} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    app.include_router(admin_router, prefix=f"{cfg.server.admin_prefix}/api")
    app.include_router(models_router, prefix=f"{cfg.server.admin_prefix}/api")
    app.include_router(skills_router, prefix=f"{cfg.server.admin_prefix}/api")
    app.include_router(sona_router, prefix=f"{cfg.server.admin_prefix}/api")

    # 终端 API（REST + WebSocket，同样先于兜底代理路由注册）
    app.include_router(terminal_router, prefix=f"{cfg.server.admin_prefix}/api")
    app.include_router(workspace_router, prefix=f"{cfg.server.admin_prefix}/api")

    # 静态 Web UI：admin 路由已注册在前，不会被吞掉 {admin_prefix}/api/*
    static_dir = Path(__file__).parent / "web" / "static"
    # 裸访问 {admin_prefix}（无尾斜杠）重定向到 UI 首页，避免落入兜底代理
    @app.get(cfg.server.admin_prefix, include_in_schema=False)
    def admin_root() -> RedirectResponse:
        return RedirectResponse(url=f"{cfg.server.admin_prefix}/")

    app.mount(f"{cfg.server.admin_prefix}", NoCacheStaticFiles(directory=str(static_dir), html=True), name="ui")

    # 兜底透明代理路由；/{path:path} 不匹配根路径 "/"，需单独注册
    app.add_api_route("/", proxy_endpoint, methods=PROXY_METHODS)
    app.add_api_route("/{path:path}", proxy_endpoint, methods=PROXY_METHODS)
    return app
