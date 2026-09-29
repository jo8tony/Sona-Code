"""Verify installed Windows sidecars and desktop close/restore behavior."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets


MARKER = "sonacode_packaged_pty_input_ok"


def _desktop_subsystem(path: Path) -> int:
    """Return the PE optional-header Subsystem value (2 means Windows GUI)."""
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError(f"not a PE executable: {path}")
        stream.seek(0x3C)
        pe_offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(pe_offset)
        if stream.read(4) != b"PE\0\0":
            raise RuntimeError(f"invalid PE signature: {path}")
        stream.seek(20, os.SEEK_CUR)  # COFF header
        optional_header = stream.read(70)
    if len(optional_header) < 70:
        raise RuntimeError(f"truncated PE optional header: {path}")
    return struct.unpack_from("<H", optional_header, 68)[0]


def _request_json(
    base_url: str, method: str, path: str, body: dict | None = None, *, timeout: float = 5
) -> dict | list:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _wait_until_ready(base_url: str, process: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"recorder sidecar exited early with code {process.returncode}")
        try:
            if _request_json(base_url, "GET", "/__recorder/api/ping").get("ok") is True:
                return
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError("recorder sidecar did not become ready")


async def _verify_terminal_input(base_url: str, session_id: str) -> None:
    ws_url = base_url.replace("http://", "ws://", 1)
    ws_url += f"/__recorder/api/terminal/ws/{session_id}"
    received = bytearray()
    try:
        async with websockets.connect(ws_url, open_timeout=10) as websocket:
            deadline = asyncio.get_running_loop().time() + 20
            attached = False
            while asyncio.get_running_loop().time() < deadline and not attached:
                message = await asyncio.wait_for(websocket.recv(), timeout=5)
                if isinstance(message, str):
                    payload = json.loads(message)
                    attached = payload.get("type") == "attached" and payload.get("alive") is True
                else:
                    received.extend(message)
            if not attached:
                raise RuntimeError("terminal websocket never reported attached")

            # The expected marker must not appear in echoed input: require actual execution.
            await websocket.send(json.dumps({
                "type": "input", "data": "Write-Output ('sonacode_' + 'packaged_pty_input_ok')\r",
            }))
            while asyncio.get_running_loop().time() < deadline:
                message = await asyncio.wait_for(websocket.recv(), timeout=5)
                if isinstance(message, bytes):
                    received.extend(message)
                    if MARKER.encode() in received:
                        return
                else:
                    payload = json.loads(message)
                    if payload.get("type") == "input_error":
                        raise RuntimeError(payload.get("detail") or "terminal input failed")
                    if payload.get("type") == "exit":
                        raise RuntimeError(f"terminal exited with code {payload.get('code')}")
        raise RuntimeError("terminal input marker was not returned by ConPTY")
    except BaseException:
        print(
            "pty output before failure: "
            f"{received.decode('utf-8', errors='replace')!r}",
            file=sys.stderr,
        )
        raise


def _verify_desktop_lifecycle(desktop: Path) -> None:
    """Exercise WM_CLOSE and a second launch against the installed GUI executable."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    def find_window(pid: int) -> int | None:
        handles = []

        @callback_type
        def visit(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, title, len(title))
            if owner.value == pid and title.value == "Sona Code":
                handles.append(hwnd)
            return True

        user32.EnumWindows(visit, 0)
        return handles[0] if handles else None

    def wait_for(predicate, description: str) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"desktop exited during {description}: {process.returncode}")
            if predicate():
                return
            time.sleep(0.1)
        raise RuntimeError(f"desktop timed out: {description}")

    desktop_log = tempfile.TemporaryFile()
    process = subprocess.Popen([str(desktop)], stdout=desktop_log, stderr=subprocess.STDOUT)
    second = None
    base_url = "http://127.0.0.1:8117"
    try:
        _wait_until_ready(base_url, process)
        original = _request_json(base_url, "GET", "/__recorder/api/ping")
        if not original.get("instance_id", "").startswith(f"{process.pid}-"):
            raise RuntimeError("desktop did not start its own backend instance")
        settings = _request_json(base_url, "GET", "/__recorder/api/settings")
        app_dir = Path(os.environ["APPDATA"]) / "SonaCode"
        for key, expected in (
            ("config_path", app_dir / "config.json"),
            ("records_dir", app_dir / "records"),
        ):
            if os.path.normcase(os.path.normpath(settings[key])) != os.path.normcase(str(expected)):
                raise RuntimeError(f"unexpected desktop {key}: {settings[key]}")
        wait_for(lambda: find_window(process.pid), "create main window")
        hwnd = find_window(process.pid)
        wait_for(lambda: user32.IsWindowVisible(hwnd), "show main window")
        if not user32.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE
            raise ctypes.WinError(ctypes.get_last_error())
        wait_for(lambda: not user32.IsWindowVisible(hwnd), "hide main window on close")
        if _request_json(base_url, "GET", "/__recorder/api/ping") != original:
            raise RuntimeError("closing the desktop replaced/stopped its backend")
        second = subprocess.Popen([str(desktop)])
        if second.wait(timeout=15) != 0:
            raise RuntimeError("second desktop launch failed")
        wait_for(lambda: user32.IsWindowVisible(hwnd), "second launch restores hidden window")
        if _request_json(base_url, "GET", "/__recorder/api/ping") != original:
            raise RuntimeError("second launch replaced the backend instance")
    except Exception:
        desktop_log.seek(0)
        trace = desktop_log.read().decode("utf-8", errors="replace")[-8000:]
        trace = trace.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::notice title=Desktop lifecycle trace::{trace}", flush=True)
        raise
    finally:
        for child in (second, process):
            if child is not None and child.poll() is None:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(child.pid)],
                    capture_output=True, check=False, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                child.wait(timeout=10)
        desktop_log.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop", required=True, type=Path)
    parser.add_argument("--recorder", required=True, type=Path)
    parser.add_argument("--opencode", required=True, type=Path)
    args = parser.parse_args()

    subsystem = _desktop_subsystem(args.desktop)
    if subsystem != 2:
        raise RuntimeError(f"desktop executable is not Windows GUI subsystem: {subsystem}")

    with tempfile.TemporaryDirectory(
        prefix="sonacode-package-smoke-", ignore_cleanup_errors=True
    ) as temp_value:
        temp = Path(temp_value)
        env = os.environ.copy()
        env.update(
            {
                "SONACODE_BUNDLED_OPENCODE": str(args.opencode),
                "XDG_CONFIG_HOME": str(temp / "config"),
                "XDG_DATA_HOME": str(temp / "data"),
                "XDG_CACHE_HOME": str(temp / "cache"),
                "XDG_STATE_HOME": str(temp / "state"),
            }
        )
        log_path = temp / "sidecar.log"
        base_url = "http://127.0.0.1:18117"
        command = [
            str(args.recorder),
            "--host",
            "127.0.0.1",
            "--port",
            "18117",
            "--config",
            str(temp / "config" / "config.json"),
            "--records-dir",
            str(temp / "records"),
        ]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        session_id: str | None = None
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(
                command,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            try:
                _wait_until_ready(base_url, process)
                check = _request_json(base_url, "GET", "/__recorder/api/terminal/check")
                if check.get("opencode_source") != "bundled" or not check.get("opencode_version"):
                    raise RuntimeError(f"bundled OpenCode check failed: {check}")
                workspace_dir = temp / "中文 project with spaces"
                workspace_dir.mkdir()
                workspace_prefix = "/__recorder/api/workspace"
                workspace_check = _request_json(base_url, "GET", f"{workspace_prefix}/check")
                if not workspace_check.get("found") or workspace_check.get("source") != "bundled":
                    raise RuntimeError(f"bundled workspace check failed: {workspace_check}")
                project = _request_json(
                    base_url, "POST", f"{workspace_prefix}/projects",
                    {"path": str(workspace_dir)},
                )
                project_path = f"{workspace_prefix}/projects/{project['id']}"
                conversation = _request_json(
                    base_url, "POST", f"{project_path}/sessions", {}, timeout=30,
                )
                sessions = _request_json(base_url, "GET", f"{project_path}/sessions", timeout=30)
                if conversation["id"] not in {item["id"] for item in sessions["items"]}:
                    raise RuntimeError("packaged OpenCode did not retain the workspace conversation")
                messages = _request_json(
                    base_url, "GET", f"{project_path}/sessions/{conversation['id']}/messages",
                    timeout=30,
                )
                if not isinstance(messages, list):
                    raise RuntimeError(f"unexpected workspace messages response: {messages}")
                _request_json(base_url, "DELETE", project_path, timeout=30)
                session = _request_json(
                    base_url,
                    "POST",
                    "/__recorder/api/terminal/sessions",
                    {"cwd": str(temp), "kind": "shell", "cols": 100, "rows": 30},
                )
                session_id = session["id"]
                asyncio.run(_verify_terminal_input(base_url, session_id))
                _request_json(
                    base_url, "DELETE", f"/__recorder/api/terminal/sessions/{session_id}"
                )
                session_id = None
            except BaseException:
                log_file.flush()
                print(log_path.read_text(encoding="utf-8", errors="replace")[-12000:], file=sys.stderr)
                raise
            finally:
                if session_id is not None:
                    try:
                        _request_json(
                            base_url, "DELETE", f"/__recorder/api/terminal/sessions/{session_id}"
                        )
                    except Exception:
                        pass
                # A one-file PyInstaller executable has a worker child. Terminating
                # only the bootloader leaks the worker and can leave its port bound.
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                    capture_output=True, check=False, creationflags=creationflags,
                    timeout=10,
                )
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()

    _verify_desktop_lifecycle(args.desktop)
    print("Windows close/restore, packaged OpenCode workspace, and ConPTY execution smoke tests passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Public job annotations expose the concrete failure even when raw Actions
        # logs are unavailable. Keep the validation gate intact.
        message = f"{type(exc).__name__}: {exc}"
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Windows package validation failed::{message}", flush=True)
        raise
