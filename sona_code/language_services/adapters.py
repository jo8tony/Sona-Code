"""Fixed stdio launches and the client capabilities/configuration we support."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import NavigationError

SYSTEM_ENV = {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'USERPROFILE',
              'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA',
              'LANG', 'LC_ALL', 'HOME'}


@dataclass
class ServerLaunch:
    argv: list[str]
    env: dict[str, str]
    initialize: dict
    configuration: dict
    timeout: float


def _source_roots(project: Path) -> list[str]:
    roots = []
    # Bounded discovery; never descend dependencies, build output or links.
    for directory, children, _ in os.walk(project, followlinks=False):
        current = Path(directory)
        children[:] = [name for name in children if name not in {'.git', 'node_modules', '.venv', 'target', 'build', 'dist'}
                       and not (current / name).is_symlink()
                       and not getattr((current / name).lstat(), 'st_file_attributes', 0) & 0x400]
        relative = current.relative_to(project).as_posix()
        if relative.endswith(('src/main/java', 'src/test/java')):
            roots.append(relative)
            children[:] = []
        if len(roots) >= 100:
            break
    return roots or ['.']


def build_launch(bundle, family: str, project: Path, settings, cache: Path) -> ServerLaunch:
    paths = bundle.launch_paths(family)
    cache.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENV}
    env['PATH'] = str(paths['node'].parent) + os.pathsep + str(paths['java'].parent)
    initialize = {'processId': os.getpid(), 'rootUri': project.as_uri(),
                  'workspaceFolders': [{'uri': project.as_uri(), 'name': project.name}],
                  'clientInfo': {'name': 'Sona Code', 'version': '2.3.1'},
                  'capabilities': {
                      'general': {'positionEncodings': ['utf-16']},
                      'textDocument': {'definition': {'linkSupport': True},
                                       'synchronization': {'didSave': True}},
                      'workspace': {'configuration': True, 'workspaceFolders': True,
                                    'didChangeWatchedFiles': {'dynamicRegistration': True}},
                      'window': {'workDoneProgress': True}}}
    configuration = {}
    if family == 'java':
        destination = cache / 'configuration'
        if not destination.exists():
            shutil.copytree(paths['configuration'], destination)
        configuration_file = destination / 'config.ini'
        if configuration_file.is_file():
            lines = configuration_file.read_text(encoding='utf-8').splitlines()
            changed = False
            for index, line in enumerate(lines):
                if line.startswith('osgi.bundles='):
                    entries = line.partition('=')[2].split(',')
                    entries = [entry for entry in entries if not any(name in entry for name in (
                        'org.eclipse.m2e.apt.core_', 'org.eclipse.jdt.apt.pluggable.core_'))]
                    lines[index] = 'osgi.bundles=' + ','.join(entries)
                    changed = changed or lines[index] != line
            if changed:
                configuration_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        java = settings.java
        if java.mode == 'gradle' and not java.gradle_home:
            raise NavigationError('missing_build_tool', '离线 Gradle 导入需要指定已有 Gradle 目录')
        config = {'autobuild': {'enabled': False},
                  'configuration': {'updateBuildConfiguration': 'disabled'},
                  'import': {'maven': {'enabled': java.mode == 'maven', 'offline': {'enabled': True}},
                             'gradle': {'enabled': java.mode == 'gradle', 'offline': {'enabled': True},
                                        'wrapper': {'enabled': False}, 'version': None,
                                        'home': java.gradle_home, 'annotationProcessing': {'enabled': False}}},
                  'maven': {'downloadSources': False, 'updateSnapshots': False},
                  'eclipse': {'downloadSources': False},
                  'jdt': {'ls': {'lombokSupport': {'enabled': False}}},
                  'project': {'sourcePaths': java.source_roots or _source_roots(project),
                              'referencedLibraries': {'include': java.libraries or ['lib/**/*.jar'],
                                                      'exclude': [], 'sources': {}}}}
        if java.project_jdk:
            config['configuration']['runtimes'] = [{'name': 'JavaSE-21', 'path': java.project_jdk, 'default': True}]
        configuration = {'java': config}
        initialize['initializationOptions'] = {'settings': configuration}
        argv = [str(paths['java']), '-Xmx1G', '-Declipse.application=org.eclipse.jdt.ls.core.id1',
                '-Dosgi.bundles.defaultStartLevel=4', '-Declipse.product=org.eclipse.jdt.ls.core.product',
                '--add-modules=ALL-SYSTEM', '--add-opens=java.base/java.util=ALL-UNNAMED',
                '--add-opens=java.base/java.lang=ALL-UNNAMED', '-jar', str(paths['equinox']),
                '-configuration', str(destination), '-data', str(cache / 'workspace')]
        timeout = 120
    else:
        entry = paths['pyright'] if family == 'python' else paths['typescript_server']
        argv = [str(paths['node']), '--max-old-space-size=512', str(entry), '--stdio']
        timeout = 45
        if family == 'python':
            configuration = {'python': {'analysis': {'autoSearchPaths': True, 'diagnosticMode': 'openFilesOnly'}}}
            if settings.python.interpreter:
                configuration['python']['pythonPath'] = settings.python.interpreter
        else:
            initialize['initializationOptions'] = {
                'tsserver': {'path': str(paths['typescript']), 'fallbackPath': str(paths['typescript']),
                             'logDirectory': str(cache / 'logs'), 'logVerbosity': 'off', 'trace': 'off'},
                'disableAutomaticTypingAcquisition': True, 'plugins': [], 'maxTsServerMemory': 512,
                'preferences': {'allowLocalPluginLoads': False}}
    return ServerLaunch(argv, env, initialize, configuration, timeout)
