"""Prepare pinned OpenCode plugin dependencies for offline first startup."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'packaging/opencode-sdk'
BUNDLE = ROOT / 'build/opencode-sdk'


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check() -> bool:
    manifest_path = BUNDLE / 'manifest.json'
    if not manifest_path.is_file() or not (BUNDLE / 'config.zip').is_file():
        return False
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    version = json.loads((ROOT / 'packaging/opencode.json').read_text(encoding='utf-8'))['version']
    return (manifest.get('version') == version
            and manifest.get('archive_sha256') == digest(BUNDLE / 'config.zip')
            and manifest.get('inputs') == {name: digest(SOURCE / name) for name in ('package.json', 'package-lock.json')})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    options = parser.parse_args()
    if check():
        print('Verified bundled OpenCode plugin SDK', flush=True)
        return
    if options.check:
        raise RuntimeError('OpenCode plugin SDK missing or stale; run scripts/prepare-native-sdk.py')
    version = json.loads((ROOT / 'packaging/opencode.json').read_text(encoding='utf-8'))['version']
    package = json.loads((SOURCE / 'package.json').read_text(encoding='utf-8'))
    if package['dependencies']['@opencode-ai/plugin'] != version:
        raise RuntimeError('Plugin SDK must match the pinned native OpenCode version')
    work = ROOT / 'build/native-sdk-package'
    work.mkdir(parents=True, exist_ok=True)
    for name in ('package.json', 'package-lock.json'):
        shutil.copy2(SOURCE / name, work / name)
    npm = shutil.which('npm.cmd') or shutil.which('npm')
    if not npm:
        raise RuntimeError('npm is required to prepare plugin dependencies')
    subprocess.run([npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], cwd=work, check=True)
    BUNDLE.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BUNDLE / 'config.zip', 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in ('package.json', 'package-lock.json'):
            archive.write(work / name, name)
        for path in sorted((work / 'node_modules').rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(work).as_posix())
    manifest = {'version': version, 'archive_sha256': digest(BUNDLE / 'config.zip'),
                'inputs': {name: digest(SOURCE / name) for name in ('package.json', 'package-lock.json')}}
    (BUNDLE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    if not check():
        raise RuntimeError('Prepared plugin SDK failed validation')
    print('Prepared offline OpenCode plugin SDK', flush=True)


if __name__ == '__main__':
    main()
