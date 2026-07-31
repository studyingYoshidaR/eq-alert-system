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
    print(f"[Warning] util_audio_player.py not found: {e}. Audio playback will be mocked.")
    class MockAudioPlayer:
        @staticmethod
        def play_audio(*args, **kwargs): pass
        @staticmethod
        def play_audio_list(*args, **kwargs): pass
        @staticmethod
        def stop_audio(*args, **kwargs): pass
    util_audio_player = MockAudioPlayer()

from eq_data import JmaEqData, EqSource, EqType
from eq_eew_wolfx import WolfxWatcher
from eq_monitor_nied import NiedMonitor
from eq_utils import EqLogger
import eq_config.eq_config as config

class EqSystem:
    def __init__(self, visualizers=None, saves_history=False):
        # ロガーセットアップ
        self.logger = EqLogger.setup_logger("MainSystem")
        
        # 設定ロード
        self.settings = self._load_settings()
        
        # 状態管理
        self.current_max_priority = 0 # 0:None, 1:NIED, 2:Forecast, 3:Warning
        self.last_event_id = ""
        self.lock = threading.Lock()
        
        # 音声再生の排他制御用変数
        self.audio_lock = threading.Lock()   # 音声制御用の独立したロック
        self.is_playing = False              # 音声が再生中かどうかのフラグ
        self.current_playing_priority = 0    # 現在再生中の音声の優先度
        
        # カウントダウン管理用変数
        self.active_countdown_sequence = []
        self.active_countdown_data = None
        self.countdown_active = True
        self.immediate_alert_event_id = ""
        self.observed_arrival_event_id = ""  # #6 観測到達フェイルセーフの重複防止
        self.first_eew_event_id = ""
        self.on_alert_start_callbacks: list = []
        threading.Thread(target=self._countdown_thread_func, daemon=True).start()
        
        # 音声アセットディレクトリ
        self.ASSETS_DIR = os.path.join(PROJECT_ROOT, "eq_assets")
        
        # 震源地名 -> 震央コード -> 地域名のマッピング
        self.hypo_jpcode_map = {}
        self.hypo_area_map = {}
        
        try:
            # 震央名 -> 震央コード ("千葉県北西部": "349")
            jpcode_path = os.path.join(self.ASSETS_DIR, "eq_hypocenter_map_jpcode.json")
            with open(jpcode_path, 'r', encoding='utf-8') as f:
                self.hypo_jpcode_map = json.load(f)
                
            # 震央コード -> 地域名 ("349": "chiba")
            area_map_path = os.path.join(self.ASSETS_DIR, "eq_hypocode_area_map.json")
            with open(area_map_path, 'r', encoding='utf-8') as f:
                self.hypo_area_map = json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load hypocenter mapping JSONs: {e}")
        
        # ウォッチャー初期化
        self.wolfx = WolfxWatcher(callback=self.on_earthquake_data)
        
        # NIEDは設定で有効な場合のみ初期化
        self.nied = None
        if self.settings.get("nied_monitor", {}).get("enabled", True):
            # NiedMonitorは内部で画像解析を行うため画像保存先等の準備が必要
            self.nied = NiedMonitor(
                settings=self.settings.get("nied_monitor", {}),
                callback=self.on_earthquake_data,
                visualizers=visualizers,
                saves_history=saves_history,
                eq_system=self
            )

    def _load_settings(self) -> dict:
        settings_path = os.path.join(PROJECT_ROOT, "eq_config", "eq_settings.json")
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Failed to load settings: {e}")
            return {}

    def start(self, block=False):
        """システム稼働開始"""
        self.logger.info("Starting Earthquake Alert System...")
        
        # WebSocket監視開始
        if self.settings.get("eew_alert", {}).get("enabled", True):
            self.wolfx.start()
        
        # NIED監視開始
        if self.nied:
            self.nied.start()
            
        self.logger.info("System is running. Press Ctrl+C to stop.")
        
        # メインスレッド維持 (コンソール単体実行時のみ)
        if block:
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

            # EEWを受信した場合、NIEDモニターの画像保存を強制トリガー
            if not data.is_simulated and data.source == EqSource.WOLFX and data.type in [EqType.FORECAST, EqType.WARNING]:
                if self.nied:
                    self.nied.force_alert()
            
            # ログ記録
            if not data.is_simulated:
                log_msg = f"[{data.source.name}] Type:{data.type.name} Hypo:{data.hypocenter_name} MaxInt:{data.max_intensity}"
                if priority >= 3:
                    self.logger.critical(log_msg)
                elif priority == 2:
                    self.logger.warning(log_msg)
                else:
                    self.logger.info(log_msg)

            # 新しいイベントまたは優先度が高い場合のみ通知
            # (同一イベントIDでも情報が更新されていれば通知するロジックが必要だが,
            #  今回は簡易的に「優先度が現在の状態以上」なら再生する判定)
            if priority > self.current_max_priority:
                self.current_max_priority = priority
                self.last_event_id = data.event_id
            
            # 常に最新の設定をリロードして反映
            self.settings = self._load_settings()
            
            # 設定から閾値文字列を取得し数値へ変換 ("1.5" → 15 のような小数も受け付ける)
            c_str = str(self.settings.get("eew_alert", {}).get("countdown_threshold_scale", "3"))
            mapping = {"1": 10, "2": 20, "3": 30, "4": 40, "5弱": 45, "5-": 45, "5強": 50, "5+": 50, "6弱": 55, "6-": 55, "6強": 60, "6+": 60, "7": 70}
            countdown_threshold = mapping.get(c_str)
            if countdown_threshold is None:
                try:
                    countdown_threshold = max(10, min(70, int(round(float(c_str) * 10))))
                except (ValueError, TypeError):
                    self.logger.warning(f"[Config] countdown_threshold_scale='{c_str}' は無効な値です。震度3にフォールバックします。")
                    countdown_threshold = 30

            # キャンセル報の場合は状態リセット
            if data.type == EqType.CANCEL:
                self.current_max_priority = 0
                self.active_countdown_sequence = []
                self.active_countdown_data = None
                self.active_countdown_threshold = 0
                self.immediate_alert_event_id = ""
                self.observed_arrival_event_id = ""
                self.first_eew_event_id = ""

            # --- カウントダウンのスケジュール計算 ---
            if data.type in [EqType.FORECAST, EqType.WARNING] and getattr(data, 's_wave_arrival_ts', 0.0) > 0:
                if getattr(data, 'is_simulated', False) and hasattr(data, 'sim_start_real_time'):
                    real_elapsed = time.time() - data.sim_start_real_time
                    sim_elapsed = real_elapsed * data.sim_playback_speed
                    current_sim_ts = data.sim_start_ts + sim_elapsed
                    t_remain = int(data.s_wave_arrival_ts - current_sim_ts)
                else:
                    t_remain = int(data.s_wave_arrival_ts - time.time())
                
                if t_remain > 0:
                    first_tick = t_remain
                    
                    def _scale_to_str(s: int) -> str:
                        if s == 0: return "不明"
                        if s < 45: return str(s//10)
                        if s == 45: return "5弱"
                        if s == 50: return "5強"
                        if s == 55: return "6弱"
                        if s == 60: return "6強"
                        return "7"
                    
                    audio_status = "ON" if data.predicted_home_scale >= countdown_threshold else "OFF"
                    source_str = "独自算出" if getattr(data, "is_custom_prediction", False) else "公式"
                    display_int = _scale_to_str(data.predicted_home_scale)
                    
                    init_msg = f" >> [Countdown] S波到達まで約{first_tick}秒! (初回検知, 予想震度: {display_int}, Source: {source_str}, Audio: {audio_status})"
                    print(init_msg)
                    if not getattr(data, 'is_simulated', False):
                        self.logger.info(init_msg)
                    
                    seq = []
                    for c in [60, 45, 30, 20, 15, 5, 4, 3]:
                        if c <= first_tick:
                            if c == 45 and 60 in seq: continue
                            if c == 20 and 30 in seq: continue
                            if c == 15 and 20 in seq: continue
                            seq.append(c)
                    
                    self.active_countdown_sequence = seq
                    self.active_countdown_data = data
                    self.active_countdown_threshold = countdown_threshold

            # 緊急地震速報チャイム再生を先行
            #chime_path = self._map_data_to_audio_list(data, returns_chime=True)
            #threading.Thread(target=self._play_audio_thread, args=(chime_path,)).start()
                
            # 画面割り込みコールバック（実EEW受信時のみ、sim_mute より前に発火）
            if not data.is_simulated and data.type in [EqType.FORECAST, EqType.WARNING]:
                for _cb in self.on_alert_start_callbacks:
                    try:
                        _cb(data)
                    except Exception as _e:
                        self.logger.warning(f"[Interrupt] alert callback error: {_e}")

            # 音声ファイルリストの構築
            # --- 音声再生の排他制御 (ドロップ・プリエンプション) ---
            if getattr(self, "sim_mute", False):
                return
                
            audio_list = self._map_data_to_audio_list(data)
            if not audio_list:
                return

            with self.audio_lock:
                if self.is_playing:
                    if priority > self.current_playing_priority:
                        # [プリエンプション] 現在の再生中より優先度が高い場合 -> 強制停止して割り込み
                        if not data.is_simulated:
                            self.logger.warning(f"Interrupting audio playback: Priority {priority} overrides {self.current_playing_priority}")
                        self._stop_current_audio()
                    else:
                        # [ドロップ] 現在の再生中と同等以下の優先度の場合 -> 無視
                        if not data.is_simulated:
                            self.logger.info(f"Dropping audio: Priority {priority} is not higher than playing priority {self.current_playing_priority}")
                        return

                # 再生フラグと優先度を更新してスレッド開始
                self.is_playing = True
                self.current_playing_priority = priority
                
                if not data.is_simulated:
                    self.logger.info(f"Starting audio playback (Priority: {priority}, Files: {len(audio_list)})")
                threading.Thread(target=self._play_audio_thread, args=(audio_list, priority, 90, data.is_simulated), daemon=True).start()

    def _play_audio_thread(self, file_list: List[str], priority: int, volume: int = 90, is_simulated: bool = False):
        """ 音声再生用スレッド """
        try:
            util_audio_player.play_audio_list(file_list, volume=volume)
        except Exception as e:
            if not is_simulated:
                self.logger.error(f"Audio playback error: {e}")
        finally:
            # 再生終了時のクリーンアップ
            with self.audio_lock:
                # 自分が再生した優先度と, 現在管理されている優先度が一致する場合のみフラグを解除
                # (割り込まれていた場合は, 新しいスレッドが管理を引き継いでいるため解除しない)
                if self.current_playing_priority == priority:
                    self.is_playing = False
                    self.current_playing_priority = 0
                    if not is_simulated:
                        self.logger.debug("Audio playback completed normally.")
    
    def _stop_current_audio(self):
        """ 現在再生中の音声を強制停止(プリエンプション用) """
        try:
            # util_audio_player 側に実装されている停止メソッドを呼び出す
            util_audio_player.stop_audio()
            # 停止完了を確実に待つための極小スリープ（デバイスの解放待ち）
            time.sleep(0.1) 
        except Exception as e:
            self.logger.error(f"Failed to stop audio playback: {e}")

    def trigger_observed_arrival(self, event_id: str):
        """ #6 到達確定フェイルセーフ。自宅近傍の観測で「実際に揺れが到達した」瞬間に、
        EEWの計算カウントダウンとは独立に緊急到達アラートを割り込ませる。
        NiedMonitor から呼ばれる。1イベント1回。"""
        if not event_id or self.observed_arrival_event_id == event_id:
            return
        if getattr(self, "sim_mute", False):
            return
        self.observed_arrival_event_id = event_id  # 先にセットして重複/連打を防止

        imm_path = os.path.join(self.ASSETS_DIR, "voice", "countdown", "eq_v_cd_immediate.wav")
        if not os.path.exists(imm_path):
            self.logger.warning(f"[ArrivalFailsafe] 音声ファイルが見つかりません: {imm_path}")
            return

        parts = [imm_path] * 3
        priority = 3.6  # 緊急到達アラートと同等(カウントダウン5/4/3秒には譲る)
        cd_volume = min(100, 90 + getattr(config, "COUNTDOWN_VOLUME_BOOST", 5))
        with self.audio_lock:
            if self.is_playing:
                if priority > self.current_playing_priority:
                    self._stop_current_audio()
                else:
                    self.logger.info(
                        f"[ArrivalFailsafe] Dropped: priority {priority} <= {self.current_playing_priority}"
                    )
                    return
            self.is_playing = True
            self.current_playing_priority = priority
            threading.Thread(target=self._play_audio_thread,
                             args=(parts, priority, cd_volume), daemon=True).start()
        self.logger.warning(f"[ArrivalFailsafe] 観測で到達確定 → 緊急到達アラート再生 (event={event_id})")

    def _countdown_thread_func(self):
        """ カウントダウン定期監視・再生スレッド """
        while getattr(self, 'countdown_active', True):
            time.sleep(0.2)  # 0.2秒ごとにチェック

            with self.lock:
                if not self.active_countdown_data:
                    continue

                if getattr(self, "sim_stop_flag", False):
                    self.active_countdown_sequence = []
                    self.active_countdown_data = None
                    continue

                if getattr(self, "sim_paused", False):
                    continue

                # シミュレーション時はリアルタイム時刻とのズレがあるため、渡されたコンテキストで判定
                data = self.active_countdown_data
                if getattr(data, 'is_simulated', False) and hasattr(data, 'sim_start_real_time'):
                    real_elapsed = time.time() - data.sim_start_real_time
                    sim_elapsed = real_elapsed * data.sim_playback_speed
                    current_sim_ts = data.sim_start_ts + sim_elapsed
                    t_remain = int(data.s_wave_arrival_ts - current_sim_ts)
                else:
                    t_remain = int(data.s_wave_arrival_ts - time.time())

                if t_remain <= 0:
                    self.active_countdown_sequence = []
                    continue

                cd_volume = min(100, 90 + getattr(config, "COUNTDOWN_VOLUME_BOOST", 5))

                # --- 短距離地震（15秒以内）緊急到達アラート: ポーリングで判定 ---
                # EEW受信時ではなく0.2秒ごとの監視で判定するため、後から予想震度が閾値を
                # 超えたケースも確実にキャッチできる
                if (t_remain <= 15
                        and not getattr(self, "sim_mute", False)
                        and not getattr(self, "sim_paused", False)
                        and self.immediate_alert_event_id != data.event_id):
                    threshold = getattr(self, "active_countdown_threshold", 30)
                    if data.predicted_home_scale >= threshold:
                        self.immediate_alert_event_id = data.event_id
                        imm_path = os.path.join(self.ASSETS_DIR, "voice", "countdown", "eq_v_cd_immediate.wav")
                        if os.path.exists(imm_path):
                            imm_parts = [imm_path] * 3
                            imm_priority = 3.6
                            with self.audio_lock:
                                if self.is_playing:
                                    if imm_priority > self.current_playing_priority:
                                        self._stop_current_audio()
                                    else:
                                        if not getattr(data, 'is_simulated', False):
                                            self.logger.info(f"[ImmediateAlert] Dropped: Priority {imm_priority} not higher than {self.current_playing_priority}")
                                        imm_parts = []
                                if imm_parts:
                                    self.is_playing = True
                                    self.current_playing_priority = imm_priority
                                    threading.Thread(target=self._play_audio_thread, args=(imm_parts, imm_priority, cd_volume, getattr(data, 'is_simulated', False)), daemon=True).start()
                            if not getattr(data, 'is_simulated', False):
                                self.logger.info(f"[ImmediateAlert] S波到達まで{t_remain}秒 → 緊急到達アラート再生")
                        else:
                            if not getattr(data, 'is_simulated', False):
                                self.logger.warning(f"[ImmediateAlert] 音声ファイルが見つかりません: {imm_path}")

                if not self.active_countdown_sequence:
                    continue

                # --- スケジュール済みのtickを通過したかチェック ---
                triggered_tick = None
                for tick in sorted(self.active_countdown_sequence, reverse=True):
                    if t_remain <= tick:
                        triggered_tick = tick
                        break

                if triggered_tick is not None:
                    # トリガーされたtickより大きい(または同じ)ものはスケジュールから削除
                    self.active_countdown_sequence = [t for t in self.active_countdown_sequence if t < triggered_tick]

                    # 音声リスト構築
                    cd_parts = []
                    voice_root = os.path.join(self.ASSETS_DIR, "voice")
                    def add_cd(subdir, filename):
                        p = os.path.join(voice_root, subdir, filename)
                        if os.path.exists(p): cd_parts.append(p)

                    if triggered_tick in [5, 4, 3]:
                        add_cd("countdown", f"eq_v_cd_sec_{triggered_tick}.wav")
                        if triggered_tick == 3:
                            add_cd("countdown", "eq_v_cd_prepare.wav")
                            add_cd("countdown", "eq_v_cd_prepare.wav")
                    else:
                        add_cd("countdown", "eq_v_cd_arrival_in.wav")
                        add_cd("countdown", f"eq_v_cd_sec_{triggered_tick}.wav")

                    if not cd_parts:
                        print(f" >> [Countdown] S波到達まで{triggered_tick}秒! (Audio missing)")
                        if not getattr(data, 'is_simulated', False):
                            self.logger.info(f"[Countdown] Triggered at {triggered_tick}s (Audio files missing, but logic executed)")
                        continue

                    # 優先度設定: 5,4,3秒は段階的に上げて前の再生に割り込めるようにする
                    if triggered_tick == 3:
                        priority = 3.9
                    elif triggered_tick == 4:
                        priority = 3.8
                    elif triggered_tick == 5:
                        priority = 3.7
                    elif triggered_tick <= 30:
                        priority = 3.5
                    else:
                        priority = 2.5

                    if getattr(data, 'is_simulated', False):
                        priority -= 10.0

                    # 音声再生の判定
                    play_audio = False
                    threshold = getattr(self, "active_countdown_threshold", 30)
                    if data.predicted_home_scale >= threshold:
                        play_audio = True

                    def _scale_to_str(s: int) -> str:
                        if s == 0: return "不明"
                        if s < 45: return str(s//10)
                        if s == 45: return "5弱"
                        if s == 50: return "5強"
                        if s == 55: return "6弱"
                        if s == 60: return "6強"
                        return "7"

                    audio_status = "ON" if play_audio else "OFF"
                    source_str = "独自算出" if getattr(data, "is_custom_prediction", False) else "公式"
                    display_int = _scale_to_str(data.predicted_home_scale)

                    log_msg = f" >> [Countdown] S波到達まで{triggered_tick}秒! (予想震度: {display_int}, Source: {source_str}, Audio: {audio_status})"
                    print(log_msg)
                    if not getattr(data, 'is_simulated', False):
                        self.logger.info(f"[Countdown] Triggered {triggered_tick}s (Priority: {priority}, Audio: {audio_status})")

                    if not play_audio:
                        continue

                    # 再生処理キューへ投入
                    if getattr(self, "sim_mute", False) or getattr(self, "sim_paused", False):
                        continue

                    with self.audio_lock:
                        if self.is_playing:
                            if priority > self.current_playing_priority:
                                if not getattr(data, 'is_simulated', False):
                                    self.logger.warning(f"[Countdown] Interrupting audio: Priority {priority} overrides {self.current_playing_priority} at {triggered_tick}s")
                                self._stop_current_audio()
                            else:
                                if not getattr(data, 'is_simulated', False):
                                    self.logger.info(f"[Countdown] Dropped at {triggered_tick}s: Priority {priority} not higher than {self.current_playing_priority}")
                                continue

                        self.is_playing = True
                        self.current_playing_priority = priority
                        threading.Thread(target=self._play_audio_thread, args=(cd_parts, priority, cd_volume, getattr(data, 'is_simulated', False)), daemon=True).start()

    def _get_priority(self, data: JmaEqData) -> float:
        """ 優先度判定（数値が大きいほど優先）"""
        base_priority = 0.0
        if data.type == EqType.CANCEL: base_priority = 4.0    # キャンセル（最優先）
        elif data.type == EqType.WARNING: base_priority = 3.0   # EEW 警報
        elif data.type == EqType.REALTIME: base_priority = 2.0  # NIED リアルタイム検知
        elif data.type == EqType.FORECAST: base_priority = 1.0  # EEW 予報

        # 本物の地震がシミュレーション音声を確実に上書きするよう、シミュレーション時は優先度を大幅に下げる
        if getattr(data, 'is_simulated', False):
            base_priority -= 10.0

        return base_priority

    def _map_data_to_audio_list(self, data: JmaEqData, returns_chime=False) -> List[str]:
        """
        JmaEqDataの内容を具体的なWAVファイルパスのリストに変換
        ※ eq_assets フォルダ内のファイル名を指定
        """
        parts = []

        # 音声フォルダのルートを定義 (eq_assets/voice)
        voice_root = os.path.join(self.ASSETS_DIR, "voice")
        
        # ヘルパー: フルパス生成
        def add_wav(subdir: str, filename: str):
            path = os.path.join(voice_root, subdir, filename)
            if os.path.exists(path):
                parts.append(path)
            else:
                if not getattr(data, 'is_simulated', False):
                    self.logger.warning(f"Audio asset missing: {subdir}/{filename}")

        # 震度階級サフィックス変換 (例: 45 -> "5w")
        def get_scale_suffix(scale_int):
            m = {10:"1", 20:"2", 30:"3", 40:"4", 45:"5w", 50:"5s", 55:"6w", 60:"6s", 70:"7"}
            return m.get(scale_int, "unknown")

        home_suffix = get_scale_suffix(data.predicted_home_scale)
        
        c_str = str(self.settings.get("eew_alert", {}).get("countdown_threshold_scale", "3"))
        mapping = {"1": 10, "2": 20, "3": 30, "4": 40, "5弱": 45, "5-": 45, "5強": 50, "5+": 50, "6弱": 55, "6-": 55, "6強": 60, "6+": 60, "7": 70}
        countdown_threshold = mapping.get(c_str)
        if countdown_threshold is None:
            try:
                countdown_threshold = max(10, min(70, int(round(float(c_str) * 10))))
            except (ValueError, TypeError):
                countdown_threshold = 30

        # 初回EEWレポート判定（新しいevent_idの初回報のみTrue）
        is_first_report = (not returns_chime
                           and data.type in [EqType.FORECAST, EqType.WARNING]
                           and data.event_id != self.first_eew_event_id)
        if is_first_report:
            self.first_eew_event_id = data.event_id

        # --- カウントダウン音声の先頭追加 (初回EEW or 閾値超え時) ---
        if not returns_chime and data.type in [EqType.FORECAST, EqType.WARNING] and getattr(data, 's_wave_arrival_ts', 0.0) > 0:
            if getattr(data, 'is_simulated', False) and hasattr(data, 'sim_start_real_time'):
                real_elapsed = time.time() - data.sim_start_real_time
                sim_elapsed = real_elapsed * data.sim_playback_speed
                current_sim_ts = data.sim_start_ts + sim_elapsed
                t_remain = int(data.s_wave_arrival_ts - current_sim_ts)
            else:
                t_remain = int(data.s_wave_arrival_ts - time.time())

            if t_remain > 0:
                first_tick = (t_remain // 5) * 5
                if not getattr(data, 'is_simulated', False):
                    self.logger.info(f"[Countdown] Initial prepend: 到達まで {first_tick}秒, 震度 {data.predicted_home_scale}")
                if first_tick >= 10 and (data.predicted_home_scale >= countdown_threshold or is_first_report):
                    add_wav("countdown", "eq_v_cd_arrival_in.wav")
                    add_wav("countdown", f"eq_v_cd_sec_{first_tick}.wav")
                    if data.predicted_home_scale > 0:
                        add_wav("countdown", "eq_v_cd_intensity.wav")
                        add_wav("scale", f"eq_v_scale_{home_suffix}.wav")

        def get_scale_suffix_str(scale_str):
            s = scale_str.replace("-", "w").replace("+", "s").replace("弱", "w").replace("強", "s")
            return s if s else "unknown"
        
        # チャイムの場合は専用ファイルを返す
        if returns_chime:
            add_wav("chime", "eq_chime_eew.wav")
            return parts

        home_suffix = get_scale_suffix(data.predicted_home_scale)
        max_suffix = get_scale_suffix_str(data.max_intensity)

        # 震源地名から地域音声サフィックスを取得
        def get_area_suffix(hypo_name: str) -> str:
            if not hypo_name or hypo_name in ["不明", "近隣", "-", "直下"]:
                return ""
            # NIEDの8方位の場合は除外
            if hypo_name in ["北", "北東", "東", "南東", "南", "南西", "西", "北西"]:
                return ""
                
            code_str = self.hypo_jpcode_map.get(hypo_name)
            if code_str:
                return self.hypo_area_map.get(str(code_str), "")
            return ""

        area_suffix = get_area_suffix(data.hypocenter_name)

        # --- シナリオ別ファイル構成 ---
        
        if data.type == EqType.CANCEL:
            add_wav("fixed", "eq_v_fixed_cancel.wav")

        elif data.type == EqType.REALTIME:

            add_wav("fixed", "eq_v_fixed_max_scale_short.wav")
            add_wav("scale", f"eq_v_scale_{max_suffix}.wav")

            # {方角} + 方面で地震... + 最大震度 + {階級}
            # 方角マッピング
            dir_map = {
                "北": "eq_v_dir_north.wav", "北東": "eq_v_dir_northeast.wav", "東": "eq_v_dir_east.wav",
                "南東": "eq_v_dir_southeast.wav", "南": "eq_v_dir_south.wav", "南西": "eq_v_dir_southwest.wav",
                "西": "eq_v_dir_west.wav", "北西": "eq_v_dir_northwest.wav"
            }
            dir_file = dir_map.get(data.hypocenter_name)
            if dir_file: add_wav("direction", dir_file)
            
            add_wav("fixed", "eq_v_fixed_nied_detect.wav")

        elif data.type == EqType.WARNING:
            # 予測震度が判明している(0より大きい)場合のみ付与
            if data.predicted_home_scale > 0:
                # 予測震度 + {階級} (x3)
                for _ in range(3):
                    add_wav("fixed", "eq_v_fixed_pred_scale_short.wav")
                    add_wav("scale", f"eq_v_scale_{home_suffix}.wav")
            
            # 震源地名（地域名）の組み込み
            if area_suffix:
                add_wav("area", f"eq_v_area_{area_suffix}.wav")

            add_wav("fixed", "eq_v_fixed_strong_quake.wav")
            add_wav("fixed", "eq_v_fixed_max_scale_short.wav")
            add_wav("scale", f"eq_v_scale_{max_suffix}.wav")

        elif data.type == EqType.FORECAST:
            # 緊急地震速報
            add_wav("fixed", "eq_v_fixed_eew.wav")
            
            # 予測震度が判明している(0より大きい)場合のみ付与
            if data.predicted_home_scale > 0:
                add_wav("fixed", "eq_v_fixed_pred_scale_is.wav")
                add_wav("scale", f"eq_v_scale_{home_suffix}.wav")
            
            # 最大震度固定音声の前に震源地名（地域名）を差し込み
            if area_suffix:
                add_wav("area", f"eq_v_area_{area_suffix}.wav")

            # ... {最大震度} の地震が発生
            add_wav("fixed", "eq_v_fixed_max_scale_is.wav")
            add_wav("scale", f"eq_v_scale_{max_suffix}.wav")
            add_wav("fixed", "eq_v_fixed_quake_occurred.wav")

        return parts

    def get_wolfx_status(self) -> str:
        """Wolfx EEW Watcherの接続ステータスを返す"""
        return self.wolfx.get_status()

    def get_nied_status(self) -> str:
        """NIED監視(地表/地中画像取得)の接続ステータスを返す"""
        if self.nied is None:
            return "offline"
        return self.nied.get_status()

if __name__ == "__main__":
    app = EqSystem()
    app.start(block=True)