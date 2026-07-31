# eq_config — 設定

システムの設定値と設定モジュール。

## ファイル

| ファイル | 役割 |
| --- | --- |
| `eq_config.py` | 定数・座標変換のロジック。NIED 画像のピクセル⇔緯度経度変換、ログ保存先（SSD 自動切替）などを定義。個人情報は含まない |
| `eq_config_local.py` | **自宅座標・SSDパス・音量など個人環境設定**。`.gitignore` 対象で git 管理外（コミットしないこと）。存在しない場合は `eq_config.py` 内のダミー値が使われる |
| `eq_config_local.py.example` | `eq_config_local.py` のテンプレート。初回セットアップ時にコピーして値を書き換える |
| `eq_settings.json` | 実行時に読み込む可変設定。毎回リロードされるためホットリロード対応（GUI の設定画面から編集・自動保存） |

## 初回セットアップ

1. `eq_config/eq_config_local.py.example` を `eq_config/eq_config_local.py` としてコピー
2. `HOME_LAT` / `HOME_LON`（自宅の緯度経度）、`HOME_REGION_NAME`（地域名）、`SSD_MOUNT_POINT`（お使いの環境のSSDマウントパス）などを書き換える
3. 以降は GUI の設定画面（config タブ）から自宅座標・音量を変更すると `eq_config_local.py` が自動更新される

## eq_config.py の主な定義

- `PROJECT_ROOT` — プロジェクトルートの絶対パス
- `SSD_BASE_DIR` — `SSD_MOUNT_POINT`（`eq_config_local.py` で定義）配下のログ保存先パス
- `NIED_HOME_X` / `NIED_HOME_Y` — 強震モニタ画像上の自宅ピクセル座標。`HOME_LAT` / `HOME_LON` から `latlon_to_nied_pixel()` により自動算出（個別に設定不可）
- `latlon_to_nied_pixel()` — 緯度経度 → 352×400px モニタ画像のピクセル座標
  - 校正基準: 35.7°N, 140.0°E → (233.5, 258.5)
  - 地理範囲: 120.09°E〜150.1°E, 28.6°N〜48.6°N（x 係数 11.73、y 係数 20.0）
- `HOME_LAT` / `HOME_LON` / `HOME_REGION_NAME` / `HOME_GROUND_CORRECTION` / `SSD_MOUNT_POINT` / `MASTER_VOLUME` / `COUNTDOWN_VOLUME_BOOST` / `PLAY_SIMULATION_END_AUDIO` — いずれも `eq_config_local.py` から読み込まれる個人環境値（上記参照）

## eq_settings.json の主なセクション

| セクション | 内容 |
| --- | --- |
| `system` | `log_level`、`simulation_mode`（true でネット接続せずダミーデータ再生） |
| `nied_monitor` | 監視・精度向上策のパラメータ群（下記） |

### nied_monitor の主なキー

- **基本監視**: `radius_pixel`（1px≒4km）、`trigger_pixels`、`trigger_intensity`、履歴保持設定、凡例/フォントサイズ
- **#1 地中 AND**: `borehole_enabled` / `borehole_confirm_intensity` / `borehole_confirm_pixels` / `borehole_override_intensity`（この震度以上は地中確認をスキップし即発報）
- **#2 PLUM**: `plum_enabled` / `plum_radius_km` / `plum_radii_km` / `plum_decrement`
- **#3 ETA 補正**: `eta_correction_enabled` / `eta_arrival_intensity` / `eta_velocity`
- **#4 サイト較正**: `site_calibration_enabled` / `site_calibration_interval_sec` / `site_search_px` / `site_bore_min_intensity` / `site_min_samples` / `site_max_station_km`
- **#6 到達フェイルセーフ**: `arrival_failsafe_*`

> JSON 内の `"//..."` キーはコメント（説明用）。各パラメータの設計判断は [eq_src/README.md](../eq_src/README.md) の精度向上ロードマップを参照。
