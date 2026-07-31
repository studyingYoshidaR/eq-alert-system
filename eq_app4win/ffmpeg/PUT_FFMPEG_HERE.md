# ここに ffmpeg.exe を置く

このフォルダに **`ffmpeg.exe`** を置くと、`build_exe.bat` 実行時に自動で exe へ同梱され、
バンドル内 `_internal/ffmpeg/ffmpeg.exe` として展開・使用されます（NIED画像の MP4 動画生成に使用）。

## 推奨: 静的（単一ファイル）ビルドの ffmpeg

DLL依存のない**スタティックビルド**の `ffmpeg.exe` を使うこと。単一ファイルで完結し、確実に動きます。

入手先（例）:
- https://www.gyan.dev/ffmpeg/builds/ の「release essentials」
- https://github.com/BtbN/FFmpeg-Builds/releases の `ffmpeg-master-latest-win64-gpl.zip`

ZIP内の `bin\ffmpeg.exe` を、このフォルダ（`eq_app4win/ffmpeg/`）にコピーするだけ。

## DLL依存版（conda等）を使う場合

`ffmpeg.exe` が単体で動かない（DLLを要求する）ビルドの場合は、
必要な `*.dll` も**同じこのフォルダに一緒に**置いてください（`_internal/ffmpeg/` に丸ごと同梱されます）。
判断がつかなければ静的ビルドを使うのが確実です。

## 未配置の場合

このフォルダに `ffmpeg.exe` が無くてもビルドは成功します。その場合 MP4 生成のみスキップされ、
アプリ本体・GIF生成・音声・警報は正常動作します（`build_exe.bat` に警告が表示されます）。

> 注: `ffmpeg.exe` を置くとこのフォルダごと同梱されるため、この案内MDも一緒に入りますが、
> サイズも影響も無視できるレベルです（気になる場合はビルド前にこのMDを移動しても構いません）。
