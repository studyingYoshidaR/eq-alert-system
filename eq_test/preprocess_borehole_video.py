"""
地中(borehole)モニタ生動画 → 既存フォーマットの毎秒PNG 前処理パイプライン。

処理:
  1. 動画を毎秒(=毎フレーム)切り出し(5fps・1コマ=実時間1秒)
  2. 左上文字・右カラーバー・右下ロゴを領域除外
  3. 震度ドット(彩度高+カラーマップ一致)のみ抽出、それ以外は透過
  4. 校正済み変換でベース画像(352x400)へ整列・重畳
  5. eqlog_b_yyyymmddhhmmss_lv{全国最大震度}.png で保存

校正変換はNIED kyoshin "rsi" 動画(540x556)→kmoniベース(352x400)用に
ベース局位置を正解として自己校正した値(同一product/projectionなら再利用可)。

使い方:
    python eq_test/preprocess_borehole_video.py
"""
import cv2, json, os, datetime, sys
import numpy as np
from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# --- 入出力 ---
VIDEO = os.path.join(ROOT, "eq_test", "raw_eqvideo", "20240101161000_rsi_b.mp4")
BASE = os.path.join(ROOT, "eq_test", "nied_monitor_baseimage_b.png")
OUT_DIR = os.path.join(ROOT, "eq_test", "test_monitor_images", "eqlog_20240101_1608_lv7.0", "borehole")
START = datetime.datetime(2024, 1, 1, 16, 8, 12)   # 1コマ目の実時刻

# --- 校正済み変換 (video px -> base px) ---
SX, SY, OX, OY = 1.01000, 1.00000, -129.0, 1.0

# --- UI除外ボックス [y0:y1, x0:x1] ---
def excl_mask(h, w):
    e = np.zeros((h, w), bool)
    e[0:32, 0:235] = True      # 左上文字
    e[:, 470:] = True          # 右カラーバー
    e[460:, 285:470] = True    # 右下ロゴ
    return e

cmap = json.load(open(os.path.join(ROOT, "eq_assets", "eq_NiedColorMap.json"), encoding="utf-8"))
CM = np.array([[c["R"], c["G"], c["B"]] for c in cmap])
CI = np.array([c["Intensity"] for c in cmap], float)


def colored_pixels(rgb, ex):
    """彩度高+カラーマップ一致の震度ドット座標と震度を返す。"""
    sat = rgb.max(2) - rgb.min(2)
    ys, xs = np.where((sat > 50) & (~ex))
    if len(xs) == 0:
        return ys, xs, np.array([])
    px = rgb[ys, xs].astype(int)
    d = ((px[:, None, :] - CM[None, :, :]) ** 2).sum(2)
    nd = d.min(1)
    ok = nd < 4000
    inten = CI[d.argmin(1)]
    return ys[ok], xs[ok], inten[ok]


def main():
    base = np.array(Image.open(BASE).convert("RGBA"))
    os.makedirs(OUT_DIR, exist_ok=True)
    cap = cv2.VideoCapture(VIDEO)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    ex = None
    saved = 0
    for idx in range(n):
        ok, fr = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)
        if ex is None:
            ex = excl_mask(*rgb.shape[:2])
        ys, xs, inten = colored_pixels(rgb, ex)

        out = base.copy()
        for y, x in zip(ys, xs):
            bx = int(round(OX + x * SX)); by = int(round(OY + y * SY))
            if 0 <= bx < 352 and 0 <= by < 400:
                out[by, bx, :3] = rgb[y, x]; out[by, bx, 3] = 255

        # 全国最大震度(色付きドットの最大)。色付きが無ければ平常扱い -3.0
        gmax = float(inten.max()) if len(inten) else -3.0
        ts = (START + datetime.timedelta(seconds=idx)).strftime("%Y%m%d%H%M%S")
        Image.fromarray(out, "RGBA").save(os.path.join(OUT_DIR, f"eqlog_b_{ts}_lv{gmax:.1f}.png"))
        saved += 1
        if idx % 60 == 0:
            print(f"  {idx}/{n}  {ts} lv{gmax:.1f}")
    cap.release()
    print(f"完了: {saved} フレームを {OUT_DIR} に保存")


if __name__ == "__main__":
    main()
