import tkinter as tk
from PIL import Image, ImageTk, ImageDraw
import queue
import json
import os
import time
import datetime
import eq_config.eq_config as config

class NiedMapVisualizer:
    def __init__(self, root, settings:dict, title="NIED Realtime Monitor", mode="full"):
        self.root = root
        if hasattr(self.root, 'title'):
            self.root.title(title)
        self.mode = mode
        
        # settings.json は config が解決する PROJECT_ROOT を基準にする（frozen/exe 対応）
        import os
        self.settings_path = os.path.join(config.PROJECT_ROOT, "eq_config", "eq_settings.json")

        # 通信監視用変数
        self.last_update_time = time.time() # 最終更新時刻
        self.timeout_seconds = 15      # タイムアウト閾値（秒）(警告を出すまで)

        # 監視半径読み込み
        self.monitor_radius = settings.get("radius_pixel", 30)

        # 凡例・文字サイズ設定
        self.legend_width = settings.get("legend_width", 35)
        self.legend_height = settings.get("legend_height", 400)
        self.base_font_size = settings.get("base_font_size", 11)

        # キャッシュ用変数
        self.last_raw_image = None
        self.last_timestamp = None
        self.last_max_intensity = None
        self._font_cache = {}

        # シミュレーション時の青枠中心オーバーライド (None = configを使用)
        self.override_cx = None
        self.override_cy = None
        
        # --- カラーマップ読み込み ---
        self.color_map_path = config.COLOR_MAP_PATH
        self.color_data = self._load_color_map()

        # --- 全体レイアウト用コンテナ ---
        self.container = tk.Frame(root, bg="black")
        self.container.pack(fill=tk.BOTH, expand=True)

        # --- ステータスバーエリア ---
        self.status_frame = tk.Frame(self.container, bg="black")
        self.status_frame.pack(side=tk.BOTTOM, fill=tk.X)

        # --- ビジュアル表示エリア ---
        self.visual_frame = tk.Frame(self.container, bg="black")
        self.visual_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        #
        if self.mode == "cropped":
            self.canvas_width = 300
            self.canvas_height = 300
        else:
            self.canvas_width = 352
            self.canvas_height = 400

        # --- [右] 凡例表示エリア (visual_frame内に配置 - 見切れ防止のためマップより先にpack) ---
        self.legend_frame = tk.Frame(self.visual_frame, bg="black", width=0)
        self.legend_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(0,0))

        # 凡例用キャンバス (bar_w=6 + ラベル最大幅=28px => 合計34px)
        self.legend_canvas = tk.Canvas(
            self.legend_frame, 
            width=self.legend_width, 
            height=self.legend_height, 
            bg="black", 
            highlightthickness=0
        )
        self.legend_canvas.pack(side=tk.TOP, fill=tk.Y, padx=(5,0), pady=5)

        # --- [左] マップ表示エリア ---
        self.map_frame = tk.Frame(self.visual_frame, bg="black")
        self.map_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # マップ描画用キャンバス (ステータスバーを押し潰さないよう初期サイズを小さく設定)
        self.canvas = tk.Canvas(
            self.map_frame, 
            bg="black", 
            highlightthickness=0,
            width=10,
            height=10
        )
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Canvas上の画像IDを保持する変数(メモリリーク対策)
        self.canvas_image_id = None
        self.latest_pil_image = None
        self.on_first_image_callback = None  # 初回画像到着時に一度だけ呼ばれる
        self.secondary_viz = None  # ズーム用サブビジュアライザへの転送先
        self.canvas.bind("<Configure>", self.on_canvas_resize)

        # --- ステータスバーの中身の配置(self.status_frame内に配置) ---
        # 1. インジケータ (左端)
        self.indicator_canvas = tk.Canvas(
            self.status_frame, 
            width=14, height=14, 
            bg="black", highlightthickness=0
        )
        self.indicator_canvas.pack(side=tk.LEFT, padx=(5,0))
        self.indicator_id = self.indicator_canvas.create_oval(3, 3, 12, 12, fill="gray", outline="")

        # 2. ステータステキスト (インジケータの右)
        self.status_label = tk.Label(
            self.status_frame, 
            text="Initializing...", 
            font=("Consolas", self.base_font_size, "bold"), 
            fg="white", bg="black"
        )
        self.status_label.pack(side=tk.LEFT)
        self.status_frame.bind("<Configure>", self.on_status_configure)

        # 初回に一度だけ凡例を描画
        self._draw_legend()
        self.legend_canvas.bind("<Configure>", self.on_legend_resize)

        # --- 画像更新キュー処理 ---
        self.image_queue = queue.Queue()
        self.root.after(100, self._check_queue)

        # 通信監視ループを開始
        self.root.after(1000, self._monitor_connection)

    def set_center_override(self, lat: float, lon: float):
        """緯度経度から青枠中心を一時的にオーバーライドする（シミュレーション用）"""
        from eq_config.eq_config import latlon_to_nied_pixel
        x, y = latlon_to_nied_pixel(lat, lon)
        self.override_cx = x
        self.override_cy = y

    def clear_center_override(self):
        """青枠中心オーバーライドを解除しconfigの値に戻す"""
        self.override_cx = None
        self.override_cy = None

    def on_canvas_resize(self, event):
        if hasattr(self, 'latest_pil_image') and self.latest_pil_image:
            self._display_scaled_image()

    def set_legend_visible(self, visible: bool):
        """凡例（カラーバー）の表示・非表示を切り替える"""
        if visible:
            self.legend_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(0,0))
            # マップ表示エリアを再packして順番を保つ
            self.map_frame.pack_forget()
            self.map_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        else:
            self.legend_frame.pack_forget()

    def _display_scaled_image(self):
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10: return
        
        img_w, img_h = self.latest_pil_image.size
        scale = min(cw / img_w, ch / img_h)
        new_w = max(1, int(img_w * scale))
        new_h = max(1, int(img_h * scale))
        
        resized = self.latest_pil_image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(resized)
        
        # PiP時（ウィンドウが狭い場合）はマップを左寄せにして左余白をなくす
        container_w = self.container.winfo_width()
        if container_w > 0 and container_w < 200:
            x_offset = 0
        else:
            x_offset = (cw - new_w) // 2
        y_offset = (ch - new_h) // 2
        
        if self.canvas_image_id is None:
            self.canvas_image_id = self.canvas.create_image(x_offset, y_offset, anchor=tk.NW, image=self.tk_img)
        else:
            self.canvas.coords(self.canvas_image_id, x_offset, y_offset)
            self.canvas.itemconfig(self.canvas_image_id, image=self.tk_img)
            
        # --- カウントダウン表示の描画 ---
        # 毎フレーム既存のテキスト・シャドウを先に削除
        if hasattr(self, 'countdown_text_id') and self.countdown_text_id:
            self.canvas.delete(self.countdown_text_id)
            self.countdown_text_id = None
        if hasattr(self, 'countdown_shadow_id') and self.countdown_shadow_id:
            self.canvas.delete(self.countdown_shadow_id)
            self.countdown_shadow_id = None

        countdown = getattr(self, 'current_countdown_sec', None)
        is_custom = getattr(self, 'current_is_custom_prediction', False)
        predicted_scale = getattr(self, 'current_predicted_home_scale', 0)

        if countdown is not None:
            if countdown > 0:
                line1 = f"S波到達まで\n約 {countdown}秒"
                color = "orange" if is_custom else "#ff4444"
            else:
                line1 = "S波到達！"
                color = "orange" if is_custom else "red"

            _scale_map = {10:"1", 20:"2", 30:"3", 40:"4", 45:"5弱", 50:"5強", 55:"6弱", 60:"6強", 70:"7"}
            if predicted_scale > 0:
                txt = f"{line1}\n予想震度：{_scale_map.get(predicted_scale, '?')}"
            else:
                txt = line1

            x = cw / 2
            y = 30
            font_setting = ("Meiryo", max(14, min(24, int(cw/13))), "bold")
            self.countdown_shadow_id = self.canvas.create_text(
                x+2, y+2, text=txt, fill="black", font=font_setting,
                anchor=tk.N, justify=tk.CENTER
            )
            self.countdown_text_id = self.canvas.create_text(
                x, y, text=txt, fill=color, font=font_setting,
                anchor=tk.N, justify=tk.CENTER
            )

    def _load_color_map(self):
        """JSONファイルを読み込む"""
        if not os.path.exists(self.color_map_path):
            print(f"Warning: Color map not found at {self.color_map_path}")
            return []
        try:
            with open(self.color_map_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Failed to load color map: {e}")
            return []

    def on_legend_resize(self, event):
        self._draw_legend()

    def _draw_legend(self):
        """右側に震度カラーバーとラベルを描画"""
        if not self.color_data:
            return

        # 古い描画項目をクリア（再描画時の重複を防ぐ）
        self.legend_canvas.delete("all")

        # 震度0以上のみ抽出してソート
        valid_data = sorted(
            [x for x in self.color_data if x.get('Intensity', -1) >= 0], 
            key=lambda x: x['Intensity']
        )
        
        if not valid_data:
            return

        # 描画設定
        bar_x = 0
        bar_w = max(2, self.legend_width - 29)
        h = self.legend_canvas.winfo_height()
        if h < 10:
            h = self.legend_height
        max_val = 7.0 # 表示上の最大震度
        
        def to_hex(r, g, b):
            return f"#{r:02x}{g:02x}{b:02x}"

        # --- カラーバー（グラデーション）の描画 ---
        # 連続するデータ点の間を矩形で埋める
        for i in range(len(valid_data) - 1):
            curr = valid_data[i]
            next_item = valid_data[i+1]
            
            val = curr['Intensity']
            next_val = next_item['Intensity']
            
            # Y座標計算 (震度7が上(0), 震度0が下(h))
            y1 = h - int((val / max_val) * h)      # 下端
            y2 = h - int((next_val / max_val) * h) # 上端
            
            color = to_hex(curr['R'], curr['G'], curr['B'])
            
            # 隙間なく描画
            self.legend_canvas.create_rectangle(
                bar_x, y2, bar_x + bar_w, y1, 
                fill=color, outline=color
            )

        # --- ラベルの描画 ---
        # 表示したい震度の目盛り (高さが低い場合は表示する目盛り数を減らして見切れを防ぐ)
        if h < 200:
            labels = [
                (0.0, "0"), (2.0, "2"), (4.0, "4"), (6.0, "6-"), (7.0, "7")
            ]
        else:
            labels = [
                (0.0, "0"), (1.0, "1"), (2.0, "2"), (3.0, "3"), (4.0, "4"),
                (5.0, "5-"), (5.5, "5+"), (6.0, "6-"), (6.5, "6+"), (7.0, "7")
            ]
        
        for val, text in labels:
            y = h - int((val / max_val) * h)
            
            # 震度7だけキャンバス外にはみ出ないよう少し下げる調整
            if val == 7.0: y += 7
            elif val == 0.0: y -= 7
            
            # 目盛り線
            self.legend_canvas.create_line(
                bar_x, y, bar_x + bar_w + 3, y, 
                fill="white"
            )
            # 文字
            self.legend_canvas.create_text(
                bar_x + bar_w + 5, y, 
                text=text, anchor=tk.W, 
                fill="white", font=("Arial", 9, "bold")
            )


    def push_image(self, img: Image.Image, timestamp: str, max_intensity: float, countdown_sec: int = None, is_custom_prediction: bool = False, predicted_home_scale: int = 0):
        # 連続更新時は古いキューを破棄して最新のみ保持する（シミュレーション倍速再生等への対応）
        while not self.image_queue.empty():
            try:
                self.image_queue.get_nowait()
            except queue.Empty:
                break
        self.image_queue.put((img, timestamp, max_intensity, countdown_sec, is_custom_prediction, predicted_home_scale))
        if self.secondary_viz is not None:
            self.secondary_viz.push_image(img.copy(), timestamp, max_intensity, countdown_sec, is_custom_prediction, predicted_home_scale)

    def _check_queue(self):
        try:
            img_data = self.image_queue.get_nowait()
            self._update_display(*img_data)
        except queue.Empty:
            pass
        finally:
            # 100ms->15msごとにチェック
            self.root.after(15, self._check_queue)

    def _get_current_radius(self):
        """現在のJSONファイルから最新の半径設定を読み込む"""
        if not os.path.exists(self.settings_path):
            return self.monitor_radius # ファイルがない場合は前回の値を使う
            
        try:
            with open(self.settings_path, 'r') as f:
                data = json.load(f)
                # nied_monitor -> radius_pixel を取得
                return data.get("nied_monitor", {}).get("radius_pixel", 30)
        except Exception:
            # 読み込み中の競合などでエラーが出たら前回の値を使う
            return self.monitor_radius
        
    def _monitor_connection(self):
        """最後の更新から一定時間経過したら警告表示"""
        elapsed = time.time() - self.last_update_time
        if elapsed > self.timeout_seconds:
            # タイムアウト状態: インジケータを赤にしてテキストを変更
            self.indicator_canvas.itemconfig(self.indicator_id, fill="red")
            self.status_label.config(
                text=f"OFFLINE - No data ({elapsed:.0f}s)",
                fg="red"
            )
        else:
            pass
        
        # 次のチェックをスケジュール(1秒ごとにチェック)
        self.root.after(1000, self._monitor_connection)

    def _update_display(self, img: Image.Image, timestamp: str, max_intensity: float, countdown_sec: int = None, is_custom_prediction: bool = False, predicted_home_scale: int = 0):
        # 初回画像到着時にコールバックを発火（一度限り）
        if self.on_first_image_callback and self.latest_pil_image is None:
            cb = self.on_first_image_callback
            self.on_first_image_callback = None
            cb()
        # 更新時刻を記録
        self.last_update_time = time.time()
        # キャッシュ保存
        self.last_raw_image = img
        self.last_timestamp = timestamp
        self.last_max_intensity = max_intensity
        self.current_countdown_sec = countdown_sec
        self.current_is_custom_prediction = is_custom_prediction
        self.current_predicted_home_scale = predicted_home_scale
        # インジケータを緑に変更
        self.indicator_canvas.itemconfig(self.indicator_id, fill="lime") # fill="#00ff00"
        cx = self.override_cx if self.override_cx is not None else config.NIED_HOME_X
        cy = self.override_cy if self.override_cy is not None else config.NIED_HOME_Y
        new_radius = self._get_current_radius()
        self.monitor_radius = new_radius
        r = self.monitor_radius

        draw_img = img.copy().convert("RGB")
        
        # --- マップ描画ロジック ---
        if self.mode == "cropped":
            left   = max(0, cx - r)
            top    = max(0, cy - r)
            right  = min(img.width, cx + r)
            bottom = min(img.height, cy + r)
            
            cropped = draw_img.crop((left, top, right, bottom))
            resized_img = cropped.resize(
                (self.canvas_width, self.canvas_height), 
                resample=Image.Resampling.NEAREST
            )
            
            draw = ImageDraw.Draw(resized_img)
            center_x = self.canvas_width // 2
            center_y = self.canvas_height // 2
            
            line_len = 10
            draw.line((center_x - line_len, center_y, center_x + line_len, center_y), fill="cyan", width=2)
            draw.line((center_x, center_y - line_len, center_x, center_y + line_len), fill="cyan", width=2)
            draw.rectangle((0, 0, self.canvas_width-1, self.canvas_height-1), outline="cyan", width=4)

            self.latest_pil_image = resized_img

        else:
            draw = ImageDraw.Draw(draw_img)
            left, top = cx - r, cy - r
            right, bottom = cx + r, cy + r
            draw.rectangle((left, top, right, bottom), outline="cyan", width=2)
            draw.line((cx-5, cy, cx+5, cy), fill="cyan", width=1)
            draw.line((cx, cy-5, cx, cy+5), fill="cyan", width=1)

            self.latest_pil_image = draw_img
        
        self._display_scaled_image()
            
        display_time = ""
        # タイムスタンプ表示
        try:
            if "(T" in timestamp:
                # シミュレーションエンジンが生成した "(T-5s)" 等を含む文字列をそのまま利用
                display_time = f"[SIM] {timestamp}"
            elif len(timestamp) == 14:
                img_dt = datetime.datetime.strptime(timestamp, "%Y%m%d%H%M%S")
                
                # 表示用フォーマット YYYY/MM/DD HH:MM:SS
                fmt_time = img_dt.strftime("%y/%m/%d %H:%M:%S")
                
                # 遅延時間の計算 (現在時刻 - 画像時刻)
                # 画像は過去のものなので (画像 - 現在) は負の値
                now = datetime.datetime.now()
                diff_sec = (img_dt - now).total_seconds()
                
                # フォーマット: YYYY/MM/DD HH:MM:SS (-X.X sec)
                display_time = f"{fmt_time}({diff_sec:.1f}sec)"
            else:
                display_time = timestamp
        except Exception as e:
            display_time = timestamp # エラー時はそのまま表示

        # 震度表示
        int_str = f"{max_intensity:.1f}" if max_intensity > -3.0 else "--"
        color = "white"
        if max_intensity >= 1.0: color = "yellow"
        if max_intensity >= 3.0: color = "red"
        
        # 画面幅（ステータスバー幅）に応じて表示内容やフォントを切り替える（見切れ防止）
        fw = self.status_frame.winfo_width()
        if fw < 10:
            fw = self.container.winfo_width()
        if fw < 10:
            fw = 300 if self.mode == "cropped" else 352
            
        base = self.base_font_size
        
        # 極小用時刻の作成
        if len(timestamp) == 14:
            try:
                short_time = datetime.datetime.strptime(timestamp, "%Y%m%d%H%M%S").strftime("%H:%M:%S")
            except:
                short_time = timestamp[-6:] if len(timestamp) >= 6 else timestamp
        else:
            short_time = timestamp

        # PiP等の極小幅では mm:ss を表示する
        short_time_ms = short_time[3:8] if ":" in short_time else short_time

        # 表示するテキスト候補とフォントサイズの組合せを優先度順に定義
        candidates = [
            (f"Updated:{display_time} | MaxInt:{int_str}", base),
            (f"Updated:{display_time} | MaxInt:{int_str}", max(8, base - 1)),
            (f"{display_time} | MaxInt:{int_str}", base),
            (f"{display_time} | MaxInt:{int_str}", max(8, base - 1)),
            (f"{display_time} | MaxInt:{int_str}", max(7, base - 2)),
            (f"{short_time} | Max:{int_str}", max(7, base - 2)),
            (f"{short_time} | {int_str}", max(6, base - 3)),
            (f"{short_time_ms} | {int_str}", max(6, base - 3)),
            (f"{int_str}", max(6, base - 3))
        ]

        import tkinter.font as tkfont
        selected_text, selected_size = candidates[-1]

        for txt_candidate, sz_candidate in candidates[:-1]:
            key = (sz_candidate, "bold")
            if key not in self._font_cache:
                self._font_cache[key] = tkfont.Font(family="Consolas", size=sz_candidate, weight="bold")
            
            # 計測幅 ＋ インジケータ(14px) ＋ 余白等(20px) = 計34px
            measured_w = self._font_cache[key].measure(txt_candidate) + 34
            if measured_w <= fw:
                selected_text = txt_candidate
                selected_size = sz_candidate
                break

        # インジケータサイズの設定
        if selected_size < 9:
            self.indicator_canvas.config(width=9, height=9)
            self.indicator_canvas.coords(self.indicator_id, 2, 2, 7, 7)
        else:
            self.indicator_canvas.config(width=14, height=14)
            self.indicator_canvas.coords(self.indicator_id, 3, 3, 12, 12)

        self.status_label.config(
            text=selected_text,
            fg=color,
            font=("Consolas", selected_size, "bold")
        )

    def update_sizes(self, legend_width: int, legend_height: int, base_font_size: int):
        self.legend_width = legend_width
        self.legend_height = legend_height
        self.base_font_size = base_font_size
        self.legend_canvas.config(width=self.legend_width, height=self.legend_height)
        self._draw_legend()
        if self.last_raw_image is not None:
            self._update_display(self.last_raw_image, self.last_timestamp, self.last_max_intensity)

    def on_status_configure(self, event):
        new_width = event.width
        if not hasattr(self, '_last_status_width') or self._last_status_width != new_width:
            self._last_status_width = new_width
            if self.last_raw_image is not None:
                self._update_display(self.last_raw_image, self.last_timestamp, self.last_max_intensity)