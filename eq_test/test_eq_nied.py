import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import shutil
import time
from io import BytesIO
from PIL import Image

# プロジェクトルートをパスに追加
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.append(project_root)
sys.path.append(os.path.join(project_root, 'eq_src'))

# テスト用の画像保存ディレクトリ
TEST_IMG_DIR = os.path.abspath("./temp_test_img")

class TestNiedMonitor(unittest.TestCase):

    def setUp(self):
        # Mock settings
        self.settings = {
            "radius_pixel": 10,
            "trigger_pixels": 10, # Respond if 10 or more pixels are triggered
            "trigger_intensity": 1.0,
            "history_duration_minutes": 1,
            "history_interval_seconds": 1
        }
        self.mock_callback = MagicMock()
        
        # Patch constants in the config module
        self.patcher = patch('eq_monitor_nied.config')
        self.mock_config = self.patcher.start()
        # 最新の仕様に合わせて変数名を変更
        self.mock_config.NIED_HOME_X = 50
        self.mock_config.NIED_HOME_Y = 50
        self.mock_config.MONITOR_IMAGE_DIR = TEST_IMG_DIR
        self.mock_config.COLOR_MAP_PATH = "dummy.json"

        from eq_monitor_nied import NiedMonitor
        
        with patch.object(NiedMonitor, '_load_color_map', return_value=[]):
            # 今回は履歴保存等のファイル書き込みもテストするため saves_history=True で初期化し, 
            # _process_current_image 経由でディレクトリ作成ロジックを叩く
            self.monitor = NiedMonitor(self.settings, self.mock_callback, saves_history=True)
        
        # Manually inject color map for testing
        # Blue(0,0,255) -> Intensity 1.0, Red(255,0,0) -> Intensity 5-Upper
        self.monitor.color_map = [
            {"R": 0, "G": 0, "B": 255, "Intensity": 1.0},
            {"R": 255, "G": 0, "B": 0, "Intensity": 5.2}
        ]

    def tearDown(self):
        self.patcher.stop()

    def test_direction_calculation(self):
        """Test the direction calculation logic (Mathematical verification)"""
        # dx, dy are in the image coordinate system (Right is +, Down is +)
        self.assertEqual(self.monitor._calculate_direction(10, 0), "東")
        self.assertEqual(self.monitor._calculate_direction(0, 10), "南")
        self.assertEqual(self.monitor._calculate_direction(-10, 0), "西")
        self.assertEqual(self.monitor._calculate_direction(0, -10), "北")
        self.assertEqual(self.monitor._calculate_direction(10, 10), "南東")

    def test_analyze_image_trigger(self):
        """Image Analysis: Verify if drawing red pixels triggers an alert"""
        
        # クロップ画像を作成 (21x21)
        crop_img = Image.new("RGB", (21, 21), (0, 0, 0))
        crop_pixels = crop_img.load()
        
        # 10ピクセル以上の赤いドットを「西（左）」に描画
        # x=5 (左側) の列に, y=5から15まで11個描画
        for i in range(11):
            crop_pixels[5, 5 + i] = (255, 0, 0)
        
        # Run analysis (前回の改修で戻り値がTupleに変わったため受け取り方を修正)
        intensity, condition_met = self.monitor._analyze_cropped_image(crop_img, "test_time")
        
        # Verification
        self.assertTrue(condition_met, "条件を満たしたためTrueが返るべきです")
        self.mock_callback.assert_called_once()
        args = self.mock_callback.call_args[0]
        eq_data = args[0]
        
        self.assertEqual(eq_data.source.name, "NIED")
        self.assertEqual(eq_data.hypocenter_name, "西")
        self.assertEqual(eq_data.max_intensity, "5弱")

    @patch('eq_monitor_nied.requests.Session.get')
    def test_process_alert_flow(self, mock_get):
        """ アラート発生時のディレクトリ作成と画像保存の一連のフローをテスト """
        # ダミーのレスポンスを設定
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        
        # ダミー画像 (100x100) を作成し, ホーム座標 (50,50) の左側に赤いドットを描画
        img = Image.new("RGB", (100, 100), (0, 0, 0))
        pixels = img.load()
        for i in range(15):
            pixels[45, 50 + i] = (255, 0, 0)
            
        # 画像をGIFバイト列に変換してモックレスポンスにセット
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='GIF')
        mock_resp.content = img_byte_arr.getvalue()
        mock_get.return_value = mock_resp

        # 現在時刻で画像処理を実行
        ts_int = int(time.time())
        self.monitor._process_current_image(ts_int)

        # アラート状態に遷移していることの確認
        self.assertTrue(self.monitor.is_alert_active)
        self.assertNotEqual(self.monitor.current_alert_dir, "")
        
        # ディレクトリと画像が実際に作成されていることを確認
        self.assertTrue(os.path.exists(self.monitor.current_alert_dir))
        surface_dir = os.path.join(self.monitor.current_alert_dir, "surface")
        self.assertTrue(os.path.exists(surface_dir))
        files = os.listdir(surface_dir)
        self.assertTrue(any(f.startswith("eqlog_s_") and f.endswith(".png") for f in files))


if __name__ == "__main__":
    # exit=Falseにすることでテスト完了後もスクリプトを続行
    unittest.main(exit=False)
    
    # ---------------------------------------------------------
    # テスト終了後のファイル整理（削除確認）
    # ---------------------------------------------------------
    if os.path.exists(TEST_IMG_DIR):
        print("\n" + "="*50)
        print("テストが完了しました。")
        ans = input(f"テスト用の出力ディレクトリ '{TEST_IMG_DIR}' を削除しますか？ [y/N]: ")
        if ans.strip().lower() in ['y', 'yes']:
            try:
                shutil.rmtree(TEST_IMG_DIR)
                print(f"-> '{TEST_IMG_DIR}' とその中身をすべて削除しました。")
            except Exception as e:
                print(f"-> 削除中にエラーが発生しました: {e}")
        else:
            print("-> 削除をスキップしました。テスト出力は保持されます。")
        print("="*50 + "\n")