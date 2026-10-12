"""Prepare pinned Windows language servers and corresponding source archives.

--check is strictly offline and read-only. Downloading is limited to --prepare.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sona_code.language_services.bundle import ENTRIES, VERSIONS, LanguageBundle, digest

INPUTS = ['THIRD_PARTY_NOTICES.md', 'packaging/language-services.json', 'packaging/language-services-package/package.json',
          'packaging/language-services-package/package-lock.json', 'scripts/prepare-language-services.py']


def _archive_path(name: str, destination: Path, seen: set) -> Path:
    path = PurePosixPath(name.rstrip('/'))
    if not name or '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Unsafe archive path')
    key = '/'.join(path.parts).casefold()
    if key in seen:
        raise RuntimeError('Duplicate archive path')
    seen.add(key)
    target = destination.joinpath(*path.parts)
    if not target.resolve().is_relative_to(destination.resolve()):
        raise RuntimeError('Archive escapes staging directory')
    return target


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    seen = set()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                _archive_path(member.orig_filename, destination, seen)
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise RuntimeError('Archive links are not allowed')
            source.extractall(destination)
    else:
        with tarfile.open(archive) as source:
            for member in source.getmembers():
                _archive_path(member.name, destination, seen)
                if not (member.isfile() or member.isdir()):
                    raise RuntimeError('Archive links/devices are not allowed')
            source.extractall(destination, filter='data')


def download(asset: dict, cache: Path) -> Path:
    path = cache / asset['name']
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and digest(path) == asset['sha256']:
        return path
    print('Downloading ' + asset['name'], flush=True)
    temporary = path.with_name(path.name + '.download')
    with urllib.request.urlopen(asset['url'], timeout=120) as source, temporary.open('wb') as output:
        shutil.copyfileobj(source, output)
    if digest(temporary) != asset['sha256']:
        raise RuntimeError('SHA-256 mismatch: ' + asset['name'])
    temporary.replace(path)
    return path


def _build_path(path: Path) -> Path:
    resolved = path.resolve()
    build = (ROOT / 'build').resolve()
    if not resolved.is_relative_to(build) or resolved == build:
        raise RuntimeError('Language resources must stay inside repository build directory')
    current = path.absolute()
    while current != build and current.exists():
        info = current.lstat()
        if current.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RuntimeError('Build target must not be a reparse point')
        current = current.parent
    return resolved


def check(bundle: Path) -> dict:
    manifest = LanguageBundle(bundle).check()
    if set(manifest['inputs']) != set(INPUTS):
        raise RuntimeError('Language bundle input list is incomplete')
    for name, expected in manifest['inputs'].items():
        if digest(ROOT / name) != expected:
            raise RuntimeError('Language bundle input changed: ' + name)
    return manifest


def licenses(stage: Path, archives: dict[str, Path]) -> None:
    target = stage / 'licenses'; target.mkdir()
    shutil.copytree(stage / 'runtime/jdk/legal', target / 'temurin')
    for name in ('pyright', 'typescript', 'typescript-language-server'):
        folder = stage / 'servers/node/node_modules' / name
        for path in folder.iterdir():
            if path.is_file() and any(word in path.name.upper() for word in ('LICENSE', 'NOTICE', 'COPYING')):
                destination = target / name / path.name; destination.parent.mkdir(exist_ok=True)
                shutil.copy2(path, destination)
    for plugin in (stage / 'servers/jdtls/plugins').glob('*.jar'):
        with zipfile.ZipFile(plugin) as source:
            for name in source.namelist():
                if name == 'about.html' or name.startswith('about_files/') or PurePosixPath(name).name.upper().startswith(('LICENSE', 'NOTICE')):
                    if not name.endswith('/'):
                        destination = target / 'jdtls-bundles' / plugin.stem / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(source.read(name))
    for component, name in (('jdtls', 'jdtls-source.tar.gz'), ('node', 'node-source.tar.gz')):
        with tarfile.open(archives[name]) as source:
            for member in source.getmembers():
                if member.isfile() and len(PurePosixPath(member.name).parts) == 2 and PurePosixPath(member.name).name.upper().startswith(('LICENSE', 'NOTICE')):
                    destination = target / component / PurePosixPath(member.name).name
                    destination.parent.mkdir(exist_ok=True); destination.write_bytes(source.extractfile(member).read())
    shutil.copy2(ROOT / 'THIRD_PARTY_NOTICES.md', target / 'SONA_THIRD_PARTY_NOTICES.md')


def prepare(output: Path) -> dict:
    if os.name != 'nt':
        raise RuntimeError('The initial language-service bundle targets Windows x64')
    output = _build_path(output)
    inputs = json.loads((ROOT / 'packaging/language-services.json').read_text(encoding='utf-8'))
    if inputs['versions'] != VERSIONS:
        raise RuntimeError('Language component pins differ from runtime')
    cache = ROOT / 'build/language-services-cache'; cache.mkdir(parents=True, exist_ok=True)
    # Reuse the already verified Node archive, retaining an independent checksum.
    node_asset = inputs['archives']['node.zip']
    old_node = ROOT / 'build/openspec-cache/node-v22.23.3-win-x64.zip'
    if old_node.is_file() and digest(old_node) == node_asset['sha256']:
        shutil.copy2(old_node, cache / 'node.zip')
    archives = {name: download(asset, cache) for name, asset in inputs['archives'].items()}
    stage = _build_path(output.with_name(output.name + '.stage-' + uuid.uuid4().hex))
    stage.mkdir()
    try:
        extract(archives['node.zip'], stage / 'unpack-node')
        node_home = next((stage / 'unpack-node').iterdir())
        runtime = stage / 'runtime'; runtime.mkdir()
        shutil.copy2(node_home / 'node.exe', runtime / 'node.exe')
        extract(archives['jdk.zip'], stage / 'unpack-jdk')
        shutil.move(str(next((stage / 'unpack-jdk').iterdir())), runtime / 'jdk')
        extract(archives['jdtls.tar.gz'], stage / 'servers/jdtls')
        package = stage / 'servers/node'; package.mkdir()
        for name in ('package.json', 'package-lock.json'):
            shutil.copy2(ROOT / 'packaging/language-services-package' / name, package / name)
        npm = node_home / 'node_modules/npm/bin/npm-cli.js'
        env = dict(os.environ, PATH=str(node_home) + os.pathsep + os.environ.get('PATH', ''))
        subprocess.run([str(node_home / 'node.exe'), str(npm), 'ci', '--ignore-scripts', '--no-audit', '--no-fund',
                        '--cache', str(ROOT / '.runtime/npm-language-cache')], cwd=package, env=env, check=True)
        sources = []; coverage = {}
        for name, component in inputs['source_components'].items():
            relative = 'sources/' + name; destination = stage / relative; destination.parent.mkdir(exist_ok=True)
            shutil.copy2(archives[name], destination)
            sources.append({'component': component, 'version': VERSIONS[component], 'path': relative,
                            'url': inputs['archives'][name]['url'], 'sha256': digest(destination)})
        for asset in inputs['eclipse_sources']:
            source = download({**asset, 'name': 'eclipse-sources/' + asset['name']}, cache)
            relative = 'sources/eclipse/' + asset['name']; destination = stage / relative
            destination.parent.mkdir(exist_ok=True); shutil.copy2(source, destination)
            coverage[asset['binary']] = relative
        for plugin in (stage / 'servers/jdtls/plugins').glob('org.eclipse.equinox.launcher.*.jar'):
            coverage[plugin.relative_to(stage).as_posix()] = 'sources/equinox-native-source.tar.gz'
        coverage['servers/jdtls/plugins/org.eclipse.m2e.workspace.cli_0.4.0.jar'] = 'sources/m2e-workspace-source.jar'
        licenses(stage, archives)
        source_manifest = {'sources': sources, 'coverage': coverage, 'eclipse': inputs['eclipse_sources']}
        (stage / 'sources/manifest.json').write_text(json.dumps(source_manifest, indent=2), encoding='utf-8')
        for path in (stage / 'unpack-node', stage / 'unpack-jdk'):
            shutil.rmtree(_build_path(path))
        entries = dict(ENTRIES)
        entries['jdtls'] = next((stage / 'servers/jdtls/plugins').glob('org.eclipse.equinox.launcher_*.jar')).relative_to(stage).as_posix()
        manifest = {'schema': 1, 'platform': 'windows-x64',
                    'components': {name: {'version': version, 'entry': entries[name]} for name, version in VERSIONS.items()},
                    'inputs': {name: digest(ROOT / name) for name in INPUTS},
                    'files': {path.relative_to(stage).as_posix(): digest(path) for path in sorted(stage.rglob('*')) if path.is_file()},
                    'sources': sources, 'source_coverage': coverage}
        (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        check(stage)
        backup = _build_path(output.with_name(output.name + '.previous-' + uuid.uuid4().hex))
        if output.exists():
            output.rename(backup)
        try:
            stage.rename(output)
        except BaseException:
            if backup.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(_build_path(backup))
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(_build_path(stage))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--prepare', action='store_true'); modes.add_argument('--check', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/language-services')
    options = parser.parse_args()
    try:
        manifest = prepare(options.output) if options.prepare else check(options.output)
        print(f"Language services verified: {len(manifest['files'])} files, six pinned components")
    except Exception as error:
        print(str(error), file=sys.stderr); raise SystemExit(1)


if __name__ == '__main__':
    main()
