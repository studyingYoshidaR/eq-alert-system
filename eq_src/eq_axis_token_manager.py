import json
import logging
import calendar
import requests
from datetime import datetime

# モジュール用ロガーの取得
logger = logging.getLogger(__name__)

# 定数
REFRESH_API_URL = "https://axis.prioris.jp/api/token/refresh/"

class TokenManager:
    def __init__(self, filepath="token.json"):
        self.filepath = filepath
        self.access_token = None
        self.last_checked = None
        self.load_token()

    def load_token(self):
        """ JSONファイルからトークン情報を読み込む """
        try:
            with open(self.filepath, 'r') as f:
                data = json.load(f)
                self.access_token = data.get("access_token")
                # 日付形式チェックも兼ねて取得
                self.last_checked = data.get("last_checked", "2000-01-01")
        except FileNotFoundError:
            logger.error(f"Error: {self.filepath} が見つかりません")
            raise
        except json.JSONDecodeError:
            logger.error(f"Error: 不正なJSON形式({self.filepath})")
            raise

    def save_token(self):
        """ 現在のトークン情報をJSONファイルに保存 """
        data = {
            "access_token": self.access_token,
            "last_checked": self.last_checked
        }
        with open(self.filepath, 'w') as f:
            json.dump(data, f, indent=4)
        logger.info("トークン情報をファイルに保存しました")

    def _get_days_remaining(self):
        """ 今月の残り日数を計算 """
        today = datetime.now()
        last_day = calendar.monthrange(today.year, today.month)[1]
        return last_day - today.day

    def refresh_if_needed(self):
        """
        条件:
        1. 今月の残り日数が7日未満
        2. 最終更新(last_checked)から8日以上経過
        の両方を満たす場合のみAPIを実行
        """
        today = datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        
        # 最終更新日からの経過日数を計算
        try:
            last_checked_date = datetime.strptime(self.last_checked, '%Y-%m-%d')
            days_since_update = (today - last_checked_date).days
        except ValueError:
            # 日付形式がおかしい場合は強制更新対象とするため大きな値を設定
            days_since_update = 999

        days_remaining = self._get_days_remaining()

        # --- デバッグ用ログ ---
        logger.debug(f"Status: 残り{days_remaining}日, 最終更新から{days_since_update}日経過")

        # 条件判定
        # 条件A: 月末7日未満でないなら何もしない
        if days_remaining >= 7:
            logger.info(f"トークン更新チェック: 今月の残り{days_remaining}日 (更新期間外です)")
            return

        # 条件B: まだ前回の更新から8日経っていないなら何もしない
        if days_since_update < 8:
            logger.info(f"トークン更新チェック: 最終更新から{days_since_update}日経過 (更新頻度制限によりスキップ)")
            return

        # --- ここから更新処理 (条件A, B両方クリア) ---
        logger.info(f"条件成立(残り{days_remaining}日 < 7 かつ 経過{days_since_update}日 >= 8): トークン更新APIを実行します...")
        
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(REFRESH_API_URL, headers=headers, timeout=10)
            
            # APIアクセスを実施した時点で日付を更新（成功可否に関わらず再試行間隔を空けるため）
            # ただし，通信エラー等の場合は更新しない戦略もあるが，
            # ここでは「APIを叩いた」事実ベースで更新日を記録し，過度なアクセスを防ぐ．
            self.last_checked = today_str 
            
            if response.status_code == 200:
                resp_json = response.json()
                status = resp_json.get("status")
                new_token = resp_json.get("token")

                if status == "generate a new token" and new_token:
                    logger.info("新しいトークンが発行されました．")
                    self.access_token = new_token
                elif status == "not due for refresh yet":
                    logger.info("API応答: まだ更新時期ではありません．")
                else:
                    logger.info(f"API応答: {status} (トークン変更なし)")
                
                # トークンまたは日付の変更をファイルに保存
                self.save_token()
            
            elif response.status_code == 402:
                logger.error("APIエラー(402): 契約期限切れです．日付のみ更新して記録します．")
                self.save_token()
            else:
                logger.error(f"トークン更新失敗: HTTP {response.status_code}")
                # 失敗時は日付更新をキャンセルしたい場合，ここで self.load_token() して戻す手もあるが，
                # 安全側に倒して「試行した」記録を残す実装．
                self.save_token()

        except Exception as e:
            logger.error(f"トークン更新中に通信エラーが発生しました: {e}")