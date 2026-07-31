"""
#7 予測精度 評価・定数フィット ハーネス (オフライン)

蓄積イベント(EEWログ + モニタ地表画像)を走査し、各イベントについて
  - EEW予測の自宅震度  vs  実測(自宅周辺リング観測)
  - EEW予測のS波到達   vs  実測の前線到達
の誤差を一覧化。集計バイアスと、勝俣式(B3)定数の較正示唆を出す。

複数イベントが集まるほど有効。1イベントでは構造確認のみ。

使い方:
    python eq_test/eval_prediction_accuracy.py
"""
import os, re, glob, json, sys, math, datetime, statistics

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.append(PROJECT_ROOT)
sys.path.append(os.path.join(PROJECT_ROOT, "eq_src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PIL import Image
from eq_ring_monitor import RingMonitor
from eq_utils import GeoUtils
import eq_config.eq_config as config
# 本番同等のEEW予測パーサ(予測震度・S波到達ts・独自算出判定を含む)を再利用
from eq_run_simulation2 import parse_eew_file

MON_BASE = os.path.join(PROJECT_ROOT, "eq_log", "monitor_images", "eq_log")
EEW_BASE = os.path.join(PROJECT_ROOT, "eq_log", "eq_eew_jma")
COLOR_MAP_PATH = os.path.join(PROJECT_ROOT, "eq_assets", "eq_NiedColorMap.json")

ARRIVAL_INTENSITY = 2.0   # 実測到達とみなす震度(自宅10km圏)
PLUM_RADIUS = 30          # 実測震度の代表(自宅30km圏ピーク)

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

def epoch(ts14):
    return datetime.datetime.strptime(ts14, "%Y%m%d%H%M%S").timestamp()


def list_monitor_events():
    """[(event_id 'YYYYMMDD_HHMM', surface_dir)] を返す。同一分の重複ディレクトリは
    地表フレーム数が最多のものを採用して二重計上を防ぐ。"""
    by_id = {}  # event_id -> (n_frames, surf_dir)
    if not os.path.isdir(MON_BASE):
        return []
    for d in sorted(os.listdir(MON_BASE)):
        m = re.match(r"eqlog_(\d{8})_(\d{4})_", d)
        if not m:
            continue
        surf = os.path.join(MON_BASE, d, "surface")
        if not os.path.isdir(surf):
            surf = os.path.join(MON_BASE, d)  # 旧構造フォールバック
        eid = f"{m.group(1)}_{m.group(2)}"
        n = len(glob.glob(os.path.join(surf, "eqlog_s_*.png")))
        if eid not in by_id or n > by_id[eid][0]:
            by_id[eid] = (n, surf)
    return [(eid, sd) for eid, (n, sd) in sorted(by_id.items())]


def find_final_eew(date8, hhmm):
    """同日±2分のEEWファイル群から最終報(報数最大)のパスを返す。"""
    date_dir = os.path.join(EEW_BASE, date8)
    if not os.path.isdir(date_dir):
        return None
    target = datetime.datetime.strptime(date8 + hhmm + "00", "%Y%m%d%H%M%S")
    best = None  # (rep, path)
    for f in os.listdir(date_dir):
        m = re.match(r"(\d{14})_(\d+)\.json", f)
        if not m:
            continue
        ft = datetime.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
        if abs((ft - target).total_seconds()) > 120:
            continue
        rep = int(m.group(2))
        if best is None or rep > best[0]:
            best = (rep, os.path.join(date_dir, f))
    return best[1] if best else None


def observe(surf_dir, rm, center):
    """地表フレーム列から (obs_int30, obs_int10, arrival_ts) を返す。"""
    frames = {}
    for p in glob.glob(os.path.join(surf_dir, "eqlog_s_*.png")):
        m = re.search(r"eqlog_s_(\d{14})_", os.path.basename(p))
        if m: frames[m.group(1)] = p
    peak30 = peak10 = -3.0
    arrival = None
    for ts in sorted(frames):
        with Image.open(frames[ts]) as raw:
            img = raw.convert("RGB")
            r30 = rm.max_within(img, match_fn, center, 30)
            r10 = rm.max_within(img, match_fn, center, 10)
        peak30 = max(peak30, r30); peak10 = max(peak10, r10)
        if arrival is None and r10 >= ARRIVAL_INTENSITY:
            arrival = epoch(ts)
    return peak30, peak10, arrival


def main():
    rm = RingMonitor(config.HOME_LAT, config.HOME_LON, radii_km=(10, 20, 30))
    center = (config.NIED_HOME_X, config.NIED_HOME_Y)
    gc = getattr(config, "HOME_GROUND_CORRECTION", 1.0)

    by_eew = {}  # eew_path -> row (同一地震=同一最終EEW報で重複排除、観測の強い方を採用)
    for event_id, surf_dir in list_monitor_events():
        date8, hhmm = event_id.split("_")
        eew_path = find_final_eew(date8, hhmm)
        if not eew_path:
            continue
        data = parse_eew_file(eew_path)
        if data is None:
            continue
        raw = json.loads(data.original_text)
        M = raw.get("Magunitude", 0.0)
        depth = float(raw.get("Depth", 0.0))
        eq_lat = raw.get("Latitude", 0.0); eq_lon = raw.get("Longitude", 0.0)
        epi = GeoUtils.calculate_distance(eq_lat, eq_lon, config.HOME_LAT, config.HOME_LON)
        hypo = math.sqrt(epi**2 + depth**2)

        obs30, obs10, arrival = observe(surf_dir, rm, center)
        obs_scale = RingMonitor.intensity_to_home_scale(obs30)

        # 勝俣式(現定数, gc) の理論震度と残差(=gc較正のヒント)
        calc = GeoUtils.calc_fallback_intensity(M, hypo, ground_correction=gc,
                                                eq_lat=eq_lat, eq_lon=eq_lon,
                                                home_lat=config.HOME_LAT, home_lon=config.HOME_LON)
        residual = obs30 - calc  # 観測 - 理論(正なら理論が過小)

        eta_err = (data.s_wave_arrival_ts - arrival) if (arrival and data.s_wave_arrival_ts > 0) else None

        row = dict(
            event=event_id, M=M, depth=depth, epi=epi, hypo=hypo,
            pred_scale=data.predicted_home_scale, custom=data.is_custom_prediction,
            obs30=obs30, obs10=obs10, obs_scale=obs_scale,
            int_err_scale=data.predicted_home_scale - obs_scale,
            calc=calc, residual=residual, eta_err=eta_err,
        )
        prev = by_eew.get(eew_path)
        if prev is None or row["obs30"] > prev["obs30"]:
            by_eew[eew_path] = row

    rows = sorted(by_eew.values(), key=lambda r: r["event"])
    if not rows:
        print("評価対象イベントが見つかりません(EEW+モニタのペア無し)。")
        return

    print(f"=== 予測精度 評価 (n={len(rows)} events, HOME_GROUND_CORRECTION={gc}) ===\n")
    hdr = (f"{'event':16} {'M':>4} {'dep':>4} {'epi':>5} | {'pred':>4} {'src':>4} "
           f"{'obs30':>5} {'obsScl':>6} {'ΔScl':>5} | {'calc':>5} {'resid':>6} {'etaErr':>7}")
    print(hdr); print("-" * len(hdr))
    for r in rows:
        src = "独自" if r["custom"] else "公式"
        eta = f"{r['eta_err']:+.1f}s" if r["eta_err"] is not None else "  -- "
        print(f"{r['event']:16} {r['M']:4.1f} {r['depth']:4.0f} {r['epi']:5.0f} | "
              f"{r['pred_scale']:4} {src:>4} {r['obs30']:5.1f} {r['obs_scale']:6} "
              f"{r['int_err_scale']:+5} | {r['calc']:5.1f} {r['residual']:+6.1f} {eta:>7}")

    # --- 集計 ---
    int_errs = [r["int_err_scale"] for r in rows]
    residuals = [r["residual"] for r in rows]
    eta_errs = [r["eta_err"] for r in rows if r["eta_err"] is not None]
    print("\n=== 集計 ===")
    print(f"  予測震度バイアス(pred-obs, scale): 平均{statistics.mean(int_errs):+.1f} "
          f"中央{statistics.median(int_errs):+.1f}  (+はEEW過大)")
    if eta_errs:
        print(f"  到達時刻バイアス(EEW-実測): 平均{statistics.mean(eta_errs):+.1f}s "
              f"中央{statistics.median(eta_errs):+.1f}s  (+はEEWが早い)")

    print("\n=== 勝俣式(B3) 定数フィット示唆 ===")
    med_res = statistics.median(residuals)
    print(f"  残差(観測-理論)中央値 = {med_res:+.1f}")
    print(f"  → HOME_GROUND_CORRECTION の示唆値 ≈ {gc:+.1f} {'+' if med_res>=0 else '-'} {abs(med_res):.1f}"
          f" = {gc + med_res:.1f}")
    if len(rows) >= 6:
        # 観測 ≈ 1.5*M + b*log10(hypo) + c の簡易最小二乗(M固定1.5前提でb,c)、参考値
        xs = [math.log10(r["hypo"]) for r in rows]
        ys = [r["obs30"] - 1.5 * r["M"] for r in rows]
        n = len(xs); sx = sum(xs); sy = sum(ys); sxx = sum(x*x for x in xs); sxy = sum(x*y for x, y in zip(xs, ys))
        b = (n*sxy - sx*sy) / (n*sxx - sx*sx)
        c = (sy - b*sx) / n
        print(f"  [回帰 n>=6] 観測≈1.5M {b:+.2f}*log10(D) {c:+.2f}  (現行: -3.2*log10(D) -0.8+gc)")
    else:
        print(f"  (多変数回帰はイベント数 n>={6} で実行。現在 n={len(rows)} のため残差較正のみ)")

    print("\n[注] 単一/少数イベントでは示唆は暫定。データ蓄積後に再実行して定数を確定する。")


if __name__ == "__main__":
    main()
