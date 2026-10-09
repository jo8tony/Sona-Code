"""Opt-in discovery contract for the pinned V1 binary, with isolated storage."""

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from sona_code.admin.skills import SkillStore

BINARIES = [path for path in os.environ.get("OPENCODE_TEST_BINARIES", "").split(os.pathsep) if path]


@pytest.mark.parametrize("binary", BINARIES or [None])
def test_native_other_agent_skills_are_discovered_without_copying(binary, tmp_path, monkeypatch):
    if binary is None:
        pytest.skip("set OPENCODE_TEST_BINARIES for native V1 skill discovery checks")
    home, project = tmp_path / "home", tmp_path / "project"
    locations = {}
    for root, name in [(home / ".claude/skills", "global-claude"),
                       (home / ".agents/skills/team", "global-agents"),
                       (project / ".claude/skills", "project-claude"),
                       (project / ".agents/skills", "project-agents")]:
        skill = root / name
        skill.mkdir(parents=True)
        locations[name] = skill / "SKILL.md"
        locations[name].write_text(f"---\nname: {name}\ndescription: Local contract fixture.\n---\nCheck files.")
    env = {**os.environ, "OPENCODE_TEST_HOME": str(home), "OPENCODE_DISABLE_AUTOUPDATE": "1",
           "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
           "OPENCODE_CONFIG_CONTENT": json.dumps({"permission": {"skill": {"global-claude": "deny"}}}),
           "OPENCODE_SERVER_PASSWORD": "local-test-password"}
    for name in ("CONFIG", "CACHE", "DATA", "STATE"):
        env[f"XDG_{name}_HOME"] = str(tmp_path / name.lower())
    for key in ("OPENCODE_CONFIG", "OPENCODE_DB", "OPENCODE_SERVER_USERNAME",
                "OPENCODE_DISABLE_EXTERNAL_SKILLS", "OPENCODE_DISABLE_CLAUDE_CODE", "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"):
        env.pop(key, None)
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENCODE_TEST_HOME", str(home))
    store = SkillStore(tmp_path / "app", external_root=tmp_path / "original")
    assert {item["name"] for item in store.list()["items"]} == {"global-claude", "global-agents"}
    assert store.external_paths() == []
    assert list(store.enabled_dir.iterdir()) == []
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (tmp_path / "native.log").open("w") as log:
        process = subprocess.Popen([binary, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
            cwd=project, env=env, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", auth=("opencode", "local-test-password"),
                              trust_env=False, timeout=20) as client:
                for _ in range(100):
                    assert process.poll() is None, (tmp_path / "native.log").read_text()[-1000:]
                    try:
                        if client.get("/global/health").status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(.1)
                response = client.get("/skill")
                response.raise_for_status()
                skills = {item["name"]: item for item in response.json()}
                for name, location in locations.items():
                    assert Path(skills[name]["location"]).resolve() == location.resolve()
                commands = client.get("/command").json()
                assert set(locations) <= {item["name"] for item in commands if item.get("source") == "skill"}
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
