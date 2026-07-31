import unittest
from unittest.mock import MagicMock, patch, mock_open
import json
import sys
import os

# パス設定
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

SRC_PATH = os.path.join(PROJECT_ROOT, 'eq_src')
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from eq_eew_wolfx import WolfxWatcher
from eq_data import EqType

class TestWolfxWatcher(unittest.TestCase):

    def setUp(self):
        self.mock_callback = MagicMock()
        
        # 設定ファイルの定数をテスト用にパッチ
        self.patcher_region = patch('eq_eew_wolfx.HOME_REGION_NAME', "千葉県北西部")
        self.patcher_targets = patch('eq_eew_wolfx.TARGET_HYPOCENTER_CODES', [287, 300]) # 287:宮城県沖, 300:茨城県南部を監視対象とする
        
        self.mock_region = self.patcher_region.start()
        self.mock_targets = self.patcher_targets.start()
        
        # WolfxWatcherのインスタンス化
        self.watcher = WolfxWatcher(self.mock_callback)
        
        # 震央対応表(JSON)の読み込みをモック化（ファイルI/O依存を排除）
        self.watcher.hypocenter_map = {
            "宮城県沖": "287",
            "茨城県南部": "300",
            "北海道東方沖": "100"  # 監視対象外のコード
        }

    def tearDown(self):
        self.patcher_region.stop()
        self.patcher_targets.stop()

    def _load_or_fallback_json(self, filename: str, fallback_data: dict) -> dict:
        """
        同一ディレクトリにダミーJSONがあればそれを読み込み、
        無ければフォールバック用の辞書データを返すヘルパーメソッド
        """
        filepath = os.path.join(os.path.dirname(__file__), filename)
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Failed to load {filename}: {e}")
        return fallback_data

    @patch('eq_eew_wolfx.os.makedirs') # ディレクトリ作成をブロック
    @patch('eq_eew_wolfx.open', new_callable=mock_open) # ログファイルの書き込みをブロック
    def test_process_forecast_target(self, mock_file, mock_makedirs):
        """
        正常なEEW予報（対象地域）が正しくパースされるかテスト
        """
        fallback_data = {
            "EventID": "20260404193655",
            "Hypocenter": "宮城県沖", # TARGET_HYPOCENTER_CODES に含まれる(287)
            "Magunitude": 5.1,
            "Depth": 70,
            "MaxIntensity": "4",
            "WarnArea": [
                {"Chiiki": "千葉県北西部", "Shindo1": "3"}
            ],
            "isWarn": False, # 予報
            "isCancel": False
        }
        
        sample_json = self._load_or_fallback_json("eq_test_wolfx_dummy_forecast.json", fallback_data)
        self.watcher._process_data(sample_json)

        self.mock_callback.assert_called_once()
        eq_data = self.mock_callback.call_args[0][0]
        
        self.assertEqual(eq_data.type, EqType.FORECAST)
        self.assertEqual(eq_data.hypocenter_name, "宮城県沖")
        self.assertEqual(eq_data.max_intensity, "4")
        self.assertEqual(eq_data.predicted_home_scale, 30) # 震度3 -> 30

    @patch('eq_eew_wolfx.os.makedirs')
    @patch('eq_eew_wolfx.open', new_callable=mock_open)
    def test_process_forecast_ignored(self, mock_file, mock_makedirs):
        """
        監視対象外の震央からのEEW予報がスキップされるかテスト
        """
        fallback_data = {
            "EventID": "20260404193656",
            "Hypocenter": "北海道東方沖", # TARGET_HYPOCENTER_CODES に含まれない(100)
            "Magunitude": 4.0,
            "Depth": 10,
            "MaxIntensity": "2",
            "WarnArea": [],
            "isWarn": False, # 予報
            "isCancel": False
        }
        
        sample_json = self._load_or_fallback_json("eq_test_wolfx_dummy_forecast_ignore.json", fallback_data)
        self.watcher._process_data(sample_json)

        # 対象外なのでコールバックは呼ばれないはず
        self.mock_callback.assert_not_called()

    @patch('eq_eew_wolfx.os.makedirs')
    @patch('eq_eew_wolfx.open', new_callable=mock_open)
    def test_process_warning(self, mock_file, mock_makedirs):
        """
        EEW警報（isWarn=True）の場合、対象外の震央でも無条件で処理されるかテスト
        """
        fallback_data = {
            "EventID": "20260404193657",
            "Hypocenter": "北海道東方沖", # 対象外だが警報なので処理されるべき
            "Magunitude": 7.0,
            "Depth": 10,
            "MaxIntensity": "6弱",
            "WarnArea": [
                {"Chiiki": "千葉県北西部", "Shindo1": "4"}
            ],
            "isWarn": True, # 警報
            "isCancel": False
        }
        
        sample_json = self._load_or_fallback_json("eq_test_wolfx_dummy_warning.json", fallback_data)
        self.watcher._process_data(sample_json)

        self.mock_callback.assert_called_once()
        eq_data = self.mock_callback.call_args[0][0]
        
        self.assertEqual(eq_data.type, EqType.WARNING)
        self.assertEqual(eq_data.predicted_home_scale, 40) # 震度4 -> 40

    @patch('eq_eew_wolfx.os.makedirs')
    @patch('eq_eew_wolfx.open', new_callable=mock_open)
    def test_process_cancel(self, mock_file, mock_makedirs):
        """
        キャンセル報が正しく処理されるかテスト
        """
        fallback_data = {
            "EventID": "20260404193658",
            "isCancel": True
        }
        
        sample_json = self._load_or_fallback_json("eq_test_wolfx_dummy_cancel.json", fallback_data)
        self.watcher._process_data(sample_json)
        
        self.mock_callback.assert_called_once()
        eq_data = self.mock_callback.call_args[0][0]
        self.assertEqual(eq_data.type, EqType.CANCEL)


if __name__ == "__main__":
    unittest.main()