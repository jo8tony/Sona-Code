"""Windows subprocess cleanup must remain bounded even when taskkill fails."""

import subprocess
from types import SimpleNamespace

import pytest

from llm_api_proxy_recorder.workspace import manager


@pytest.mark.parametrize("failure", [None, OSError("blocked"), subprocess.TimeoutExpired("taskkill", 5)])
async def test_windows_cleanup_reaps_server_when_tree_cleanup_fails(monkeypatch, failure):
    calls = []
    state = {"alive": True}

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if failure:
            raise failure
        return subprocess.CompletedProcess(argv, 1)

    def kill():
        state["alive"] = False

    def wait(timeout):
        assert timeout == 3
        assert not state["alive"]
        calls.append("wait")

    monkeypatch.setattr(manager, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(manager.subprocess, "run", run)
    process = SimpleNamespace(pid=123, poll=lambda: None if state["alive"] else 0,
                              kill=kill, wait=wait)
    await manager.WorkspaceManager._stop_process(process)
    assert calls[0][0] == ["taskkill", "/F", "/T", "/PID", "123"]
    assert calls[0][1]["timeout"] == 5
    assert calls[-1] == "wait"


async def test_windows_cleanup_does_not_kill_already_reaped_process(monkeypatch):
    monkeypatch.setattr(manager, "os", SimpleNamespace(name="nt"))
    process = SimpleNamespace(poll=lambda: 0)
    await manager.WorkspaceManager._stop_process(process)
