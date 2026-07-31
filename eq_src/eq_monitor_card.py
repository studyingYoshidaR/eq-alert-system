"""
モニタ画面システムログ上部に表示する地震情報カードのスタック管理

新規に記録された地震イベントを、システムログ (tk.Text) の上端にオーバーレイ
表示する。複数のイベントは同じ位置に重ねて保持し (LIFO スタック)、常に最新の
1 件のみを表示する。[x] で最上位を閉じると 1 つ前のイベントが現れる。[確認] で
該当イベントのログ表示へ遷移する。表示内容は log タブの一覧レイアウト
(日時 / 震源 / 最大) を踏襲する。
"""
import tkinter as tk

# Colours (align with monitor system-log dark theme)
_CARD_BG = "#26313d"
_CARD_BORDER = "#3d5163"
_LABEL_FG = "#8a97a3"
_VALUE_FG = "#e6edf3"
_DATE_FG = "#9fb0bf"


def _split_dt_lines(dt: str):
    """"YYYY/MM/DD HH:MM" を (mm/dd, hh:mm) の2行に分割する。
    想定外の形式であれば全体を1行目に、2行目は空文字とする。"""
    parts = dt.split(" ", 1)
    if len(parts) == 2:
        date_part, time_part = parts
        segs = date_part.split("/")
        if len(segs) == 3:
            return f"{segs[1]}/{segs[2]}", time_part
    return dt, ""


def _intensity_colour(max_int: str) -> str:
    """最大震度文字列に応じた強調色を返す (>=5 で赤系)"""
    s = (max_int or "").strip()
    head = s[:1]
    if head in ("7", "6"):
        return "#ff5c5c"
    if head == "5":
        return "#ff9d3c"
    if head == "4":
        return "#ffd24a"
    return _VALUE_FG


class MonitorLogCardStack:
    """システムログ上端に重ねる地震カードのスタック"""

    def __init__(self, container, anchor, on_confirm=None):
        """
        Args:
            container: カード Frame の親となるウィジェット (ログ用 Frame)
            anchor: place() の基準にするウィジェット (システムログの Text)
            on_confirm: [確認] 押下時に呼ばれる callback(card: dict)
        """
        self._container = container
        self._anchor = anchor
        self._on_confirm = on_confirm
        self._stack = []      # list[dict]: {"id","dt","hypo","max_int"}
        self._frame = None    # 現在表示中のカード Frame

    # --- public API ---------------------------------------------------------
    def push(self, card: dict):
        """カードを追加。同一 id が存在する場合は内容を更新して最前面へ移動する"""
        cid = card.get("id")
        # 同一イベントの重複を防ぐ (EEW 更新などで再通知されたケース)
        self._stack = [c for c in self._stack if c.get("id") != cid]
        self._stack.append(card)
        self._render()

    def clear(self):
        """全カードを除去する (ログ「クリア」操作と連動)"""
        self._stack.clear()
        self._render()

    def count(self) -> int:
        return len(self._stack)

    # --- internal -----------------------------------------------------------
    def _close_top(self):
        """最上位カードを閉じ、1 つ前のイベントを表示する"""
        if self._stack:
            self._stack.pop()
        self._render()

    def _confirm_top(self):
        if not self._stack:
            return
        card = self._stack[-1]
        if self._on_confirm:
            self._on_confirm(card)

    def _render(self):
        if self._frame is not None:
            self._frame.destroy()
            self._frame = None
        if not self._stack:
            return

        card = self._stack[-1]
        behind = len(self._stack) - 1  # 背後に控えるカード数

        # 外枠 (下辺のみアクセントラインを引くため highlightthickness を利用)
        self._frame = tk.Frame(
            self._container, bg=_CARD_BG,
            highlightbackground=_CARD_BORDER, highlightthickness=1, bd=0
        )
        inner = tk.Frame(self._frame, bg=_CARD_BG)
        # 高さを狭めるため pady=0 に変更
        inner.pack(fill=tk.X, padx=6, pady=0)

        # ボタン群を先に配置して右側の領域を確保 (狭幅でも見切れさせない)
        btns = tk.Frame(inner, bg=_CARD_BG)
        btns.pack(side=tk.RIGHT, pady=0)
        if behind > 0:
            tk.Label(btns, text=f"他{behind}件", bg=_CARD_BG, fg=_LABEL_FG,
                     font=("Meiryo", 8)).pack(side=tk.LEFT, padx=(0, 4))
                     
        # ボタンを縦に並べるためのフレーム
        btn_col = tk.Frame(btns, bg=_CARD_BG)
        btn_col.pack(side=tk.LEFT)
        
        self._make_button(btn_col, "確認", "#2a6496", "#357ebd", self._confirm_top, {"side": tk.TOP, "pady": (0, 1), "fill": tk.X})
        self._make_button(btn_col, "×", "#c0392b", "#e74c3c", self._close_top, {"side": tk.TOP, "fill": tk.X})

        # 左: 情報 (grid で見出し行と値行を列ごとに整列)
        info = tk.Frame(inner, bg=_CARD_BG)
        # 高さを詰めるため、情報領域の pack にも不要な余白を入れない
        info.pack(side=tk.LEFT, anchor=tk.W, pady=0)

        label_font = ("MS Gothic", 8)
        value_font = ("MS Gothic", 10, "bold")
        date_font = ("MS Gothic", 8, "bold")  # 日付は小さめ
        time_font = ("MS Gothic", 10, "bold") # 時刻はやや大きく

        date_line1, date_line2 = _split_dt_lines(card.get("dt", "--/-- --:--"))
        right_pads = [8, 6, 0]  # 列間の右パディング(日時→震源→最大)。震源・最大を左へ寄せて詰める。

        # 縦方向のスペースを節約するため、日時を別フレームにまとめる (2行分の高さを最小化)
        tk.Label(info, text="日時", bg=_CARD_BG, fg=_LABEL_FG, font=label_font, bd=0, pady=0).grid(
            row=0, column=0, sticky=tk.W, padx=(0, right_pads[0]), pady=0)
            
        dt_frame = tk.Frame(info, bg=_CARD_BG)
        dt_frame.grid(row=1, column=0, sticky=tk.NW, padx=(0, right_pads[0]), pady=0)
        tk.Label(dt_frame, text=date_line1, bg=_CARD_BG, fg=_DATE_FG, font=date_font, bd=0, pady=0).pack(anchor=tk.W, pady=0)
        tk.Label(dt_frame, text=date_line2, bg=_CARD_BG, fg=_DATE_FG, font=time_font, bd=0, pady=0).pack(anchor=tk.W, pady=0)

        tk.Label(info, text="震源", bg=_CARD_BG, fg=_LABEL_FG, font=label_font, bd=0, pady=0).grid(
            row=0, column=1, sticky=tk.W, padx=(0, right_pads[1]), pady=0)
        tk.Label(info, text=card.get("hypo", "－－－"), bg=_CARD_BG, fg=_VALUE_FG,
                 font=value_font, bd=0, pady=0).grid(row=1, column=1, sticky=tk.W, padx=(0, right_pads[1]), pady=0)

        tk.Label(info, text="最大", bg=_CARD_BG, fg=_LABEL_FG, font=label_font, bd=0, pady=0).grid(
            row=0, column=2, sticky="", pady=0)
        tk.Label(info, text=card.get("max_int", "-"), bg=_CARD_BG,
                 fg=_intensity_colour(card.get("max_int", "")), font=value_font, bd=0, pady=0).grid(
            row=1, column=2, sticky="", pady=0)

        # ログ Text の上端に重ねる
        self._frame.place(in_=self._anchor, relx=0.0, rely=0.0, relwidth=1.0)

    @staticmethod
    def _make_button(parent, text, bg, hover_bg, command, pack_kwargs=None):
        if pack_kwargs is None:
            pack_kwargs = {"side": tk.LEFT, "padx": 2}
            
        btn = tk.Label(parent, text=text, bg=bg, fg="white",
                       font=("Meiryo", 8, "bold"), cursor="hand2",
                       padx=8, pady=0, relief="flat") # pady=1 -> 0 に縮小して高さを抑える
        btn.pack(**pack_kwargs)
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>", lambda e: btn.config(bg=hover_bg))
        btn.bind("<Leave>", lambda e: btn.config(bg=bg))
        return btn
