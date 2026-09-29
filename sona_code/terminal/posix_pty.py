"""macOS PTY 进程封装，保持与 pywinpty 使用的少量接口一致。"""

from __future__ import annotations

import fcntl
import os
import signal
import struct
import termios


class PosixPtyProcess:
    def __init__(self, pid: int, fd: int):
        self.pid = pid
        self.fd = fd
        self.exitstatus: int | None = None
        self.closed = False

    @classmethod
    def spawn(
        cls, argv: list[str], cwd: str, env: dict[str, str], dimensions: tuple[int, int]
    ) -> "PosixPtyProcess":
        pid, fd = os.forkpty()
        if pid == 0:
            # fork 后的子进程只做必要的系统调用，然后立即 exec。
            try:
                os.chdir(cwd)
                os.execve(argv[0], argv, env)
            except BaseException:
                os._exit(127)
        proc = cls(pid, fd)
        proc.setwinsize(*dimensions)
        return proc

    def read(self, size: int = 65536) -> bytes:
        return os.read(self.fd, size)

    def write(self, data: str | bytes) -> None:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        while raw:
            raw = raw[os.write(self.fd, raw):]

    def setwinsize(self, rows: int, cols: int) -> None:
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def _reap(self) -> bool:
        if self.exitstatus is not None:
            return True
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return True
        if pid:
            self.exitstatus = os.waitstatus_to_exitcode(status)
            return True
        return False

    def close(self, force: bool = False) -> None:
        if self.closed:
            return
        if not self._reap():
            try:
                os.killpg(self.pid, signal.SIGKILL if force else signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.closed = True
        try:
            _, status = os.waitpid(self.pid, 0)
            self.exitstatus = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            pass
