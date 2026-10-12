"""Resolve only verified, pinned language-service resources; never download."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from .errors import NavigationError

VERSIONS = {'node': '22.23.3', 'jdk': '21.0.12.1+1', 'jdtls': '1.61.0',
            'pyright': '1.1.414', 'typescript_server': '6.0.2', 'typescript': '6.0.3'}
ENTRIES = {'node': 'runtime/node.exe', 'jdk': 'runtime/jdk/bin/java.exe',
           'pyright': 'servers/node/node_modules/pyright/langserver.index.js',
           'typescript_server': 'servers/node/node_modules/typescript-language-server/lib/cli.mjs',
           'typescript': 'servers/node/node_modules/typescript/lib/tsserver.js'}
REQUIRED_FILES = (
    'runtime/jdk/bin/server/jvm.dll', 'runtime/jdk/lib/modules', 'runtime/jdk/release',
    'servers/jdtls/config_win/config.ini',
    'servers/node/node_modules/pyright/dist/pyright-langserver.js',
    'servers/node/node_modules/typescript/lib/_tsserver.js',
    'servers/node/node_modules/typescript/lib/typescript.js',
    'licenses/node/LICENSE', 'licenses/temurin/java.base/LICENSE', 'licenses/jdtls/LICENSE',
    'licenses/pyright/LICENSE.txt', 'licenses/typescript/LICENSE.txt',
    'licenses/typescript-language-server/LICENSE', 'licenses/SONA_THIRD_PARTY_NOTICES.md',
    'sources/manifest.json',
)


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_path(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative or '\0' in relative
            or any(part in {'', '.', '..'} for part in relative.split('/'))):
        raise NavigationError('invalid_bundle', '语言服务清单含无效路径')
    current = root.resolve(strict=True)
    for part in relative.split('/'):
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise NavigationError('invalid_bundle', '语言服务资源不允许链接或重解析点')
    return current


class LanguageBundle:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or os.environ.get('SONACODE_BUNDLED_LANGUAGE_SERVICES')
                         or Path(__file__).resolve().parents[2] / 'build/language-services').absolute()
        self.manifest = None

    def check(self) -> dict:
        try:
            manifest = json.loads(checked_path(self.root, 'manifest.json').read_text(encoding='utf-8'))
            if manifest.get('schema') != 1 or manifest.get('platform') != 'windows-x64':
                raise ValueError('schema')
            components, files = manifest['components'], manifest['files']
            if not isinstance(components, dict) or set(components) != set(VERSIONS) or not isinstance(files, dict):
                raise ValueError('component inventory')
            if any(relative not in files for relative in REQUIRED_FILES):
                raise ValueError('missing required runtime, license or source inventory')
            for name, version in VERSIONS.items():
                component = components[name]
                if component['version'] != version:
                    raise ValueError('version')
                entry = component['entry']
                if name == 'jdtls':
                    if not entry.startswith('servers/jdtls/plugins/org.eclipse.equinox.launcher_') or not entry.endswith('.jar'):
                        raise ValueError('launcher')
                elif entry != ENTRIES[name]:
                    raise ValueError('entry')
                if entry not in files:
                    raise ValueError('missing entry hash')
            for relative, expected in files.items():
                if not isinstance(expected, str) or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
                    raise ValueError('invalid checksum')
                path = checked_path(self.root, relative)
                if not path.is_file() or digest(path) != expected:
                    raise ValueError('missing or corrupt resource')
            sources = manifest['sources']
            if not isinstance(sources, list) or not all(isinstance(item, dict) for item in sources):
                raise ValueError('source inventory')
            for name, version in VERSIONS.items():
                matches = [item for item in sources if item['component'] == name and item['version'] == version]
                if not matches or any(files.get(item['path']) != item['sha256'] for item in matches):
                    raise ValueError('missing corresponding source')
            coverage = manifest['source_coverage']
            if not isinstance(coverage, dict):
                raise ValueError('source coverage')
            for relative in files:
                if relative.startswith(('servers/jdtls/plugins/org.eclipse.', 'servers/jdtls/plugins/ch.qos.logback.')):
                    relative = '/'.join(relative.split('/')[:4])
                    # A full tag archive can cover several JDT bundles; other Eclipse
                    # components require a matching source artifact explicitly mapped.
                    if relative not in coverage:
                        raise ValueError('uncovered Eclipse bundle')
                    if relative in coverage and coverage[relative] not in files:
                        raise ValueError('missing Eclipse source')
            checked_path(self.root, 'servers/jdtls/config_win')
            source_index = json.loads(checked_path(self.root, 'sources/manifest.json').read_text(encoding='utf-8'))
            if (source_index.get('sources') != sources or source_index.get('coverage') != coverage
                    or not isinstance(source_index.get('eclipse'), list)):
                raise ValueError('source inventory differs from resource manifest')
            self.manifest = manifest
            return manifest
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise NavigationError('bundle_unavailable', '语言服务资源缺失或校验失败，请重新安装完整组件包') from exc

    def launch_paths(self, family: str) -> dict[str, Path]:
        if family not in {'java', 'python', 'typescript'}:
            raise NavigationError('unsupported_language', '此语言暂不支持项目跳转')
        manifest = self.check()
        paths = {name: checked_path(self.root, component['entry']) for name, component in manifest['components'].items()}
        paths['java'], paths['equinox'] = paths['jdk'], paths['jdtls']
        paths['configuration'] = checked_path(self.root, 'servers/jdtls/config_win')
        return paths
