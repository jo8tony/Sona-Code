# -*- mode: python ; coding: utf-8 -*-

import sys
import subprocess
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPEC).resolve().parent.parent
STATIC_DIR = ROOT / "sona_code" / "web" / "static"

datas = [(str(STATIC_DIR), "sona_code/web/static"),
         (str(ROOT / "sona_code/workspace/managed-shell.js"), "sona_code/workspace")]
hiddenimports = collect_submodules("uvicorn")
binaries = []

if sys.platform == "win32":
    subprocess.run([sys.executable, str(ROOT / 'scripts/prepare-native-sdk.py'), '--check'], check=True)
    sdk = ROOT / 'build/opencode-sdk'
    if not (sdk / 'manifest.json').is_file() or not (sdk / 'config.zip').is_file():
        raise SystemExit('Offline plugin SDK missing; run scripts/prepare-native-sdk.py')
    datas.append((str(sdk), 'opencode-sdk'))
    import winpty

    winpty_runtime_dir = Path(winpty.__file__).resolve().parent
    for runtime_name in ("OpenConsole.exe", "winpty-agent.exe"):
        runtime_path = winpty_runtime_dir / runtime_name
        if not runtime_path.is_file():
            raise SystemExit(f"pywinpty runtime helper missing: {runtime_path}")
        binaries.append((str(runtime_path), "winpty"))

a = Analysis(
    [str(ROOT / "sona_code" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sona-code-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
