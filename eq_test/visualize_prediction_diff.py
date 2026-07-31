"""#8 予測 vs 実測 差分可視化 — 距離減衰プロファイル (オフライン HTML レポート)

#7 (eval_prediction_accuracy) が数値で示した仮説
  「勝俣式の距離減衰係数 -3.2 は急峻さ不足 (実測は -3.6 前後)」
を、イベント散布図で目視・裏取りするための道具。

各イベントを
  x = log10(震源距離 D[km])
  y = マグニチュード正規化観測震度 = obs30 - 1.5*M
の 1 点として置き、次を重ねる:
  - 現行モデル線   y = -3.2*log10(D) - 0.8 + gc     (勝俣式の距離項)
  - 回帰フィット線 y = b*log10(D) + c   (最小二乗; 全点 / クリーン点 の 2 通り)

点の色は 独自算出(=勝俣式フォールバックが実際に効くイベント) / 公式 で分け、
ノイズフロア(obs30 < 1.0)の点は淡色にして「回帰の信頼度が落ちる領域」を明示する。

予測系(本番コード)には一切触れない純オフライン。#7 の抽出関数を再利用する。

使い方:
    python eq_test/visualize_prediction_diff.py [出力パス.html]
既定出力: eq_log/prediction_diff.html
"""
import os
import sys
import math
import json
import html
import datetime
import statistics

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.append(PROJECT_ROOT)
sys.path.append(os.path.join(PROJECT_ROOT, "eq_src"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# #7 ハーネスの抽出ロジックをそのまま共有 (DRY)
from eval_prediction_accuracy import (
    list_monitor_events, find_final_eew, observe, match_fn,
)
from eq_ring_monitor import RingMonitor
from eq_utils import GeoUtils
import eq_config.eq_config as config
from eq_run_simulation2 import parse_eew_file

NOISE_FLOOR = 1.0          # obs30 がこれ未満 = ノイズフロア付近 (回帰の信頼度低)
PLUM_RADIUS = 30           # 実測震度の代表 (自宅 30km 圏ピーク)
CURRENT_SLOPE = -3.2       # 現行 勝俣式の距離減衰係数
CURRENT_BASE = -0.8        # 現行 勝俣式の定数項 (gc 前)


# ── データ収集 ────────────────────────────────────────────────
def collect_rows():
    """蓄積イベントを走査し、散布図・表に必要な行のリストを返す。
    同一地震 (同一最終 EEW 報) は観測の強い方を採用して重複排除。"""
    rm = RingMonitor(config.HOME_LAT, config.HOME_LON, radii_km=(10, 20, 30))
    center = (config.NIED_HOME_X, config.NIED_HOME_Y)
    gc = getattr(config, "HOME_GROUND_CORRECTION", 1.0)

    by_eew = {}
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
        eq_lat = raw.get("Latitude", 0.0)
        eq_lon = raw.get("Longitude", 0.0)
        epi = GeoUtils.calculate_distance(eq_lat, eq_lon, config.HOME_LAT, config.HOME_LON)
        hypo = math.sqrt(epi ** 2 + depth ** 2)
        if hypo <= 0:
            continue

        obs30, obs10, arrival = observe(surf_dir, rm, center)

        row = dict(
            event=event_id, M=M, depth=depth, epi=epi, hypo=hypo,
            logD=math.log10(hypo),
            obs30=obs30,
            y_norm=obs30 - 1.5 * M,
            custom=data.is_custom_prediction,
            clean=(obs30 >= NOISE_FLOOR),
        )
        prev = by_eew.get(eew_path)
        if prev is None or row["obs30"] > prev["obs30"]:
            by_eew[eew_path] = row

    rows = sorted(by_eew.values(), key=lambda r: r["event"])
    return rows, gc


def fit_line(points):
    """(x, y) 点列に最小二乗直線 y = b*x + c を当て (b, c) を返す。不能なら None。"""
    n = len(points)
    if n < 2:
        return None
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    den = n * sxx - sx * sx
    if abs(den) < 1e-9:
        return None
    b = (n * sxy - sx * sy) / den
    c = (sy - b * sx) / n
    return b, c


# ── SVG 散布図 ────────────────────────────────────────────────
def build_scatter_svg(rows, gc, fit_all, fit_clean):
    W, H = 880, 540
    ML, MR, MT, MB = 66, 150, 28, 60
    PW, PH = W - ML - MR, H - MT - MB

    cur_b, cur_c = CURRENT_SLOPE, CURRENT_BASE + gc

    logs = [r["logD"] for r in rows]
    xmin, xmax = min(logs) - 0.06, max(logs) + 0.06

    def line_y(fit, x):
        return fit[0] * x + fit[1] if fit else None

    ys = [r["y_norm"] for r in rows]
    for x in (xmin, xmax):
        ys.append(cur_b * x + cur_c)
        if fit_all:
            ys.append(line_y(fit_all, x))
        if fit_clean:
            ys.append(line_y(fit_clean, x))
    ymin, ymax = min(ys), max(ys)
    pad = (ymax - ymin) * 0.08 or 1.0
    ymin -= pad
    ymax += pad

    def sx(logd):
        return ML + (logd - xmin) / (xmax - xmin) * PW

    def sy(y):
        return MT + (ymax - y) / (ymax - ymin) * PH

    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
             f'aria-label="距離減衰プロファイル散布図" font-family="var(--font)">']

    # --- グリッド + 軸目盛 ---
    d_ticks = [d for d in (30, 50, 100, 200, 300, 500, 700, 1000)
               if xmin <= math.log10(d) <= xmax]
    for d in d_ticks:
        x = sx(math.log10(d))
        parts.append(f'<line x1="{x:.1f}" y1="{MT}" x2="{x:.1f}" y2="{MT+PH}" '
                     f'stroke="var(--grid)" stroke-width="1"/>')
        parts.append(f'<text x="{x:.1f}" y="{MT+PH+20}" fill="var(--muted)" '
                     f'font-size="12" text-anchor="middle" '
                     f'style="font-variant-numeric:tabular-nums">{d}</text>')
    y_lo, y_hi = math.ceil(ymin), math.floor(ymax)
    for yv in range(y_lo, y_hi + 1):
        y = sy(yv)
        parts.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{ML+PW}" y2="{y:.1f}" '
                     f'stroke="var(--grid)" stroke-width="1"/>')
        parts.append(f'<text x="{ML-10}" y="{y+4:.1f}" fill="var(--muted)" '
                     f'font-size="12" text-anchor="end" '
                     f'style="font-variant-numeric:tabular-nums">{yv}</text>')

    # 軸ライン
    parts.append(f'<line x1="{ML}" y1="{MT+PH}" x2="{ML+PW}" y2="{MT+PH}" '
                 f'stroke="var(--axis)" stroke-width="1"/>')
    parts.append(f'<line x1="{ML}" y1="{MT}" x2="{ML}" y2="{MT+PH}" '
                 f'stroke="var(--axis)" stroke-width="1"/>')
    # 軸タイトル
    parts.append(f'<text x="{ML+PW/2:.0f}" y="{H-14}" fill="var(--secondary)" '
                 f'font-size="13" text-anchor="middle">震源距離 D [km] (対数)</text>')
    parts.append(f'<text x="18" y="{MT+PH/2:.0f}" fill="var(--secondary)" '
                 f'font-size="13" text-anchor="middle" '
                 f'transform="rotate(-90 18 {MT+PH/2:.0f})">正規化観測震度  obs − 1.5M</text>')

    # --- モデル線 ---
    def draw_line(fit, color, label, dash=None):
        y1, y2 = line_y(fit, xmin), line_y(fit, xmax)
        da = f' stroke-dasharray="{dash}"' if dash else ''
        parts.append(f'<line x1="{sx(xmin):.1f}" y1="{sy(y1):.1f}" '
                     f'x2="{sx(xmax):.1f}" y2="{sy(y2):.1f}" stroke="{color}" '
                     f'stroke-width="2" stroke-linecap="round"{da}/>')
        parts.append(f'<text x="{sx(xmax)+8:.1f}" y="{sy(y2)+4:.1f}" fill="{color}" '
                     f'font-size="12" font-weight="600">{label}</text>')

    draw_line((cur_b, cur_c), "var(--c-current)", f"現行 {cur_b:g}")
    if fit_all:
        draw_line(fit_all, "var(--c-fit)", f"回帰(全) {fit_all[0]:+.2f}")
    if fit_clean:
        draw_line(fit_clean, "var(--c-fitclean)", f"回帰(clean) {fit_clean[0]:+.2f}", dash="5 4")

    # --- 観測点 ---
    for r in rows:
        cx, cy = sx(r["logD"]), sy(r["y_norm"])
        fill = "var(--c-custom)" if r["custom"] else "var(--c-official)"
        op = "1" if r["clean"] else "0.4"
        cur_pred = cur_b * r["logD"] + cur_c
        tip = (f'{r["event"]}  M{r["M"]:.1f} d{r["depth"]:.0f}km '
               f'epi{r["epi"]:.0f}km / obs{r["obs30"]:+.1f} '
               f'y={r["y_norm"]:+.1f} / 現行予測y={cur_pred:+.1f} '
               f'残差{r["y_norm"]-cur_pred:+.1f} / '
               f'{"独自算出" if r["custom"] else "公式"}'
               f'{"" if r["clean"] else " (ノイズフロア)"}')
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="{fill}" '
            f'fill-opacity="{op}" stroke="var(--surface)" stroke-width="2" '
            f'class="pt" data-tip="{html.escape(tip, quote=True)}"/>')

    parts.append('</svg>')
    return "\n".join(parts)


def legend_html():
    items = [
        ('<span class="sw" style="background:var(--c-custom)"></span>', '観測点: 独自算出 (勝俣式が効く)'),
        ('<span class="sw" style="background:var(--c-official)"></span>', '観測点: 公式予測'),
        ('<span class="sw dim"></span>', '淡色 = ノイズフロア (obs&lt;1.0)'),
        ('<span class="ln" style="background:var(--c-current)"></span>', '現行モデル -3.2'),
        ('<span class="ln" style="background:var(--c-fit)"></span>', '回帰(全点)'),
        ('<span class="ln dash" style="background:var(--c-fitclean)"></span>', '回帰(cleanのみ)'),
    ]
    lis = "".join(f'<li>{sw}<span>{lab}</span></li>' for sw, lab in items)
    return f'<ul class="legend">{lis}</ul>'


def table_html(rows, gc):
    cur_b, cur_c = CURRENT_SLOPE, CURRENT_BASE + gc
    head = ("<tr><th>event</th><th>src</th><th>M</th><th>dep</th><th>epi</th>"
            "<th>D(hypo)</th><th>obs30</th><th>y=obs−1.5M</th>"
            "<th>現行予測y</th><th>残差</th></tr>")
    body = []
    for r in rows:
        cur_pred = cur_b * r["logD"] + cur_c
        resid = r["y_norm"] - cur_pred
        cls = "" if r["clean"] else ' class="nf"'
        src = "独自" if r["custom"] else "公式"
        body.append(
            f"<tr{cls}><td>{r['event']}</td><td>{src}</td><td>{r['M']:.1f}</td>"
            f"<td>{r['depth']:.0f}</td><td>{r['epi']:.0f}</td>"
            f"<td>{r['hypo']:.0f}</td><td>{r['obs30']:+.1f}</td>"
            f"<td>{r['y_norm']:+.1f}</td><td>{cur_pred:+.1f}</td>"
            f"<td>{resid:+.1f}</td></tr>")
    return f'<table class="tbl">{head}{"".join(body)}</table>'


def build_html(rows, gc, fit_all, fit_clean):
    n = len(rows)
    n_clean = sum(1 for r in rows if r["clean"])
    scatter = build_scatter_svg(rows, gc, fit_all, fit_clean)

    def slope_str(fit):
        return f"{fit[0]:+.2f}" if fit else "—"

    # 解釈文
    parts = []
    if fit_all:
        gap = CURRENT_SLOPE - fit_all[0]  # 現行 - 回帰。回帰が急ならgap>0
        if fit_all[0] < CURRENT_SLOPE - 0.15:
            parts.append(f"回帰(全点)の傾き {fit_all[0]:+.2f} は現行 {CURRENT_SLOPE:g} より"
                         f"<b>急峻</b>で、実測が遠地で予測より速く減衰する #7 の示唆を支持。")
        elif fit_all[0] > CURRENT_SLOPE + 0.15:
            parts.append(f"回帰(全点)の傾き {fit_all[0]:+.2f} は現行より緩い。仮説とは逆方向。")
        else:
            parts.append(f"回帰(全点)の傾き {fit_all[0]:+.2f} は現行とほぼ同等。")
    if fit_clean:
        parts.append(f"クリーン点(obs≥{NOISE_FLOOR:g}, n={n_clean})のみの回帰は {fit_clean[0]:+.2f}。"
                     f"全点との差はノイズフロア点の影響度を表す。")
    interp = " ".join(parts) if parts else "回帰にはイベント数が不足。"

    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    cards = f"""
    <div class="cards">
      <div class="card"><span class="k">イベント数</span><span class="v">{n}</span>
        <span class="s">clean {n_clean}</span></div>
      <div class="card"><span class="k">現行係数</span><span class="v">{CURRENT_SLOPE:g}</span>
        <span class="s">勝俣式 距離項</span></div>
      <div class="card"><span class="k">回帰 (全点)</span><span class="v">{slope_str(fit_all)}</span>
        <span class="s">最小二乗</span></div>
      <div class="card"><span class="k">回帰 (clean)</span><span class="v">{slope_str(fit_clean)}</span>
        <span class="s">obs≥{NOISE_FLOOR:g}</span></div>
    </div>"""

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>#8 予測 vs 実測 距離減衰プロファイル</title>
<style>
  :root {{
    --font: system-ui, -apple-system, "Segoe UI", "Yu Gothic UI", sans-serif;
    --plane:#f9f9f7; --surface:#fcfcfb; --primary:#0b0b0b; --secondary:#52514e;
    --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
    --c-custom:#eb6834; --c-official:#2a78d6; --c-current:#898781;
    --c-fit:#008300; --c-fitclean:#4a3aa7;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{
      --plane:#0d0d0d; --surface:#1a1a19; --primary:#fff; --secondary:#c3c2b7;
      --muted:#898781; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
      --c-custom:#d95926; --c-official:#3987e5; --c-current:#898781;
      --c-fit:#008300; --c-fitclean:#9085e9;
    }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--plane); color:var(--primary); font-family:var(--font);
         line-height:1.55; padding:28px 20px 60px; }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:var(--secondary); font-size:13px; margin:0 0 20px; }}
  .cards {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:20px; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:10px;
           padding:12px 16px; min-width:120px; display:flex; flex-direction:column; }}
  .card .k {{ font-size:12px; color:var(--secondary); }}
  .card .v {{ font-size:26px; font-weight:600; font-variant-numeric:tabular-nums; }}
  .card .s {{ font-size:11px; color:var(--muted); }}
  .panel {{ background:var(--surface); border:1px solid var(--border); border-radius:12px;
            padding:16px 16px 8px; margin-bottom:20px; position:relative; }}
  .interp {{ font-size:13px; color:var(--secondary); background:var(--surface);
             border:1px solid var(--border); border-left:3px solid var(--c-fit);
             border-radius:8px; padding:12px 14px; margin-bottom:20px; }}
  .legend {{ list-style:none; display:flex; flex-wrap:wrap; gap:8px 18px; padding:8px 4px 4px;
             margin:0; font-size:12px; color:var(--secondary); }}
  .legend li {{ display:flex; align-items:center; gap:6px; }}
  .sw {{ width:11px; height:11px; border-radius:50%; display:inline-block; }}
  .sw.dim {{ background:var(--c-official); opacity:0.4; }}
  .ln {{ width:16px; height:2px; display:inline-block; border-radius:2px; }}
  .ln.dash {{ background:linear-gradient(90deg,var(--c-fitclean) 60%,transparent 60%);
              background-size:7px 2px; }}
  .tbl {{ border-collapse:collapse; width:100%; font-size:12px;
          font-variant-numeric:tabular-nums; }}
  .tbl th, .tbl td {{ text-align:right; padding:5px 8px; border-bottom:1px solid var(--grid); }}
  .tbl th:first-child, .tbl td:first-child,
  .tbl th:nth-child(2), .tbl td:nth-child(2) {{ text-align:left; }}
  .tbl th {{ color:var(--secondary); font-weight:600; }}
  .tbl tr.nf td {{ color:var(--muted); }}
  .tblwrap {{ overflow-x:auto; }}
  h2 {{ font-size:14px; margin:24px 0 8px; }}
  #tip {{ position:fixed; pointer-events:none; background:var(--primary); color:var(--surface);
          font-size:11px; padding:6px 9px; border-radius:6px; max-width:320px; opacity:0;
          transition:opacity .08s; z-index:10; box-shadow:0 2px 8px rgba(0,0,0,.25); }}
  .pt {{ cursor:pointer; }}
  .foot {{ color:var(--muted); font-size:11px; margin-top:24px; }}
</style></head>
<body><div class="wrap">
  <h1>#8 予測 vs 実測 — 距離減衰プロファイル</h1>
  <p class="sub">勝俣式の距離減衰係数 -3.2 と実測の当てはまりを検証 (#7 抽出を再利用)。生成 {generated}</p>
  {cards}
  <div class="interp">{interp}</div>
  <div class="panel">{scatter}{legend_html()}</div>
  <h2>イベント一覧 (残差 = 観測 − 現行予測。正 = 現行が過小)</h2>
  <div class="tblwrap">{table_html(rows, gc)}</div>
  <p class="foot">y = obs30 − 1.5M でマグニチュード依存を除き、残る距離依存 (傾き) を比較する。
    ノイズフロア(obs&lt;{NOISE_FLOOR:g})点は淡色/灰字。少数イベントでは回帰は暫定。</p>
</div>
<div id="tip"></div>
<script>
  const tip = document.getElementById('tip');
  document.querySelectorAll('.pt').forEach(function (el) {{
    el.addEventListener('mousemove', function (e) {{
      tip.textContent = el.getAttribute('data-tip');
      tip.style.left = (e.clientX + 12) + 'px';
      tip.style.top = (e.clientY + 12) + 'px';
      tip.style.opacity = '1';
    }});
    el.addEventListener('mouseleave', function () {{ tip.style.opacity = '0'; }});
  }});
</script>
</body></html>"""


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJECT_ROOT, "eq_log", "prediction_diff.html")
    rows, gc = collect_rows()
    if not rows:
        print("評価対象イベントが見つかりません (EEW+モニタのペア無し)。")
        return
    pts_all = [(r["logD"], r["y_norm"]) for r in rows]
    pts_clean = [(r["logD"], r["y_norm"]) for r in rows if r["clean"]]
    fit_all = fit_line(pts_all)
    fit_clean = fit_line(pts_clean)

    doc = build_html(rows, gc, fit_all, fit_clean)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)

    n_clean = sum(1 for r in rows if r["clean"])
    print(f"生成: {out}")
    print(f"  n={len(rows)} events (clean {n_clean})  gc={gc}")
    if fit_all:
        print(f"  回帰(全点)  傾き {fit_all[0]:+.2f}  切片 {fit_all[1]:+.2f}  (現行 {CURRENT_SLOPE:g})")
    if fit_clean:
        print(f"  回帰(clean) 傾き {fit_clean[0]:+.2f}  切片 {fit_clean[1]:+.2f}")


if __name__ == "__main__":
    main()
