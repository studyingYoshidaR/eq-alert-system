# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec file for Earthquake Alert System (Windows)
# Build: pyinstaller eq_app.spec
#        (or run build_exe.bat)
#
import os
from pathlib import Path

block_cipher = None
BASE = os.path.abspath(os.path.dirname(SPEC))          # eq_app4win/
SRC  = os.path.join(BASE, 'eq_src')
TEST = os.path.join(BASE, 'eq_test')

# ── conda Library/bin から不足 DLL を明示的に追加 ────────────────────────────
import sys as _sys
_CONDA_BIN = os.path.join(os.path.dirname(_sys.executable), 'Library', 'bin')
_extra_dlls = [
    'tcl86t.dll', 'tk86t.dll',          # tkinter
    'libexpat.dll',                       # pyexpat (xml/plistlib)
    'liblzma.dll',                        # _lzma
    'LIBBZ2.dll',                         # _bz2
    'libmpdec-4.dll',                     # _decimal
    'libcrypto-3-x64.dll',               # _hashlib / _ssl
    'libssl-3-x64.dll',                   # _ssl
    'ffi.dll',                            # _ctypes
]
_tcl_tk_binaries = [
    (os.path.join(_CONDA_BIN, _dll), '.')
    for _dll in _extra_dlls
    if os.path.isfile(os.path.join(_CONDA_BIN, _dll))
]

# ── データファイル (src_path, dest_in_bundle) ────────────────────────────────
datas = []

if os.path.isdir(os.path.join(BASE, 'eq_assets')):
    datas.append((os.path.join(BASE, 'eq_assets'), 'eq_assets'))

if os.path.isfile(os.path.join(BASE, 'eq_config', 'eq_settings.json')):
    datas.append((os.path.join(BASE, 'eq_config', 'eq_settings.json'), 'eq_config'))

if os.path.isdir(os.path.join(TEST, 'test_eq_log')):
    datas.append((os.path.join(TEST, 'test_eq_log'), 'eq_test/test_eq_log'))

if os.path.isdir(os.path.join(TEST, 'test_monitor_images')):
    datas.append((os.path.join(TEST, 'test_monitor_images'), 'eq_test/test_monitor_images'))

# ── ffmpeg 同梱 ──────────────────────────────────────────────────────────────
# eq_app4win/ffmpeg/ に ffmpeg.exe (+ 必要なら付随DLL) を置くと自動で同梱し、
# バンドル内 _internal/ffmpeg/ffmpeg.exe として展開される。
# 静的(単一ファイル)ビルドの ffmpeg.exe を推奨。無ければ MP4 生成のみスキップされる。
_FFMPEG_DIR = os.path.join(BASE, 'ffmpeg')
if os.path.isfile(os.path.join(_FFMPEG_DIR, 'ffmpeg.exe')):
    datas.append((_FFMPEG_DIR, 'ffmpeg'))
    print('[spec] bundling ffmpeg from', _FFMPEG_DIR)
else:
    print('[spec] WARNING: eq_app4win/ffmpeg/ffmpeg.exe が無いため ffmpeg は同梱されません（MP4生成不可）')

# ── アナリシス ───────────────────────────────────────────────────────────────
a = Analysis(
    [os.path.join(SRC, 'eq_app.py')],
    pathex=[BASE, SRC],
    binaries=_tcl_tk_binaries,
    datas=datas,
    hiddenimports=[
        'pygame',
        'pygame.mixer',
        'PIL._tkinter_finder',
        'websocket',
        'websocket._abnf',
        'websocket._app',
        'websocket._core',
        'websocket._exceptions',
        'websocket._handshake',
        'websocket._http',
        'websocket._logging',
        'websocket._socket',
        'websocket._ssl_compat',
        'websocket._url',
        'websocket._utils',
        'numpy',
        'json',
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'tkinter.filedialog',
        'eq_config.eq_config',
        'eq_main',
        'eq_monitor_nied',
        'eq_eew_wolfx',
        'eq_visualizer',
        'eq_data',
        'eq_utils',
        'eq_timeline',
        'eq_ring_monitor',
        'eq_site_calibration',
        'eq_monitor_card',
        'util_audio_player',
        'eq_test.eq_run_simulation2',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'pandas', 'IPython'],
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
    name='EqAlertSystem',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,              # ウィンドウアプリ (コンソール非表示)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(BASE, 'eq_assets', 'app_icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='EqAlertSystem',
)
