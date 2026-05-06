# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

datas_qtwe_core, binaries_qtwe_core, hiddenimports_qtwe_core = collect_all('PySide6.QtWebEngineCore')
datas_qtwe_widgets, binaries_qtwe_widgets, hiddenimports_qtwe_widgets = collect_all('PySide6.QtWebEngineWidgets')

extra_datas = [('app_version.txt', '.')]
if os.path.exists('app_config.json'):
    extra_datas.append(('app_config.json', '.'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries_qtwe_core + binaries_qtwe_widgets,
    datas=extra_datas + datas_qtwe_core + datas_qtwe_widgets,
    hiddenimports=['PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore'] + hiddenimports_qtwe_core + hiddenimports_qtwe_widgets,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_qtwebengine.py'],
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
    name='AIBossWorkbench',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
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
    name='AIBossWorkbench',
)
