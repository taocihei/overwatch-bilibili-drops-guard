# -*- mode: python ; coding: utf-8 -*-
import sys
from importlib.util import find_spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

required_packages = ('requests', 'selenium', 'PIL', 'qrcode', 'tkinter', '_tkinter', 'wasmtime')
missing_packages = [name for name in required_packages if find_spec(name) is None]
if missing_packages:
    raise RuntimeError(
        'Missing required build packages: '
        + ', '.join(missing_packages)
        + '. Run: python -m pip install -r requirements.txt'
    )

python_root = Path(sys.base_prefix)
tcl_library = python_root / 'tcl' / 'tcl8.6'
tk_library = python_root / 'tcl' / 'tk8.6'
tkinter_lib = python_root / 'Lib' / 'tkinter'
tkinter_pyd = python_root / 'DLLs' / '_tkinter.pyd'
tcl_dll = python_root / 'DLLs' / 'tcl86t.dll'
tk_dll = python_root / 'DLLs' / 'tk86t.dll'

required_tk_resources = (
    tcl_library,
    tk_library,
    tkinter_lib,
    tkinter_pyd,
    tcl_dll,
    tk_dll,
)
missing_tk_resources = [str(path) for path in required_tk_resources if not path.exists()]
if missing_tk_resources:
    raise RuntimeError(
        'Missing required Tcl/Tk packaging resources: '
        + ', '.join(missing_tk_resources)
        + '. Install a complete Python distribution with Tcl/Tk support.'
    )

datas = [
    ('assets/app.ico', 'assets'),
    ('assets/live_watch_skynet.wasm', 'assets'),
]
binaries = []
hiddenimports = [
    '_tkinter',
    'qrcode.image.pil',
    # cookie_capture 按浏览器类型动态加载这两个模块，静态分析无法发现。
    'selenium.webdriver.chrome.webdriver',
    'selenium.webdriver.edge.webdriver',
]
for source, target in (
    (tcl_library, '_tcl_data'),
    (tk_library, '_tk_data'),
    (tkinter_lib, 'tkinter'),
):
    datas.append((str(source), target))
for source, target in (
    (tkinter_pyd, '.'),
    (tcl_dll, '.'),
    (tk_dll, '.'),
):
    binaries.append((str(source), target))
# Selenium 与 Pillow 使用 PyInstaller 官方 hook 即可。collect_all 会把未使用
# 的 Firefox/Safari/IE 驱动、Pillow 全部图片插件，甚至 pytest/pygments 一并
# 塞进 one-file 包；每次启动都要解压这些内容，既增大约几十 MB，也拖慢首屏。
tmp_ret = collect_all('wasmtime')
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
    # Pillow 的 hook 会自动带入可选 numpy 支持；本项目只做图标和
    # 二维码的基本绘制，不使用 numpy。排除后可减少 one-file 解包量。
    excludes=['numpy'],
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
    version='assets\\windows_version_info.txt',
    manifest='assets\\windows_app.manifest',
)
