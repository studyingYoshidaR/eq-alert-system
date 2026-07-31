import time
import threading
import requests
import datetime
import json
import math
import os
import queue
import glob
from io import BytesIO
from typing import Callable, Tuple, List, Dict, Optional
from PIL import Image

# Internal project modules
from eq_data import JmaEqData, EqSource, EqType
from eq_utils import EqLogger
import eq_config.eq_config as config 

# 画像履歴保存用クラス (リスト管理方式 + Queue構造)
class ImageHistorySaver:
    def __init__(self, base_dir: str, duration_min: int, interval_sec: int, logger):
        self.base_dir = os.path.join(base_dir, "history")
        self.duration_min = duration_min
        self.interval_sec = interval_sec
        self.logger = logger
        
        self.queue = queue.Queue()
        self.running = True
        self.last_save_time = 0
        self.saved_files = [] # (timestamp_float, filepath) のリストで管理しlistdirを回避
        
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)

        # 起動時に既存の古いファイルをリストにロード
        self._load_existing_files()

        # 保存管理用のスレッドを開始
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def _load_existing_files(self):
        """起動時にディレクトリにある既存ファイルを管理リストに追加"""
        try:
            # パターン: YYYYMMDDHHMMSS.png
            pattern = os.path.join(self.base_dir, "*.png")
            files = sorted(glob.glob(pattern))
            
            for file_path in files:
                filename = os.path.basename(file_path)
                time_part = filename.split('.')[0]
                if len(time_part) == 14 and time_part.isdigit():
                    try:
                        # ファイル名からタイムスタンプを復元
                        dt = datetime.datetime.strptime(time_part, "%Y%m%d%H%M%S")
                        ts = dt.timestamp()
                        self.saved_files.append((ts, file_path))
                    except ValueError:
                        continue
            self.logger.info(f"Loaded {len(self.saved_files)} existing history files.")
        except Exception as e:
            self.logger.error(f"Failed to load existing history files: {e}")

    def push(self, img: Image.Image, time_str: str):
        """画像をキューに追加（メインスレッドから呼ばれる）"""
        if not self.running: 
            return
        # 画像のコピーを渡さないとスレッド間で画像操作が競合する可能性があるためコピー
        self.queue.put((img.copy(), time_str))

    def stop(self):
        self.running = False
        self.queue.put(None)
        self.thread.join()

    def _worker(self):
        """保存と削除を行うバックグラウンド処理"""
        while self.running:
            try:
                item = self.queue.get(timeout=1.0)
                if item is None: break
                
                img, time_str = item
                now = time.time()

                # m秒間隔の判定
                if now - self.last_save_time >= self.interval_sec:
                    self._save_image(img, time_str, now)
                    self.last_save_time = now
                    # 保存のタイミングで古いファイルを掃除
                    self._cleanup_old_images(now)
                    
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"History saver error: {e}")

    def _save_image(self, img, time_str, current_ts):
        path = os.path.join(self.base_dir, f"{time_str}.png")
        try:
            # 圧縮レベルを下げてCPU負荷を軽減
            img.save(path, compress_level=1)
            self.saved_files.append((current_ts, path))
        except Exception as e:
            self.logger.warning(f"Failed to save history image: {e}")

    def _cleanup_old_images(self, current_ts):
        """n分経過した画像を削除 (メモリ上のリストを使用)"""
        retention_sec = self.duration_min * 60
        expire_time = current_ts - retention_sec

        # リストは時系列順なので先頭からチェック
        while self.saved_files:
            ts, path = self.saved_files[0]
            if ts < expire_time:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                    self.saved_files.pop(0)
                except Exception as e:
                    self.logger.debug(f"Cleanup error: {e}")
                    self.saved_files.pop(0) # エラーでもリストからは外す
            else:
                break # これ以上新しいファイルはチェック不要

class NiedMonitor:
    # NIED Base URL
    BASE_URL = "http://www.kmoni.bosai.go.jp/data/map_img/RealTimeImg/jma_s/"

    def __init__(self, settings: dict, callback: Callable[[JmaEqData], None], visualizer=None, saves_history:bool=False):
        self.logger = EqLogger.setup_logger("NiedMonitor")
        self.callback = callback
        self.settings = settings
        self.visualizer = visualizer
        self.saves_history = saves_history
        
        # Load settings
        self.radius_px = settings.get("radius_pixel", 30)
        self.trigger_pixels = settings.get("trigger_pixels", 5)
        self.trigger_intensity = settings.get("trigger_intensity", 1.5) # float
        
        # 履歴保存設定の読み込み
        hist_duration = settings.get("history_duration_minutes", 10) # デフォルト10分
        hist_interval = settings.get("history_interval_seconds", 10) # デフォルト10秒

        # Load color map
        self.color_map = self._load_color_map(config.COLOR_MAP_PATH)
        self.color_cache = {}

        # Session利用でKeep-Alive有効化
        self.session = requests.Session()

        # Ensure image save directory exists
        if not os.path.exists(config.MONITOR_IMAGE_DIR):
            os.makedirs(config.MONITOR_IMAGE_DIR)

        # 履歴保存サービスの初期化
        if self.saves_history:
            self.history_saver = ImageHistorySaver(
                config.MONITOR_IMAGE_DIR, 
                hist_duration, 
                hist_interval,
                self.logger
            )
        else:
            self.history_saver = None

        self.keep_running = True
        self.last_trigger_time = 0
        self.cooldown_seconds = 60

    def _load_color_map(self, path: str) -> List[Dict]:
        """Loads the color map in JSON format"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load color map: {e}")
            return []

    def start(self):
        thread = threading.Thread(target=self._monitor_loop, daemon=True)
        thread.start()
        self.logger.info("NIED monitor (Image Analysis) started.")

    def stop(self):
        self.keep_running = False
        if self.history_saver:
            self.history_saver.stop()
        self.session.close()

    def _monitor_loop(self):

        # 毎秒リクエストを投げるタイミング(0.00~0.99 ms)
        TARGET_OFFSET = 0.13

        while self.keep_running:
            # 現在時刻取得
            now_ts = time.time()
            current_sec_int = math.floor(now_ts)

            # 次のターゲット時刻: 現在の秒 + オフセット
            next_target = current_sec_int + TARGET_OFFSET

            if next_target <= now_ts:
                next_target += 1.0

            # 待機
            sleep_duration = next_target - time.time()
            if sleep_duration > 0:
                time.sleep(sleep_duration)
            
            try:
                # Fetch & Analyze image
                target_data_ts = math.floor(next_target)
                self._process_current_image(target_data_ts)
            except Exception as e:
                self.logger.warning(f"Monitor loop error: {e}")

    def _process_current_image(self, target_ts_int: int):
        # 時刻生成のロジック
        now = datetime.datetime.fromtimestamp(target_ts_int)
        # 1秒単位の微調整
        now = now - datetime.timedelta(seconds=1)

        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime("%Y%m%d%H%M%S")
        
        url = f"{self.BASE_URL}{date_str}/{time_str}.jma_s.gif"

        try:
            resp = self.session.get(url, timeout=1.0)
            if resp.status_code == 404:
                # self.logger.debug(f"404 Not Found: {time_str} - Adjust TARGET_OFFSET")
                return
            elif resp.status_code != 200:
                return

            # Load with PIL
            img = Image.open(BytesIO(resp.content)).convert("RGB")

            # Saverが存在する場合, 履歴保存スレッドへ画像を渡す
            if self.history_saver:
                self.history_saver.push(img, time_str)

            # Calculate crop area
            home_x, home_y = config.NIED_HOME_X, config.NIED_HOME_Y
            left   = max(0, home_x - self.radius_px)
            top    = max(0, home_y - self.radius_px)
            right  = min(img.width, home_x + self.radius_px)
            bottom = min(img.height, home_y + self.radius_px)

            crop_img = img.crop((left, top, right, bottom))

            current_intensity = self._analyze_cropped_image(crop_img, time_str)

            if self.visualizer:
                # Update visualizer with cropped image analysis result
                self.visualizer.push_image(img, time_str, current_intensity)

        except Exception as e:
            self.logger.debug(f"Fetch failed: {e}")
            
    def _analyze_cropped_image(self, img: Image.Image, time_str: str) -> float:
        """Analyzes the image and triggers an alert if conditions are met"""
        # List colors included in the image (count, (r,g,b))
        colors = img.getcolors(maxcolors=4096)
        if not colors:
            return -3.0 # if couldnt analyze colors then return minimum intensity

        triggered_pixel_count = 0
        max_intensity = -3.0
        display_max_intensity = -3.0
        
        # Variables for vector addition to calculate direction
        sum_dx = 0
        sum_dy = 0

        # Pixel access object
        pixels = img.load()
        width, height = img.size
        center_x, center_y = width // 2, height // 2

        # Note: Since getcolors does not provide coordinates, a full pixel scan is required.
        # Scanning all pixels in Python is slow, so it's more efficient to use getcolors 
        # first to see if any colors exceed the threshold.
        
        # 1. Check if any color exceeds the threshold (Optimization)
        target_colors = set()
        for count, rgb in colors:
            intensity = self._match_color_intensity_cached(rgb)

            if intensity > display_max_intensity:
                display_max_intensity = intensity

            if intensity >= self.trigger_intensity:
                target_colors.add(rgb)
                if intensity > max_intensity:
                    max_intensity = intensity
        
        if not target_colors:
            return display_max_intensity# Terminate if no colors exceed threshold

        # 2. Scan pixels to identify coordinates (Only if necessary)
        for y in range(height):
            for x in range(width):
                rgb = pixels[x, y]
                if rgb in target_colors:
                    triggered_pixel_count += 1
                    # Add relative coordinates from the center
                    sum_dx += (x - center_x)
                    sum_dy += (y - center_y)

        # 3. Decision & Alert
        if triggered_pixel_count >= self.trigger_pixels:
            current_time = time.time()
            if current_time - self.last_trigger_time < self.cooldown_seconds:
                return display_max_intensity

            self.last_trigger_time = current_time
            
            # Calculate direction
            direction_str = self._calculate_direction(sum_dx, sum_dy)
            
            # Save image (for evidence)
            save_path = os.path.join(config.MONITOR_IMAGE_DIR, f"alert_{time_str}_lv{max_intensity:.1f}.png")
            img.save(save_path)

            # Generate data
            eq_data = JmaEqData(
                source=EqSource.NIED,
                type=EqType.REALTIME,
                event_id=time_str,
                hypocenter_name=direction_str, # [Update] Sets direction such as "South West"
                max_intensity=self._format_intensity_str(max_intensity),
                predicted_home_scale=0
            )
            
            self.logger.warning(f"NIED Alert: {direction_str} MaxInt:{max_intensity:.1f} Pixels:{triggered_pixel_count}")
            self.callback(eq_data)
        else:
            # Overwrite save the latest image (for debugging)
            img.save(os.path.join(config.MONITOR_IMAGE_DIR, "latest.png"))

        return display_max_intensity
    
    # Caching wrapper for color intensity matching
    def _match_color_intensity_cached(self, target_rgb: Tuple[int, int, int]) -> float:
        if target_rgb in self.color_cache:
            return self.color_cache[target_rgb]
        
        val = self._match_color_intensity(target_rgb)
        self.color_cache[target_rgb] = val
        return val

    def _match_color_intensity(self, target_rgb: Tuple[int, int, int]) -> float:
        """Returns seismic intensity (float) from RGB using the JSON map"""
        r1, g1, b1 = target_rgb
        min_dist = 999999
        matched_intensity = -3.0 # Default

        # Find the closest color using simple Euclidean distance
        for item in self.color_map:
            r2, g2, b2 = item["R"], item["G"], item["B"]
            dist = (r1-r2)**2 + (g1-g2)**2 + (b1-b2)**2
            if dist < min_dist:
                min_dist = dist
                matched_intensity = item["Intensity"]
        
        # Ignore if the distance is too far (undefined color)
        if min_dist > 500: # Threshold is adjustable
            return -3.0
            
        return matched_intensity
    
    def _calculate_direction(self, dx: int, dy: int) -> str:
        """Returns the 8-point compass direction as a string from relative coordinates (dx, dy)"""
        if dx == 0 and dy == 0:
            return "直下" # Exactly at center

        # atan2 returns (-pi, pi). Note that the y-axis is positive downwards.
        # Image coordinates: Right is x+, Down is y+
        # To align with North as up (y-), atan2(dy, dx) provides the correct angle.
        angle = math.atan2(dy, dx) # radians
        degree = math.degrees(angle)
        
        # Convert angle to direction (Right=0, Down=90, Left=180, Up=-90)
        # N(-90), NE(-45), E(0), SE(45), S(90), SW(135), W(180), NW(-135)
        
        # Normalize to 0~360 for easier handling
        # Image angle: East(0) -> Clockwise
        normalized_deg = (degree + 360) % 360
        
        if 337.5 <= normalized_deg or normalized_deg < 22.5:
            return "東"
        if 22.5 <= normalized_deg < 67.5: 
            return "南東"
        if 67.5 <= normalized_deg < 112.5:
            return "南"
        if 112.5 <= normalized_deg < 157.5:
            return "南西"
        if 157.5 <= normalized_deg < 202.5: 
            return "西"
        if 202.5 <= normalized_deg < 247.5: 
            return "北西"
        if 247.5 <= normalized_deg < 292.5:
            return "北"
        if 292.5 <= normalized_deg < 337.5:
            return "北東"
        return "不明"

    def _format_intensity_str(self, val: float) -> str:
        """Converts float intensity to a string for speech/display"""
        if val < 5.0:
            return str(int(val)) # 1, 2, 3, 4
        if val < 5.5:
            return "5弱"
        if val < 6.0:
            return "5強"
        if val < 6.5: 
            return "6弱"
        if val < 7.0:
            return "6強"
        return "7"