import tkinter as tk
import json
import threading
import os
import sys
import argparse

# --- パス設定: 親ディレクトリや外部モジュールへのパスを通す ---
# 現在のファイル(eq_main.py)のディレクトリ
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# プロジェクトルート (alert_jma)
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))

sys.path.append(CURRENT_DIR)
sys.path.append(PROJECT_ROOT)

# 自作モジュール
from eq_monitor_nied import NiedMonitor
from eq_visualizer import NiedMapVisualizer
from eq_data import JmaEqData
import eq_config.eq_config as config

def dummy_alert_callback(data: JmaEqData):
    """アラート検知時のコールバック"""
    print(f"!!! ALERT !!! {data.get_voice_text()}")

def main():
    # --- 引数の解析処理を追加 ---
    parser = argparse.ArgumentParser(description='NIED Realtime Monitor GUI')
    
    # "mode" という引数を追加 (省略可能, デフォルトは "full", 選択肢は full/cropped)
    parser.add_argument('mode', nargs='?', choices=['full', 'cropped'], default='full',
                        help='Display mode: "full" (default) or "cropped" (zoomed)')
    
    args = parser.parse_args()
    print(f"Starting in [{args.mode}] mode...")
    # -------------------------
    # 設定読み込み
    try:
        with open('../eq_config/eq_settings.json', 'r') as f:
            settings_all = json.load(f)
            nied_settings = settings_all.get("nied_monitor", {})
    except FileNotFoundError as e:
        print(f"Settings file not found.\n{e}")
        return

    # 1. Tkinter Root作成
    root = tk.Tk()
    
    # 2. Visualizer作成
    visualizer = NiedMapVisualizer(root, mode=args.mode)

    # 3. Monitor作成（Visualizerを渡す）
    monitor = NiedMonitor(
        settings=nied_settings, 
        callback=dummy_alert_callback,
        visualizer=visualizer # <--- 修正したinitに渡す
    )

    # 4. Monitorを別スレッドで開始
    monitor.start()

    # 5. アプリ終了時の処理（スレッド停止）
    def on_close():
        print("Stopping monitor...")
        monitor.stop()
        root.destroy()
        sys.exit(0)

    root.protocol("WM_DELETE_WINDOW", on_close)

    # 6. GUIループ開始（ここはブロックする）
    print("Starting GUI...")
    root.mainloop()

if __name__ == "__main__":
    main()