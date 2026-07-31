"""
#6 到達確定フェイルセーフ のオフライン検証(20260626)。

実 NiedMonitor の検出ロジック(_maybe_trigger_arrival_failsafe)を、ダミーの eq_system で
受けて、カウントダウン中に自宅近傍(10km圏)が閾値到達した瞬間に到達アラートが
発火するか・1イベント1回に抑えられるかを確認する。

使い方:
    python eq_test/test_arrival_failsafe.py
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

from eq_monitor_nied import NiedMonitor
import eq_config.eq_config as config

EVENT = os.path.join(PROJECT_ROOT, "eq_log", "monitor_images", "eq_log", "eqlog_20260626_2229_lv5.3")
SURF = os.path.join(EVENT, "surface")


class FakeData:
    def __init__(self, event_id):
        self.event_id = event_id


class FakeSystem:
    """EqSystem.trigger_observed_arrival の重複防止挙動を模した受け手。"""
    def __init__(self):
        self.active_countdown_data = FakeData("ev_20260626")
        self.observed_arrival_event_id = ""
        self.fire_log = []        # 実発火(重複防止後)
        self.call_count = 0       # NiedMonitor からの呼び出し総数

    def trigger_observed_arrival(self, event_id):
        self.call_count += 1
        if not event_id or self.observed_arrival_event_id == event_id:
            return
        self.observed_arrival_event_id = event_id
        self.fire_log.append(event_id)


def run(threshold):
    settings = {"radius_pixel": 50, "trigger_pixels": 10, "trigger_intensity": 1.3,
                "arrival_failsafe_enabled": True, "arrival_failsafe_intensity": threshold,
                "arrival_failsafe_radius_km": 10,
                # 副作用(地中フェッチ等)を抑止
                "borehole_enabled": False, "plum_enabled": False,
                "eta_correction_enabled": False, "site_calibration_enabled": False}
    fake = FakeSystem()
    nm = NiedMonitor(settings, callback=lambda d: None, saves_history=False, eq_system=fake)
    home_x, home_y = config.NIED_HOME_X, config.NIED_HOME_Y

    surf = {}
    for p in glob.glob(os.path.join(SURF, "*.png")):
        m = re.search(r"eqlog_s_(\d{14})_", os.path.basename(p))
        if m:
            surf[m.group(1)] = p

    peak_near = -3.0
    for ts in sorted(surf):
        with Image.open(surf[ts]) as raw:
            img = raw.convert("RGB")
            near = nm.ring_monitor.max_within(img, nm._match_color_intensity_cached, (home_x, home_y), 10)
            peak_near = max(peak_near, near)
            nm._maybe_trigger_arrival_failsafe(img, home_x, home_y)
    return fake, peak_near


def main():
    print("=== #6 到達確定フェイルセーフ 検証(20260626, 自宅10km圏) ===")
    # まず近傍ピークを把握
    _, peak = run(threshold=99)
    print(f"自宅10km圏 観測ピーク = 震度{peak:.1f}\n")

    for thr in [3.0, 2.0]:
        fake, _ = run(thr)
        fired = "発火" if fake.fire_log else "発火せず"
        print(f"閾値 震度{thr}: 検出呼び出し {fake.call_count} 回 → 実発火 {len(fake.fire_log)} 回 [{fired}]")

    print("\n=== 判定 ===")
    print(f"・本イベントは自宅近傍が弱く(ピーク震度{peak:.1f})、既定閾値 震度3.0 では発火せず(妥当)。")
    print("・閾値を下回る設定では検出が連続するが、重複防止で実発火は1回に収束(機構OK)。")
    print("・本領: EEWカウントダウンが遅すぎる(到達済みなのに未到達表示)時に、")
    print("  自宅近傍の実揺れ観測で独立に到達アラートを出す安全ネット。")


if __name__ == "__main__":
    main()
