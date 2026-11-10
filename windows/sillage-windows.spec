# -*- mode: python ; coding: utf-8 -*-
# Build Windows : pyinstaller windows/sillage-windows.spec (depuis la racine du projet, sur Windows)
import os
from PyInstaller.utils.hooks import collect_all

R = os.path.abspath(os.path.join(SPECPATH, '..'))
J = lambda *p: os.path.join(R, *p)

datas = [(J('web'), 'web'), (J('worker.py'), '.'), (J('audio.py'), '.'), (J('imports.py'), '.'),
         (J('import_worker.py'), '.'), (J('batches.py'), '.'), (J('server.py'), '.'),
         (J('windows', 'node', 'node.exe'), 'bin')]
binaries = []
hiddenimports = ['server', 'worker', 'audio', 'imports', 'import_worker', 'batches', 'runpy', 'msvcrt']
for package in ['yt_dlp', 'yt_dlp_ejs', 'imageio_ffmpeg', 'certifi', 'mutagen', 'websockets', 'webview']:
    d, b, h = collect_all(package)
    datas += d; binaries += b; hiddenimports += h

a = Analysis([J('windows', 'launcher.py')], pathex=[R], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=['tkinter'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [('X utf8_mode=1', None, 'OPTION')], exclude_binaries=True, name='Sillage',
          console=False, upx=False, icon=J('windows', 'Sillage.ico'))
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name='Sillage')
