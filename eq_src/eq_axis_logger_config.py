import os
import logging
from logging.handlers import TimedRotatingFileHandler

def init_logging(log_filename="eew_app.log", backup_count=0):
    """
    ロガーの初期設定を行う関数
    
    Args:
        log_filename (str): ログファイル名
        backup_count (int): 古いログを保持する数（日数）。0の場合は自動削除なし（無制限）。
    """
    # 1. ログ保存先ディレクトリの作成 (../eq_log/)
    # このファイルの場所を基準に、一つ上の階層の eq_log を指定
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, '../eq_log')
    
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
        print(f"[System] Created log directory: {log_dir}")

    log_path = os.path.join(log_dir, log_filename)

    # 2. フォーマット設定
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 3. ルートロガーの取得と設定リセット
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # 既存のハンドラーがあれば削除（二重出力防止）
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # 4. コンソール出力用ハンドラー (StreamHandler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # 5. ファイル出力用ハンドラー (TimedRotatingFileHandler)
    # 毎日(midnight)ローテーションを行い、backup_count分だけ世代管理する
    file_handler = TimedRotatingFileHandler(
        filename=log_path,
        when='midnight',
        interval=1,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    logging.info(f"Logging initialized. Output to: {log_path} (Auto-delete: {'OFF' if backup_count==0 else f'{backup_count} days'})")