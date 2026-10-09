"""Website login handoff and read-only scene/model catalog."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from sona_code.config import SonaSiteConfig, UpstreamConfig, UpstreamModelConfig
from sona_code.site_credentials import read_credentials, write_credentials

LOGIN_TTL_SECONDS = 300
BUSINESS_ID = "LZ2103SONA"
INPUT_MODALITIES = {"image", "audio", "video", "pdf"}
DEFAULT_CONTEXT_LENGTH = 256 * 1024
DEFAULT_OUTPUT_LENGTH = 32 * 1024


class SonaSiteError(Exception):
    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


@dataclass
class PendingLogin:
    environment: str
    origin: str
    expires_at: float
    status: str = "pending"
    detail: str = ""


@dataclass
class SiteSession:
    token: str
    user: dict
    providers: list[UpstreamConfig]
    updated_at: float


class SonaSiteManager:
    def __init__(self, config_path: str | None = None) -> None:
        self._pending: dict[str, PendingLogin] = {}
        self._sessions: dict[tuple[str, str], SiteSession] = {}
        self._path = Path(config_path + ".sona-sessions") if config_path else None
        self.startup_refreshing = False
        if self._path:
            for item in read_credentials(self._path):
                try:
                    environment, origin = item["environment"], item["origin"]
                    site = SonaSiteConfig(environment=environment, **{environment + "_url": origin})
                    token = item["token"]
                    if (not isinstance(token, str) or not token or site.active_url() != origin
                            or not isinstance(item["user"], dict)):
                        continue
                    providers = [UpstreamConfig.model_validate(p) for p in item["providers"]]
                    if any(p.source != "sona" for p in providers):
                        continue
                    for provider in providers:
                        for model in provider.models:
                            model.context_length = model.context_length or DEFAULT_CONTEXT_LENGTH
                            model.output_length = model.output_length or DEFAULT_OUTPUT_LENGTH
                    self._sessions[(environment, origin)] = SiteSession(
                        token, item["user"], providers, float(item["updated_at"]))
                except (ValueError, KeyError, TypeError):
                    continue

    def _save(self, sessions: dict[tuple[str, str], SiteSession]) -> None:
        if not self._path:
            return
        try:
            write_credentials(self._path, [
                {"environment": env, "origin": origin, "token": session.token,
                 "user": session.user, "updated_at": session.updated_at,
                 "providers": [p.model_dump() for p in session.providers]}
                for (env, origin), session in sessions.items()
            ])
        except OSError as exc:
            raise SonaSiteError("保存网站登录凭据失败，请检查配置目录权限", 503) from exc

    def start_login(self, site: SonaSiteConfig, callback_url: str) -> tuple[str, str]:
        origin = site.active_url()
        if not origin:
            raise SonaSiteError("请先设置当前环境的网站前端地址", 409)
        now = time.monotonic()
        # Only the newest handoff may complete, including callbacks already
        # fetching the catalog when the user cancels and starts again.
        self._pending.clear()
        state = secrets.token_urlsafe(32)
        self._pending[state] = PendingLogin(site.environment, origin, now + LOGIN_TTL_SECONDS)
        return state, (f"{origin}/#/desktop-connect?state={quote(state)}"
                       f"&callback={quote(callback_url, safe='')}")

    def login_status(self, state: str, site: SonaSiteConfig) -> dict:
        pending = self._pending.get(state)
        if pending is None or pending.expires_at <= time.monotonic():
            raise SonaSiteError("登录请求已失效，请返回 App 重新登录", 400)
        if pending.environment != site.environment or pending.origin != site.active_url():
            raise SonaSiteError("网站环境已变化，请返回 App 重新登录", 409)
        return {"status": pending.status, "detail": pending.detail}

    def cancel_login(self, state: str) -> None:
        self._pending.pop(state, None)

    def require_login(self, state: str, site: SonaSiteConfig) -> None:
        if self.login_status(state, site)["status"] != "processing":
            raise SonaSiteError("登录请求已失效，请返回 App 重新登录", 400)

    def finish_login(self, state: str, detail: str = "") -> None:
        pending = self._pending.get(state)
        if pending and pending.status == "processing":
            pending.status = "error" if detail else "completed"
            pending.detail = detail

    def consume_login(self, state: str, site: SonaSiteConfig) -> tuple[str, str]:
        if self.login_status(state, site)["status"] != "pending":
            raise SonaSiteError("登录请求已失效，请返回 App 重新登录", 400)
        pending = self._pending[state]
        pending.status = "processing"
        return pending.environment, pending.origin

    def session(self, site: SonaSiteConfig) -> SiteSession | None:
        return self._sessions.get((site.environment, site.active_url()))

    def set_session(self, environment: str, origin: str, session: SiteSession) -> None:
        sessions = {**self._sessions, (environment, origin): session}
        self._save(sessions)
        self._sessions = sessions

    def clear(self, site: SonaSiteConfig) -> None:
        sessions = dict(self._sessions)
        sessions.pop((site.environment, site.active_url()), None)
        self._save(sessions)
        self._sessions = sessions

    def clear_environment(self, environment: str) -> None:
        sessions = {key: session for key, session in self._sessions.items() if key[0] != environment}
        self._save(sessions)
        self._sessions = sessions

    def status(self, site: SonaSiteConfig) -> dict:
        session = self.session(site)
        return {
            "connected": session is not None,
            "startup_refreshing": self.startup_refreshing,
            "updated_at": session.updated_at if session else None,
            "user": {key: session.user[key] for key in ("id", "userId", "userName")
                     if key in session.user} if session else None,
            "providers": [
                {"id": provider.name, "name": provider.display_name,
                 "models": [{"id": model.id, "name": model.display_name or model.id}
                            for model in provider.models]}
                for provider in session.providers
            ] if session else [],
        }

    async def load_catalog(self, origin: str, token: str, environment: str) -> SiteSession:
        # Scene subscriptions belong to the user, independently of managed
        # spaces. Without an explicit role the website chooses the highest
        # role, making RESOURCE_ADMIN requests require an unrelated space.
        headers = {"Authorization": token, "X-B3-BusinessId": BUSINESS_ID,
                   "X-Active-Role": "USER",
                   "Cache-Control": "no-cache, no-store", "Pragma": "no-cache"}
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                user = await _get_body(client, f"{origin}/api/v1/auth/current-user", headers)
                if not isinstance(user, dict) or not user.get("id"):
                    raise SonaSiteError("网站登录凭证无效", 401)
                user = {key: user[key] for key in ("id", "userId", "userName") if key in user}
                scenes = await _get_body(client, f"{origin}/api/v1/scenes", headers)
                if not isinstance(scenes, list):
                    raise SonaSiteError("网站场景列表格式无效", 502)
                providers = []
                for scene in scenes:
                    if not isinstance(scene, dict) or scene.get("status") != 1:
                        continue
                    scene_id = scene.get("id")
                    if not isinstance(scene_id, int) or scene_id <= 0:
                        continue
                    info = await _get_body(
                        client, f"{origin}/api/v1/scenes/{scene_id}/integration-info", headers)
                    if not isinstance(info, dict):
                        raise SonaSiteError("网站场景接入信息格式无效", 502)
                    key = info.get("apiKey")
                    if not isinstance(key, str) or not key:
                        continue
                    models = []
                    seen = set()
                    for item in info.get("availableModels") or []:
                        if not isinstance(item, dict):
                            continue
                        name = item.get("modelName")
                        if not isinstance(name, str) or not name.strip() or name in seen:
                            continue
                        seen.add(name)
                        modalities = item.get("modalities") or []
                        models.append(UpstreamModelConfig(
                            id=name, display_name=name, api_type="chat_completions",
                            context_length=DEFAULT_CONTEXT_LENGTH,
                            output_length=DEFAULT_OUTPUT_LENGTH,
                            input_modalities=[value for value in modalities
                                              if value in INPUT_MODALITIES] if isinstance(modalities, list) else [],
                        ))
                    if models:
                        providers.append(UpstreamConfig(
                            name=f"sona-{environment}-{scene_id}",
                            display_name=str(info.get("sceneName") or scene.get("name") or scene_id),
                            base_url=f"{origin}/api/v1", api_key=key,
                            api_type="chat_completions", route_through_proxy=True,
                            key_strategy="replace", source="sona", models=models,
                            extra_headers={"X-B3-BusinessId": BUSINESS_ID},
                        ))
        except httpx.HTTPError as exc:
            raise SonaSiteError("连接网站失败，请检查前端地址和网络", 502) from exc
        return SiteSession(token=token, user=user, providers=providers, updated_at=time.time())


async def _get_body(client: httpx.AsyncClient, url: str, headers: dict[str, str]):
    # Some intranet gateways cache GET responses. Each catalog read must reach
    # the platform even when a scene's subscriptions changed since login.
    response = await client.get(url, headers=headers, params={"_sona_refresh": secrets.token_hex(8)})
    if response.status_code in {301, 302, 303, 307, 308, 401, 403}:
        raise SonaSiteError("网站登录已失效或没有访问权限", 401)
    if response.is_error:
        raise SonaSiteError(f"网站接口返回 HTTP {response.status_code}", 502)
    try:
        data = response.json()
    except ValueError as exc:
        raise SonaSiteError("网站接口未返回 JSON 数据", 502) from exc
    if isinstance(data, dict) and "returnCode" in data:
        if data.get("returnCode") != "SUC0000":
            if data.get("returnCode") in {"INF0002", "INF1001", "INF1002"}:
                raise SonaSiteError("网站登录已失效，请重新登录", 401)
            raise SonaSiteError(str(data.get("errorMsg") or "网站接口返回错误"), 502)
        return data.get("body")
    return data
