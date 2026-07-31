import json
import threading
import time
import websocket
from typing import Callable, Optional

# Project internal modules
from eq_data import JmaEqData, EqSource, EqType
from eq_config.eq_config import HOME_REGION_NAME
from eq_utils import EqLogger

# Configuration loading module (thresholds etc. can be passed at runtime or loaded here. This time, designed to run independently.)
# Although settings are expected to be passed from eq_main.py, it is designed to run independently here.

WOLFX_URL = "wss://ws-api.wolfx.jp/jma_eew"

class WolfxWatcher:
    def __init__(self, callback: Callable[[JmaEqData], None]):
        """
        :param callback: Function to call when data is received (argument is JmaEqData
        """
        self.logger = EqLogger.setup_logger("WolfxWatcher")
        self.callback = callback
        self.ws: Optional[websocket.WebSocketApp] = None
        self.keep_running = True
        self.reconnect_delay = 5  # seconds to wait before reconnecting

    def start(self):
        """
        en: Start WebSocket connection in a separate thread
        """
        thread = threading.Thread(target=self._run_forever, daemon=True)
        thread.start()
        self.logger.info("Wolfx watcher started.")

    def _run_forever(self):
        """
        en: Loop to keep reconnecting even if disconnected
        """
        while self.keep_running:
            self.logger.info(f"Connecting to {WOLFX_URL} ...")
            # Initialize WebSocketApp
            self.ws = websocket.WebSocketApp(
                WOLFX_URL,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close
            )
            # Block and maintain connection
            self.ws.run_forever(ping_interval=60, ping_timeout=10)
            
            if self.keep_running:
                self.logger.warning(f"Connection lost. Reconnecting in {self.reconnect_delay}s...")
                time.sleep(self.reconnect_delay)

    def stop(self):
        self.keep_running = False
        if self.ws:
            self.ws.close()

    def _on_open(self, ws):
        self.logger.info("WebSocket Connected.")

    def _on_error(self, ws, error):
        self.logger.error(f"WebSocket Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self.logger.info("WebSocket Closed.")

    def _on_message(self, ws, message):
        """
        Process received message
        """
        try:
            data = json.loads(message)
            self._process_data(data)
        except Exception as e:
            self.logger.error(f"Message processing error: {e}")
            
    def _process_data(self, data: dict):
        """
        en: Parse JSON data according to Wolfx JMA EEW format
        """
        # 1. データの妥当性チェック
        # EventIDやTitleの有無でEEWと判断
        if not data.get("EventID"):
            return

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

        # 2. 震源情報の取得（ルート階層から取得）
        name = data.get("Hypocenter", "unknown")
        
        # ドキュメント仕様: "Magunitude" (スペル注意)
        mag = data.get("Magunitude", 0.0)
        
        # ドキュメント仕様: Depthは数値型
        depth = str(data.get("Depth", "0"))
        
        # ドキュメント仕様: MaxIntensityは文字列 ("2", "5弱"など想定)
        max_int = data.get("MaxIntensity", "0")

        # 3. 自宅地点の予測震度検索
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
        
        # 4. 警報/予報の判定
        # isWarnフラグを優先
        eq_type = EqType.WARNING if data.get("isWarn", False) else EqType.FORECAST

        # データオブジェクト作成
        eq_data = JmaEqData(
            source=EqSource.WOLFX,
            type=eq_type,
            event_id=data.get("EventID", "unknown"),
            hypocenter_name=name,
            magnitude=mag,
            depth=depth,
            max_intensity=max_int,
            predicted_home_scale=home_scale,
            is_final=data.get("isFinal", False),
            original_text=json.dumps(data, ensure_ascii=False)
        )

        self.logger.info(f"EEW Received: {name} M{mag} Max:{max_int} HomeScale:{home_scale}")
        self.callback(eq_data)