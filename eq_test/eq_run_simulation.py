import sys
import os
import json
import time
from typing import List, Dict

# --- パス設定 ---
# このファイルのディレクトリ (eq_test)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# プロジェクトルート (alert_jma)
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
# ソースコードディレクトリ (eq_src)
SRC_DIR = os.path.join(PROJECT_ROOT, "eq_src")
# シミュレーション用音声ディレクトリ (eq_assets/voice/simulation)
SIM_VOICE_DIR = os.path.join(PROJECT_ROOT, "eq_assets", "voice", "simulation")
# Audio Player (util/audio)
AUDIO_UTIL_DIR = os.path.abspath(os.path.join(PROJECT_ROOT, "../util/audio"))

sys.path.append(PROJECT_ROOT)
sys.path.append(SRC_DIR)
sys.path.append(AUDIO_UTIL_DIR)

# --- モジュールインポート ---
try:
    from eq_main import EqSystem
    from eq_data import JmaEqData, EqSource, EqType
    import util_audio_player
except ImportError as e:
    print(f"[Error] Failed to import modules: {e}")
    sys.exit(1)

SCENARIO_FILE = os.path.join(CURRENT_DIR, "eq_sim_scenarios.json")

def load_scenarios(filepath: str) -> List[Dict]:
    """JSONファイルからシナリオリストを読み込む"""
    if not os.path.exists(filepath):
        print(f"[Error] Scenario file not found: {filepath}")
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[Error] Failed to load json: {e}")
        return []

def convert_to_eq_data(raw_data: Dict) -> JmaEqData:
    """辞書データをJmaEqDataオブジェクトに変換"""
    # Enumへの変換
    source_str = raw_data.get("source", "WOLFX")
    type_str = raw_data.get("type", "FORECAST")
    
    source_enum = EqSource[source_str] if source_str in EqSource.__members__ else EqSource.WOLFX
    type_enum = EqType[type_str] if type_str in EqType.__members__ else EqType.FORECAST

    return JmaEqData(
        source=source_enum,
        type=type_enum,
        event_id=raw_data.get("event_id", "test_id"),
        hypocenter_name=raw_data.get("hypocenter_name", "不明"),
        max_intensity=raw_data.get("max_intensity", "0"),
        predicted_home_scale=raw_data.get("predicted_home_scale", 0),
        is_final=False,
        original_text="Simulation Data"
    )

def main():
    print("=== Earthquake Alert Simulation Start ===")
    
    # システムの初期化 (設定ファイルのロードやロガー準備)
    # 実際のWebSocket接続やNIED監視は start() を呼ばなければ動かないため安全
    app = EqSystem()
    print("System Initialized.")

    scenarios = load_scenarios(SCENARIO_FILE)
    if not scenarios:
        print("No scenarios to run.")
        return

    try:
        # 訓練開始通知
        util_audio_player.play_audio(os.path.join(SIM_VOICE_DIR, "eq_v_sim_start.wav"),volume=90)
        time.sleep(5)  # 待機

        for i, sc in enumerate(scenarios):
            print(f"\n--- Running Scenario {i+1}: {sc.get('scenario_name')} ---")
            print(f"Description: {sc.get('description')}")
            
            # データの作成
            eq_data = convert_to_eq_data(sc["data"])
            
            # データ注入 (on_earthquake_dataを直接叩く)
            app.on_earthquake_data(eq_data)
            
            # 待機 (音声再生完了待ちなど)
            wait_sec = sc.get("wait_seconds", 5)
            print(f"Waiting {wait_sec} seconds...")
            time.sleep(wait_sec)

    except KeyboardInterrupt:
        print("\nSimulation aborted by user.")
    finally:
        app.stop()
        # 訓練終了通知
        util_audio_player.play_audio(os.path.join(SIM_VOICE_DIR, "eq_v_sim_end.wav"),volume=90)
        print("=== Simulation Finished ===")

if __name__ == "__main__":
    main()