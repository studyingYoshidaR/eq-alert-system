import time
import threading
import requests
import datetime
import json
import math
import os
import queue
import glob
import re
import subprocess
from io import BytesIO
from collections import deque
from typing import Callable, Tuple, List, Dict, Optional
from PIL import Image, ImageDraw, ImageFont

# Internal project modules
from eq_data import JmaEqData, EqSource, EqType
from eq_utils import EqLogger
from eq_ring_monitor import RingMonitor
from eq_site_calibration import SiteCalibrator, measure_amplification
import eq_config.eq_config as config

# 画像履歴保存用クラス (リスト管理方式 + Queue構造)
class ImageHistorySaver:
    def __init__(self, base_dir: str, duration_min: int, interval_sec: int, logger):
        self.base_dir = os.path.join(base_dir, "recent_history")
        self.duration_min = duration_min
        self.interval_sec = interval_sec
        self.logger = logger
        
        self.queue = queue.Queue()
        self.running = True
        self.last_save_time = 0
        self.saved_files = [] # (timestamp_float, filepath) のリストで管理しlistdirを回避

        self.save_error_reported = False # エラーの連続出力を防ぐためのフラグ
        
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
        """ 保存と削除を行うバックグラウンド処理 """
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
                
                img.close() # メモリ解放
                    
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

            # 保存に成功し、以前エラーが出ていた場合は復旧を通知してフラグをリセット
            if self.save_error_reported:
                self.logger.info("History image save recovered successfully.")
                self.save_error_reported = False
        except Exception as e:
            # エラーがまだ報告されていない場合のみ警告を出す
            if not self.save_error_reported:
                self.logger.warning(f"Failed to save history image (further warnings muted until recovered): {e}")
                self.save_error_reported = True

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
    # NIED Base URL (地表: jma_s / 地中: jma_b)
    BASE_URL = "http://www.kmoni.bosai.go.jp/data/map_img/RealTimeImg/jma_s/"
    BASE_URL_B = "http://www.kmoni.bosai.go.jp/data/map_img/RealTimeImg/jma_b/"

    def __init__(self, settings: dict, callback: Callable[[JmaEqData], None], visualizers=None, saves_history:bool=False, eq_system=None, is_borehole_func=None):
        self.logger = EqLogger.setup_logger("NiedMonitor")
        self.callback = callback
        self.settings = settings
        self.visualizers = visualizers if visualizers is not None else []
        self.saves_history = saves_history
        self.eq_system = eq_system
        self.is_borehole_func = is_borehole_func
        
        # Load settings
        self.radius_px = settings.get("radius_pixel", 30)
        self.trigger_pixels = settings.get("trigger_pixels", 5)
        self.trigger_intensity = settings.get("trigger_intensity", 1.0) # float
        # アラート終了のヒステリシス: 最終活動(全国最大>=1.0)からこの秒数無活動が続いた時のみ終了。
        # 瞬間的に<1.0へ落ちるコマで即終了→再開し1地震が複数フォルダに分裂するのを防ぐ。
        self.alert_end_grace_sec = settings.get("alert_end_grace_sec", 45.0)
        # 末尾トリム: アラート終了時、最終活動から alert_tail_keep_sec 秒より後の
        # 静穏コマを削除してから動画生成する(false=グレース期間の全コマを残す)
        self.alert_trim_quiet_tail = settings.get("alert_trim_quiet_tail", False)
        self.alert_tail_keep_sec = settings.get("alert_tail_keep_sec", 10.0)

        # --- #1 地中(borehole)によるノイズ排除AND設定 ---
        # 地表トリガー時のみ地中画像を取得して確認する(遅延ロード)。
        # 取得失敗時はフェイルオープン(地表判定を尊重)。
        self.borehole_enabled = settings.get("borehole_enabled", True)
        # 地中は地盤増幅前のため地表より震度が小さい→確認閾値は低めに設定
        self.borehole_confirm_intensity = settings.get("borehole_confirm_intensity", 0.5)
        self.borehole_confirm_pixels = settings.get("borehole_confirm_pixels", 2)
        # 二段ゲート: 地表ピークがこの震度以上なら生活ノイズではあり得ない強震として
        # 地中確認をスキップし即発報する(見逃し・検出遅延の防止)
        self.borehole_override_intensity = settings.get("borehole_override_intensity", 3.0)
        # EEW連動バイパス: 直近この秒数以内にEEW(実報)を受信していれば地中確認を免除。
        # サイト増幅+1.5〜1.8のため地表1.3〜2.3の本物の地震は地中が確認閾値に届かず
        # 誤抑制される(20260702千葉県北東部M4.0で実証)。生活ノイズにEEWは付随しないため
        # EEW受信中=実地震進行中とみなす。0で無効。
        self.borehole_eew_bypass_sec = settings.get("borehole_eew_bypass_sec", 180.0)
        self.last_eew_ts = 0.0  # 最後に実EEWを受信した時刻(force_alert経由で更新)

        # --- #2 疑似PLUM法: 自宅周辺リングの実測でEEW予測震度を上方修正 ---
        self.plum_enabled = settings.get("plum_enabled", True)
        self.plum_radius_km = settings.get("plum_radius_km", 30)
        # 単一近傍点の過大評価を避けるための減算(0=生の最大=過小評価しにくい安全側)
        self.plum_decrement = settings.get("plum_decrement", 0.0)
        self.ring_monitor = RingMonitor(
            config.HOME_LAT, config.HOME_LON,
            radii_km=tuple(settings.get("plum_radii_km", [10, 20, 30])),
        )
        self._last_plum_scale = 0  # 同一値の連続ログ抑制用

        # --- #3 S波到達ETA補正(観測アンカー方式・短縮のみ) ---
        # リングへの前線到達を観測し min(EEW予測, 観測ETA) でカウントダウンを前倒し補正
        self.eta_correction_enabled = settings.get("eta_correction_enabled", True)
        # リングを「前線到達」とみなす震度閾値(P波先行を弾くため弱すぎない値)
        self.eta_arrival_intensity = settings.get("eta_arrival_intensity", 2.0)
        # 残り距離→時間換算のS波速度(km/s, システム既定の4.0と同値)
        self.eta_velocity = settings.get("eta_velocity", 4.0)

        # --- #4 サイト地盤増幅 較正(記録のみ・予測への適用は保留) ---
        self.site_calibration_enabled = settings.get("site_calibration_enabled", True)
        self.site_calibration_interval = settings.get("site_calibration_interval_sec", 5)
        self.site_search_px = settings.get("site_search_px", 12)
        self.site_bore_min_intensity = settings.get("site_bore_min_intensity", 1.0)
        self.site_calibrator = SiteCalibrator(
            os.path.join(config.PROJECT_ROOT, "eq_log", "site_calibration.json"),
            min_samples=settings.get("site_min_samples", 5),
            max_station_km=settings.get("site_max_station_km", 25.0),
        )
        self._last_calib_fetch = 0.0

        # --- #6 到達確定フェイルセーフ(観測ベース・地中不要) ---
        # カウントダウン中、自宅近傍が実際に強く揺れた瞬間にEEW計算と独立で到達アラート
        self.arrival_failsafe_enabled = settings.get("arrival_failsafe_enabled", True)
        self.arrival_failsafe_intensity = settings.get("arrival_failsafe_intensity", 3.0)
        self.arrival_failsafe_radius_km = settings.get("arrival_failsafe_radius_km", 10)
        
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

        # アラート期間中のステート管理変数
        self.is_alert_active = False
        self.alert_start_time_str = ""
        self.alert_max_intensity = -3.0
        self.current_alert_dir = ""
        self.alert_last_active_ts = 0.0  # 最後にトリガー水準の活動を観測した時刻
        self.alert_last_active_time_str = ""  # 同・画像データ時刻(末尾トリムの基準)

        self.alert_save_error_reported = False  # アラート時保存エラー用のフラグ
        
        # 外部からのアラート強制開始用フラグ
        self.external_alert_triggered = False

        # アラート期間開始時に呼ばれるコールバック (引数: group_id, max_intensity)
        # GUI 側で地震発生中表示(カウントアップ)の開始に利用する
        self.on_alert_start_callbacks: list = []
        # アラート期間終了時に呼ばれるコールバック (引数: group_id "%Y%m%d_%H%M")
        # GUI 側で地震カード表示などに利用する
        self.on_alert_end_callbacks: list = []

        # 画像取得の接続状態(GUIのNIED(S/B)表示に利用): 直近の取得成功時刻
        self.last_fetch_success_ts = 0.0

        # シミュレーション時のクロップ中心オーバーライド (None = configを使用)
        self.override_cx = None
        self.override_cy = None
        
        # 過去数秒分の画像をメモリに保持するリングバッファ（最大10フレーム
        self.recent_frames = deque(maxlen=10)

    def set_center_override(self, lat: float, lon: float):
        """緯度経度からクロップ中心を一時的にオーバーライドする（シミュレーション用）"""
        from eq_config.eq_config import latlon_to_nied_pixel
        x, y = latlon_to_nied_pixel(lat, lon)
        self.override_cx = x
        self.override_cy = y

    def clear_center_override(self):
        """クロップ中心オーバーライドを解除しconfigの値に戻す"""
        self.override_cx = None
        self.override_cy = None

    # 外部(EEWなど)から画像保存を強制トリガー
    def force_alert(self):
        self.external_alert_triggered = True
        # 実EEW受信の記録(地中確認のEEW連動バイパス用)
        self.last_eew_ts = time.time()

    def _eew_recently_received(self) -> bool:
        """直近にEEW(実報)を受信していればTrue。実地震の進行中は地中確認を免除する。"""
        if self.borehole_eew_bypass_sec <= 0:
            return False
        return (time.time() - self.last_eew_ts) <= self.borehole_eew_bypass_sec

    def _trim_quiet_tail(self, alert_dir: str):
        """アラート終了時、最終活動コマから alert_tail_keep_sec 秒より後の
        静穏コマ(グレース期間の尾)を surface/borehole から削除する。"""
        last_ts_str = getattr(self, 'alert_last_active_time_str', "")
        if not last_ts_str:
            return
        try:
            last_dt = datetime.datetime.strptime(last_ts_str, "%Y%m%d%H%M%S")
        except ValueError:
            return
        cutoff = last_dt + datetime.timedelta(seconds=self.alert_tail_keep_sec)

        removed = 0
        for sub in ("surface", "borehole"):
            d = os.path.join(alert_dir, sub)
            if not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                # ファイル名中の14桁データ時刻で判定 (eqlog_s_<14桁>_lv*.png)
                mt = re.search(r"_(\d{14})_", name)
                if not mt:
                    continue
                try:
                    frame_dt = datetime.datetime.strptime(mt.group(1), "%Y%m%d%H%M%S")
                except ValueError:
                    continue
                if frame_dt > cutoff:
                    try:
                        os.remove(os.path.join(d, name))
                        removed += 1
                    except OSError as e:
                        self.logger.warning(f"Failed to trim tail frame {name}: {e}")
        if removed:
            self.logger.info(
                f"Trimmed {removed} quiet tail frames "
                f"(kept {self.alert_tail_keep_sec:.0f}s after last activity {last_ts_str})."
            )

    # メモリ上のバッファから画像を保存する処理
    def _save_buffered_frames(self, seconds_back: int):
        """ メモリ上のバッファから指定秒数分の画像をアラートディレクトリに保存 """
        if not getattr(self, 'current_alert_dir', ""):
            return
            
        # バッファの後ろから指定秒数(フレーム)分を取得
        frames_to_save = list(self.recent_frames)[-seconds_back:]

        # バッファから震度(buf_intensity)も展開
        for buf_img, buf_b_img, buf_time_str, buf_intensity in frames_to_save:

            # 地表画像の生成
            save_filename = f"eqlog_s_{buf_time_str}_lv{buf_intensity:.1f}.png"
            save_path = os.path.join(self.current_alert_dir, "surface", save_filename)
            try:
                buf_img.save(save_path)
                
                # 地中画像の生成
                if buf_b_img is not None:
                    b_filename = f"eqlog_b_{buf_time_str}_lv{buf_intensity:.1f}.png"
                    b_save_path = os.path.join(self.current_alert_dir, "borehole", b_filename)
                    buf_b_img.save(b_save_path)
            except Exception as e:
                self.logger.warning(f"Failed to save buffered image {save_filename}: {e}")
                
        self.logger.info(f"Saved {len(frames_to_save)} past frames from memory buffer.")
        self.recent_frames.clear() # 重複保存を防ぐためにバッファをクリア

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

    def get_status(self) -> str:
        """画像取得の接続状態を返す (GUIのNIED(S/B)表示用、WolfxWatcher.get_status()に準拠)"""
        if self.last_fetch_success_ts <= 0:
            return "connecting"
        if time.time() - self.last_fetch_success_ts < 10.0:
            return "connected"
        return "offline"

    def stop(self):
        self.keep_running = False
        if self.history_saver:
            self.history_saver.stop()
        self.session.close()

    def _monitor_loop(self):

        # 毎秒リクエストを投げるタイミング(0.00~0.99 ms)
        TARGET_OFFSET = 0.13
        self.missed_frames = []

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
                # 1. Recover missed frames
                recovered = []
                for missed_ts in list(self.missed_frames):
                    success = self._process_current_image(missed_ts, is_recovery=True)
                    if success:
                        recovered.append(missed_ts)
                    elif time.time() - missed_ts > 10:
                        # Give up after 10 seconds
                        recovered.append(missed_ts)
                
                for r in recovered:
                    if r in self.missed_frames:
                        self.missed_frames.remove(r)
                
                # 2. Fetch & Analyze current image
                target_data_ts = math.floor(next_target)
                success = self._process_current_image(target_data_ts, is_recovery=False)
                if not success:
                    # Keep track of failed fetches, max 10 to avoid unbounded growth
                    if len(self.missed_frames) < 10:
                        self.missed_frames.append(target_data_ts)
            except Exception as e:
                self.logger.warning(f"Monitor loop error: {e}")

    def _draw_status_bar(self, img: Image.Image, timestamp: str, max_intensity: float):
        """ 画像の下部にステータスバーを描画 (eq_visualizerのロジックを移植) """
        draw = ImageDraw.Draw(img)
        width, height = img.size
        bar_h = 28  # バーの高さ
        
        # 1. バーの背景（黒帯）
        draw.rectangle((0, height - bar_h, width, height), fill="black")

        # 2. フォント設定 (DejaVuSansMono-Bold を推奨)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 12)
        except:
            font = ImageFont.load_default()

        # 3. 左側：時刻情報の生成
        try:
            img_dt = datetime.datetime.strptime(timestamp, "%Y%m%d%H%M%S")
            fmt_time = img_dt.strftime("%y/%m/%d %H:%M:%S")
            # 遅延計算
            display_time = f"Updated: {fmt_time} (--.-s)"
        except:
            display_time = f"Updated: {timestamp}"

        # 4. 右側：震度情報の生成
        int_str = f"{max_intensity:.1f}" if max_intensity > -3.0 else "--"
        max_int_text = f"MaxInt: {int_str}"

        # 震度に応じた色
        text_color = "white"
        if max_intensity >= 1.0: text_color = "yellow"
        if max_intensity >= 3.0: text_color = "red"

        # 5. 描画位置の計算
        # 左端から10px
        draw.text((10, height - bar_h + 5), display_time, fill="white", font=font)
        
        # 右端から震度テキストの幅分だけ引いた位置 (右詰め)
        text_width = draw.textlength(max_int_text, font=font)
        draw.text((width - text_width - 10, height - bar_h + 5), max_int_text, fill=text_color, font=font)

    def _process_current_image(self, target_ts_int: int, is_recovery: bool = False) -> bool:
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
                if is_recovery:
                    self.logger.warning(f"404 Not Found (Recovery): {time_str} - Image still not ready")
                else:
                    self.logger.debug(f"404 Not Found: {time_str} - Image might not be ready yet")
                return False
            elif resp.status_code != 200:
                self.logger.warning(f"HTTP {resp.status_code} for {time_str}")
                return False

            # Load with PIL
            # BytesIOを明示的に閉じ, Image.openをwith文で処理
            image_stream = BytesIO(resp.content)
            with Image.open(image_stream) as raw_img:
                img = raw_img.convert("RGB")

            self.last_fetch_success_ts = time.time()

            # Saverが存在する場合, 履歴保存スレッドへ画像を渡す
            if self.history_saver:
                self.history_saver.push(img, time_str)

            # Calculate crop area
            # 日本全域の解析（画像保存トリガー用）
            # trigger_alert=False を渡し, コールバック（音声等）は鳴らさない
            global_intensity, global_condition_met = self._analyze_cropped_image(img, time_str, trigger_alert=False)

            
            fetched_b_img = None
            if getattr(self, 'borehole_enabled', True):
                fetched_b_img = self._fetch_borehole_image(time_str)
            
            b_copy = fetched_b_img.copy() if fetched_b_img else None
            self.recent_frames.append((img.copy(), b_copy, time_str, global_intensity))

            # 自宅周辺の切り抜き
            home_x = self.override_cx if self.override_cx is not None else config.NIED_HOME_X
            home_y = self.override_cy if self.override_cy is not None else config.NIED_HOME_Y
            left   = max(0, home_x - self.radius_px)
            top    = max(0, home_y - self.radius_px)
            right  = min(img.width, home_x + self.radius_px)
            bottom = min(img.height, home_y + self.radius_px)

            crop_img = img.crop((left, top, right, bottom))

            # trigger_alert=True を渡し, 自宅周辺が揺れた時だけコールバックを鳴らす
            # #1: 地表トリガー成立時のみ地中で確認(遅延ロード)するゲートを渡す
            confirm_fn = (lambda: self._borehole_confirms(time_str, fetched_b_img)) if self.borehole_enabled else None
            local_intensity, local_condition_met = self._analyze_cropped_image(crop_img, time_str, trigger_alert=True, confirm_fn=confirm_fn)

            # #2: 疑似PLUM法。EEWカウントダウン中、自宅周辺の実測がEEW予測を上回れば上方修正
            if self.plum_enabled and self.eq_system is not None:
                self._apply_plum_revision(img, home_x, home_y)

            # #3: S波到達ETA補正。観測した前線位置でカウントダウンを前倒し(短縮)補正
            if self.eta_correction_enabled and self.eq_system is not None:
                self._apply_eta_correction(time_str, img, home_x, home_y)

            # #4: サイト増幅サンプルの記録(アラート中のみ・低頻度・予測へは未適用)
            if self.site_calibration_enabled and global_condition_met:
                self._maybe_record_site_amplification(img, time_str, home_x, home_y)

            # #6: 到達確定フェイルセーフ。カウントダウン中、自宅近傍が実際に強く揺れたら即到達アラート
            if self.arrival_failsafe_enabled and self.eq_system is not None:
                self._maybe_trigger_arrival_failsafe(img, home_x, home_y)

            # 画像保存のステート管理には「全域」の結果を適用
            current_intensity = global_intensity
            condition_met = global_condition_met or self.external_alert_triggered # 外部トリガー(EEW)のフラグも条件

            # アラート状態の遷移とディレクトリ管理
            if not self.is_alert_active:
                if condition_met:
                    # アラート開始
                    self.is_alert_active = True
                    self.external_alert_triggered = False  # フラグリセット
                    self.alert_start_time_str = now.strftime("%Y%m%d_%H%M")
                    self.alert_start_ts = time.time()      # 開始時刻を記録
                    self.alert_last_active_ts = time.time() # 活動時刻を初期化
                    self.alert_last_active_time_str = time_str
                    self.alert_max_intensity = current_intensity
                    
                    # saves_history が True の時だけフォルダを作成
                    if self.saves_history:
                        self.current_alert_dir = os.path.join(
                            config.MONITOR_IMAGE_DIR,
                            f"eq_log/eqlog_{self.alert_start_time_str}_lv{self.alert_max_intensity:.1f}"
                        )
                        os.makedirs(self.current_alert_dir, exist_ok=True)
                        os.makedirs(os.path.join(self.current_alert_dir, "surface"), exist_ok=True)
                        os.makedirs(os.path.join(self.current_alert_dir, "borehole"), exist_ok=True)
                        self.logger.info(f"Alert Period Started. Dir: {self.current_alert_dir}")
                        # アラート開始時にバッファから5秒前からの画像を保存
                        self._save_buffered_frames(seconds_back=5)
                    else:
                        self.logger.info("Alert Period Started. (Image saving disabled)")

                    # アラート開始通知 (GUI の地震発生中表示に利用)
                    for _cb in self.on_alert_start_callbacks:
                        try:
                            _cb(self.alert_start_time_str, self.alert_max_intensity)
                        except Exception as _e:
                            self.logger.warning(f"[AlertStart] callback error: {_e}")
            else:
                # すでにアラート中: 外部トリガー(EEW)は活動継続とみなして時刻を更新しリセット
                if self.external_alert_triggered:
                    self.alert_last_active_ts = time.time()
                    self.alert_last_active_time_str = time_str
                    self.external_alert_triggered = False

                # アラート継続中
                # 活動判定: 全国最大が1.0以上なら「揺れ継続」として最終活動時刻を更新
                if current_intensity >= 1.0:
                    self.alert_last_active_ts = time.time()
                    self.alert_last_active_time_str = time_str

                # 終了条件(ヒステリシス): 最終活動から alert_end_grace_sec 秒間の無活動が続いた場合のみ終了
                # (揺れが到達する前に即終了してしまわないよう最低30秒間は継続)
                if (time.time() - self.alert_last_active_ts >= self.alert_end_grace_sec
                        and time.time() - getattr(self, 'alert_start_ts', 0) >= 30.0):
                    self.is_alert_active = False
                    quiet_sec = time.time() - self.alert_last_active_ts
                    self.logger.info(f"Alert Period Ended. (quiet for {quiet_sec:.0f}s)")

                    # アラート終了通知 (GUI の地震カード表示などに利用)
                    ended_group_id = self.alert_start_time_str
                    for _cb in self.on_alert_end_callbacks:
                        try:
                            _cb(ended_group_id)
                        except Exception as _e:
                            self.logger.warning(f"[AlertEnd] callback error: {_e}")

                    # アラート終了時, 保存が有効なら(設定に応じ末尾トリム後)動画生成スレッドを起動
                    if self.saves_history and getattr(self, 'current_alert_dir', ""):
                        if self.alert_trim_quiet_tail:
                            self._trim_quiet_tail(self.current_alert_dir)
                        self._start_video_generation(self.current_alert_dir)

                else:
                    # 継続: 期間中の最大震度が更新された場合はディレクトリをリネーム
                    if current_intensity > self.alert_max_intensity:
                        # saves_history が True の時だけリネーム処理を実行
                        if self.saves_history and self.current_alert_dir:
                            new_dir = os.path.join(
                                config.MONITOR_IMAGE_DIR, 
                                f"eq_log/eqlog_{self.alert_start_time_str}_lv{current_intensity:.1f}"
                            )
                            try:
                                if not os.path.exists(new_dir):
                                    os.rename(self.current_alert_dir, new_dir)
                                    self.logger.info(f"Alert Dir Renamed: {new_dir}")
                                    self.current_alert_dir = new_dir
                            except Exception as e:
                                self.logger.error(f"Failed to rename alert dir: {e}")
                        
                        self.alert_max_intensity = current_intensity

            # 画像の保存処理
            if self.is_alert_active:
                # saves_history が True の時だけ画像を保存
                if self.saves_history and getattr(self, 'current_alert_dir', ""):
                    # 地震検知時: 毎秒のオリジナル画像を保存
                    save_filename = f"eqlog_s_{time_str}_lv{current_intensity:.1f}.png"
                    save_path = os.path.join(self.current_alert_dir, "surface", save_filename)
                    try:
                        img.save(save_path)
                        
                        # 地中データの取得・保存 (地震発生時のみ)
                        if getattr(self, 'borehole_enabled', True) and fetched_b_img:
                            b_filename = f"eqlog_b_{time_str}_lv{current_intensity:.1f}.png"
                            b_save_path = os.path.join(self.current_alert_dir, "borehole", b_filename)
                            fetched_b_img.save(b_save_path)

                        # 復旧時の処理
                        if getattr(self, 'alert_save_error_reported', False):
                            self.logger.info("Alert eqlog image save recovered successfully.")
                            self.alert_save_error_reported = False

                    except Exception as e:
                        # 最初の1回のみ警告
                        if not getattr(self, 'alert_save_error_reported', False):
                            self.logger.warning(f"Failed to save eqlog image (further warnings muted): {e}")
                            self.alert_save_error_reported = True
            else:
                # 平時: 最新画像のみ上書き保存
                pass #img.save(os.path.join(config.MONITOR_IMAGE_DIR, "latest.png"))

            if self.visualizers:
                # Update all visualizers with cropped image analysis result
                countdown_sec = None
                is_custom = False
                predicted_home_scale = 0
                if self.eq_system and getattr(self.eq_system, 'active_countdown_data', None):
                    # EqSystem maintains the countdown schedule. Calculate remaining time
                    data = self.eq_system.active_countdown_data
                    if data.s_wave_arrival_ts > 0:
                        t_remain = int(data.s_wave_arrival_ts - time.time())
                        if t_remain > -60: # Keep showing "Arrived!" for 60 seconds
                            countdown_sec = t_remain
                        is_custom = getattr(data, 'is_custom_prediction', False)
                        predicted_home_scale = getattr(data, 'predicted_home_scale', 0)

                push_img = img
                if self.is_borehole_func and self.is_borehole_func() and fetched_b_img:
                    push_img = fetched_b_img

                for viz in self.visualizers:
                    viz.push_image(push_img.copy(), time_str, local_intensity, countdown_sec=countdown_sec, is_custom_prediction=is_custom, predicted_home_scale=predicted_home_scale)

            # メインスレッド側の画像リソースとストリームを明示的に解放
            crop_img.close()
            img.close()
            image_stream.close()
            if fetched_b_img:
                fetched_b_img.close()    

            return True

        except Exception as e:
            self.logger.debug(f"Fetch failed: {e}")
            return False
            
    def _analyze_cropped_image(self, img: Image.Image, time_str: str, trigger_alert: bool = True, confirm_fn=None) -> Tuple[float, bool]:
        """Analyzes the image and triggers an alert if conditions are met"""
        # List colors included in the image (count, (r,g,b))
        colors = img.getcolors(maxcolors=4096)
        if not colors:
            return -3.0, False # if couldnt analyze colors then return minimum intensity

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
            return display_max_intensity, False # Terminate if no colors exceed threshold

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
        # トリガー条件を満たしているか判定（状態管理用）
        condition_met = (triggered_pixel_count >= self.trigger_pixels)

        if condition_met and trigger_alert:
            current_time = time.time()
            # 音声やデータのコールバックはクールダウン時間で制御し連続発報を防ぐ
            if current_time - self.last_trigger_time >= self.cooldown_seconds:
                # #1: 二段ゲート。
                #  - 地表ピークが override 以上 = 生活ノイズではあり得ない強震
                #    → 地中確認をスキップして即発報(見逃し・検出遅延の防止)
                #  - 直近にEEW受信あり = 実地震進行中 → 地中確認を免除して発報
                #    (生活ノイズにEEWは付随しない。地表1.3〜2.3帯は増幅差で地中が
                #     確認閾値に届かず本物でも誤抑制されるため)
                #  - それ未満(ノイズが紛れる弱震帯) → 地中で確認できなければ握り潰す
                # 確認NG時は last_trigger_time を更新せず、次フレームで再判定させる。
                needs_borehole_check = (confirm_fn is not None
                                        and max_intensity < self.borehole_override_intensity)
                eew_bypass = needs_borehole_check and self._eew_recently_received()
                if needs_borehole_check and not eew_bypass and not confirm_fn():
                    self.logger.info(
                        f"NIED Alert suppressed (地中で未確認=生活ノイズの可能性): "
                        f"地表MaxInt:{max_intensity:.1f} Pixels:{triggered_pixel_count}"
                    )
                    return display_max_intensity, condition_met
                if eew_bypass:
                    self.logger.info("[Borehole] EEW受信中のため地中確認を免除(実地震進行中)")

                self.last_trigger_time = current_time

                direction_str = self._calculate_direction(sum_dx, sum_dy)
                
                eq_data = JmaEqData(
                    source=EqSource.NIED,
                    type=EqType.REALTIME,
                    event_id=time_str,
                    hypocenter_name=direction_str,
                    max_intensity=self._format_intensity_str(max_intensity),
                    predicted_home_scale=0
                )
                
                self.logger.warning(f"NIED Alert: {direction_str} MaxInt:{max_intensity:.1f} Pixels:{triggered_pixel_count}")
                self.callback(eq_data)
        else:
            # Overwrite save the latest image (for debugging)
            pass #img.save(os.path.join(config.MONITOR_IMAGE_DIR, "latest.png"))

        return display_max_intensity, condition_met
    
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
    
    # === #1 地中(borehole)によるノイズ排除AND ===
    @staticmethod
    def should_alert(surface_condition_met: bool, borehole_available: bool,
                     borehole_trig_count: int, borehole_confirm_pixels: int) -> bool:
        """地表トリガーを地中で確認するか否かの純粋判定(テスト容易化のため分離)。
        - 地表が未トリガーなら発報しない
        - 地中が取得不能ならフェイルオープン(地表を尊重して発報)
        - それ以外は地中の確認ピクセル数で判定
        """
        if not surface_condition_met:
            return False
        if not borehole_available:
            return True
        return borehole_trig_count >= borehole_confirm_pixels

    def _fetch_borehole_image(self, time_str: str):
        """地表と同一タイムスタンプの地中(jma_b)フル画像を取得して返す。失敗時 None。"""
        date_str = time_str[:8]
        url = f"{self.BASE_URL_B}{date_str}/{time_str}.jma_b.gif"
        try:
            resp = self.session.get(url, timeout=1.0)
            if resp.status_code != 200:
                return None
            with Image.open(BytesIO(resp.content)) as raw_img:
                return raw_img.convert("RGB")
        except Exception as e:
            self.logger.debug(f"[Borehole] fetch failed: {e}")
            return None

    def _fetch_borehole_crop(self, time_str: str, img: Image.Image = None):
        """地中フル画像から自宅クロップを返す。失敗時 None。"""
        if img is None:
            img = self._fetch_borehole_image(time_str)
        if img is None:
            return None
        home_x = self.override_cx if self.override_cx is not None else config.NIED_HOME_X
        home_y = self.override_cy if self.override_cy is not None else config.NIED_HOME_Y
        left   = max(0, home_x - self.radius_px)
        top    = max(0, home_y - self.radius_px)
        right  = min(img.width, home_x + self.radius_px)
        bottom = min(img.height, home_y + self.radius_px)
        crop = img.crop((left, top, right, bottom))
        return crop

    def _evaluate_borehole_crop(self, crop_img: Image.Image) -> Tuple[float, int]:
        """地中クロップ内の最大震度と確認閾値以上のピクセル数を返す。"""
        colors = crop_img.getcolors(maxcolors=4096)
        if not colors:
            return -3.0, 0
        max_int = -3.0
        trig_count = 0
        for count, rgb in colors:
            inten = self._match_color_intensity_cached(rgb)
            if inten > max_int:
                max_int = inten
            if inten >= self.borehole_confirm_intensity:
                trig_count += count
        return max_int, trig_count

    def _borehole_confirms(self, time_str: str, b_img: Image.Image = None) -> bool:
        """地中画像を取得して揺れを確認する。取得失敗時はフェイルオープン(True)。"""
        crop = self._fetch_borehole_crop(time_str, b_img)
        if crop is None:
            self.logger.debug("[Borehole] 取得不可 → フェイルオープン(地表判定を尊重)")
            return True
        max_int, trig_count = self._evaluate_borehole_crop(crop)
        crop.close()
        ok = self.should_alert(True, True, trig_count, self.borehole_confirm_pixels)
        if not ok:
            self.logger.info(f"[Borehole] 未確認: MaxInt:{max_int:.1f} TrigPix:{trig_count} (< {self.borehole_confirm_pixels})")
        else:
            self.logger.debug(f"[Borehole] 確認OK: MaxInt:{max_int:.1f} TrigPix:{trig_count}")
        return ok

    # === #2 疑似PLUM法: 自宅周辺リング実測による予測震度の上方修正 ===
    def _apply_plum_revision(self, img: Image.Image, center_x: float, center_y: float):
        """EEWカウントダウン中、自宅周辺 plum_radius_km 圏内の観測最大震度が
        現在の予測震度を上回る場合、active_countdown_data.predicted_home_scale を上方修正する。
        (PLUM=近隣観測点の最大値で予測、距離減衰を仮定しない過小評価しにくい手法)
        """
        data = getattr(self.eq_system, 'active_countdown_data', None)
        if not data:
            return
        # 観測最大震度(自宅周辺R km圏)
        plum_int = self.ring_monitor.max_within(
            img, self._match_color_intensity_cached, (center_x, center_y), self.plum_radius_km
        )
        plum_int -= self.plum_decrement
        new_scale = RingMonitor.intensity_to_home_scale(plum_int)
        cur = getattr(data, 'predicted_home_scale', 0)
        if new_scale > cur:
            # 単純なint代入(GIL下で原子的)。カウントダウンスレッドは毎回ライブ参照する。
            data.predicted_home_scale = new_scale
            data.is_custom_prediction = True  # 実測由来であることを明示
            self.logger.warning(
                f"[PLUM] 予測震度を上方修正: {cur} → {new_scale} "
                f"(観測{plum_int:.1f}, 自宅{self.plum_radius_km}km圏)"
            )
            self._last_plum_scale = new_scale

    # === #3 S波到達ETA補正(観測アンカー方式・短縮のみ) ===
    def _apply_eta_correction(self, time_str: str, img: Image.Image, center_x: float, center_y: float):
        """自宅周辺リングへの前線到達を観測し、カウントダウンを観測ベースで前倒し補正する。
        - 観測ETA = 画像データ時刻 + 観測前線距離 / V (画像遅延を含めデータ時刻基準で算出)
        - s_wave_arrival_ts = min(EEW予測, 観測ETA) で「短縮のみ」適用(=遅延方向の危険を排除)
        - 速度の差分推定はせず前線位置で再アンカー(原点時刻・震央誤差を吸収)
        """
        data = getattr(self.eq_system, 'active_countdown_data', None)
        if not data or getattr(data, 's_wave_arrival_ts', 0.0) <= 0:
            return
        ring_max = self.ring_monitor.ring_maxes(img, self._match_color_intensity_cached, (center_x, center_y))
        # 内側(自宅に近い)リングから探し、最初に閾値を超えたリング半径=観測前線距離
        obs_front_km = None
        for r in self.ring_monitor.radii_km:  # 昇順=内側から
            if ring_max.get(r, -3.0) >= self.eta_arrival_intensity:
                obs_front_km = r
                break
        if obs_front_km is None:
            return
        try:
            data_ts = datetime.datetime.strptime(time_str, "%Y%m%d%H%M%S").timestamp()
        except Exception:
            return
        obs_eta = data_ts + obs_front_km / self.eta_velocity
        cur = data.s_wave_arrival_ts
        # 0.5秒以上の前倒しのみ反映(微小変動・揺り戻しを無視。単調短縮)
        if obs_eta < cur - 0.5:
            data.s_wave_arrival_ts = obs_eta
            self.logger.warning(
                f"[ETA] S波到達を観測で前倒し: 残り {cur - data_ts:.1f}s → {obs_eta - data_ts:.1f}s "
                f"(観測前線 {obs_front_km}km, lit≥{self.eta_arrival_intensity})"
            )

    # === #4 サイト地盤増幅サンプルの記録(予測への適用は保留) ===
    def _maybe_record_site_amplification(self, surf_img: Image.Image, time_str: str,
                                         center_x: float, center_y: float):
        """アラート中、低頻度で地中フル画像を取得し、最寄りの有意な地中ピクセルで
        co-located 増幅(地表-地中)を測定して較正サンプルに記録する。予測へは適用しない。"""
        now = time.time()
        if now - self._last_calib_fetch < self.site_calibration_interval:
            return
        self._last_calib_fetch = now
        bore_img = self._fetch_borehole_image(time_str)
        if bore_img is None:
            return
        try:
            res = measure_amplification(
                surf_img, bore_img, (center_x, center_y), self._match_color_intensity_cached,
                search_px=self.site_search_px, bore_min_intensity=self.site_bore_min_intensity,
                max_dist_km=self.site_calibrator.max_station_km,
            )
        finally:
            bore_img.close()
        if res is None:
            return  # 近傍に有意な地中ピクセルなし(カバレッジ無し)
        amp, dist, s_int, b_int, _px = res
        event_id = getattr(self, 'alert_start_time_str', '') or time_str
        if self.site_calibrator.record_sample(event_id, now, amp, dist, s_int, b_int):
            self.logger.info(
                f"[SiteCalib] サンプル記録: 増幅{amp:+.1f}(地表{s_int:.1f}-地中{b_int:.1f}) "
                f"最寄り地中{dist:.1f}km event={event_id} | {self.site_calibrator.status()}"
            )

    # === #6 到達確定フェイルセーフ(観測ベース・地中不要) ===
    def _maybe_trigger_arrival_failsafe(self, img: Image.Image, center_x: float, center_y: float):
        """カウントダウン中、自宅近傍(arrival_failsafe_radius_km圏)が arrival_failsafe_intensity
        以上に達したら、EEW計算カウントダウンと独立に到達アラートを発火する(1イベント1回)。"""
        data = getattr(self.eq_system, 'active_countdown_data', None)
        if not data:
            return
        near = self.ring_monitor.max_within(
            img, self._match_color_intensity_cached, (center_x, center_y),
            self.arrival_failsafe_radius_km
        )
        if near >= self.arrival_failsafe_intensity:
            self.eq_system.trigger_observed_arrival(getattr(data, 'event_id', ''))

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

    def _start_video_generation(self, target_dir: str, generate_gif: bool = True, generate_mp4: bool = True, on_complete=None, on_progress=None):
        """ アラート終了時にバックグラウンドでステータスバー付きのGIF(5fps)とMP4(1fps)を生成 """
        def worker():
            try:
                self.logger.info(f"Video generation started in: {target_dir}")
                
                # 画像リストの取得とソート (時系列順) — surface/ サブフォルダ対応
                _surface_dir = os.path.join(target_dir, "surface")
                _png_dir = _surface_dir if os.path.exists(_surface_dir) else target_dir
                search_pattern = os.path.join(_png_dir, "*.png")
                image_files = sorted(glob.glob(search_pattern))

                if not image_files:
                    msg = f"画像ファイルが見つかりません。\nディレクトリ内にPNGファイルが存在しません。\n対象: {target_dir}"
                    self.logger.warning(msg)
                    if on_complete: on_complete(False, msg)
                    return

                total_frames = len(image_files)

                # --- 1. メモリ上で画像にテキストを合成するジェネレータ ---
                def get_annotated_frames():
                    for i, p in enumerate(image_files):
                        try:
                            # ファイル名(例: eqlog_s_20231025120000_lv2.1.png)から情報を抽出
                            basename = os.path.basename(p)
                            _m = re.search(r"_(?:s_|b_)?(\d{14})_lv([-0-9.]+)\.png", basename)
                            if not _m:
                                continue
                            time_str = _m.group(1)
                            intensity = float(_m.group(2))
                            
                            # 画像を開き、ステータスバーを描画したものを返す
                            with Image.open(p) as im:
                                # 完全に新しいRGBオブジェクトとしてコピーを作成
                                frame = im.convert("RGB").copy() 
                                # このframeに対して描画を行う
                                self._draw_status_bar(frame, time_str, intensity)
                                if on_progress:
                                    on_progress(i + 1, total_frames, f"フレーム読込中... ({i + 1}/{total_frames})")
                                yield frame
                        except Exception as e:
                            self.logger.debug(f"Frame annotation error on {p}: {e}")
                            continue

                # 全フレームをメモリに読み込む (ラズパイでも数十MB程度なので安全)
                frames = list(get_annotated_frames())
                if not frames:
                    if on_complete: on_complete(False, "Failed to load frames.")
                    return

                # --- 2. GIF生成 (5fps) ---
                if generate_gif:
                    gif_path = os.path.join(target_dir, "alert_summary.gif")
                    frames[0].save(
                        gif_path,
                        save_all=True,
                        append_images=frames[1:],
                        optimize=False,
                        duration=200, # 1000ms ÷ 5fps = 200ms
                        loop=0,
                        disposal=2  # 前フレームを破棄して新しいフレームを描画する設定
                    )
                    self.logger.info(f"GIF generated (5fps): {gif_path}")

                # --- 3. MP4生成 (1fps / FFmpegへメモリから直接Pipeで渡す) ---
                if generate_mp4:
                    mp4_path = os.path.join(target_dir, "alert_summary.mp4")
                    width, height = frames[0].size
                    
                    import shutil
                    import sys
                    ffmpeg_cmd = "ffmpeg"
                    if not shutil.which(ffmpeg_cmd):
                        possible_ffmpeg = os.path.join(sys.prefix, "Library", "bin", "ffmpeg.exe")
                        if os.path.exists(possible_ffmpeg):
                            ffmpeg_cmd = possible_ffmpeg
                        else:
                            for base in ["C:\\Miniconda3", "C:\\ProgramData\\Miniconda3", os.path.expanduser("~\\Miniconda3"), os.path.expanduser("~\\Anaconda3")]:
                                p = os.path.join(base, "Library", "bin", "ffmpeg.exe")
                                if os.path.exists(p):
                                    ffmpeg_cmd = p
                                    break

                    cmd = [
                        ffmpeg_cmd, "-y",
                        "-f", "rawvideo",           # 生のピクセルデータとして入力
                        "-vcodec", "rawvideo",
                        "-s", f"{width}x{height}",  # 解像度
                        "-pix_fmt", "rgb24",        # ピクセルフォーマット
                        "-framerate", "1",          # 1fps
                        "-i", "-",                  # 標準入力(Pipe)から読み込み
                        "-c:v", "libx264",
                        "-preset", "medium",        # ultrafastより少し圧縮効率が良い（ファイルサイズが小さくなる）
                        "-threads", "2",            # ラズパイの4コア中2コアを使用（システムを完全フリーズさせない）
                        "-crf", "15",               # 画質係数（0〜51, 低いほど高画質.）
                        "-tune", "animation",       # アニメ・グラフィック向け最適化（線をくっきりさせる）
                        "-pix_fmt", "yuv420p",
                        mp4_path
                    ]
                    
                    # サブプロセスを起動
                    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    
                    # Python側からFFmpegに描画済みのフレームデータを直接流し込む
                    for frame in frames:
                        process.stdin.write(frame.tobytes())
                    
                    out, err = process.communicate() # 処理完了を待つ
                    
                    if process.returncode == 0:
                        self.logger.info(f"MP4 generated (1fps): {mp4_path}")
                    else:
                        err_msg = err.decode('utf-8', errors='ignore') if err else "Unknown FFmpeg error"
                        self.logger.error(f"FFmpeg failed: {err_msg}")
                        if on_complete: on_complete(False, f"FFmpegエラー: {err_msg}")
                        return

                if on_complete:
                    on_complete(True, "動画の作成が完了しました。")

            except FileNotFoundError as e:
                # ffmpegがインストールされていない場合の[WinError 2]などを拾う
                if "ffmpeg" in str(e) or getattr(e, 'winerror', None) == 2:
                    msg = "FFmpegがインストールされていないか、システムに認識されていません。\nMP4動画を作成するにはFFmpegが必要です。\n\n※GIF画像のみであれば作成可能です。"
                else:
                    msg = f"ファイルが見つかりません: {e}"
                self.logger.error(f"Video generation error: {msg}")
                if on_complete: on_complete(False, msg)
            except Exception as e:
                self.logger.error(f"Video generation error: {e}")
                if on_complete: on_complete(False, f"エラーが発生しました: {e}")

        # メインループをブロックしないよう別スレッドで実行
        threading.Thread(target=worker, daemon=True).start()