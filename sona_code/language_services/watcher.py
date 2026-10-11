"""Bounded polling of project source/config changes, never draft reloading."""
from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

from sona_code.workspace.files import project_file_path

EXCLUDED = {'.git', 'node_modules', '.venv', 'venv', 'target', 'build', 'dist', '__pycache__', '.gradle'}
EXTENSIONS = {'python': {'.py', '.pyi'}, 'java': {'.java', '.jar'},
              'typescript': {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts', '.json'}}
CONFIGS = {'pyrightconfig.json', 'pyproject.toml', 'setup.cfg', 'pom.xml', 'build.gradle',
           'build.gradle.kts', 'settings.gradle', 'settings.gradle.kts', 'gradle.properties',
           'tsconfig.json', 'jsconfig.json', 'package.json', '.classpath', '.project'}


def _ordinary(info) -> bool:
    return not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400


class ProjectWatcher:
    def __init__(self, root: Path, family: str, emit_changes, *, limit: int = 20000):
        self.root, self.family, self.emit_changes = root, family, emit_changes
        self.limit = min(20000, max(1, limit))
        self.snapshot = {}
        self.limited = False
        self.closed = False
        self._task = None
        self._lock = asyncio.Lock()

    def _scan(self):
        result = {}
        limited = False
        try:
            if not _ordinary(self.root.lstat()) or not self.root.is_dir():
                return result, limited
        except OSError:
            return result, limited
        # Keep existing entries before admitting new files into a full watch set.
        for relative in self.snapshot:
            try:
                info = project_file_path(self.root, relative).stat()
                result[relative] = (info.st_mtime_ns, info.st_size)
            except Exception:
                pass
        visited = 0
        for directory, children, files in os.walk(self.root, followlinks=False):
            current = Path(directory)
            children[:] = sorted(name for name in children if name not in EXCLUDED
                                 and _ordinary((current / name).lstat()))
            visited += len(children) + len(files)
            if visited > 20000:
                limited = True
                break
            for name in sorted(files):
                path = current / name
                if name not in CONFIGS and path.suffix.lower() not in EXTENSIONS[self.family]:
                    continue
                relative = path.relative_to(self.root).as_posix()
                try:
                    info = path.lstat()
                    if not _ordinary(info) or not stat.S_ISREG(info.st_mode):
                        continue
                    if relative not in result and len(result) >= self.limit:
                        limited = True
                        continue
                    result[relative] = (info.st_mtime_ns, info.st_size)
                except OSError:
                    continue
        return result, limited

    async def start(self) -> None:
        self.snapshot, self.limited = await asyncio.to_thread(self._scan)
        self._task = asyncio.create_task(self._loop())

    async def scan(self) -> None:
        async with self._lock:
            updated, limited = await asyncio.to_thread(self._scan)
            if self.closed:
                return
            changes = [{'uri': (self.root / path).as_uri(), 'type': 1 if path not in self.snapshot else 2}
                       for path, value in updated.items() if self.snapshot.get(path) != value]
            changes += [{'uri': (self.root / path).as_uri(), 'type': 3} for path in self.snapshot if path not in updated]
            self.snapshot, self.limited = updated, limited
            if changes:
                await self.emit_changes(changes)

    async def saved(self, path: str) -> None:
        async with self._lock:
            info = await asyncio.to_thread(lambda: project_file_path(self.root, path).stat())
            event = {'uri': (self.root / path).as_uri(), 'type': 2 if path in self.snapshot else 1}
            if path in self.snapshot or len(self.snapshot) < self.limit:
                self.snapshot[path] = (info.st_mtime_ns, info.st_size)
            await self.emit_changes([event])

    async def _loop(self):
        while not self.closed:
            await asyncio.sleep(2)
            try:
                await self.scan()
            except OSError:
                continue

    async def close(self) -> None:
        self.closed = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
