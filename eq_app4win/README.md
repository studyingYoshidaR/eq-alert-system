# eq_app4win — Windows ビルド用ミラー

Windows で配布用実行ファイル（`.exe`）を生成するための PyInstaller ビルド環境。

`eq_src/` `eq_config/` `eq_assets/` `eq_test/` はプロジェクトルートのミラー（ほぼ同一内容の複製）で、ビルドを自己完結させるために同梱している。**ソースの正本はルート側**であり、機能追加・修正はルートで行い、必要に応じてここへ反映する。

## ビルド手順

### 1. 依存パッケージのインストール

```bat
pip install -r requirements.txt
```

`requirements.txt`: `pygame` / `Pillow` / `websocket-client` / `numpy` / `requests` / `pyinstaller`

### 2. exe のビルド

```bat
build_exe.bat
```

- カレントをこのフォルダに移し、`pyinstaller eq_app.spec --noconfirm` を実行
- PyInstaller が未インストールなら自動で `pip install` する
- 出力先: `dist\EqAlertSystem\EqAlertSystem.exe`
- **配布は `dist\EqAlertSystem\` フォルダごと**行う（`_internal/` に依存物が入る）

### 3. ソースから直接実行（ビルド不要）

```bat
run.bat
```

`python eq_src\eq_app.py` を起動する（動作確認用）。

## ファイル

| ファイル | 役割 |
| --- | --- |
| `build_exe.bat` | PyInstaller ビルドの一括実行 |
| `eq_app.spec` | PyInstaller 設定（同梱アセット・エントリポイント定義） |
| `run.bat` | ソースから直接起動 |
| `requirements.txt` | ビルド・実行に必要な Python パッケージ |
| `util_audio_player.py` | 音声再生ユーティリティ（Windows 向け同梱物） |
| `dist/` | ビルド成果物（生成される。バージョン管理対象外） |

> 注: `dist/` 配下は PyInstaller が生成する成果物のため、このミラー内の各サブフォルダに個別 README は置いていない。ソースの詳細は [../eq_src/README.md](../eq_src/README.md) を参照。
