import time
import threading
import requests
import datetime
import json
import math
import os
from io import BytesIO
from typing import Callable, Tuple, List, Dict
from PIL import Image

# Internal project modules
from eq_data import JmaEqData, EqSource, EqType
from eq_utils import EqLogger
import eq_config.eq_config as config 

class NiedMonitor:
    # NIED Base URL
    BASE_URL = "http://www.kmoni.bosai.go.jp/data/map_img/RealTimeImg/jma_s/"

    def __init__(self, settings: dict, callback: Callable[[JmaEqData], None], visualizer=None):
        self.logger = EqLogger.setup_logger("NiedMonitor")
        self.callback = callback
        self.settings = settings
        self.visualizer = visualizer
        
        # Load settings
        self.radius_px = settings.get("radius_pixel", 30)
        self.trigger_pixels = settings.get("trigger_pixels", 5)
        self.trigger_intensity = settings.get("trigger_intensity", 1.5) # float
        
        # Load color map
        self.color_map = self._load_color_map(config.COLOR_MAP_PATH)
        
        # Ensure image save directory exists
        if not os.path.exists(config.MONITOR_IMAGE_DIR):
            os.makedirs(config.MONITOR_IMAGE_DIR)

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

    def _monitor_loop(self):
        while self.keep_running:
            start_time = time.time()
            try:
                # 1. Fetch & Analyze image
                self._process_current_image()
            except Exception as e:
                self.logger.warning(f"Monitor loop error: {e}")
            
            # Maintain 1-second interval
            elapsed = time.time() - start_time
            sleep_time = max(0, 1.0 - elapsed)
            time.sleep(sleep_time)

    def _process_current_image(self):
        # Generate timestamp (2 seconds ago)
        now = datetime.datetime.now() - datetime.timedelta(seconds=2)
        date_str = now.strftime("%Y%m%d")
        time_str = now.strftime("%Y%m%d%H%M%S")
        
        url = f"{self.BASE_URL}{date_str}/{time_str}.jma_s.gif"

        try:
            resp = requests.get(url, timeout=2.0)
            if resp.status_code != 200:
                return

            # Load with PIL
            img = Image.open(BytesIO(resp.content)).convert("RGB")

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
            intensity = self._match_color_intensity(rgb)

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
        
        if 337.5 <= normalized_deg or normalized_deg < 22.5: return "東"
        if 22.5 <= normalized_deg < 67.5: return "南東"
        if 67.5 <= normalized_deg < 112.5: return "南"
        if 112.5 <= normalized_deg < 157.5: return "南西"
        if 157.5 <= normalized_deg < 202.5: return "西"
        if 202.5 <= normalized_deg < 247.5: return "北西"
        if 247.5 <= normalized_deg < 292.5: return "北"
        if 292.5 <= normalized_deg < 337.5: return "北東"
        return "不明"

    def _format_intensity_str(self, val: float) -> str:
        """Converts float intensity to a string for speech/display"""
        if val < 5.0: return str(int(val)) # 1, 2, 3, 4
        if val < 5.5: return "5弱"
        if val < 6.0: return "5強"
        if val < 6.5: return "6弱"
        if val < 7.0: return "6強"
        return "7"