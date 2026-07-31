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
        en: Parse JSON data and call callback only when necessary
        """
        msg_type = data.get("Type")
        
        # Ignore heartbeat etc.
        if msg_type not in ["ScalePrompt", "Hypocenter"]: 
            return

        # Check for cancellation report
        if data.get("isCancel", False):
            eq_data = JmaEqData(
                source=EqSource.WOLFX,
                type=EqType.CANCEL,
                event_id=data.get("EventID", "不明"),
                hypocenter_name="-"
            )
            self.callback(eq_data)
            return

        # Hypocenter information
        hypocenter = data.get("Hypocenter", {})
        name = hypocenter.get("Name", "unknown")
        mag = hypocenter.get("Magnitude", 0.0)
        depth = str(hypocenter.get("Depth", "0"))
        max_int = str(data.get("MaxIntensity", "0"))

        # Search for predicted intensity at home region
        # Search for the point where Addr matches HOME_REGION_NAME (e.g., "Chiba Prefecture Northwest") from the Points array
        points = data.get("Points", [])
        home_scale = 0
        for p in points:
            if p.get("Addr") == HOME_REGION_NAME:
                # Scale is in the format 10, 20, 30...
                home_scale = int(p.get("Scale", 0))
                break
        
        # Determine if it's a warning or forecast
        # Adjust criteria as needed, e.g., treat as warning if isWarn flag is True or home scale is high (40 or more = intensity 4 or more)
        # Here, we prioritize the flag in the data
        eq_type = EqType.WARNING if data.get("isWarn", False) else EqType.FORECAST

        # Create data object
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