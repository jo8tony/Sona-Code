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
            {"name": ".hidden", "path": ".hidden", "directory": True},
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


def test_file_index_includes_hidden_generated_and_git_ignored_files(tmp_path):
    app = create_app(AppConfig(default_upstream="main", upstreams=[
        UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")
    ]), config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    deep = project / "src" / "deep" / "views"
    deep.mkdir(parents=True)
    (deep / "页面.html").write_text("<h1>Hi</h1>", encoding="utf-8")
    (project / "AGENTS.md").write_text("test", encoding="utf-8")
    (project / ".env").write_text("EXAMPLE=value", encoding="utf-8")
    (project / ".gitignore").write_text("ignored/\n*.log\n", encoding="utf-8")
    (project / "debug.log").write_text("test", encoding="utf-8")
    expected = ["AGENTS.md", ".env", ".gitignore", "debug.log", "src/deep/views/页面.html"]
    for name in (".secret", "node_modules", "build", "dist", "target", "__pycache__", "ignored"):
        (project / name).mkdir()
        (project / name / "included.txt").write_text("included")
        expected.append(f"{name}/included.txt")
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
        assert response.json() == {"items": sorted(expected), "truncated": False}
        assert client.get("/__recorder/api/workspace/projects/unknown/file-index").status_code == 404
        tree = client.get(f"/__recorder/api/workspace/projects/{project_id}/tree").json()
        names = {item["name"] for item in tree["items"]}
        assert {".secret", ".env", ".gitignore", "node_modules", "build", "dist",
                "target", "__pycache__", "ignored", "debug.log"} <= names


def test_large_project_tree_and_index_are_not_truncated(tmp_path):
    app = create_app(AppConfig(default_upstream="main", upstreams=[
        UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")
    ]), config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    project.mkdir()
    expected = [f"file-{index:05d}.txt" for index in range(20001)]
    for name in expected:
        (project / name).touch()
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        prefix = f"/__recorder/api/workspace/projects/{project_id}"
        tree = client.get(f"{prefix}/tree").json()
        assert tree["truncated"] is False
        assert [item["path"] for item in tree["items"]] == expected
        assert client.get(f"{prefix}/file-index").json() == {"items": expected, "truncated": False}


def test_tree_shows_links_without_allowing_access_outside_project(tmp_path):
    import pytest

    app = create_app(AppConfig(default_upstream="main", upstreams=[
        UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")
    ]), config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "inside.txt").write_text("inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.txt").write_text("outside")
    try:
        (project / "external").symlink_to(outside, target_is_directory=True)
        (project / "alias").symlink_to(project / "src", target_is_directory=True)
        (project / "src" / "cycle").symlink_to(project, target_is_directory=True)
        (project / "broken").symlink_to(project / "missing.txt")
        (project / "file-link.txt").symlink_to(project / "src" / "inside.txt")
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or elevated privileges")
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    with TestClient(app) as client:
        project_id = client.get("/__recorder/api/workspace/projects").json()["items"][0]["id"]
        prefix = f"/__recorder/api/workspace/projects/{project_id}"
        tree = client.get(f"{prefix}/tree").json()
        assert {item["name"] for item in tree["items"]} == {
            "alias", "src", "external", "broken", "file-link.txt"}
        assert client.get(f"{prefix}/tree", params={"path": "alias"}).json()["items"] == [
            {"name": "cycle", "path": "alias/cycle", "directory": True},
            {"name": "inside.txt", "path": "alias/inside.txt", "directory": False},
        ]
        assert client.get(f"{prefix}/tree", params={"path": "external"}).status_code == 404
        assert client.post(f"{prefix}/entries/open", params={
            "path": "external/outside.txt", "mode": "browser"}).status_code == 404
        assert client.get(f"{prefix}/file-index").json() == {
            "items": ["file-link.txt", "src/inside.txt"], "truncated": False}


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
