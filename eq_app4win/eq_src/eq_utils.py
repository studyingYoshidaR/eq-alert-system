import logging
import logging.handlers
import os
import sys
import math
import json
import csv
from typing import List, Dict, Tuple, Optional

# Import from eq_config.eq_const will be done later
# from eq_config.eq_const import HOME_LATITUDE, HOME_LONGITUDE

_UTILS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_LOG_DIR = os.path.abspath(os.path.join(_UTILS_DIR, "..", "eq_log"))

class EqLogger:
    @staticmethod
    def setup_logger(name: str = "alert_jma", log_dir: str = _DEFAULT_LOG_DIR) -> logging.Logger:
        """
        Create a logger with log rotation
        """
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO) # default
        
        # Log format
        formatter = logging.Formatter(
            '%(asctime)s - [%(levelname)s] - %(filename)s:%(lineno)d - %(message)s'
        )
        
        # Rotating file output (1MB x 5 generations)
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "system.log"),
            maxBytes=1024*1024,
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        
        # Console output
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        
        return logger

class GeoUtils:
    @staticmethod
    def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """
        Calculate the distance (km) between two points using the Haversine formula
        """
        R = 6371.0  # Earth radius in kilometers

        d_lat = math.radians(lat2 - lat1)
        d_lon = math.radians(lon2 - lon1)
        
        a = (math.sin(d_lat / 2) ** 2 +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(d_lon / 2) ** 2)
        
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    @staticmethod
    def calculate_s_wave_arrival_ts(origin_ts: float, eq_lat: float, eq_lon: float, depth_km: float, home_lat: float, home_lon: float) -> float:
        """
        Calculate the estimated S-wave arrival timestamp based on hypocentral distance
        """
        # Epicentral distance
        epi_dist = GeoUtils.calculate_distance(eq_lat, eq_lon, home_lat, home_lon)
        
        # Hypocentral distance
        hypo_dist = math.sqrt(epi_dist**2 + depth_km**2)
        
        # S-wave velocity (approx 4.0 km/s)
        s_wave_vel = 4.0
        
        return origin_ts + (hypo_dist / s_wave_vel)

    # WarnArea地域座標キャッシュ（初回ロード時に保持）
    _warnarea_coords: Optional[Dict] = None

    @staticmethod
    def _load_warnarea_coords() -> Dict:
        if GeoUtils._warnarea_coords is None:
            # frozen(exe)では _MEIPASS(_internal) 配下、ソースでは eq_src の親を基準にする
            if getattr(sys, 'frozen', False):
                _base = sys._MEIPASS
            else:
                _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            coords_path = os.path.join(_base, "eq_assets", "eq_warnarea_coords.json")
            try:
                with open(coords_path, encoding='utf-8') as f:
                    raw = json.load(f)
                GeoUtils._warnarea_coords = {k: v for k, v in raw.items() if not k.startswith('_')}
            except Exception:
                GeoUtils._warnarea_coords = {}
        return GeoUtils._warnarea_coords

    @staticmethod
    def estimate_intensity_from_warnarea(
        warn_areas: List[Dict],
        eq_lat: float, eq_lon: float, depth_km: float,
        home_lat: float, home_lon: float,
        shindo_str_to_float: Optional[Dict] = None
    ) -> Optional[float]:
        """
        案C: WarnAreaの各地域から HOME への震度を外挿する。
        震源に近い地域の実測予測値を基に距離減衰で補外する。

        返り値: 推定震度 (float)。外挿できなければ None。
        """
        SHINDO_MAP = shindo_str_to_float or {
            "1": 1.0, "2": 2.0, "3": 3.0, "4": 4.0,
            "5弱": 5.0, "5-": 5.0, "5強": 5.5, "5+": 5.5,
            "6弱": 6.0, "6-": 6.0, "6強": 6.5, "6+": 6.5,
            "7": 7.0
        }
        coords = GeoUtils._load_warnarea_coords()

        home_epi = GeoUtils.calculate_distance(eq_lat, eq_lon, home_lat, home_lon)
        home_hypo = math.sqrt(home_epi ** 2 + depth_km ** 2)

        # 距離比の上限: HOME までの距離 / 基準地域までの距離 がこれを超える外挿は精度低下
        MAX_RATIO = 3.5

        candidates = []
        for area in warn_areas:
            chiiki = area.get("Chiiki", "")
            shindo_str = area.get("Shindo1", "")
            if chiiki not in coords or shindo_str not in SHINDO_MAP:
                continue
            I_ref = SHINDO_MAP[shindo_str]
            c = coords[chiiki]
            ref_epi = GeoUtils.calculate_distance(eq_lat, eq_lon, c["lat"], c["lon"])
            ref_hypo = math.sqrt(ref_epi ** 2 + depth_km ** 2)

            # 基準地域が HOME より震源に近い場合のみ外挿
            if ref_hypo >= home_hypo or ref_hypo <= 0:
                continue
            # 距離比が大きすぎる場合は外挿精度が低下するためスキップ
            if home_hypo / ref_hypo > MAX_RATIO:
                continue

            # 距離減衰係数 3.2 で外挿: I_home = I_ref - 3.2 * log10(D_home / D_ref)
            I_home = I_ref - 3.2 * math.log10(home_hypo / ref_hypo)
            candidates.append((ref_hypo, I_home))

        if not candidates:
            return None

        # 推定震度が最大の候補を採用（最も保守的な = 過小評価しにくい値）
        return max(candidates, key=lambda x: x[1])[1]

    @staticmethod
    def calc_fallback_intensity(
        mag: float, hypo_dist: float,
        ground_correction: float = 1.0,
        eq_lat: float = 0.0, eq_lon: float = 0.0,
        home_lat: float = 0.0, home_lon: float = 0.0
    ) -> float:
        """
        案B3: 勝俣式 + 距離・方位角補正。
        北東方向(東北・北海道方面) かつ遠距離では異常震域効果として追加補正を加える。

        返り値: 計算震度 (float)
        """
        if hypo_dist <= 0:
            return 0.0

        correction = ground_correction

        # 案B3: 北東象限(方位0〜90°) かつ hypo_dist > 400km → +1.0 加算
        if (eq_lat > 0 and eq_lon > 0 and home_lat > 0 and home_lon > 0
                and hypo_dist > 400):
            bearing = math.degrees(math.atan2(
                math.sin(math.radians(eq_lon - home_lon)) * math.cos(math.radians(eq_lat)),
                math.cos(math.radians(home_lat)) * math.sin(math.radians(eq_lat)) -
                math.sin(math.radians(home_lat)) * math.cos(math.radians(eq_lat)) *
                math.cos(math.radians(eq_lon - home_lon))
            ))
            bearing = (bearing + 360) % 360
            # 北〜東(0〜90°) = 東北・北海道方面（異常震域の典型的方向）
            if 0 <= bearing <= 90:
                correction += 1.0

        return 1.5 * mag - 3.2 * math.log10(hypo_dist) - 0.8 + correction

    @staticmethod
    def load_nied_stations(csv_path: str) -> List[Dict]:
        """
        en: Load NIED station list from CSV
        """
        stations = []
        try:
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Assuming CSV headers are 'code', 'name', 'lat', 'lon'
                    stations.append({
                        "code": row['code'],
                        "name": row['name'],
                        "lat": float(row['lat']),
                        "lon": float(row['lon'])
                    })
        except Exception as e:
            # Instead of printing here, let the caller log the error
            raise e
        return stations