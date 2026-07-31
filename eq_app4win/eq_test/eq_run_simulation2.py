import sys
import os
import json
import time
import glob
import re
import datetime
import unicodedata
import threading
import math
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from PIL import Image
import numpy as np

# --- パス設定 ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(PROJECT_ROOT, "eq_src")
SIM_VOICE_DIR = os.path.join(PROJECT_ROOT, "eq_assets", "voice", "simulation")

# NIEDモニタは取得タイムスタンプ(T+0.13)より約1.13秒前のデータ(T-1)のURLをフェッチするため、
# ファイル名に記録されるのはデータ時刻(T-1)。実際に画像が表示されるのはT+0.13なので
# 実質レイテンシは約1.2秒。シミュレーションでこれを再現する。
NIED_IMAGE_LATENCY_SEC = 1.2
# util_audio_player.py は PROJECT_ROOT (eq_app4win/) 直下に配置
AUDIO_UTIL_DIR = PROJECT_ROOT

sys.path.append(PROJECT_ROOT)
sys.path.append(SRC_DIR)
sys.path.append(AUDIO_UTIL_DIR)

# --- モジュールインポート ---
try:
    from eq_main import EqSystem
    from eq_data import JmaEqData, EqSource, EqType
    from eq_utils import GeoUtils
    import eq_config.eq_config as eq_config
except ImportError as e:
    print(f"[Error] Failed to import core modules: {e}")
    sys.exit(1)

try:
    import util_audio_player
except ImportError as e:
    print(f"[Warning] util_audio_player.py not found: {e}. Audio playback will be mocked.")
    class MockAudioPlayer:
        @staticmethod
        def play_audio(*args, **kwargs): pass
        @staticmethod
        def play_audio_list(*args, **kwargs): pass
        @staticmethod
        def stop_audio(*args, **kwargs): pass
    util_audio_player = MockAudioPlayer()

SCENARIO_FILE = os.path.join(CURRENT_DIR, "eq_sim_scenarios.json")

# --- 震度解析用カラーマップ ---
RAW_COLOR_MAP = [
    {"Intensity":-3,"R":0,"G":0,"B":205},{"Intensity":-2.5,"R":0,"G":36,"B":227},{"Intensity":-2.4,"R":0,"G":43,"B":231},{"Intensity":-2,"R":0,"G":72,"B":250},{"Intensity":-1.5,"R":0,"G":140,"B":194},{"Intensity":-1,"R":0,"G":208,"B":139},{"Intensity":-0.9,"R":6,"G":212,"B":130},{"Intensity":-0.8,"R":12,"G":216,"B":121},{"Intensity":-0.7,"R":18,"G":220,"B":113},{"Intensity":-0.6,"R":25,"G":224,"B":104},{"Intensity":-0.5,"R":31,"G":228,"B":96},{"Intensity":-0.4,"R":37,"G":233,"B":88},{"Intensity":-0.3,"R":44,"G":237,"B":79},{"Intensity":-0.2,"R":50,"G":241,"B":71},{"Intensity":-0.1,"R":56,"G":245,"B":62},{"Intensity":0,"R":63,"G":250,"B":54},{"Intensity":0.1,"R":75,"G":250,"B":49},{"Intensity":0.2,"R":88,"G":250,"B":45},{"Intensity":0.3,"R":100,"G":251,"B":41},{"Intensity":0.4,"R":113,"G":251,"B":37},{"Intensity":0.5,"R":125,"G":252,"B":33},{"Intensity":0.6,"R":138,"G":252,"B":28},{"Intensity":0.7,"R":151,"G":253,"B":24},{"Intensity":0.8,"R":163,"G":253,"B":20},{"Intensity":0.9,"R":176,"G":254,"B":16},{"Intensity":1,"R":189,"G":255,"B":12},{"Intensity":1.1,"R":195,"G":254,"B":10},{"Intensity":1.2,"R":202,"G":254,"B":9},{"Intensity":1.3,"R":208,"G":254,"B":8},{"Intensity":1.4,"R":215,"G":254,"B":7},{"Intensity":1.5,"R":222,"G":255,"B":5},{"Intensity":1.6,"R":228,"G":254,"B":4},{"Intensity":1.7,"R":235,"G":255,"B":3},{"Intensity":1.8,"R":241,"G":254,"B":2},{"Intensity":1.9,"R":248,"G":255,"B":1},{"Intensity":2,"R":255,"G":255,"B":0},{"Intensity":2.1,"R":254,"G":251,"B":0},{"Intensity":2.2,"R":254,"G":248,"B":0},{"Intensity":2.3,"R":254,"G":244,"B":0},{"Intensity":2.4,"R":254,"G":241,"B":0},{"Intensity":2.5,"R":255,"G":238,"B":0},{"Intensity":2.6,"R":254,"G":234,"B":0},{"Intensity":2.7,"R":255,"G":231,"B":0},{"Intensity":2.8,"R":254,"G":227,"B":0},{"Intensity":2.9,"R":255,"G":224,"B":0},{"Intensity":3,"R":255,"G":221,"B":0},{"Intensity":3.1,"R":254,"G":213,"B":0},{"Intensity":3.2,"R":254,"G":205,"B":0},{"Intensity":3.3,"R":254,"G":197,"B":0},{"Intensity":3.4,"R":254,"G":190,"B":0},{"Intensity":3.5,"R":255,"G":182,"B":0},{"Intensity":3.6,"R":254,"G":174,"B":0},{"Intensity":3.7,"R":255,"G":167,"B":0},{"Intensity":3.8,"R":254,"G":159,"B":0},{"Intensity":3.9,"R":255,"G":151,"B":0},{"Intensity":4,"R":255,"G":144,"B":0},{"Intensity":4.1,"R":254,"G":136,"B":0},{"Intensity":4.2,"R":254,"G":128,"B":0},{"Intensity":4.3,"R":254,"G":121,"B":0},{"Intensity":4.4,"R":254,"G":113,"B":0},{"Intensity":4.5,"R":255,"G":106,"B":0},{"Intensity":4.6,"R":254,"G":98,"B":0},{"Intensity":4.7,"R":255,"G":90,"B":0},{"Intensity":4.8,"R":254,"G":83,"B":0},{"Intensity":4.9,"R":255,"G":75,"B":0},{"Intensity":5,"R":255,"G":68,"B":0},{"Intensity":5.1,"R":254,"G":61,"B":0},{"Intensity":5.2,"R":253,"G":54,"B":0},{"Intensity":5.3,"R":252,"G":47,"B":0},{"Intensity":5.4,"R":251,"G":40,"B":0},{"Intensity":5.5,"R":250,"G":33,"B":0},{"Intensity":5.6,"R":249,"G":27,"B":0},{"Intensity":5.7,"R":248,"G":20,"B":0},{"Intensity":5.8,"R":247,"G":13,"B":0},{"Intensity":5.9,"R":246,"G":6,"B":0},{"Intensity":6,"R":245,"G":0,"B":0},{"Intensity":6.1,"R":238,"G":0,"B":0},{"Intensity":6.2,"R":230,"G":0,"B":0},{"Intensity":6.3,"R":223,"G":0,"B":0},{"Intensity":6.4,"R":215,"G":0,"B":0},{"Intensity":6.5,"R":208,"G":0,"B":0},{"Intensity":6.6,"R":200,"G":0,"B":0},{"Intensity":6.7,"R":192,"G":0,"B":0},{"Intensity":6.8,"R":185,"G":0,"B":0},{"Intensity":6.9,"R":177,"G":0,"B":0},{"Intensity":7.0,"R":170,"G":0,"B":0}
]

# --- 【高速化】カラーマップのNumPy配列化 ---
_COLOR_MAP_RGB = np.array([[d['R'], d['G'], d['B']] for d in RAW_COLOR_MAP], dtype=np.int32)
_COLOR_MAP_INT = np.array([d['Intensity'] for d in RAW_COLOR_MAP], dtype=np.float32)

def get_max_intensity_vectorized(colors_rgb: np.ndarray) -> float:
    """NumPyブロードキャストによる高速な最大震度算出（誤認防止の距離リミット付き）"""
    if len(colors_rgb) == 0:
        return -3.0
    diff = colors_rgb[:, np.newaxis, :] - _COLOR_MAP_RGB[np.newaxis, :, :]
    dist_sq = np.sum(diff**2, axis=2)
    
    # 各ピクセルについて、最も近いカラーマップへの距離を求める
    min_dist_sq = np.min(dist_sq, axis=1)
    best_indices = np.argmin(dist_sq, axis=1)
    
    # 距離が遠すぎる（約RGB各値が平均40程度以上ずれている）色はノイズとして除外
    valid_mask = min_dist_sq < 4000
    
    if not np.any(valid_mask):
        return -3.0
        
    valid_indices = best_indices[valid_mask]
    return float(np.max(_COLOR_MAP_INT[valid_indices]))

def get_max_intensity_and_color_vectorized(colors_rgb: np.ndarray) -> Tuple[float, np.ndarray]:
    """重心計算用：最大震度とそのピクセルの色を返す（距離リミット付き）"""
    if len(colors_rgb) == 0:
        return -3.0, None
    diff = colors_rgb[:, np.newaxis, :] - _COLOR_MAP_RGB[np.newaxis, :, :]
    dist_sq = np.sum(diff**2, axis=2)
    
    min_dist_sq = np.min(dist_sq, axis=1)
    best_indices = np.argmin(dist_sq, axis=1)
    
    valid_mask = min_dist_sq < 4000
    if not np.any(valid_mask):
        return -3.0, None
        
    # 有効なピクセルのみに絞り込む
    valid_colors = colors_rgb[valid_mask]
    valid_best_indices = best_indices[valid_mask]
    
    intensities = _COLOR_MAP_INT[valid_best_indices]
    max_idx = np.argmax(intensities)
    
    return float(intensities[max_idx]), valid_colors[max_idx]

@dataclass
class SimEvent:
    """シミュレーション内の一つのイベントデータ"""
    timestamp: float
    data: JmaEqData
    label: str
    image_path: str = "" # 画像パスを保持

@dataclass
class PastEventGroup:
    """過去の地震一連のデータ群"""
    id: str           # YYYYMMDD_HHMM
    display_dt: str
    hypocenter: str = "---" # 震源地名
    lat: float = 0.0          
    lon: float = 0.0
    max_intensity: str = "0"
    has_eew: bool = False
    has_nied: bool = False
    has_borehole: bool = False
    start_ts: float = 0.0
    timeline: List[SimEvent] = field(default_factory=list)

# --- ユーティリティ ---
def intensity_to_float(s: str) -> float:
    mapping = {"0": 0.0, "1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0, 
               "5弱": 4.5, "5-": 4.5, "5強": 5.0, "5+": 5.0, 
               "6弱": 5.5, "6-": 5.5, "6強": 6.0, "6+": 6.0, "7": 7.0}
    return mapping.get(s, 0.0)

def float_to_intensity_str(val: float) -> str:
    if val < 0.5: return "0"
    if val < 1.5: return "1"
    if val < 2.5: return "2"
    if val < 3.5: return "3"
    if val < 4.5: return "4"
    if val < 5.0: return "5弱"
    if val < 5.5: return "5強"
    if val < 6.0: return "6弱"
    if val < 6.5: return "6強"
    return "7"

def clear_line_print(text: str):
    """ターミナルの現在行をクリアしてテキストを出力する"""
    sys.stdout.write("\r\033[K")
    print(text)

def get_east_asian_width_count(text: str) -> int:
    """文字列の表示幅（全角=2, 半角=1）を計算する"""
    count = 0
    for c in text:
        if unicodedata.east_asian_width(c) in 'FWA':
            count += 2
        else:
            count += 1
    return count

def pad_ja(text: str, width: int) -> str:
    """表示幅に基づいて文字列を右側スペース埋めする"""
    text_width = get_east_asian_width_count(text)
    padding = max(0, width - text_width)
    return text + " " * padding

def center_ja(text: str, width: int) -> str:
    """表示幅に基づいて文字列を中央揃え（スペース埋め）する"""
    text_width = get_east_asian_width_count(text)
    padding = max(0, width - text_width)
    left_pad = padding // 2
    right_pad = padding - left_pad
    return " " * left_pad + text + " " * right_pad

def format_hypocenter(name: str) -> str:
    """震源地名から都府県を省き, 3文字に丸める"""
    if not name or name in ["不明", "近隣", "---"]:
        return "－－－"
    s = name.replace("東京都", "東京")
    s = re.sub(r"(京都|大阪)府", r"\1", s)
    s = re.sub(r"県", "", s)
    return s[:3]

def get_direction_name(eq_lat: float, eq_lon: float, home_lat: float = 35.681236, home_lon: float = 139.767125) -> str:
    if eq_lat == 0.0 and eq_lon == 0.0: return "近隣"
    dy = eq_lat - home_lat
    dx = (eq_lon - home_lon) * math.cos(math.radians(home_lat))
    angle = math.degrees(math.atan2(dy, dx))
    
    if -22.5 <= angle < 22.5: return "東"
    elif 22.5 <= angle < 67.5: return "北東"
    elif 67.5 <= angle < 112.5: return "北"
    elif 112.5 <= angle < 157.5: return "北西"
    elif 157.5 <= angle <= 180 or -180 <= angle < -157.5: return "西"
    elif -157.5 <= angle < -112.5: return "南西"
    elif -112.5 <= angle < -67.5: return "南"
    elif -67.5 <= angle < -22.5: return "南東"
    return "近隣"

def _get_current_radius():
    settings_path = os.path.join(PROJECT_ROOT, "eq_config", "eq_settings.json")
    try:
        if os.path.exists(settings_path):
            with open(settings_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("nied_monitor", {}).get("radius_pixel", 30)
    except Exception:
        pass
    return 30

def extract_max_intensity_from_image(img_path: str, app_settings: dict, home_cx: float = None, home_cy: float = None) -> float:
    try:
        # with文でファイルリソースを確実に解放
        with Image.open(img_path) as raw_img:
            img = raw_img.convert("RGBA")
        nied_settings = app_settings.get("nied_monitor", {})
        cx = home_cx if home_cx is not None else nied_settings.get("home_x", getattr(eq_config, "NIED_HOME_X", 0))
        cy = home_cy if home_cy is not None else nied_settings.get("home_y", getattr(eq_config, "NIED_HOME_Y", 0))
        radius = nied_settings.get("radius_pixel", getattr(eq_config, "NIED_MONITOR_RADIUS", 30))
        
        if cx == 0 and cy == 0:
            left, top, right, bottom = 0, 0, img.width, img.height
        else:
            left, top = max(0, cx - radius), max(0, cy - radius)
            right, bottom = min(img.width, cx + radius), min(img.height, cy + radius)
        
        cropped_img = img.crop((left, top, right, bottom))
        rgb_arr = np.array(cropped_img)

        # 配列化が終わったらPillowの画像メモリを明示的に即時解放
        cropped_img.close()
        img.close()
        
        r, g, b, a = rgb_arr[:,:,0].astype(float), rgb_arr[:,:,1].astype(float), rgb_arr[:,:,2].astype(float), rgb_arr[:,:,3].astype(float)
        rgb_max = np.maximum(np.maximum(r, g), b)
        rgb_min = np.minimum(np.minimum(r, g), b)
        sat = rgb_max - rgb_min
        
        safe_rgb_max = np.where(rgb_max == 0, 1.0, rgb_max)
        relative_sat = sat / safe_rgb_max
        
        # 彩度が高く、明るく、不透明なピクセルのみを抽出（条件を eq_video_parser.py に合わせて厳格化）
        valid_mask = (rgb_max > 80) & (sat > 50) & (relative_sat > 0.5) & (a > 0)
        valid_pixels = rgb_arr[valid_mask][:, :3].astype(np.int32)
        
        if len(valid_pixels) > 0:
            # 海の青色を除外
            sea_mask = (valid_pixels[:, 2] > 150) & (valid_pixels[:, 0] < 50) & (valid_pixels[:, 1] < 150)
            target_pixels = valid_pixels[~sea_mask]
            
            if len(target_pixels) > 0:
                unique_colors = np.unique(target_pixels, axis=0)
                return get_max_intensity_vectorized(unique_colors)
                
        return -3.0
    except Exception:
        return -3.0

def estimate_direction_from_nied(img_path: str, home_x: int, home_y: int) -> str:
    try:
        if home_x == 0 and home_y == 0: return "近隣"
        
        with Image.open(img_path) as raw_img:
            img = Image.open(img_path).convert("RGBA")
        rgb_arr = np.array(img)

        # 配列化が終わったらPillowの画像メモリを明示的に即時解放
        img.close()
        
        r, g, b, a = rgb_arr[:,:,0].astype(float), rgb_arr[:,:,1].astype(float), rgb_arr[:,:,2].astype(float), rgb_arr[:,:,3].astype(float)
        rgb_max = np.maximum(np.maximum(r, g), b)
        rgb_min = np.minimum(np.minimum(r, g), b)
        sat = rgb_max - rgb_min
        
        safe_rgb_max = np.where(rgb_max == 0, 1.0, rgb_max)
        relative_sat = sat / safe_rgb_max
        
        # 色付きドットの抽出（条件を eq_video_parser.py に合わせて厳格化）
        valid_mask = (rgb_max > 80) & (sat > 50) & (relative_sat > 0.5) & (a > 0)
        sea_mask_2d = (b > 150) & (r < 50) & (g < 150)
        valid_mask = valid_mask & (~sea_mask_2d)
        
        valid_pixels = rgb_arr[valid_mask][:, :3].astype(np.int32)
        
        if len(valid_pixels) == 0: return "近隣"
        
        unique_colors = np.unique(valid_pixels, axis=0)
        max_int, max_color = get_max_intensity_and_color_vectorized(unique_colors)
        
        if max_color is None: return "近隣"
        
        strongest_mask = valid_mask & \
                         (rgb_arr[:,:,0] == max_color[0]) & \
                         (rgb_arr[:,:,1] == max_color[1]) & \
                         (rgb_arr[:,:,2] == max_color[2])
        y_coords, x_coords = np.where(strongest_mask)
        
        if len(y_coords) == 0: return "近隣"
        
        eq_x = np.mean(x_coords)
        eq_y = np.mean(y_coords)
        
        dist = math.sqrt((eq_x - home_x)**2 + (eq_y - home_y)**2)
        if dist < 25: return "近隣"
            
        dx = eq_x - home_x
        dy = eq_y - home_y 
        angle = math.degrees(math.atan2(-dy, dx))
        
        if -22.5 <= angle < 22.5: return "東"
        elif 22.5 <= angle < 67.5: return "北東"
        elif 67.5 <= angle < 112.5: return "北"
        elif 112.5 <= angle < 157.5: return "北西"
        elif 157.5 <= angle <= 180 or -180 <= angle < -157.5: return "西"
        elif -157.5 <= angle < -112.5: return "南西"
        elif -112.5 <= angle < -67.5: return "南"
        elif -67.5 <= angle < -22.5: return "南東"
        
        return "近隣"
    except Exception:
        return "近隣"

# --- データパース関連 ---
def parse_eew_file(filepath: str, home_lat: float = None, home_lon: float = None) -> JmaEqData:
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        is_warn = data.get("isWarn", False)
        eq_type = EqType.WARNING if is_warn else EqType.FORECAST
        if data.get("isCancel", False): eq_type = EqType.CANCEL
            
        home_scale = 0
        home_region = getattr(eq_config, "HOME_REGION_NAME", "千葉県北西部")
        for area in data.get("WarnArea", []):
            if area.get("Chiiki") == home_region:
                s = area.get("Shindo1", "0")
                mapping = {"1": 10, "2": 20, "3": 30, "4": 40, "5弱": 45, "5-": 45, 
                           "5強": 50, "5+": 50, "6弱": 55, "6-": 55, "6強": 60, "6+": 60, "7": 70}
                home_scale = mapping.get(s, 0)
                break
                
        # S-wave arrival timestamp calculation
        origin_time_str = data.get("OriginTime", "")
        origin_ts = 0.0
        try:
            dt = datetime.datetime.strptime(origin_time_str, "%Y/%m/%d %H:%M:%S")
            origin_ts = dt.timestamp()
        except Exception:
            pass

        eq_lat = data.get("Latitude", 0.0)
        eq_lon = data.get("Longitude", 0.0)
        eq_depth = float(data.get("Depth", 0.0))
        
        if home_lat is None:
            home_lat = getattr(eq_config, "HOME_LAT", 35.681236)
        if home_lon is None:
            home_lon = getattr(eq_config, "HOME_LON", 139.767125)

        s_wave_ts = 0.0
        if origin_ts > 0 and eq_lat > 0 and eq_lon > 0:
            s_wave_ts = GeoUtils.calculate_s_wave_arrival_ts(origin_ts, eq_lat, eq_lon, eq_depth, home_lat, home_lon)

        # 独自計算フォールバック (予想震度が無い場合)
        countdown_threshold = 30
        try:
            settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eq_config", "eq_settings.json")
            if os.path.exists(settings_path):
                with open(settings_path, "r", encoding="utf-8") as f:
                    st = json.load(f)
                    c_str = str(st.get("eew_alert", {}).get("countdown_threshold_scale", "3"))
                    mapping = {"1": 10, "2": 20, "3": 30, "4": 40, "5弱": 45, "5-": 45, "5強": 50, "5+": 50, "6弱": 55, "6-": 55, "6強": 60, "6+": 60, "7": 70}
                    v = mapping.get(c_str)
                    if v is None:
                        try:
                            v = max(10, min(70, int(round(float(c_str) * 10))))
                        except (ValueError, TypeError):
                            v = 30
                    countdown_threshold = v
        except Exception:
            pass

        is_custom_prediction = False
        mag = data.get("Magunitude", 0.0)
        ground_correction = getattr(eq_config, "HOME_GROUND_CORRECTION", 1.0)
        if home_scale == 0 and mag > 0.0 and eq_lat > 0 and eq_lon > 0:
            epi_dist = GeoUtils.calculate_distance(eq_lat, eq_lon, home_lat, home_lon)
            hypo_dist = math.sqrt(epi_dist**2 + eq_depth**2)
            if hypo_dist > 0:
                # 案C: WarnAreaの最近傍地域から外挿
                calc_intensity = GeoUtils.estimate_intensity_from_warnarea(
                    data.get("WarnArea", []), eq_lat, eq_lon, eq_depth, home_lat, home_lon
                )
                # 案C が使えない場合は案B3（勝俣式 + 北東方向異常震域補正）
                if calc_intensity is None:
                    calc_intensity = GeoUtils.calc_fallback_intensity(
                        mag, hypo_dist,
                        ground_correction=ground_correction,
                        eq_lat=eq_lat, eq_lon=eq_lon,
                        home_lat=home_lat, home_lon=home_lon
                    )

                if calc_intensity >= 7.0: home_scale = 70
                elif calc_intensity >= 6.5: home_scale = 60
                elif calc_intensity >= 6.0: home_scale = 55
                elif calc_intensity >= 5.5: home_scale = 50
                elif calc_intensity >= 5.0: home_scale = 45
                elif calc_intensity >= 4.0: home_scale = 40
                elif calc_intensity >= 3.0: home_scale = 30
                elif calc_intensity >= 2.0: home_scale = 20
                elif calc_intensity >= 1.0: home_scale = 10
                else: home_scale = 0

                if home_scale > 0:
                    is_custom_prediction = True

        return JmaEqData(
            source=EqSource.WOLFX,
            type=eq_type,
            event_id=data.get("EventID", "0000"),
            hypocenter_name=data.get("Hypocenter", "不明"),
            magnitude=mag,
            depth=str(data.get("Depth", "0")),
            max_intensity=data.get("MaxIntensity", "0"),
            predicted_home_scale=home_scale,
            is_final=data.get("isFinal", False),
            original_text=json.dumps(data),
            s_wave_arrival_ts=s_wave_ts,
            is_custom_prediction=is_custom_prediction
        )
    except Exception as e:
        print(f"[Error] EEW parse failed ({filepath}): {e}")
        return None

def parse_nied_filename(filename: str) -> Tuple[float, str]:
    # 震度部分([-0-9.]+)の後に拡張子のドットが来ないよう肯定先読み
    m = re.search(r"eqlog_s_(\d{14})_lv([-0-9.]+)(?=\.png)", filename)
    if m:
        try:
            # 念のため抽出した文字列の末尾からドットを削るstripを行うとより安全
            dt = datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
            # 抽出した文字列の末尾にドットが残っていたら削除
            lv_str = m.group(2).rstrip('.')
            return dt.timestamp(), lv_str
        except ValueError:
            pass
    return 0.0, "0"

# --- ログ収集ロジック (引数でディレクトリのリストを動的に受け取る) ---
def collect_past_events(eew_bases: List[str], nied_bases: List[str]) -> List[PastEventGroup]:
    groups: Dict[str, PastEventGroup] = {}
    
    # 1. EEWログの収集 (複数ディレクトリ対応)
    for eew_base in eew_bases:
        if os.path.exists(eew_base):
            for date_dir in os.listdir(eew_base):
                if not re.match(r"\d{8}", date_dir): continue
                date_path = os.path.join(eew_base, date_dir)
                for f in os.listdir(date_path):
                    if not f.endswith(".json"): continue
                    
                    path = os.path.join(date_path, f)

                    # タイムスタンプをJSON内部の AnnouncedTime から取得
                    ts = 0.0
                    try:
                        with open(path, 'r', encoding='utf-8') as jf:
                            raw_dict = json.load(jf)
                        ann_time = raw_dict.get("AnnouncedTime")
                        if ann_time:
                            ts = datetime.datetime.strptime(ann_time, "%Y/%m/%d %H:%M:%S").timestamp()
                        else:
                            m = re.search(r"(\d{14})", f)
                            if m:
                                ts = datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M%S").timestamp()
                    except Exception:
                        continue
                    
                    if ts == 0.0: continue

                    m_file = re.search(r"(\d{14})", f)
                    if not m_file: continue
                    group_id = m_file.group(1)[:12]
                    key = f"{group_id[:8]}_{group_id[8:]}"
                    
                    if key not in groups:
                        groups[key] = PastEventGroup(id=key, display_dt=key)
                    
                    data = parse_eew_file(path)
                    if data is None: continue

                    if data.hypocenter_name and data.hypocenter_name != "不明":
                        groups[key].hypocenter = data.hypocenter_name

                    try:
                        raw_dict = json.loads(data.original_text)
                        groups[key].lat = raw_dict.get("Latitude", 0.0)
                        groups[key].lon = raw_dict.get("Longitude", 0.0)
                    except Exception:
                        pass

                    groups[key].timeline.append(SimEvent(timestamp=ts, data=data, label="WOLFX", image_path=path))
                    groups[key].has_eew = True
                    
                    cur_max = intensity_to_float(groups[key].max_intensity)
                    new_max = intensity_to_float(data.max_intensity)
                    if new_max > cur_max:
                        groups[key].max_intensity = data.max_intensity

    # 2. NIEDログの収集 (複数ディレクトリ対応)
    for nied_base in nied_bases:
        if os.path.exists(nied_base):
            for dirname in os.listdir(nied_base):
                m_dir = re.search(r"eqlog_(?:s_|b_)?(\d{8}_\d{4})_lv([0-9]+(?:\.[0-9]+)?)", dirname)
                if not m_dir: continue

                raw_key = m_dir.group(1)
                raw_lv_val = m_dir.group(2)
                try:
                    dir_max_lv_str = float_to_intensity_str(float(raw_lv_val))
                except (ValueError, TypeError):
                    print(f"[collect_past_events] Skip: unparseable lv value {raw_lv_val!r} in dir {dirname!r}")
                    continue

                # EEWとNIEDの1分ズレを許容して紐づけ
                matched_key = raw_key
                try:
                    nied_dt = datetime.datetime.strptime(raw_key, "%Y%m%d_%H%M")
                    # 既に登録されているEEWのキー（時刻）と比較して、前後1分以内のズレであれば同じイベントとみなす
                    for existing_key in list(groups.keys()):
                        existing_dt = datetime.datetime.strptime(existing_key, "%Y%m%d_%H%M")
                        delta_sec = (nied_dt - existing_dt).total_seconds()
                        
                        # 前後1分（60秒以内）のズレを許容して同じイベントとみなす
                        if abs(delta_sec) <= 60:
                            matched_key = existing_key
                            break
                except ValueError:
                    pass
                # ----------------------------------------------

                if matched_key not in groups:
                    groups[matched_key] = PastEventGroup(id=matched_key, display_dt=matched_key)
                
                groups[matched_key].has_nied = True
                
                dir_path = os.path.join(nied_base, dirname)
                has_bh = False
                if 'eqlog_b_' in dirname:
                    has_bh = True
                else:
                    bh_dir = os.path.join(dir_path, 'borehole')
                    if os.path.exists(bh_dir) and any(f.startswith("eqlog_b_") for f in os.listdir(bh_dir)):
                        has_bh = True
                    elif os.path.exists(dir_path) and any(f.startswith("eqlog_b_") for f in os.listdir(dir_path)):
                        has_bh = True

                if has_bh:
                    groups[matched_key].has_borehole = True

                if intensity_to_float(dir_max_lv_str) > intensity_to_float(groups[matched_key].max_intensity):
                    groups[matched_key].max_intensity = dir_max_lv_str

                dir_path = os.path.join(nied_base, dirname)
                # surface/ サブフォルダがあれば優先してスキャン（新構造対応）
                surface_path = os.path.join(dir_path, "surface")
                scan_path = surface_path if os.path.exists(surface_path) else dir_path
                for f in os.listdir(scan_path):
                    if not f.startswith("eqlog_s_") or not f.endswith(".png"): continue
                    ts, lv_float_str = parse_nied_filename(f)
                    if ts == 0.0: continue

                    direction_name = "近隣"
                    if groups[matched_key].lat != 0.0 and groups[matched_key].lon != 0.0:
                        h_lat = getattr(eq_config, "HOME_LATITUDE", 35.681236)
                        h_lon = getattr(eq_config, "HOME_LONGITUDE", 139.767125)
                        direction_name = get_direction_name(groups[matched_key].lat, groups[matched_key].lon, h_lat, h_lon)

                    img_path = os.path.join(scan_path, f)
                    nied_data = JmaEqData(
                        source=EqSource.NIED, type=EqType.REALTIME,
                        event_id=f"nied_{raw_key}", hypocenter_name=direction_name,
                        max_intensity=float_to_intensity_str(float(lv_float_str)),
                        predicted_home_scale=0
                    )
                    # 画像パスを含めてタイムラインに追加
                    groups[matched_key].timeline.append(SimEvent(timestamp=ts, data=nied_data, label="NIED", image_path=img_path))
                    
    # 各グループの開始時刻とタイムラインのソート

    for g in groups.values():
        if g.timeline:
            g.timeline.sort(key=lambda x: x.timestamp)
            
            # --- トリガー時刻（地震発生基準時刻）の特定 ---
            # 5秒前から保存されていることを考慮し、最初のWOLFX、または震度1以上のNIEDをトリガーとして採用
            trigger_ts = g.timeline[0].timestamp
            for ev in g.timeline:
                if ev.label == "WOLFX":
                    trigger_ts = ev.timestamp
                    break
                elif ev.label == "NIED":
                    lv = intensity_to_float(ev.data.max_intensity)
                    if lv >= 0.5:  # 震度1以上の最初の画像をトリガーとみなす
                        trigger_ts = ev.timestamp
                        break
            
            g.start_ts = trigger_ts

    return sorted(groups.values(), key=lambda x: x.id)

# --- 実行コア ---
def run_simulation_engine(app: EqSystem, event_group: PastEventGroup, playback_speed: float = 1.0, visualizers=None, on_finish_callback=None, log_callback=None, eew_callback=None, is_borehole_func=None):
    def log_print(msg, is_tick=False):
        clean_msg = msg.replace("\033[K", "")
        if log_callback:
            log_callback(clean_msg)
        else:
            if is_tick:
                sys.stdout.write(msg)
                sys.stdout.flush()
            else:
                sys.stdout.write("\r\033[K")
                print(clean_msg.lstrip('\r'))

    sim_start_ts = event_group.start_ts - 5
    if event_group.timeline:
        sim_start_ts = min(sim_start_ts, event_group.timeline[0].timestamp)
    remaining_events = list(event_group.timeline)
    
    # 開始5秒前からのダミー表示用として、最初のNIED画像を抽出
    base_nied_image_path = None
    for e in remaining_events:
        if e.label == "NIED" and e.image_path:
            base_nied_image_path = e.image_path
            break
    
    duration_real_sec = 600 
    start_real_time = time.time()
    last_injection_real_time = start_real_time
    nied_image_pushed = False # NIED画像がイベントキューから取り出されたかのフラグ
    last_nied_max_intensity = -1.0

    pause_start_real_time = None

    try:
        real_elapsed = 0
        while real_elapsed < duration_real_sec:
            # Check stop flag set by UI
            if getattr(app, 'sim_stop_flag', False):
                log_print("[中止] ユーザー操作によりシミュレーションを中断しました。")
                break
                
            # Check pause flag set by UI
            if getattr(app, 'sim_paused', False):
                if pause_start_real_time is None:
                    pause_start_real_time = time.time()
                time.sleep(0.1)
                
                # Dynamically advance start_real_time by exactly the sleep duration
                # to keep real_elapsed perfectly frozen without drift
                now = time.time()
                start_real_time += (now - pause_start_real_time)
                pause_start_real_time = now
                
                if hasattr(app, 'eq_system') and getattr(app.eq_system, 'active_countdown_data', None):
                    app.eq_system.active_countdown_data.sim_start_real_time = start_real_time
                continue
                
            pause_start_real_time = None
            
            sim_elapsed = int(real_elapsed * playback_speed)
            current_sim_ts = sim_start_ts + sim_elapsed
            current_dt = datetime.datetime.fromtimestamp(current_sim_ts)
            
            time_offset = sim_elapsed - 5
            sign = "+" if time_offset >= 0 else ""
            sim_time_str = f"{current_dt.strftime('%Y/%m/%d %H:%M:%S')} (T{sign}{time_offset}s)"
            
            log_print(f"\r[SIM TIME] {sim_time_str} ", is_tick=True)

            # --- [開始5秒前(T<0)のプレ表示] ---
            if visualizers and base_nied_image_path and time_offset < 0 and not nied_image_pushed:
                try:
                    img = Image.open(base_nied_image_path)
                    # 地震発生前として震度0扱い（-3.0）でプッシュ
                    for viz in visualizers:
                        viz.push_image(img.copy(), sim_time_str, -3.0)
                except Exception:
                    pass

            while remaining_events:
                ev_peek = remaining_events[0]
                # NIEDイベントは実レイテンシ分遅延させて表示する
                display_ts = ev_peek.timestamp + (NIED_IMAGE_LATENCY_SEC if ev_peek.label == "NIED" else 0.0)
                if display_ts > current_sim_ts:
                    break
                ev = remaining_events.pop(0)
                
                # --- NIED画像解析とビジュアライザ更新 ---
                if ev.label == "NIED" and visualizers and ev.image_path:
                    # 本物の画像イベントが来たらフラグを立ててダミー表示を解除
                    nied_image_pushed = True
                    try:
                        _ovr_cx = getattr(app, 'sim_override_home_cx', None)
                        _ovr_cy = getattr(app, 'sim_override_home_cy', None)
                        extracted_int = extract_max_intensity_from_image(ev.image_path, app.settings, home_cx=_ovr_cx, home_cy=_ovr_cy)
                        ev.data.max_intensity = float_to_intensity_str(extracted_int)

                        # オーバーライド時は常に方角を再計算。通常時は未確定名のみ
                        if _ovr_cx is not None or ev.data.hypocenter_name in ("近隣", "---"):
                            nied_settings = app.settings.get("nied_monitor", {})
                            home_x = _ovr_cx if _ovr_cx is not None else nied_settings.get("home_x", getattr(eq_config, "NIED_HOME_X", 0))
                            home_y = _ovr_cy if _ovr_cy is not None else nied_settings.get("home_y", getattr(eq_config, "NIED_HOME_Y", 0))

                            estimated_dir = estimate_direction_from_nied(ev.image_path, home_x, home_y)
                            if estimated_dir != "近隣":
                                ev.data.hypocenter_name = estimated_dir
                        
                        current_image_path = ev.image_path
                        if is_borehole_func and is_borehole_func():
                            import glob
                            b_dir = os.path.join(os.path.dirname(os.path.dirname(ev.image_path)), "borehole")
                            base = os.path.basename(ev.image_path)
                            parts = base.split('_lv')
                            if len(parts) >= 2:
                                prefix = parts[0].replace('eqlog_s_', 'eqlog_b_')
                                pattern = os.path.join(b_dir, prefix + "*.png")
                                matches = glob.glob(pattern)
                                if matches:
                                    current_image_path = matches[0]
                                else:
                                    current_image_path = current_image_path.replace("\\surface\\eqlog_s_", "\\borehole\\eqlog_b_").replace("/surface/eqlog_s_", "/borehole/eqlog_b_")
                            else:
                                current_image_path = current_image_path.replace("\\surface\\eqlog_s_", "\\borehole\\eqlog_b_").replace("/surface/eqlog_s_", "/borehole/eqlog_b_")
                        
                        
                        img = Image.open(current_image_path)
                        countdown_sec = None
                        is_custom = False
                        if getattr(app, 'sim_original_s_wave_arrival_ts', 0.0) > 0.0:
                            countdown_sec = int(app.sim_original_s_wave_arrival_ts - current_sim_ts)
                            # Stop showing countdown after it's been "Arrived" for a long time
                            if countdown_sec < -60:
                                countdown_sec = None
                            is_custom = getattr(app, 'sim_original_is_custom_prediction', False)
                        predicted_home_scale = getattr(app, 'sim_original_predicted_home_scale', 0)

                        app.sim_current_ev_image_path = ev.image_path
                        app.sim_current_time_str = sim_time_str
                        app.sim_current_intensity = extracted_int
                        app.sim_current_countdown_sec = countdown_sec
                        app.sim_current_is_custom = is_custom
                        app.sim_current_predicted_home_scale = predicted_home_scale

                        for viz in visualizers:
                            viz.push_image(img.copy(), sim_time_str, extracted_int, countdown_sec=countdown_sec, is_custom_prediction=is_custom, predicted_home_scale=predicted_home_scale)
                    except Exception:
                        pass
                
                alert_threshold = app.settings.get("nied_monitor", {}).get("alert_threshold", 2.0)
                
                if ev.label == "NIED" and intensity_to_float(ev.data.max_intensity) < alert_threshold:
                    pass
                else:
                    should_inject = True
                    if ev.label == "NIED":
                        current_int = intensity_to_float(ev.data.max_intensity)
                        if current_int <= last_nied_max_intensity:
                            should_inject = False
                        else:
                            last_nied_max_intensity = current_int

                    if should_inject:
                        if ev.label == "WOLFX" and eew_callback:
                            eew_callback(ev.data)
                            if ev.data.s_wave_arrival_ts > 0:
                                app.sim_original_s_wave_arrival_ts = ev.data.s_wave_arrival_ts
                                app.sim_original_is_custom_prediction = getattr(ev.data, 'is_custom_prediction', False)
                                app.sim_original_predicted_home_scale = getattr(ev.data, 'predicted_home_scale', 0)
                                # Inject simulation context for accurate countdown in EqSystem
                                ev.data.sim_playback_speed = playback_speed
                                ev.data.sim_start_real_time = start_real_time
                                ev.data.sim_start_ts = sim_start_ts
                        ev.data.is_simulated = True
                        app.on_earthquake_data(ev.data)
                        log_print(f" >> [{ev.label}] データ投入: エリア内最大震度{ev.data.max_intensity} / 方角:{ev.data.hypocenter_name} (Type: {ev.data.type.name})")
                
                last_injection_real_time = time.time()
                # 【高速化】ループのブロック時間を 0.05秒 から 0.01秒 に短縮し、倍速再生時のラグを排除
                time.sleep(0.01)

            if not remaining_events and (time.time() - last_injection_real_time) >= 15.0:
                log_print("[情報] 全イベントの投入と音声再生待機が完了しました。")
                break

            next_tick = start_real_time + (real_elapsed + 1)
            sleep_time = next_tick - time.time()
            if sleep_time > 0:
                time.sleep(sleep_time)
                
            real_elapsed += 1

    except KeyboardInterrupt:
        log_print("[中止] ユーザー操作によりシミュレーションを中断しました。")
    finally:
        # GUI終了のスケジュール
        if on_finish_callback:
            on_finish_callback()

# --- 動作テスト ---
def run_operation_test(app: EqSystem):
    scenarios = []
    if os.path.exists(SCENARIO_FILE):
        with open(SCENARIO_FILE, 'r', encoding='utf-8') as f:
            scenarios = json.load(f)
    
    if not scenarios:
        print("シナリオファイルが空です。")
        return

    print("\n--- 動作テスト実行 ---")
    util_audio_player.play_audio(os.path.join(SIM_VOICE_DIR, "eq_v_sim_start.wav"), volume=90)
    time.sleep(3)

    for sc in scenarios:
        data_raw = sc["data"]
        eq_data = JmaEqData(
            source=EqSource[data_raw.get("source", "WOLFX")],
            type=EqType[data_raw.get("type", "FORECAST")],
            event_id=data_raw.get("event_id", "sim_event"),
            hypocenter_name=data_raw.get("hypocenter_name", "テスト震源"),
            max_intensity=data_raw.get("max_intensity", "3"),
            predicted_home_scale=data_raw.get("predicted_home_scale", 30)
        )
        
        offset = data_raw.get("s_wave_arrival_offset", 0)
        if offset > 0:
            eq_data.s_wave_arrival_ts = time.time() + offset
            
        print(f"\n[Scenario] {sc.get('scenario_name')} 実行中...")
        app.on_earthquake_data(eq_data)
        
        wait_sec = sc.get("wait_seconds", 5)
        # Sleep incrementally to allow background threads (like countdown) to log output
        for _ in range(wait_sec):
            time.sleep(1)

# --- メインメニュー ---
def main():
    print("="*40)
    print("  EARTHQUAKE ALERT SYSTEM SIMULATOR")
    print("="*40)
    print("1: 動作テスト (固定シナリオ JSON から実行)")
    print("2: 仮想シナリオ (テスト用生成データから追体験)")
    print("3: 過去ログデータ (本番の履歴データから追体験)")
    print("q: 終了")
    
    choice = input("\n選択 > ").strip().lower()

    if choice == 'q': return
    
    app = EqSystem()

    try:
        if choice == '1':
            run_operation_test(app)
        elif choice in ['2', '3']:
            if choice == '2':
                # 仮想シナリオ: 生成されたテストディレクトリを参照 (リスト化)
                eew_dirs = [os.path.join(CURRENT_DIR, "test_eq_log", "test_eq_eew_jma")]
                nied_dirs = [os.path.join(CURRENT_DIR, "test_monitor_images", "eq_log")]
                data_type_name = "仮想シナリオ"
            else:
                # 過去ログデータ: SSDとSDカード(実運用)の両方を参照
                ssd_mount = getattr(eq_config, "SSD_MOUNT_POINT", "/mnt/eq_ssd")
                eew_dirs = [
                    os.path.join(ssd_mount, "raspberrypi/alert_eq/eq_log/eq_eew_jma"),
                    os.path.join(PROJECT_ROOT, "eq_log", "eq_eew_jma")
                ]
                nied_dirs = [
                    os.path.join(ssd_mount, "raspberrypi/alert_eq/eq_log/monitor_images/eq_log"),
                    os.path.join(PROJECT_ROOT, "eq_log", "monitor_images", "eq_log")
                ]
                data_type_name = "過去ログデータ"

            # リストを渡すように変更
            events = collect_past_events(eew_dirs, nied_dirs)
            
            if not events:
                print(f"\n[エラー] {data_type_name}が見つかりませんでした。パスを確認してください。")
            else:
                print(f"\n--- {data_type_name} ---")
                header = (f"\n{pad_ja('No.', 5)}{pad_ja('日時', 16)}{pad_ja('震源地', 10)}"
                        f"{pad_ja('最大震度', 10)}{pad_ja('EEW', 6)}{pad_ja('NIED', 6)}")
                print(header)
                print("-" * 53)
                
                for i, ev in enumerate(events):
                    eew_mark = "〇" if ev.has_eew else "-"
                    nied_mark = "〇" if ev.has_nied else "-"
                    fmt_hypo = format_hypocenter(ev.hypocenter)
                    
                    row = (f"{pad_ja(f'[{i+1}]', 5)}{pad_ja(ev.display_dt, 16)}"
                        f"{pad_ja(fmt_hypo, 10)}{pad_ja(ev.max_intensity, 10)}"
                        f"{pad_ja(eew_mark, 6)}{pad_ja(nied_mark, 6)}")
                    print(row)
                
                try:
                    sel = int(input("\n再生する番号を選択してください (0:戻る) > "))
                    if 1 <= sel <= len(events):
                        speed_input = input("再生速度を入力してください (例: 1.0, 2.0 / デフォルト: 1.0) > ")
                        speed = float(speed_input) if speed_input.replace('.', '', 1).isdigit() else 1.0
                        
                        target_event = events[sel-1]
                        
                        # --- GUI連携スレッドの起動 ---
                        if target_event.has_nied:
                            try:
                                import tkinter as tk
                                from eq_visualizer import NiedMapVisualizer
                                
                                root = tk.Tk()
                                root.protocol("WM_DELETE_WINDOW", lambda: None) 
                                visualizer = NiedMapVisualizer(root, settings=app.settings.get("nied_monitor", {}), mode="full")
                                
                                # シミュレーションを別スレッドで実行
                                sim_thread = threading.Thread(
                                    target=run_simulation_engine, 
                                    args=(app, target_event, speed, visualizer, root)
                                )
                                sim_thread.start()
                                
                                # Tkinterメインループ (ここでブロック)
                                root.mainloop()
                                sim_thread.join()
                                
                            except Exception as e:
                                print(f"\n[Warning] GUIの初期化に失敗しました。CUIモードで実行します: {e}")
                                run_simulation_engine(app, target_event, speed)
                        else:
                            run_simulation_engine(app, target_event, speed)

                except ValueError:
                    print("有効な数字を入力してください。")
    
    except ValueError:
        print("有効な数字を入力してください。")
    except KeyboardInterrupt:
        # どこで中断されてもここで一括キャッチしてクリーンアップ
        clear_line_print("\n[中止] ユーザー操作によりシミュレーションを中断しました。")
    finally:
        # --- 確実な終了・割り込み処理 ---
        app.stop()
        
        # 音声制御用ロックを取得し、以後のシステム音声再生を完全にブロックする
        with app.audio_lock:
            app.is_playing = True
            app.current_playing_priority = 9999  # 絶対に割り込まれない最大優先度を設定
            app._stop_current_audio()
        
        # バックグラウンドで結合処理中だった音声スレッドの遅延再生を潰すための極小スリープと念押し停止
        time.sleep(0.2)
        app._stop_current_audio()
        
        # 終了音声の再生
        util_audio_player.play_audio(os.path.join(SIM_VOICE_DIR, "eq_v_sim_end.wav"), volume=90)
        print("\n=== シミュレーション終了 ===")

if __name__ == "__main__":
    main()