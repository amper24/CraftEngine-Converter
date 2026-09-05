# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: build a single self-contained EXE (windowed).

Bundles schemas/ and mappings/ into the executable and imports the whole
`converter` package so the GUI works out of the box.
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("converter") + ["yaml", "numpy"]

a = Analysis(
    ["run_gui.py"],
    pathex=[],
    binaries=[],
    datas=[("schemas", "schemas"), ("mappings", "mappings"), ("models", "models")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CraftEngineConverter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed GUI, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)