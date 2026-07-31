"""
NIED モニタ画像ディレクトリの一括移行・リネームスクリプト

【処理内容】
  1. eqlog_*/        内の eqlog_*.png を eqlog_*/surface/ に移動
  2. eqlog_*/borehole/ ディレクトリを作成
  3. eqlog_*/surface/ 内の eqlog_*.png を eqlog_s_*.png にリネーム

【冪等性】
  - surface/ 内に既に eqlog_s_*.png がある場合はスキップ
  - 何度実行しても安全

【使い方】
  # デフォルト（eq_config.py と同じ SSD優先・SDフォールバック）
  python migrate_and_rename_surface.py

  # パスを明示する場合
  python migrate_and_rename_surface.py /mnt/<SSD_MOUNT_POINT>/raspberrypi/alert_eq/eq_log/monitor_images/eq_log
"""

import os
import sys
import shutil

# ----- パス解決 -----
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))
sys.path.append(_PROJECT_ROOT)
try:
    import eq_config.eq_config as _eq_config
    _SSD_MOUNT = _eq_config.SSD_MOUNT_POINT
except ImportError:
    _SSD_MOUNT = "/mnt/eq_ssd"
_SSD_EQ_LOG  = os.path.join(_SSD_MOUNT, "raspberrypi/alert_eq/eq_log/monitor_images/eq_log")
_SD_EQ_LOG   = os.path.join(_PROJECT_ROOT, "eq_log", "monitor_images", "eq_log")

def _default_dirs():
    if os.path.exists(_SSD_MOUNT):
        return [_SSD_EQ_LOG]
    return [_SD_EQ_LOG]

IMAGE_EXTS = ('.png', '.jpg', '.jpeg')


def process_base(base_dir: str) -> tuple:
    """指定ベースディレクトリ配下の全 eqlog_* を処理。
    戻り値: (moved_count, renamed_count)
    """
    if not os.path.isdir(base_dir):
        print(f"  [Skip] 存在しません: {base_dir}")
        return 0, 0

    moved_total = 0
    renamed_total = 0

    for entry in sorted(os.listdir(base_dir)):
        if not entry.startswith("eqlog_"):
            continue
        eq_dir = os.path.join(base_dir, entry)
        if not os.path.isdir(eq_dir):
            continue

        surface_dir = os.path.join(eq_dir, "surface")
        borehole_dir = os.path.join(eq_dir, "borehole")
        os.makedirs(surface_dir, exist_ok=True)
        os.makedirs(borehole_dir, exist_ok=True)

        # --- Step 1: 親ディレクトリの eqlog_*.png を surface/ に移動 ---
        to_move = [
            f for f in os.listdir(eq_dir)
            if f.startswith("eqlog_") and f.endswith(IMAGE_EXTS)
            and os.path.isfile(os.path.join(eq_dir, f))
        ]
        for f in to_move:
            shutil.move(os.path.join(eq_dir, f), os.path.join(surface_dir, f))
        moved_total += len(to_move)

        # --- Step 2: surface/ 内の eqlog_*.png を eqlog_s_*.png にリネーム ---
        to_rename = [
            f for f in os.listdir(surface_dir)
            if f.startswith("eqlog_") and not f.startswith("eqlog_s_")
            and f.endswith(IMAGE_EXTS)
        ]
        for f in to_rename:
            new_name = "eqlog_s_" + f[len("eqlog_"):]
            os.rename(os.path.join(surface_dir, f), os.path.join(surface_dir, new_name))
        renamed_total += len(to_rename)

        # 何もしなかった場合はスキップ表示
        if to_move or to_rename:
            parts = []
            if to_move:
                parts.append(f"移動 {len(to_move)} 件")
            if to_rename:
                parts.append(f"リネーム {len(to_rename)} 件")
            print(f"  [OK] {entry}: {', '.join(parts)}")

    return moved_total, renamed_total


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else _default_dirs()

    total_moved = 0
    total_renamed = 0

    for t in targets:
        abs_t = os.path.abspath(t)
        print(f"\n対象: {abs_t}")
        m, r = process_base(abs_t)
        total_moved += m
        total_renamed += r

    print(f"\n{'='*50}")
    print(f"完了: 移動 {total_moved} 件 / リネーム {total_renamed} 件")


if __name__ == "__main__":
    main()
