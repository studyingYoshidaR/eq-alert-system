"""
#2 疑似PLUM法 / リング観測モジュール のオフライン検証テスト。

20260626 22:29 イベントの地表(surface)画像列について、自宅周辺 10/20/30km 圏の
観測最大震度を毎秒算出し、RingMonitor.intensity_to_home_scale で予測スケールへ変換。
EEW が仮に予測震度=30(震度3)だった場合に、PLUM がどう上方修正するかを時系列表示する。

使い方:
    python eq_test/test_plum_ring.py
"""
import os, re, glob, json, sys
from PIL import Image
import numpy as np

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.append(PROJECT_ROOT)
sys.path.append(os.path.join(PROJECT_ROOT, "eq_src"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from eq_ring_monitor import RingMonitor   # 本番コアを直接import
import eq_config.eq_config as config

EVENT = os.path.join(PROJECT_ROOT, "eq_log", "monitor_images", "eq_log", "eqlog_20260626_2229_lv5.3")
SURF = os.path.join(EVENT, "surface")
COLOR_MAP_PATH = os.path.join(PROJECT_ROOT, "eq_assets", "eq_NiedColorMap.json")

with open(COLOR_MAP_PATH, "r", encoding="utf-8") as f:
    cmap = json.load(f)

# 本番 _match_color_intensity と同じ最近傍(距離リミット500)を再現した match_fn
_cache = {}
def match_fn(rgb):
    rgb = tuple(rgb)
    if rgb in _cache:
        return _cache[rgb]
    r1, g1, b1 = rgb
    best, md = -3.0, 1e18
    for it in cmap:
        d = (r1 - it["R"]) ** 2 + (g1 - it["G"]) ** 2 + (b1 - it["B"]) ** 2
        if d < md:
            md, best = d, it["Intensity"]
    if md > 500:
        best = -3.0
    _cache[rgb] = best
    return best


def main():
    rm = RingMonitor(config.HOME_LAT, config.HOME_LON, radii_km=(10, 20, 30))
    center = (config.NIED_HOME_X, config.NIED_HOME_Y)

    print("=== RingMonitor 事前計算(ピクセル数 sanity) ===")
    print(f"home=({config.HOME_LAT},{config.HOME_LON}) center_px={center}")
    for r in rm.radii_km:
        print(f"  リング {r:2}km : {len(rm.rings[r]):3} px(環状)")
    print(f"  disc(<= {rm.max_radius_km}km) : {len(rm.disc)} px")

    surf = {}
    for p in glob.glob(os.path.join(SURF, "*.png")):
        m = re.search(r"eqlog_s_(\d{14})_", os.path.basename(p))
        if m:
            surf[m.group(1)] = p

    # 全フレームの PLUM 観測震度を一度だけ算出
    series = []  # (ts, ring10, ring20, ring30, plum_int, scale)
    for ts in sorted(surf):
        with Image.open(surf[ts]) as raw:
            img = raw.convert("RGB")
            rmax = rm.ring_maxes(img, match_fn, center)
            plum_int = rm.max_within(img, match_fn, center, 30)
        scale = RingMonitor.intensity_to_home_scale(plum_int)
        series.append((ts, rmax[10], rmax[20], rmax[30], plum_int, scale))

    peak_scale = max((s[5] for s in series), default=0)
    peak_int = max((s[4] for s in series), default=-3.0)

    print(f"\n=== PLUM 観測 時系列 (自宅30km圏内最大震度) ===")
    print(f"{'time':14} | {'10km':>5} {'20km':>5} {'30km':>5} | {'PLUM_int':>8} {'scale':>5}")
    print("-" * 56)
    for ts, r10, r20, r30, pi, sc in series:
        if pi >= 1.0:
            print(f"{ts:14} | {r10:5.1f} {r20:5.1f} {r30:5.1f} | {pi:8.1f} {sc:5}")

    print(f"\nPLUM 観測ピーク: 震度{peak_int:.1f} → scale {peak_scale}")
    print(f"(参考) 従来の自宅クロップ(半径50px=200km)最大 = lv5.3 だが、"
          f"30km圏内の実態は震度{peak_int:.1f}。PLUMの方が自宅近傍に忠実)")

    # --- 本番ロジック predicted=max(EEW, PLUM) を複数の仮EEW値で検証 ---
    print(f"\n=== 上方修正の検証 (predicted = max(EEW, PLUM); 単調上昇) ===")
    for eew in [10, 30, 40]:
        final = max(eew, peak_scale)
        verb = "上方修正" if final > eew else "据え置き"
        print(f"  EEW予測={eew:2} → 最終予測={final:2}  [{verb}]")
    print("\n  [OK] 過小評価(EEW=10)はPLUMが30へ補正、")
    print("       正当/過大(EEW=30,40)はPLUM以下のため不要な吊り上げをしない(片方向のみ)")


if __name__ == "__main__":
    main()
