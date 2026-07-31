"""
#4 サイト地盤増幅 較正モジュール (機構のみ・適用は保留)

自宅地点の地盤増幅 = (地表震度 − 地中震度) を実測で蓄積し、HOME_GROUND_CORRECTION
の較正に用いるための機構。

重要な制約(20260626で判明):
  純粋なサイト増幅は「同一地点」の地表/地中の差でしか測れない。地中観測網は疎で、
  自宅近傍に地中局が無い場合がある(本プロジェクトの自宅は最寄り約18km)。
  そのため measure_amplification は「有意な揺れを示す最寄りの地中ピクセル」を探し、
  そこでの co-located 差を測り、自宅までの距離を品質指標として一緒に記録する。
  距離が大きいサンプルは経路距離が混入するため、推定時に距離で品質ゲートする。

適用(predicted への反映)は呼び出し側で保留。本モジュールは「測る・貯める・推定する」まで。
"""
import os
import json
import statistics
from typing import Callable, Optional, Tuple, List, Dict

from eq_utils import GeoUtils
from eq_ring_monitor import pixel_to_latlon


def measure_amplification(
    surf_img, bore_img, center_xy: Tuple[float, float],
    match_fn: Callable[[Tuple[int, int, int]], float],
    search_px: int = 12, bore_min_intensity: float = 1.0,
    max_dist_km: Optional[float] = None,
) -> Optional[Tuple[float, float, float, float, Tuple[int, int]]]:
    """有意な揺れを示す最寄りの地中ピクセルで co-located 増幅を測定する。

    戻り値: (amplification, distance_km, surf_int, bore_int, (x, y)) または None
      amplification = surf_int - bore_int (同一ピクセル)
      distance_km   = そのピクセルの自宅からの地理距離(品質指標)
    地中が bore_min_intensity 以上のピクセルが探索範囲(かつ max_dist_km 以内)に
    無ければ None。max_dist_km を与えると品質範囲外のピクセルは最初から無視する。
    """
    home_lat, home_lon = pixel_to_latlon(*center_xy)
    bpx = bore_img.load()
    spx = surf_img.load()
    bw, bh = bore_img.size
    cx, cy = center_xy

    best = None  # (dist_km, x, y, bore_int)
    for dy in range(-search_px, search_px + 1):
        for dx in range(-search_px, search_px + 1):
            x = int(round(cx + dx))
            y = int(round(cy + dy))
            if not (0 <= x < bw and 0 <= y < bh):
                continue
            b_int = match_fn(bpx[x, y])
            if b_int < bore_min_intensity:
                continue
            lat, lon = pixel_to_latlon(x, y)
            dist = GeoUtils.calculate_distance(lat, lon, home_lat, home_lon)
            if max_dist_km is not None and dist > max_dist_km:
                continue
            if best is None or dist < best[0]:
                best = (dist, x, y, b_int)

    if best is None:
        return None
    dist, x, y, b_int = best
    sw, sh = surf_img.size
    if not (0 <= x < sw and 0 <= y < sh):
        return None
    s_int = match_fn(spx[x, y])
    amp = s_int - b_int
    return (amp, dist, s_int, b_int, (x, y))


def interpolate_borehole_at_home(
    bore_img, center_xy: Tuple[float, float],
    match_fn: Callable[[Tuple[int, int, int]], float],
    search_px: int = 20, min_valid_intensity: float = -1.0,
    power: float = 2.0, max_dist_km: float = 80.0,
) -> Optional[Tuple[float, int, float, float]]:
    """周囲の有意な地中ピクセルから逆距離加重(IDW)で自宅地点の地中(基盤)震度を補間推定。

    自宅近傍に地中局が無い場合の代替。背景(局なし)は min_valid_intensity 未満として除外。
    戻り値: (interp_intensity, n_stations, nearest_km, mean_dist_km) または None
    """
    home_lat, home_lon = pixel_to_latlon(*center_xy)
    bpx = bore_img.load()
    bw, bh = bore_img.size
    cx, cy = center_xy

    num = den = dsum = 0.0
    n = 0
    nearest = 1e9
    for dy in range(-search_px, search_px + 1):
        for dx in range(-search_px, search_px + 1):
            x = int(round(cx + dx))
            y = int(round(cy + dy))
            if not (0 <= x < bw and 0 <= y < bh):
                continue
            v = match_fn(bpx[x, y])
            if v < min_valid_intensity:   # 背景(局配置なし)を除外
                continue
            lat, lon = pixel_to_latlon(x, y)
            d = GeoUtils.calculate_distance(lat, lon, home_lat, home_lon)
            if d > max_dist_km:
                continue
            d = max(d, 0.1)
            w = 1.0 / (d ** power)
            num += w * v
            den += w
            dsum += d
            n += 1
            nearest = min(nearest, d)
    if n == 0 or den == 0:
        return None
    return (num / den, n, nearest, dsum / n)


class SiteCalibrator:
    """サイト増幅サンプルを永続化・蓄積し、頑健推定(中央値)を返す。

    - イベント(event_id)ごとにピーク増幅サンプルを1つに集約。
    - distance_km <= max_station_km の高品質サンプルのみ推定に使用。
    - サンプルが min_samples 未満なら estimate()=None(=未較正)。
    """

    def __init__(self, path: str, min_samples: int = 5, max_station_km: float = 25.0):
        self.path = path
        self.min_samples = min_samples
        self.max_station_km = max_station_km
        self.samples: Dict[str, Dict] = {}  # event_id -> sample dict
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.samples = {s["event_id"]: s for s in data.get("samples", [])}
        except Exception:
            self.samples = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"samples": list(self.samples.values())}, f,
                          ensure_ascii=False, indent=2)
        except Exception:
            pass

    def record_sample(self, event_id: str, ts: float, amplification: float,
                      distance_km: float, surf_int: float, bore_int: float) -> bool:
        """イベント単位でピーク増幅を集約記録。新規/更新があれば True。"""
        prev = self.samples.get(event_id)
        # 同一イベント内ではより強い揺れ(地中震度が高い)のサンプルを採用(SN比が高い)
        if prev is not None and prev.get("bore_int", -99) >= bore_int:
            return False
        self.samples[event_id] = {
            "event_id": event_id, "ts": ts,
            "amplification": round(amplification, 2),
            "distance_km": round(distance_km, 1),
            "surf_int": round(surf_int, 1), "bore_int": round(bore_int, 1),
        }
        self._save()
        return True

    def quality_samples(self) -> List[Dict]:
        return [s for s in self.samples.values()
                if s.get("distance_km", 1e9) <= self.max_station_km]

    def estimate(self) -> Optional[float]:
        """高品質サンプルの増幅中央値。サンプル不足なら None。"""
        q = self.quality_samples()
        if len(q) < self.min_samples:
            return None
        return float(statistics.median(s["amplification"] for s in q))

    def calibrated_ground_correction(self, default: float, apply: bool = False) -> float:
        """較正済み地盤補正値。apply=False(既定=保留)なら常に default を返す。
        apply=True かつ十分なサンプルがある時のみ推定値を返す。"""
        if not apply:
            return default
        est = self.estimate()
        return est if est is not None else default

    def status(self) -> str:
        q = self.quality_samples()
        est = self.estimate()
        est_str = f"{est:.2f}" if est is not None else "未較正"
        return (f"samples={len(self.samples)} quality(<= {self.max_station_km}km)={len(q)} "
                f"estimate={est_str} (min_samples={self.min_samples})")
