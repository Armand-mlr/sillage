# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
    ('web', 'web'),
    ('worker.py', '.'),
    ('audio.py', '.'),
    ('imports.py', '.'),
    ('import_worker.py', '.'),
    ('batches.py', '.')
]
binaries = []
hiddenimports = [
    'worker',
    'audio',
    'imports',
    'import_worker',
    'batches',
    'runpy',
    'tempfile',
    'fcntl'
]

# Collecte des dépendances (binaires FFmpeg, solveur JS yt_dlp_ejs, certificats)
for package in ['yt_dlp', 'yt_dlp_ejs', 'imageio_ffmpeg', 'certifi', 'mutagen', 'websockets']:
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

block_cipher = None

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='sillage-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='sillage-backend',
)