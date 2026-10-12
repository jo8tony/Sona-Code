"""File previews are bounded, passive, and independent of native project servers."""

import os

import pytest
from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.config import AppConfig, UpstreamConfig
from sona_code.workspace import files
from sona_code.workspace.routes import _project_id


@pytest.fixture
def project_preview(tmp_path):
    app = create_app(AppConfig(default_upstream="main", upstreams=[
        UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")
    ]), config_path=str(tmp_path / "config.json"))
    project = tmp_path / "project"
    project.mkdir()
    app.state.runtime.terminal_projects.add(str(project), "opencode")

    async def no_native_request(*args, **kwargs):
        raise AssertionError("File previews must not start or query OpenCode")

    app.state.runtime.workspace.request = no_native_request
    with TestClient(app) as client:
        yield client, f"/__recorder/api/workspace/projects/{_project_id(str(project))}", project


@pytest.mark.parametrize("codec,label", [
    ("utf-8", "UTF-8"), ("utf-8-sig", "UTF-8 BOM"),
    ("utf-16", "UTF-16"), ("gb18030", "GB18030"),
])
def test_text_preview_handles_windows_encodings_without_writing(project_preview, codec, label):
    client, prefix, project = project_preview
    path = project / "中文代码.py"
    text = '# 示例\r\nprint("你好")\r\n'
    data = text.encode(codec)
    path.write_bytes(data)
    response = client.get(f"{prefix}/entries/preview", params={"path": path.name})
    assert response.status_code == 200
    preview = response.json()
    assert preview["kind"] == "text"
    assert preview["language"] == "python"
    assert preview["encoding"] == label
    assert preview["content"] == text.replace("\r\n", "\n")
    assert preview["line_count"] == 3
    assert preview["size"] == len(data)
    assert preview["truncated"] is False
    assert response.headers["cache-control"] == "no-store"
    assert path.read_bytes() == data


@pytest.mark.parametrize("name,kind,language", [
    ("README.md", "markdown", "markdown"), ("settings.json", "text", "json"),
    (".env", "text", "plain"), ("Dockerfile", "text", "docker"),
    ("empty.txt", "text", "plain"), ("unknown.custom", "text", "plain"),
])
def test_preview_detects_documents_configs_and_extensionless_text(project_preview, name, kind, language):
    client, prefix, project = project_preview
    (project / name).write_text("" if name == "empty.txt" else "# Content\n<script>alert(1)</script>")
    preview = client.get(f"{prefix}/entries/preview", params={"path": name}).json()
    assert (preview["kind"], preview["language"]) == (kind, language)
    assert "content" in preview
    # HTML and script-like text are never served as active project documents.
    assert client.get(f"{prefix}/entries/content", params={"path": name}).status_code == 400


@pytest.mark.parametrize("name,payload,mime,kind", [
    ("image.png", b"\x89PNG\r\n\x1a\n", "image/png", "image"),
    ("diagram.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
     "image/svg+xml", "image"),
    ("manual.pdf", b"%PDF-1.7\n%%EOF", "application/pdf", "pdf"),
])
def test_media_content_is_uncached_passive_and_version_checked(project_preview, name, payload, mime, kind):
    client, prefix, project = project_preview
    path = project / name
    path.write_bytes(payload)
    metadata = client.get(f"{prefix}/entries/preview", params={"path": name}).json()
    assert metadata["kind"] == kind
    assert "content" not in metadata
    response = client.get(f"{prefix}/entries/content", params={"path": name, "version": metadata["version"]})
    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"].split(";")[0] == mime
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert "default-src 'none'" in response.headers["content-security-policy"]
    path.write_bytes(payload + b"changed")
    assert client.get(f"{prefix}/entries/content", params={"path": name, "version": metadata["version"]}).status_code == 409


def test_preview_bounds_large_text_media_and_line_count(project_preview, monkeypatch):
    client, prefix, project = project_preview
    monkeypatch.setattr(files, "MAX_TEXT_BYTES", 64)
    monkeypatch.setattr(files, "MAX_IMAGE_BYTES", 64)
    monkeypatch.setattr(files, "MAX_PDF_BYTES", 64)
    monkeypatch.setattr(files, "MAX_TEXT_LINES", 3)
    for name in ("big.txt", "big.png", "big.pdf"):
        (project / name).write_bytes(b"x" * 65)
        preview = client.get(f"{prefix}/entries/preview", params={"path": name}).json()
        assert preview["kind"] == "unsupported"
        assert preview["reason"] == "too_large"
        assert "content" not in preview
    assert client.get(f"{prefix}/entries/content", params={"path": "big.png"}).status_code == 413
    assert client.get(f"{prefix}/entries/content", params={"path": "big.pdf"}).status_code == 413
    (project / "many.txt").write_text("a\nb\nc\nd\ne")
    preview = client.get(f"{prefix}/entries/preview", params={"path": "many.txt"}).json()
    assert preview["truncated"] is True
    assert preview["line_count"] == 5
    assert preview["content"] == "a\nb\nc"


def test_binary_and_office_files_have_system_open_fallback(project_preview):
    client, prefix, project = project_preview
    for name, payload, reason in (
        ("data.bin", b"\x00\x01\xff", "format"),
        ("report.docx", b"PK\x03\x04", "format"),
        ("hidden.custom", b"abc\x00\x01", "binary"),
        ("invalid.custom", b"\xff\x00abc", "binary"),
    ):
        (project / name).write_bytes(payload)
        preview = client.get(f"{prefix}/entries/preview", params={"path": name}).json()
        assert preview["kind"] == "unsupported"
        assert preview["reason"] == reason


@pytest.mark.parametrize("relative", ["../outside", "/etc/passwd", "C:/outside", "src\\file.py", "a/../b", ""])
def test_preview_rejects_invalid_paths_and_directories(project_preview, relative):
    client, prefix, _ = project_preview
    for endpoint in ("preview", "content"):
        assert client.get(f"{prefix}/entries/{endpoint}", params={"path": relative}).status_code in {400, 404}
    assert client.get(f"{prefix}/entries/preview", params={"path": "missing.py"}).status_code == 404


def test_preview_symlinks_stay_inside_project(project_preview, tmp_path):
    client, prefix, project = project_preview
    (project / "inside.txt").write_text("inside")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")
    try:
        (project / "alias.txt").symlink_to(project / "inside.txt")
        (project / "external.png").symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks require Windows developer mode or elevated privileges")
    assert client.get(f"{prefix}/entries/preview", params={"path": "alias.txt"}).json()["content"] == "inside"
    for endpoint in ("preview", "content"):
        assert client.get(f"{prefix}/entries/{endpoint}", params={"path": "external.png"}).status_code == 404


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX named pipes")
def test_preview_never_reads_special_files(project_preview):
    client, prefix, project = project_preview
    os.mkfifo(project / "pipe.txt")
    assert client.get(f"{prefix}/entries/preview", params={"path": "pipe.txt"}).status_code == 400
