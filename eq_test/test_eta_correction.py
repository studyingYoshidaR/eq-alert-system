"""
#3 S波到達ETA補正(観測アンカー方式) のオフライン検証テスト。

20260626 イベントについて:
  1. EEW(原点時刻+震央)から従来方式の s_wave_arrival_ts を算出
  2. 地表フレーム列でリング前線到達を観測し、観測アンカーETA = データ時刻 + 前線距離/V を算出
  3. min(EEW, 観測) の短縮のみ補正でカウントダウンが実到達にどれだけ整合するかを比較

使い方:
    python eq_test/test_eta_correction.py
"""
import os, re, glob, json, sys, datetime
from PIL import Image

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.append(PROJECT_ROOT)
sys.path.append(os.path.join(PROJECT_ROOT, "eq_src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from eq_ring_monitor import RingMonitor
from eq_utils import GeoUtils
import eq_config.eq_config as config

EVENT = os.path.join(PROJECT_ROOT, "eq_log", "monitor_images", "eq_log", "eqlog_20260626_2229_lv5.3")
SURF = os.path.join(EVENT, "surface")
EEW_DIR = os.path.join(PROJECT_ROOT, "eq_log", "eq_eew_jma", "20260626")
COLOR_MAP_PATH = os.path.join(PROJECT_ROOT, "eq_assets", "eq_NiedColorMap.json")

ARRIVAL_INTENSITY = 2.0
V = 4.0

with open(COLOR_MAP_PATH, "r", encoding="utf-8") as f:
    cmap = json.load(f)
_cache = {}
def match_fn(rgb):
    rgb = tuple(rgb)
    if rgb in _cache: return _cache[rgb]
    r1, g1, b1 = rgb
    best, md = -3.0, 1e18
    for it in cmap:
        d = (r1 - it["R"]) ** 2 + (g1 - it["G"]) ** 2 + (b1 - it["B"]) ** 2
        if d < md: md, best = d, it["Intensity"]
    if md > 500: best = -3.0
    _cache[rgb] = best
    return best

def epoch(ts):
    return datetime.datetime.strptime(ts, "%Y%m%d%H%M%S").timestamp()


def load_eew_s_wave_ts():
    """EEW最終報から従来方式の home S波到達tsを算出。"""
    files = sorted(glob.glob(os.path.join(EEW_DIR, "*.json")),
                   key=lambda p: int(os.path.basename(p).split('_')[1].split('.')[0]))
    d = json.load(open(files[-1], encoding="utf-8"))
    origin = datetime.datetime.strptime(d["OriginTime"], "%Y/%m/%d %H:%M:%S").timestamp()
    lat, lon, dep = d["Latitude"], d["Longitude"], float(d["Depth"])
    s_ts = GeoUtils.calculate_s_wave_arrival_ts(origin, lat, lon, dep, config.HOME_LAT, config.HOME_LON)
    return s_ts, origin, lat, lon, dep


def main():
    rm = RingMonitor(config.HOME_LAT, config.HOME_LON, radii_km=(10, 20, 30))
    center = (config.NIED_HOME_X, config.NIED_HOME_Y)

    eew_s_ts, origin, lat, lon, dep = load_eew_s_wave_ts()
    print(f"=== EEW 従来方式 ===")
    print(f"震央=({lat},{lon}) 深さ{dep}km 原点={datetime.datetime.fromtimestamp(origin):%H:%M:%S}")
    print(f"EEW S波到達(自宅,V=4.0)= {datetime.datetime.fromtimestamp(eew_s_ts):%H:%M:%S}")

    surf = {}
    for p in glob.glob(os.path.join(SURF, "*.png")):
        m = re.search(r"eqlog_s_(\d{14})_", os.path.basename(p))
        if m: surf[m.group(1)] = p

    # 実到達(ground truth): 10km圏(自宅近傍)が初めて ARRIVAL_INTENSITY を超えた時刻
    actual_arrival = None
    corrected_ts = eew_s_ts
    rows = []
    for ts in sorted(surf):
        with Image.open(surf[ts]) as raw:
            img = raw.convert("RGB")
            ring_max = rm.ring_maxes(img, match_fn, center)
        # 観測前線距離(内側から最初に閾値超え)
        obs_front = None
        for r in rm.radii_km:
            if ring_max.get(r, -3.0) >= ARRIVAL_INTENSITY:
                obs_front = r; break
        if obs_front is not None and actual_arrival is None and obs_front == rm.radii_km[0]:
            actual_arrival = epoch(ts)  # 最内リング到達=実質の自宅到達
        # 観測アンカーETA → min補正
        if obs_front is not None:
            obs_eta = epoch(ts) + obs_front / V
            if obs_eta < corrected_ts - 0.5:
                corrected_ts = obs_eta
        rows.append((ts, ring_max, obs_front, corrected_ts))

    print(f"\n=== 時系列(EEW残り vs 補正後残り; 実到達={datetime.datetime.fromtimestamp(actual_arrival):%H:%M:%S} と仮定) ===")
    print(f"{'time':14} | {'10km':>5}{'20km':>5}{'30km':>5} | {'front':>5} | {'EEW_rem':>7} {'corr_rem':>8}")
    print("-" * 66)
    for ts, ring_max, obs_front, corr in rows:
        if max(ring_max.values()) < 1.0:  # 揺れ前は省略
            continue
        e_rem = eew_s_ts - epoch(ts)
        c_rem = corr - epoch(ts)
        fs = f"{obs_front}km" if obs_front else "-"
        print(f"{ts:14} | {ring_max[10]:5.1f}{ring_max[20]:5.1f}{ring_max[30]:5.1f} | {fs:>5} | "
              f"{e_rem:7.1f} {c_rem:8.1f}")

    print(f"\n=== 0点(到達予告)の実到達との誤差 ===")
    print(f"実到達(観測10km圏)     : {datetime.datetime.fromtimestamp(actual_arrival):%H:%M:%S}")
    print(f"EEW予測の到達          : {datetime.datetime.fromtimestamp(eew_s_ts):%H:%M:%S}  (誤差 {eew_s_ts-actual_arrival:+.1f}s)")
    print(f"補正後の到達           : {datetime.datetime.fromtimestamp(corrected_ts):%H:%M:%S}  (誤差 {corrected_ts-actual_arrival:+.1f}s)")
    print(f"\n[安全性] 補正は min(EEW, 観測) の短縮のみ → 0点が実到達より後ろにずれる")
    print(f"         (=到達済みなのに未到達表示する)危険方向には決して動かない。")


if __name__ == "__main__":
    main()
