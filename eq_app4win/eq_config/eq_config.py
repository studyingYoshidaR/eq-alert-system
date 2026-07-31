# eq_config/eq_config.py  (Windows version)
import os
import json as _json

import sys as _sys
if getattr(_sys, 'frozen', False):
    PROJECT_ROOT = _sys._MEIPASS
else:
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# --- ログ・データ保存先: %LOCALAPPDATA%\alert_eq ---
APPDATA_DIR  = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'alert_eq')
LOG_BASE_DIR = os.path.join(APPDATA_DIR, 'eq_log')
LOG_DIR      = os.path.join(APPDATA_DIR, 'logs')  # システムログ (system.log)

# ラズパイ版のSSDログ保存先。Windows版では保存先にAppDataを使うため未使用だが、
# eq_app.py / eq_run_simulation2.py が過去ログ検索パスの組み立てで参照するため定義する
# (Windowsには存在しないパスなので os.path.exists で弾かれ無害)
SSD_MOUNT_POINT = "/mnt/eq_ssd"

# --- user env. setting (初期デフォルト値) ---
# 実際のユーザー設定は %LOCALAPPDATA%\alert_eq\user_home.json に保存され、
# 起動時に _load_user_home() が下記の値を上書きする(初回起動時のみこの値が使われる)。
# 個人情報を含めないため、ここには汎用のプレースホルダ値(東京駅)を置く。
# home geographic coordinates
HOME_LAT = 35.681236
HOME_LON = 139.767125

# home position of kyoshin monitor pic (HOME_LAT/LON に対応するピクセル座標)
NIED_HOME_X = 230.8
NIED_HOME_Y = 258.9

HOME_REGION_NAME = "(未設定)"

# 震度フォールバック計算の地盤補正値（勝俣式の +α）
# 1.0 = 標準地盤  2.0 = 軟弱地盤（関東平野沖積低地など）推奨
HOME_GROUND_CORRECTION = 1.0

# マスター音量 (0-100)
MASTER_VOLUME = 90

# カウントダウン単体音声（5,4,3秒）の音量ブースト（MASTER_VOLUME への加算値）
COUNTDOWN_VOLUME_BOOST = 5

# シミュレーション終了時音声の再生
PLAY_SIMULATION_END_AUDIO = True

# Filtering Settings
# 自宅周辺で地震警報を出すべき震央コードのリスト（予報時に使用）
TARGET_HYPOCENTER_CODES = [
    # 1. 千葉・茨城・東京・埼玉（直下・隣接）
    300, 301, 309, 310, 311, 320, 321, 330, 331, 332, 340, 341, 342, 350, 351, 352, 360, 361, 
    # 2. 近海・プレート境界（東部・南部からの揺れ）
    349, 471, 472, 473, 475, 476, 477, 478, 480, 481,
    # 3. 東北（北部からの強い揺れ）
    200, 201, 202, 203, 210, 211, 212, 213, 220, 221, 222, 230, 231,232, 233, 240, 241,242, 243, 250, 251, 252,
    281, 282, 283, 284, 285, 286, 287, 288, 289,
    # 4. 東海・甲信越（南部・西部からの揺れ）
    485, 486, 487, 469,
    370, 372, 378, 379, 380, 381, 390, 391, 400, 401, 411 ,412, 420, 421, 422, 440, 441, 442, 443, 495, 498,
    # 5. 伊豆諸島・小笠原（異常震域・遠方）
    482, 483, 911, 915, 916, 918
]

USER_HOME_PATH = os.path.join(APPDATA_DIR, 'user_home.json')

def _load_user_home():
    global HOME_LAT, HOME_LON, HOME_REGION_NAME, HOME_GROUND_CORRECTION, NIED_HOME_X, NIED_HOME_Y
    global MASTER_VOLUME, COUNTDOWN_VOLUME_BOOST, PLAY_SIMULATION_END_AUDIO
    try:
        with open(USER_HOME_PATH, 'r', encoding='utf-8') as _f:
            _d = _json.load(_f)
        HOME_LAT               = float(_d.get("HOME_LAT",               HOME_LAT))
        HOME_LON               = float(_d.get("HOME_LON",               HOME_LON))
        HOME_REGION_NAME       =   str(_d.get("HOME_REGION_NAME",       HOME_REGION_NAME))
        HOME_GROUND_CORRECTION = float(_d.get("HOME_GROUND_CORRECTION", HOME_GROUND_CORRECTION))
        NIED_HOME_X            = float(_d.get("NIED_HOME_X",            NIED_HOME_X))
        NIED_HOME_Y            = float(_d.get("NIED_HOME_Y",            NIED_HOME_Y))
        MASTER_VOLUME          =   int(_d.get("MASTER_VOLUME",          MASTER_VOLUME))
        COUNTDOWN_VOLUME_BOOST =   int(_d.get("COUNTDOWN_VOLUME_BOOST", COUNTDOWN_VOLUME_BOOST))
        PLAY_SIMULATION_END_AUDIO = bool(_d.get("PLAY_SIMULATION_END_AUDIO", PLAY_SIMULATION_END_AUDIO))
    except (FileNotFoundError, _json.JSONDecodeError, KeyError, TypeError):
        pass

_load_user_home()

def _read_user_home_dict() -> dict:
    """user_home.json を読んで dict を返す（存在しない場合は空 dict）"""
    try:
        with open(USER_HOME_PATH, 'r', encoding='utf-8') as _f:
            return _json.load(_f)
    except (FileNotFoundError, _json.JSONDecodeError):
        return {}

def _write_user_home_dict(d: dict):
    os.makedirs(APPDATA_DIR, exist_ok=True)
    with open(USER_HOME_PATH, 'w', encoding='utf-8') as _f:
        _json.dump(d, _f, indent=2, ensure_ascii=False)

def save_user_home(lat, lon, region, ground_corr, nied_x, nied_y):
    """HOME座標設定をメモリとAppData/user_home.jsonの両方に保存する"""
    global HOME_LAT, HOME_LON, HOME_REGION_NAME, HOME_GROUND_CORRECTION, NIED_HOME_X, NIED_HOME_Y
    HOME_LAT               = float(lat)
    HOME_LON               = float(lon)
    HOME_REGION_NAME       = str(region)
    HOME_GROUND_CORRECTION = float(ground_corr)
    NIED_HOME_X            = float(nied_x)
    NIED_HOME_Y            = float(nied_y)
    d = _read_user_home_dict()
    d.update({
        "HOME_LAT": HOME_LAT, "HOME_LON": HOME_LON,
        "HOME_REGION_NAME": HOME_REGION_NAME,
        "HOME_GROUND_CORRECTION": HOME_GROUND_CORRECTION,
        "NIED_HOME_X": NIED_HOME_X, "NIED_HOME_Y": NIED_HOME_Y,
    })
    _write_user_home_dict(d)

def save_audio_prefs(master_volume: int, countdown_boost: int, play_sim_end: bool = True):
    """音量設定をメモリとAppData/user_home.jsonの両方に保存する"""
    global MASTER_VOLUME, COUNTDOWN_VOLUME_BOOST, PLAY_SIMULATION_END_AUDIO
    MASTER_VOLUME             = int(max(0, min(100, master_volume)))
    COUNTDOWN_VOLUME_BOOST    = int(max(0, min(50,  countdown_boost)))
    PLAY_SIMULATION_END_AUDIO = bool(play_sim_end)
    d = _read_user_home_dict()
    d.update({
        "MASTER_VOLUME":             MASTER_VOLUME,
        "COUNTDOWN_VOLUME_BOOST":    COUNTDOWN_VOLUME_BOOST,
        "PLAY_SIMULATION_END_AUDIO": PLAY_SIMULATION_END_AUDIO,
    })
    _write_user_home_dict(d)

def latlon_to_nied_pixel(lat: float, lon: float):
    """緯度経度 → NIEDリアルタイムモニタ画像(352×400px)のピクセル座標
    校正基準: 35.7°N, 140.0°E → (233.5, 258.5)
    """
    x = (lon - 120.09) * 11.73
    y = (48.625 - lat) * 20.0
    x = max(0.0, min(351.0, x))
    y = max(0.0, min(399.0, y))
    return round(x, 1), round(y, 1)

def get_wolfx_log_dir() -> str:
    """EEW JSONログ保存先"""
    path = os.path.join(LOG_BASE_DIR, "eq_eew_jma")
    os.makedirs(path, exist_ok=True)
    return path

def get_monitor_base_dir() -> str:
    """NIED画像保存先ベースディレクトリ"""
    path = os.path.join(LOG_BASE_DIR, "monitor_images")
    os.makedirs(path, exist_ok=True)
    return path

# --- 固定パス ---
COLOR_MAP_PATH    = os.path.join(PROJECT_ROOT, "eq_assets", "eq_NiedColorMap.json")
MONITOR_IMAGE_DIR = get_monitor_base_dir()
