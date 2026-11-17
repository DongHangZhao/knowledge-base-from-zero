# -*- mode: python ; coding: utf-8 -*-
# 2026-11-17 跨平台修复：datas 改基于 spec 所在目录的相对路径。
# 原写死本机绝对路径（库主工作盘），Linux/macOS CI runner 上必然找不到文件而构建失败。
import os
_SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    ['kb_hub_server.py'],
    pathex=[],
    binaries=[],
    datas=[(os.path.join(_SPEC_DIR, 'web', 'index.html'), 'web'),
           (os.path.join(_SPEC_DIR, 'inventory.example.json'), '.')],
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
    name='kb-hub',
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
