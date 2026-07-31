import unittest
from unittest.mock import MagicMock, patch, ANY
import sys
import os
import json

# --- パス設定 ---
# テスト実行時に eq_src や eq_config を読み込めるようにパスを通す
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(PROJECT_ROOT, "eq_src")

sys.path.append(PROJECT_ROOT)
sys.path.append(SRC_DIR)

# --- 外部モジュールのモック ---
# util_audio_player が存在しない環境でもテストできるよう、sys.modules にモックを注入
mock_audio_player = MagicMock()
sys.modules["util_audio_player"] = mock_audio_player

# --- テスト対象のインポート ---
# パス設定とモック注入の後に行う
from eq_main import EqSystem
from eq_data import JmaEqData, EqSource, EqType

class TestEqSystem(unittest.TestCase):

    def setUp(self):
        """各テストの前に実行されるセットアップ"""
        # 共通のモック設定
        self.mock_settings = {
            "eew_alert": {"enabled": True},
            "nied_monitor": {"enabled": True}
        }
        
        # ログ、Wolfx, NiedMonitor, 設定ロードをパッチする
        self.patcher_logger = patch("eq_main.EqLogger")
        self.patcher_wolfx = patch("eq_main.WolfxWatcher")
        self.patcher_nied = patch("eq_main.NiedMonitor")
        self.patcher_open = patch("builtins.open", new_callable=unittest.mock.mock_open, read_data=json.dumps(self.mock_settings))
        self.patcher_path_exists = patch("os.path.exists", return_value=True) # 音声ファイル等の存在チェックを常にTrueに

        self.mock_logger_cls = self.patcher_logger.start()
        self.mock_wolfx_cls = self.patcher_wolfx.start()
        self.mock_nied_cls = self.patcher_nied.start()
        self.mock_open = self.patcher_open.start()
        self.mock_path_exists = self.patcher_path_exists.start()

        # テスト対象のインスタンス化
        self.system = EqSystem()

    def tearDown(self):
        """各テストの後に実行されるクリーンアップ"""
        patch.stopall()

    def test_initialization(self):
        """初期化処理のテスト"""
        # ウォッチャーが正しく初期化されているか
        self.mock_wolfx_cls.assert_called_once()
        self.mock_nied_cls.assert_called_once()
        # 設定がロードされているか
        self.assertEqual(self.system.settings, self.mock_settings)

    def test_get_priority(self):
        """優先度判定ロジックのテスト"""
        # ダミーデータ作成ヘルパー
        def make_data(etype):
            d = MagicMock(spec=JmaEqData)
            d.type = etype
            d.is_simulated = False
            return d

        self.assertEqual(self.system._get_priority(make_data(EqType.CANCEL)), 4)
        self.assertEqual(self.system._get_priority(make_data(EqType.WARNING)), 3)
        self.assertEqual(self.system._get_priority(make_data(EqType.REALTIME)), 2)
        self.assertEqual(self.system._get_priority(make_data(EqType.FORECAST)), 1)
        
    def test_map_data_to_audio_list_warning(self):
        """WARNINGタイプの音声リスト生成テスト"""
        data = JmaEqData(
            source=EqSource.WOLFX,
            type=EqType.WARNING,
            event_id="test_id",
            hypocenter_name="千葉県北西部",
            max_intensity="5弱",
            predicted_home_scale=40 # 震度4
        )
        
        audio_list = self.system._map_data_to_audio_list(data)
        
        # 期待されるファイルが含まれているか確認
        # WARNINGの場合: 震度階級(3回) + 震源(Place/Fixed) + 最大震度
        self.assertTrue(any("v_scale_4.wav" in f for f in audio_list))
        self.assertTrue(any("v_fixed_strong_quake.wav" in f for f in audio_list))
        self.assertTrue(any("v_scale_5w.wav" in f for f in audio_list))

    def test_map_data_to_audio_list_cancel(self):
        """CANCELタイプの音声リスト生成テスト"""
        data = JmaEqData(
            source=EqSource.WOLFX,
            type=EqType.CANCEL,
            event_id="test_id",
            hypocenter_name=""
        )
        audio_list = self.system._map_data_to_audio_list(data)
        self.assertTrue(any("v_fixed_cancel.wav" in f for f in audio_list))

    @patch("threading.Thread")
    def test_on_earthquake_data_playback(self, mock_thread):
        """データ受信時に音声再生スレッドが起動するか"""
        data = JmaEqData(
            source=EqSource.WOLFX,
            type=EqType.WARNING, # 優先度3
            event_id="new_event",
            max_intensity="5強",
            predicted_home_scale=40,
            hypocenter_name="Dummy"
        )

        # 初期状態は優先度0なので、優先度3が来たら再生されるはず
        self.system.on_earthquake_data(data)

        # スレッド開始の確認
        mock_thread.assert_called_once()
        # 優先度が更新されたか
        self.assertEqual(self.system.current_max_priority, 3)
        self.assertEqual(self.system.last_event_id, "new_event")

    @patch("threading.Thread")
    def test_on_earthquake_data_ignore_low_priority(self, mock_thread):
        """低い優先度のデータが無視されるか"""
        self.system.current_max_priority = 3
        self.system.is_playing = True
        self.system.current_playing_priority = 3
        self.system.last_event_id = "same_event"
        
        # 新たに「予報(2)」が来た場合
        new_data = JmaEqData(
            source=EqSource.WOLFX,
            type=EqType.FORECAST,
            event_id="same_event",
            max_intensity="4",
            predicted_home_scale=30,
            hypocenter_name="Dummy"
        )

        self.system.on_earthquake_data(new_data)

        # 再生スレッドは呼ばれないはず
        mock_thread.assert_not_called()
        # 優先度は維持されるはず
        self.assertEqual(self.system.current_max_priority, 3)

    def test_on_earthquake_data_cancel_reset(self):
        """キャンセル報受信時に優先度がリセットされるか"""
        self.system.current_max_priority = 3
        
        cancel_data = JmaEqData(
            source=EqSource.WOLFX,
            type=EqType.CANCEL,
            event_id="cancel_event",
            hypocenter_name=""
        )

        # threading.Threadのパッチはクラスレベルで行っていないため、
        # ここでは音声再生の中身(play_audio_list)までモックしていないとエラーになる可能性があるが、
        # util_audio_player自体をモックしているので大丈夫。
        
        self.system.on_earthquake_data(cancel_data)
        
        # 優先度が0にリセットされていること
        self.assertEqual(self.system.current_max_priority, 0)

    def test_play_audio_thread(self):
        """再生スレッドの中身が util_audio_player を呼ぶか"""
        file_list = ["/path/to/a.wav", "/path/to/b.wav"]
        self.system._play_audio_thread(file_list, priority=1)
        
        mock_audio_player.play_audio_list.assert_called_with(file_list, volume=90)

if __name__ == "__main__":
    unittest.main()