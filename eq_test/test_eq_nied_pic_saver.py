import time
import io
import os
import sys
from unittest.mock import Mock
from PIL import Image, ImageDraw

# --- パス設定 ---
# テスト実行時に eq_src や eq_config を読み込めるようにパスを通す
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(PROJECT_ROOT, "eq_src")

sys.path.append(PROJECT_ROOT)
sys.path.append(SRC_DIR)

from eq_monitor_nied import NiedMonitor       # モニタークラス
from eq_data import JmaEqData, EqSource, EqType
import eq_config.eq_config as config          # 設定モジュール

# 1. テスト用のコールバック関数（外部システムへの受け渡しをシミュレート）
def test_callback(eq_data: JmaEqData):
    print(f"\n[コールバック受信] 震源方向: {eq_data.hypocenter_name}, 最大震度: {eq_data.max_intensity}\n")

# 2. ダミー画像（GIF）を生成してバイト列として返すヘルパー関数
def create_dummy_image_bytes(color_rgb):
    # 実際のK-moni画像と同等のサイズ (例: 352x400) を作成
    img = Image.new("RGB", (352, 400), (30, 30, 30)) # 背景は暗いグレー
    draw = ImageDraw.Draw(img)
    
    # 自宅設定座標に揺れの円を描画
    cx, cy = config.NIED_HOME_X, config.NIED_HOME_Y
    radius = 20
    draw.ellipse([cx-radius, cy-radius, cx+radius, cy+radius], fill=color_rgb)
    
    # HTTPレスポンスの content と同じようにBytesIOに保存
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='GIF')
    return img_byte_arr.getvalue()

def run_nied_test():
    print("=== 強震モニタ解析クラス(動画生成機能付き)のテストを開始します ===")
    
    # テスト用の設定値
    settings = {
        "radius_pixel": 30,
        "trigger_pixels": 5,
        "trigger_intensity": 1.5,
        "history_duration_minutes": 1,
        "history_interval_seconds": 1
    }

    # モニターのインスタンス化 (saves_history=True で保存と動画生成を有効化)
    monitor = NiedMonitor(settings, test_callback, visualizer=None, saves_history=True)
    
    # Mock機能を使ってrequests.Session.get を乗っ取る（外部通信を遮断）
    mock_resp = Mock()
    mock_resp.status_code = 200
    monitor.session.get = Mock(return_value=mock_resp)

    try:
        current_ts = int(time.time())

        print("\n[フェーズ1] 平常時のテスト (青色画像: 震度0以下)")
        # 青色のダミー画像をセット
        mock_resp.content = create_dummy_image_bytes((0, 0, 255))
        monitor._process_current_image(current_ts)
        print(" -> アラートは発報されず、状態は平常のままのはずです。")
        time.sleep(1)

        print("\n[フェーズ2] 地震発生のテスト (赤色画像: 震度5強以上)")
        # 赤色のダミー画像をセット
        mock_resp.content = create_dummy_image_bytes((255, 0, 0))
        
        # 揺れが5秒間続いたと仮定して画像を5回連続で流し込む
        for i in range(5):
            monitor._process_current_image(current_ts + i + 1)
            time.sleep(0.2) # 高速に処理を進める
        print(" -> アラートが発報され, eq_logフォルダ内に毎秒の画像を保存")

        print("\n[フェーズ3] 地震収束のテスト (黒色画像: 震度表示なし)")
        # 黒色のダミー画像をセットして揺れを収束させる
        mock_resp.content = create_dummy_image_bytes((0, 0, 0))
        monitor._process_current_image(current_ts + 10)
        print(" -> 最大震度が1.0未満になりアラートが終了します。")
        print(" -> バックグラウンドで動画生成(GIF/MP4)を開始")

        print("\n動画生成の完了を待機しています (約10秒)...")
        time.sleep(10)
        
    except Exception as e:
        print(f"テスト中にエラーが発生しました: {e}")
    finally:
        # スレッドの終了処理
        monitor.stop()
        print("\n=== テスト完了 ===")
        print(f"画像保存ディレクトリ ({config.MONITOR_IMAGE_DIR}/eq_log) を確認してください。")
        print("フォルダ内に 'alert_summary.gif' と 'alert_summary.mp4' が生成されていれば成功")

if __name__ == "__main__":
    run_nied_test()