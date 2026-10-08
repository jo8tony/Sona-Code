"""Project tree and directory references stay inside the registered workspace."""

from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.config import AppConfig, UpstreamConfig


def test_tree_lists_project_entries_and_rejects_traversal(tmp_path, monkeypatch):
    config = AppConfig(
        upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
        default_upstream="main",
        model_settings={"source": "native", "show_native_models": True},
    )
    app = create_app(config, config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "page.html").write_text("<h1>Hi</h1>", encoding="utf-8")
    (project / ".hidden").mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    from sona_code.workspace import routes
    opened = []
    monkeypatch.setattr(routes.webbrowser, "open", lambda url: opened.append(url) or True)

    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        prefix = f"/__recorder/api/workspace/projects/{project_id}"
        assert client.get(f"{prefix}/tree").json()["items"] == [
            {"name": "src", "path": "src", "directory": True}
        ]
        assert client.get(f"{prefix}/tree", params={"path": "src"}).json()["items"] == [
            {"name": "page.html", "path": "src/page.html", "directory": False}
        ]
        assert client.get(f"{prefix}/tree", params={"path": "../"}).status_code == 400
        assert client.get(f"{prefix}/tree", params={"path": "src/page.html"}).status_code == 400
        assert client.post(f"{prefix}/entries/open", params={"path": "../outside", "mode": "browser"}).status_code in {400, 404}
        assert client.post(f"{prefix}/entries/open", params={"path": "src/page.html", "mode": "browser"}).json() == {"ok": True}
        assert opened == [(project / "src" / "page.html").as_uri()]


def test_file_index_is_local_and_excludes_hidden_generated_and_linked_entries(tmp_path):
    app = create_app(AppConfig(default_upstream="main", upstreams=[
        UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")
    ]), config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    deep = project / "src" / "deep" / "views"
    deep.mkdir(parents=True)
    (deep / "页面.html").write_text("<h1>Hi</h1>", encoding="utf-8")
    (project / "AGENTS.md").write_text("test", encoding="utf-8")
    for name in (".secret", "node_modules", "build", "dist", "target", "__pycache__"):
        (project / name).mkdir()
        (project / name / "excluded.txt").write_text("excluded")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    try:
        (project / "linked.txt").symlink_to(outside)
    except OSError:
        pass  # Windows ordinary users may not have symlink privileges.
    app.state.runtime.terminal_projects.add(str(project), "opencode")

    async def no_native_request(*args, **kwargs):
        raise AssertionError("Indexing project files must not start OpenCode")

    app.state.runtime.workspace.request = no_native_request
    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        response = client.get(f"/__recorder/api/workspace/projects/{project_id}/file-index")
        assert response.status_code == 200
        assert response.json() == {"items": ["AGENTS.md", "src/deep/views/页面.html"], "truncated": False}
        assert client.get("/__recorder/api/workspace/projects/unknown/file-index").status_code == 404


def test_directory_reference_is_sent_as_context_text(tmp_path):
    config = AppConfig(
        upstreams=[UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")],
        default_upstream="main",
        model_settings={"source": "native", "show_native_models": True},
    )
    app = create_app(config, config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    calls = []

    async def fake_request(_project, _config, method, endpoint, *, body=None, params=None):
        calls.append((method, endpoint, body))
        if endpoint == "/config/providers":
            return {"providers": [{"id": "main", "models": {"model-x": {}}}], "default": {}}
        if endpoint == "/provider":
            return {"connected": ["main"]}
        return {"ok": True}

    app.state.runtime.workspace.request = fake_request
    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        response = client.post(
            f"/__recorder/api/workspace/projects/{project_id}/sessions/ses_1/prompt",
            json={"text": "查看 @src", "references": [{"path": "src"}],
                  "provider_id": "main", "model_id": "model-x"},
        )
        assert response.status_code == 200
        assert calls[-1][2]["parts"] == [
            {"type": "text", "text": "查看 @src"},
            {"type": "text", "text": "\n引用项目目录 @src。请按需查看此目录下的文件。"},
        ]
