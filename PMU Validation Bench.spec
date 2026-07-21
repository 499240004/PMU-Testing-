# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.hooks import copy_metadata

datas = [('pmu_validation\\calibration.json', '.')]
binaries = []
hiddenimports = ['mso8104a', 'measurements', 'hp3325b_driver', 'serial.tools.list_ports']
datas += copy_metadata('pyvisa')
datas += copy_metadata('pyvisa_py')
hiddenimports += collect_submodules('hp34401')
hiddenimports += collect_submodules('upmu')
hiddenimports += collect_submodules('pmu_validation')
hiddenimports += collect_submodules('serial')
hiddenimports += collect_submodules('pyvisa')
tmp_ret = collect_all('pyvisa_py')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['pmu_gui.py'],
    pathex=['apps\\scope', 'apps\\hp34401', 'apps\\hp3325', 'apps\\power-brick\\host'],
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
    [],
    exclude_binaries=True,
    name='PMU Validation Bench',
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
    name='PMU Validation Bench',
)
