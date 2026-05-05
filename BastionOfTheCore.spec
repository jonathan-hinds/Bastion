# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH)

datas = [
    (str(ROOT / "bastion" / "data"), "bastion/data"),
    (str(ROOT / "Sounds"), "Sounds"),
    (str(ROOT / "Sprites" / "Sprites"), "Sprites/Sprites"),
    (str(ROOT / "Sprites" / "Enemies" / "Sprites"), "Sprites/Enemies/Sprites"),
    (str(ROOT / "Sprites" / "Enemies" / "Bosses"), "Sprites/Enemies/Bosses"),
]


a = Analysis(
    ["run_game.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
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
    [],
    exclude_binaries=True,
    name="BastionOfTheCore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="BastionOfTheCore",
)
