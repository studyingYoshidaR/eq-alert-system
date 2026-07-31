import tkinter as tk
from PIL import Image, ImageTk, ImageDraw
import queue
import json
import os
import eq_config.eq_config as config

class NiedMapVisualizer:
    def __init__(self, root: tk.Tk, title="NIED Realtime Monitor", mode="full"):
        self.root = root
        self.root.title(title)
        self.mode = mode
        
        # --- カラーマップ読み込み ---
        self.color_map_path = "../eq_assets/eq_NiedColorMap.json"
        self.color_data = self._load_color_map()

        # --- 全体レイアウト用コンテナ ---
        self.container = tk.Frame(root, bg="black")
        self.container.pack(fill=tk.BOTH, expand=True)

        # --- [左] マップ表示エリア ---
        self.map_frame = tk.Frame(self.container, bg="black")
        self.map_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        if self.mode == "cropped":
            self.canvas_width = 300
            self.canvas_height = 300
        else:
            self.canvas_width = 352
            self.canvas_height = 400

        self.canvas = tk.Canvas(
            self.map_frame, 
            width=self.canvas_width, 
            height=self.canvas_height, 
            bg="black", 
            highlightthickness=0
        )
        self.canvas.pack(side=tk.TOP, pady=5)

        self.status_label = tk.Label(
            self.map_frame, 
            text="Initializing...", 
            font=("Consolas", 12, "bold"), 
            fg="white", bg="black"
        )
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

        # --- [右] 凡例表示エリア ---
        self.legend_frame = tk.Frame(self.container, bg="black", width=60)
        self.legend_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5)
        
        # 凡例用キャンバス（高さはマップに合わせる）
        self.legend_canvas = tk.Canvas(
            self.legend_frame, 
            width=40, 
            height=self.canvas_height, 
            bg="black", 
            highlightthickness=0
        )
        self.legend_canvas.pack(side=tk.TOP, fill=tk.Y, pady=5)

        # 初回に一度だけ凡例を描画
        self._draw_legend()

        # --- 画像更新キュー処理 ---
        self.image_queue = queue.Queue()
        self.root.after(100, self._check_queue)

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

    def _draw_legend(self):
        """右側に震度カラーバーとラベルを描画"""
        if not self.color_data:
            return

        # 震度0以上のみ抽出してソート
        valid_data = sorted(
            [x for x in self.color_data if x.get('Intensity', -1) >= 0], 
            key=lambda x: x['Intensity']
        )
        
        if not valid_data:
            return

        # 描画設定
        bar_x = 0
        bar_w = 10  # カラーバーの幅
        h = self.canvas_height
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
        # 表示したい震度の目盛り
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

    def push_image(self, img: Image.Image, timestamp: str, max_intensity: float):
        if self.image_queue.empty():
            self.image_queue.put((img, timestamp, max_intensity))

    def _check_queue(self):
        try:
            img_data = self.image_queue.get_nowait()
            self._update_display(*img_data)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._check_queue)

    def _update_display(self, img: Image.Image, timestamp: str, max_intensity: float):
        cx, cy = config.NIED_HOME_X, config.NIED_HOME_Y
        r = getattr(config, 'MONITOR_RADIUS_PX', 30)

        draw_img = img.copy().convert("RGB")
        
        # --- マップ描画ロジック (変更なし) ---
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

            self.tk_img = ImageTk.PhotoImage(resized_img)

        else:
            draw = ImageDraw.Draw(draw_img)
            left, top = cx - r, cy - r
            right, bottom = cx + r, cy + r
            draw.rectangle((left, top, right, bottom), outline="cyan", width=2)
            draw.line((cx-5, cy, cx+5, cy), fill="cyan", width=1)
            draw.line((cx, cy-5, cx, cy+5), fill="cyan", width=1)

            self.tk_img = ImageTk.PhotoImage(draw_img)
        
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.tk_img)

        if len(timestamp) == 14:
            # "YYYY/MM/DD HH:MM:SS" 形式に変換
            display_time = f"{timestamp[0:4]}/{timestamp[4:6]}/{timestamp[6:8]} {timestamp[8:10]}:{timestamp[10:12]}:{timestamp[12:14]}"
        else:
            display_time = timestamp

        int_str = f"{max_intensity:.1f}" if max_intensity > -3.0 else "--"
        color = "white"
        if max_intensity >= 1.0: color = "yellow"
        if max_intensity >= 3.0: color = "red"
        
        self.status_label.config(
            text=f"Time: {display_time} | Max Intensity: {int_str}",
            fg=color
        )