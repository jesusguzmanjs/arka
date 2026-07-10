# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe for the CueGrid Python core sidecar (v1.9).

Freezes ``src/cuegrid/cli.py`` into a single-file, standalone Windows
executable that Tauri packages as an ``externalBin`` sidecar under the
``cuegrid-core`` identifier. See ``../.openspec/3-gui-spec.md`` §6.2 and
``../README.md`` ("Packaging the sidecar") for the full pipeline this
build step feeds into.

Build with:

    cd core
    pyinstaller --clean cuegrid.spec

Output: ``dist/cuegrid-core.exe``. The orchestration scripts at the repo
root (``build-win.ps1`` / ``sync.ps1``) move and rename this artifact into
``gui/src-tauri/binaries/cuegrid-core-<target-triple>.exe``, per Tauri's
sidecar naming contract (Rust target-triple suffix required).
"""

from PyInstaller.utils.hooks import collect_submodules

# librosa and audioread both perform environment-dependent conditional
# imports (audio backend probing at runtime, optional accelerated code
# paths) that PyInstaller's static import-graph analysis of cli.py alone
# cannot always see. Pulling in every submodule of these packages -- plus
# our own `cuegrid` package, defensively, in case any internal module ever
# imports another one dynamically -- avoids "ModuleNotFoundError" surprises
# inside the frozen executable that would otherwise only surface at
# runtime, on an end user's machine, long after the build succeeded.
hiddenimports = (
    collect_submodules("librosa")
    + collect_submodules("audioread")
    + collect_submodules("soundfile")
    + collect_submodules("cuegrid")
)

a = Analysis(
    ['src\\cuegrid\\cli.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
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
    name='cuegrid-core',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
