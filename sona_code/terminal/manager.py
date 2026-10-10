"""终端会话管理：Windows ConPTY / macOS PTY 托管 opencode / shell。

设计要点：
- 每个会话一个输出泵任务：阻塞 read 放线程池，读到数据后广播给所有已连接
  WebSocket 客户端并追加环形回放缓冲；EOF/异常即视为进程退出。
- 会话生命周期 = 服务进程生命周期；服务关闭时统一 kill，避免孤儿进程。
- 环境注入（联动代理）：仅 opencode 会话注入 OPENAI/ANTHROPIC_BASE_URL，
  使其 LLM 请求经本代理转发，进入轨迹记录体系。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from starlette.websockets import WebSocket

from sona_code.config import AppConfig

logger = logging.getLogger("sona_code")

_VERSION_CACHE_TTL_SECONDS = 30.0
_version_cache: dict[tuple[str, float], tuple[float, str | None]] = {}

if sys.platform == "win32":
    try:
        from winpty import PtyProcess  # type: ignore[import-not-found]
    except ImportError:
        PtyProcess = None
elif sys.platform == "darwin":
    from sona_code.terminal.posix_pty import PosixPtyProcess as PtyProcess
else:
    PtyProcess = None

# 会话占位 API key：仅上游 key_strategy=replace 时注入（代理侧会替换真实 key）
BUNDLED_OPENCODE_ENV = "SONACODE_BUNDLED_OPENCODE"
ORIGINAL_XDG_PREFIX = "SONACODE_ORIGINAL_"
XDG_KEYS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")


class TerminalError(Exception):
    """终端操作失败：携带 HTTP 状态码与 detail。"""

    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


@dataclass
class TerminalSession:
    id: str
    kind: str  # "opencode" | "shell"
    cwd: str
    title: str
    created_at: str
    proc: object
    max_buffer: int  # 回放缓冲字节上限（0=禁用）
    alive: bool = True
    exit_code: int | None = None
    clients: set = field(default_factory=set)
    buffer: list[bytes] = field(default_factory=list)
    buf_size: int = 0
    pump_task: asyncio.Task | None = None
    output_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    input_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    scope: object | None = None

    def append_buffer(self, raw: bytes) -> None:
        if self.max_buffer <= 0:
            return
        self.buffer.append(raw)
        self.buf_size += len(raw)
        while self.buf_size > self.max_buffer and len(self.buffer) > 1:
            self.buf_size -= len(self.buffer.pop(0))

    def info(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "cwd": self.cwd,
            "title": self.title,
            "alive": self.alive,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "clients": len(self.clients),
        }


# ------------------------------------------------------------------ 命令解析
def detect_shell() -> str:
    """按当前系统选择通用 shell。"""
    if sys.platform == "darwin":
        preferred = os.environ.get("SHELL", "")
        if preferred and Path(preferred).is_file():
            return preferred
        return "/bin/zsh"
    for name in ("pwsh.exe", "powershell.exe"):
        if shutil.which(name):
            return name
    return "powershell.exe"


@lru_cache(maxsize=1)
def _macos_login_path() -> str | None:
    """读取用户终端的 PATH；Finder 启动的桌面应用通常只有系统默认 PATH。"""
    if sys.platform != "darwin":
        return None
    marker = "__SONACODE_LOGIN_PATH__="
    try:
        result = subprocess.run(
            [detect_shell(), "-lic", f'printf "\\n{marker}%s\\n" "$PATH"'],
            capture_output=True,
            check=False,
            text=True,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed(result.stdout.splitlines()):
        if line.startswith(marker):
            return line[len(marker):] or None
    return None


def _macos_command_path() -> str:
    """合并交互终端路径与常见安装位置，用于查找和启动 CLI。"""
    paths = []
    for value in (
        _macos_login_path(),
        os.environ.get("PATH", ""),
        "/opt/homebrew/bin:/usr/local/bin",
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".opencode" / "bin"),
        str(Path.home() / ".bun" / "bin"),
    ):
        paths.extend(part for part in (value or "").split(os.pathsep) if part)
    return os.pathsep.join(dict.fromkeys(paths))


def resolve_executable(command: str) -> str | None:
    """解析命令到完整可执行路径；找不到返回 None。支持完整路径或 PATH 查找。"""
    command = command.strip()
    if not command:
        return None
    if os.path.sep in command or (len(command) >= 2 and command[1] == ":"):
        p = Path(command).expanduser()
        return str(p) if p.is_file() else None
    path = _macos_command_path() if sys.platform == "darwin" else None
    return shutil.which(command, path=path)


def _build_argv(exe: str, args: list[str]) -> list[str]:
    """npm shim（.cmd/.bat）不能被 CreateProcess 直接执行，需经 cmd.exe /c 包装。"""
    if exe.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", exe, *args]
    return [exe, *args]


@dataclass(frozen=True)
class OpenCodeResolution:
    path: str | None
    source: str  # "bundled" | "path" | "custom" | "missing"


def resolve_opencode(cfg: AppConfig) -> OpenCodeResolution:
    """按配置解析 OpenCode：自定义模式不回退，自动模式优先 Windows 随包版。"""
    terminal = cfg.terminal
    if terminal.command_mode == "custom":
        path = resolve_executable(terminal.command)
        return OpenCodeResolution(path, "custom" if path else "missing")

    if sys.platform == "win32":
        bundled = os.environ.get(BUNDLED_OPENCODE_ENV, "").strip()
        if bundled:
            path = Path(bundled)
            if path.is_file():
                return OpenCodeResolution(str(path), "bundled")

    path = resolve_executable("opencode")
    return OpenCodeResolution(path, "path" if path else "missing")


def executable_version(executable: str | None, timeout: float = 3.0) -> str | None:
    """快速探测 OpenCode 版本；任何启动或超时失败均返回 None。"""
    if not executable:
        return None
    cache_key = (os.path.normcase(os.path.abspath(executable)), timeout)
    cached = _version_cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _VERSION_CACHE_TTL_SECONDS:
        return cached[1]

    run_kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        # 桌面应用没有父控制台；版本探测若不显式隐藏窗口，会短暂弹出 cmd 窗口。
        run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        result = subprocess.run(
            _build_argv(executable, ["--version"]),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
            **run_kwargs,
        )
    except (OSError, subprocess.SubprocessError):
        _version_cache[cache_key] = (now, None)
        return None
    output = (result.stdout or result.stderr).strip()
    version = output.splitlines()[0].strip() if result.returncode == 0 and output else None
    _version_cache[cache_key] = (now, version)
    return version


def detect_git_bash() -> str | None:
    """检测 Git for Windows 的 bash，不把 System32/WSL bash 误认为 Git Bash。"""
    configured = os.environ.get("OPENCODE_GIT_BASH_PATH", "").strip()
    if configured and Path(configured).is_file():
        return configured
    if sys.platform != "win32":
        return None

    candidates: list[Path] = []
    for root_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(root_name)
        if not root:
            continue
        base = Path(root)
        if root_name == "LOCALAPPDATA":
            candidates.append(base / "Programs" / "Git" / "bin" / "bash.exe")
        else:
            candidates.extend(
                (
                    base / "Git" / "bin" / "bash.exe",
                    base / "Git" / "usr" / "bin" / "bash.exe",
                )
            )

    git = shutil.which("git.exe") or shutil.which("git")
    if git:
        git_path = Path(git)
        # Git/cmd/git.exe -> Git/bin/bash.exe
        candidates.append(git_path.parent.parent / "bin" / "bash.exe")
        candidates.append(git_path.parent.parent / "usr" / "bin" / "bash.exe")
    return next((str(path) for path in candidates if path.is_file()), None)


def _restore_user_xdg(env: dict[str, str]) -> None:
    """通用 shell 会话恢复桌面应用启动前的 XDG 环境，避免遭受 OpenCode 隔离目录影响。"""
    for key in XDG_KEYS:
        original = os.environ.get(f"{ORIGINAL_XDG_PREFIX}{key}", "")
        if original:
            env[key] = original
        else:
            env.pop(key, None)


def _build_env(cfg: AppConfig, kind: str) -> dict[str, str]:
    """进程环境：终端变量、OpenCode 临时 provider 配置、用户覆盖。"""
    env = dict(os.environ)
    if sys.platform == "darwin":
        env["PATH"] = _macos_command_path()
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    t = cfg.terminal
    if kind == "shell":
        _restore_user_xdg(env)
    if kind == "opencode":
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
        git_bash = detect_git_bash()
        if git_bash:
            env["OPENCODE_GIT_BASH_PATH"] = git_bash
        else:
            env.pop("OPENCODE_GIT_BASH_PATH", None)
    env.update(t.inject_env)  # 用户配置可覆盖代理与目录注入
    if kind == "opencode":
        # 随应用发布的 OpenCode 必须由应用升级，禁止子进程自行替换版本。
        env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    if kind == "opencode":
        from sona_code.admin.models import compile_providers, native_provider_id
        try:
            inline = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
            if not isinstance(inline, dict):
                inline = {}
        except ValueError:
            inline = {}
        managed = compile_providers(cfg)
        if managed:
            providers = inline.get("provider")
            if not isinstance(providers, dict):
                providers = {}
            providers.update(managed)
            inline["provider"] = providers
            env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
        choice = cfg.model_settings.default_model if cfg.model_settings.source == "custom" else None
        if choice:
            inline["model"] = f"{native_provider_id(choice.provider)}/{choice.model}"
        elif cfg.model_settings.source == "sona":
            first = next((provider for provider in cfg.upstreams
                          if provider.source == "sona" and provider.models), None)
            if first:
                inline["model"] = f"{native_provider_id(first.name)}/{first.models[0].id}"
        if inline:
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline, ensure_ascii=False)
        from sona_code.admin.skills import SkillStore
        store = SkillStore()
        try:
            inline = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
            if not isinstance(inline, dict):
                inline = {}
        except ValueError:
            inline = {}
        external_paths = store.external_paths()
        skills = inline.get("skills")
        if not isinstance(skills, dict):
            skills = {}
        paths = skills.get("paths", [])
        skills["paths"] = list(dict.fromkeys([*(paths if isinstance(paths, list) else []), *external_paths]))
        if external_paths:
            inline["skills"] = skills
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(inline, ensure_ascii=False)
    for key in list(env):
        if key in (BUNDLED_OPENCODE_ENV, "SONACODE_DESKTOP_INSTANCE_ID", "SONACODE_SHUTDOWN_TOKEN") or key.startswith(
            ORIGINAL_XDG_PREFIX
        ):
            env.pop(key, None)
    return env


# ---------------------------------------------------------------------- 管理器
class TerminalManager:
    def __init__(self) -> None:
        self.sessions: dict[str, TerminalSession] = {}
        self.closing = False
        self.commands = None

    # ------------------------------------------------------------ 会话生命周期
    async def create(
        self, cfg: AppConfig, cwd: str, kind: str, rows: int, cols: int
    ) -> TerminalSession:
        t = cfg.terminal
        if self.closing:
            raise TerminalError("应用正在退出", 503)
        if not t.enabled:
            raise TerminalError("终端模块未启用（settings 中 terminal.enabled）")
        if PtyProcess is None:
            raise TerminalError("当前平台缺少终端 PTY 依赖或尚不支持", 500)
        if len(self.sessions) >= t.max_sessions:
            raise TerminalError(f"已达会话上限（max_sessions={t.max_sessions}）")

        path = Path(cwd).expanduser()
        if not path.is_absolute():
            raise TerminalError("cwd 必须是绝对路径")
        if not path.is_dir():
            raise TerminalError(f"目录不存在或不是文件夹：{path}")

        if kind == "opencode":
            resolution = resolve_opencode(cfg)
            if resolution.path is None:
                if t.command_mode == "custom":
                    raise TerminalError(f"未找到自定义 OpenCode 命令：{t.command}")
                raise TerminalError("未找到随包或 PATH 中的 OpenCode 可执行程序")
            command, args = resolution.path, list(t.args)
        elif kind == "shell":
            command, args = t.shell_command or detect_shell(), []
        else:
            raise TerminalError(f"未知会话类型：{kind}")

        exe = resolve_executable(command)
        if exe is None:
            raise TerminalError(f"未找到命令 {command}，请确认已安装并在 PATH 中")
        argv = _build_argv(exe, args)
        env = _build_env(cfg, kind)
        if getattr(self, "environment", None) is not None:
            env = await asyncio.to_thread(self.environment, str(path.resolve()), kind, env)

        sid = uuid.uuid4().hex[:12]
        if self.commands is not None and kind == "opencode":
            env = self.commands.environment("terminal:" + sid, str(path.resolve()), env)
        scope = None
        launch_task = None
        try:
            if self.commands is not None and os.name == "nt":
                from sona_code.processes import WindowsJob
                scope = WindowsJob()
            def launch():
                spawned = PtyProcess.spawn(argv, cwd=str(path), env=env, dimensions=(rows, cols))
                try:
                    if scope is not None:
                        scope.assign(spawned.pid)
                except BaseException:
                    spawned.close(True)
                    raise
                return spawned
            if sys.platform == "darwin":
                # 直接在事件循环线程 forkpty，避免在线程池工作线程中死锁。
                proc = PtyProcess.spawn(argv, cwd=str(path), env=env, dimensions=(rows, cols))
            else:
                launch_task = asyncio.create_task(asyncio.to_thread(launch))
                proc = await asyncio.shield(launch_task)
            if self.closing:
                await asyncio.to_thread(proc.close, True)
                if scope is not None:
                    await asyncio.to_thread(scope.close)
                raise TerminalError("应用正在退出", 503)
        except BaseException as e:
            if launch_task is not None and 'proc' not in locals():
                try:
                    proc = await asyncio.shield(launch_task)
                except BaseException:
                    pass
            if 'proc' in locals():
                await asyncio.to_thread(proc.close, True)
            if scope is not None:
                await asyncio.to_thread(scope.close)
            if self.commands is not None:
                await self.commands.stop_owner("terminal:" + sid)
            if isinstance(e, asyncio.CancelledError):
                raise
            raise TerminalError(f"启动失败：{type(e).__name__}: {e}") from e

        session = TerminalSession(
            id=sid,
            kind=kind,
            cwd=str(path),
            title=path.name or str(path),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            proc=proc,
            max_buffer=t.scrollback_kb * 1024,
            scope=scope,
        )
        self.sessions[sid] = session
        session.pump_task = asyncio.create_task(self._pump(session))
        logger.info("终端会话 %s 启动：%s（cwd=%s）", sid, " ".join(argv)[:200], path)
        return session

    def get(self, session_id: str) -> TerminalSession | None:
        return self.sessions.get(session_id)

    def list(self) -> list[dict]:
        return [s.info() for s in self.sessions.values()]

    async def kill(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session is None:
            return False
        try:
            if self.commands is not None:
                await self.commands.stop_owner("terminal:" + session_id)
            if session.scope is not None:
                await asyncio.to_thread(session.scope.close)
            # close(force=True)：关 socket（解除泵阻塞）+ 强杀进程
            await asyncio.to_thread(session.proc.close, True)
        except Exception:
            logger.warning("终端会话 %s 关闭进程失败", session_id, exc_info=True)
            raise TerminalError("终端进程清理失败，请重试", 503)
        session.alive = False
        if session.pump_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(session.pump_task), 3)
            except asyncio.TimeoutError:
                session.pump_task.cancel()
                await asyncio.gather(session.pump_task, return_exceptions=True)
        self.sessions.pop(session_id, None)
        logger.info("终端会话 %s 已终止（cwd=%s）", session_id, session.cwd)
        return True

    async def shutdown(self) -> None:
        """服务退出时终止全部会话进程。"""
        ids = list(self.sessions)
        if ids:
            results = await asyncio.gather(*(self.kill(sid) for sid in ids), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    # ------------------------------------------------------------ 客户端交互
    async def attach(self, websocket: WebSocket, session: TerminalSession) -> None:
        """先发送回放，再订阅实时输出，避免附加时同一分块收到两次。"""
        async with session.output_lock:
            await websocket.send_text(json.dumps(
                {"type": "attached", "alive": session.alive, "kind": session.kind}
            ))
            replay = b"".join(session.buffer)
            if replay:
                await websocket.send_bytes(replay)
            if session.alive:
                session.clients.add(websocket)
            else:
                await websocket.send_text(json.dumps({"type": "exit", "code": session.exit_code}))

    def detach(self, websocket: WebSocket, session: TerminalSession) -> None:
        session.clients.discard(websocket)

    async def write(self, session: TerminalSession, text: str) -> str | None:
        """按顺序写入 PTY；失败时返回可安全展示给前端的错误。"""
        if not text:
            return None
        if not session.alive:
            return "终端进程已经退出"
        try:
            # 多个浏览器连接可能同时操作同一会话，锁保证按键顺序不会交错。
            async with session.input_lock:
                await asyncio.to_thread(session.proc.write, text)
            return None
        except Exception:
            logger.warning(
                "终端会话 %s 写入失败（字符数=%s）",
                session.id,
                len(text),
                exc_info=True,
            )
            return "终端输入写入失败，请重新启动会话"

    def resize(self, session: TerminalSession, cols: int, rows: int) -> None:
        try:
            session.proc.setwinsize(rows, cols)
        except Exception:
            logger.debug("会话 %s resize 失败", session.id, exc_info=True)

    # ---------------------------------------------------------------- 输出泵
    async def _pump(self, session: TerminalSession) -> None:
        proc = session.proc
        try:
            while True:
                try:
                    # Windows 返回 str，macOS 返回 bytes；EOF 抛异常。
                    data = await asyncio.to_thread(proc.read, 65536)
                except (EOFError, OSError, ValueError):
                    break
                if not data:
                    await asyncio.sleep(0.05)
                    continue
                raw = data.encode("utf-8") if isinstance(data, str) else data
                async with session.output_lock:
                    session.append_buffer(raw)
                    await self._broadcast(session, raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("终端会话 %s 输出泵异常", session.id, exc_info=True)
        finally:
            session.alive = False
            if self.commands is not None:
                await self.commands.stop_owner("terminal:" + session.id)
            if session.scope is not None:
                await asyncio.to_thread(session.scope.close)
            try:
                if hasattr(proc, "_reap"):
                    proc._reap()
                session.exit_code = proc.exitstatus
            except Exception:
                pass
            await self._notify_exit(session)
            logger.info("终端会话 %s 进程退出（code=%s）", session.id, session.exit_code)

    async def _broadcast(self, session: TerminalSession, raw: bytes) -> None:
        for ws in list(session.clients):
            try:
                await ws.send_bytes(raw)
            except Exception:
                session.clients.discard(ws)

    async def _notify_exit(self, session: TerminalSession) -> None:
        import json

        msg = json.dumps({"type": "exit", "code": session.exit_code})
        for ws in list(session.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                session.clients.discard(ws)
