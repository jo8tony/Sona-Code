"""Typed project analysis configuration stored only in the app data directory."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sona_code.workspace.files import FileError, project_directory_path, project_file_path
from .errors import NavigationError

LocalPath = Annotated[str, Field(min_length=1, max_length=4096)]


class JavaSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['source', 'maven', 'gradle'] = 'source'
    source_roots: list[LocalPath] = Field(default_factory=list, max_length=100)
    libraries: list[LocalPath] = Field(default_factory=list, max_length=1000)
    project_jdk: LocalPath | None = None
    gradle_home: LocalPath | None = None


class PythonSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    interpreter: LocalPath | None = None


class AnalysisSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    java: JavaSettings = Field(default_factory=JavaSettings)
    python: PythonSettings = Field(default_factory=PythonSettings)


def _external(value: str, *, directory=False) -> Path:
    if '\0' in value or not Path(value).is_absolute():
        raise ValueError('需要已有环境的绝对路径')
    path = Path(value)
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('运行环境不允许重解析点')
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError('运行环境路径不存在')
    return path


def validate_settings(root: Path, settings: AnalysisSettings) -> None:
    try:
        for path in settings.java.source_roots:
            project_directory_path(root, path)
        for path in settings.java.libraries:
            if project_file_path(root, path).suffix.lower() != '.jar':
                raise ValueError('Java 库必须为已有 JAR 文件')
        if settings.python.interpreter:
            _external(settings.python.interpreter)
        if settings.java.project_jdk:
            jdk = _external(settings.java.project_jdk, directory=True)
            _external(str(jdk / 'bin' / ('java.exe' if os.name == 'nt' else 'java')))
            _external(str(jdk / 'release'))
            jdk_runtime_name(jdk)
        if settings.java.gradle_home:
            gradle = _external(settings.java.gradle_home, directory=True)
            _external(str(gradle / 'bin' / ('gradle.bat' if os.name == 'nt' else 'gradle')))
        if settings.java.mode == 'gradle' and not settings.java.gradle_home:
            raise ValueError('离线 Gradle 导入需要指定已有 Gradle 目录')
    except (OSError, ValueError, FileError) as exc:
        raise NavigationError('invalid_settings', '请检查项目内源码/JAR 路径和已有本地运行环境：' + str(exc)[:300]) from exc


def jdk_runtime_name(jdk: Path) -> str:
    """Use the selected project's JDK version, independently of the server JDK."""
    try:
        with (jdk / 'release').open('rb') as source:
            content = source.read(65537)
        if len(content) > 65536:
            raise ValueError()
        match = re.search(r'^JAVA_VERSION="(\d+)(?:\.(\d+))?', content.decode('utf-8'), re.MULTILINE)
        if not match:
            raise ValueError()
        major = int(match.group(2)) if match.group(1) == '1' else int(match.group(1))
        if major < 8:
            raise ValueError()
        return 'JavaSE-1.8' if major == 8 else f'JavaSE-{major}'
    except (OSError, ValueError, TypeError) as exc:
        raise NavigationError('invalid_settings', '请指定带有有效 release 信息的 JDK 8 或更高版本目录') from exc


class AnalysisSettingsStore:
    def __init__(self, data_root: Path):
        self.path = data_root / 'settings.json'
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('schema') != 1 or not isinstance(data.get('projects'), dict):
                raise ValueError()
            return data['projects']
        except (ValueError, AttributeError) as exc:
            raise NavigationError('invalid_settings', '项目分析设置文件损坏，请恢复应用内的 settings.json') from exc

    def get(self, project_id: str) -> AnalysisSettings:
        with self._lock:
            try:
                return AnalysisSettings.model_validate(self._read().get(project_id, {}))
            except ValidationError as exc:
                raise NavigationError('invalid_settings', '已保存的项目分析设置不符合当前格式') from exc

    def _write(self, projects):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.analysis-settings-', suffix='.tmp')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump({'schema': 1, 'projects': projects}, output, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def save(self, project_id: str, root: Path, settings: AnalysisSettings) -> None:
        validate_settings(root, settings)
        with self._lock:
            projects = self._read()
            projects[project_id] = settings.model_dump()
            self._write(projects)

    def remove(self, project_id: str) -> None:
        with self._lock:
            projects = self._read()
            if project_id in projects:
                del projects[project_id]
                self._write(projects)
