# -*- coding: utf-8 -*-
"""
アラート保存期間のライフサイクル検証テスト(終了ヒステリシス + 末尾トリム)。

背景: 旧実装は「全国最大<1.0のコマが来た瞬間に終了」だったため、色量子化で
揺れ継続中でも一瞬<1.0のコマが混じると即終了→数秒後に再開し、1地震が複数の
eqlogフォルダに分裂していた(実ログ: 20260530 06:38:42開始→06:39:12終了→06:39:14再開)。

修正:
  1. 終了ヒステリシス: 最終活動(全国最大>=1.0 or EEW外部トリガー)から
     alert_end_grace_sec(既定45s)無活動が続いた時のみ終了。最低継続30sは維持。
  2. 末尾トリム(オプション): alert_trim_quiet_tail=true なら終了時に
     最終活動+alert_tail_keep_sec(既定10s)より後の静穏コマを削除して動画生成。
     false(既定)はグレース期間の全コマを残す。

session/time をモックし、本番 NiedMonitor._process_current_image を通し駆動:
  Part1(ヒステリシス): 静穏3s→活動35s→静穏35s(ディップ)→活動10s→静穏46s
    → 開始1回/終了1回(ディップで分裂しない)、最終活動から45s静穏後に終了
  Part2(末尾トリム): 実ファイル保存込みで trim=True/False を駆動
    → True: 最終活動+10sより後が削除され、残りは毎秒連続
    → False: グレース期間の尾(+44s)が全て残る

使い方:
    python eq_test/test_alert_lifecycle.py
"""
import os, io, json, sys, datetime, shutil, tempfile
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

# --- 画像準備: color_mapからトリガー帯(>=1.3)の色を選ぶ ---
with open(config.COLOR_MAP_PATH, encoding="utf-8") as f:
    cmap = json.load(f)
active_rgb = None
for item in cmap:
    if 1.5 <= item["Intensity"] <= 2.5:
        active_rgb = (item["R"], item["G"], item["B"])
        break
assert active_rgb, "color map にトリガー帯の色が見つからない"

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
    current = "quiet"

    def get(self, url, timeout=None):
        if ".jma_b." in url:
            return FakeResp(b"", 404)
        return FakeResp(BYTES_ACTIVE if self.current == "active" else BYTES_QUIET)

    def close(self):
        pass


BASE_SETTINGS = {
    "radius_pixel": 10,
    "trigger_pixels": 10,
    "trigger_intensity": 1.3,
    "alert_end_grace_sec": 45,
    "alert_tail_keep_sec": 10,
    "borehole_enabled": False,
    "plum_enabled": False,
    "eta_correction_enabled": False,
    "site_calibration_enabled": False,
    "arrival_failsafe_enabled": False,
}


def make_monitor(settings: dict, saves_history: bool):
    mon = m.NiedMonitor(settings, callback=lambda d: None, saves_history=saves_history)
    if saves_history:
        mon.history_saver = None  # 履歴保存スレッドへのpushを止める(本テストの対象外)
    mon.session = FakeSession()
    return mon


def step(mon, state):
    FakeTime.t += 1.0
    mon.session.current = state
    assert mon._process_current_image(int(FakeTime.t)), f"frame failed t={FakeTime.t}"


def test_hysteresis():
    """Part1: 終了ヒステリシス(ディップ橋渡し・分裂防止)"""
    FakeTime.t = 1000.0
    mon = make_monitor(dict(BASE_SETTINGS), saves_history=False)

    starts, ends = [], []
    prev_active = False

    def observe():
        nonlocal prev_active
        if mon.is_alert_active and not prev_active:
            starts.append(FakeTime.t)
        if prev_active and not mon.is_alert_active:
            ends.append(FakeTime.t)
        prev_active = mon.is_alert_active

    for _ in range(3):
        step(mon, "quiet"); observe()
    assert not mon.is_alert_active, "静穏時にアラートが立った"

    for _ in range(35):
        step(mon, "active"); observe()
    assert mon.is_alert_active, "活動中にアラートが開始しない"

    # 35秒ディップ: grace(45s)未満なので継続していること(旧実装はここで終了→分裂)
    for i in range(35):
        step(mon, "quiet"); observe()
        assert mon.is_alert_active, f"ディップ{i+1}s で早期終了(分裂の再現)"

    for _ in range(10):
        step(mon, "active"); observe()
    assert mon.is_alert_active

    # 46秒静穏: 最終活動から45s経過時点で終了すること
    for _ in range(46):
        step(mon, "quiet"); observe()
    assert not mon.is_alert_active, "grace経過後も終了しない"

    assert len(starts) == 1, f"開始が{len(starts)}回(分裂)"
    assert len(ends) == 1, f"終了が{len(ends)}回"
    quiet_len = ends[0] - (starts[0] + 35 + 35 + 10 - 1)  # 最終活動フレームからの静穏秒数
    assert 45 <= quiet_len <= 46, f"終了までの静穏時間が想定外: {quiet_len}"
    print(f"Part1 OK: 開始1回/終了1回, 35sディップ橋渡し, 最終活動+{quiet_len:.0f}sで終了")


def run_trim_lifecycle(trim: bool, workdir: str):
    """静穏3s→活動35s→静穏60s を実ファイル保存込みで駆動し、
    (surfaceのデータ時刻list, 最終活動dt, 動画生成呼び出しlist) を返す"""
    FakeTime.t = 1000.0
    orig_dir = m.config.MONITOR_IMAGE_DIR
    m.config.MONITOR_IMAGE_DIR = workdir  # 保存先を一時ディレクトリへ
    try:
        settings = dict(BASE_SETTINGS, alert_trim_quiet_tail=trim)
        mon = make_monitor(settings, saves_history=True)
        video_calls = []
        mon._start_video_generation = lambda d: video_calls.append(d)

        for _ in range(3):
            step(mon, "quiet")
        for _ in range(35):
            step(mon, "active")
        assert mon.is_alert_active
        for _ in range(60):
            step(mon, "quiet")
        assert not mon.is_alert_active, "アラートが終了していない"

        last_active_dt = datetime.datetime.strptime(mon.alert_last_active_time_str, "%Y%m%d%H%M%S")
        surf = os.path.join(video_calls[0], "surface")
        dts = sorted(
            datetime.datetime.strptime(f.split("_")[2], "%Y%m%d%H%M%S")
            for f in os.listdir(surf)
        )
        return dts, last_active_dt, video_calls
    finally:
        m.config.MONITOR_IMAGE_DIR = orig_dir


def test_tail_trim():
    """Part2: 末尾トリムのON/OFF"""
    # trim=True: 最終活動+keep_sec でカットされ、残りは毎秒連続
    tmp1 = tempfile.mkdtemp(prefix="eqtrim_on_")
    try:
        dts, last_dt, calls = run_trim_lifecycle(True, tmp1)
        cutoff = last_dt + datetime.timedelta(seconds=10)
        assert len(calls) == 1, "動画生成が1回でない"
        assert max(dts) == cutoff, f"末尾が想定と違う: max={max(dts)}, cutoff={cutoff}"
        expected = int((max(dts) - min(dts)).total_seconds()) + 1
        assert len(dts) == expected, f"コマ欠落: {len(dts)} != {expected}"
        print(f"Part2 OK(trim=True): {len(dts)}コマ連続, 末尾は最終活動+10sでカット")
    finally:
        shutil.rmtree(tmp1, ignore_errors=True)

    # trim=False(既定): グレース期間の尾が全て残る
    tmp2 = tempfile.mkdtemp(prefix="eqtrim_off_")
    try:
        dts, last_dt, calls = run_trim_lifecycle(False, tmp2)
        tail_kept = int((max(dts) - last_dt).total_seconds())
        assert tail_kept >= 40, f"trim無効なのに尾が残っていない: +{tail_kept}s"
        expected = int((max(dts) - min(dts)).total_seconds()) + 1
        assert len(dts) == expected, f"コマ欠落: {len(dts)} != {expected}"
        print(f"Part2 OK(trim=False): {len(dts)}コマ連続, 末尾は最終活動+{tail_kept}sまで保持")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)


if __name__ == "__main__":
    test_hysteresis()
    test_tail_trim()
    print("ALL OK: hysteresis bridges dips / tail trim follows config")
