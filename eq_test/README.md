# eq_test — テスト・シミュレーション・評価

ユニットテスト、過去ログ再生シミュレーション、予測精度評価、地中データ前処理スクリプトを収める。

## ユニットテスト

| ファイル | 対象 |
| --- | --- |
| `test_eq_data.py` | 統一データ型 `JmaEqData` |
| `test_eq_main.py` | システムコア `EqSystem`（優先度判定・コールバック） |
| `test_eq_wolfx.py` | Wolfx EEW 受信 |
| `test_eq_nied.py` | NIED モニタ解析 |
| `test_eq_nied_pic_saver.py` | モニタ画像の保存処理 |
| `test_borehole_and.py` | #1 地中 AND ノイズ排除（本番 `should_alert()` を import、クールダウン込み実フロー再現 + override 閾値スイープ） |
| `test_plum_ring.py` | #2 疑似 PLUM（本番 `RingMonitor` を import） |
| `test_eta_correction.py` | #3 S 波到達 ETA 補正（短縮のみ） |
| `test_site_calibration.py` | #4 サイト地盤増幅較正（実データ + 合成サンプル） |
| `test_site_interpolation.py` | #4 IDW 補間による自宅地中推定 |
| `test_arrival_failsafe.py` | #6 到達確定フェイルセーフ（閾値スイープ・重複防止） |

## シミュレーション・評価

| ファイル | 役割 |
| --- | --- |
| `eq_run_simulation.py` / `eq_run_simulation2.py` | 過去 EEW ログを再生してシステムを駆動。`eq_run_simulation2.py` の `parse_eew_file` は評価ハーネスからも再利用 |
| `eq_sim_nied_gen.py` | シミュレーション用 NIED 画像列の生成 |
| `eval_prediction_accuracy.py` | **#7 精度評価**。蓄積イベントで EEW 予測 vs 実測（`RingMonitor` で自宅 30km/10km 観測）の震度・到達時刻誤差を集計、勝俣式定数フィットを示唆 |

## 地中データ前処理

| ファイル | 役割 |
| --- | --- |
| `preprocess_borehole_video.py` | NIED kyoshin "rsi" 地中動画 → 毎秒 PNG（`eqlog_b_` フォーマット）。cv2 で読込。座標校正変換込み |
| `migrate_and_rename_surface.py` | 既存の地表画像を新フォーマットへ移行・リネーム |

## テストデータ

| ディレクトリ | 内容 |
| --- | --- |
| `test_eq_log/test_eq_eew_jma/<日付>/` | 再生用の過去 EEW ログ（2011/03/11、2021/10/07、2024/01/01 等） |
| `test_monitor_images/eq_log/eqlog_<日付>_<hhmm>_lv*/` | 検証用モニタ画像。`surface/`（地表）と `borehole/`（地中）を含む |
| `raw_eqvideo/` | 前処理前の生の地中動画素材 |

## 注意

- シミュレーション（`eq_run_simulation2.py`）は `NiedMonitor` を通さず PNG を直読みするため、#1 のライブ判定は各ユニットテストでオフライン検証している。
- 精度向上策の多くは「安全側設計」のため、EEW が強気なイベント（例: 20260626）では発動しない。発火実証には EEW 過小評価/到達遅延サンプルが必要。詳細な検証知見はプロジェクトメモリ（`project-monitor-borehole`）を参照。
