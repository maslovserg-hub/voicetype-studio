# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the lightweight launcher.

Produces a single-file ``VoiceTypeStudio-Setup.exe`` (~5 MB) that bootstraps
the full app from GitHub Releases on first run.

Build:
    venv\\Scripts\\pyinstaller launcher\\Launcher.spec
"""

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        # Bundled robot icon for the installer window. Same .ico the
        # main app uses, so the installer looks like the app it installs.
        ('../assets/icon.ico', 'assets'),
        ('../assets/icon_64.png', 'assets'),
    ],
    hiddenimports=[
        # IShellLinkW shortcut creation: WScript.Shell mangles Cyrillic
        # paths, so we go straight at the Unicode COM interface via
        # pywin32. These modules aren't picked up automatically because
        # ``win32com.shell`` uses dynamic imports.
        'win32com.shell',
        'win32com.shell.shell',
        'pythoncom',
        'pywintypes',
    ],
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
    name='VoiceTypeStudio-Setup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='../assets/icon.ico',
)
