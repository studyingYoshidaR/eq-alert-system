"""
#4 サイト地盤増幅 較正機構 のオフライン検証テスト。

検証1(実データ): 20260626 の地表/地中ペアで measure_amplification を実行し、
                 自宅近傍の地中カバレッジの有無を確認する。
検証2(機構):    SiteCalibrator の record/集約/品質ゲート/中央値推定が正しく動くか
                 (合成サンプルで検証)。永続化は一時ファイルで分離。

使い方:
    python eq_test/test_site_calibration.py
"""
import os, re, glob, json, sys, tempfile
from PIL import Image

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.append(PROJECT_ROOT)
sys.path.append(os.path.join(PROJECT_ROOT, "eq_src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from eq_site_calibration import SiteCalibrator, measure_amplification
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


def test_real_data():
    print("=== 検証1: 20260626 実データでの増幅測定 ===")
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

    found = 0
    nearest_overall = None
    for ts in common:
        with Image.open(surf[ts]) as s, Image.open(bore[ts]) as b:
            res = measure_amplification(s.convert("RGB"), b.convert("RGB"), center, match_fn,
                                        search_px=12, bore_min_intensity=1.0, max_dist_km=25.0)
        if res is not None:
            amp, dist, si, bi, px = res
            found += 1
            if nearest_overall is None or dist < nearest_overall[1]:
                nearest_overall = (ts, dist, amp, si, bi)
    print(f"  共通フレーム {len(common)} 中、有意な近傍地中(>=震度1.0)が取れたフレーム: {found}")
    if found:
        ts, dist, amp, si, bi = nearest_overall
        print(f"  最寄り例: {ts} 距離{dist:.1f}km 増幅{amp:+.1f}(地表{si:.1f}-地中{bi:.1f})")
    else:
        print("  [想定通り] 自宅近傍(探索範囲)に震度1以上の地中ピクセルなし")
        print("  → この自宅はカバレッジ外。較正サンプルは0件(機構は正しく『記録せず』)")


def test_calibrator_mechanism():
    print("\n=== 検証2: SiteCalibrator 機構(合成サンプル) ===")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "site_calibration.json")
        cal = SiteCalibrator(path, min_samples=5, max_station_km=25.0)

        print(f"  初期 estimate={cal.estimate()} (サンプル0)")
        # 同一イベントは集約(地中震度が高い方を採用)されることを確認
        cal.record_sample("ev1", 1.0, amplification=1.0, distance_km=8, surf_int=3.0, bore_int=2.0)
        cal.record_sample("ev1", 2.0, amplification=1.8, distance_km=8, surf_int=4.0, bore_int=2.2)  # bore強→更新
        cal.record_sample("ev1", 3.0, amplification=9.9, distance_km=8, surf_int=9.9, bore_int=0.1)  # bore弱→無視
        assert len(cal.samples) == 1, "イベント集約が効いていない"
        assert cal.samples["ev1"]["amplification"] == 1.8, "ピーク(bore強)採用になっていない"
        print(f"  ev1 集約後: amp={cal.samples['ev1']['amplification']} (bore強サンプル採用) [OK]")

        # 距離品質ゲート: 30km(>25)は推定から除外される
        cal.record_sample("ev2", 1.0, 1.5, distance_km=10, surf_int=3.0, bore_int=1.5)
        cal.record_sample("ev3", 1.0, 2.0, distance_km=12, surf_int=3.5, bore_int=1.5)
        cal.record_sample("ev4", 1.0, 1.6, distance_km=15, surf_int=3.0, bore_int=1.4)
        cal.record_sample("ev5_far", 1.0, 5.0, distance_km=30, surf_int=6.0, bore_int=1.0)  # 遠い→除外
        print(f"  全{len(cal.samples)}件 / 品質(<=25km){len(cal.quality_samples())}件")

        est = cal.estimate()  # 5件未満? ev1,2,3,4=4件(品質) → None
        print(f"  estimate(品質4件 < min5)={est} (未較正のはず)")
        assert est is None, "min_samples ゲートが効いていない"

        cal.record_sample("ev6", 1.0, 1.7, distance_km=9, surf_int=3.0, bore_int=1.3)
        est = cal.estimate()  # 品質5件 → median([1.8,1.5,2.0,1.6,1.7])=1.7
        print(f"  +1件で品質5件 → estimate={est} (中央値)")
        assert est == 1.7, f"中央値推定が不正: {est}"

        # 適用ゲート: apply=False は常に default
        gc_held = cal.calibrated_ground_correction(default=1.0, apply=False)
        gc_apply = cal.calibrated_ground_correction(default=1.0, apply=True)
        print(f"  ground_correction: 保留(apply=False)={gc_held}  適用(apply=True)={gc_apply}")
        assert gc_held == 1.0 and gc_apply == 1.7, "適用ゲートが不正"

        # 永続化の往復
        cal2 = SiteCalibrator(path, min_samples=5, max_station_km=25.0)
        assert cal2.estimate() == 1.7, "永続化ロードが不正"
        print(f"  永続化往復後 estimate={cal2.estimate()} [OK]")
    print("  [PASS] 集約/距離ゲート/min_samples/中央値/適用保留/永続化 すべて整合")


if __name__ == "__main__":
    test_real_data()
    test_calibrator_mechanism()
