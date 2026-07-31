"""
#4 補間ベースのサイト増幅較正 の効果検証(20260626)。

自宅に地中局が無いため、周囲の地中局からIDW補間で自宅地点の基盤震度を推定し、
地表(自宅10km圏ピーク) との差で増幅を算出。補間の信頼性(支持局の距離・数)も併記し、
「効果が見いだせるか」を判断する材料を出す。

使い方:
    python eq_test/test_site_interpolation.py
"""
import os, re, glob, json, sys
from PIL import Image

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.append(PROJECT_ROOT)
sys.path.append(os.path.join(PROJECT_ROOT, "eq_src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from eq_site_calibration import interpolate_borehole_at_home
from eq_ring_monitor import RingMonitor
import eq_config.eq_config as config

EVENT = os.path.join(PROJECT_ROOT, "eq_log", "monitor_images", "eq_log", "eqlog_20260626_2229_lv5.3")
SURF = os.path.join(EVENT, "surface")
BORE = os.path.join(EVENT, "borehole")
COLOR_MAP_PATH = os.path.join(PROJECT_ROOT, "eq_assets", "eq_NiedColorMap.json")

cmap = json.load(open(COLOR_MAP_PATH, encoding="utf-8"))
_cache = {}
def match_fn(rgb):
    rgb = tuple(rgb)
    if rgb in _cache: return _cache[rgb]
    r1, g1, b1 = rgb; best, md = -3.0, 1e18
    for it in cmap:
        d = (r1-it["R"])**2 + (g1-it["G"])**2 + (b1-it["B"])**2
        if d < md: md, best = d, it["Intensity"]
    if md > 500: best = -3.0
    _cache[rgb] = best; return best


def main():
    rm = RingMonitor(config.HOME_LAT, config.HOME_LON, radii_km=(10, 20, 30))
    center = (config.NIED_HOME_X, config.NIED_HOME_Y)

    def idx(d, pat, grp):
        o = {}
        for p in glob.glob(os.path.join(d, "*.png")):
            m = re.search(pat, os.path.basename(p))
            if m: o[grp(m)] = p
        return o
    surf = idx(SURF, r"eqlog_s_(\d{14})_", lambda m: m.group(1))
    bore = idx(BORE, r"eqlog_b_(\d{14})_", lambda m: m.group(1))
    common = sorted(set(surf) & set(bore))

    print("=== 補間による自宅基盤震度の推定 と 増幅 (IDW, search80km) ===")
    print(f"{'time':14} | {'surf_home':>9} {'interp_bore':>11} {'amp':>5} | {'n':>3} {'near_km':>7} {'mean_km':>7}")
    print("-" * 70)
    best = None  # (interp_bore, ts, surf, amp, n, near, mean)
    for ts in common:
        with Image.open(surf[ts]) as s, Image.open(bore[ts]) as b:
            simg = s.convert("RGB"); bimg = b.convert("RGB")
            surf_home = rm.max_within(simg, match_fn, center, 10)   # 自宅10km圏の地表ピーク
            interp = interpolate_borehole_at_home(bimg, center, match_fn,
                                                  search_px=20, min_valid_intensity=-1.0,
                                                  max_dist_km=80.0)
        if interp is None:
            continue
        ib, n, near, mean = interp
        amp = surf_home - ib
        if surf_home >= 1.5 and (best is None or ib > best[0]):
            best = (ib, ts, surf_home, amp, n, near, mean)
        if surf_home >= 1.5:
            print(f"{ts:14} | {surf_home:9.1f} {ib:11.1f} {amp:5.1f} | {n:3} {near:7.1f} {mean:7.1f}")

    print("\n=== 評価 ===")
    if best is None:
        print("  補間に必要な地中局が周囲に存在せず。効果なし。")
        return
    ib, ts, sh, amp, n, near, mean = best
    print(f"  代表(基盤最強時 {ts}):")
    print(f"    地表(自宅10km) = {sh:.1f}")
    print(f"    補間基盤       = {ib:.1f}  (支持局 {n}個, 最寄り {near:.1f}km, 平均 {mean:.1f}km)")
    print(f"    推定サイト増幅 = {amp:+.1f}")
    print(f"\n  [信頼性] 支持局の最寄りが {near:.1f}km。" +
          ("近傍局が無く遠方からの外挿のため信頼性は低い。" if near > 25 else "近傍局があり比較的妥当。"))
    print(f"  [判断材料] 増幅 {amp:+.1f} が関東平野の妥当範囲(+1.5〜+2.0)に入るか、")
    print(f"             かつ複数イベントで安定するかで『効果あり』を判定する。単一イベントでは暫定。")


if __name__ == "__main__":
    main()
