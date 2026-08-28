# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build recipe for the CueGrid Python core resource (v2.0).

Freezes ``cuegrid/cli.py`` into an uncompressed directory structure (--onedir)
that Tauri packages inside its ``resources`` bundle under the ``resources/cuegrid-core``
path. This eliminates runtime extraction latency (0ms cold start).
See ``../.openspec/3-gui-spec.md`` and ``../README.md`` for the full pipeline.

Build with:

    cd core
    pyinstaller --clean cuegrid.spec

Output: ``dist/cuegrid-core/`` folder containing ``cuegrid-core.exe`` and its DLLs.
The orchestration scripts at the repo root (``sync.ps1``) move this entire folder
into ``gui/src-tauri/resources/cuegrid-core``.
"""

from PyInstaller.utils.hooks import collect_submodules

# librosa and audioread both perform environment-dependent conditional
# imports (audio backend probing at runtime, optional accelerated code
# paths) that PyInstaller's static import-graph analysis of cli.py alone
# cannot always see. CueGrid's own modules are left to that static graph: a
# broad ``collect_submodules("cuegrid")`` would package dormant legacy code.
hiddenimports = (
    collect_submodules("librosa")
    + collect_submodules("audioread")
    + collect_submodules("soundfile")
)

a = Analysis(
    ['cuegrid\\cli.py'],
    pathex=['cuegrid'],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Legacy Stem helpers intentionally remain in source control but must
    # never be frozen into the production sidecar or pull in ffmpeg-python.
    excludes=[
        "cuegrid.audio.legacy_stems",
        "cuegrid.nml.stems",
        "ffmpeg",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],  # <--- Limpiamos las binarios de aquí adentro
    exclude_binaries=True,  # <--- CRUCIAL: Indica a PyInstaller que los binarios van fuera, en la carpeta
    name='cuegrid-core',
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

# EL BLOQUE MAESTRO: COLLECT es el que junta el EXE con todas sus dependencias (.dll, etc.)
# y crea la carpeta estructurada 'cuegrid-core' dentro de dist/
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='cuegrid-core',  # Esto define el nombre de la carpeta de salida
)
