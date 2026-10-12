"""Real project files: lossless writes, optimistic concurrency and boundaries."""
import codecs
import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.config import AppConfig, UpstreamConfig
from sona_code.workspace.routes import _project_id


@pytest.fixture
def files_client(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    app = create_app(AppConfig(default_upstream="main", upstreams=[
        UpstreamConfig(name="main", base_url="http://127.0.0.1:9001")
    ], recording={"dir": str(tmp_path / "records")}), config_path=str(tmp_path / "config.json"))
    app.state.runtime.terminal_projects.add(str(project), "opencode")
    async def no_native_request(*args, **kwargs):
        raise AssertionError("Editing must not start OpenCode")
    app.state.runtime.workspace.request = no_native_request
    with TestClient(app) as client:
        yield client, project, f"/__recorder/api/workspace/projects/{_project_id(str(project))}/file"


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("bom", [b"", codecs.BOM_UTF8])
def test_lossless_save(files_client, eol, bom):
    client, project, url = files_client
    path = project / "代码.py"
    path.write_bytes(bom + f"# 中文{eol}print(1){eol}".encode())
    response = client.get(url, params={"path": path.name})
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "# 中文\nprint(1)\n"
    assert data["encoding"] == ("utf-8-bom" if bom else "utf-8")
    assert not data["read_only"]
    saved = client.put(url, params={"path": path.name}, json={
        "content": "# 中文\nprint(2)\n", "revision": data["revision"]})
    assert saved.status_code == 200
    assert saved.json()["revision"] != data["revision"]
    assert path.read_bytes() == bom + f"# 中文{eol}print(2){eol}".encode()
    assert not list(project.glob(".sona-edit-*"))


def test_conflict_preserves_external_file(files_client):
    client, project, url = files_client
    path = project / "a.txt"
    path.write_text("original", encoding="utf-8")
    data = client.get(url, params={"path": "a.txt"}).json()
    path.write_text("external", encoding="utf-8")
    response = client.put(url, params={"path": "a.txt"}, json={
        "content": "editor", "revision": data["revision"]})
    assert response.status_code == 409
    assert path.read_text() == "external"


@pytest.mark.parametrize("relative", ["../outside", "/outside", "a/../x", "a\\x", "C:/x", "a.txt:stream", ".git/config"])
def test_reject_unsafe_paths(files_client, relative):
    client, _, url = files_client
    assert client.get(url, params={"path": relative}).status_code in {400, 404}
    assert client.put(url, params={"path": relative}, json={
        "content": "bad", "revision": "0" * 64}).status_code in {400, 404}


@pytest.mark.parametrize("raw,status", [(b"\x00binary", 415), (b"\xff", 415), (b"x" * (2 * 1024 * 1024 + 1), 413)], ids=["binary", "encoding", "oversize"])
def test_reject_uneditable_files(files_client, raw, status):
    client, project, url = files_client
    (project / "data").write_bytes(raw)
    assert client.get(url, params={"path": "data"}).status_code == status


@pytest.mark.parametrize("content", ["a\r\nb\nc", "x" * 20001], ids=["mixed-eol", "long-line"])
def test_lossy_or_long_line_is_read_only(files_client, content):
    client, project, url = files_client
    (project / "a").write_bytes(content.encode())
    data = client.get(url, params={"path": "a"}).json()
    assert data["read_only"] and data["reason"]
    assert client.put(url, params={"path": "a"}, json={
        "content": "replacement", "revision": data["revision"]}).status_code == 422
    assert (project / "a").read_bytes() == content.encode()


def test_cross_origin_write_rejected(files_client):
    client, project, url = files_client
    (project / "a").write_text("before")
    data = client.get(url, params={"path": "a"}).json()
    body = {"content": "after", "revision": data["revision"]}
    assert client.put(url, params={"path": "a"}, json=body,
                      headers={"Origin": "https://other.example"}).status_code == 403
    assert client.put(url, params={"path": "a"}, json=body,
                      headers={"Origin": "http://testserver"}).status_code == 200


def test_directory_missing_link_and_oversize_save(files_client, tmp_path):
    client, project, url = files_client
    (project / "folder").mkdir()
    assert client.get(url, params={"path": "folder"}).status_code == 400
    assert client.get(url, params={"path": "missing"}).status_code == 404
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    try:
        (project / "link").symlink_to(outside)
    except OSError:
        pass
    else:
        assert client.get(url, params={"path": "link"}).status_code == 400
    (project / "a").write_text("small")
    data = client.get(url, params={"path": "a"}).json()
    assert client.put(url, params={"path": "a"}, json={
        "content": "中" * 800000, "revision": data["revision"]}).status_code == 413
    if os.name != "nt":
        os.chmod(project / "a", 0o755)
        assert client.put(url, params={"path": "a"}, json={
            "content": "updated", "revision": data["revision"]}).status_code == 200
        assert (project / "a").stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name != "nt", reason="NTFS DACL preservation")
def test_windows_save_preserves_explicit_file_dacl(files_client):
    client, project, url = files_client
    path = project / "acl.txt"
    path.write_text("before", encoding="utf-8")
    env = dict(os.environ, SONA_TEST_ACL_PATH=str(path))

    def powershell(command):
        return subprocess.check_output(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                        "$ErrorActionPreference='Stop'; "
                                        "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'); "
                                        + command], env=env, text=True, timeout=20).strip()

    before = powershell("$acl=Get-Acl -LiteralPath $env:SONA_TEST_ACL_PATH; "
                        "$acl.SetAccessRuleProtection($true,$true); "
                        "Set-Acl -LiteralPath $env:SONA_TEST_ACL_PATH -AclObject $acl; "
                        "(Get-Acl -LiteralPath $env:SONA_TEST_ACL_PATH).Sddl")
    data = client.get(url, params={"path": "acl.txt"}).json()
    response = client.put(url, params={"path": "acl.txt"}, json={"content": "after", "revision": data["revision"]})
    assert response.status_code == 200, response.text
    assert path.read_text(encoding="utf-8") == "after"
    assert powershell("(Get-Acl -LiteralPath $env:SONA_TEST_ACL_PATH).Sddl") == before
    assert not list(project.glob(".sona-*-*"))
