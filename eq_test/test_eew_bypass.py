# -*- coding: utf-8 -*-
"""
#1 地中ANDの「EEW連動バイパス」検証テスト(20260702 千葉県北東部 M4.0 の再現)。

背景: サイト増幅+1.5〜1.8のため、地表1.3〜2.3帯の本物の地震は地中が確認閾値
(0.5)に物理的に届かず、実地震でも「生活ノイズの可能性」として60秒以上
誤抑制された(実ログ: 地表MaxInt:1.5-1.7 / 地中MaxInt:0.2 TrigPix:0)。
修正: 直近 borehole_eew_bypass_sec 秒以内にEEW(実報)を受信していれば
地中確認を免除して発報する(生活ノイズにEEWは付随しない)。

session/time をモックし、本番 NiedMonitor._process_current_image を通し駆動:
  Phase1: 地表活動(1.5)+地中静穏, EEW無し → 抑制(発報ゼロ) ※ノイズ排除の維持
  Phase2: EEW受信(force_alert)          → 次フレームで発報 ※バイパス
  Phase2b: クールダウン(60s)内          → 再発報なし
  Phase3: バイパス窓+クールダウン経過後  → 再び抑制 ※ゲート復帰

使い方:
    python eq_test/test_eew_bypass.py
"""
import os, io, json, sys
from PIL import Image

# Windows コンソール(cp932)でも記号を出力できるよう UTF-8 に再設定
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.append(PROJECT_ROOT)
sys.path.append(os.path.join(PROJECT_ROOT, "eq_src"))

from eq_config import eq_config as config
import eq_monitor_nied as m


# --- 時刻モック(モジュール内の time.* を差し替え) ---
class FakeTime:
    t = 1000.0

    @classmethod
    def time(cls):
        return cls.t

    @classmethod
    def sleep(cls, s):
        pass


m.time = FakeTime

# --- 画像準備: color_mapからゲート帯(1.3〜3.0)の色を選ぶ ---
with open(config.COLOR_MAP_PATH, encoding="utf-8") as f:
    cmap = json.load(f)
active_rgb = None
for item in cmap:
    if 1.5 <= item["Intensity"] <= 2.5:
        active_rgb = (item["R"], item["G"], item["B"])
        active_int = item["Intensity"]
        break
assert active_rgb, "color map にゲート帯の色が見つからない"

SIZE = (352, 400)  # 実際のNIEDモニタ画像サイズ


def to_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


BYTES_ACTIVE = to_bytes(Image.new("RGB", SIZE, active_rgb))
BYTES_QUIET = to_bytes(Image.new("RGB", SIZE, (255, 255, 255)))  # マップ外=静穏


class FakeResp:
    def __init__(self, content, code=200):
        self.content = content
        self.status_code = code


class FakeSession:
    def get(self, url, timeout=None):
        if ".jma_b." in url:
            # 地中: 200で静穏画像(フェイルオープンではなく「取得成功・未確認」)
            return FakeResp(BYTES_QUIET)
        return FakeResp(BYTES_ACTIVE)

    def close(self):
        pass


def main():
    settings = {
        "radius_pixel": 10,
        "trigger_pixels": 10,
        "trigger_intensity": 1.3,
        "alert_end_grace_sec": 45,
        "borehole_enabled": True,
        "borehole_confirm_intensity": 0.5,
        "borehole_confirm_pixels": 2,
        "borehole_override_intensity": 3.0,
        "borehole_eew_bypass_sec": 180,
        "plum_enabled": False,
        "eta_correction_enabled": False,
        "site_calibration_enabled": False,
        "arrival_failsafe_enabled": False,
    }

    alerts = []
    mon = m.NiedMonitor(settings, callback=lambda d: alerts.append(d), saves_history=False)
    mon.session = FakeSession()

    def step():
        FakeTime.t += 1.0
        ok = mon._process_current_image(int(FakeTime.t))
        assert ok, f"frame failed at t={FakeTime.t}"

    print(f"surface active color -> intensity {active_int} (gate band 1.3-3.0)")

    # Phase1: EEW無し → 地中未確認で抑制され続ける
    for _ in range(10):
        step()
    assert len(alerts) == 0, f"EEW無しで発報された: {len(alerts)}"
    print(f"Phase1 OK: 10フレーム全て抑制 (alerts={len(alerts)})")

    # Phase2: EEW受信 → バイパスで発報
    mon.force_alert()  # eq_main が実EEW受信時に呼ぶのと同じ経路
    step()
    assert len(alerts) == 1, f"EEW受信後に発報されない: {len(alerts)}"
    print(f"Phase2 OK: EEW受信の次フレームで発報 (alerts={len(alerts)})")

    # Phase2b: クールダウン(60s)内は再発報しない
    for _ in range(5):
        step()
    assert len(alerts) == 1, "クールダウン内に再発報"
    print("Phase2b OK: クールダウン内の再発報なし")

    # Phase3: バイパス窓(180s)+クールダウン(60s)経過後 → ゲート復帰
    FakeTime.t += 200.0
    for _ in range(5):
        step()
    assert len(alerts) == 1, f"バイパス窓経過後もゲートが無効のまま: {len(alerts)}"
    print("Phase3 OK: 窓経過後は地中ANDゲートが復帰(抑制)")

    print("ALL OK: suppress without EEW / fire with EEW / gate restored after window")


if __name__ == "__main__":
    main()
