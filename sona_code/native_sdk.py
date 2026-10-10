"""Prepare the bundled plugin SDK for a fresh isolated OpenCode config."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath

_LOCK = threading.Lock()


def _linked(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def seed_config(destination: Path, bundle: Path) -> None:
    """Leave existing dependency setups untouched; never copy user configuration."""
    with _LOCK:
        if any(os.path.lexists(destination / name) for name in ('package.json', 'package-lock.json', 'node_modules')):
            return
        targets = [destination, destination.parent, *(destination / name for name in ('package.json', 'package-lock.json', 'node_modules'))]
        if any(_linked(path) for path in targets):
            raise RuntimeError('Linked OpenCode SDK destination is not supported')
        if destination.resolve().parent != destination.parent.resolve():
            raise RuntimeError('OpenCode SDK destination escapes its config root')
        archive = bundle / 'config.zip'
        manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        if checksum != manifest.get('archive_sha256'):
            raise RuntimeError('Bundled OpenCode plugin SDK checksum mismatch')
        with zipfile.ZipFile(archive) as source:
            names = source.namelist()
            required = {'package.json', 'package-lock.json', 'node_modules/@opencode-ai/plugin/package.json'}
            if not required.issubset(names):
                raise RuntimeError('Bundled OpenCode plugin SDK is incomplete')
            for member in source.infolist():
                name = member.filename
                parts = PurePosixPath(name).parts
                if ('\\' in name or ':' in name or PurePosixPath(name).is_absolute() or '..' in parts
                        or not parts or parts[0] not in {'package.json', 'package-lock.json', 'node_modules'}
                        or stat.S_ISLNK(member.external_attr >> 16)):
                    raise RuntimeError(f'Invalid OpenCode SDK archive path: {name}')
            plugin = json.loads(source.read('node_modules/@opencode-ai/plugin/package.json'))
            if plugin.get('version') != manifest.get('version'):
                raise RuntimeError('Bundled OpenCode plugin SDK version mismatch')
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.sona-sdk-', dir=destination.parent) as staging:
                source.extractall(staging)
                destination.mkdir(exist_ok=True)
                # Commit package.json last so native startup sees the complete seed.
                for name in ('node_modules', 'package-lock.json', 'package.json'):
                    shutil.move(str(Path(staging) / name), str(destination / name))


def prepare_bundled_sdk(env: dict[str, str], project: str | None = None) -> None:
    root = os.environ.get('XDG_CONFIG_HOME')
    if not root or env.get('XDG_CONFIG_HOME') != root or env.get('OPENCODE_CONFIG_DIR'):
        return  # Explicit user directory overrides own their dependency setup.
    bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1] / 'build')) / 'opencode-sdk'
    if bundle.is_dir():
        seed_config(Path(root) / 'opencode', bundle)
        if project:
            directory = Path(project) / '.opencode'
            if directory.is_dir():
                seed_config(directory, bundle)
    elif getattr(sys, 'frozen', False):
        raise RuntimeError('Installed OpenCode plugin SDK missing; reinstall the complete application')
