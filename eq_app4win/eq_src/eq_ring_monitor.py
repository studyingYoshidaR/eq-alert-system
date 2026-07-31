"""
リング観測モジュール (core)

自宅を中心とした既知距離(km)の同心リングに該当する NIED モニタ画像のピクセル群を
事前計算し、毎フレームの観測最大震度を返す再利用コンポーネント。

用途:
  - #2 疑似PLUM法 : 自宅周辺 R km 圏内の観測最大震度で EEW 予測震度を上方修正
  - #3 (将来)     : リングへの波面到達時刻から ETA を再計算
  - #5 (将来)     : 経路上サンプル点の実測で経路バイアスを補正

設計:
  - ピクセル座標 → 緯度経度の逆変換 (latlon_to_nied_pixel の逆) で各ピクセルの
    自宅からの地理距離を Haversine で算出し、リングへ振り分ける。
    校正の x/y 異方性 (約 7.7 / 5.5 km/px) を距離計算で吸収するため、
    単純な「ピクセル半径」より正確。
  - オフセット(dx,dy)は中心非依存として事前計算し、本番(自宅)/シミュレーション
    (エリアオーバーライド中心)の双方に同一オフセットを適用する。
"""
from typing import Callable, Dict, List, Tuple

from eq_utils import GeoUtils
import eq_config.eq_config as config


def pixel_to_latlon(x: float, y: float) -> Tuple[float, float]:
    """latlon_to_nied_pixel の逆変換。
    x = (lon-120.09)*11.73 / y = (48.625-lat)*20.0 より復元。"""
    lon = x / 11.73 + 120.09
    lat = 48.625 - y / 20.0
    return lat, lon


class RingMonitor:
    def __init__(self, home_lat: float, home_lon: float,
                 radii_km: Tuple[int, ...] = (10, 20, 30), px_search: int = 12):
        self.home_lat = home_lat
        self.home_lon = home_lon
        self.radii_km = tuple(sorted(radii_km))
        self.max_radius_km = self.radii_km[-1]

        # 基準中心(自宅キャリブレーション)で相対オフセット→距離km を事前計算
        cx, cy = config.NIED_HOME_X, config.NIED_HOME_Y
        # disc: 最大半径以内の全オフセット [(dx, dy, dist_km)]
        self.disc: List[Tuple[int, int, float]] = []
        # rings: 距離リングごとのオフセット (累積ではなく環状)
        self.rings: Dict[int, List[Tuple[int, int]]] = {r: [] for r in self.radii_km}
        for dy in range(-px_search, px_search + 1):
            for dx in range(-px_search, px_search + 1):
                lat, lon = pixel_to_latlon(cx + dx, cy + dy)
                d = GeoUtils.calculate_distance(lat, lon, home_lat, home_lon)
                if d <= self.max_radius_km:
                    self.disc.append((dx, dy, d))
                    # 最初に該当する(=最小の)リング半径に振り分け
                    for r in self.radii_km:
                        if d <= r:
                            self.rings[r].append((dx, dy))
                            break

    def max_within(self, img, match_fn: Callable[[Tuple[int, int, int]], float],
                   center_xy: Tuple[float, float], radius_km: float = None) -> float:
        """center から radius_km 以内のピクセルの観測最大震度を返す(疑似PLUM)。
        img: PIL RGB Image / match_fn: rgb→震度float。"""
        if radius_km is None:
            radius_km = self.max_radius_km
        cx, cy = center_xy
        px = img.load()
        w, h = img.size
        mx = -3.0
        for dx, dy, d in self.disc:
            if d > radius_km:
                continue
            x = int(round(cx + dx))
            y = int(round(cy + dy))
            if 0 <= x < w and 0 <= y < h:
                v = match_fn(px[x, y])
                if v > mx:
                    mx = v
        return mx

    def ring_maxes(self, img, match_fn: Callable[[Tuple[int, int, int]], float],
                   center_xy: Tuple[float, float]) -> Dict[int, float]:
        """各リング半径ごとの観測最大震度を返す(将来 #3/#5 用)。"""
        cx, cy = center_xy
        px = img.load()
        w, h = img.size
        out = {}
        for r in self.radii_km:
            mx = -3.0
            for dx, dy in self.rings[r]:
                x = int(round(cx + dx))
                y = int(round(cy + dy))
                if 0 <= x < w and 0 <= y < h:
                    v = match_fn(px[x, y])
                    if v > mx:
                        mx = v
            out[r] = mx
        return out

    @staticmethod
    def intensity_to_home_scale(v: float) -> int:
        """観測震度(float) → 内部スケール(×10)。EEW predicted_home_scale と同一の梯子
        (eq_eew_wolfx と一致させ max() 比較を整合させる)。"""
        if v >= 7.0: return 70
        if v >= 6.5: return 60
        if v >= 6.0: return 55
        if v >= 5.5: return 50
        if v >= 5.0: return 45
        if v >= 4.0: return 40
        if v >= 3.0: return 30
        if v >= 2.0: return 20
        if v >= 1.0: return 10
        return 0
