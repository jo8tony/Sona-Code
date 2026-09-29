"""OpenCode 全局 JSONC 编辑接口：语法、冲突保护及原文保留。"""

import os
import stat

from fastapi.testclient import TestClient

from sona_code.app import create_app
from sona_code.config import default_config


def test_opencode_jsonc_editor_preserves_comments_and_checks_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    with TestClient(create_app(default_config(), config_path=str(tmp_path / "proxy.json"))) as client:
        url = "/__recorder/api/settings/opencode-config"
        original = client.get(url).json()
        assert original["path"] == str(tmp_path / "xdg/opencode/opencode.jsonc")

        content = '{\n  // 保留说明\n  "$schema": "https://opencode.ai/config.json",\n  "model": "deepseek/test",\n}\n'
        saved = client.put(url, json={"content": content, "revision": original["revision"]})
        assert saved.status_code == 200, saved.text
        assert (tmp_path / "xdg/opencode/opencode.jsonc").read_text(encoding="utf-8") == content
        assert client.get(url).json()["content"] == content

        outdated = client.put(url, json={"content": "{}", "revision": original["revision"]})
        assert outdated.status_code == 409
        invalid = client.put(url, json={"content": '{"x":,}', "revision": saved.json()["revision"]})
        assert invalid.status_code == 422
        assert (tmp_path / "xdg/opencode/opencode.jsonc").read_text(encoding="utf-8") == content


def test_opencode_import_is_allowlisted_idempotent_and_never_overwrites(tmp_path, monkeypatch):
    source_config = tmp_path / "user-config"
    source_data = tmp_path / "user-data"
    target_config = tmp_path / "app-config"
    target_data = tmp_path / "app-data"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(target_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(target_data))
    monkeypatch.setenv("SONACODE_ORIGINAL_XDG_CONFIG_HOME", str(source_config))
    monkeypatch.setenv("SONACODE_ORIGINAL_XDG_DATA_HOME", str(source_data))

    source_root = source_config / "opencode"
    (source_root / "agents").mkdir(parents=True)
    (source_root / "opencode.jsonc").write_text('{"model":"old"}', encoding="utf-8")
    (source_root / "agents" / "review.md").write_text("review", encoding="utf-8")
    (source_root / "node_modules").mkdir()
    (source_root / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("do not import", encoding="utf-8")
    symlink_created = False
    try:
        os.symlink(outside, source_root / "agents" / "outside.md")
        symlink_created = True
    except OSError:
        pass  # Windows CI 可能未授予创建符号链接的权限。
    (source_data / "opencode").mkdir(parents=True)
    (source_data / "opencode" / "auth.json").write_text('{"secret":"value"}', encoding="utf-8")
    (source_data / "opencode" / "opencode.db").write_text("session", encoding="utf-8")

    destination_root = target_config / "opencode"
    destination_root.mkdir(parents=True)
    (destination_root / "opencode.jsonc").write_text('{"model":"keep"}', encoding="utf-8")

    with TestClient(create_app(default_config(), config_path=str(tmp_path / "proxy.json"))) as client:
        preview = client.get("/__recorder/api/settings/opencode-import").json()
        assert preview["candidate_count"] == 3
        assert preview["copy_count"] == 2
        assert preview["conflicts"] == ["config/opencode.jsonc"]
        assert "secret" not in str(preview)
        if symlink_created:
            assert preview["ignored_symlinks"] == 1

        imported = client.post("/__recorder/api/settings/opencode-import", json={})
        assert imported.status_code == 200, imported.text
        assert imported.json()["copied"] == [
            "config/agents/review.md",
            "credentials/auth.json",
        ]
        assert imported.json()["skipped"] == ["config/opencode.jsonc"]
        assert (destination_root / "opencode.jsonc").read_text(encoding="utf-8") == '{"model":"keep"}'
        assert (destination_root / "agents" / "review.md").read_text(encoding="utf-8") == "review"
        assert (target_data / "opencode" / "auth.json").exists()
        if os.name != "nt":
            mode = (target_data / "opencode" / "auth.json").stat().st_mode
            assert stat.S_IMODE(mode) == 0o600
        assert not (target_data / "opencode" / "opencode.db").exists()
        assert not (destination_root / "node_modules").exists()
        assert not (destination_root / "agents" / "outside.md").exists()

        repeated = client.post("/__recorder/api/settings/opencode-import", json={}).json()
        assert repeated["copied"] == []
        assert len(repeated["skipped"]) == 3
