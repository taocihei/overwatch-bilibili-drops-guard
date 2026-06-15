# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

python_root = Path(sys.base_prefix)
tcl_library = python_root / 'tcl' / 'tcl8.6'
tk_library = python_root / 'tcl' / 'tk8.6'
tkinter_lib = python_root / 'Lib' / 'tkinter'
tkinter_pyd = python_root / 'DLLs' / '_tkinter.pyd'
tcl_dll = python_root / 'DLLs' / 'tcl86t.dll'
tk_dll = python_root / 'DLLs' / 'tk86t.dll'

datas = [('assets/app.ico', 'assets')]
binaries = []
hiddenimports = ['_tkinter']
for source, target in (
    (tcl_library, '_tcl_data'),
    (tk_library, '_tk_data'),
    (tkinter_lib, 'tkinter'),
):
    if source.exists():
        datas.append((str(source), target))
for source, target in (
    (tkinter_pyd, '.'),
    (tcl_dll, '.'),
    (tk_dll, '.'),
):
    if source.exists():
        binaries.append((str(source), target))
tmp_ret = collect_all('selenium')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app.py'],
    pathex=[],
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
    name='OverwatchBiliDrops',
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
    icon=['assets\\app.ico'],
)
