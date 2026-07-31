import json
import time
import logging
import os
from collections import deque
from unittest.mock import MagicMock, patch

# main からクラスをインポート
from eq_eew_axis import AxisEEWClient

from eq_axis_logger_config import init_logging

# === テスト設定 ===
# 実行したいシナリオファイルのリスト（順番に実行）
SCENARIO_FILES = [
    "test_eq_axis_scenario.json",
    "test_eq_axis_scenario_kanto.json",
    "test_eq_axis_scenario_miyagi.json"
]
WAIT_SECONDS = 3  # 各データ受信後の待機時間(秒)

# ログ設定
# テスト実行前にロガーをテストモードで初期化
# ファイル名: eq_axis_test.log
# backup_count: 7 (テストログは7日分だけ残す設定など)
init_logging(log_filename="eq_axis_test.log", backup_count=0)
logger = logging.getLogger(__name__)

def load_test_data(filepath):
    """ JSONファイルを読み込み文字列として返す """
    if not os.path.exists(filepath):
        logger.error(f"File not found: {filepath}")
        return None
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.dumps(json.load(f))

class MockWebSocketScenario:
    """
    複数のシナリオを順次実行するためのモックWebSocket
    """
    # 実行待ちのシナリオキュー (クラス変数として共有)
    scenario_queue = deque(SCENARIO_FILES)
    
    def __init__(self, url, header, on_open, on_message, on_error, on_close):
        self.on_open = on_open
        self.on_message = on_message
        self.on_close = on_close
        self.id = len(SCENARIO_FILES) - len(MockWebSocketScenario.scenario_queue) + 1

    def run_forever(self, ping_interval=60, ping_timeout=10):
        """
        1回の接続セッションをシミュレート
        """
        # キューが空ならテスト終了
        if not MockWebSocketScenario.scenario_queue:
            logger.info(">>> 全シナリオ完了．テストを終了します．")
            raise KeyboardInterrupt

        # 次のシナリオファイル名を取得
        current_file = MockWebSocketScenario.scenario_queue.popleft()
        
        logger.info(f"\n--- [Session #{self.id}] Start Scenario: {current_file} ---")

        # 1. 接続確立
        if self.on_open:
            self.on_open(self)

        # 2. サーバー挨拶 (hello)
        if self.on_message:
            time.sleep(0.5)
            self.on_message(self, "hello")

        # 3. シナリオデータ注入
        json_data = load_test_data(current_file)
        if self.on_message and json_data:
            time.sleep(1.0) # 少し間を空ける
            self.on_message(self, json_data)

        # 4. 待機 (処理の確認時間)
        logger.info(f"--- [Session #{self.id}] Waiting {WAIT_SECONDS}s... ---")
        time.sleep(WAIT_SECONDS)

        # 5. 切断して次の再接続を誘発
        # (キューがまだ残っていれば切断, なければループ先頭で終了)
        if MockWebSocketScenario.scenario_queue:
            logger.info(f"--- [Session #{self.id}] Closing connection to switch scenario ---")
            if self.on_close:
                self.on_close(self, 1000, "Scenario Finished")
        else:
            # 最後のシナリオが終わった後のループのために一旦閉じる
            pass 

def run_multi_test():
    print(f"=== AXIS Multi-Scenario Test (Files: {len(SCENARIO_FILES)}) ===\n")

    # 本物のsleep関数を退避 (再帰エラー防止)
    original_sleep = time.sleep

    # モックの適用
    with patch('eq_eew_axis.TokenManager') as MockTM, \
        patch('eq_eew_axis.websocket.WebSocketApp', side_effect=MockWebSocketScenario) as MockWS, \
        patch('eq_eew_axis.time.sleep', side_effect=lambda x: original_sleep(x if x < 5 else 0.1)):

        # トークンマネージャーのダミー設定
        instance = MockTM.return_value
        instance.access_token = "TEST_TOKEN_MULTI"

        client = AxisEEWClient()
        
        try:
            # クライアント実行 (再接続ループに入り, 次々とシナリオをこなす)
            client.run()
        except KeyboardInterrupt:
            logger.info("Test finished successfully.")

if __name__ == "__main__":
    run_multi_test()