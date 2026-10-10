"""Owned process scopes; Windows jobs survive exited intermediate parents."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time


class ProcessScope:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.job = None
        self.closed = False
        self._close_lock = threading.Lock()
        if os.name == "nt":
            self.job = WindowsJob()

    def spawn(self, argv: list[str], **kwargs) -> subprocess.Popen:
        if self.job:
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x00000004
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(argv, **kwargs)
            self.process = process
            if self.job:
                self.job.assign(process.pid)
                _resume_process(process.pid)
        except BaseException:
            if self.process is not None:
                self.process.kill()
                self.process.wait(timeout=3)
            self.close()
            raise
        return process

    def close(self) -> None:
        with self._close_lock:
            if self.closed:
                return
            self._close()
            self.closed = True

    def _close(self) -> None:
        if self.job:
            self.job.close()
            self.job = None
        elif self.process:
            # A group can outlive its leader. Always address the saved group ID.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.process and self.process.poll() is None:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                    ("max_working", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("basic", _BasicLimits), ("io", ctypes.c_uint64 * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    class _Accounting(ctypes.Structure):
        _fields_ = [("times", ctypes.c_int64 * 4), ("faults", wintypes.DWORD),
                    ("total", wintypes.DWORD), ("active", wintypes.DWORD), ("terminated", wintypes.DWORD)]

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                    ("id", wintypes.DWORD), ("owner", wintypes.DWORD),
                    ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
                    ("flags", wintypes.DWORD)]

    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]

    def _check(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def _resume_process(pid: int) -> None:
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry(size=ctypes.sizeof(_ThreadEntry))
            more = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.owner == pid:
                    thread = _check(kernel.OpenThread(2, False, entry.id))
                    try:
                        if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise ctypes.WinError(ctypes.get_last_error())
                    finally:
                        kernel.CloseHandle(thread)
                    return
                more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("Owned process thread was not found")
        finally:
            kernel.CloseHandle(snapshot)

    class WindowsJob:
        def __init__(self) -> None:
            self._close_lock = threading.Lock()
            self.handle = _check(kernel.CreateJobObjectW(None, None))
            limits = _ExtendedLimits()
            limits.basic.flags = 0x00002000  # KILL_ON_JOB_CLOSE, no breakaway.
            try:
                _check(kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
            except BaseException:
                kernel.CloseHandle(self.handle)
                self.handle = None
                raise

        def assign(self, pid: int) -> None:
            process = _check(kernel.OpenProcess(0x0101, False, pid))
            try:
                _check(kernel.AssignProcessToJobObject(self.handle, process))
            finally:
                kernel.CloseHandle(process)

        def close(self) -> None:
            with self._close_lock:
                self._close()

        def _close(self) -> None:
            if not self.handle:
                return
            _check(kernel.TerminateJobObject(self.handle, 1))
            deadline = time.monotonic() + 3
            while True:
                info = _Accounting()
                _check(kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None))
                if not info.active:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Owned process job did not terminate")
                time.sleep(.02)
            _check(kernel.CloseHandle(self.handle))
            self.handle = None
