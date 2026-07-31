import os
import json
import threading
import time
import datetime
import websocket
from typing import Callable, Optional

# Project internal modules
from eq_data import JmaEqData, EqSource, EqType
from eq_config.eq_config import HOME_REGION_NAME, TARGET_HYPOCENTER_CODES, get_wolfx_log_dir, HOME_LAT, HOME_LON, HOME_GROUND_CORRECTION, PROJECT_ROOT
from eq_utils import EqLogger, GeoUtils
import math

# システムログの出力先（Windowsビルドでは AppData。無ければ setup_logger の既定を使う）
try:
    from eq_config.eq_config import LOG_DIR as _WOLFX_LOG_DIR
except ImportError:
    _WOLFX_LOG_DIR = None

# Configuration loading module (thresholds etc. can be passed at runtime or loaded here. This time, designed to run independently.)
# Although settings are expected to be passed from eq_main.py, it is designed to run independently here.

WOLFX_URL = "wss://ws-api.wolfx.jp/jma_eew"

class WolfxWatcher:
    def __init__(self, callback: Callable[[JmaEqData], None]):
        """
        param callback: Function to call when data is received (argument is JmaEqData
        """
        self.logger = (EqLogger.setup_logger("WolfxWatcher", log_dir=_WOLFX_LOG_DIR)
                       if _WOLFX_LOG_DIR else EqLogger.setup_logger("WolfxWatcher"))
        self.callback = callback
        self.ws: Optional[websocket.WebSocketApp] = None
        self.keep_running = True
        self.status = "disconnected"  # "disconnected", "connecting", "connected"
        
        # 指数バックオフ用の設定
        self.delays = [0.2, 0.5, 1.0, 5.0, 10.0, 20.0, 30.0, 60.0, 300.0, 600.0]
        self.delay_index = 0
        
        # 震央対応表JSONの読み込み
        # PROJECT_ROOT は config が frozen(exe) 対応で解決する（exeでは _internal を指す）
        json_path = os.path.join(PROJECT_ROOT, "eq_assets", "eq_hypocenter_map_jpcode.json")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                self.hypocenter_map = json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load hypocenter map: {e}")
            self.hypocenter_map = {}

        # ログ保存用ディレクトリの設定と作成
        # 関数を呼び出してSSD/SDの判定結果を取得
        self.log_dir = get_wolfx_log_dir()
        os.makedirs(self.log_dir, exist_ok=True)

    def start(self):
        """
        Start WebSocket connection in a separate thread
        """
        thread = threading.Thread(target=self._run_forever, daemon=True)
        thread.start()
        self.logger.info("Wolfx watcher started.")

    def _run_forever(self):
        """
        Loop to keep reconnecting even if disconnected
        """
        while self.keep_running:
            self.status = "connecting"
            current_delay = self.delays[self.delay_index]
            
            # 1秒以上の待ち時間が発生する場合のみ接続開始ログを表示
            if current_delay >= 1.0:
                self.logger.info(f"Connecting to {WOLFX_URL} ...")
            else:
                self.logger.debug(f"Connecting to {WOLFX_URL} ...")

            # Initialize WebSocketApp
            self.ws = websocket.WebSocketApp(
                WOLFX_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close
            )
            
            # 標準のPing機能を無効化し, 手動ハートビート（_on_message内）に切り替え
            self.ws.run_forever()
            
            if self.keep_running:
                # 待ち時間の判定に基づきログレベルを調整
                if current_delay >= 1.0:
                    self.logger.warning(f"Connection lost. Reconnecting in {current_delay}s...")
                else:
                    self.logger.debug(f"Quick reconnection in {current_delay}s...")
                
                time.sleep(current_delay)
                
                # 次回の待ち時間を増加（リストの最大値で止める）
                if self.delay_index < len(self.delays) - 1:
                    self.delay_index += 1

    def stop(self):
        self.keep_running = False
        if self.ws:
            self.ws.close()

    def _on_open(self, ws):
        self.status = "connected"
        # 接続成功時に、過去の遅延が大きかったか初回接続時のみINFOで出す
        if getattr(self, '_is_first_connect', True) or self.delays[self.delay_index] >= 1.0:
            self.logger.info("WebSocket Connected.")
            self._is_first_connect = False
        else:
            self.logger.debug("WebSocket Reconnected quickly.")
        
        # 接続成功時にバックオフのインデックスをリセット
        self.delay_index = 0

    def _on_error(self, ws, error):
        self.status = "disconnected"
        # 瞬断時はエラーログを出さないよう現在の遅延設定が1秒以上の場合のみ出力
        if self.delays[self.delay_index] >= 1.0:
            self.logger.error(f"WebSocket Error: {error}")
        else:
            self.logger.debug(f"WebSocket Silent Error (Quick recovery expected): {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self.status = "disconnected"
        # 瞬断時はクローズログを出さないよう制御
        if self.delays[self.delay_index] >= 1.0:
            self.logger.info("WebSocket Closed.")
        else:
            self.logger.debug("WebSocket Closed (Silent).")

    def _on_message(self, ws, message):
        """
        Process received message
        """
        try:
            data = json.loads(message)
            
            # ハートビート対応
            if data.get("type") == "heartbeat":
                # サーバーからのハートビートに対しミリ秒タイムスタンプを付与してpongを返信
                pong = {
                    "type": "pong",
                    "timestamp": str(int(time.time() * 1000))
                }
                ws.send(json.dumps(pong))
                return

            self._process_data(data)
        except Exception as e:
            self.logger.error(f"Message processing error: {e}")
            
    def _process_data(self, data: dict):
        """
        Parse JSON data according to Wolfx JMA EEW format
        """
        # 1. データの妥当性チェック
        # EventIDやTitleの有無でEEWと判断
        if not data.get("EventID"):
            return
        
        # 生データのJSONファイル保存
        # 現在の日付(YYYYMMDD)を取得し, 日別ディレクトリを作成
        current_date_str = datetime.datetime.now().strftime("%Y%m%d")
        daily_log_dir = os.path.join(self.log_dir, current_date_str)
        os.makedirs(daily_log_dir, exist_ok=True)

        # 同一EventIDで複数回（第1報、第2報...）来ることを考慮し, 報番号(Serial)またはタイムスタンプをファイル名に含める
        serial = data.get("Serial", int(time.time() * 1000))
        filename = f"{data.get('EventID')}_{serial}.json"
        filepath = os.path.join(self.log_dir, filename)

        # 保存先のディレクトリを daily_log_dir に変更
        filepath = os.path.join(daily_log_dir, filename)

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                # indent=4 を指定して可読性の高いフォーマットで保存
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            self.logger.error(f"Failed to save raw data to {filepath}: {e}")

        # キャンセル報のチェック
        if data.get("isCancel", False):
            eq_data = JmaEqData(
                source=EqSource.WOLFX,
                type=EqType.CANCEL,
                event_id=data.get("EventID", "不明"),
                hypocenter_name="-"
            )
            self.callback(eq_data)
            return

        # 2. 警報/予報の判定
        # isWarnフラグを優先
        is_warn = data.get("isWarn", False)
        eq_type = EqType.WARNING if is_warn else EqType.FORECAST

        # 3. 震源情報の取得（ルート階層から取得）
        name = data.get("Hypocenter", "unknown")

        # 4. 震央コード(config)によるフィルタリング 
        if not is_warn:
            code_str = self.hypocenter_map.get(name)
            if code_str:
                try:
                    code_int = int(code_str)
                    # 指定したターゲットリストに含まれているかチェック
                    if code_int not in TARGET_HYPOCENTER_CODES:
                        # 予報かつ対象外の地域なのでスキップ
                        self.logger.debug(f"Ignored Forecast: Hypocenter '{name}' (Code: {code_int}) is not in target list.")
                        return
                except ValueError:
                    self.logger.error(f"Invalid code format in map for '{name}': {code_str}")
                    return
            else:
                # 予報かつマップに存在しない震央の場合スキップ
                self.logger.debug(f"Ignored Forecast: Hypocenter '{name}' not found in map.")
                return
        
        # ドキュメント仕様: "Magunitude" (スペル注意)
        mag = data.get("Magunitude", 0.0)
        
        # ドキュメント仕様: Depthは数値型
        depth = str(data.get("Depth", "0"))
        
        # ドキュメント仕様: MaxIntensityは文字列 ("2", "5弱"など想定)
        max_int = data.get("MaxIntensity", "0")

        # 5. 自宅地点の予測震度検索
        # "WarnArea" を使用
        # WarnArea: [{"Chiiki": "地域名", "Shindo1": "震度", ...}, ...] の形式と想定
        warn_areas = data.get("WarnArea", [])
        home_scale = 0
        
        # 震度文字列を内部用数値(10,20...45...)に変換するヘルパー
        def str_scale_to_int(s_scale):
            mapping = {
                "1": 10, "2": 20, "3": 30, "4": 40,
                "5弱": 45, "5-": 45, "5強": 50, "5+": 50,
                "6弱": 55, "6-": 55, "6強": 60, "6+": 60,
                "7": 70
            }
            return mapping.get(s_scale, 0)

        for area in warn_areas:
            # HOME_REGION_NAME (例: "千葉県北西部") と一致するか確認
            if area.get("Chiiki") == HOME_REGION_NAME:
                # "Shindo1" が最大震度(文字列)と仮定
                shindo_str = area.get("Shindo1", "0")
                home_scale = str_scale_to_int(shindo_str)
                break

        # S-wave arrival timestamp calculation
        origin_time_str = data.get("OriginTime", "")
        origin_ts = 0.0
        try:
            dt = datetime.datetime.strptime(origin_time_str, "%Y/%m/%d %H:%M:%S")
            origin_ts = dt.timestamp()
        except Exception:
            pass

        eq_lat = data.get("Latitude", 0.0)
        eq_lon = data.get("Longitude", 0.0)
        eq_depth_val = float(data.get("Depth", 0.0))
        
        s_wave_ts = 0.0
        if origin_ts > 0 and eq_lat > 0 and eq_lon > 0:
            s_wave_ts = GeoUtils.calculate_s_wave_arrival_ts(origin_ts, eq_lat, eq_lon, eq_depth_val, HOME_LAT, HOME_LON)

        # 独自計算フォールバック (予想震度が無い場合)
        is_custom_prediction = False
        if home_scale == 0 and mag > 0.0 and eq_lat > 0 and eq_lon > 0:
            epi_dist = GeoUtils.calculate_distance(eq_lat, eq_lon, HOME_LAT, HOME_LON)
            hypo_dist = math.sqrt(epi_dist**2 + eq_depth_val**2)
            if hypo_dist > 0:
                # 案C: WarnAreaの最近傍地域から外挿
                calc_intensity = GeoUtils.estimate_intensity_from_warnarea(
                    warn_areas, eq_lat, eq_lon, eq_depth_val, HOME_LAT, HOME_LON
                )
                method = "WarnArea外挿"
                # 案C が使えない場合は案B3（勝俣式 + 北東方向異常震域補正）
                if calc_intensity is None:
                    calc_intensity = GeoUtils.calc_fallback_intensity(
                        mag, hypo_dist,
                        ground_correction=HOME_GROUND_CORRECTION,
                        eq_lat=eq_lat, eq_lon=eq_lon,
                        home_lat=HOME_LAT, home_lon=HOME_LON
                    )
                    method = "B3式"

                if calc_intensity >= 7.0: home_scale = 70
                elif calc_intensity >= 6.5: home_scale = 60
                elif calc_intensity >= 6.0: home_scale = 55
                elif calc_intensity >= 5.5: home_scale = 50
                elif calc_intensity >= 5.0: home_scale = 45
                elif calc_intensity >= 4.0: home_scale = 40
                elif calc_intensity >= 3.0: home_scale = 30
                elif calc_intensity >= 2.0: home_scale = 20
                elif calc_intensity >= 1.0: home_scale = 10
                else: home_scale = 0

                if home_scale > 0:
                    is_custom_prediction = True
                    self.logger.info(f"Fallback intensity [{method}]: {calc_intensity:.2f} -> home_scale {home_scale}")

        # 6. オブジェクト生成してコールバック
        eq_data = JmaEqData(
            source=EqSource.WOLFX,
            type=eq_type,
            event_id=data.get("EventID", "不明"),
            hypocenter_name=name,
            magnitude=mag,
            depth=depth,
            max_intensity=max_int,
            predicted_home_scale=home_scale,
            is_final=data.get("isFinal", False),
            original_text=json.dumps(data, ensure_ascii=False),
            s_wave_arrival_ts=s_wave_ts,
            is_custom_prediction=is_custom_prediction
        )

        self.logger.info(f"EEW Received: {name} M{mag} Max:{max_int} HomeScale:{home_scale}")
        self.callback(eq_data)

    def get_status(self) -> str:
        return self.status