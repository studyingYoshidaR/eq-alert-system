import sys
import os
import time
import json
import threading
from typing import List, Optional

# --- パス設定: 親ディレクトリや外部モジュールへのパスを通す ---
# 現在のファイル(eq_main.py)のディレクトリ
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# プロジェクトルート (alert_jma)
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
# Audio Playerのあるディレクトリ (../../util/audio)
AUDIO_UTIL_DIR = os.path.abspath(os.path.join(PROJECT_ROOT, "../util/audio"))

sys.path.append(CURRENT_DIR)
sys.path.append(PROJECT_ROOT)
sys.path.append(AUDIO_UTIL_DIR)

# --- モジュールインポート ---
try:
    import util_audio_player # type: ignore
except ImportError as e:
    print(f"[Error] util_audio_player.py not found in {AUDIO_UTIL_DIR} {e}")
    sys.exit(1)

from eq_data import JmaEqData, EqSource, EqType
from eq_eew_wolfx import WolfxWatcher
from eq_monitor_nied import NiedMonitor
from eq_utils import EqLogger
import eq_config.eq_config as config

class EqSystem:
    def __init__(self):
        # ロガーセットアップ
        self.logger = EqLogger.setup_logger("MainSystem")
        
        # 設定ロード
        self.settings = self._load_settings()
        
        # 状態管理
        self.current_max_priority = 0 # 0:None, 1:NIED, 2:Forecast, 3:Warning
        self.last_event_id = ""
        self.lock = threading.Lock()
        
        # 音声アセットディレクトリ
        self.ASSETS_DIR = os.path.join(PROJECT_ROOT, "eq_assets")
        
        # ウォッチャー初期化
        self.wolfx = WolfxWatcher(callback=self.on_earthquake_data)
        
        # NIEDは設定で有効な場合のみ初期化
        self.nied = None
        if self.settings.get("nied_monitor", {}).get("enabled", True):
            # NiedMonitorは内部で画像解析を行うため、画像保存先等の準備が必要
            self.nied = NiedMonitor(
                settings=self.settings.get("nied_monitor", {}),
                callback=self.on_earthquake_data
            )

    def _load_settings(self) -> dict:
        settings_path = os.path.join(PROJECT_ROOT, "eq_config", "eq_settings.json")
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load settings: {e}")
            return {}

    def start(self):
        """システム稼働開始"""
        self.logger.info("Starting Earthquake Alert System...")
        
        # WebSocket監視開始
        if self.settings.get("eew_alert", {}).get("enabled", True):
            self.wolfx.start()
        
        # NIED監視開始
        if self.nied:
            self.nied.start()
            
        self.logger.info("System is running. Press Ctrl+C to stop.")
        
        # メインスレッド維持
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """終了処理"""
        self.logger.info("Stopping system...")
        self.wolfx.stop()
        if self.nied:
            self.nied.stop()
        self.logger.info("System stopped.")

    def on_earthquake_data(self, data: JmaEqData):
        """
        Wolfx/NIEDからのデータ受信コールバック
        """
        with self.lock:
            # 優先度判定
            priority = self._get_priority(data)
            
            # ログ記録
            log_msg = f"[{data.source.name}] Type:{data.type.name} Hypo:{data.hypocenter_name} MaxInt:{data.max_intensity}"
            if priority >= 3:
                self.logger.critical(log_msg)
            elif priority == 2:
                self.logger.warning(log_msg)
            else:
                self.logger.info(log_msg)

            # 新しいイベントか、または優先度が高い場合のみ通知
            # (同一イベントIDでも情報が更新されていれば通知するロジックが必要だが、
            #  今回は簡易的に「優先度が現在の状態以上」なら再生する判定)
            if priority >= self.current_max_priority:
                self.current_max_priority = priority
                self.last_event_id = data.event_id
                
                # 音声ファイルリストの構築
                audio_list = self._map_data_to_audio_list(data)
                
                if audio_list:
                    self.logger.info(f"Playing audio sequence ({len(audio_list)} files)")
                    # 別スレッドで再生しないと受信処理がブロックされるためスレッド化
                    threading.Thread(target=self._play_audio_thread, args=(audio_list,)).start()

            # キャンセル報の場合は状態リセット
            if data.type == EqType.CANCEL:
                self.current_max_priority = 0

    def _play_audio_thread(self, file_list: List[str]):
        """音声再生用スレッド"""
        try:
            # util_audio_playerの機能を使ってリスト再生(結合再生)
            # volumeは設定ファイルや優先度に応じて変えても良い
            util_audio_player.play_audio_list(file_list, volume=90)
        except Exception as e:
            self.logger.error(f"Audio playback error: {e}")

    def _get_priority(self, data: JmaEqData) -> int:
        if data.type == EqType.CANCEL: return 4 # キャンセルは最優先で知らせる
        if data.type == EqType.WARNING: return 3
        if data.type == EqType.FORECAST: return 2
        if data.type == EqType.REALTIME: return 1
        return 0

    def _map_data_to_audio_list(self, data: JmaEqData) -> List[str]:
        """
        JmaEqDataの内容を具体的なWAVファイルパスのリストに変換する
        ※ eq_assets フォルダ内のファイル名を指定
        """
        parts = []

        # 音声フォルダのルートを定義 (eq_assets/voice)
        voice_root = os.path.join(self.ASSETS_DIR, "voice")
        
        # ヘルパー: フルパス生成
        def add_wav(subdir: str, filename: str):
            # 例: eq_assets/voice/fixed/v_fixed_eew.wav
            path = os.path.join(voice_root, subdir, filename)
            if os.path.exists(path):
                parts.append(path)
            else:
                self.logger.warning(f"Audio asset missing: {subdir}/{filename}")

        # 震度階級サフィックス変換 (例: 45 -> "5w")
        def get_scale_suffix(scale_int):
            m = {10:"1", 20:"2", 30:"3", 40:"4", 45:"5w", 50:"5s", 55:"6w", 60:"6s", 70:"7"}
            return m.get(scale_int, "unknown")

        def get_scale_suffix_str(scale_str):
            s = scale_str.replace("-", "w").replace("+", "s").replace("弱", "w").replace("強", "s")
            return s if s else "unknown"

        home_suffix = get_scale_suffix(data.predicted_home_scale)
        max_suffix = get_scale_suffix_str(data.max_intensity)

        # --- シナリオ別ファイル構成 ---
        
        if data.type == EqType.CANCEL:
            add_wav("fixed", "eq_v_fixed_cancel.wav")

        elif data.type == EqType.REALTIME:
            # {方角} + 方面で地震... + 最大震度 + {階級}
            # 方角マッピング
            dir_map = {
                "北": "eq_v_dir_n.wav", "北東": "eq_v_dir_northeast.wav", "東": "eq_v_dir_e.wav",
                "南東": "eq_v_dir_southeast.wav", "南": "eq_v_dir_s.wav", "南西": "eq_v_dir_southwest.wav",
                "西": "eq_v_dir_w.wav", "北西": "eq_v_dir_northwest.wav"
            }
            dir_file = dir_map.get(data.hypocenter_name)
            if dir_file: add_wav("direction", dir_file)
            
            add_wav("fixed", "eq_v_fixed_nied_detect.wav")
            add_wav("fixed", "eq_v_fixed_max_scale_short.wav")
            add_wav("scale", f"eq_v_scale_{max_suffix}.wav")

        elif data.type == EqType.WARNING:
            # 予測震度 + {階級} (x3)
            for _ in range(3):
                add_wav("fixed", "eq_v_fixed_pred_scale_short.wav")
                add_wav("scale", f"eq_v_scale_{home_suffix}.wav")
            
            # {震源} は動的なので、ファイルがあれば追加、なければスキップ
            # 例: assets/places/v_place_千葉県北西部.wav
            # 今回は実装簡略化のため、汎用音声 "v_fixed_strong_quake.wav" (で強い地震が...) に繋げる
            
            add_wav("fixed", "eq_v_fixed_strong_quake.wav")
            add_wav("fixed", "eq_v_fixed_max_scale_is.wav")
            add_wav("scale", f"eq_v_scale_{max_suffix}.wav")

        elif data.type == EqType.FORECAST:
            # 緊急地震速報 + 予測震度は + {階級}
            add_wav("fixed", "eq_v_fixed_eew.wav")
            add_wav("fixed", "eq_v_fixed_pred_scale_is.wav")
            add_wav("scale", f"eq_v_scale_{home_suffix}.wav")
            
            # ... {最大震度} の地震が発生
            add_wav("fixed", "eq_v_fixed_max_scale_is.wav")
            add_wav("scale", f"eq_v_scale_{max_suffix}.wav")
            add_wav("fixed", "eq_v_fixed_quake_occurred.wav")

        return parts

if __name__ == "__main__":
    app = EqSystem()
    app.start()