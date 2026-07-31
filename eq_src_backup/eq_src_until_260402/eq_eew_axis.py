import json
import time
import logging
import websocket
from eq_axis_token_manager import TokenManager  # 作成したモジュールをインポート
from eq_axis_logger_config import init_logging

# ロガー初期化 (本番用設定)
# ファイル名: eq_axis.log
# backup_count: 0 (自動削除なし) または 30 (30日保存) など任意に設定
init_logging(log_filename="eq_axis.log", backup_count=0)
logger = logging.getLogger(__name__)

# 定数
WS_URL = "wss://ws.axis.prioris.jp/socket"
TOKEN_FILE = "token.json"

class AxisEEWClient:
    def __init__(self):
        # 別ファイルのクラスを利用してトークン管理
        self.tm = TokenManager(filepath=TOKEN_FILE)
        
        # 再接続待機時間の管理用
        self.retry_interval = 5
        self.max_retry_interval = 120

    def on_open(self, ws):
        logger.info("### Connection Opened ###")
        self.retry_interval = 5

    def on_message(self, ws, message):
        # 接続確認用メッセージ
        if message == "hello":
            logger.info("Server greeting received: 'hello'")
            return
        # サーバーエラーメッセージ
        if message.startswith("error:"):
            logger.error(f"Server Error: {message}")
            return

        try:
            data = json.loads(message)
            title = data.get("Title", "不明なデータ")
            
            if "緊急地震速報" in title:
                serial = data.get("Serial", 0)
                hypo = data.get("Hypocenter", {}).get("Name", "不明")
                mag = data.get("Magnitude", "-")
                logger.info(f"【受信】{title} 第{serial}報: 震源{hypo} M{mag}")
                
                # ここで外部連携などを実行
                # self.process_earthquake_data(data)
                
            else:
                logger.debug(f"Received JSON: {message}")

        except json.JSONDecodeError:
            logger.error(f"JSON Decode Error: {message}")

    def on_error(self, ws, error):
        logger.error(f"WebSocket Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        logger.info(f"### Connection Closed ### Code: {close_status_code}, Msg: {close_msg}")

    def run(self):
        """メインループ"""
        logger.info("Starting AxisEEWClient with TokenManager...")
        
        while True:
            try:
                # 接続前にトークン状態をチェック・更新
                self.tm.refresh_if_needed()

                # WebSocket接続
                header = [f"Authorization: Bearer {self.tm.access_token}"]
                
                ws = websocket.WebSocketApp(
                    WS_URL,
                    header=header,
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )

                # Pingによる接続維持
                ws.run_forever(ping_interval=60, ping_timeout=10)

            except Exception as e:
                logger.error(f"Critical Loop Error: {e}")

            # 切断後の待機
            logger.info(f"Reconnecting in {self.retry_interval} seconds...")
            time.sleep(self.retry_interval)
            self.retry_interval = min(self.retry_interval * 2, self.max_retry_interval)

if __name__ == "__main__":
    client = AxisEEWClient()
    try:
        client.run()
    except KeyboardInterrupt:
        logger.info("Stopping client by user request.")