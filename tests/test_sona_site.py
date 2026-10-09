"""Website login handoff and scene catalog use existing XT APIs."""

from urllib.parse import parse_qs, urlsplit
import asyncio
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from sona_code.admin.models import route_token
from sona_code.app import create_app
from sona_code.config import default_config
from sona_code.proxy.handler import _build_forward_headers
from sona_code.proxy.router import build_upstream_url, resolve_upstream
from sona_code.sona_site import SiteSession, SonaSiteError, SonaSiteManager
from sona_code.config import UpstreamConfig, UpstreamModelConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("roles", [["USER"], ["USER", "RESOURCE_ADMIN"], ["USER", "SUPER_ADMIN"]])
async def test_catalog_uses_frontend_origin_and_scene_keys(monkeypatch, roles):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.url.host == "sona.example.test"
        assert request.headers["Authorization"] == "site-token"
        assert request.headers["X-B3-BusinessId"] == "LZ2103SONA"
        assert request.headers["X-Active-Role"] == "USER"
        assert "X-Space-Id" not in request.headers
        bodies = {
            "/api/v1/auth/current-user": {"id": 7, "userName": "用户", "token": "extra-secret", "roles": roles},
            "/api/v1/scenes": [{"id": 12, "name": "场景 A", "status": 1},
                                {"id": 13, "name": "停用场景", "status": 0}],
            "/api/v1/scenes/12/integration-info": {
                "sceneName": "场景 A", "apiKey": "scene-secret",
                "availableModels": [{"modelName": "model-a", "modalities": ["image"]}],
            },
        }
        return httpx.Response(200, json={"returnCode": "SUC0000", "body": bodies[request.url.path]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr("sona_code.sona_site.httpx.AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    session = await SonaSiteManager().load_catalog("https://sona.example.test", "site-token", "uat")
    assert [request.url.path for request in calls] == [
        "/api/v1/auth/current-user", "/api/v1/scenes", "/api/v1/scenes/12/integration-info"]
    assert session.providers[0].base_url == "https://sona.example.test/api/v1"
    assert session.providers[0].api_key == "scene-secret"
    assert session.providers[0].models[0].id == "model-a"
    assert session.providers[0].models[0].context_length == 262144
    assert session.providers[0].models[0].output_length == 32768
    from sona_code.admin.models import compile_providers
    compiled = compile_providers(default_config().model_copy(update={"upstreams": session.providers}))
    assert next(iter(compiled.values()))["models"]["model-a"]["limit"] == {"context": 262144, "output": 32768}
    assert session.user == {"id": 7, "userName": "用户"}
    cfg = default_config().model_copy(update={"upstreams": session.providers})
    path = f"/managed/{route_token(session.providers[0].name)}/{route_token('model-a')}/chat/completions"
    upstream, tail = resolve_upstream(path, cfg)
    assert build_upstream_url(upstream.base_url, tail, "") == "https://sona.example.test/api/v1/chat/completions"
    headers = dict(_build_forward_headers(Request({"type": "http", "path": path,
        "headers": [(b"authorization", b"Bearer wrong")]}), upstream))
    assert headers["authorization"] == "Bearer scene-secret"
    assert headers["X-B3-BusinessId"] == "LZ2103SONA"


def test_login_callback_is_one_time_and_source_status_hides_secrets(tmp_path):
    cfg = default_config()
    app = create_app(cfg, str(tmp_path / "config.json"))

    async def catalog(origin, token, environment):
        assert origin == cfg.sona_site.prod_url
        assert environment == "prod"
        assert token == "site-secret"
        return SiteSession(token, {"id": 7, "userName": "用户"}, [], 0)

    app.state.runtime.sona_site.load_catalog = catalog
    with TestClient(app) as client:
        prefix = "/__recorder/api/models"
        source = client.get(prefix + "/source").json()
        assert source["source"] == "sona"
        assert source["prod_url"] == "https://sona.paasoa.cmbchina.cn"
        start = client.post(prefix + "/sona/login/start").json()["url"]
        params = parse_qs(urlsplit(start).fragment.partition("?")[2])
        state = params["state"][0]
        assert params["callback"] == ["http://127.0.0.1:8117/__recorder/api/models/sona/callback"]
        callback = client.post(prefix + "/sona/callback", data={"state": state, "token": "site-secret"})
        assert callback.status_code == 200
        assert "window.close()" in callback.text
        assert "location.replace" in callback.text
        assert cfg.sona_site.prod_url in callback.text
        assert "site-secret" not in callback.text
        assert client.get(prefix + "/source").json()["connected"] is True
        assert "site-secret" not in client.get(prefix + "/source").text
        assert client.post(prefix + "/sona/callback", data={"state": state, "token": "site-secret"}).status_code == 400
        config_path = tmp_path / "config.json"
        assert not config_path.exists() or "site-secret" not in config_path.read_text()


def test_site_environment_switch_keeps_separate_login(tmp_path):
    app = create_app(default_config(), str(tmp_path / "config.json"))
    with TestClient(app) as client:
        prefix = "/__recorder/api/models/source"
        source = client.get(prefix).json()
        app.state.runtime.sona_site.set_session("prod", source["prod_url"],
                                                SiteSession("secret", {"id": 1}, [], 0))
        assert client.get(prefix).json()["connected"]
        source["environment"] = "uat"
        assert client.put(prefix, json=source).status_code == 200
        assert client.get(prefix).json()["connected"] is False
        source["environment"] = "prod"
        assert client.put(prefix, json=source).status_code == 200
        assert client.get(prefix).json()["connected"] is True


def test_refresh_fetches_new_subscriptions_and_updates_native_config(tmp_path, monkeypatch):
    cfg = default_config()
    cfg.recording.dir = str(tmp_path / "records")
    app = create_app(cfg, str(tmp_path / "config.json"))
    project = tmp_path / "project"
    project.mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    platform_calls = []
    native_calls = []
    generation = 0

    def respond(request):
        platform_calls.append(request)
        assert request.headers["Cache-Control"] == "no-cache, no-store"
        assert request.url.params["_sona_refresh"]
        bodies = {
            "/api/v1/auth/current-user": {"id": 7},
            "/api/v1/scenes": [{"id": 12, "status": 1}],
            "/api/v1/scenes/12/integration-info": {
                "apiKey": "test-scene-key", "sceneName": "Test scene",
                "availableModels": [{"modelName": "first"}, *(
                    [{"modelName": "new-subscription"}] if generation else [])],
            },
        }
        return httpx.Response(200, json={"returnCode": "SUC0000", "body": bodies[request.url.path]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr("sona_code.sona_site.httpx.AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))

    async def native(*args, **kwargs):
        native_calls.append(args)
        raise AssertionError("Website model picker must not wait for OpenCode startup")

    app.state.runtime.workspace.request = native
    with TestClient(app) as client:
        prefix = "/__recorder/api/models"
        start = client.post(prefix + "/sona/login/start").json()["url"]
        state = parse_qs(urlsplit(start).fragment.partition("?")[2])["state"][0]
        assert client.post(prefix + "/sona/callback", data={"state": state, "token": "test-token"}).status_code == 200
        before = client.get(prefix + "/source").json()
        assert [m["id"] for m in before["providers"][0]["models"]] == ["first"]
        generation = 1
        result = client.post(prefix + "/sona/refresh")
        assert result.status_code == 200
        assert [m["id"] for m in result.json()["providers"][0]["models"]] == ["first", "new-subscription"]
        assert result.json()["updated_at"] >= before["updated_at"]
        assert len(platform_calls) == 6
        assert len({request.url.params["_sona_refresh"] for request in platform_calls}) == 6
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        picker = client.get(f"/__recorder/api/workspace/projects/{project_id}/models")
        assert picker.status_code == 200
        assert "new-subscription" in picker.json()["providers"][0]["models"]
        assert not native_calls
        from sona_code.admin.models import compile_providers
        providers = compile_providers(app.state.runtime.provider_config())
        assert any("new-subscription" in provider["models"] for provider in providers.values())
        assert "test-token" not in picker.text
        assert "test-scene-key" not in result.text


def saved_catalog(token="saved-token", name="cached"):
    return SiteSession(token, {"id": 7}, [UpstreamConfig(
        name="sona-prod-12", base_url="https://sona.paasoa.cmbchina.cn/api/v1",
        api_key="scene-secret", source="sona", models=[UpstreamModelConfig(id=name)],
    )], time.time())


def test_credentials_and_catalog_survive_restart_and_logout(tmp_path):
    path = str(tmp_path / "config.json")
    site = default_config().sona_site
    manager = SonaSiteManager(path)
    manager.set_session("prod", site.prod_url, saved_catalog())
    restored = SonaSiteManager(path)
    assert restored.session(site).token == "saved-token"
    assert restored.session(site).providers[0].api_key == "scene-secret"
    assert restored.session(site).providers[0].models[0].context_length == 262144
    assert restored.session(site).providers[0].models[0].output_length == 32768
    file = tmp_path / "config.json.sona-sessions"
    if os.name == "nt":
        assert file.read_bytes().startswith(b"DPAPI\n")
        assert b"saved-token" not in file.read_bytes()
        assert b"scene-secret" not in file.read_bytes()
    else:
        assert file.stat().st_mode & 0o777 == 0o600
    restored.clear(site)
    assert SonaSiteManager(path).session(site) is None
    file.write_bytes(b"DPAPI\nbroken-file")
    assert SonaSiteManager(path).session(site) is None


@pytest.mark.parametrize("failure", [None, 401, 502])
def test_startup_refreshes_once_without_blocking_or_losing_offline_catalog(tmp_path, failure):
    cfg = default_config()
    cfg.recording.dir = str(tmp_path / "records")
    path = str(tmp_path / "config.json")
    SonaSiteManager(path).set_session("prod", cfg.sona_site.prod_url, saved_catalog())
    app = create_app(cfg, path)
    calls = []
    released = False

    async def catalog(origin, token, environment):
        import asyncio
        calls.append((origin, token, environment))
        while not released:
            await asyncio.sleep(0.005)
        if failure:
            raise SonaSiteError("expired" if failure == 401 else "offline", failure)
        return saved_catalog(name="fresh")

    app.state.runtime.sona_site.load_catalog = catalog
    with TestClient(app) as client:
        prefix = "/__recorder/api/models"
        source = client.get(prefix + "/source").json()
        assert source["connected"] and source["startup_refreshing"]
        assert source["providers"][0]["models"][0]["id"] == "cached"
        released = True
        deadline = time.monotonic() + 3
        while source["startup_refreshing"] and time.monotonic() < deadline:
            source = client.get(prefix + "/source").json()
        assert not source["startup_refreshing"]
        assert len(calls) == 1 and calls[0][1] == "saved-token"
        assert source["connected"] is (failure != 401)
        restored = SonaSiteManager(path).session(cfg.sona_site)
        if failure == 401:
            assert restored is None
            assert "url" in client.post(prefix + "/sona/login/start").json()
        else:
            assert restored.token == "saved-token"
            assert restored.providers[0].models[0].id == ("fresh" if failure is None else "cached")
            if failure is None:
                assert client.post(prefix + "/sona/login/start").json() == {"connected": True}
                assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["INF0002", "INF1001", "INF1002"])
async def test_expired_platform_business_code_requests_login(monkeypatch, code):
    real_client = httpx.AsyncClient
    monkeypatch.setattr("sona_code.sona_site.httpx.AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"returnCode": code})), **kwargs))
    with pytest.raises(SonaSiteError) as error:
        await SonaSiteManager().load_catalog("https://sona.example.test", "expired", "prod")
    assert error.value.status == 401


def test_sona_login_and_models_are_available_without_a_project(tmp_path):
    cfg = default_config()
    cfg.recording.dir = str(tmp_path / "records")
    app = create_app(cfg, str(tmp_path / "config.json"))

    async def catalog(*args):
        return saved_catalog()

    async def no_native(*args, **kwargs):
        raise AssertionError("Website login must not need a project server")

    app.state.runtime.sona_site.load_catalog = catalog
    app.state.runtime.workspace.request = no_native
    with TestClient(app) as client:
        prefix = "/__recorder/api"
        assert client.get(prefix + "/workspace/projects").json()["items"] == []
        assert client.get(prefix + "/workspace/models").json()["sona_connected"] is False
        login = client.post(prefix + "/models/sona/login/start").json()
        state = login["state"]
        assert state == parse_qs(urlsplit(login["url"]).fragment.partition("?")[2])["state"][0]
        status = prefix + "/models/sona/login/" + state
        assert client.get(status).json()["status"] == "pending"
        assert client.post(prefix + "/models/sona/callback", data={"state": state, "token": "test"}).status_code == 200
        assert client.get(status).json()["status"] == "completed"
        response = client.get(prefix + "/workspace/models")
        assert response.json()["sona_connected"] is True
        assert list(response.json()["providers"][0]["models"]) == ["cached"]
        assert "scene-secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["cancel", "restart", "replay"])
async def test_inflight_login_cannot_complete_after_cancel_or_retry(tmp_path, action):
    cfg = default_config()
    cfg.recording.dir = str(tmp_path / "records")
    app = create_app(cfg, str(tmp_path / "config.json"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def catalog(*args):
        entered.set()
        await release.wait()
        return saved_catalog()

    app.state.runtime.sona_site.load_catalog = catalog
    prefix = "/__recorder/api/models/sona"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        state = (await client.post(prefix + "/login/start")).json()["state"]
        payload = {"state": state, "token": "test"}
        callback = asyncio.create_task(client.post(prefix + "/callback", data=payload))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert (await client.get(prefix + "/login/" + state)).json()["status"] == "processing"
            if action == "cancel":
                assert (await client.delete(prefix + "/login/" + state)).status_code == 200
            elif action == "restart":
                assert (await client.post(prefix + "/login/start")).json()["state"] != state
            else:
                assert (await client.post(prefix + "/callback", data=payload)).status_code == 400
                assert (await client.get(prefix + "/login/" + state)).json()["status"] == "processing"
            release.set()
            result = await asyncio.wait_for(callback, 2)
            assert result.status_code == (200 if action == "replay" else 400)
            assert (app.state.runtime.sona_site.session(cfg.sona_site) is not None) is (action == "replay")
        finally:
            release.set()
            if not callback.done():
                callback.cancel()
            await app.state.runtime.aclose()


def test_login_callback_failure_is_reported_to_app_and_can_be_retried(tmp_path):
    app = create_app(default_config(), str(tmp_path / "config.json"))

    async def unavailable(*args):
        raise SonaSiteError("无权访问该空间", 502)

    app.state.runtime.sona_site.load_catalog = unavailable
    with TestClient(app) as client:
        prefix = "/__recorder/api/models/sona"
        state = client.post(prefix + "/login/start").json()["state"]
        assert client.post(prefix + "/callback", data={"state": state, "token": "test"}).status_code == 400
        assert client.get(prefix + "/login/" + state).json() == {"status": "error", "detail": "无权访问该空间"}
        retry = client.post(prefix + "/login/start").json()["state"]
        assert retry != state
        assert client.get(prefix + "/login/" + retry).json()["status"] == "pending"
