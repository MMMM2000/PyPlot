# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


# Include data files from the ``plotting`` package so bundled executables can
# access resources like ``default_config.json`` via ``importlib.resources``.
plotting_datas = [(str(Path("plotting") / "default_config.json"), "plotting")]

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=plotting_datas,
    hiddenimports=[],
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
    name='launcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
