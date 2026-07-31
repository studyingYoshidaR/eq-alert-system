import os
import math
import datetime
import json
import random
from PIL import Image, ImageDraw

# --- 震度とRGBの厳密なカラーマップ ---
RAW_COLOR_MAP = [
    {"Intensity":-3,"R":0,"G":0,"B":205},{"Intensity":-2.5,"R":0,"G":36,"B":227},{"Intensity":-2.4,"R":0,"G":43,"B":231},{"Intensity":-2,"R":0,"G":72,"B":250},{"Intensity":-1.5,"R":0,"G":140,"B":194},{"Intensity":-1,"R":0,"G":208,"B":139},{"Intensity":-0.9,"R":6,"G":212,"B":130},{"Intensity":-0.8,"R":12,"G":216,"B":121},{"Intensity":-0.7,"R":18,"G":220,"B":113},{"Intensity":-0.6,"R":25,"G":224,"B":104},{"Intensity":-0.5,"R":31,"G":228,"B":96},{"Intensity":-0.4,"R":37,"G":233,"B":88},{"Intensity":-0.3,"R":44,"G":237,"B":79},{"Intensity":-0.2,"R":50,"G":241,"B":71},{"Intensity":-0.1,"R":56,"G":245,"B":62},{"Intensity":0,"R":63,"G":250,"B":54},{"Intensity":0.1,"R":75,"G":250,"B":49},{"Intensity":0.2,"R":88,"G":250,"B":45},{"Intensity":0.3,"R":100,"G":251,"B":41},{"Intensity":0.4,"R":113,"G":251,"B":37},{"Intensity":0.5,"R":125,"G":252,"B":33},{"Intensity":0.6,"R":138,"G":252,"B":28},{"Intensity":0.7,"R":151,"G":253,"B":24},{"Intensity":0.8,"R":163,"G":253,"B":20},{"Intensity":0.9,"R":176,"G":254,"B":16},{"Intensity":1,"R":189,"G":255,"B":12},{"Intensity":1.1,"R":195,"G":254,"B":10},{"Intensity":1.2,"R":202,"G":254,"B":9},{"Intensity":1.3,"R":208,"G":254,"B":8},{"Intensity":1.4,"R":215,"G":254,"B":7},{"Intensity":1.5,"R":222,"G":255,"B":5},{"Intensity":1.6,"R":228,"G":254,"B":4},{"Intensity":1.7,"R":235,"G":255,"B":3},{"Intensity":1.8,"R":241,"G":254,"B":2},{"Intensity":1.9,"R":248,"G":255,"B":1},{"Intensity":2,"R":255,"G":255,"B":0},{"Intensity":2.1,"R":254,"G":251,"B":0},{"Intensity":2.2,"R":254,"G":248,"B":0},{"Intensity":2.3,"R":254,"G":244,"B":0},{"Intensity":2.4,"R":254,"G":241,"B":0},{"Intensity":2.5,"R":255,"G":238,"B":0},{"Intensity":2.6,"R":254,"G":234,"B":0},{"Intensity":2.7,"R":255,"G":231,"B":0},{"Intensity":2.8,"R":254,"G":227,"B":0},{"Intensity":2.9,"R":255,"G":224,"B":0},{"Intensity":3,"R":255,"G":221,"B":0},{"Intensity":3.1,"R":254,"G":213,"B":0},{"Intensity":3.2,"R":254,"G":205,"B":0},{"Intensity":3.3,"R":254,"G":197,"B":0},{"Intensity":3.4,"R":254,"G":190,"B":0},{"Intensity":3.5,"R":255,"G":182,"B":0},{"Intensity":3.6,"R":254,"G":174,"B":0},{"Intensity":3.7,"R":255,"G":167,"B":0},{"Intensity":3.8,"R":254,"G":159,"B":0},{"Intensity":3.9,"R":255,"G":151,"B":0},{"Intensity":4,"R":255,"G":144,"B":0},{"Intensity":4.1,"R":254,"G":136,"B":0},{"Intensity":4.2,"R":254,"G":128,"B":0},{"Intensity":4.3,"R":254,"G":121,"B":0},{"Intensity":4.4,"R":254,"G":113,"B":0},{"Intensity":4.5,"R":255,"G":106,"B":0},{"Intensity":4.6,"R":254,"G":98,"B":0},{"Intensity":4.7,"R":255,"G":90,"B":0},{"Intensity":4.8,"R":254,"G":83,"B":0},{"Intensity":4.9,"R":255,"G":75,"B":0},{"Intensity":5,"R":255,"G":68,"B":0},{"Intensity":5.1,"R":254,"G":61,"B":0},{"Intensity":5.2,"R":253,"G":54,"B":0},{"Intensity":5.3,"R":252,"G":47,"B":0},{"Intensity":5.4,"R":251,"G":40,"B":0},{"Intensity":5.5,"R":250,"G":33,"B":0},{"Intensity":5.6,"R":249,"G":27,"B":0},{"Intensity":5.7,"R":248,"G":20,"B":0},{"Intensity":5.8,"R":247,"G":13,"B":0},{"Intensity":5.9,"R":246,"G":6,"B":0},{"Intensity":6,"R":245,"G":0,"B":0},{"Intensity":6.1,"R":238,"G":0,"B":0},{"Intensity":6.2,"R":230,"G":0,"B":0},{"Intensity":6.3,"R":223,"G":0,"B":0},{"Intensity":6.4,"R":215,"G":0,"B":0},{"Intensity":6.5,"R":208,"G":0,"B":0},{"Intensity":6.6,"R":200,"G":0,"B":0},{"Intensity":6.7,"R":192,"G":0,"B":0},{"Intensity":6.8,"R":185,"G":0,"B":0},{"Intensity":6.9,"R":177,"G":0,"B":0},{"Intensity":7.0,"R":170,"G":0,"B":0}
]

COLOR_STEPS = sorted([(d["Intensity"], (d["R"], d["G"], d["B"])) for d in RAW_COLOR_MAP], key=lambda x: x[0])

# --- ユーティリティ関数 ---
def get_color_from_intensity(intensity):
    if intensity <= COLOR_STEPS[0][0]: return COLOR_STEPS[0][1]
    if intensity >= COLOR_STEPS[-1][0]: return COLOR_STEPS[-1][1]
    for i in range(len(COLOR_STEPS) - 1):
        low_val, low_rgb = COLOR_STEPS[i]
        high_val, high_rgb = COLOR_STEPS[i+1]
        if low_val <= intensity <= high_val:
            ratio = (intensity - low_val) / (high_val - low_val)
            r = int(low_rgb[0] + (high_rgb[0] - low_rgb[0]) * ratio)
            g = int(low_rgb[1] + (high_rgb[1] - low_rgb[1]) * ratio)
            b = int(low_rgb[2] + (high_rgb[2] - low_rgb[2]) * ratio)
            return (r, g, b)
    return (0, 0, 205)

def intensity_float_to_str(val: float) -> str:
    """数値の震度を気象庁の階級文字列（弱・強）に変換"""
    if val < 0.5: return "0"
    if val < 1.5: return "1"
    if val < 2.5: return "2"
    if val < 3.5: return "3"
    if val < 4.5: return "4"
    if val < 5.0: return "5弱"
    if val < 5.5: return "5強"
    if val < 6.0: return "6弱"
    if val < 6.5: return "6強"
    return "7"

# --- NIED モニタ画像生成クラス ---
class EqImageGenerator:
    def __init__(self, base_image_path):
        if not os.path.exists(base_image_path):
            raise FileNotFoundError(f"ベース画像が見つかりません: {base_image_path}")
        self.base_img = Image.open(base_image_path).convert("RGBA")
        self.width, self.height = self.base_img.size
        self.stations = self._extract_stations()
        print(f"[Image] 抽出された観測点数: {len(self.stations)}")

    def _extract_stations(self):
        stations = []
        pixels = self.base_img.load()
        for y in range(0, self.height, 2):
            for x in range(0, self.width, 2):
                r, g, b, a = pixels[x, y]
                # 平時の観測点（暗い青系統）を抽出
                if a > 0 and b > 100 and r < 50 and g < 150:
                    stations.append((x, y))
        return stations

    def generate(self, output_base_dir, start_time: datetime.datetime, epicenter_x, epicenter_y, max_intensity_target, duration_sec=120):
        dt_str = start_time.strftime("%Y%m%d_%H%M")
        target_dir_name = f"eqlog_{dt_str}_lv{max_intensity_target:.1f}"
        parent_dir = os.path.join(output_base_dir, target_dir_name)
        output_dir = os.path.join(parent_dir, "surface")
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(os.path.join(parent_dir, "borehole"), exist_ok=True)
        print(f"[Image] 保存先ディレクトリ: {os.path.abspath(output_dir)}")

        # P波・S波の伝播速度モデル (画像スケールに合わせたピクセル/秒)
        p_wave_speed = 18.0
        s_wave_speed = 9.5
        attenuation_factor = 0.022 

        for t in range(duration_sec):
            frame = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(frame)
            
            for sx, sy in self.stations:
                dist = math.sqrt((sx - epicenter_x)**2 + (sy - epicenter_y)**2)
                p_arrival = dist / p_wave_speed
                s_arrival = dist / s_wave_speed
                
                # 震源距離による最大到達震度の減衰モデル
                station_max_int = max_intensity_target - (dist * attenuation_factor)
                intensity = -3.0
                
                if t > p_arrival and t <= s_arrival:
                    # P波到達（初期微動）: 振幅は小さい
                    intensity = -1.5 + (station_max_int * 0.1)
                
                if t > s_arrival:
                    # S波到達（主要動）: 急激な立ち上がりと指数関数的なコーダ波の減衰
                    t_diff = t - s_arrival
                    attack = 1.0 - math.exp(-1.2 * t_diff)
                    decay_rate = 0.04 - (max_intensity_target * 0.003)
                    decay = math.exp(-max(0.01, decay_rate) * t_diff)
                    amplitude = (station_max_int - (-3.0)) * attack * decay
                    intensity = -3.0 + amplitude

                # 物理限界（-3.0〜7.0）へのクランプ
                intensity = max(-3.0, min(7.0, intensity))
                color = get_color_from_intensity(intensity)
                # 視認性向上のため2x2矩形で描画
                draw.rectangle([sx, sy, sx+1, sy+1], fill=color)

            file_dt = start_time + datetime.timedelta(seconds=t)
            filename = f"eqlog_s_{file_dt.strftime('%Y%m%d%H%M%S')}_lv{max_intensity_target:.1f}.png"
            frame.save(os.path.join(output_dir, filename))
            
            import sys
            sys.stdout.write(f"\r\033[K[Image] Generating frame t={t:3d}/{duration_sec} ({filename})")
            sys.stdout.flush()
            
        print(f"\n[Image] {duration_sec}枚の画像を生成しました。")


# --- EEW (Wolfx) JSON生成クラス ---
class EewJsonGenerator:
    def __init__(self, output_base_dir):
        self.output_base_dir = output_base_dir

    def generate(self, start_time: datetime.datetime, hypocenter_name: str, lat: float, lon: float, 
                 target_magnitude: float, target_max_intensity: float, report_count: int = 9):
        """
        時間経過に伴う観測データの蓄積と、IPF法による震源・規模推定の精度向上を模倣したJSONシーケンスを生成。
        """
        date_dir_str = start_time.strftime("%Y%m%d")
        output_dir = os.path.join(self.output_base_dir, date_dir_str)
        os.makedirs(output_dir, exist_ok=True)
        print(f"[EEW] JSON保存先ディレクトリ: {os.path.abspath(output_dir)}")

        # EventIDは地震発生時刻（秒単位）
        event_id = start_time.strftime("%Y%m%d%H%M%S")
        origin_time_str = start_time.strftime("%Y/%m/%d %H:%M:%S")
        
        # 気象庁の実運用に近い発表遅延スケジュールのエミュレーション（秒）
        delay_schedule = [4, 7, 8, 9, 10, 13, 17, 30, 49]
        
        # 初期報のマグニチュードは過小評価されやすい特性を反映
        mag_start = target_magnitude - 0.6
        
        for i in range(report_count):
            serial = i + 1
            is_final = (serial == report_count)
            
            if i < len(delay_schedule):
                delay_sec = delay_schedule[i]
            else:
                delay_sec = delay_schedule[-1] + (i - len(delay_schedule) + 1) * 15
                
            announced_time = start_time + datetime.timedelta(seconds=delay_sec)
            
            # 対数的な精度向上カーブ（初期に大きく変化し、後半は微調整）
            progress = math.log1p(i * 2) / math.log1p((report_count - 1) * 2) if report_count > 1 else 1.0
            
            current_mag = mag_start + (target_magnitude - mag_start) * progress
            
            # 震度も同様に徐々に成長する
            int_start = max(1.0, target_max_intensity - 1.5)
            current_int_float = int_start + (target_max_intensity - int_start) * progress
            current_int_str = intensity_float_to_str(current_int_float)
            
            # システム上の精度テキストの変化
            if serial <= 2:
                accuracy_mag = "防災科研システム"
            elif serial <= 5:
                accuracy_mag = "P 相／全相混在"
            else:
                accuracy_mag = "全点全相"

            is_warn = (current_int_float >= 4.5)
            
            # 居住地（千葉県北西部）の距離減衰をシミュレート（震央からやや離れているため震度を下げる）
            home_intensity_float = max(1.0, current_int_float - 1.2) 
            home_intensity_str = intensity_float_to_str(home_intensity_float)
            
            warn_area = []
            if is_warn or current_int_float >= 3.0:
                # 1. 実際の震源地付近（茨城県）のエリア情報
                warn_area.append({
                    "Chiiki": "茨城県北部",
                    "Shindo1": current_int_str,
                    "Shindo2": intensity_float_to_str(max(1.0, current_int_float - 0.5)),
                    "Time": "//////",
                    "Type": "警報" if is_warn else "予報",
                    "Arrive": "既に到達と予測" if delay_sec > 10 else "未到達"
                })
                # 2. テスト用ホームエリア（千葉県北西部）のエリア情報
                warn_area.append({
                    "Chiiki": "千葉県北西部",
                    "Shindo1": home_intensity_str,
                    "Shindo2": intensity_float_to_str(max(1.0, home_intensity_float - 1.0)),
                    "Time": "//////",
                    "Type": "警報" if is_warn else "予報",
                    "Arrive": "既に到達と予測" if delay_sec > 18 else "未到達"
                })

            json_payload = {
                "type": "jma_eew",
                "Title": "緊急地震速報（警報）" if is_warn else "緊急地震速報（予報）",
                "CodeType": "一般向け緊急地震速報" if is_warn else "Ｍ、最大予測震度及び主要動到達予測時刻の緊急地震速報",
                "Issue": {
                    "Source": "東京",
                    "Status": "通常"
                },
                "EventID": event_id,
                "Serial": serial,
                "AnnouncedTime": announced_time.strftime("%Y/%m/%d %H:%M:%S"),
                "OriginTime": origin_time_str,
                "Hypocenter": hypocenter_name,
                "Latitude": round(lat, 1),
                "Longitude": round(lon, 1),
                "Magunitude": round(current_mag, 1),
                "Depth": 50 if serial > 4 else random.choice([40, 60]), # 深さは後半で安定
                "MaxIntensity": current_int_str,
                "Accuracy": {
                    "Epicenter": "IPF 法（5 点以上）",
                    "Depth": "IPF 法（5 点以上）",
                    "Magnitude": accuracy_mag
                },
                "MaxIntChange": {
                    "String": "ほとんど変化なし",
                    "Reason": "不明、未設定時、キャンセル時"
                },
                "WarnArea": warn_area,
                "isSea": False, # 茨城北部は内陸
                "isTraining": False,
                "isAssumption": False,
                "isWarn": is_warn,
                "isFinal": is_final,
                "isCancel": False,
                "OriginalText": f"Generated Simulation Data for {hypocenter_name}: Serial {serial}",
                "Pond": "91"
            }

            filename = f"{event_id}_{serial}.json"
            filepath = os.path.join(output_dir, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(json_payload, f, ensure_ascii=False, indent=4)
            
            print(f"[EEW] Generated: {filename} (M{current_mag:.1f}, MaxInt:{current_int_str}, 警報:{is_warn})")


# --- 実行統合セクション ---
if __name__ == "__main__":
    # --- 1. 基本パラメータ設定 ---
    base_img_path = "../monitor_images/history/saved/20260125114614.png"
    
    test_image_dir = "./test_monitor_images/eq_log"
    test_eew_dir = "./test_eq_log/test_eq_eew_jma"
    
    # 提示された「茨城県北部」の地震画像データ群に基づくパラメータ
    # ※シミュレーションの発生時刻を任意に設定 (画像ファイルのタイムスタンプに合わせることも可能)
    sim_start_dt = datetime.datetime(2026, 1, 25, 11, 46, 0)
    
    # 画像の視覚的中心（茨城県北部付近）のピクセル座標へ補正
    epicenter_x = 310
    epicenter_y = 285 
    
    # 実際の気象庁データに基づく茨城県北部のメタデータ
    hypocenter_name = "茨城県北部"
    target_lat = 36.7
    target_lon = 140.6
    
    target_magnitude = 5.4             # M5.4 (典型的な茨城北部の強震クラス)
    target_max_intensity_float = 5.5   # 最大震度 5強
    
    simulation_duration = 120 # 2分間
    report_count = 9          # EEWの総発表回数

    print("=" * 50)
    print("  テスト用 大規模地震仮想データ生成ツール (茨城県北部)")
    print("=" * 50)
    
    try:
        # --- 2. 画像データ生成 ---
        print("\n>>> 強震モニタ画像 (NIED) の生成を開始します...")
        img_generator = EqImageGenerator(base_img_path)
        img_generator.generate(
            output_base_dir=test_image_dir,
            start_time=sim_start_dt,
            epicenter_x=epicenter_x,
            epicenter_y=epicenter_y,
            max_intensity_target=target_max_intensity_float,
            duration_sec=simulation_duration
        )
        
        # --- 3. EEW JSONデータ生成 ---
        print("\n>>> 緊急地震速報 (Wolfx EEW) の生成を開始します...")
        eew_generator = EewJsonGenerator(test_eew_dir)
        eew_generator.generate(
            start_time=sim_start_dt,
            hypocenter_name=hypocenter_name,
            lat=target_lat,
            lon=target_lon,
            target_magnitude=target_magnitude,
            target_max_intensity=target_max_intensity_float,
            report_count=report_count
        )
        
        print("\n[統合完了] すべての仮想データの生成と保存が完了しました。")
        print(f"画像保存先: {os.path.abspath(test_image_dir)}")
        print(f"EEW保存先: {os.path.abspath(test_eew_dir)}")
        
    except Exception as e:
        print(f"\n[エラー終了] {e}")