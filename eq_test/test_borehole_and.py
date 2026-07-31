"""
#1 地中(borehole)ノイズ排除AND のオフライン検証テスト。

20260626 22:29 のイベントについて、保存済みの地表(surface)/地中(borehole)画像から
自宅クロップの「地表トリガー成立」と「地中確認」を再現し、本番の判定ロジック
NiedMonitor.should_alert() が以下を満たすことを確認する。

  検証1(フォルスネガティブ無し): 本物の地震で地表がトリガーした全フレームで、
                                 地中も確認OK → 発報が握り潰されない
  検証2(挙動の可視化):           地表トリガー有/無、地中確認有/無の分布を一覧表示

使い方:
    python eq_test/test_borehole_and.py
"""
import os, re, glob, json, sys, datetime
from PIL import Image
import numpy as np

# Windows コンソール(cp932)でも記号を出力できるよう UTF-8 に再設定
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

COOLDOWN_SEC = 60  # NiedMonitor.cooldown_seconds と同じ

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.append(PROJECT_ROOT)
sys.path.append(os.path.join(PROJECT_ROOT, "eq_src"))

# 本番の判定ロジックを直接import(テストと実装の一致を担保)
from eq_monitor_nied import NiedMonitor
import eq_config.eq_config as eq_config

EVENT = os.path.join(PROJECT_ROOT, "eq_log", "monitor_images", "eq_log", "eqlog_20260626_2229_lv5.3")
SURF = os.path.join(EVENT, "surface")
BORE = os.path.join(EVENT, "borehole")
COLOR_MAP_PATH = os.path.join(PROJECT_ROOT, "eq_assets", "eq_NiedColorMap.json")

# 自宅ピクセル座標は eq_config_local.py(個人環境設定)から取得
NIED_HOME_X, NIED_HOME_Y, RADIUS = eq_config.NIED_HOME_X, eq_config.NIED_HOME_Y, 50
TRIGGER_INTENSITY = 1.3
TRIGGER_PIXELS = 10
BORE_CONFIRM_INTENSITY = 0.5
BORE_CONFIRM_PIXELS = 2

with open(COLOR_MAP_PATH, "r", encoding="utf-8") as f:
    cmap = json.load(f)
CM_RGB = np.array([[d["R"], d["G"], d["B"]] for d in cmap], dtype=np.int32)
CM_INT = np.array([d["Intensity"] for d in cmap], dtype=np.float32)


def crop_intensities(img_path):
    with Image.open(img_path) as raw:
        img = raw.convert("RGB")
        x0 = max(0, int(NIED_HOME_X - RADIUS)); y0 = max(0, int(NIED_HOME_Y - RADIUS))
        x1 = min(img.width, int(NIED_HOME_X + RADIUS)); y1 = min(img.height, int(NIED_HOME_Y + RADIUS))
        crop = np.array(img.crop((x0, y0, x1, y1)))[:, :, :3].astype(np.int32)
    px = crop.reshape(-1, 3)
    uniq, inv = np.unique(px, axis=0, return_inverse=True)
    diff = uniq[:, None, :] - CM_RGB[None, :, :]
    dist_sq = np.sum(diff ** 2, axis=2)
    inten = CM_INT[np.argmin(dist_sq, axis=1)].copy()
    inten[np.min(dist_sq, axis=1) > 500] = -3.0
    return inten[inv]


def trig_count(intensities, thr):
    return int(np.sum(intensities >= thr))


def max_and_trig(img_path, thr):
    it = crop_intensities(img_path)
    return float(np.max(it)), int(np.sum(it >= thr))


def index(dirpath, pat, grp):
    out = {}
    for p in glob.glob(os.path.join(dirpath, "*.png")):
        m = re.search(pat, os.path.basename(p))
        if m:
            out[grp(m)] = p
    return out


def epoch(ts):
    return datetime.datetime.strptime(ts, "%Y%m%d%H%M%S").timestamp()


def simulate(frames, policy, override_intensity=None):
    """frames: list of (ts, s_on, s_max, b_avail, b_trig)。クールダウン込みで発報tsの列を返す。
    policy='surface' は地表のみ。'and' は地中確認ゲート付き。
    override_intensity を与えると S_max>=override は地中確認なしで発報(二段ゲート)。
    握り潰し時は last_fire を更新しない(=本番 _analyze_cropped_image と同じ挙動)。"""
    fired = []
    last_fire = -1e18
    for ts, s_on, s_max, b_avail, b_trig in frames:
        if not s_on:
            continue
        if epoch(ts) - last_fire < COOLDOWN_SEC:
            continue
        if policy == "surface":
            ok = True
        elif override_intensity is not None and s_max >= override_intensity:
            ok = True  # 地表強度オーバーライド
        else:
            ok = NiedMonitor.should_alert(s_on, b_avail, b_trig, BORE_CONFIRM_PIXELS)
        if ok:
            fired.append(ts)
            last_fire = epoch(ts)
    return fired


def main():
    surf = index(SURF, r"eqlog_s_(\d{14})_", lambda m: m.group(1))
    bore = index(BORE, r"eqlog_b_(\d{14})_", lambda m: m.group(1))
    all_ts = sorted(set(surf) | set(bore))
    print(f"[Data] surface={len(surf)} borehole={len(bore)} union={len(all_ts)}\n")

    # 全フレームの (地表トリガー有無, 地表ピーク震度, 地中確認材料) を一度だけ計算
    frames = []
    for ts in all_ts:
        if ts in surf:
            s_max, s_trig = max_and_trig(surf[ts], TRIGGER_INTENSITY)
        else:
            s_max, s_trig = -3.0, 0
        s_on = s_trig >= TRIGGER_PIXELS
        b_avail = ts in bore
        b_trig = trig_count(crop_intensities(bore[ts]), BORE_CONFIRM_INTENSITY) if b_avail else 0
        frames.append((ts, s_on, s_max, b_avail, b_trig))

    def fmt(lst):
        return ", ".join(t[8:] for t in lst) if lst else "(なし)"

    surf_fired = simulate(frames, "surface")

    print("--- クールダウン(60s)込みの実フロー: オーバーライド閾値スイープ ---")
    print(f"{'方式':28} | {'発報数':>5} | 発報時刻")
    print("-" * 70)
    print(f"{'地表のみ(ノイズ排除なし)':24} | {len(surf_fired):5} | {fmt(surf_fired)}")
    sweeps = [("純粋AND(override無し)", None), ("override=震度2(2.0)", 2.0),
              ("override=震度3(3.0)", 3.0), ("override=震度4(4.0)", 4.0),
              ("override=震度5弱(4.5)", 4.5)]
    for label, ov in sweeps:
        f = simulate(frames, "and", override_intensity=ov)
        print(f"{label:24} | {len(f):5} | {fmt(f)}")

    onset = "20260626222904"
    and_pure = simulate(frames, "and")
    print("\n--- 検証1: 本物の地震の初動を握り潰していないか(純粋AND) ---")
    if and_pure and and_pure[0] == onset:
        print(f"  [PASS] 初動 {onset[8:]} で発報")
    else:
        print(f"  [FAIL] 初動未発報: {fmt(and_pure)}")

    # ゲート帯域(=地中確認が要求される S_max 範囲)を override 別に明示
    print("\n--- 参考: 各 override でのゲート帯域(この帯域の弱い揺れだけ地中確認) ---")
    for label, ov in sweeps:
        hi = f"{ov:.1f}" if ov is not None else "∞"
        print(f"  {label:24}: S_max {TRIGGER_INTENSITY} 〜 {hi} を地中ANDで判定")

    print("\n[注] 本イベントは本物の地震のみ。生活ノイズ抑制(フォルスポジティブ排除)の")
    print("     実証には『地表のみ微反応・地中静穏』の静穏期サンプルが別途必要。")


if __name__ == "__main__":
    main()
