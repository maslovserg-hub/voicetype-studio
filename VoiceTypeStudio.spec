# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for VoiceType Studio.

Builds a ``--onedir`` bundle (~250-300 MB) under ``dist/VoiceTypeStudio/``.
Onefile would unpack 250 MB to a temp dir on every launch — slow, and the
tray icon would lag visibly. With onedir the cold start is < 5 sec.

Build:
    venv\\Scripts\\pyinstaller VoiceTypeStudio.spec

The model (~440 MB GigaAM v3_e2e_ctc) is NOT bundled — it lives in
``C:\\gigaam_cache\\`` and is downloaded on first ASR call. Same with
silero v4_ru (~50 MB) which lands under ``%APPDATA%\\VoiceTypeStudio\\silero``.
"""

from PyInstaller.utils.hooks import collect_all


datas = []
binaries = []
hiddenimports = [
    # pystray's Windows backend isn't auto-discovered.
    'pystray._win32',
    # Audio I/O backends.
    'sounddevice',
    'soundfile',
    # GigaAM dependencies it loads at runtime via importlib.
    'sentencepiece',
    'hydra._internal.utils',
    'hydra._internal.instantiate._internal.utils',
    # aiogram's filter machinery uses lazy imports.
    'magic_filter',
    'aiogram.fsm.storage.memory',
    'aiogram.client.session.aiohttp',
]


def _collect(name):
    d, b, h = collect_all(name)
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)


# ASR / DSP — torch + GigaAM + onnx are the bulk of the bundle size.
_collect('gigaam')
_collect('torch')
_collect('torchaudio')
_collect('onnxruntime')
_collect('hydra')
_collect('omegaconf')

# UI deps — customtkinter ships theme JSON + assets that must be carried.
_collect('customtkinter')
_collect('tkinterdnd2')

# Bot / async stack.
_collect('aiogram')
_collect('pydantic')
_collect('aiohttp')
_collect('aiofiles')

# Media.
_collect('pydub')
_collect('yt_dlp')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Other ML frameworks pulled in transitively but unused.
        'torchvision', 'torchtext', 'torchrec',
        # Heavy stdlib modules we never touch — saves a few MB.
        'lib2to3', 'ftplib', 'imaplib', 'smtplib',
        'poplib', 'nntplib', 'telnetlib',
        # GUI alternatives we don't want pulled in by accident.
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VoiceTypeStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,            # tray app — never show a console window
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
    upx=False,
    name='VoiceTypeStudio',
)
