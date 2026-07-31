# eq_config/eq_config.py
import os

# --- 追加: プロジェクトのルートディレクトリを取得 ---
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# --- 個人環境設定（自宅座標・SSDパス・音量等）は eq_config_local.py に分離 ---
# eq_config_local.py は .gitignore 対象（個人情報のためコミットしない）。
# 初回はテンプレート(eq_config_local.py.example)をコピーして作成するか、
# GUIの設定画面（config タブ）から保存すると自動生成される。
_LOCAL_DEFAULTS = {
    "SSD_MOUNT_POINT": "/mnt/eq_ssd",
    "HOME_LAT": 35.681236,
    "HOME_LON": 139.767125,
    "HOME_REGION_NAME": "(未設定: eq_config_local.py で自宅地域名を設定してください)",
    "HOME_GROUND_CORRECTION": 1.0,
    "MASTER_VOLUME": 100,
    "COUNTDOWN_VOLUME_BOOST": 5,
    "PLAY_SIMULATION_END_AUDIO": True,
}

try:
    from eq_config.eq_config_local import (
        SSD_MOUNT_POINT, HOME_LAT, HOME_LON, HOME_REGION_NAME,
        HOME_GROUND_CORRECTION, MASTER_VOLUME, COUNTDOWN_VOLUME_BOOST,
        PLAY_SIMULATION_END_AUDIO,
    )
except ImportError:
    SSD_MOUNT_POINT = _LOCAL_DEFAULTS["SSD_MOUNT_POINT"]
    HOME_LAT = _LOCAL_DEFAULTS["HOME_LAT"]
    HOME_LON = _LOCAL_DEFAULTS["HOME_LON"]
    HOME_REGION_NAME = _LOCAL_DEFAULTS["HOME_REGION_NAME"]
    HOME_GROUND_CORRECTION = _LOCAL_DEFAULTS["HOME_GROUND_CORRECTION"]
    MASTER_VOLUME = _LOCAL_DEFAULTS["MASTER_VOLUME"]
    COUNTDOWN_VOLUME_BOOST = _LOCAL_DEFAULTS["COUNTDOWN_VOLUME_BOOST"]
    PLAY_SIMULATION_END_AUDIO = _LOCAL_DEFAULTS["PLAY_SIMULATION_END_AUDIO"]

# --- 保存先自動切り替えの仕組み ---
SSD_BASE_DIR = os.path.join(SSD_MOUNT_POINT, "raspberrypi/alert_eq/eq_log")

def latlon_to_nied_pixel(lat: float, lon: float):
    """緯度経度 → NIEDリアルタイムモニタ画像(352×400px)のピクセル座標
    校正基準: 35.7°N, 140.0°E → (233.5, 258.5)
    地理範囲: 120.09°E〜150.1°E, 28.6°N〜48.6°N
    """
    x = (lon - 120.09) * 11.73
    y = (48.625 - lat) * 20.0
    x = max(0.0, min(351.0, x))
    y = max(0.0, min(399.0, y))
    return round(x, 1), round(y, 1)

# home position of kyoshin monitor pic（HOME_LAT/LON から自動算出）
NIED_HOME_X, NIED_HOME_Y = latlon_to_nied_pixel(HOME_LAT, HOME_LON)

# Filtering Settings
# 自宅周辺で地震警報を出すべき震央コードのリスト（予報時に使用。詳細は eq_config/README.md 参照）
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

def get_wolfx_log_dir() -> str:
    """WolfxのJSONログ保存先（SSD優先、SDフォールバック）"""
    ssd_path = os.path.join(SSD_BASE_DIR, "eq_eew_jma")
    sd_path = os.path.join(PROJECT_ROOT, "eq_log", "eq_eew_jma")
    
    # SSDがマウントされてアクセス可能かチェック
    if os.path.exists(SSD_MOUNT_POINT):
        os.makedirs(ssd_path, exist_ok=True)
        return ssd_path
    else:
        os.makedirs(sd_path, exist_ok=True)
        return sd_path

def get_monitor_base_dir() -> str:
    """NIED画像の保存先ベース（SSD優先、SDフォールバック）"""
    ssd_path = os.path.join(SSD_BASE_DIR, "monitor_images")
    sd_path = os.path.join(PROJECT_ROOT, "eq_log", "monitor_images")
    
    # SSDがマウントされてアクセス可能かチェック
    if os.path.exists(SSD_MOUNT_POINT):
        os.makedirs(ssd_path, exist_ok=True)
        return ssd_path
    else:
        os.makedirs(sd_path, exist_ok=True)
        return sd_path

# --- config,setting file path ---
COLOR_MAP_PATH = os.path.join(PROJECT_ROOT, "eq_assets", "eq_NiedColorMap.json")
# 固定パスだったものを関数での動的取得に変更
MONITOR_IMAGE_DIR = get_monitor_base_dir()

_LOCAL_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "eq_config_local.py")

def _write_local_config():
    """現在の値(グローバル変数)を eq_config_local.py (git管理外)へ書き出す"""
    fc = (
        "# eq_config/eq_config_local.py\n"
        "# 個人環境設定（自宅座標・SSDパス・音量等）。.gitignore 対象、コミットしないこと。\n"
        f"SSD_MOUNT_POINT = {SSD_MOUNT_POINT!r}\n"
        f"HOME_LAT = {HOME_LAT}\n"
        f"HOME_LON = {HOME_LON}\n"
        f"HOME_REGION_NAME = {HOME_REGION_NAME!r}\n"
        f"HOME_GROUND_CORRECTION = {HOME_GROUND_CORRECTION}\n"
        f"MASTER_VOLUME = {MASTER_VOLUME}\n"
        f"COUNTDOWN_VOLUME_BOOST = {COUNTDOWN_VOLUME_BOOST}\n"
        f"PLAY_SIMULATION_END_AUDIO = {PLAY_SIMULATION_END_AUDIO}\n"
    )
    with open(_LOCAL_CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write(fc)

def save_user_home(lat, lon, region, ground_corr, nied_x=None, nied_y=None):
    """自宅座標を eq_config_local.py へ保存（NIEDピクセル座標は HOME_LAT/LON から自動算出するため引数は無視）"""
    global HOME_LAT, HOME_LON, HOME_REGION_NAME, HOME_GROUND_CORRECTION, NIED_HOME_X, NIED_HOME_Y
    HOME_LAT = lat
    HOME_LON = lon
    HOME_REGION_NAME = region
    HOME_GROUND_CORRECTION = ground_corr
    NIED_HOME_X, NIED_HOME_Y = latlon_to_nied_pixel(lat, lon)
    _write_local_config()

def save_audio_prefs(master_volume, countdown_boost, play_sim_end=True):
    global MASTER_VOLUME, COUNTDOWN_VOLUME_BOOST, PLAY_SIMULATION_END_AUDIO
    MASTER_VOLUME = master_volume
    COUNTDOWN_VOLUME_BOOST = countdown_boost
    PLAY_SIMULATION_END_AUDIO = play_sim_end
    _write_local_config()
