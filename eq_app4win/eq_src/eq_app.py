import os
import sys
import socket
import json
import re
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import queue
from PIL import Image, ImageTk

# --- パス設定 ---
if getattr(sys, 'frozen', False):          # PyInstaller bundle
    CURRENT_DIR = sys._MEIPASS
    PROJECT_ROOT = sys._MEIPASS
else:
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
# util_audio_player.py は PROJECT_ROOT (eq_app4win/) 直下に配置
AUDIO_UTIL_DIR = PROJECT_ROOT
if CURRENT_DIR not in sys.path:
    sys.path.append(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)
sys.path.append(AUDIO_UTIL_DIR)

from eq_main import EqSystem
from eq_visualizer import NiedMapVisualizer
from eq_timeline import TimelineCanvas, diff_dicts
try:
    import util_audio_player  # type: ignore
except ImportError:
    class _MockAudioPlayer:
        @staticmethod
        def play_audio(*args, **kwargs): pass
        @staticmethod
        def play_audio_list(*args, **kwargs): pass
        @staticmethod
        def stop_audio(*args, **kwargs): pass
    util_audio_player = _MockAudioPlayer()
from eq_test.eq_run_simulation2 import run_simulation_engine, collect_past_events, format_hypocenter, pad_ja, center_ja, intensity_to_float, SIM_VOICE_DIR, parse_eew_file, SimEvent
from eq_monitor_card import MonitorLogCardStack

class SimTextRedirector:
    def __init__(self, callback):
        self.callback = callback

    def write(self, string):
        if string:
            self.callback(string)

    def flush(self):
        pass

import time

class LoadingOverlay:
    def __init__(self, parent, title="Loading", message="お待ちください...", delay_ms=0):
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.geometry("300x120")
        self.top.configure(bg="#2b2b2b")
        # タイトルバーを非表示にし、常に最前面へ
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.transient(parent)
        
        # 画面中央へ配置
        parent.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        if pw <= 1 or ph <= 1:
            pw, ph = parent.winfo_screenwidth(), parent.winfo_screenheight()
            px, py = 0, 0
        self.x = px + (pw // 2) - 150
        self.y = py + (ph // 2) - 60
        self.top.geometry(f"300x120+{self.x}+{self.y}")
        
        # UI
        border_frame = tk.Frame(self.top, bg="#0078D7", bd=2)
        border_frame.pack(fill=tk.BOTH, expand=True)
        inner_frame = tk.Frame(border_frame, bg="#2b2b2b")
        inner_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        self.lbl_title = tk.Label(inner_frame, text=title, bg="#2b2b2b", fg="#ffffff", font=("Meiryo", 10, "bold"))
        self.lbl_title.pack(pady=(10, 5))
        
        self.lbl_msg = tk.Label(inner_frame, text=message, bg="#2b2b2b", fg="#aaaaaa", font=("Meiryo", 9))
        self.lbl_msg.pack(pady=(0, 10))
        
        self.progress = ttk.Progressbar(inner_frame, orient=tk.HORIZONTAL, length=250, mode='determinate')
        self.progress.pack(pady=(0, 10))
        
        self.top.withdraw() # 初期は非表示
        
        self._is_showing = False
        self._is_closed = False
        self._show_time = time.time() + (delay_ms / 1000.0)
        
        if delay_ms <= 0:
            self._show()

    def _show(self):
        if self._is_closed or self._is_showing: return
        self._is_showing = True
        self.top.geometry(f"300x120+{self.x}+{self.y}")
        self.top.deiconify()
        self.top.grab_set()
        self.top.update()

    def tick(self):
        if not self._is_showing and not self._is_closed:
            if time.time() >= self._show_time:
                self._show()
        if self._is_showing:
            self.top.update()

    def update_message(self, msg):
        self.lbl_msg.config(text=msg)
        self.tick()

    def update_progress(self, val, max_val=100, msg=None):
        self.progress["maximum"] = max_val
        self.progress["value"] = val
        if msg:
            self.lbl_msg.config(text=msg)
        self.tick()
        
    def set_indeterminate(self):
        self.progress.config(mode='indeterminate')
        self.progress.start(10)
        self.tick()

    def close(self):
        self._is_closed = True
        if self._is_showing:
            self.top.grab_release()
        self.top.destroy()


class SlideSwitch(tk.Canvas):
    def __init__(self, parent, var, command=None, text_off="地表", text_on="地中", width=60, height=22, **kwargs):
        try:
            default_bg = parent.cget("bg")
        except tk.TclError:
            try:
                default_bg = parent.cget("background")
            except tk.TclError:
                default_bg = "#3c3f41" # fallback for ttk.Frame
                
        bg = kwargs.pop("bg", default_bg)
        super().__init__(parent, width=width, height=height, highlightthickness=0, bg=bg, **kwargs)
        self.var = var
        self.command = command
        self.text_off = text_off
        self.text_on = text_on
        self.width = width
        self.height = height
        
        self.bind("<Button-1>", self.toggle)
        self.draw()
        
        # Add trace to update UI if variable changes externally
        self._trace_id = self.var.trace_add("write", lambda *args: self.draw())
        self.bind("<Destroy>", self._on_destroy)

    def _on_destroy(self, event):
        try:
            self.var.trace_remove("write", self._trace_id)
        except Exception:
            pass

    def draw(self):
        self.delete("all")
        is_on = self.var.get()
        bg_color = "#8b4513" if is_on else "#2a6496"
        
        # Background pill shape
        r = self.height / 2
        self.create_oval(0, 0, r*2, self.height, fill=bg_color, outline="")
        self.create_oval(self.width - r*2, 0, self.width, self.height, fill=bg_color, outline="")
        self.create_rectangle(r, 0, self.width - r, self.height, fill=bg_color, outline="")
        
        # Text
        text_str = self.text_on if is_on else self.text_off
        text_x = self.width / 2 - 8 if is_on else self.width / 2 + 8
        self.create_text(text_x, self.height / 2, text=text_str, fill="white", font=("Meiryo", 8, "bold"))
        
        # Toggle circle
        cr = r - 2
        cx = self.width - r if is_on else r
        self.create_oval(cx - cr, 2, cx + cr, self.height - 2, fill="white", outline="")

    def toggle(self, event=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

class UnifiedEqApp:
    def __init__(self, root, splash=None):
        self.root = root
        self.root.title("Earthquake Alert System")
        self.root.geometry("1099x420")

        # --- ウィンドウアイコン (タスクバー・タイトルバー) ---
        # iconphoto() + PNG で高解像度アイコンを設定（ico上限の256pxを超えられる）
        # PhotoImage を self に保持しないとGCで消える
        _icon_png = os.path.join(PROJECT_ROOT, "eq_assets", "app_icon.png")
        _icon_ico = os.path.join(PROJECT_ROOT, "eq_assets", "app_icon.ico")
        def _set_window_icon():
            try:
                _img = Image.open(_icon_png).resize((512, 512), Image.LANCZOS)
                self._app_icon_photo = ImageTk.PhotoImage(_img)
                self.root.iconphoto(True, self._app_icon_photo)
            except Exception:
                # フォールバック: ico
                try:
                    if os.path.exists(_icon_ico):
                        self.root.iconbitmap(_icon_ico)
                except Exception:
                    pass
        if os.path.exists(_icon_png):
            self.root.after(0, _set_window_icon)

        # --- Styling (Dark Theme) ---
        style = ttk.Style()
        style.theme_use('clam')
        bg_color = "#2b2b2b"
        fg_color = "#ffffff"
        panel_bg = "#3c3f41"
        
        self.root.configure(bg=bg_color)
        style.configure("TFrame", background=bg_color)
        style.configure("Panel.TFrame", background=panel_bg)
        style.configure("TLabel", background=bg_color, foreground=fg_color, font=("Meiryo", 10))
        style.configure("Panel.TLabel", background=panel_bg, foreground=fg_color, font=("Meiryo", 10))
        style.configure("TNotebook", background=bg_color, borderwidth=0)
        style.configure("TNotebook.Tab", background=panel_bg, foreground="white", font=("Meiryo", 9), padding=[4, 2])
        style.map("TNotebook.Tab", background=[("selected", "#505354")])

        # --- 状態管理 ---
        self.state = "home"
        self.settings_path = os.path.join(PROJECT_ROOT, "eq_config", "eq_settings.json")
        self.debug_log_path = os.path.join(PROJECT_ROOT, "drag_debug.log")

        # PiP state management
        self.pip_visible_full = True
        self.pip_visible_crop = True
        self.pip_relx_full = None
        self.pip_rely_full = None
        self.pip_relx_crop = None
        self.pip_rely_crop = None

        # --- データソースパス設定 (AppData) ---
        import eq_config.eq_config as _cfg
        _monitor_base = _cfg.get_monitor_base_dir()
        self.log_dir            = _cfg.APPDATA_DIR
        self.eew_log_dir        = _cfg.get_wolfx_log_dir()
        self.nied_image_log_dir = os.path.join(_monitor_base, "eq_log")
        # フォールバック: 既存の eqlog_* ディレクトリも対応
        self.legacy_log_dir     = _monitor_base

        # --- 左右のフレーム分割 ---
        self.left_frame = ttk.Frame(root, width=290, style="Panel.TFrame")
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 5), pady=10)
        self.left_frame.pack_propagate(False)

        self.right_frame = tk.Frame(root, bg="black")
        self.right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 2), pady=10)
        self.viewport = self.right_frame

        # --- 左パネル (タイトル + Wolfx/NIED状態 + タブ) ---
        # タイトルとステータス行をまとめて1つのFrameに収め、地震発生中は
        # このブロック全体の背景を赤化できるようにする (地震発生中表示)
        self.header_alert_frame = tk.Frame(self.left_frame, bg=panel_bg)
        self.header_alert_frame.pack(fill=tk.X)

        self.title_label = tk.Label(
            self.header_alert_frame, text="Earthquake Alert System",
            font=("Meiryo", 12, "bold"), bg=panel_bg, fg="white"
        )
        self.title_label.pack(pady=(2, 2))

        # Wolfx / NIED 接続状態 + 地震発生中カウントアップ表示
        self.status_rows_frame = tk.Frame(self.header_alert_frame, bg=panel_bg)
        self.status_rows_frame.pack(fill=tk.X, padx=10, pady=(0, 2))
        self.status_rows_frame.grid_columnconfigure(0, weight=0)

        status_label_font = ("Meiryo", 9, "bold")

        wolfx_label_lbl = tk.Label(self.status_rows_frame, text="Wolfx", font=status_label_font, bg=panel_bg, fg="white", pady=0)
        wolfx_label_lbl.grid(row=0, column=0, sticky="w", pady=0)
        wolfx_colon_lbl = tk.Label(self.status_rows_frame, text=":", font=status_label_font, bg=panel_bg, fg="white", pady=0)
        wolfx_colon_lbl.grid(row=0, column=1, sticky="w", pady=0)
        self.wolfx_status_label = tk.Label(self.status_rows_frame, text="Initializing...", font=status_label_font, bg=panel_bg, fg="white", pady=0)
        self.wolfx_status_label.grid(row=0, column=2, sticky="w", padx=(4, 0), pady=0)

        nied_label_lbl = tk.Label(self.status_rows_frame, text="NIED(S/B)", font=status_label_font, bg=panel_bg, fg="white", pady=0)
        nied_label_lbl.grid(row=1, column=0, sticky="w", pady=0)
        nied_colon_lbl = tk.Label(self.status_rows_frame, text=":", font=status_label_font, bg=panel_bg, fg="white", pady=0)
        nied_colon_lbl.grid(row=1, column=1, sticky="w", pady=0)
        self.nied_status_label = tk.Label(self.status_rows_frame, text="Initializing...", font=status_label_font, bg=panel_bg, fg="white", pady=0)
        self.nied_status_label.grid(row=1, column=2, sticky="w", padx=(4, 0), pady=0)

        # 地震発生中カウントアップ / 平常時は "--:--"。Wolfx行とNIED行の中間の高さに右寄せで配置
        self.alert_timer_label = tk.Label(
            self.status_rows_frame, text="--:--", font=("MS Gothic", 13, "bold"), bg=panel_bg, fg="white"
        )
        self.alert_timer_label.place(relx=1.0, rely=0.5, anchor="e")

        # 地震発生中に背景を赤化する対象ウィジェット一覧
        self._header_alert_widgets = [
            self.header_alert_frame, self.title_label, self.status_rows_frame,
            wolfx_label_lbl, wolfx_colon_lbl, self.wolfx_status_label,
            nied_label_lbl, nied_colon_lbl, self.nied_status_label,
            self.alert_timer_label,
        ]
        self._header_normal_bg = panel_bg
        self._header_alert_bg = "#9a2e22"
        self._alert_active_start_ts = None
        self._alert_timer_after_id = None
        self._alert_hypo3 = "---"
        self._alert_max_int_str = "-"
        self._alert_is_test = False
        self._test_alert_after_id = None

        self.notebook = ttk.Notebook(self.left_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        if splash: splash.update_message("UIのタブを構築しています...")
        self.setup_tabs()

        # --- 右パネル (ビューポート) ---
        # 1. Simulation View
        if splash: splash.update_message("設定を読み込んでいます...")
        settings = self.load_settings()
        nied_settings = settings.get("nied_monitor", {})

        self.sim_view = tk.Frame(self.viewport, bg="#111111")
        self.sim_default_label = tk.Label(self.sim_view, text="--- Simulation Map Area ---", bg="#111111", fg="white", font=("Meiryo", 16))
        self.sim_default_label.pack(expand=True)
        
        # Split PanedWindow for Simulation Playback (initially hidden)
        self.sim_paned_window = tk.PanedWindow(self.sim_view, orient=tk.HORIZONTAL, bg="#3c3f41", bd=0, sashwidth=4, sashpad=2)
        
        # Left Pane: Text widget for log output
        self.sim_left_pane = tk.Frame(self.sim_paned_window, bg="#1e1e1e")
        self.sim_log_text = tk.Text(self.sim_left_pane, bg="#1e1e1e", fg="white", font=("Consolas", 9), wrap=tk.NONE, bd=0, highlightthickness=0)
        self.sim_log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sim_log_scroll_y = tk.Scrollbar(self.sim_left_pane, orient="vertical", command=self.sim_log_text.yview)
        sim_log_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        sim_log_scroll_x = tk.Scrollbar(self.sim_left_pane, orient="horizontal", command=self.sim_log_text.xview)
        sim_log_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.sim_log_text.config(yscrollcommand=sim_log_scroll_y.set, xscrollcommand=sim_log_scroll_x.set)
        
        # Right Pane: Monitor Map or EEW JSON
        self.sim_right_pane = tk.Frame(self.sim_paned_window, bg="#111111")
        
        # Right Pane top control bar (Toggle Button)
        self.sim_right_top_bar = tk.Frame(self.sim_right_pane, bg="#1a1a1a", height=30)
        self.sim_right_top_bar.pack(side=tk.TOP, fill=tk.X)
        self.sim_right_top_bar.pack_propagate(False)
        
        self.sim_toggle_btn = tk.Label(self.sim_right_top_bar, text="EEW原文表示", bg="#3a3a3a", fg="#ffffff", font=("Meiryo", 8, "bold"), cursor="hand2", padx=10)
        self.sim_toggle_btn.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=2)
        self.sim_toggle_btn.bind("<Enter>", lambda e: self.sim_toggle_btn.config(bg="#555555"))
        self.sim_toggle_btn.bind("<Leave>", lambda e: self.sim_toggle_btn.config(bg="#3a3a3a"))
        self.sim_toggle_btn.bind("<Button-1>", lambda e: self.toggle_sim_right_view())

        self.sim_borehole_var = tk.BooleanVar(value=False)
        self.sim_toggle_borehole_btn = SlideSwitch(
            self.sim_right_top_bar, self.sim_borehole_var, command=self._on_toggle_borehole_sim,
            width=60, height=22, bg="#1a1a1a"
        )
        self.sim_toggle_borehole_btn.pack(side=tk.RIGHT, padx=5, pady=2)
        self.sim_toggle_borehole_btn.pack_forget() # Show only when borehole data exists

        # Right Pane main content: Map Frame
        self.sim_map_frame = tk.Frame(self.sim_right_pane, bg="black")
        self.sim_map_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        
        # Right Pane alternative content: EEW Text Frame (starts hidden)
        self.sim_eew_text_frame = tk.Frame(self.sim_right_pane, bg="#1e1e1e")
        self.sim_eew_text = tk.Text(self.sim_eew_text_frame, bg="#1e1e1e", fg="white", font=("Consolas", 10), wrap=tk.NONE, bd=0, highlightthickness=0)
        self.sim_eew_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sim_eew_scroll_y = tk.Scrollbar(self.sim_eew_text_frame, orient="vertical", command=self.sim_eew_text.yview)
        sim_eew_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        sim_eew_scroll_x = tk.Scrollbar(self.sim_eew_text_frame, orient="horizontal", command=self.sim_eew_text.xview)
        sim_eew_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.sim_eew_text.config(yscrollcommand=sim_eew_scroll_y.set, xscrollcommand=sim_eew_scroll_x.set)
        
        # dedicated visualizer for simulation map frame
        self.sim_viz = NiedMapVisualizer(self.sim_map_frame, settings=nied_settings, mode="full")
        self.sim_viz.canvas.bind("<Button-1>", lambda e: self._toggle_sim_map_zoom())

        # Zoom (cropped) frame — initially hidden, shown when user clicks the blue frame
        self.sim_map_zoom_frame = tk.Frame(self.sim_right_pane, bg="black")
        self.sim_viz_zoom = NiedMapVisualizer(self.sim_map_zoom_frame, settings=nied_settings, mode="cropped")
        self.sim_viz_zoom.canvas.bind("<Button-1>", lambda e: self._toggle_sim_map_zoom())

        # Add panes to PanedWindow
        self.sim_paned_window.add(self.sim_left_pane)
        self.sim_paned_window.add(self.sim_right_pane)
        
        self.sim_showing_eew = False
        self.sim_latest_eew = None

        # 2. Config View
        self.misc_view = tk.Frame(self.viewport, bg="#222222")
        self.misc_lbl = tk.Label(self.misc_view, text="設定は左パネルから変更・保存できます。", bg="#222222", fg="#aaaaaa", font=("Meiryo", 14))
        self.misc_lbl.pack(expand=True)

        # 3. JSON Viewer (Log View) - Split into Left and Right Columns via PanedWindow
        self.json_view = tk.Frame(self.viewport, bg="#1e1e1e")
        self.log_paned_window = tk.PanedWindow(self.json_view, orient=tk.HORIZONTAL, bg="#3c3f41", bd=0, sashwidth=4, sashpad=2)
        self.log_paned_window.pack(fill=tk.BOTH, expand=True)
        self.log_sash_initialized = False
        self.json_view.bind("<Configure>", lambda e: self.adjust_log_panes(e))
        
        # State variables for 2 panels
        self.log_pane_left_visible = True
        self.log_pane_right_visible = False
        self.selected_file_path_1 = None
        self.selected_file_path_2 = None
        self.log_target_panel_var = tk.StringVar(value="left")
        self.log_zoom_factor_1 = 1.0
        self.log_zoom_factor_2 = 1.0
        self.play_session_counters = {"left": 0, "right": 0}
        self.active_play_loops = {"left": None, "right": None}
        
        # Column 1 (Left Panel)
        self.log_pane_left = tk.Frame(self.log_paned_window, bg="#1e1e1e", highlightthickness=2, highlightbackground="#2a6496")
        self.log_paned_window.add(self.log_pane_left, minsize=100)
        
        # Left Panel Header Frame
        self.log_pane_left_header = tk.Frame(self.log_pane_left, bg="#3a3a3a", height=20)
        self.log_pane_left_header.pack(side=tk.TOP, fill=tk.X)
        self.log_pane_left_header.pack_propagate(False)
        
        self.btn_close_1 = tk.Label(self.log_pane_left_header, text="×", bg="#3a3a3a", fg="#888888", font=("Meiryo", 9, "bold"), cursor="hand2", width=2)
        self.btn_close_1.pack(side=tk.RIGHT, padx=2)
        self.btn_close_1.bind("<Button-1>", lambda e: self.close_panel("left"))
        self.btn_close_1.bind("<Enter>", lambda e: self.btn_close_1.config(bg="#d9534f", fg="white") if self.log_pane_left_visible and self.log_pane_right_visible else None)
        self.btn_close_1.bind("<Leave>", lambda e: self.btn_close_1.config(bg=self.log_pane_left_header.cget("bg"), fg="#888888") if self.log_pane_left_visible and self.log_pane_right_visible else None)
        
        self.btn_swap_1 = tk.Label(self.log_pane_left_header, text="⇔", bg="#3a3a3a", fg="#888888", font=("Meiryo", 9, "bold"), cursor="hand2", width=2)
        self.btn_swap_1.pack(side=tk.RIGHT, padx=2)
        self.btn_swap_1.bind("<Button-1>", lambda e: self.swap_panels())
        self.btn_swap_1.bind("<Enter>", lambda e: self.btn_swap_1.config(bg="#555555", fg="white"))
        self.btn_swap_1.bind("<Leave>", lambda e: self.btn_swap_1.config(bg=self.log_pane_left_header.cget("bg"), fg="#888888"))

        self.btn_split_1 = tk.Label(self.log_pane_left_header, text="＋", bg="#3a3a3a", fg="#888888", font=("Meiryo", 9, "bold"), cursor="hand2", width=2)
        self.btn_split_1.bind("<Button-1>", lambda e: self.split_panel())
        self.btn_split_1.bind("<Enter>", lambda e: self.btn_split_1.config(bg="#555555", fg="white"))
        self.btn_split_1.bind("<Leave>", lambda e: self.btn_split_1.config(bg=self.log_pane_left_header.cget("bg"), fg="#888888"))

        self.btn_zoom_in_1 = tk.Label(self.log_pane_left_header, text="🔍+", bg="#3a3a3a", fg="#888888", font=("Meiryo", 9, "bold"), cursor="hand2", width=3)
        self.btn_zoom_in_1.bind("<Button-1>", lambda e: self.zoom_panel("left", 1.05))
        self.btn_zoom_in_1.bind("<Enter>", lambda e: self.btn_zoom_in_1.config(bg="#555555", fg="white"))
        self.btn_zoom_in_1.bind("<Leave>", lambda e: self.btn_zoom_in_1.config(bg=self.log_pane_left_header.cget("bg"), fg="#888888"))
        
        self.btn_zoom_out_1 = tk.Label(self.log_pane_left_header, text="🔍-", bg="#3a3a3a", fg="#888888", font=("Meiryo", 9, "bold"), cursor="hand2", width=3)
        self.btn_zoom_out_1.bind("<Button-1>", lambda e: self.zoom_panel("left", 0.95))
        self.btn_zoom_out_1.bind("<Enter>", lambda e: self.btn_zoom_out_1.config(bg="#555555", fg="white"))
        self.btn_zoom_out_1.bind("<Leave>", lambda e: self.btn_zoom_out_1.config(bg=self.log_pane_left_header.cget("bg"), fg="#888888"))
        
        # Left Panel Text & Canvas
        self.log_text_1 = tk.Text(self.log_pane_left, bg="#1e1e1e", fg="white", font=("Consolas", 10), wrap=tk.NONE, bd=0, highlightthickness=0)
        self.log_canvas_1 = tk.Canvas(self.log_pane_left, bg="#1e1e1e", bd=0, highlightthickness=0)
        
        self.scroll_y_1 = tk.Scrollbar(self.log_pane_left, orient="vertical", command=self.log_text_1.yview)
        self.scroll_y_1.pack(side=tk.RIGHT, fill=tk.Y)
        self.scroll_x_1 = tk.Scrollbar(self.log_pane_left, orient="horizontal", command=self.log_text_1.xview)
        self.scroll_x_1.pack(side=tk.BOTTOM, fill=tk.X)
        self.log_text_1.configure(yscrollcommand=self.scroll_y_1.set, xscrollcommand=self.scroll_x_1.set)
        self.log_text_1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Timeline Frame for Left Panel (initially hidden)
        self.tl_frame_1 = tk.Frame(self.log_pane_left, bg="#1e1e1e")
        self.tl_top_bar_1 = tk.Frame(self.tl_frame_1, bg="#2b2b2b", height=30)
        self.tl_top_bar_1.pack(side=tk.TOP, fill=tk.X)
        self.tl_top_bar_1.pack_propagate(False)
        tk.Label(self.tl_top_bar_1, text="フィルタ(項目名):", bg="#2b2b2b", fg="#cccccc", font=("Meiryo", 8)).pack(side=tk.LEFT, padx=5)
        self.tl_filter_cb_1 = ttk.Combobox(self.tl_top_bar_1, width=18, state="readonly")
        self.tl_filter_cb_1.pack(side=tk.LEFT, padx=2, pady=2)
        self.tl_filter_cb_1.bind("<<ComboboxSelected>>", lambda e: self.on_filter_cb_change("left"))
        
        self.tl_region_cb_1 = ttk.Combobox(self.tl_top_bar_1, width=15, state="readonly")
        # Do not pack region_cb initially
        self.tl_region_cb_1.bind("<<ComboboxSelected>>", lambda e: self.update_timeline_view("left"))
        
        # JSONに戻るボタン
        btn_tl_close_1 = tk.Label(self.tl_top_bar_1, text="JSONビューに戻る", bg="#444444", fg="white", font=("Meiryo", 8), cursor="hand2", padx=5)
        btn_tl_close_1.pack(side=tk.RIGHT, padx=5, pady=2)
        btn_tl_close_1.bind("<Button-1>", lambda e: self.hide_timeline_view("left"))
        btn_tl_close_1.bind("<Enter>", lambda e: btn_tl_close_1.config(bg="#555555"))
        btn_tl_close_1.bind("<Leave>", lambda e: btn_tl_close_1.config(bg="#444444"))

        self.tl_canvas_1 = TimelineCanvas(
            self.tl_frame_1, 
            on_report_click=lambda idx: self.jump_from_tl_to_json("left", idx),
            on_diff_click=lambda key: self.tl_diff_clicked("left", key)
        )
        self.tl_scroll_1 = ttk.Scrollbar(self.tl_frame_1, orient="vertical", command=self.tl_canvas_1.yview)
        self.tl_scroll_1.pack(side=tk.RIGHT, fill=tk.Y)
        self.tl_canvas_1.configure(yscrollcommand=self.tl_scroll_1.set)
        
        self.tl_scroll_x_1 = ttk.Scrollbar(self.tl_frame_1, orient="horizontal", command=self.tl_canvas_1.xview)
        self.tl_scroll_x_1.pack(side=tk.BOTTOM, fill=tk.X)
        self.tl_canvas_1.configure(xscrollcommand=self.tl_scroll_x_1.set)
        
        self.tl_canvas_1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_text_1.bind("<ButtonRelease-1>", lambda e: self.jump_from_json_to_tl(e, "left"))

        # Video controls for Left Panel (initially hidden)
        self.video_ctrl_1 = tk.Frame(self.log_canvas_1, bg="#1a1a1a", height=30)
        self.video_play_btn_1 = tk.Label(self.video_ctrl_1, text="⏸", bg="#1a1a1a", fg="white", font=("Meiryo", 9, "bold"), cursor="hand2", width=3)
        self.video_play_btn_1.pack(side=tk.LEFT, padx=5)
        self.video_slider_1 = tk.Scale(self.video_ctrl_1, from_=0, to=100, orient=tk.HORIZONTAL, bg="#1a1a1a", fg="white", highlightthickness=0, bd=0, showvalue=False)
        self.video_slider_1.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.video_time_lbl_1 = tk.Label(self.video_ctrl_1, text="0/0", bg="#1a1a1a", fg="#cccccc", font=("Meiryo", 8))
        self.video_time_lbl_1.pack(side=tk.RIGHT, padx=5)


        # Column 2 (Right Panel)
        self.log_pane_right = tk.Frame(self.log_paned_window, bg="#1e1e1e", highlightthickness=2, highlightbackground="#3c3f41")
        # Default is 1-panel, so right panel is NOT added initially.
        
        # Right Panel Header Frame
        self.log_pane_right_header = tk.Frame(self.log_pane_right, bg="#2b2b2b", height=20)
        self.log_pane_right_header.pack(side=tk.TOP, fill=tk.X)
        self.log_pane_right_header.pack_propagate(False)
        
        self.btn_close_2 = tk.Label(self.log_pane_right_header, text="×", bg="#2b2b2b", fg="#888888", font=("Meiryo", 9, "bold"), cursor="hand2", width=2)
        self.btn_close_2.pack(side=tk.RIGHT, padx=2)
        self.btn_close_2.bind("<Button-1>", lambda e: self.close_panel("right"))
        self.btn_close_2.bind("<Enter>", lambda e: self.btn_close_2.config(bg="#d9534f", fg="white") if self.log_pane_left_visible and self.log_pane_right_visible else None)
        self.btn_close_2.bind("<Leave>", lambda e: self.btn_close_2.config(bg=self.log_pane_right_header.cget("bg"), fg="#888888") if self.log_pane_left_visible and self.log_pane_right_visible else None)
        
        self.btn_swap_2 = tk.Label(self.log_pane_right_header, text="⇔", bg="#2b2b2b", fg="#888888", font=("Meiryo", 9, "bold"), cursor="hand2", width=2)
        self.btn_swap_2.pack(side=tk.RIGHT, padx=2)
        self.btn_swap_2.bind("<Button-1>", lambda e: self.swap_panels())
        self.btn_swap_2.bind("<Enter>", lambda e: self.btn_swap_2.config(bg="#555555", fg="white"))
        self.btn_swap_2.bind("<Leave>", lambda e: self.btn_swap_2.config(bg=self.log_pane_right_header.cget("bg"), fg="#888888"))

        self.btn_split_2 = tk.Label(self.log_pane_right_header, text="＋", bg="#2b2b2b", fg="#888888", font=("Meiryo", 9, "bold"), cursor="hand2", width=2)
        self.btn_split_2.bind("<Button-1>", lambda e: self.split_panel())
        self.btn_split_2.bind("<Enter>", lambda e: self.btn_split_2.config(bg="#555555", fg="white"))
        self.btn_split_2.bind("<Leave>", lambda e: self.btn_split_2.config(bg=self.log_pane_right_header.cget("bg"), fg="#888888"))

        self.btn_zoom_in_2 = tk.Label(self.log_pane_right_header, text="🔍+", bg="#2b2b2b", fg="#888888", font=("Meiryo", 9, "bold"), cursor="hand2", width=3)
        self.btn_zoom_in_2.bind("<Button-1>", lambda e: self.zoom_panel("right", 1.05))
        self.btn_zoom_in_2.bind("<Enter>", lambda e: self.btn_zoom_in_2.config(bg="#555555", fg="white"))
        self.btn_zoom_in_2.bind("<Leave>", lambda e: self.btn_zoom_in_2.config(bg=self.log_pane_right_header.cget("bg"), fg="#888888"))
        
        self.btn_zoom_out_2 = tk.Label(self.log_pane_right_header, text="🔍-", bg="#2b2b2b", fg="#888888", font=("Meiryo", 9, "bold"), cursor="hand2", width=3)
        self.btn_zoom_out_2.bind("<Button-1>", lambda e: self.zoom_panel("right", 0.95))
        self.btn_zoom_out_2.bind("<Enter>", lambda e: self.btn_zoom_out_2.config(bg="#555555", fg="white"))
        self.btn_zoom_out_2.bind("<Leave>", lambda e: self.btn_zoom_out_2.config(bg=self.log_pane_right_header.cget("bg"), fg="#888888"))
        
        # Right Panel Text & Canvas
        self.log_text_2 = tk.Text(self.log_pane_right, bg="#1e1e1e", fg="white", font=("Consolas", 10), wrap=tk.NONE, bd=0, highlightthickness=0)
        self.log_canvas_2 = tk.Canvas(self.log_pane_right, bg="#1e1e1e", bd=0, highlightthickness=0)
        
        self.scroll_y_2 = tk.Scrollbar(self.log_pane_right, orient="vertical", command=self.log_text_2.yview)
        self.scroll_y_2.pack(side=tk.RIGHT, fill=tk.Y)
        self.scroll_x_2 = tk.Scrollbar(self.log_pane_right, orient="horizontal", command=self.log_text_2.xview)
        self.scroll_x_2.pack(side=tk.BOTTOM, fill=tk.X)
        self.log_text_2.configure(yscrollcommand=self.scroll_y_2.set, xscrollcommand=self.scroll_x_2.set)
        self.log_text_2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Timeline Frame for Right Panel (initially hidden)
        self.tl_frame_2 = tk.Frame(self.log_pane_right, bg="#1e1e1e")
        self.tl_top_bar_2 = tk.Frame(self.tl_frame_2, bg="#2b2b2b", height=30)
        self.tl_top_bar_2.pack(side=tk.TOP, fill=tk.X)
        self.tl_top_bar_2.pack_propagate(False)
        tk.Label(self.tl_top_bar_2, text="フィルタ(項目名):", bg="#2b2b2b", fg="#cccccc", font=("Meiryo", 8)).pack(side=tk.LEFT, padx=5)
        self.tl_filter_cb_2 = ttk.Combobox(self.tl_top_bar_2, width=18, state="readonly")
        self.tl_filter_cb_2.pack(side=tk.LEFT, padx=2, pady=2)
        self.tl_filter_cb_2.bind("<<ComboboxSelected>>", lambda e: self.on_filter_cb_change("right"))
        
        self.tl_region_cb_2 = ttk.Combobox(self.tl_top_bar_2, width=15, state="readonly")
        self.tl_region_cb_2.bind("<<ComboboxSelected>>", lambda e: self.update_timeline_view("right"))
        
        btn_tl_close_2 = tk.Label(self.tl_top_bar_2, text="JSONビューに戻る", bg="#444444", fg="white", font=("Meiryo", 8), cursor="hand2", padx=5)
        btn_tl_close_2.pack(side=tk.RIGHT, padx=5, pady=2)
        btn_tl_close_2.bind("<Button-1>", lambda e: self.hide_timeline_view("right"))
        btn_tl_close_2.bind("<Enter>", lambda e: btn_tl_close_2.config(bg="#555555"))
        btn_tl_close_2.bind("<Leave>", lambda e: btn_tl_close_2.config(bg="#444444"))

        self.tl_canvas_2 = TimelineCanvas(
            self.tl_frame_2, 
            on_report_click=lambda idx: self.jump_from_tl_to_json("right", idx),
            on_diff_click=lambda key: self.tl_diff_clicked("right", key)
        )
        self.tl_scroll_2 = ttk.Scrollbar(self.tl_frame_2, orient="vertical", command=self.tl_canvas_2.yview)
        self.tl_scroll_2.pack(side=tk.RIGHT, fill=tk.Y)
        self.tl_canvas_2.configure(yscrollcommand=self.tl_scroll_2.set)
        
        self.tl_scroll_x_2 = ttk.Scrollbar(self.tl_frame_2, orient="horizontal", command=self.tl_canvas_2.xview)
        self.tl_scroll_x_2.pack(side=tk.BOTTOM, fill=tk.X)
        self.tl_canvas_2.configure(xscrollcommand=self.tl_scroll_x_2.set)
        
        self.tl_canvas_2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Video controls for Right Panel (initially hidden)
        self.video_ctrl_2 = tk.Frame(self.log_canvas_2, bg="#1a1a1a", height=30)
        self.video_play_btn_2 = tk.Label(self.video_ctrl_2, text="⏸", bg="#1a1a1a", fg="white", font=("Meiryo", 9, "bold"), cursor="hand2", width=3)
        self.video_play_btn_2.pack(side=tk.LEFT, padx=5)
        self.video_slider_2 = tk.Scale(self.video_ctrl_2, from_=0, to=100, orient=tk.HORIZONTAL, bg="#1a1a1a", fg="white", highlightthickness=0, bd=0, showvalue=False)
        self.video_slider_2.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.video_time_lbl_2 = tk.Label(self.video_ctrl_2, text="0/0", bg="#1a1a1a", fg="#cccccc", font=("Meiryo", 8))
        self.video_time_lbl_2.pack(side=tk.RIGHT, padx=5)

        self.log_text = self.log_text_1
        self.log_viewer_text = self.log_text_1
        
        # Panning & Playback states
        self.video_playing = {"left": True, "right": True}
        self.pan_start_x = 0
        self.pan_start_y = 0
        
        # Bind focus/click activation events
        for w in [self.log_pane_left, self.log_pane_left_header, self.log_text_1]:
            w.bind("<Button-1>", lambda e: self.activate_panel("left"))
        self.log_text_1.bind("<FocusIn>", lambda e: self.activate_panel("left"))
        self.log_text_1.bind("<Control-MouseWheel>", lambda e: self.on_mousewheel_zoom(e, "left"))
            
        for w in [self.log_pane_right, self.log_pane_right_header, self.log_text_2]:
            w.bind("<Button-1>", lambda e: self.activate_panel("right"))
        self.log_text_2.bind("<FocusIn>", lambda e: self.activate_panel("right"))
        self.log_text_2.bind("<Control-MouseWheel>", lambda e: self.on_mousewheel_zoom(e, "right"))

        # Initialize highlight and button visibility states
        self.update_log_panel_headers()

        # 4. Maps (Full & Cropped)
        settings = self.load_settings()
        nied_settings = settings.get("nied_monitor", {})
        
        self.full_frame = tk.Frame(self.viewport, bg="black", highlightbackground="#1e3d59", highlightthickness=2)
        self.crop_frame = tk.Frame(self.viewport, bg="black", highlightbackground="#ff6e40", highlightthickness=2)
        
        self.viz_full = NiedMapVisualizer(self.full_frame, nied_settings, mode="full")
        self.viz_crop = NiedMapVisualizer(self.crop_frame, nied_settings, mode="cropped")

        # Initialize close buttons and bind drag/click handlers
        self.setup_close_buttons()
        self.setup_drag_and_click(self.full_frame, "full", self.viz_full)
        self.setup_drag_and_click(self.crop_frame, "crop", self.viz_crop)

        # --- EqSystem 初期化 (バックグラウンドで監視開始) ---
        self.eq_system = EqSystem(visualizers=[self.viz_full, self.viz_crop], saves_history=True)
        self.eq_system.start()
        
        # Monitor用の地中切り替え関数をNiedMonitorに登録
        if hasattr(self, 'rt_borehole_var') and hasattr(self.eq_system, 'nied') and self.eq_system.nied:
            self.eq_system.nied.is_borehole_func = self.rt_borehole_var.get
            
        self._last_interrupt_event_id = ""
        self.eq_system.on_alert_start_callbacks.append(self._on_real_alert_start)
        # アラート期間(NIED画像解析ベース、動画保存と同じ期間)の開始/終了で
        # 地震発生中表示(カウントアップ)と地震カード表示を切り替える
        if hasattr(self.eq_system, 'nied') and self.eq_system.nied:
            self.eq_system.nied.on_alert_start_callbacks.append(self._on_nied_alert_start)
            self.eq_system.nied.on_alert_end_callbacks.append(self._on_alert_period_end)
        self.sim_thread = None
        
        # --- シミュレーション制御フラグ ---
        self.sim_paused = False
        self.sim_running = False
        self.sim_stop_flag = False
        self.sim_session_id = 0  # セッションIDで on_finish の二重実行を防ぐ
        self.current_event_groups = []  # ロード済みイベントグループを保持

        self.log_sort_col = "date"
        self.log_sort_desc = True
        self.sim_sort_col = "date"
        self.sim_sort_desc = True

        self.update_wolfx_status() # Start connection status polling
        self.update_nied_status()
        self.refresh_log_viewer()
        self.right_frame.bind("<Configure>", lambda e: self.adjust_home_layout())
        self.apply_layout()

        # ウィンドウを閉じる際の処理を登録
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Start IPC socket server for single-instance check and toggle controls
        self.start_ipc_server()
        
        # UI全体の構築が完了した後にバインドする
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

    def setup_close_buttons(self):
        # Create close buttons on the frames
        self.full_close_btn = tk.Label(
            self.full_frame,
            text="×",
            bg="#222222",
            fg="#888888",
            font=("Meiryo", 8, "bold"),
            cursor="hand2",
            padx=4,
            pady=0,
            bd=0,
            highlightthickness=0
        )
        self.full_close_btn.bind("<Button-1>", lambda e: self.hide_pip("full"))
        self.full_close_btn.bind("<Enter>", lambda e: self.full_close_btn.config(bg="#ff4d4d", fg="white"))
        self.full_close_btn.bind("<Leave>", lambda e: self.full_close_btn.config(bg="#222222", fg="#888888"))

        self.crop_close_btn = tk.Label(
            self.crop_frame,
            text="×",
            bg="#222222",
            fg="#888888",
            font=("Meiryo", 8, "bold"),
            cursor="hand2",
            padx=4,
            pady=0,
            bd=0,
            highlightthickness=0
        )
        self.crop_close_btn.bind("<Button-1>", lambda e: self.hide_pip("crop"))
        self.crop_close_btn.bind("<Enter>", lambda e: self.crop_close_btn.config(bg="#ff4d4d", fg="white"))
        self.crop_close_btn.bind("<Leave>", lambda e: self.crop_close_btn.config(bg="#222222", fg="#888888"))

    def show_close_btn(self, name):
        btn = self.full_close_btn if name == "full" else self.crop_close_btn
        btn.place(relx=1.0, rely=0.0, anchor=tk.NE, x=-2, y=2)
        btn.lift()

    def hide_close_btn(self, name):
        btn = self.full_close_btn if name == "full" else self.crop_close_btn
        btn.place_forget()

    def setup_drag_and_click(self, frame, name, viz):
        widgets = [
            frame, 
            viz.container, 
            viz.status_frame, 
            viz.visual_frame, 
            viz.map_frame, 
            viz.canvas, 
            viz.status_label, 
            viz.indicator_canvas
        ]
        for w in widgets:
            w.bind("<Button-1>", lambda e, n=name: self.on_drag_start(e, n))
            w.bind("<B1-Motion>", lambda e, n=name: self.on_drag_motion(e, n))
            w.bind("<ButtonRelease-1>", lambda e, n=name: self.on_drag_release(e, n))

    def is_pip(self, name):
        if name == "full":
            return self.state in ["focus_crop", "simulation", "config", "logviewer"]
        elif name == "crop":
            return self.state in ["focus_full", "simulation", "config", "logviewer"]
        return False

    def hide_pip(self, name):
        if name == "full":
            self.pip_visible_full = False
        else:
            self.pip_visible_crop = False
        self.apply_layout()
        return "break"

    def place_pip(self, name):
        frame = self.full_frame if name == "full" else self.crop_frame
        is_visible = self.pip_visible_full if name == "full" else self.pip_visible_crop
        
        if not is_visible:
            frame.place_forget()
            self.hide_close_btn(name)
            return

        pip_w = 0.15
        pip_h = 0.26
        
        if name == "full":
            relx = self.pip_relx_full if self.pip_relx_full is not None else 0.82
            rely = self.pip_rely_full if self.pip_rely_full is not None else 0.70
        else:
            relx = self.pip_relx_crop if self.pip_relx_crop is not None else 0.66
            rely = self.pip_rely_crop if self.pip_rely_crop is not None else 0.70

        frame.place(relx=relx, rely=rely, relwidth=pip_w, relheight=pip_h)
        frame.lift()
        if self.state in ["focus_full", "focus_crop"]:
            self.hide_close_btn(name)
        else:
            self.show_close_btn(name)

    def on_drag_start(self, event, name):
        if not self.is_pip(name):
            return
        self.drag_start_x = event.x_root
        self.drag_start_y = event.y_root
        
        frame = self.full_frame if name == "full" else self.crop_frame
        place_info = frame.place_info()
        
        try:
            self.drag_start_relx = float(place_info.get('relx', 0.82 if name == "full" else 0.66))
            self.drag_start_rely = float(place_info.get('rely', 0.70))
        except (ValueError, TypeError):
            self.drag_start_relx = 0.82 if name == "full" else 0.66
            self.drag_start_rely = 0.70
            
        self.dragged = False
        
        try:
            with open(self.debug_log_path, "a", encoding="utf-8") as f:
                f.write(f"START: name={name}, x_root={event.x_root}, y_root={event.y_root}, relx={self.drag_start_relx:.4f}, rely={self.drag_start_rely:.4f}\n")
        except:
            pass

    def on_drag_motion(self, event, name):
        if not self.is_pip(name):
            return
        dx = event.x_root - self.drag_start_x
        dy = event.y_root - self.drag_start_y

        if abs(dx) > 3 or abs(dy) > 3:
            self.dragged = True

        if self.dragged:
            frame = self.full_frame if name == "full" else self.crop_frame
            parent_w = self.right_frame.winfo_width()
            parent_h = self.right_frame.winfo_height()
            
            if parent_w < 100:
                parent_w = 800
            if parent_h < 100:
                parent_h = 400

            d_relx = dx / parent_w
            d_rely = dy / parent_h

            new_relx = self.drag_start_relx + d_relx
            new_rely = self.drag_start_rely + d_rely

            pip_w = 0.15
            pip_h = 0.26

            # Clamp to screen edges first
            new_relx = max(0.0, min(new_relx, 1.0 - pip_w))
            new_rely = max(0.0, min(new_rely, 1.0 - pip_h))

            # PiP overlap prevention using 2D AABB:
            # Only push apart if both X and Y ranges overlap simultaneously.
            other_name = "crop" if name == "full" else "full"
            other_visible = self.pip_visible_crop if name == "full" else self.pip_visible_full
            if other_visible and self.is_pip(other_name):
                other_relx = (self.pip_relx_crop if name == "full" else self.pip_relx_full)
                other_rely = (self.pip_rely_crop if name == "full" else self.pip_rely_full)
                if other_relx is None:
                    other_relx = 0.66 if name == "full" else 0.82
                if other_rely is None:
                    other_rely = 0.70

                # Compute overlap on each axis (positive = overlapping)
                ox = min(new_relx + pip_w, other_relx + pip_w) - max(new_relx, other_relx)
                oy = min(new_rely + pip_h, other_rely + pip_h) - max(new_rely, other_rely)

                if ox > 0 and oy > 0:
                    # Boxes actually overlap. Resolve along the axis with smaller penetration.
                    if ox <= oy:
                        # Push apart horizontally
                        pos_left = other_relx - pip_w
                        pos_right = other_relx + pip_w
                        
                        # Validate boundaries
                        left_valid = (0.0 <= pos_left <= 1.0 - pip_w)
                        right_valid = (0.0 <= pos_right <= 1.0 - pip_w)
                        
                        if left_valid and not right_valid:
                            new_relx = pos_left
                        elif right_valid and not left_valid:
                            new_relx = pos_right
                        else:
                            # Both valid (or neither, fallback to closest)
                            if abs(new_relx - pos_left) < abs(new_relx - pos_right):
                                new_relx = pos_left
                            else:
                                new_relx = pos_right
                    else:
                        # Push apart vertically
                        pos_top = other_rely - pip_h
                        pos_bottom = other_rely + pip_h
                        
                        # Validate boundaries
                        top_valid = (0.0 <= pos_top <= 1.0 - pip_h)
                        bottom_valid = (0.0 <= pos_bottom <= 1.0 - pip_h)
                        
                        if top_valid and not bottom_valid:
                            new_rely = pos_top
                        elif bottom_valid and not top_valid:
                            new_rely = pos_bottom
                        else:
                            # Both valid (or neither, fallback to closest)
                            if abs(new_rely - pos_top) < abs(new_rely - pos_bottom):
                                new_rely = pos_top
                            else:
                                new_rely = pos_bottom

                    # Re-clamp to screen edges after push
                    new_relx = max(0.0, min(new_relx, 1.0 - pip_w))
                    new_rely = max(0.0, min(new_rely, 1.0 - pip_h))

            if name == "full":
                self.pip_relx_full = new_relx
                self.pip_rely_full = new_rely
            else:
                self.pip_relx_crop = new_relx
                self.pip_rely_crop = new_rely

            frame.place(relx=new_relx, rely=new_rely, relwidth=pip_w, relheight=pip_h)
            frame.lift()

    def on_drag_release(self, event, name):
        if not self.is_pip(name):
            self.on_map_click(name)
            return

        if not self.dragged:
            # Click occurred without a drag
            # Only allow map click behavior (maximizing/toggling) on the monitor tab (tab index == 0)
            if self.notebook.index(self.notebook.select()) == 0:
                self.on_map_click(name)

    def setup_tabs(self):
        settings = self.load_settings()
        nied_settings = settings.get("nied_monitor", {})

        # 1. monitor
        self.tab_rt = ttk.Frame(self.notebook, style="Panel.TFrame")
        self.notebook.add(self.tab_rt, text="monitor")

        # UI Control Area (Toggles & Settings)
        pip_toggle_frame = ttk.Frame(self.tab_rt, style="Panel.TFrame")
        pip_toggle_frame.pack(fill=tk.X, padx=10, pady=(8, 4))

        # Row 0: Borehole toggle (Monitor)
        borehole_toggle_frame = ttk.Frame(pip_toggle_frame, style="Panel.TFrame")
        borehole_toggle_frame.pack(fill=tk.X, pady=(0, 3))
        ttk.Label(borehole_toggle_frame, text="モニタ画像:", style="Panel.TLabel").pack(side=tk.LEFT)

        self.rt_borehole_var = tk.BooleanVar(value=False)
        self.rt_borehole_switch = SlideSwitch(borehole_toggle_frame, self.rt_borehole_var, width=60, height=22)
        self.rt_borehole_switch.pack(side=tk.LEFT, padx=(4, 2))

        # Row 1: Toggles (PiP)
        row1_frame = ttk.Frame(pip_toggle_frame, style="Panel.TFrame")
        row1_frame.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(row1_frame, text="他画面PiP:", style="Panel.TLabel").pack(side=tk.LEFT)

        # Full map toggle
        self.pip_toggle_full_var = tk.BooleanVar(value=True)
        self.pip_toggle_full_btn = tk.Label(
            row1_frame, text="Full ✓",
            bg="#2a6496", fg="white",
            font=("Meiryo", 8, "bold"), cursor="hand2",
            padx=6, pady=2, relief="flat"
        )
        self.pip_toggle_full_btn.pack(side=tk.LEFT, padx=(4, 2))
        self.pip_toggle_full_btn.bind("<Button-1>", lambda e: self._toggle_pip_pref("full"))

        # Crop map toggle
        self.pip_toggle_crop_var = tk.BooleanVar(value=True)
        self.pip_toggle_crop_btn = tk.Label(
            row1_frame, text="Crop ✓",
            bg="#2a6496", fg="white",
            font=("Meiryo", 8, "bold"), cursor="hand2",
            padx=6, pady=2, relief="flat"
        )
        self.pip_toggle_crop_btn.pack(side=tk.LEFT, padx=2)
        self.pip_toggle_crop_btn.bind("<Button-1>", lambda e: self._toggle_pip_pref("crop"))

        # Row 2: アプリ制御（再起動 / 終了）
        appctl_frame = ttk.Frame(pip_toggle_frame, style="Panel.TFrame")
        appctl_frame.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(appctl_frame, text="アプリ制御:", style="Panel.TLabel").pack(side=tk.LEFT)
        self.app_restart_btn = self._create_flat_button(
            appctl_frame, "⟳ 再起動", self._confirm_restart_app,
            bg="#b9770e", hover_bg="#d97706"
        )
        self.app_restart_btn.pack(side=tk.LEFT, padx=(4, 2))
        self.app_quit_btn = self._create_flat_button(
            appctl_frame, "⏻ 終了", self._confirm_quit_app,
            bg="#a5352b", hover_bg="#c0392b"
        )
        self.app_quit_btn.pack(side=tk.LEFT, padx=2)

        # --- Monitor Terminal Log ---
        ttk.Separator(self.tab_rt, orient="horizontal").pack(fill=tk.X, padx=10, pady=(6, 3))
        
        log_label_frame = tk.Frame(self.tab_rt, bg="#3c3f41")
        log_label_frame.pack(fill=tk.X, padx=10, pady=(0, 2))
        ttk.Label(log_label_frame, text="システムログ:", style="Panel.TLabel", font=("Meiryo", 9)).pack(side=tk.LEFT)

        clear_log_btn = tk.Label(
            log_label_frame, text="クリア",
            bg="#c0392b", fg="white", font=("Meiryo", 8), cursor="hand2", padx=6, pady=2, relief="flat"
        )
        clear_log_btn.pack(side=tk.RIGHT, padx=(2, 0))
        clear_log_btn.bind("<Button-1>", lambda e: self.clear_monitor_log())
        clear_log_btn.bind("<Enter>", lambda e: clear_log_btn.config(bg="#e74c3c"))
        clear_log_btn.bind("<Leave>", lambda e: clear_log_btn.config(bg="#c0392b"))

        test_log_btn = tk.Label(
            log_label_frame, text="表示テスト",
            bg="#2a6496", fg="white", font=("Meiryo", 8), cursor="hand2", padx=6, pady=2, relief="flat"
        )
        test_log_btn.pack(side=tk.RIGHT, padx=2)
        test_log_btn.bind("<Button-1>", lambda e: self.insert_test_log())
        test_log_btn.bind("<Enter>", lambda e: test_log_btn.config(bg="#357ebd"))
        test_log_btn.bind("<Leave>", lambda e: test_log_btn.config(bg="#2a6496"))

        monitor_log_frame = tk.Frame(self.tab_rt, bg="#1e1e1e")
        monitor_log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        self.monitor_log_text = tk.Text(
            monitor_log_frame, bg="#1e1e1e", fg="#cccccc",
            font=("Consolas", 8), wrap=tk.WORD,
            bd=0, highlightthickness=0, height=8, state=tk.DISABLED
        )
        monitor_log_scroll = tk.Scrollbar(monitor_log_frame, orient="vertical", command=self.monitor_log_text.yview)
        monitor_log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.monitor_log_text.config(yscrollcommand=monitor_log_scroll.set)
        self.monitor_log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tag colours for log components and levels
        self.monitor_log_text.tag_configure("TIME", foreground="#777777")
        self.monitor_log_text.tag_configure("MODULE", foreground="#5bc0de")
        self.monitor_log_text.tag_configure("MSG", foreground="#dddddd")
        self.monitor_log_text.tag_configure("HIGHLIGHT", foreground="#ffb6c1", font=("Consolas", 8, "bold"))
        self.monitor_log_text.tag_configure("ERROR", foreground="#ff6b6b", font=("Consolas", 8, "bold"))
        self.monitor_log_text.tag_configure("WARNING", foreground="#ffc107", font=("Consolas", 8, "bold"))
        self.monitor_log_text.tag_configure("INFO", foreground="#8ec07c")
        self.monitor_log_text.tag_configure("DEBUG", foreground="#666666")

        # 地震カードスタック (システムログ上端にオーバーレイ表示)
        self.monitor_card_stack = MonitorLogCardStack(
            monitor_log_frame, self.monitor_log_text,
            on_confirm=self._on_monitor_card_confirm
        )

        # Queue + handler for thread-safe logging display
        self._log_queue = queue.Queue()
        self._setup_gui_log_handler()
        self.root.after(250, self._poll_log_queue)

        # 2. log
        self.tab_log = ttk.Frame(self.notebook, style="Panel.TFrame")
        self.notebook.add(self.tab_log, text="log")
        
        # --- Log ツールバー ---
        log_toolbar = ttk.Frame(self.tab_log, style="Panel.TFrame")
        log_toolbar.pack(fill=tk.X, padx=5, pady=(5, 2))
        
        self.btn_refresh_log = self._create_flat_button(log_toolbar, "リスト更新", self.refresh_log_viewer)
        self.btn_refresh_log.pack(side=tk.LEFT, padx=2)
        
        self.btn_open_log_dir = self._create_flat_button(log_toolbar, "外部ビューア", self.open_log_dir)
        self.btn_open_log_dir.pack(side=tk.LEFT, padx=2)

        # --- Log 検索・フィルタ (Row 1) ---
        log_filter_row1 = ttk.Frame(self.tab_log, style="Panel.TFrame")
        log_filter_row1.pack(fill=tk.X, padx=5, pady=(2, 1))
        
        ttk.Label(log_filter_row1, text="絞込:", style="Panel.TLabel").pack(side=tk.LEFT, padx=(2, 1))
        self.log_filter_source_cb = ttk.Combobox(log_filter_row1, values=["すべて", "EEWあり", "NIED地表のみ", "NIED地中あり"], width=13, state="readonly")
        self.log_filter_source_cb.set("すべて")
        self.log_filter_source_cb.pack(side=tk.LEFT, padx=1)
        self.log_filter_source_cb.bind("<<ComboboxSelected>>", lambda e: self.update_log_list())

        ttk.Label(log_filter_row1, text="震源:", style="Panel.TLabel").pack(side=tk.LEFT, padx=(8, 1))
        self.log_filter_hypo_entry = ttk.Entry(log_filter_row1, width=7)
        self.log_filter_hypo_entry.pack(side=tk.LEFT, padx=1)
        self.log_filter_hypo_entry.bind("<KeyRelease>", lambda e: self.update_log_list())

        # --- Log イベントリスト ---
        log_list_title_frame = ttk.Frame(self.tab_log, style="Panel.TFrame")
        log_list_title_frame.pack(fill=tk.X, padx=5, pady=(5, 2))

        ttk.Label(log_list_title_frame, text="地震イベント一覧:", style="Panel.TLabel").pack(side=tk.LEFT)

        self.btn_delete_log = self._create_flat_button(
            log_list_title_frame, "選択削除", self._delete_selected_log_event,
            bg="#8b1a1a", hover_bg="#a02020"
        )
        self.btn_delete_log.pack(side=tk.RIGHT)

        # 見出し行フレーム
        self.log_list_header_frame = tk.Frame(self.tab_log, bg="#2b2b2b")
        self.log_list_header_frame.pack(fill=tk.X, padx=5, pady=(2, 0))
        
        list_frame = ttk.Frame(self.tab_log, style="Panel.TFrame")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.log_event_listbox = tk.Listbox(
            list_frame, height=4, bg="#1e1e1e", fg="white", selectbackground="#2a6496",
            bd=0, highlightthickness=0, font=("MS Gothic", 9),
            yscrollcommand=scrollbar.set
        )
        self.log_event_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_event_listbox.bind("<<ListboxSelect>>", self.on_log_event_select)
        scrollbar.config(command=self.log_event_listbox.yview)
        
        # --- Log タイプ選択ボタン (セグメンテッド・コントロール風フラットトグル) ---
        type_frame = ttk.Frame(self.tab_log, style="Panel.TFrame")
        type_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(type_frame, text="データ種別:", style="Panel.TLabel").pack(side=tk.LEFT, padx=5)
        
        self.log_type_var = tk.StringVar(value="eew")
        
        self.btn_type_eew = self._create_flat_button(
            type_frame, "EEW (JSON)", lambda: self._select_log_type("eew"),
            bg="#2a6496", hover_bg="#357ebd", font=("Meiryo", 8, "bold"), padx=8, pady=2
        )
        self.btn_type_eew.pack(side=tk.LEFT, padx=2)
        
        self.btn_type_monitor = self._create_flat_button(
            type_frame, "Monitor (画像)", lambda: self._select_log_type("monitor"),
            bg="#555555", hover_bg="#666666", font=("Meiryo", 8, "bold"), padx=8, pady=2
        )
        self.btn_type_monitor.pack(side=tk.LEFT, padx=2)
        
        # --- Log ファイルリスト ---
        file_header_frame = ttk.Frame(self.tab_log, style="Panel.TFrame")
        file_header_frame.pack(fill=tk.X, padx=5, pady=(5, 2))
        ttk.Label(file_header_frame, text="ファイル一覧:", style="Panel.TLabel").pack(side=tk.LEFT)
        
        self.log_borehole_var = tk.BooleanVar(value=False)
        self.btn_toggle_borehole_log = SlideSwitch(
            file_header_frame, self.log_borehole_var, command=self._on_toggle_borehole_log,
            width=60, height=22, bg="#3c3f41"
        )
        self.btn_toggle_borehole_log.pack_forget()
        
        self.btn_create_video = self._create_flat_button(
            file_header_frame, "動画作成", self._on_create_video_click,
            bg="#8b4513", hover_bg="#a0522d", font=("Meiryo", 8, "bold"), padx=8, pady=2
        )
        self.btn_create_video.pack_forget()

        self.btn_show_tl = self._create_flat_button(
            file_header_frame, "TL表示", self._on_show_tl_click,
            bg="#2d7d6a", hover_bg="#389e87", font=("Meiryo", 8, "bold"), padx=8, pady=2
        )
        # 起動時はEEWなので表示する
        self.btn_show_tl.pack(side=tk.RIGHT, padx=5)
        
        file_list_frame = ttk.Frame(self.tab_log, style="Panel.TFrame")
        file_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        
        file_scrollbar = ttk.Scrollbar(file_list_frame)
        file_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.log_file_listbox = tk.Listbox(
            file_list_frame, height=6, bg="#1e1e1e", fg="white", selectbackground="#2a6496",
            bd=0, highlightthickness=0, font=("Meiryo", 9),
            yscrollcommand=file_scrollbar.set
        )
        self.log_file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.log_file_listbox.bind("<<ListboxSelect>>", self.on_log_file_select)
        file_scrollbar.config(command=self.log_file_listbox.yview)

        # 3. sim
        self.tab_sim = ttk.Frame(self.notebook, style="Panel.TFrame")
        self.notebook.add(self.tab_sim, text="sim")
        
        # --- Sim イベントリスト ---
        ttk.Label(self.tab_sim, text="[ シミュレーション対象 ]", style="Panel.TLabel").pack(anchor=tk.W, pady=(5, 2), padx=5)
        
        # --- Sim 検索・フィルタ (Row 1) ---
        sim_filter_row1 = ttk.Frame(self.tab_sim, style="Panel.TFrame")
        sim_filter_row1.pack(fill=tk.X, padx=5, pady=(2, 1))
        
        ttk.Label(sim_filter_row1, text="絞込:", style="Panel.TLabel").pack(side=tk.LEFT, padx=(2, 1))
        self.sim_filter_source_cb = ttk.Combobox(sim_filter_row1, values=["すべて", "EEWあり", "NIED地表のみ", "NIED地中あり"], width=13, state="readonly")
        self.sim_filter_source_cb.set("すべて")
        self.sim_filter_source_cb.pack(side=tk.LEFT, padx=1)
        self.sim_filter_source_cb.bind("<<ComboboxSelected>>", lambda e: self.update_sim_list())

        ttk.Label(sim_filter_row1, text="震源:", style="Panel.TLabel").pack(side=tk.LEFT, padx=(8, 1))
        self.sim_filter_hypo_entry = ttk.Entry(sim_filter_row1, width=7)
        self.sim_filter_hypo_entry.pack(side=tk.LEFT, padx=1)
        self.sim_filter_hypo_entry.bind("<KeyRelease>", lambda e: self.update_sim_list())

        # 見出し行フレーム
        self.sim_list_header_frame = tk.Frame(self.tab_sim, bg="#2b2b2b")
        self.sim_list_header_frame.pack(fill=tk.X, padx=5, pady=(2, 0))
        
        sim_list_frame = ttk.Frame(self.tab_sim, style="Panel.TFrame")
        sim_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)
        
        sim_scrollbar = ttk.Scrollbar(sim_list_frame)
        sim_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.sim_event_listbox = tk.Listbox(
            sim_list_frame, height=8, bg="#1e1e1e", fg="white", selectbackground="#2a6496",
            bd=0, highlightthickness=0, font=("MS Gothic", 9),
            yscrollcommand=sim_scrollbar.set
        )
        self.sim_event_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.sim_event_listbox.bind("<<ListboxSelect>>", self._on_sim_list_select)
        sim_scrollbar.config(command=self.sim_event_listbox.yview)
        
        # --- Sim オプション行1: 速度 + ミュートボタン ---
        opt_row1 = ttk.Frame(self.tab_sim, style="Panel.TFrame")
        opt_row1.pack(fill=tk.X, padx=5, pady=(5, 2))
        ttk.Label(opt_row1, text="速度:", style="Panel.TLabel").pack(side=tk.LEFT)
        self.sim_speed_var = tk.StringVar(value="1.0")
        ttk.Entry(opt_row1, textvariable=self.sim_speed_var, width=4).pack(side=tk.LEFT, padx=(3, 8))

        self.sim_mute_var = tk.BooleanVar(value=False)
        self.sim_mute_btn = self._create_flat_button(
            opt_row1, "音声ON", self._toggle_sim_mute,
            bg="#27ae60", hover_bg="#219a52", font=("Meiryo", 9), padx=6, pady=2
        )
        self.sim_mute_btn.pack(side=tk.LEFT)

        # --- Sim オプション行2: 座標指定 ---
        opt_row2 = ttk.Frame(self.tab_sim, style="Panel.TFrame")
        opt_row2.pack(fill=tk.X, padx=5, pady=(2, 5))
        ttk.Label(opt_row2, text="エリア補正:", style="Panel.TLabel", font=("Meiryo", 10)).pack(side=tk.LEFT)
        ttk.Label(opt_row2, text="緯度", style="Panel.TLabel", font=("Meiryo", 10)).pack(side=tk.LEFT, padx=(4, 1))
        from eq_config.eq_config import HOME_LAT as _HOME_LAT, HOME_LON as _HOME_LON
        self.sim_lat_offset_var = tk.StringVar(value=str(_HOME_LAT))
        ttk.Entry(opt_row2, textvariable=self.sim_lat_offset_var, width=6).pack(side=tk.LEFT, padx=1)
        ttk.Label(opt_row2, text="経度", style="Panel.TLabel", font=("Meiryo", 10)).pack(side=tk.LEFT, padx=(4, 1))
        self.sim_lon_offset_var = tk.StringVar(value=str(_HOME_LON))
        ttk.Entry(opt_row2, textvariable=self.sim_lon_offset_var, width=7).pack(side=tk.LEFT, padx=1)

        # --- Sim 制御ボタン ---
        ctrl_frame = ttk.Frame(self.tab_sim, style="Panel.TFrame")
        ctrl_frame.pack(fill=tk.X, padx=5, pady=5)

        self.sim_playpause_btn = self._create_flat_button(
            ctrl_frame, "▶ 再生", self._on_sim_playpause_click,
            bg="#2a6496", hover_bg="#357ebd", font=("Meiryo", 9), padx=8, pady=3
        )
        self.sim_playpause_btn.pack(side=tk.LEFT, padx=2)

        self.sim_stop_btn = self._create_flat_button(
            ctrl_frame, "中断", self.stop_simulation,
            bg="#d9534f", hover_bg="#c9302c", font=("Meiryo", 9), padx=8, pady=3
        )
        self.sim_stop_btn.pack(side=tk.LEFT, padx=2)
        self._set_flat_button_state(self.sim_stop_btn, tk.DISABLED)

        # --- Sim 状態インジケータ ---
        self.sim_status_label = tk.Label(ctrl_frame, text="●", bg="#3c3f41", fg="#00ff00", font=("Meiryo", 12))
        self.sim_status_label.pack(side=tk.LEFT, padx=8)

        # 4. config
        self.tab_config = ttk.Frame(self.notebook, style="Panel.TFrame")
        self.notebook.add(self.tab_config, text="config")
        
        # --- スクロール可能なCanvasの構築 ---
        # ダークテーマで視認できる縦スクロールバーのスタイル
        _sb_style = ttk.Style()
        _sb_style.configure("Config.Vertical.TScrollbar",
                            troughcolor="#2b2b2b", bordercolor="#2b2b2b",
                            background="#6a6a6a", arrowcolor="#d0d0d0",
                            gripcount=0, width=16)
        _sb_style.map("Config.Vertical.TScrollbar",
                      background=[("active", "#9a9a9a"), ("pressed", "#b0b0b0")])
        self.config_canvas = tk.Canvas(self.tab_config, bg="#1e1e1e", highlightthickness=0)
        self.config_scrollbar = ttk.Scrollbar(self.tab_config, orient="vertical",
                                               style="Config.Vertical.TScrollbar",
                                               command=self.config_canvas.yview)
        
        self.config_inner_frame = ttk.Frame(self.config_canvas, style="Panel.TFrame")
        self.config_inner_frame.bind(
            "<Configure>",
            lambda e: self.config_canvas.configure(
                scrollregion=self.config_canvas.bbox("all")
            )
        )
        
        # マウスホイールスクロールの共通ハンドラ
        def _on_mousewheel(event):
            if hasattr(event, 'num') and event.num == 4:
                self.config_canvas.yview_scroll(-1, "units")
            elif hasattr(event, 'num') and event.num == 5:
                self.config_canvas.yview_scroll(1, "units")
            elif hasattr(event, 'delta') and event.delta != 0:
                self.config_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            
        self._config_win_id = self.config_canvas.create_window((0, 0), window=self.config_inner_frame, anchor="nw", width=self.tab_config.winfo_width())
        self.config_canvas.configure(yscrollcommand=self.config_scrollbar.set)

        # Canvasの幅追従
        self.config_canvas.bind("<Configure>", lambda e: self.config_canvas.itemconfig(self._config_win_id, width=e.width))
        self.config_canvas.bind("<MouseWheel>", _on_mousewheel)
        self.config_canvas.bind("<Button-4>", _on_mousewheel)
        self.config_canvas.bind("<Button-5>", _on_mousewheel)
        self.config_inner_frame.bind("<MouseWheel>", _on_mousewheel)
        self.config_inner_frame.bind("<Button-4>", _on_mousewheel)
        self.config_inner_frame.bind("<Button-5>", _on_mousewheel)
        
        # スクロールバーを先にpackして領域を確保 → Canvasが残り幅に追従
        self.config_scrollbar.pack(side="right", fill="y")
        self.config_canvas.pack(side="left", fill="both", expand=True)

        self.config_entries = {}
        settings = self.load_settings()
        nied = settings.get("nied_monitor", {})
        
        self._add_config_entry(self.config_inner_frame, "監視半径(px)", "radius_pixel", nied.get("radius_pixel", 50), _on_mousewheel)
        self._add_config_entry(self.config_inner_frame, "トリガー判定(px)", "trigger_pixels", nied.get("trigger_pixels", 10), _on_mousewheel)
        self._add_config_entry(self.config_inner_frame, "トリガー震度", "trigger_intensity", nied.get("trigger_intensity", 1.3), _on_mousewheel)
        self._add_config_entry(self.config_inner_frame, "凡例幅", "legend_width", nied.get("legend_width", 35), _on_mousewheel)
        self._add_config_entry(self.config_inner_frame, "凡例高さ", "legend_height", nied.get("legend_height", 400), _on_mousewheel)
        self._add_config_entry(self.config_inner_frame, "文字サイズ", "base_font_size", nied.get("base_font_size", 11), _on_mousewheel)
        
        def to_scale_str(val):
            mapping_rev = {10: "1", 20: "2", 30: "3", 40: "4", 45: "5弱", 50: "5強", 55: "6弱", 60: "6強", 70: "7"}
            try:
                int_val = int(val)
                return mapping_rev.get(int_val, str(val))
            except ValueError:
                return str(val)

        eew_alert = settings.get("eew_alert", {})
        default_scale = to_scale_str(eew_alert.get("countdown_threshold_scale", "3"))
        self._add_config_entry(self.config_inner_frame, "カウントダウン閾値震度", "countdown_threshold_scale", default_scale, None)

        interrupt_val = bool(eew_alert.get("interrupt_enabled", True))
        self._add_config_toggle(self.config_inner_frame, "アラート割り込み遷移", "interrupt_enabled", interrupt_val)

        # --- アラート保存設定セクション ---
        ttk.Separator(self.config_inner_frame, orient="horizontal").pack(fill=tk.X, padx=10, pady=(8, 4))
        ttk.Label(self.config_inner_frame, text="アラート保存設定", style="Panel.TLabel",
                  font=("Meiryo", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(2, 4))

        self._add_config_entry(self.config_inner_frame, "終了猶予(秒)", "alert_end_grace_sec",
                                nied.get("alert_end_grace_sec", 45), _on_mousewheel)

        trim_tail_val = bool(nied.get("alert_trim_quiet_tail", False))
        self._add_config_toggle(self.config_inner_frame, "末尾の静穏コマを削除", "alert_trim_quiet_tail", trim_tail_val)

        self._add_config_entry(self.config_inner_frame, "末尾保持秒数", "alert_tail_keep_sec",
                                nied.get("alert_tail_keep_sec", 10), _on_mousewheel)

        # --- HOME座標設定セクション ---
        ttk.Separator(self.config_inner_frame, orient="horizontal").pack(fill=tk.X, padx=10, pady=(8, 4))
        ttk.Label(self.config_inner_frame, text="HOME座標設定", style="Panel.TLabel",
                  font=("Meiryo", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(2, 4))
        ttk.Label(self.config_inner_frame,
                  text="※ 変更は次回EEW受信時から反映されます",
                  style="Panel.TLabel", font=("Meiryo", 8), foreground="#aaaaaa"
                  ).pack(anchor=tk.W, padx=10, pady=(0, 4))

        import eq_config.eq_config as _cfg_home
        self._add_config_entry(self.config_inner_frame, "緯度 (HOME_LAT)",    "home_lat",    _cfg_home.HOME_LAT,               _on_mousewheel)
        self._add_config_entry(self.config_inner_frame, "経度 (HOME_LON)",    "home_lon",    _cfg_home.HOME_LON,               _on_mousewheel)
        # JMA地域名: フィルタ付きコンボボックス
        _cfg_region_all = []
        try:
            _jpcode_path = os.path.join(_cfg_home.PROJECT_ROOT, "eq_assets", "eq_hypocenter_map_jpcode.json")
            with open(_jpcode_path, 'r', encoding='utf-8') as _jf:
                _cfg_region_all = sorted(json.load(_jf).keys())
        except Exception:
            pass
        _cfg_region_frame = ttk.Frame(self.config_inner_frame, style="Panel.TFrame")
        _cfg_region_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Label(_cfg_region_frame, text="JMA地域名", style="Panel.TLabel").pack(side=tk.LEFT)
        _cfg_region_var = tk.StringVar(value=str(_cfg_home.HOME_REGION_NAME))
        _cfg_region_cb = ttk.Combobox(_cfg_region_frame, textvariable=_cfg_region_var,
                                       values=_cfg_region_all, width=12, font=("Meiryo", 9))
        _cfg_region_cb.pack(side=tk.RIGHT)
        def _cfg_filter_regions(event=None, _cb=_cfg_region_cb, _var=_cfg_region_var, _all=_cfg_region_all):
            if event and event.keysym in ('Return', 'Escape', 'Tab', 'Down', 'Up'):
                if event.keysym in ('Return', 'Tab'):
                    self.save_config(show_msg=False)
                return
            typed = _var.get()
            filtered = [r for r in _all if typed in r] if typed else _all
            _cb['values'] = filtered
            if filtered:
                try:
                    _cb.event_generate('<Down>')
                except Exception:
                    pass
        _cfg_region_cb.bind('<KeyRelease>', _cfg_filter_regions)
        _cfg_region_cb.bind('<FocusOut>', lambda e: self.save_config(show_msg=False))
        _cfg_region_cb.bind('<<ComboboxSelected>>', lambda e: self.save_config(show_msg=False))
        if _on_mousewheel:
            _cfg_region_frame.bind("<MouseWheel>", _on_mousewheel)
            _cfg_region_cb.bind("<MouseWheel>", _on_mousewheel)
        self.config_entries["home_region"] = _cfg_region_cb
        self._add_config_entry(self.config_inner_frame, "地盤補正係数",        "home_ground", _cfg_home.HOME_GROUND_CORRECTION, _on_mousewheel)
        self._add_config_entry(self.config_inner_frame, "マップ中心X (px)",   "home_nied_x", _cfg_home.NIED_HOME_X,            _on_mousewheel)
        self._add_config_entry(self.config_inner_frame, "マップ中心Y (px)",   "home_nied_y", _cfg_home.NIED_HOME_Y,            _on_mousewheel)

        # --- 音量設定セクション ---
        ttk.Separator(self.config_inner_frame, orient="horizontal").pack(fill=tk.X, padx=10, pady=(8, 4))
        ttk.Label(self.config_inner_frame, text="音量設定", style="Panel.TLabel",
                  font=("Meiryo", 10, "bold")).pack(anchor=tk.W, padx=10, pady=(2, 6))

        # マスター音量スライダー
        vol_row = ttk.Frame(self.config_inner_frame, style="Panel.TFrame")
        vol_row.pack(fill=tk.X, padx=10, pady=(0, 4))
        ttk.Label(vol_row, text="マスター音量", style="Panel.TLabel").pack(side=tk.LEFT)
        self.master_volume_var = tk.IntVar(value=_cfg_home.MASTER_VOLUME)
        vol_display = tk.Label(vol_row, textvariable=self.master_volume_var,
                               bg="#3c3f41", fg="#ffffff", font=("Consolas", 10), width=4)
        vol_display.pack(side=tk.RIGHT)
        vol_scale = ttk.Scale(vol_row, from_=0, to=100, orient=tk.HORIZONTAL,
                              variable=self.master_volume_var,
                              command=lambda v: (
                                  self.master_volume_var.set(int(float(v))),
                                  self.save_config(show_msg=False)
                              ))
        vol_scale.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(6, 4))
        vol_scale.bind("<MouseWheel>", _on_mousewheel)

        # カウントダウンブースト
        self._add_config_entry(self.config_inner_frame, "CD音量ブースト (+)",
                               "countdown_boost", _cfg_home.COUNTDOWN_VOLUME_BOOST, _on_mousewheel)

        # シミュレーション終了時音声
        self._add_config_toggle(
            self.config_inner_frame,
            "シミュレーション終了音声",
            "play_sim_end",
            getattr(_cfg_home, "PLAY_SIMULATION_END_AUDIO", True)
        )

        # テスト再生ボタン行
        test_row = ttk.Frame(self.config_inner_frame, style="Panel.TFrame")
        test_row.pack(fill=tk.X, padx=10, pady=(6, 8))

        test_play_btn = tk.Label(
            test_row, text="▶ テスト再生", bg="#2a6496", fg="white",
            font=("Meiryo", 9, "bold"), cursor="hand2", padx=10, pady=4, relief="flat"
        )
        test_play_btn.pack(side=tk.LEFT, padx=(0, 6))
        test_play_btn.bind("<Enter>", lambda e: test_play_btn.config(bg="#357ebd"))
        test_play_btn.bind("<Leave>", lambda e: test_play_btn.config(bg="#2a6496"))
        test_play_btn.bind("<Button-1>", lambda e: self._play_test_audio())

        test_stop_btn = tk.Label(
            test_row, text="■ 停止", bg="#555555", fg="white",
            font=("Meiryo", 9, "bold"), cursor="hand2", padx=10, pady=4, relief="flat"
        )
        test_stop_btn.pack(side=tk.LEFT)
        test_stop_btn.bind("<Enter>", lambda e: test_stop_btn.config(bg="#777777"))
        test_stop_btn.bind("<Leave>", lambda e: test_stop_btn.config(bg="#555555"))
        test_stop_btn.bind("<Button-1>", lambda e: util_audio_player.stop_audio())

        # 設定タブ内の全ウィジェットでマウスホイールを有効化し、スクロール範囲を確定する
        # （トグル/ボタン/スライダ/コンボ上でもホイールでスクロールでき、末尾まで到達できるように）
        def _bind_wheel_recursive(w):
            for child in w.winfo_children():
                child.bind("<MouseWheel>", _on_mousewheel)
                child.bind("<Button-4>", _on_mousewheel)
                child.bind("<Button-5>", _on_mousewheel)
                _bind_wheel_recursive(child)
        _bind_wheel_recursive(self.config_inner_frame)
        self.config_inner_frame.update_idletasks()
        self.config_canvas.configure(scrollregion=self.config_canvas.bbox("all"))

    def _add_config_toggle(self, parent, label_text, key, initial_val):
        f = ttk.Frame(parent, style="Panel.TFrame")
        f.pack(fill=tk.X, padx=10, pady=5)
        lbl = ttk.Label(f, text=label_text, style="Panel.TLabel")
        lbl.pack(side=tk.LEFT)

        var = tk.BooleanVar(value=initial_val)

        btn = tk.Label(
            f,
            text="ON ✓" if initial_val else "OFF ✗",
            bg="#27ae60" if initial_val else "#555555",
            fg="white",
            font=("Meiryo", 8, "bold"),
            cursor="hand2",
            padx=8, pady=2
        )
        btn.pack(side=tk.RIGHT)

        def _toggle(_var=var, _btn=btn):
            _var.set(not _var.get())
            _btn.config(
                text="ON ✓" if _var.get() else "OFF ✗",
                bg="#27ae60" if _var.get() else "#555555"
            )
            self.save_config(show_msg=False)

        btn.bind("<Button-1>", lambda e: _toggle())
        btn.bind("<Enter>", lambda e: btn.config(bg="#219a52" if var.get() else "#666666"))
        btn.bind("<Leave>", lambda e: btn.config(bg="#27ae60" if var.get() else "#555555"))

        self.config_entries[key] = var

    def _add_config_entry(self, parent, label_text, key, val, mousewheel_handler=None):
        f = ttk.Frame(parent, style="Panel.TFrame")
        f.pack(fill=tk.X, padx=10, pady=5)
        
        lbl = ttk.Label(f, text=label_text, style="Panel.TLabel")
        lbl.pack(side=tk.LEFT)
        
        e = ttk.Entry(f, width=8)
        e.insert(0, str(val))
        e.pack(side=tk.RIGHT)
        
        # フォーカスアウトおよびEnterキー押下で自動保存（メッセージ非表示）
        e.bind("<FocusOut>", lambda event: self.save_config(show_msg=False))
        e.bind("<Return>", lambda event: self.save_config(show_msg=False))
        
        if mousewheel_handler:
            f.bind("<MouseWheel>", mousewheel_handler)
            f.bind("<Button-4>", mousewheel_handler)
            f.bind("<Button-5>", mousewheel_handler)
            lbl.bind("<MouseWheel>", mousewheel_handler)
            lbl.bind("<Button-4>", mousewheel_handler)
            lbl.bind("<Button-5>", mousewheel_handler)
            e.bind("<MouseWheel>", mousewheel_handler)
            e.bind("<Button-4>", mousewheel_handler)
            e.bind("<Button-5>", mousewheel_handler)
            
        self.config_entries[key] = e

    def load_settings(self):
        try:
            with open(self.settings_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def save_config(self, show_msg=True):
        settings = self.load_settings()
        if "nied_monitor" not in settings:
            settings["nied_monitor"] = {}
        if "eew_alert" not in settings:
            settings["eew_alert"] = {}
        
        try:
            settings["nied_monitor"]["radius_pixel"] = int(self.config_entries["radius_pixel"].get())
            settings["nied_monitor"]["trigger_pixels"] = int(self.config_entries["trigger_pixels"].get())
            settings["nied_monitor"]["trigger_intensity"] = float(self.config_entries["trigger_intensity"].get())
            settings["eew_alert"]["countdown_threshold_scale"] = self.config_entries["countdown_threshold_scale"].get().strip()
            if "interrupt_enabled" in self.config_entries:
                settings["eew_alert"]["interrupt_enabled"] = bool(self.config_entries["interrupt_enabled"].get())

            settings["nied_monitor"]["alert_end_grace_sec"] = float(self.config_entries["alert_end_grace_sec"].get())
            if "alert_trim_quiet_tail" in self.config_entries:
                settings["nied_monitor"]["alert_trim_quiet_tail"] = bool(self.config_entries["alert_trim_quiet_tail"].get())
            settings["nied_monitor"]["alert_tail_keep_sec"] = float(self.config_entries["alert_tail_keep_sec"].get())

            # Read and clamp new entries
            legend_width_val = int(self.config_entries["legend_width"].get())
            legend_height_val = int(self.config_entries["legend_height"].get())
            text_size_val = int(self.config_entries["base_font_size"].get())
            
            legend_width_val = max(10, min(legend_width_val, 150))
            legend_height_val = max(50, min(legend_height_val, 600))
            text_size_val = max(6, min(text_size_val, 30))
            
            settings["nied_monitor"]["legend_width"] = legend_width_val
            settings["nied_monitor"]["legend_height"] = legend_height_val
            settings["nied_monitor"]["base_font_size"] = text_size_val
            
            # Update entry fields with clamped values
            self.config_entries["legend_width"].delete(0, tk.END)
            self.config_entries["legend_width"].insert(0, str(legend_width_val))
            self.config_entries["legend_height"].delete(0, tk.END)
            self.config_entries["legend_height"].insert(0, str(legend_height_val))
            self.config_entries["base_font_size"].delete(0, tk.END)
            self.config_entries["base_font_size"].insert(0, str(text_size_val))
            
            with open(self.settings_path, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
                
            # Apply to visualizers immediately
            self.viz_full.update_sizes(legend_width_val, legend_height_val, text_size_val)
            self.viz_crop.update_sizes(legend_width_val, legend_height_val, text_size_val)
            
            # HOME設定の保存（user_home.json + モジュール属性を即時更新）
            import eq_config.eq_config as _cfg
            try:
                _lat = float(self.config_entries["home_lat"].get())
                _lon = float(self.config_entries["home_lon"].get())
                # 緯度経度からNIEDピクセル座標を自動計算して入力欄も更新
                _nx, _ny = _cfg.latlon_to_nied_pixel(_lat, _lon)
                self.config_entries["home_nied_x"].delete(0, tk.END)
                self.config_entries["home_nied_x"].insert(0, str(_nx))
                self.config_entries["home_nied_y"].delete(0, tk.END)
                self.config_entries["home_nied_y"].insert(0, str(_ny))
                _cfg.save_user_home(
                    lat        = _lat,
                    lon        = _lon,
                    region     = self.config_entries["home_region"].get().strip(),
                    ground_corr= float(self.config_entries["home_ground"].get()),
                    nied_x     = _nx,
                    nied_y     = _ny,
                )
                # シミュレーションのエリア補正デフォルト値を同期
                self.sim_lat_offset_var.set(str(_cfg.HOME_LAT))
                self.sim_lon_offset_var.set(str(_cfg.HOME_LON))
            except (ValueError, KeyError):
                pass

            # 音量設定の保存
            try:
                _cfg.save_audio_prefs(
                    master_volume  = self.master_volume_var.get(),
                    countdown_boost= int(self.config_entries["countdown_boost"].get()),
                    play_sim_end   = self.config_entries["play_sim_end"].get()
                )
            except (ValueError, KeyError, AttributeError):
                pass
            if show_msg:
                messagebox.showinfo("Success", "設定を保存しました。\n（凡例・文字サイズは即時反映、その他は次回起動時に反映されます）")
        except ValueError:
            if show_msg:
                # auto-save中にエラーが起きても無視する（入力途中の可能性があるため）
                pass
            else:
                pass # もしくはログに出力のみ

    def _play_test_audio(self):
        """configタブの現在の音量でサンプル音声を再生する"""
        import eq_config.eq_config as _cfg
        volume = self.master_volume_var.get()
        test_path = os.path.join(_cfg.PROJECT_ROOT, "eq_assets", "voice", "fixed", "eq_v_fixed_eew.wav")
        if not os.path.exists(test_path):
            # フォールバック: eq_assets 以下の最初に見つかった WAV
            import glob as _glob
            wavs = _glob.glob(os.path.join(_cfg.PROJECT_ROOT, "eq_assets", "**", "*.wav"), recursive=True)
            if not wavs:
                return
            test_path = wavs[0]
        threading.Thread(
            target=util_audio_player.play_audio,
            args=(test_path, volume),
            daemon=True
        ).start()

    def _check_first_launch(self):
        """user_home.json が無ければ初回設定ダイアログを表示する"""
        import eq_config.eq_config as _cfg
        if os.path.exists(_cfg.USER_HOME_PATH):
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("初期設定 — HOME座標の入力")
        dialog.geometry("440x340")
        dialog.resizable(False, False)
        dialog.configure(bg="#2b2b2b")
        dialog.grab_set()

        try:
            if hasattr(self, '_app_icon_photo'):
                dialog.iconphoto(False, self._app_icon_photo)
        except Exception:
            pass

        tk.Label(dialog, text="HOME座標の設定", bg="#2b2b2b", fg="white",
                 font=("Meiryo", 12, "bold")).pack(pady=(16, 4))
        tk.Label(dialog,
                 text="このシステムの地震警報はあなたの現在地をもとに計算します。\n"
                      "緯度・経度を入力してください（後でconfigタブから変更できます）。",
                 bg="#2b2b2b", fg="#cccccc", font=("Meiryo", 9),
                 justify=tk.LEFT, wraplength=400).pack(padx=20, pady=(0, 10))

        form = tk.Frame(dialog, bg="#2b2b2b")
        form.pack(padx=20, fill=tk.X)

        def _row(label, default):
            f = tk.Frame(form, bg="#2b2b2b")
            f.pack(fill=tk.X, pady=4)
            tk.Label(f, text=label, bg="#2b2b2b", fg="#cccccc",
                     font=("Meiryo", 10), width=16, anchor=tk.W).pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            tk.Entry(f, textvariable=var, width=14, font=("Consolas", 10)).pack(side=tk.LEFT)
            return var

        lat_var = _row("緯度  (例: 35.68)",  str(_cfg.HOME_LAT))
        lon_var = _row("経度  (例: 139.77)", str(_cfg.HOME_LON))

        # --- 地域名: フィルタ付きコンボボックス ---
        region_frame = tk.Frame(form, bg="#2b2b2b")
        region_frame.pack(fill=tk.X, pady=4)
        tk.Label(region_frame, text="JMA地域名", bg="#2b2b2b", fg="#cccccc",
                 font=("Meiryo", 10), width=16, anchor=tk.W).pack(side=tk.LEFT)

        all_regions = []
        try:
            _jpcode_path = os.path.join(_cfg.PROJECT_ROOT, "eq_assets",
                                        "eq_hypocenter_map_jpcode.json")
            with open(_jpcode_path, 'r', encoding='utf-8') as _f:
                all_regions = sorted(json.load(_f).keys())
        except Exception:
            pass

        region_var = tk.StringVar(value=_cfg.HOME_REGION_NAME)
        region_cb  = ttk.Combobox(region_frame, textvariable=region_var,
                                   values=all_regions, width=20, font=("Meiryo", 10))
        region_cb.pack(side=tk.LEFT)

        def _filter_regions(event=None):
            if event and event.keysym in ('Return', 'Escape', 'Tab', 'Down', 'Up'):
                return
            typed = region_var.get()
            filtered = [r for r in all_regions if typed in r] if typed else all_regions
            region_cb['values'] = filtered
            if filtered:
                try:
                    region_cb.event_generate('<Down>')
                except Exception:
                    pass

        region_cb.bind('<KeyRelease>', _filter_regions)

        tk.Label(dialog, text="入力で絞り込み / 空欄で予報地域照合をスキップ",
                 bg="#2b2b2b", fg="#888888", font=("Meiryo", 8)).pack(padx=20, anchor=tk.W, pady=(2, 0))

        def _save():
            try:
                lat = float(lat_var.get())
                lon = float(lon_var.get())
            except ValueError:
                tk.Label(dialog, text="緯度・経度は数値で入力してください",
                         bg="#2b2b2b", fg="#ff6b6b", font=("Meiryo", 9)).pack()
                return
            _nx, _ny = _cfg.latlon_to_nied_pixel(lat, lon)
            _cfg.save_user_home(lat, lon, region_var.get().strip(),
                                _cfg.HOME_GROUND_CORRECTION, _nx, _ny)
            # configタブのエントリも更新
            for key, val in [("home_lat", lat), ("home_lon", lon),
                              ("home_region", region_var.get().strip())]:
                if key in self.config_entries:
                    self.config_entries[key].delete(0, tk.END)
                    self.config_entries[key].insert(0, str(val))
            self.sim_lat_offset_var.set(str(lat))
            self.sim_lon_offset_var.set(str(lon))
            dialog.destroy()

        def _skip():
            dialog.destroy()

        btn_frame = tk.Frame(dialog, bg="#2b2b2b")
        btn_frame.pack(pady=12)
        tk.Button(btn_frame, text="設定して続行", command=_save,
                  bg="#2a6496", fg="white", font=("Meiryo", 10, "bold"),
                  relief="flat", padx=12, pady=4).pack(side=tk.LEFT, padx=8)
        tk.Button(btn_frame, text="スキップ（デフォルト使用）", command=_skip,
                  bg="#555555", fg="white", font=("Meiryo", 9),
                  relief="flat", padx=8, pady=4).pack(side=tk.LEFT, padx=4)
    def update_wolfx_status(self):
        try:
            status = self.eq_system.get_wolfx_status()
            if status == "connected":
                self.wolfx_status_label.config(text="ONLINE", fg="#00ff00")
            elif status == "connecting":
                self.wolfx_status_label.config(text="CONNECTING...", fg="yellow")
            else:
                self.wolfx_status_label.config(text="OFFLINE", fg="red")
        except Exception:
            pass
        finally:
            self.root.after(1000, self.update_wolfx_status)

    def update_nied_status(self):
        try:
            status = self.eq_system.get_nied_status()
            if status == "connected":
                self.nied_status_label.config(text="ONLINE", fg="#00ff00")
            elif status == "connecting":
                self.nied_status_label.config(text="CONNECTING...", fg="yellow")
            else:
                self.nied_status_label.config(text="OFFLINE", fg="red")
        except Exception:
            pass
        finally:
            self.root.after(1000, self.update_nied_status)

    # --- 地震発生中表示 (タイトル/ステータス欄の赤化 + カウントアップ) ---
    def _set_header_alert(self, active: bool):
        bg = self._header_alert_bg if active else self._header_normal_bg
        for w in self._header_alert_widgets:
            try:
                w.config(bg=bg)
            except Exception:
                pass

    @staticmethod
    def _hypo3(name: str) -> str:
        name = (name or "").strip()
        return name[:3] if name else "---"

    @staticmethod
    def _format_max_int_display(value) -> str:
        """震度値(JMA文字列 or NIED画像解析の連続値float)を表示用文字列に変換する"""
        if isinstance(value, str):
            v = value.strip()
            return v.replace("-", "弱").replace("+", "強") if v else "-"
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "-"
        if v < 0.5:
            return "-"
        buckets = [
            (1.5, "1"), (2.5, "2"), (3.5, "3"), (4.5, "4"),
            (4.75, "5弱"), (5.25, "5強"), (5.75, "6弱"), (6.25, "6強"),
        ]
        for threshold, label in buckets:
            if v < threshold:
                return label
        return "7"

    def _start_alert_display(self, hypo3="---", max_int_str="-", is_test=False):
        """地震発生中表示を開始する (カウントアップ開始 + タイトル欄赤化)

        既にタイマーループが走っている場合(テスト表示中に実アラートへ切り替わる等)は、
        古いループを止めてから新しいループを1本だけ張り直す。
        """
        if self._alert_timer_after_id is not None:
            try:
                self.root.after_cancel(self._alert_timer_after_id)
            except Exception:
                pass
            self._alert_timer_after_id = None
        self._alert_is_test = is_test
        self._alert_hypo3 = hypo3
        self._alert_max_int_str = max_int_str
        self._alert_active_start_ts = time.time()
        self._set_header_alert(True)
        self._tick_alert_timer()

    def _update_alert_info(self, hypo3=None, max_int_str=None):
        """地震発生中表示の内容(震源地/最大震度)だけを更新する(継続中に確報が届いた場合)

        テスト表示中に実EEWの確報を受信した場合は、テストではなく本物のアラートへ
        昇格させ、予約済みのテスト終了処理(5秒後)をキャンセルする。
        """
        if self._alert_active_start_ts is None:
            return
        if self._alert_is_test:
            self._alert_is_test = False
            if self._test_alert_after_id is not None:
                try:
                    self.root.after_cancel(self._test_alert_after_id)
                except Exception:
                    pass
                self._test_alert_after_id = None
        if hypo3 is not None:
            self._alert_hypo3 = hypo3
        if max_int_str is not None:
            self._alert_max_int_str = max_int_str

    def _tick_alert_timer(self):
        if self._alert_active_start_ts is None:
            return
        elapsed = int(time.time() - self._alert_active_start_ts)
        mm, ss = divmod(max(0, elapsed), 60)
        self.alert_timer_label.config(text=f"{self._alert_hypo3} {self._alert_max_int_str} {mm:02d}:{ss:02d}")
        self._alert_timer_after_id = self.root.after(1000, self._tick_alert_timer)

    def _stop_alert_display(self):
        """地震発生中表示を終了する (平常時の表示へ復帰)"""
        self._alert_active_start_ts = None
        self._alert_is_test = False
        if self._alert_timer_after_id is not None:
            try:
                self.root.after_cancel(self._alert_timer_after_id)
            except Exception:
                pass
            self._alert_timer_after_id = None
        if self._test_alert_after_id is not None:
            try:
                self.root.after_cancel(self._test_alert_after_id)
            except Exception:
                pass
            self._test_alert_after_id = None
        self._set_header_alert(False)
        self.alert_timer_label.config(text="--:--")

    def refresh_log_viewer(self):
        """eq_run_simulation2.py のように地震ごとにまとめてロード"""
        # SSDのパスとSDカード（プロジェクト内）のパス、レガシーパスをすべてリスト化して検索対象
        import eq_config.eq_config as _cfg_ssd
        ssd_base = os.path.join(_cfg_ssd.SSD_MOUNT_POINT, "raspberrypi/alert_eq")
        eew_dirs = [
            os.path.join(ssd_base, "eq_log", "eq_eew_jma"),
            self.eew_log_dir,
            os.path.join(PROJECT_ROOT, "eq_test", "test_eq_log", "test_eq_eew_jma")
        ]
        nied_dirs = [
            os.path.join(ssd_base, "eq_log", "monitor_images", "eq_log"),
            self.nied_image_log_dir,
            self.legacy_log_dir,  # 過去のディレクトリ構成にも対応
            os.path.join(PROJECT_ROOT, "eq_test", "test_monitor_images", "eq_log")
        ]
        
        self.all_event_groups = collect_past_events(eew_dirs, nied_dirs)
        self.update_log_headers()
        self.update_sim_headers()
        self.update_log_list()
        self.update_sim_list()

    def update_log_headers(self):
        # Clear existing widgets in header frame
        for child in self.log_list_header_frame.winfo_children():
            child.destroy()
            
        date_arrow = "▼" if self.log_sort_desc else "▲"
        int_arrow = "▼" if self.log_sort_desc else "▲"
        
        date_txt = f"日時{date_arrow}" if self.log_sort_col == "date" else "日時"
        int_txt = f"最大{int_arrow}" if self.log_sort_col == "intensity" else "最大"
        
        cols = [
            ("No.", 4, tk.W, None),
            (date_txt, 12, tk.W, lambda e=None: self.toggle_log_sort("date")),
            ("震源▼", 5, tk.W, lambda e: self._show_hypocenter_filter_menu('log', e)),
            (int_txt, 4, tk.W, lambda e=None: self.toggle_log_sort("intensity")),
            ("EEW", 4, tk.W, None),
            ("NIED", 4, tk.W, None)
        ]
        
        for text, width, anchor, cmd in cols:
            lbl = tk.Label(
                self.log_list_header_frame, text=text, width=width,
                bg="#2b2b2b", fg="#888888", font=("MS Gothic", 9),
                anchor=anchor, justify=tk.LEFT,
                padx=0, pady=0, bd=0, highlightthickness=0
            )
            lbl.pack(side=tk.LEFT)
            if cmd:
                lbl.config(cursor="hand2", fg="#bbbbbb")
                lbl.bind("<Button-1>", cmd)
                lbl.bind("<Enter>", lambda e, l=lbl: l.config(fg="white", bg="#3c3f41"))
                lbl.bind("<Leave>", lambda e, l=lbl: l.config(fg="#bbbbbb", bg="#2b2b2b"))

    def toggle_log_sort(self, col):
        if self.log_sort_col == col:
            self.log_sort_desc = not self.log_sort_desc
        else:
            self.log_sort_col = col
            self.log_sort_desc = True
        self.update_log_headers()
        self.update_log_list()

    def update_sim_headers(self):
        # Clear existing widgets in header frame
        for child in self.sim_list_header_frame.winfo_children():
            child.destroy()
            
        date_arrow = "▼" if self.sim_sort_desc else "▲"
        int_arrow = "▼" if self.sim_sort_desc else "▲"
        
        date_txt = f"日時{date_arrow}" if self.sim_sort_col == "date" else "日時"
        int_txt = f"最大{int_arrow}" if self.sim_sort_col == "intensity" else "最大"
        
        cols = [
            ("No.", 4, tk.W, None),
            (date_txt, 12, tk.W, lambda e=None: self.toggle_sim_sort("date")),
            ("震源▼", 5, tk.W, lambda e: self._show_hypocenter_filter_menu('sim', e)),
            (int_txt, 4, tk.W, lambda e=None: self.toggle_sim_sort("intensity")),
            ("EEW", 4, tk.W, None),
            ("NIED", 4, tk.W, None)
        ]
        
        for text, width, anchor, cmd in cols:
            lbl = tk.Label(
                self.sim_list_header_frame, text=text, width=width,
                bg="#2b2b2b", fg="#888888", font=("MS Gothic", 9),
                anchor=anchor, justify=tk.LEFT,
                padx=0, pady=0, bd=0, highlightthickness=0
            )
            lbl.pack(side=tk.LEFT)
            if cmd:
                lbl.config(cursor="hand2", fg="#bbbbbb")
                lbl.bind("<Button-1>", cmd)
                lbl.bind("<Enter>", lambda e, l=lbl: l.config(fg="white", bg="#3c3f41"))
                lbl.bind("<Leave>", lambda e, l=lbl: l.config(fg="#bbbbbb", bg="#2b2b2b"))

    def toggle_sim_sort(self, col):
        if self.sim_sort_col == col:
            self.sim_sort_desc = not self.sim_sort_desc
        else:
            self.sim_sort_col = col
            self.sim_sort_desc = True
        self.update_sim_headers()
        self.update_sim_list()

    def update_log_list(self):
        import datetime
        self.log_event_listbox.delete(0, tk.END)
        self.log_file_listbox.delete(0, tk.END)
        
        

        if not hasattr(self, 'all_event_groups'):
            return

        filtered = list(self.all_event_groups)
        
        # Source Filter
        src_filter = self.log_filter_source_cb.get()
        if src_filter == "EEWあり":
            filtered = [g for g in filtered if g.has_eew]
        elif src_filter == "NIED地表のみ":
            filtered = [g for g in filtered if g.has_nied and not getattr(g, 'has_borehole', False)]
        elif src_filter == "NIED地中あり":
            filtered = [g for g in filtered if getattr(g, 'has_borehole', False)]

        # Hypocenter Keyword Filter
        hypo_keyword = self.log_filter_hypo_entry.get().strip().lower()
        if hypo_keyword:
            filtered = [g for g in filtered if hypo_keyword in g.hypocenter.lower() or hypo_keyword in format_hypocenter(g.hypocenter).lower()]

        # Sorting
        if self.log_sort_col == "date":
            filtered.sort(key=lambda x: x.id, reverse=self.log_sort_desc)
        elif self.log_sort_col == "intensity":
            filtered.sort(key=lambda x: (intensity_to_float(x.max_intensity), x.id), reverse=self.log_sort_desc)

        self.current_event_groups = filtered

        for i, group in enumerate(self.current_event_groups):
            try:
                dt_obj = datetime.datetime.strptime(group.id, "%Y%m%d_%H%M")
                fmt_dt = dt_obj.strftime("%y/%m/%d %H:%M")
            except Exception:
                fmt_dt = group.id
            
            fmt_hypo = format_hypocenter(group.hypocenter)
            eew_mark = "〇" if group.has_eew else "－"
            nied_mark = "◎" if getattr(group, 'has_borehole', False) else ("〇" if group.has_nied else "－")
            label = (f"{pad_ja(f'[{i+1}]', 5)}"
                     f"{pad_ja(fmt_dt, 15)}"
                     f" {pad_ja(fmt_hypo, 6)}"
                     f" {center_ja(group.max_intensity, 6)}"
                     f" {center_ja(eew_mark, 4)}"
                     f" {center_ja(nied_mark, 4)}")
            self.log_event_listbox.insert(tk.END, label)

    def update_sim_list(self):
        import datetime
        self.sim_event_listbox.delete(0, tk.END)

        if not hasattr(self, 'all_event_groups'):
            return

        filtered = list(self.all_event_groups)
        
        # Source Filter
        src_filter = self.sim_filter_source_cb.get()
        if src_filter == "EEWあり":
            filtered = [g for g in filtered if g.has_eew]
        elif src_filter == "NIED地表のみ":
            filtered = [g for g in filtered if g.has_nied and not getattr(g, 'has_borehole', False)]
        elif src_filter == "NIED地中あり":
            filtered = [g for g in filtered if getattr(g, 'has_borehole', False)]

        # Hypocenter Keyword Filter
        hypo_keyword = self.sim_filter_hypo_entry.get().strip().lower()
        if hypo_keyword:
            filtered = [g for g in filtered if hypo_keyword in g.hypocenter.lower() or hypo_keyword in format_hypocenter(g.hypocenter).lower()]

        # Sorting
        if self.sim_sort_col == "date":
            filtered.sort(key=lambda x: x.id, reverse=self.sim_sort_desc)
        elif self.sim_sort_col == "intensity":
            filtered.sort(key=lambda x: (intensity_to_float(x.max_intensity), x.id), reverse=self.sim_sort_desc)

        self.current_sim_event_groups = filtered

        for i, group in enumerate(self.current_sim_event_groups):
            try:
                dt_obj = datetime.datetime.strptime(group.id, "%Y%m%d_%H%M")
                fmt_dt = dt_obj.strftime("%y/%m/%d %H:%M")
            except Exception:
                fmt_dt = group.id
            
            fmt_hypo = format_hypocenter(group.hypocenter)
            eew_mark = "〇" if group.has_eew else "－"
            if getattr(group, 'has_borehole', False):
                nied_mark = "◎"
            elif group.has_nied:
                nied_mark = "〇"
            else:
                nied_mark = "－"
            
            label = (f"{pad_ja(f'[{i+1}]', 5)}"
                     f"{pad_ja(fmt_dt, 15)}"
                     f" {pad_ja(fmt_hypo, 6)}"
                     f" {center_ja(group.max_intensity, 6)}"
                     f" {center_ja(eew_mark, 4)}"
                     f" {center_ja(nied_mark, 4)}")
            
            self.sim_event_listbox.insert(tk.END, label)

    def _show_hypocenter_filter_menu(self, panel_type, event):
        if not hasattr(self, 'all_event_groups'): return
        menu = tk.Menu(self.root, tearoff=0, bg="#2b2b2b", fg="white")
        
        from eq_test.eq_run_simulation2 import format_hypocenter
        hypos = set()
        for g in self.all_event_groups:
            hypos.add(format_hypocenter(g.hypocenter))
        hypos = sorted(list(hypos))
        
        def _apply_filter(h):
            if panel_type == 'log':
                self.log_filter_hypo_entry.delete(0, tk.END)
                if h != "すべて":
                    self.log_filter_hypo_entry.insert(0, h)
                self.update_log_list()
            else:
                self.sim_filter_hypo_entry.delete(0, tk.END)
                if h != "すべて":
                    self.sim_filter_hypo_entry.insert(0, h)
                self.update_sim_list()
                
        menu.add_command(label="すべて (クリア)", command=lambda: _apply_filter("すべて"))
        menu.add_separator()
        for h in hypos:
            menu.add_command(label=h, command=lambda ht=h: _apply_filter(ht))
            
        menu.tk_popup(event.x_root, event.y_root)
    
    def on_log_event_select(self, event):
        """Log イベントが選択された"""
        sel = self.log_event_listbox.curselection()
        if not sel:
            return
        
        idx = sel[0]
        if idx >= len(self.current_event_groups):
            return
        
        self.current_selected_group = self.current_event_groups[idx]
        # ファイルリストを再更新
        self.on_log_type_change()
    
    def on_log_type_change(self):
        """EEW/Monitor 切り替え"""
        if not hasattr(self, 'current_selected_group'):
            return
        
        group = self.current_selected_group
        log_type = self.log_type_var.get()
        
        self.log_file_listbox.delete(0, tk.END)
        print(f"[Log] 選択イベント: {group.id}, 種別: {log_type}")
        
        splash = LoadingOverlay(self.root, title="ログ読み込み", message="ファイルリストを構築中...", delay_ms=500)
        splash.set_indeterminate()
        
        try:
            if log_type == "eew":
                # EEW ファイル表示 (WOLFX のみ)
                eew_events = sorted([e for e in group.timeline if e.label == "WOLFX"], key=lambda x: x.timestamp)
                for i, event in enumerate(eew_events):
                    self.log_file_listbox.insert(tk.END, f"[{i+1}] {event.data.event_id if event.data else 'Unknown'}")
                    if i % 50 == 0:
                        splash.tick()
                print(f"[Log] EEWファイル {len(eew_events)} 件をリストアップしました。")
            else:
                # Monitor ファイル表示
                if group.has_nied:
                    nied_dir = None
                    
                    # パターン1: タイムライン情報から画像パスを逆算（simタブと同じ確実な方法）
                    nied_events = [e for e in group.timeline if e.label == "NIED" and getattr(e, 'image_path', None)]
                    if nied_events:
                        nied_dir = os.path.dirname(nied_events[0].image_path)
                        print(f"[Log] タイムライン情報からディレクトリを特定: {nied_dir}")
                    
                    # パターン2: 念のためのフォールバック検索（_lv縛りを外して広く検索）
                    if not nied_dir or not os.path.exists(nied_dir):
                        import eq_config.eq_config as _cfg_ssd
                        ssd_base = os.path.join(_cfg_ssd.SSD_MOUNT_POINT, "raspberrypi/alert_eq")
                        search_dirs = [
                            os.path.join(ssd_base, "eq_log", "monitor_images", "eq_log"),
                            self.nied_image_log_dir,
                            self.legacy_log_dir
                        ]
                        prefix = f"eqlog_{group.id}"  # _lv を除去して部分一致
                        for base_dir in search_dirs:
                            if os.path.exists(base_dir):
                                for d in os.listdir(base_dir):
                                    if d.startswith(prefix) and os.path.isdir(os.path.join(base_dir, d)):
                                        nied_dir = os.path.join(base_dir, d)
                                        print(f"[Log] 検索によりディレクトリを特定: {nied_dir}")
                                        break
                            if nied_dir: break
                    
                    if nied_dir and os.path.exists(nied_dir):
                        # borehole/surfaceサブフォルダ対応
                        if os.path.basename(nied_dir) in ("surface", "borehole"):
                            nied_dir = os.path.dirname(nied_dir)
                        
                        all_file_paths = []
                        for f in sorted(os.listdir(nied_dir)):
                            if f.endswith(('.mp4', '.gif')):
                                all_file_paths.append(os.path.join(nied_dir, f))
                                
                        is_borehole = self.log_borehole_var.get()
                        target_subdir = "borehole" if is_borehole else "surface"
                        target_dir = os.path.join(nied_dir, target_subdir)
                        
                        if os.path.exists(target_dir):
                            for f in sorted(os.listdir(target_dir)):
                                if f.endswith(('.png', '.jpg', '.jpeg')):
                                    all_file_paths.append(os.path.join(target_dir, f))
                        else:
                            # 互換性フォールバック: サブフォルダが無い場合は親フォルダから取得
                            for f in sorted(os.listdir(nied_dir)):
                                if f.endswith(('.png', '.jpg', '.jpeg')):
                                    all_file_paths.append(os.path.join(nied_dir, f))
                        
                        for idx, fp in enumerate(all_file_paths):
                            self.log_file_listbox.insert(tk.END, os.path.basename(fp))
                            i = self.log_file_listbox.size() - 1
                            if fp.endswith('.mp4'):
                                self.log_file_listbox.itemconfig(i, bg="#0f3c5f", fg="white")
                            elif fp.endswith('.gif'):
                                self.log_file_listbox.itemconfig(i, bg="#3f0f5f", fg="white")
                            
                            if idx % 100 == 0:
                                splash.tick()
                                
                        print(f"[Log] {len(all_file_paths)} 件のメディアファイルをリストアップしました。")
                    else:
                        print(f"[Error] 対象のNIED画像ディレクトリが見つかりません。")
        finally:
            splash.close()

    def _delete_selected_log_event(self):
        """選択中のイベントに紐づくEEW JSONファイルとNIED画像フォルダを削除する"""
        import shutil
        if not hasattr(self, 'current_selected_group') or not self.current_selected_group:
            messagebox.showwarning("通知", "削除するイベントをリストから選択してください。")
            return

        group = self.current_selected_group

        # --- 凍結バンドル内のファイルは削除不可 ---
        frozen_root = getattr(sys, '_MEIPASS', None)

        # EEWファイルパスを収集
        eew_paths = [
            e.image_path for e in group.timeline
            if e.label == "WOLFX" and e.image_path and os.path.exists(e.image_path)
            and (frozen_root is None or not e.image_path.startswith(frozen_root))
        ]

        # NIEDディレクトリを特定
        nied_dir = None
        nied_events = [e for e in group.timeline if e.label == "NIED" and getattr(e, 'image_path', None)]
        if nied_events:
            candidate = os.path.dirname(nied_events[0].image_path)
            # surface/ サブフォルダ由来の場合は親(eqlog_*) に正規化して丸ごと削除する
            if os.path.basename(candidate) in ("surface", "borehole"):
                candidate = os.path.dirname(candidate)
            if frozen_root is None or not candidate.startswith(frozen_root):
                nied_dir = candidate
        if not nied_dir or not os.path.exists(nied_dir):
            for base_dir in [self.nied_image_log_dir, self.legacy_log_dir]:
                if not os.path.exists(base_dir):
                    continue
                for d in os.listdir(base_dir):
                    if d.startswith(f"eqlog_{group.id}") and os.path.isdir(os.path.join(base_dir, d)):
                        candidate = os.path.join(base_dir, d)
                        if frozen_root is None or not candidate.startswith(frozen_root):
                            nied_dir = candidate
                            break
                if nied_dir:
                    break

        if not eew_paths and not nied_dir:
            messagebox.showinfo("通知",
                "削除可能なファイルが見つかりません。\n"
                "（同梱デモデータはexe内部に格納されており削除できません）")
            return

        # 確認ダイアログ
        parts = []
        if eew_paths:
            parts.append(f"EEW JSON: {len(eew_paths)} ファイル")
        if nied_dir and os.path.exists(nied_dir):
            n = sum(len(files) for _root, _dirs, files in os.walk(nied_dir))
            parts.append(f"NIED 画像: {n} ファイル ({os.path.basename(nied_dir)})")

        msg = (f"イベント [{group.id}]  {group.hypocenter} を削除します。\n\n"
               + "\n".join(parts) + "\n\nこの操作は元に戻せません。削除しますか？")
        if not messagebox.askyesno("削除確認", msg, icon="warning"):
            return

        errors = []
        # EEW JSONファイルを削除
        for path in eew_paths:
            try:
                os.remove(path)
                date_dir = os.path.dirname(path)
                if os.path.isdir(date_dir) and not os.listdir(date_dir):
                    os.rmdir(date_dir)
            except Exception as ex:
                errors.append(str(ex))

        # NIEDディレクトリを削除
        if nied_dir and os.path.exists(nied_dir):
            try:
                shutil.rmtree(nied_dir)
            except Exception as ex:
                errors.append(str(ex))

        if errors:
            messagebox.showwarning("一部エラー", f"削除中にエラーが発生しました:\n{errors[0]}")

        self.current_selected_group = None
        self.log_file_listbox.delete(0, tk.END)
        self.refresh_log_viewer()

    def _on_create_video_click(self):
        """動画再作成ボタンが押された時の処理"""
        import tkinter.messagebox as mb
        if not hasattr(self, 'current_selected_group') or not self.current_selected_group:
            mb.showwarning("通知", "地震が選択されていません。")
            return
            
        group = self.current_selected_group
        if not group.has_nied:
            mb.showwarning("通知", "この地震にはNIEDのモニタ画像データがありません。")
            return
            
        # ディレクトリの特定 (on_log_type_changeと同じロジック)
        nied_dir = None
        nied_events = [e for e in group.timeline if e.label == "NIED" and getattr(e, 'image_path', None)]
        if nied_events:
            nied_dir = os.path.dirname(nied_events[0].image_path)

        if not nied_dir or not os.path.exists(nied_dir):
            import eq_config.eq_config as _cfg_ssd
            ssd_base = os.path.join(_cfg_ssd.SSD_MOUNT_POINT, "raspberrypi/alert_eq")
            search_dirs = [
                os.path.join(ssd_base, "eq_log", "monitor_images", "eq_log"),
                self.nied_image_log_dir,
                self.legacy_log_dir
            ]
            prefix = f"eqlog_{group.id}"
            for base_dir in search_dirs:
                if os.path.exists(base_dir):
                    for d in os.listdir(base_dir):
                        if d.startswith(prefix) and os.path.isdir(os.path.join(base_dir, d)):
                            nied_dir = os.path.join(base_dir, d)
                            break
                if nied_dir: break

        # surface/ サブフォルダ由来の場合は親ディレクトリに正規化
        if nied_dir and os.path.basename(nied_dir) in ("surface", "borehole"):
            nied_dir = os.path.dirname(nied_dir)

        if nied_dir and os.path.exists(nied_dir):
            gif_path = os.path.join(nied_dir, "alert_summary.gif")
            mp4_path = os.path.join(nied_dir, "alert_summary.mp4")
            gif_exists = os.path.exists(gif_path)
            mp4_exists = os.path.exists(mp4_path)
            
            def do_generate(gen_gif, gen_mp4, window=None):
                if window: window.destroy()
                print(f"[Log] 動画作成を開始します: GIF={gen_gif}, MP4={gen_mp4}")
                
                splash = LoadingOverlay(self.root, title="動画作成", message="初期化中...")
                
                def on_progress(current, total, status):
                    self.root.after(0, splash.update_progress, current, total, status)
                
                def on_complete(success, msg):
                    def show():
                        splash.close()
                        import tkinter.messagebox as mb
                        if success:
                            mb.showinfo("完了", msg)
                        else:
                            mb.showerror("エラー", f"動画作成中にエラーが発生しました。\n\n{msg}")
                    self.root.after(0, show)
                
                if hasattr(self.eq_system, 'nied') and self.eq_system.nied:
                    self.eq_system.nied._start_video_generation(
                        nied_dir, 
                        generate_gif=gen_gif, 
                        generate_mp4=gen_mp4, 
                        on_complete=on_complete,
                        on_progress=on_progress
                    )
                else:
                    splash.close()
                    print("[Error] nied が初期化されていません。")

            if gif_exists or mp4_exists:
                top = tk.Toplevel(self.root)
                top.title("動画再作成")
                top.geometry("300x120")
                top.configure(bg="#2b2b2b")
                top.transient(self.root)
                top.wait_visibility(top)
                top.grab_set()
                
                msg = "既に動画ファイルが存在します。\nどのように処理しますか？"
                ttk.Label(top, text=msg, style="Panel.TLabel", justify="center").pack(pady=10)
                
                btn_frame = tk.Frame(top, bg="#2b2b2b")
                btn_frame.pack(pady=5)
                
                # 上書き
                btn_ow = tk.Button(btn_frame, text="すべて上書き", bg="#8b4513", fg="white", relief="flat", command=lambda: do_generate(True, True, top))
                btn_ow.pack(side=tk.LEFT, padx=5)
                
                # 〇〇のみ作成
                if gif_exists and not mp4_exists:
                    btn_only = tk.Button(btn_frame, text="MP4のみ作成", bg="#2a6496", fg="white", relief="flat", command=lambda: do_generate(False, True, top))
                    btn_only.pack(side=tk.LEFT, padx=5)
                elif mp4_exists and not gif_exists:
                    btn_only = tk.Button(btn_frame, text="GIFのみ作成", bg="#2a6496", fg="white", relief="flat", command=lambda: do_generate(True, False, top))
                    btn_only.pack(side=tk.LEFT, padx=5)
                    
                # キャンセル
                btn_cancel = tk.Button(btn_frame, text="キャンセル", bg="#555555", fg="white", relief="flat", command=top.destroy)
                btn_cancel.pack(side=tk.LEFT, padx=5)
            else:
                do_generate(True, True)
        else:
            import tkinter.messagebox as mb
            mb.showwarning("通知", "動画作成元の画像ディレクトリが見つかりません。\nデータが保存されていないか、削除された可能性があります。")

    def on_filter_cb_change(self, panel_name):
        cb = self.tl_filter_cb_1 if panel_name == "left" else self.tl_filter_cb_2
        rcb = self.tl_region_cb_1 if panel_name == "left" else self.tl_region_cb_2
        if cb.get() == "警報対象地域":
            rcb.pack(side=tk.LEFT, padx=2, pady=2)
            if not rcb.get():
                rcb.set("(すべて)")
        else:
            rcb.pack_forget()
        self.update_timeline_view(panel_name)

    def jump_from_json_to_tl(self, event, panel_name):
        pass # Now handled by tags in _render_eew_log

    def _on_show_tl_click(self):
        active_panel = self.log_target_panel_var.get()
        self._show_tl_for_panel(active_panel)

    def _show_tl_for_panel(self, panel_name, filter_key=""):
        if not hasattr(self, 'current_selected_group') or not self.current_selected_group:
            import tkinter.messagebox as mb
            mb.showwarning("通知", "地震が選択されていません。")
            return
            
        group = self.current_selected_group
        eew_events = sorted([e for e in group.timeline if e.label == "WOLFX"], key=lambda x: getattr(x, 'timestamp', 0))
        if not eew_events:
            import tkinter.messagebox as mb
            mb.showwarning("通知", "EEWデータがありません。")
            return

        SECTION_KEYS = {
            "基本情報": ["EventID", "Serial", "発表区分", "発表元", "発表ステータス", "発表時刻 (UTC+9)", "地震発生時刻"],
            "震源・規模": ["震源地", "M (マグニチュード)", "深さ (Depth)", "最大震度", "緯度 (Latitude)", "経度 (Longitude)"],
            "震源・規模の精度情報": ["震央の精度", "深さの精度", "マグニチュードの精度"],
            "最大震度の変化情報": ["最大震度変化", "最大震度変化理由"],
            "フラグ状況": ["isSea (海域地震)", "isTraining (訓練報)", "isAssumption (推定震源)", "isWarn (警報)", "isFinal (最終報)", "isCancel (取消)"],
            "警報対象地域": ["WarnArea (地域予測)"]
        }

        timeline_data = []
        prev_json = {}
        unique_keys = set()
        unique_chiikis = set()
        
        import datetime
        import re
        for i, ev in enumerate(eew_events):
            try:
                curr_json = json.loads(ev.data.original_text)
                diffs = diff_dicts(prev_json, curr_json)
                
                filtered_diffs = []
                for d in diffs:
                    k, ov, nv = d
                    
                    if k.startswith("地域予測 ("):
                        unique_keys.add("WarnArea (地域予測)")
                        chiiki_match = re.search(r"地域予測 \((.+)\)", k)
                        if chiiki_match:
                            unique_chiikis.add(chiiki_match.group(1))
                    else:
                        unique_keys.add(k)

                    # Match filter
                    if filter_key:
                        if filter_key in SECTION_KEYS:
                            if filter_key == "警報対象地域":
                                if not k.startswith("地域予測 ("):
                                    continue
                            else:
                                if k not in SECTION_KEYS[filter_key]:
                                    continue
                        elif filter_key.startswith("地域予測 ("):
                            if k != filter_key:
                                continue
                        else:
                            if k != filter_key:
                                continue

                    filtered_diffs.append(d)
                
                serial = curr_json.get("Issue", {}).get("Serial", str(i+1))
                is_final = curr_json.get("Earthquake", {}).get("Condition") == "最終報" or ev.data.is_final
                title = f"第{serial}報"
                if is_final:
                    title += " (最終)"
                
                if filtered_diffs or not filter_key:
                    # Unix timestamp to JST string if it's a float
                    if isinstance(ev.timestamp, (int, float)):
                        ts_str = datetime.datetime.fromtimestamp(ev.timestamp).strftime("%H:%M:%S")
                    else:
                        ts_str = ev.timestamp.strftime("%H:%M:%S") if hasattr(ev.timestamp, "strftime") else str(ev.timestamp)
                        
                    timeline_data.append({
                        "title": title,
                        "timestamp": ts_str,
                        "diffs": filtered_diffs,
                        "report_idx": i,
                        "is_final": is_final
                    })
                prev_json = curr_json
            except Exception as e:
                print(f"Error parsing EEW JSON: {e}")

        timeline_data.reverse()
        cb_values = ["(すべて)", "基本情報", "震源・規模", "震源・規模の精度情報", "フラグ状況", "警報対象地域"]
        # Add actual keys excluding section ones if wanted, but simpler to just show what's changed
        other_keys = sorted(list(unique_keys))
        for ok in other_keys:
            if ok not in cb_values and ok != "WarnArea (地域予測)":
                cb_values.append(ok)
                
        chiiki_values = ["(すべて)"] + sorted(list(unique_chiikis))

        if panel_name == "left":
            self.log_text_1.pack_forget()
            self.log_canvas_1.pack_forget()
            self.video_ctrl_1.pack_forget()
            self.tl_frame_1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.tl_filter_cb_1['values'] = cb_values
            self.tl_region_cb_1['values'] = chiiki_values

            is_warn = filter_key == "警報対象地域" or filter_key.startswith("地域予測")
            if is_warn and not filter_key.startswith("地域予測"):
                self.tl_filter_cb_1.set("警報対象地域")
                self.tl_region_cb_1.pack_forget()
            elif filter_key.startswith("地域予測"):
                self.tl_filter_cb_1.set("警報対象地域")
                self.tl_region_cb_1.pack(side=tk.LEFT, padx=2, pady=2)
                m = re.search(r"地域予測 \((.+)\)", filter_key)
                if m: self.tl_region_cb_1.set(m.group(1))
            else:
                self.tl_filter_cb_1.set(filter_key if filter_key else "(すべて)")
                self.tl_region_cb_1.pack_forget()

            self.tl_canvas_1.set_data(timeline_data)
        else:
            self.log_text_2.pack_forget()
            self.log_canvas_2.pack_forget()
            self.video_ctrl_2.pack_forget()
            self.tl_frame_2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            self.tl_filter_cb_2['values'] = cb_values
            self.tl_region_cb_2['values'] = chiiki_values

            is_warn = filter_key == "警報対象地域" or filter_key.startswith("地域予測")
            if is_warn and not filter_key.startswith("地域予測"):
                self.tl_filter_cb_2.set("警報対象地域")
                self.tl_region_cb_2.pack_forget()
            elif filter_key.startswith("地域予測"):
                self.tl_filter_cb_2.set("警報対象地域")
                self.tl_region_cb_2.pack(side=tk.LEFT, padx=2, pady=2)
                m = re.search(r"地域予測 \((.+)\)", filter_key)
                if m: self.tl_region_cb_2.set(m.group(1))
            else:
                self.tl_filter_cb_2.set(filter_key if filter_key else "(すべて)")
                self.tl_region_cb_2.pack_forget()

            self.tl_canvas_2.set_data(timeline_data)

    def tl_diff_clicked(self, panel_name, key):
        """Called when a diff item button is clicked in the TimelineCanvas."""
        # Strip prefixes like "地域予測 (" if it's there? No, just use the key.
        self._show_tl_for_panel(panel_name, filter_key=key)

    def hide_timeline_view(self, panel_name):
        if panel_name == "left":
            self.tl_frame_1.pack_forget()
            self.log_text_1.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        else:
            self.tl_frame_2.pack_forget()
            self.log_text_2.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def update_timeline_view(self, panel_name):
        if panel_name == "left":
            filter_key = self.tl_filter_cb_1.get()
            region_key = self.tl_region_cb_1.get()
        else:
            filter_key = self.tl_filter_cb_2.get()
            region_key = self.tl_region_cb_2.get()

        if filter_key == "警報対象地域":
            if region_key and region_key != "(すべて)":
                filter_key = f"地域予測 ({region_key})"
        elif filter_key == "(すべて)":
            filter_key = ""

        self._show_tl_for_panel(panel_name, filter_key)

    def jump_from_tl_to_json(self, panel_name, report_idx):
        self.hide_timeline_view(panel_name)
        self.log_file_listbox.selection_clear(0, tk.END)
        self.log_file_listbox.selection_set(report_idx)
        self.log_file_listbox.see(report_idx)
        # Instead of triggering the whole listbox event which replaces both panels,
        # we can just manually trigger viewing that specific file.
        self.on_log_file_select(None)

    def on_log_file_select(self, event):
        """Log ファイルが選択された"""
        if not hasattr(self, 'current_selected_group'):
            return
        
        sel = self.log_file_listbox.curselection()
        if not sel:
            return
        
        log_type = self.log_type_var.get()
        group = self.current_selected_group
        idx = sel[0]
        active_panel = self.log_target_panel_var.get()
        
        file_path = None
        if log_type == "eew":
            eew_events = sorted([e for e in group.timeline if e.label == "WOLFX"], key=lambda x: x.timestamp)
            if idx < len(eew_events):
                file_path = eew_events[idx].image_path
        else:
            nied_dir = None
            nied_events = [e for e in group.timeline if e.label == "NIED" and getattr(e, 'image_path', None)]
            if nied_events:
                nied_dir = os.path.dirname(nied_events[0].image_path)
            
            if not nied_dir or not os.path.exists(nied_dir):
                import eq_config.eq_config as _cfg_ssd
                ssd_base = os.path.join(_cfg_ssd.SSD_MOUNT_POINT, "raspberrypi/alert_eq")
                search_dirs = [
                    os.path.join(ssd_base, "eq_log", "monitor_images", "eq_log"),
                    self.nied_image_log_dir,
                    self.legacy_log_dir
                ]
                prefix = f"eqlog_{group.id}"
                for base_dir in search_dirs:
                    if os.path.exists(base_dir):
                        for d in os.listdir(base_dir):
                            if d.startswith(prefix) and os.path.isdir(os.path.join(base_dir, d)):
                                nied_dir = os.path.join(base_dir, d)
                                break
                    if nied_dir: break
            
            if nied_dir and os.path.exists(nied_dir):
                # surface/ サブフォルダ由来の場合は親に正規化
                if os.path.basename(nied_dir) in ("surface", "borehole"):
                    nied_dir = os.path.dirname(nied_dir)
                
                all_file_paths = []
                for f in sorted(os.listdir(nied_dir)):
                    if f.endswith(('.mp4', '.gif')):
                        all_file_paths.append(os.path.join(nied_dir, f))
                        
                is_borehole = self.log_borehole_var.get()
                target_subdir = "borehole" if is_borehole else "surface"
                target_dir = os.path.join(nied_dir, target_subdir)
                
                if os.path.exists(target_dir):
                    for f in sorted(os.listdir(target_dir)):
                        if f.endswith(('.png', '.jpg', '.jpeg')):
                            all_file_paths.append(os.path.join(target_dir, f))
                else:
                    # Fallback if specific subdirectory does not exist
                    for f in sorted(os.listdir(nied_dir)):
                        if f.endswith(('.png', '.jpg', '.jpeg')):
                            all_file_paths.append(os.path.join(nied_dir, f))
                            
                if idx < len(all_file_paths):
                    file_path = all_file_paths[idx]
        
        if file_path:
            print(f"[Log] 画面に描画します: {file_path}")
            if active_panel == "left":
                self.selected_file_path_1 = file_path
            else:
                self.selected_file_path_2 = file_path
            self.render_file_path(file_path, active_panel)
        else:
            print(f"[Error] パスが取得できませんでした。")
            
    def render_file_path(self, file_path, panel_name):
        """指定したパネルにファイル(JSONまたは画像、動画)の内容をレンダリングする"""
        if panel_name == "left":
            t = self.log_text_1
            canvas = self.log_canvas_1
            scroll_y = self.scroll_y_1
            scroll_x = self.scroll_x_1
            pane = self.log_pane_left
            zoom_factor = self.log_zoom_factor_1
            video_ctrl = self.video_ctrl_1
            video_play_btn = self.video_play_btn_1
            video_slider = self.video_slider_1
            video_time_lbl = self.video_time_lbl_1
        else:
            t = self.log_text_2
            canvas = self.log_canvas_2
            scroll_y = self.scroll_y_2
            scroll_x = self.scroll_x_2
            pane = self.log_pane_right
            zoom_factor = self.log_zoom_factor_2
            video_ctrl = self.video_ctrl_2
            video_play_btn = self.video_play_btn_2
            video_slider = self.video_slider_2
            video_time_lbl = self.video_time_lbl_2

        # If TL is visible for this panel, dismiss it first so JSON fills the full pane
        tl_frame = self.tl_frame_1 if panel_name == "left" else self.tl_frame_2
        tl_frame.pack_forget()

        # Increment play session counter to terminate any active loop for this panel
        self.play_session_counters[panel_name] += 1
        session_id = self.play_session_counters[panel_name]

        # For JSON or None: use Text, hide Canvas, hide Video Controls
        if not file_path or not os.path.exists(file_path) or file_path.endswith('.json'):
            # Hide Video Control
            video_ctrl.place_forget()
            # Hide Canvas
            canvas.pack_forget()
            # Show Text
            t.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            # Reconfigure scrollbars to control Text
            scroll_y.config(command=t.yview)
            scroll_x.config(command=t.xview)
            t.config(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
            
            if not file_path or not os.path.exists(file_path):
                t.config(state=tk.NORMAL)
                t.delete("1.0", tk.END)
                t.config(state=tk.DISABLED)
                self.update_log_panel_headers()
                return

            # EEW JSON Display
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    raw_data = json.load(f)
                self._render_eew_log(raw_data, t)
            except Exception as e:
                t.config(state=tk.NORMAL)
                t.delete("1.0", tk.END)
                t.insert(tk.END, f"JSON読み込み失敗: {e}\n\nパス: {file_path}")
                t.config(state=tk.DISABLED)
            self.update_log_panel_headers()
            return

        # For media files (images, GIFs, videos), hide Text and show Canvas
        t.pack_forget()
        video_ctrl.place_forget()
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Reconfigure scrollbars to control Canvas
        scroll_y.config(command=canvas.yview)
        scroll_x.config(command=canvas.xview)
        canvas.config(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        
        # Clear previous Canvas content and create container label
        canvas.delete("all")
        img_label = tk.Label(canvas, bg="#1e1e1e")
        canvas.create_window((0, 0), window=img_label, anchor="nw")

        # Panning bindings
        def start_pan(event):
            self.on_pan_start(event, canvas)
        def do_pan(event):
            self.on_pan_drag(event, canvas)

        canvas.bind("<Button-1>", start_pan)
        canvas.bind("<B1-Motion>", do_pan)
        img_label.bind("<Button-1>", start_pan)
        img_label.bind("<B1-Motion>", do_pan)

        # Zoom and Scroll wheel bindings
        canvas.bind("<Control-MouseWheel>", lambda e: self.on_mousewheel_zoom(e, panel_name))
        img_label.bind("<Control-MouseWheel>", lambda e: self.on_mousewheel_zoom(e, panel_name))
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        img_label.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        canvas.bind("<Shift-MouseWheel>", lambda e: canvas.xview_scroll(int(-1 * (e.delta / 120)), "units"))
        img_label.bind("<Shift-MouseWheel>", lambda e: canvas.xview_scroll(int(-1 * (e.delta / 120)), "units"))

        # Calculate target bounding size
        pane_w = pane.winfo_width()
        pane_h = pane.winfo_height()
        
        # Fallback if UI is not fully laid out yet
        if pane_w < 50 or pane_h < 50:
            vw = self.right_frame.winfo_width()
            vh = self.right_frame.winfo_height()
            if vw < 100 or vh < 100:
                vw = 800
                vh = 400
            pane_w = (vw - 60) // 2
            pane_h = vh - 60
        
        max_w = max(100, pane_w - 30)
        max_h = max(100, pane_h - 30)
        
        target_w = int(max_w * zoom_factor)
        target_h = int(max_h * zoom_factor)

        if file_path.lower().endswith('.gif'):
            try:
                gif = Image.open(file_path)
                
                # Determine total frame count first
                total_frames = 0
                try:
                    while True:
                        total_frames += 1
                        gif.seek(gif.tell() + 1)
                except EOFError:
                    pass
                gif.seek(0)

                # Step-skipping based on frame count to reduce load time and memory usage
                step = 1
                if total_frames > 150:
                    step = 3
                elif total_frames > 80:
                    step = 2

                w, h = gif.size
                scale = min(target_w / w, target_h / h)
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))

                # Extract and resize PIL frames in Python (very lightweight)
                pil_frames = []
                frame_idx = 0
                try:
                    while True:
                        if frame_idx % step == 0:
                            frame = gif.copy().convert("RGBA")
                            if hasattr(Image, "Resampling"):
                                resized = frame.resize((new_w, new_h), Image.Resampling.BILINEAR)
                            else:
                                resized = frame.resize((new_w, new_h), Image.BILINEAR)
                            
                            # Sum durations of skipped frames to maintain correct playback speed
                            dur_sum = 0
                            for j in range(step):
                                dur_sum += gif.info.get('duration', 100)
                            if dur_sum <= 0:
                                dur_sum = 100 * step
                            pil_frames.append((resized, dur_sum))
                            
                        gif.seek(gif.tell() + 1)
                        frame_idx += 1
                except EOFError:
                    pass

                if not pil_frames:
                    raise Exception("No frames found in GIF")

                # Cache PIL frames reference (Python memory)
                if panel_name == "left":
                    self.current_log_image_1 = pil_frames
                else:
                    self.current_log_image_2 = pil_frames

                canvas.config(scrollregion=(0, 0, new_w, new_h))

                def update_gif_frame(idx):
                    if self.play_session_counters[panel_name] != session_id:
                        return
                    frame_idx = idx % len(pil_frames)
                    pil_img, dur = pil_frames[frame_idx]
                    
                    # Convert to PhotoImage on-the-fly (keeps Tcl graphics handles minimal)
                    photo = ImageTk.PhotoImage(pil_img)
                    
                    # Keep single active PhotoImage reference to avoid GC
                    if panel_name == "left":
                        self.active_gif_photo_1 = photo
                    else:
                        self.active_gif_photo_2 = photo
                        
                    img_label.config(image=photo)
                    img_label.image = photo
                    self.root.after(dur, lambda: update_gif_frame(frame_idx + 1))

                update_gif_frame(0)

            except Exception as e:
                canvas.delete("all")
                err_lbl = tk.Label(canvas, text=f"GIFの読み込みに失敗しました: {e}", bg="#1e1e1e", fg="red")
                canvas.create_window((10, 10), window=err_lbl, anchor="nw")

        elif file_path.lower().endswith('.mp4'):
            try:
                import cv2
                cap = cv2.VideoCapture(file_path)
                if not cap.isOpened():
                    raise Exception("Video open failed")

                v_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                v_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                if v_w <= 0 or v_h <= 0:
                    v_w, v_h = 640, 480
                if total_frames <= 0:
                    total_frames = 100
                
                scale = min(target_w / v_w, target_h / v_h)
                new_w = max(1, int(v_w * scale))
                new_h = max(1, int(v_h * scale))

                # Force 1 frame per second playback (1000ms delay)
                dur = 1000

                canvas.config(scrollregion=(0, 0, new_w, new_h))

                # Overlay the video control frame inside the Canvas at the bottom of the viewport
                video_ctrl.place(relx=0.0, rely=1.0, anchor=tk.SW, relwidth=1.0, height=30)
                self.video_playing[panel_name] = True
                video_play_btn.config(text="⏸")

                # Configure slider range
                video_slider.config(from_=0, to=total_frames - 1)
                video_slider.set(0)

                video_state = {
                    'was_playing': True,
                    'is_seeking': False
                }

                def format_time(seconds):
                    m = int(seconds) // 60
                    s = int(seconds) % 60
                    return f"{m:02d}:{s:02d}"

                def seek_to(frame_idx):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
                    ret, frame = cap.read()
                    if ret:
                        resized_frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                        frame_rgb = cv2.cvtColor(resized_frame, cv2.COLOR_BGR2RGB)
                        pil_img = Image.fromarray(frame_rgb)
                        photo = ImageTk.PhotoImage(pil_img)

                        if panel_name == "left":
                            self.current_log_image_1 = photo
                        else:
                            self.current_log_image_2 = photo

                        img_label.config(image=photo)
                        img_label.image = photo
                        video_time_lbl.config(text=f"{format_time(frame_idx)} / {format_time(total_frames)}")
                        video_slider.set(frame_idx)

                # Slider events
                def on_slider_press(event):
                    video_state['was_playing'] = self.video_playing[panel_name]
                    if video_state['was_playing']:
                        self.video_playing[panel_name] = False
                        video_play_btn.config(text="▶")
                    video_state['is_seeking'] = True

                def on_slider_drag(event):
                    val = video_slider.get()
                    seek_to(int(val))

                def on_slider_release(event):
                    val = video_slider.get()
                    seek_to(int(val))
                    video_state['is_seeking'] = False
                    if video_state['was_playing']:
                        self.video_playing[panel_name] = True
                        video_play_btn.config(text="⏸")
                        update_video_frame()

                video_slider.bind("<ButtonPress-1>", on_slider_press)
                video_slider.bind("<B1-Motion>", on_slider_drag)
                video_slider.bind("<ButtonRelease-1>", on_slider_release)

                # Toggle Play/Pause button
                def toggle_play(event=None):
                    self.video_playing[panel_name] = not self.video_playing[panel_name]
                    if self.video_playing[panel_name]:
                        video_play_btn.config(text="⏸")
                        update_video_frame()
                    else:
                        video_play_btn.config(text="▶")

                video_play_btn.bind("<Button-1>", toggle_play)

                def update_video_frame():
                    if self.play_session_counters[panel_name] != session_id:
                        cap.release()
                        return
                    if not self.video_playing[panel_name] or video_state['is_seeking']:
                        return

                    ret, frame = cap.read()
                    if not ret:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ret, frame = cap.read()
                        if not ret:
                            cap.release()
                            return
                    
                    frame_idx = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
                    if frame_idx < 0:
                        frame_idx = 0

                    resized_frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
                    frame_rgb = cv2.cvtColor(resized_frame, cv2.COLOR_BGR2RGB)
                    pil_img = Image.fromarray(frame_rgb)
                    photo = ImageTk.PhotoImage(pil_img)

                    if panel_name == "left":
                        self.current_log_image_1 = photo
                    else:
                        self.current_log_image_2 = photo

                    img_label.config(image=photo)
                    img_label.image = photo
                    
                    video_time_lbl.config(text=f"{format_time(frame_idx)} / {format_time(total_frames)}")
                    video_slider.set(frame_idx)

                    self.root.after(dur, update_video_frame)

                update_video_frame()

            except Exception as e:
                canvas.delete("all")
                err_lbl = tk.Label(canvas, text=f"動画の再生に失敗しました: {e}", bg="#1e1e1e", fg="red")
                canvas.create_window((10, 10), window=err_lbl, anchor="nw")

        else: # Standard images (PNG, JPG, JPEG)
            try:
                pil_img = Image.open(file_path)
                w, h = pil_img.size
                scale = min(target_w / w, target_h / h)
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))

                if hasattr(Image, "Resampling"):
                    resized_img = pil_img.resize((new_w, new_h), Image.Resampling.BILINEAR)
                else:
                    resized_img = pil_img.resize((new_w, new_h), Image.BILINEAR)
                
                photo = ImageTk.PhotoImage(resized_img)
                
                if panel_name == "left":
                    self.current_log_image_1 = photo
                else:
                    self.current_log_image_2 = photo

                canvas.config(scrollregion=(0, 0, new_w, new_h))
                img_label.config(image=photo)
                img_label.image = photo

            except Exception as e:
                canvas.delete("all")
                err_lbl = tk.Label(canvas, text=f"画像の読み込みに失敗しました: {e}", bg="#1e1e1e", fg="red")
                canvas.create_window((10, 10), window=err_lbl, anchor="nw")

        self.update_log_panel_headers()

    def _render_eew_log(self, data, t):
        """EEW JSONデータを見やすい形式で指定したテキストウィジェットに表示する"""
        t.config(state=tk.NORMAL)
        t.delete("1.0", tk.END)

        def get_val(key):
            if key in data:
                return data[key]
            parts = key.split('.')
            temp = data
            for p in parts:
                if isinstance(temp, dict) and p in temp:
                    temp = temp[p]
                else:
                    return None
            return temp

        panel_name = "left" if t == self.log_text_1 else "right"

        # TLフィルタリング可能なセクション見出しとフィールド名
        CLICKABLE_SECTIONS = {"基本情報", "震源・規模", "震源・規模の精度情報", "最大震度の変化情報", "フラグ状況", "警報対象地域"}
        CLICKABLE_FIELDS = {
            "発表区分", "発表元", "発表ステータス", "震源地", "M (マグニチュード)",
            "深さ (Depth)", "最大震度", "緯度 (Latitude)", "経度 (Longitude)",
            "震央の精度", "深さの精度", "マグニチュードの精度", "最大震度変化", "最大震度変化理由",
            "isSea (海域地震)", "isTraining (訓練報)", "isAssumption (推定震源)",
            "isWarn (警報)", "isCancel (取消)"
        }

        def line(label, value, color="white"):
            val_str = str(value) if value is not None else "-"
            t.insert(tk.END, "  ")
            if label in CLICKABLE_FIELDS:
                t.insert(tk.END, f" {label} ", ("clickable_lbl", f"jump_{label}"))
                t.tag_bind(f"jump_{label}", "<ButtonRelease-1>", lambda e, lbl=label: self._show_tl_for_panel(panel_name, lbl))
                t.tag_bind(f"jump_{label}", "<Enter>", lambda e, tag=f"jump_{label}": t.tag_config(tag, background="#555555"))
                t.tag_bind(f"jump_{label}", "<Leave>", lambda e, tag=f"jump_{label}": t.tag_config(tag, background="#333333"))
            else:
                t.insert(tk.END, f" {label} ", "dim_lbl")
            pad = max(1, 20 - len(label))
            t.insert(tk.END, " " * pad)
            t.insert(tk.END, f"{val_str}\n", color)

        def separator(title=""):
            if title:
                t.insert(tk.END, "\n── ")
                if title in CLICKABLE_SECTIONS:
                    t.insert(tk.END, f" {title} ", ("clickable_sec", f"jump_{title}"))
                    t.tag_bind(f"jump_{title}", "<ButtonRelease-1>", lambda e, sec=title: self._show_tl_for_panel(panel_name, sec))
                    t.tag_bind(f"jump_{title}", "<Enter>", lambda e, tag=f"jump_{title}": t.tag_config(tag, background="#666666"))
                    t.tag_bind(f"jump_{title}", "<Leave>", lambda e, tag=f"jump_{title}": t.tag_config(tag, background="#444444"))
                else:
                    t.insert(tk.END, f" {title} ", "sec_plain")
                t.insert(tk.END, " ─" + "─" * max(0, 36 - len(title)) + "\n", "dim")
            else:
                t.insert(tk.END, "\n")

        # タグ設定
        t.tag_config("clickable_lbl", foreground="#ffffff", background="#333333", relief="raised", borderwidth=1, font=("Meiryo", 9))
        t.tag_config("clickable_sec", foreground="#ffffff", background="#444444", relief="raised", borderwidth=1, font=("Meiryo", 9, "bold"))
        t.tag_config("white",   foreground="#ffffff", font=("Meiryo", 9, "bold"))
        t.tag_config("yellow",  foreground="#f0c040", font=("Meiryo", 9, "bold"))
        t.tag_config("red",     foreground="#ff6060", font=("Meiryo", 9, "bold"))
        t.tag_config("cyan",    foreground="#66cccc", font=("Meiryo", 9, "bold"))
        t.tag_config("dim",     foreground="#555555", font=("Meiryo", 9))
        t.tag_config("flag_on", foreground="#ff6060", font=("Meiryo", 9, "bold"))
        t.tag_config("flag_off",foreground="#444444", font=("Meiryo", 9))
        t.tag_config("dim_lbl",  foreground="#777777", font=("Meiryo", 9))
        t.tag_config("sec_plain",foreground="#bbbbbb", font=("Meiryo", 9, "bold"))

        # EEWデータを表示
        title = get_val("Title") or "緊急地震速報"
        is_warn = get_val("isWarn") or False
        is_can = get_val("isCancel") or False
        head_color = "red" if is_warn else ("yellow" if not is_can else "dim")
        t.insert(tk.END, f"\n  {title}\n", head_color)

        separator("基本情報")
        line("EventID", get_val("EventID"))
        serial = get_val("Serial")
        line("発表回数", f"第 {serial} 報" if serial is not None else "-")
        line("発表区分", get_val("CodeType"))
        line("発表元", get_val("Issue.Source"))
        line("発表ステータス", get_val("Issue.Status"))
        line("発表時刻 (UTC+9)", get_val("AnnouncedTime"))
        line("地震発生時刻", get_val("OriginTime"))

        separator("震源・規模")
        line("震源地", get_val("Hypocenter"), "yellow")
        line("M (マグニチュード)", get_val("Magunitude"), "yellow")
        line("深さ (Depth)", f"{get_val('Depth')} km" if get_val('Depth') is not None else "-")
        mi = get_val("MaxIntensity")
        mi_color = "red" if any(x in str(mi) for x in ["5弱", "5強", "6弱", "6強", "7", "5-", "5+", "6-", "6+"]) else "yellow"
        line("最大震度", mi, mi_color)
        line("緯度 (Latitude)", get_val("Latitude"))
        line("経度 (Longitude)", get_val("Longitude"))

        separator("震源・規模の精度情報")
        line("震央の精度", get_val("Accuracy.Epicenter"))
        line("深さの精度", get_val("Accuracy.Depth"))
        line("マグニチュードの精度", get_val("Accuracy.Magnitude"))

        # 震度変化情報
        ch_str = get_val("MaxIntChange.String")
        ch_reason = get_val("MaxIntChange.Reason")
        if ch_str or ch_reason:
            separator("最大震度の変化情報")
            line("最大震度変化", ch_str)
            line("最大震度変化理由", ch_reason)

        separator("フラグ状況")
        def flag(label, val):
            t.insert(tk.END, "  ")
            if label in CLICKABLE_FIELDS:
                t.insert(tk.END, f" {label} ", ("clickable_lbl", f"jump_{label}"))
                t.tag_bind(f"jump_{label}", "<ButtonRelease-1>", lambda e, lbl=label: self._show_tl_for_panel(panel_name, lbl))
                t.tag_bind(f"jump_{label}", "<Enter>", lambda e, tag=f"jump_{label}": t.tag_config(tag, background="#555555"))
                t.tag_bind(f"jump_{label}", "<Leave>", lambda e, tag=f"jump_{label}": t.tag_config(tag, background="#333333"))
            else:
                t.insert(tk.END, f" {label} ", "dim_lbl")
            pad = max(1, 20 - len(label))
            t.insert(tk.END, " " * pad)
            t.insert(tk.END, "YES\n" if val else "no\n", "flag_on" if val else "flag_off")
        
        flag("isSea (海域地震)", get_val("isSea"))
        flag("isTraining (訓練報)", get_val("isTraining"))
        flag("isAssumption (推定震源)", get_val("isAssumption"))
        flag("isWarn (警報)", get_val("isWarn"))
        flag("isFinal (最終報)", get_val("isFinal"))
        flag("isCancel (取消)", get_val("isCancel"))

        # 警報対象地域の一覧
        warn_areas = get_val("WarnArea")
        if isinstance(warn_areas, list) and len(warn_areas) > 0:
            separator("警報対象地域")
            for area in warn_areas:
                if not isinstance(area, dict):
                    continue
                chiiki = area.get("Chiiki", area.get("Name", "-"))
                shindo1 = area.get("Shindo1", area.get("MaxInt", "-"))
                shindo2 = area.get("Shindo2", "-")
                arrive = area.get("Arrive", "")
                atype = area.get("Type", "予報")
                
                arrive_str = " (到達済み)" if arrive else ""
                shindo_str = f"震度 {shindo1}"
                if shindo2 and shindo2 != shindo1 and shindo2 != "-":
                    shindo_str += f"〜{shindo2}"
                    
                area_detail = f"{shindo_str} | {atype}{arrive_str}"
                
                lbl = f"地域予測 ({chiiki})"
                t.insert(tk.END, "  ")
                t.insert(tk.END, f" {lbl} ", ("clickable_lbl", f"jump_{lbl}"))
                pad = max(1, 20 - len(lbl))
                t.insert(tk.END, " " * pad)
                t.insert(tk.END, f"{area_detail}\n", "cyan")
                t.tag_bind(f"jump_{lbl}", "<ButtonRelease-1>", lambda e, key=lbl: self._show_tl_for_panel(panel_name, key))
                t.tag_bind(f"jump_{lbl}", "<Enter>", lambda e, tag=f"jump_{lbl}": t.tag_config(tag, background="#555555"))
                t.tag_bind(f"jump_{lbl}", "<Leave>", lambda e, tag=f"jump_{lbl}": t.tag_config(tag, background="#333333"))

        # 気象庁発表の原文
        orig_txt = get_val("OriginalText")
        if orig_txt:
            separator("気象庁発表の原文 (Original Text)")
            t.insert(tk.END, f"{orig_txt.strip()}\n", "dim")

        t.insert(tk.END, "\n")
        t.config(state=tk.DISABLED)
    def get_selected_file_path(self):
        if self.log_target_panel_var.get() == "left":
            return getattr(self, 'selected_file_path_1', None)
        else:
            return getattr(self, 'selected_file_path_2', None)

    def open_log_dir(self):
        target = self.get_selected_file_path()
        if not target or not os.path.exists(target):
            target = self.log_dir
        
        if sys.platform == "win32":
            os.startfile(target)
        elif sys.platform == "darwin":
            os.system(f'open "{target}"')
        else:
            os.system(f'xdg-open "{target}"')

    def start_simulation(self):
        """Sim タブから再生開始"""
        if self.sim_running:
            self.sim_paused = False
            self.eq_system.sim_paused = False
            self._update_sim_playpause_btn('playing')
            return
        
        sel = self.sim_event_listbox.curselection()
        if not sel:
            messagebox.showwarning("警告", "シミュレーション対象を選択してください。")
            return
        
        idx = sel[0]
        if idx >= len(self.current_sim_event_groups):
            messagebox.showerror("エラー", "イベントが見つかりません。")
            return
        
        target_group = self.current_sim_event_groups[idx]

        try:
            speed = float(self.sim_speed_var.get())
        except Exception:
            speed = 1.0

        # エリア座標適用
        import copy
        from eq_config.eq_config import HOME_LAT, HOME_LON
        try:
            input_lat = float(self.sim_lat_offset_var.get())
            input_lon = float(self.sim_lon_offset_var.get())
        except (ValueError, AttributeError):
            input_lat, input_lon = HOME_LAT, HOME_LON

        # シム専用ビジュアライザのみ座標を上書き（リアルタイムモニタは変えない）
        for viz in [self.sim_viz, self.sim_viz_zoom]:
            viz.set_center_override(input_lat, input_lon)
        from eq_config.eq_config import latlon_to_nied_pixel
        _px, _py = latlon_to_nied_pixel(input_lat, input_lon)
        self.eq_system.sim_override_home_cx = _px
        self.eq_system.sim_override_home_cy = _py
        print(f"[Sim] ホーム座標更新: ({input_lat:.4f}°, {input_lon:.4f}°) → pixel ({_px}, {_py})")

        modified_group = copy.copy(target_group)
        modified_timeline = []
        is_borehole = self.sim_borehole_var.get()
        
        splash = LoadingOverlay(self.root, title="シミュレーション開始", message="データを構築中...")
        try:
            total_ev = len(target_group.timeline)
            for i, ev in enumerate(target_group.timeline):
                splash.update_progress(i, total_ev, f"データを構築中... ({i+1}/{total_ev})")
                if ev.label == "WOLFX" and ev.image_path:
                    if input_lat != HOME_LAT or input_lon != HOME_LON:
                        new_data = parse_eew_file(ev.image_path, home_lat=input_lat, home_lon=input_lon)
                    else:
                        new_data = ev.data
                    modified_timeline.append(SimEvent(
                        timestamp=ev.timestamp,
                        data=new_data if new_data else ev.data,
                        label=ev.label, image_path=ev.image_path
                    ))
                elif ev.label == "NIED" and ev.image_path:
                    modified_timeline.append(SimEvent(
                        timestamp=ev.timestamp,
                        data=ev.data,
                        label=ev.label, image_path=ev.image_path
                    ))
                else:
                    modified_timeline.append(ev)
                    
            modified_group.timeline = modified_timeline
            target_group = modified_group
        finally:
            splash.close()

        # フラグをリセット
        self.sim_session_id += 1
        session_id = self.sim_session_id
        self.sim_running = True
        self.sim_paused = False
        self.sim_stop_flag = False

        self.eq_system.sim_paused = False
        self.eq_system.sim_stop_flag = False
        self.eq_system.sim_mute = self.sim_mute_var.get()
        self.eq_system.sim_original_s_wave_arrival_ts = 0.0
        self.eq_system.sim_original_is_custom_prediction = False
        self.eq_system.sim_original_predicted_home_scale = 0
        
        # UI 更新
        self._update_sim_playpause_btn('playing')
        self._set_flat_button_state(self.sim_stop_btn, tk.NORMAL)
        
        # Redirect stdout/stderr to thread-safe GUI callback
        self.orig_stdout = sys.stdout
        self.orig_stderr = sys.stderr
        sys.stdout = SimTextRedirector(self.write_sim_log_threadsafe)
        sys.stderr = SimTextRedirector(self.write_sim_log_threadsafe)
        self.redirect_logging_handlers(sys.stderr)
        
        # Clear log and latest EEW
        self.sim_log_text.config(state=tk.NORMAL)
        self.sim_log_text.delete("1.0", tk.END)
        self.sim_log_text.config(state=tk.DISABLED)
        
        self.sim_latest_eew = None
        self.update_sim_eew_text_widget()
        
        # Reset toggle view to map; hide map frame until first NIED image arrives
        self.sim_showing_eew = False
        self.sim_eew_text_frame.pack_forget()
        self.sim_map_frame.pack_forget()
        self.sim_map_zoom_frame.pack_forget()
        self.sim_toggle_btn.config(text="EEW原文表示")
        self.sim_map_zoomed = False

        # Reset visualizer state and register callback to show map on first image
        self.sim_viz.latest_pil_image = None
        self.sim_viz.secondary_viz = None
        self.sim_viz.on_first_image_callback = lambda: self.sim_map_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Hide default label, show paned window
        self.sim_default_label.pack_forget()
        self.sim_paned_window.pack(fill=tk.BOTH, expand=True)
        self.root.update_idletasks()
        try:
            self.sim_paned_window.sash_place(0, self.sim_view.winfo_width() // 2, 0)
        except Exception:
            pass

        # Start queue processing
        self.sim_gui_queue = queue.Queue()
        self.process_sim_gui_queue()

        # 準備完了ログをボタン押下直後にメインスレッドから書き込む
        self.write_sim_log_gui(f"[シミュレーション準備完了]: {target_group.display_dt}")
        self.write_sim_log_gui(f"再生速度: {speed}x")
        if input_lat != HOME_LAT or input_lon != HOME_LON:
            self.write_sim_log_gui(f"エリア補正: 緯度={input_lat:.4f}° 経度={input_lon:.4f}° (HOME: {HOME_LAT}, {HOME_LON})")

        # 起動音を再生
        if not self.sim_mute_var.get():
            try:
                util_audio_player.play_audio(os.path.join(SIM_VOICE_DIR, "eq_v_sim_start.wav"), volume=90)
            except Exception:
                pass

        def on_finish():
            # stdout/stderr の復元はどのスレッドからでも安全
            if hasattr(self, 'orig_stdout'):
                sys.stdout = self.orig_stdout
            if hasattr(self, 'orig_stderr'):
                sys.stderr = self.orig_stderr
                self.redirect_logging_handlers(self.orig_stderr)

            # stop_simulation() が既にクリーンアップ済み、または別のシミュレーションが開始済み → スキップ
            if getattr(self, 'sim_stop_flag', False) or self.sim_session_id != session_id:
                return

            # 正常終了時のみ: tkinter 操作はメインスレッドにスケジュール
            def _do_finish():
                if self.sim_session_id != session_id:
                    return
                self.sim_running = False
                self.sim_paused = False
                self._update_sim_playpause_btn('idle')
                self._set_flat_button_state(self.sim_stop_btn, tk.DISABLED)
                self.sim_map_zoomed = False
                self.sim_viz.secondary_viz = None
                self.sim_map_zoom_frame.pack_forget()
                self.sim_paned_window.pack_forget()
                self.sim_default_label.pack(expand=True)
                # ホーム座標をデフォルトにリセット
                for viz in [self.sim_viz, self.sim_viz_zoom]:
                    viz.clear_center_override()
                self.eq_system.sim_override_home_cx = None
                self.eq_system.sim_override_home_cy = None
                self._play_sim_end_audio()

            self.root.after(0, _do_finish)
        
        def _launch_thread():
            if getattr(self, 'sim_stop_flag', False) or self.sim_session_id != session_id:
                return
            self.sim_thread = threading.Thread(
                target=run_simulation_engine,
                args=(self.eq_system, target_group, speed, [self.sim_viz], on_finish),
                kwargs={
                    'log_callback': self.write_sim_log_threadsafe,
                    'eew_callback': self.handle_sim_eew_threadsafe,
                    'is_borehole_func': self.sim_borehole_var.get
                },
                daemon=True
            )
            self.sim_thread.start()

        def _countdown(n):
            if getattr(self, 'sim_stop_flag', False) or self.sim_session_id != session_id:
                return
            self.write_sim_log_gui(f"\r{n}秒後に開始します...")
            if n > 1:
                self.root.after(1000, lambda: _countdown(n - 1))
            else:
                self.root.after(1000, _launch_thread)

        _countdown(5)
    
    def pause_simulation(self):
        """一時停止/再開"""
        if not self.sim_running:
            return
        
        self.sim_paused = not self.sim_paused
        self.eq_system.sim_paused = self.sim_paused
        
        if self.sim_paused:
            self._update_sim_playpause_btn('paused')
            # Stop active audio announcement when pausing
            with self.eq_system.audio_lock:
                self.eq_system.is_playing = False
                self.eq_system.current_playing_priority = 0
                self.eq_system._stop_current_audio()
        else:
            self._update_sim_playpause_btn('playing')
    
    def _on_real_alert_start(self, data):
        """バックグラウンドスレッドから呼ばれる: 実EEWアラート発生時の割り込み判定"""
        # 地震発生中表示: NIED側のアラート開始通知(~1秒遅れ)を待たず、
        # EEW受信の時点で即座に開始する(未開始なら開始、開始済みなら内容を更新)。
        # 終了はNIED側のアラート期間終了(動画保存と同じタイミング)に委ねる。
        # (タブ切替の割り込み設定に関わらず、常に反映する)
        hypo3 = self._hypo3(data.hypocenter_name)
        max_int_str = self._format_max_int_display(data.max_intensity)

        def _apply_alert_display():
            if self._alert_active_start_ts is None:
                self._start_alert_display(hypo3=hypo3, max_int_str=max_int_str, is_test=False)
            else:
                self._update_alert_info(hypo3, max_int_str)
        self.root.after(0, _apply_alert_display)

        if data.event_id == self._last_interrupt_event_id:
            return
        settings = self.load_settings()
        if not settings.get("eew_alert", {}).get("interrupt_enabled", True):
            return
        if self.state not in ("logviewer", "simulation"):
            return
        self._last_interrupt_event_id = data.event_id
        self.root.after(0, self._do_alert_interrupt)

    def _do_alert_interrupt(self):
        """メインスレッド: log/simを停止してmonitorタブへ切り替える"""
        if self.state == "simulation" and self.sim_running:
            self.stop_simulation(show_msg=False)
        if self.state == "logviewer":
            self.play_session_counters["left"] += 1
            self.play_session_counters["right"] += 1
        self.notebook.select(0)

    def _play_sim_end_audio(self):
        import eq_config.eq_config as _cfg
        if not getattr(_cfg, "PLAY_SIMULATION_END_AUDIO", True):
            return
        if self.sim_mute_var.get():
            return
        try:
            import threading
            target_file = os.path.join(SIM_VOICE_DIR, 'eq_v_sim_end.wav')
            threading.Thread(target=util_audio_player.play_audio, args=(target_file,), kwargs={'volume': 90}, daemon=True).start()
        except Exception:
            pass

    def stop_simulation(self, show_msg=True):
        """中断"""
        if not self.sim_running:
            return
        
        # Restore stdout/stderr
        if hasattr(self, 'orig_stdout'):
            sys.stdout = self.orig_stdout
        if hasattr(self, 'orig_stderr'):
            sys.stderr = self.orig_stderr
            self.redirect_logging_handlers(self.orig_stderr)

        self.sim_stop_flag = True
        self.sim_running = False
        self.sim_paused = False
        self.sim_map_zoomed = False
        
        # Set EqSystem flags and stop active audio
        self.eq_system.sim_stop_flag = True
        self.eq_system.sim_paused = False
        with self.eq_system.audio_lock:
            self.eq_system.is_playing = False
            self.eq_system.current_playing_priority = 0
            self.eq_system._stop_current_audio()
        with self.eq_system.lock:
            self.eq_system.active_countdown_sequence = []
            self.eq_system.active_countdown_data = None
        
        # UI 更新
        self._update_sim_playpause_btn('idle')
        self._set_flat_button_state(self.sim_stop_btn, tk.DISABLED)
        
        # ホーム座標をデフォルトに戻す
        for viz in [self.sim_viz, self.sim_viz_zoom]:
            viz.clear_center_override()
        self.eq_system.sim_override_home_cx = None
        self.eq_system.sim_override_home_cy = None

        # Hide paned window, show default label
        self.sim_map_zoomed = False
        self.sim_viz.secondary_viz = None
        self.sim_map_zoom_frame.pack_forget()
        self.sim_paned_window.pack_forget()
        self.sim_default_label.pack(expand=True)
        self._play_sim_end_audio()

        if show_msg:
            messagebox.showinfo("シミュレーション", "シミュレーションを中断しました。")

    def _on_sim_list_select(self, event):
        if not hasattr(self, 'current_sim_event_groups'):
            return
        sel = self.sim_event_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self.current_sim_event_groups):
            return
        group = self.current_sim_event_groups[idx]
        if getattr(group, 'has_borehole', False):
            self.sim_toggle_borehole_btn.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=2)
        else:
            self.sim_toggle_borehole_btn.pack_forget()

    def _on_toggle_borehole_sim(self, event=None):
        is_borehole = self.sim_borehole_var.get()
        
        # Redraw current frame immediately if simulation is loaded
        if hasattr(self.eq_system, 'sim_current_ev_image_path') and self.eq_system.sim_current_ev_image_path:
            base_path = self.eq_system.sim_current_ev_image_path
            current_path = base_path
            if is_borehole:
                import glob
                b_dir = os.path.join(os.path.dirname(os.path.dirname(current_path)), "borehole")
                base = os.path.basename(current_path)
                parts = base.split('_lv')
                if len(parts) >= 2:
                    prefix = parts[0].replace('eqlog_s_', 'eqlog_b_')
                    pattern = os.path.join(b_dir, prefix + "*.png")
                    matches = glob.glob(pattern)
                    if matches:
                        current_path = matches[0]
                    else:
                        current_path = current_path.replace("\\surface\\eqlog_s_", "\\borehole\\eqlog_b_").replace("/surface/eqlog_s_", "/borehole/eqlog_b_")
                else:
                    current_path = current_path.replace("\\surface\\eqlog_s_", "\\borehole\\eqlog_b_").replace("/surface/eqlog_s_", "/borehole/eqlog_b_")
            try:
                from PIL import Image
                img = Image.open(current_path)
                for viz in [self.sim_viz, self.sim_viz_zoom]:
                    viz.push_image(
                        img.copy(), 
                        getattr(self.eq_system, 'sim_current_time_str', ""), 
                        getattr(self.eq_system, 'sim_current_intensity', 0.0),
                        countdown_sec=getattr(self.eq_system, 'sim_current_countdown_sec', None),
                        is_custom_prediction=getattr(self.eq_system, 'sim_current_is_custom', False),
                        predicted_home_scale=getattr(self.eq_system, 'sim_current_predicted_home_scale', 0)
                    )
            except Exception as e:
                print(f"[Sim] Borehole redraw failed: {e}")

    def _toggle_sim_mute(self):
        self.sim_mute_var.set(not self.sim_mute_var.get())
        if self.sim_mute_var.get():
            self.sim_mute_btn.config(text="音声OFF", bg="#c0392b", fg="white")
            self.sim_mute_btn.default_bg = "#c0392b"
            self.sim_mute_btn.hover_bg = "#a93226"
        else:
            self.sim_mute_btn.config(text="音声ON", bg="#27ae60", fg="white")
            self.sim_mute_btn.default_bg = "#27ae60"
            self.sim_mute_btn.hover_bg = "#219a52"
        if self.sim_running:
            self.eq_system.sim_mute = self.sim_mute_var.get()

    def _on_sim_playpause_click(self):
        if not self.sim_running:
            self.start_simulation()
        elif self.sim_paused:
            self.start_simulation()  # resume
        else:
            self.pause_simulation()

    def _update_sim_playpause_btn(self, sim_state: str):
        """sim_state: 'idle' | 'playing' | 'paused'"""
        btn = self.sim_playpause_btn
        if sim_state == 'idle':
            btn.config(text="▶ 再生", bg="#2a6496", fg="white", cursor="hand2", state=tk.NORMAL)
            btn.default_bg = "#2a6496"
            btn.hover_bg = "#357ebd"
            self.sim_status_label.config(text="●", fg="#00ff00")
        elif sim_state == 'playing':
            btn.config(text="一時停止", bg="#555555", fg="white", cursor="hand2", state=tk.NORMAL)
            btn.default_bg = "#555555"
            btn.hover_bg = "#666666"
            self.sim_status_label.config(text="●", fg="#ffff00")
        elif sim_state == 'paused':
            btn.config(text="▶ 再開", bg="#aa6600", fg="white", cursor="hand2", state=tk.NORMAL)
            btn.default_bg = "#aa6600"
            btn.hover_bg = "#cc7700"
            self.sim_status_label.config(text="●", fg="#ff9900")

    def _toggle_sim_map_zoom(self):
        """NIEDモニタ青枠クリックでクロップ拡大ビュー/フルマップをトグル"""
        if not self.sim_running:
            return
        if self.sim_map_zoomed:
            # 戻す: クロップビューを隠してフルマップを表示
            self.sim_map_zoom_frame.pack_forget()
            self.sim_viz.secondary_viz = None
            if not self.sim_showing_eew:
                self.sim_map_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            self.sim_map_zoomed = False
        else:
            # 拡大: フルマップを隠してクロップビューを表示
            self.sim_map_frame.pack_forget()
            self.sim_map_zoom_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            self.sim_viz.secondary_viz = self.sim_viz_zoom
            # 現在表示中の画像をすぐにクロップビューへ転送
            if self.sim_viz.last_raw_image is not None:
                self.sim_viz_zoom.push_image(
                    self.sim_viz.last_raw_image.copy(),
                    self.sim_viz.last_timestamp or "",
                    self.sim_viz.last_max_intensity if self.sim_viz.last_max_intensity is not None else -3.0,
                    getattr(self.sim_viz, 'current_countdown_sec', None),
                    getattr(self.sim_viz, 'current_is_custom_prediction', False),
                    getattr(self.sim_viz, 'current_predicted_home_scale', 0)
                )
            self.sim_map_zoomed = True

    def toggle_sim_right_view(self):
        if not hasattr(self, 'sim_showing_eew'):
            self.sim_showing_eew = False
            
        self.sim_showing_eew = not self.sim_showing_eew
        if self.sim_showing_eew:
            # EEW表示へ: マップ系フレームをすべて隠す（ズームも解除）
            self.sim_map_frame.pack_forget()
            self.sim_map_zoom_frame.pack_forget()
            self.sim_viz.secondary_viz = None
            self.sim_map_zoomed = False
            self.sim_eew_text_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            self.sim_toggle_btn.config(text="モニタ画像表示")
            self.update_sim_eew_text_widget()
        else:
            self.sim_eew_text_frame.pack_forget()
            self.sim_map_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            self.sim_toggle_btn.config(text="EEW原文表示")

    def update_sim_eew_text_widget(self):
        self.sim_eew_text.config(state=tk.NORMAL)
        self.sim_eew_text.delete("1.0", tk.END)
        if hasattr(self, 'sim_latest_eew') and self.sim_latest_eew:
            try:
                raw_json = json.loads(self.sim_latest_eew.original_text)
                pretty_json = json.dumps(raw_json, indent=4, ensure_ascii=False)
                self.sim_eew_text.insert(tk.END, pretty_json)
            except Exception as e:
                self.sim_eew_text.insert(tk.END, f"EEWデータパース失敗: {e}\n\n{self.sim_latest_eew.original_text}")
        else:
            self.sim_eew_text.insert(tk.END, "最新の緊急地震速報(EEW)データはありません。")
        self.sim_eew_text.config(state=tk.DISABLED)

    def write_sim_log_threadsafe(self, msg):
        self.sim_gui_queue.put(("log", msg))
        
    def handle_sim_eew_threadsafe(self, data):
        self.sim_gui_queue.put(("eew", data))

    def redirect_logging_handlers(self, stream):
        """loggingのStreamHandler出力を指定のstream(SimTextRedirector等)にリダイレクトする"""
        import logging
        # ルートロガーのハンドラを更新
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if type(handler) is logging.StreamHandler:
                handler.setStream(stream)
        # 登録済みの全ロガーのハンドラを更新
        for logger_name in logging.Logger.manager.loggerDict:
            logger = logging.getLogger(logger_name)
            if hasattr(logger, "handlers"):
                for handler in logger.handlers:
                    if type(handler) is logging.StreamHandler:
                        handler.setStream(stream)

    def process_sim_gui_queue(self):
        if not self.sim_running:
            return
        try:
            while True:
                msg_type, payload = self.sim_gui_queue.get_nowait()
                if msg_type == "log":
                    self.write_sim_log_gui(payload)
                elif msg_type == "eew":
                    self.handle_sim_eew_gui(payload)
        except queue.Empty:
            pass
        finally:
            self.root.after(30, self.process_sim_gui_queue)

    def write_sim_log_gui(self, text):
        self.sim_log_text.config(state=tk.NORMAL)
        if text.startswith('\r'):
            text = text.lstrip('\r')
            last_line_index = self.sim_log_text.index("end-1c linestart")
            self.sim_log_text.delete(last_line_index, "end-1c")
            self.sim_log_text.insert("end-1c", text)
        else:
            last_line_index = self.sim_log_text.index("end-1c linestart")
            last_line_text = self.sim_log_text.get(last_line_index, "end-1c")
            if last_line_text.startswith("[SIM TIME]"):
                # Insert the normal log ABOVE the [SIM TIME] line
                self.sim_log_text.insert(last_line_index, text + "\n")
            else:
                if self.sim_log_text.get("1.0", "end-1c"):
                    current_val = self.sim_log_text.get("1.0", "end-1c")
                    if not current_val.endswith('\n'):
                        self.sim_log_text.insert("end-1c", "\n")
                self.sim_log_text.insert("end-1c", text + "\n")
        self.sim_log_text.see("end")
        self.sim_log_text.config(state=tk.DISABLED)

    def handle_sim_eew_gui(self, data):
        self.sim_latest_eew = data
        self.update_sim_eew_text_widget()

    def _toggle_pip_pref(self, name):
        """monitorタブのトグルボタン: 他画面遷移時のPiP表示設定を切り替える"""
        if name == "full":
            self.pip_toggle_full_var.set(not self.pip_toggle_full_var.get())
            on = self.pip_toggle_full_var.get()
            self.pip_toggle_full_btn.config(
                text="Full ✓" if on else "Full ✗",
                bg="#2a6496" if on else "#555555"
            )
        else:
            self.pip_toggle_crop_var.set(not self.pip_toggle_crop_var.get())
            on = self.pip_toggle_crop_var.get()
            self.pip_toggle_crop_btn.config(
                text="Crop ✓" if on else "Crop ✗",
                bg="#2a6496" if on else "#555555"
            )

    _TEST_INTENSITY_CHOICES = ["1", "2", "3", "4", "5弱", "5強", "6弱", "6強", "7"]

    def insert_test_log(self):
        import logging
        import random
        logger = logging.getLogger("TestLog")
        logger.info("システムは正常に稼働しています。")
        logger.warning("これは警告メッセージの表示テストです。")
        logger.error("通信エラーをシミュレートしたテストログです。")
        logger.info("EEW Alert: 千葉県北西部 M5.0 最大震度5弱")

        # 実際のアラート表示中はテストを行わない(表示の競合・上書きを防ぐ)
        if self._alert_active_start_ts is not None and not self._alert_is_test:
            logger.info("実際のアラート表示中のため、地震発生中表示のテストをスキップしました。")
            return

        # 地震発生中表示(赤バナー)を約5秒間表示し、終了後に地震カードの表示テストを行う
        # 震源地は実際のイベントと混同しないよう固定で「テスト」、震度はランダム
        test_max_int = random.choice(self._TEST_INTENSITY_CHOICES)
        self._start_alert_display(hypo3="テスト", max_int_str=test_max_int, is_test=True)
        self._test_alert_after_id = self.root.after(5000, lambda: self._finish_test_alert_display(test_max_int))

    def _finish_test_alert_display(self, test_max_int):
        self._test_alert_after_id = None
        # 表示中に実際のアラートへ切り替わっていた場合は何もしない
        # (実アラートの終了は _on_alert_period_end 側が処理する)
        if not self._alert_is_test:
            return
        self._stop_alert_display()
        self.insert_test_card(max_int=test_max_int)

    def clear_monitor_log(self):
        self.monitor_log_text.config(state=tk.NORMAL)
        self.monitor_log_text.delete("1.0", tk.END)
        self.monitor_log_text.config(state=tk.DISABLED)
        # ログのクリアに合わせて地震カードも全消去する
        if hasattr(self, "monitor_card_stack"):
            self.monitor_card_stack.clear()

    def insert_test_card(self, max_int=None):
        """PC 表示テスト用: サンプルの地震カードをスタックに追加する

        震源地は実際のイベントと混同しないよう固定で「テスト」とする。
        max_int 省略時はランダムに選択する。
        """
        import datetime
        import random
        now = datetime.datetime.now()
        if max_int is None:
            max_int = random.choice(self._TEST_INTENSITY_CHOICES)
        # 表示テストは短時間に連打されうるため、分単位の id だと同一 id とみなされて
        # 上書き(push側の重複除去)されてしまう。テスト連打時も別カードとして
        # 確実にスタックされるよう、押下ごとに連番を付与して id を一意にする。
        self._test_card_seq = getattr(self, "_test_card_seq", 0) + 1
        gid = f"{now.strftime('%Y%m%d_%H%M')}_{self._test_card_seq}"
        self.monitor_card_stack.push({
            "id": gid,
            "dt": now.strftime("%Y/%m/%d %H:%M"),
            "hypo": format_hypocenter("テスト"),
            "max_int": max_int,
        })

    def _push_event_card(self, group_id: str):
        """
        アラート終了通知を受け、ログを再スキャンして該当イベントの
        カードを生成・表示する (メインスレッドで実行)

        Args:
            group_id: NIED アラート開始時刻由来の識別子 ("%Y%m%d_%H%M")
        """
        import datetime
        try:
            self.refresh_log_viewer()
        except Exception as e:
            print(f"[Card] ログ再スキャンに失敗: {e}")
        group = self._find_event_group(group_id)
        if group is None:
            print(f"[Card] 該当イベントが見つかりません: {group_id}")
            return
        try:
            dt_obj = datetime.datetime.strptime(group.id, "%Y%m%d_%H%M")
            fmt_dt = dt_obj.strftime("%Y/%m/%d %H:%M")
        except Exception:
            fmt_dt = group.id
        self.monitor_card_stack.push({
            "id": group.id,
            "dt": fmt_dt,
            "hypo": format_hypocenter(group.hypocenter),
            "max_int": group.max_intensity,
        })

    def _find_event_group(self, group_id: str):
        """id 完全一致、無ければ前後 60 秒許容で最善一致の EventGroup を返す"""
        import datetime
        groups = getattr(self, "all_event_groups", None)
        if not groups:
            return None
        for g in groups:
            if g.id == group_id:
                return g
        # 最善一致 (EEW と NIED の記録時刻に最大 1 分のズレがあるため)
        try:
            target_dt = datetime.datetime.strptime(group_id, "%Y%m%d_%H%M")
        except ValueError:
            return None
        best, best_delta = None, None
        for g in groups:
            try:
                g_dt = datetime.datetime.strptime(g.id, "%Y%m%d_%H%M")
            except ValueError:
                continue
            delta = abs((g_dt - target_dt).total_seconds())
            if delta <= 60 and (best_delta is None or delta < best_delta):
                best, best_delta = g, delta
        return best

    def _on_nied_alert_start(self, group_id: str, max_intensity):
        """NIED 監視スレッドから呼ばれる: アラート期間(動画保存と同じ期間)の開始通知

        表示テスト中に実際のアラートが発生した場合は、テストの終了処理(5秒後)を
        キャンセルし、本物のアラート表示へ切り替える(実アラートが常に優先)。
        """
        max_int_str = self._format_max_int_display(max_intensity)

        def _do():
            if self._test_alert_after_id is not None:
                try:
                    self.root.after_cancel(self._test_alert_after_id)
                except Exception:
                    pass
                self._test_alert_after_id = None
            if self._alert_active_start_ts is None or self._alert_is_test:
                # 未開始、またはテスト表示中 -> 本物のアラート表示として(再)開始する
                self._start_alert_display(hypo3="---", max_int_str=max_int_str, is_test=False)
            else:
                # 既にEEW側の通知で開始済み: 開始時刻/震源地は保持し、最大震度だけ反映する
                self._update_alert_info(max_int_str=max_int_str)

        try:
            self.root.after(0, _do)
        except Exception:
            pass

    def _on_alert_period_end(self, group_id: str):
        """NIED 監視スレッドから呼ばれる: アラート期間終了通知をメインスレッドへ委譲

        地震発生中表示(赤バナー)を終了させてから地震カードを表示する。
        """
        def _do(gid=group_id):
            self._stop_alert_display()
            self._push_event_card(gid)
        try:
            self.root.after(0, _do)
        except Exception:
            pass

    def _on_monitor_card_confirm(self, card: dict):
        """カードの [確認] 押下: log タブを開き該当イベントを最善一致で選択する"""
        group_id = card.get("id")
        # log タブへ切り替え (index 1)
        try:
            self.notebook.select(self.tab_log)
        except Exception:
            self.notebook.select(1)
        try:
            self.refresh_log_viewer()
        except Exception:
            pass
        group = self._find_event_group(group_id)
        if group is None:
            return
        # フィルタを解除して確実に一覧へ出す
        try:
            self.log_filter_source_cb.set("すべて")
            self.log_filter_hypo_entry.delete(0, tk.END)
            self.update_log_list()
        except Exception:
            pass
        # current_event_groups (表示順) から該当行を特定して選択
        for idx, g in enumerate(getattr(self, "current_event_groups", [])):
            if g.id == group.id:
                self.log_event_listbox.selection_clear(0, tk.END)
                self.log_event_listbox.selection_set(idx)
                self.log_event_listbox.see(idx)
                self.on_log_event_select(None)
                break

    def _setup_gui_log_handler(self):
        """全ロガーにGUI表示用ハンドラを追加する"""
        import logging
        app = self  # シミュレーション実行中フラグの参照用

        class _QueueLogHandler(logging.Handler):
            def __init__(self, log_queue):
                super().__init__()
                self.log_queue = log_queue

            def emit(self, record):
                # シミュレーションスレッドからのログはシステムログ（モニタ画面）から除外する
                # （これにより、シミュ中であっても別スレッドからの本物のEEWログは表示されるようになる）
                curr_thread = threading.current_thread()
                sim_thread = getattr(app, 'sim_thread', None)
                if sim_thread and curr_thread == sim_thread:
                    return
                # さらに、シミュレーション停止処理直後など、メインスレッドから呼ばれるがシミュレーション由来のもの
                # もしある場合はここで除外する（現時点では主にsim_threadからのログが混入する）
                try:
                    time_str = self.formatter.formatTime(record, '%H:%M:%S')
                    msg = record.getMessage()
                    self.log_queue.put({
                        "time": time_str,
                        "level": record.levelname,
                        "module": record.module,
                        "msg": msg
                    })
                except Exception:
                    pass

        handler = _QueueLogHandler(self._log_queue)
        handler.setFormatter(logging.Formatter())

        # ルートロガーにハンドラを追加して全子ロガーの出力をキャプチャ
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)

    def _poll_log_queue(self):
        """キューからログを取り出してmonitor画面のTextウィジェットに表示する"""
        MAX_LINES = 200  # 最大保持行数
        try:
            batch_count = 0
            while not self._log_queue.empty() and batch_count < 50:
                log_data = self._log_queue.get_nowait()
                self.monitor_log_text.config(state=tk.NORMAL)
                
                time_str = log_data["time"]
                level = log_data["level"]
                module_name = log_data["module"][:14].ljust(14)
                msg = log_data["msg"]

                level_tag = level if level in ("ERROR", "WARNING", "INFO", "DEBUG") else "INFO"
                
                # キーワードに応じてメッセージ部分を強調
                msg_tag = "MSG"
                if "EEW" in msg or "Alert" in msg or "データ投入" in msg:
                    msg_tag = "HIGHLIGHT"
                
                # 各要素を色分けして挿入
                self.monitor_log_text.insert(tk.END, f"{time_str} ", "TIME")
                self.monitor_log_text.insert(tk.END, "| ", "TIME")
                self.monitor_log_text.insert(tk.END, f"{level:<7} ", level_tag)
                self.monitor_log_text.insert(tk.END, f"| {module_name} | ", "MODULE")
                self.monitor_log_text.insert(tk.END, f"{msg}\n", msg_tag)

                # 行数上限を超えたら古い行を削除
                line_count = int(self.monitor_log_text.index('end-1c').split('.')[0])
                if line_count > MAX_LINES:
                    self.monitor_log_text.delete("1.0", f"{line_count - MAX_LINES}.0")

                self.monitor_log_text.see(tk.END)
                self.monitor_log_text.config(state=tk.DISABLED)
                batch_count += 1
        except Exception:
            pass
        finally:
            self.root.after(250, self._poll_log_queue)

    def reset_log_viewer_state(self):
        # Reset visibility flags
        self.log_pane_left_visible = True
        self.log_pane_right_visible = False
        
        # Reset active panel
        self.log_target_panel_var.set("left")
        self.log_viewer_text = self.log_text_1
        
        # Clear paths and images
        self.selected_file_path_1 = None
        self.selected_file_path_2 = None
        self.current_log_image_1 = None
        self.current_log_image_2 = None
        
        # Clear text widgets
        self.render_file_path(None, "left")
        self.render_file_path(None, "right")
        
        # Reset PanedWindow panes to show only Left
        self.log_paned_window.forget(self.log_pane_left)
        self.log_paned_window.forget(self.log_pane_right)
        self.log_paned_window.add(self.log_pane_left, minsize=100)
        
        # Reset sash state
        self.log_sash_initialized = False
        
        # Update headers
        self.update_log_panel_headers()

    def _refresh_config_scrollregion(self):
        """設定タブのスクロール範囲とインナーフレーム幅を実レイアウトに合わせて再計算する。"""
        try:
            self.config_inner_frame.update_idletasks()
            self.config_canvas.configure(scrollregion=self.config_canvas.bbox("all"))
            self.config_canvas.itemconfig(self._config_win_id, width=self.config_canvas.winfo_width())
        except Exception:
            pass

    def on_tab_changed(self, event):
        # Stop simulation when transitioning away from the sim tab
        if getattr(self, 'state', None) == "simulation" and self.notebook.index(self.notebook.select()) != 2:
            if self.sim_running:
                self.stop_simulation()

        # Reset log viewer state when transitioning away from the log tab
        if getattr(self, 'state', None) == "logviewer":
            self.reset_log_viewer_state()

        sel = self.notebook.index(self.notebook.select())
        if sel == 0:
            self.state = "home"
            # Reset drag offsets only when returning to the monitor tab
            self.pip_relx_full = None
            self.pip_rely_full = None
            self.pip_relx_crop = None
            self.pip_rely_crop = None
        elif sel == 1:
            self.state = "logviewer"
        elif sel == 2:
            self.state = "simulation"
        elif sel == 3:
            self.state = "config"
            # 設定タブは非表示のまま構築されるため、表示された時点で（レイアウト確定後に）
            # スクロール範囲を再計算する。これを行わないとサムがトラック全体を占め、
            # 「バーが無い/途中で止まる」状態になる。
            self.after_idle(self._refresh_config_scrollregion)

        # Restore PiP visibility on any tab transition (respect toggle preferences)
        self.pip_visible_full = self.pip_toggle_full_var.get()
        self.pip_visible_crop = self.pip_toggle_crop_var.get()

        self.apply_layout()

    def on_map_click(self, name):
        """マップクリック時の処理：フォーカスを切り替える"""
        if self.state == "home":
            if name == "full":
                self.state = "focus_full"
            elif name == "crop":
                self.state = "focus_crop"
        elif self.state == "focus_full":
            if name == "crop":  # clicked the PiP
                self.state = "home"
        elif self.state == "focus_crop":
            if name == "full":  # clicked the PiP
                self.state = "home"
        self.apply_layout()

    def apply_layout(self):
        """現在の状態に応じてレイアウトを適用"""
        self.hide_all()

        if self.state == "home":
            self.viz_full.set_legend_visible(True)
            self.viz_crop.set_legend_visible(False)
            self.adjust_home_layout()
            self.full_frame.lift()
            self.crop_frame.lift()

        elif self.state == "focus_full":
            self.viz_full.set_legend_visible(True)
            self.viz_crop.set_legend_visible(False)
            self.full_frame.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
            self.place_pip("crop")

        elif self.state == "focus_crop":
            self.viz_crop.set_legend_visible(True)
            self.viz_full.set_legend_visible(False)
            self.crop_frame.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
            self.place_pip("full")

        elif self.state == "simulation":
            self.viz_full.set_legend_visible(False)
            self.viz_crop.set_legend_visible(False)
            self.sim_view.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
            self.place_pip("full")
            self.place_pip("crop")

        elif self.state == "config":
            self.viz_full.set_legend_visible(False)
            self.viz_crop.set_legend_visible(False)
            self.misc_view.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
            self.place_pip("full")
            self.place_pip("crop")

        elif self.state == "logviewer":
            self.viz_full.set_legend_visible(False)
            self.viz_crop.set_legend_visible(False)
            self.json_view.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
            self.place_pip("full")
            self.place_pip("crop")

    def hide_all(self):
        self.sim_view.place_forget()
        self.misc_view.place_forget()
        self.json_view.place_forget()
        self.full_frame.place_forget()
        self.crop_frame.place_forget()
        self.full_frame.pack_forget()
        self.crop_frame.pack_forget()
        self.hide_close_btn("full")
        self.hide_close_btn("crop")

    def adjust_log_panes(self, event=None):
        if getattr(self, 'log_sash_initialized', False):
            return
        w = self.json_view.winfo_width()
        if w > 200:
            try:
                self.log_paned_window.sash_place(0, w // 2, 0)
                self.log_sash_initialized = True
            except Exception:
                pass

    def adjust_home_layout(self):
        """Monitor tab のホームレイアウト調整"""
        if self.state != "home":
            return
        w = self.right_frame.winfo_width()
        h = self.right_frame.winfo_height()
        if w < 10 or h < 10:
            w, h = 490, 460

        # Load dynamic settings
        settings = self.load_settings()
        nied_settings = settings.get("nied_monitor", {})
        legend_w = nied_settings.get("legend_width", 35)

        # Non-square dynamic aspect ratio layout to eliminate margins
        crop_w = h
        full_w = int(h * 0.88) + legend_w
        req_w = crop_w + full_w

        if w < req_w:
            scale = w / req_w
            crop_w = max(10, int(h * scale))
            full_w = w - crop_w
            crop_h = crop_w
            full_h = crop_w
            y_offset = (h - crop_h) // 2
            x_offset = 0
        else:
            crop_h = h
            full_h = h
            y_offset = 0
            x_offset = w - req_w

        self.crop_frame.place(x=x_offset, y=y_offset, width=crop_w, height=crop_h)
        self.full_frame.place(x=x_offset + crop_w, y=y_offset, width=full_w, height=full_h)

    def _create_flat_button(self, parent, text, command, bg="#2a6496", fg="white", hover_bg="#357ebd", font=("Meiryo", 8, "bold"), padx=8, pady=3):
        btn = tk.Label(
            parent, text=text,
            bg=bg, fg=fg,
            font=font, cursor="hand2",
            padx=padx, pady=pady, relief="flat"
        )
        # Store properties for state management
        btn.default_bg = bg
        btn.hover_bg = hover_bg
        btn.default_fg = fg

        btn.bind("<Button-1>", lambda e: command() if btn.cget("state") != "disabled" else None)
        btn.bind("<Enter>", lambda e: btn.config(bg=btn.hover_bg) if btn.cget("state") != "disabled" else None)
        btn.bind("<Leave>", lambda e: btn.config(bg=btn.default_bg) if btn.cget("state") != "disabled" else None)
        return btn

    def _set_flat_button_state(self, btn, state):
        if state == tk.NORMAL:
            btn.config(state=tk.NORMAL, bg=btn.default_bg, fg=btn.default_fg, cursor="hand2")
        else: # DISABLED
            btn.config(state="disabled", bg="#444444", fg="#888888", cursor="arrow")

    def _on_toggle_borehole_log(self, event=None):
        # We don't manually toggle the var here anymore if it's called from SlideSwitch.command,
        # wait! SlideSwitch.toggle does `self.var.set(...)` then `self.command()`.
        # So var is already updated. We just need to trigger the UI changes.
        sel = self.log_file_listbox.curselection()
        sel_idx = sel[0] if sel else None

        self.on_log_type_change()

        if sel_idx is not None and sel_idx < self.log_file_listbox.size():
            self.log_file_listbox.selection_set(sel_idx)
            self.on_log_file_select(None)

    def _select_log_type(self, val):
        self.log_type_var.set(val)
        if val == "eew":
            self.btn_type_eew.config(bg="#2a6496")
            self.btn_type_eew.default_bg = "#2a6496"
            self.btn_type_eew.hover_bg = "#357ebd"
            self.btn_type_monitor.config(bg="#555555")
            self.btn_type_monitor.default_bg = "#555555"
            self.btn_type_monitor.hover_bg = "#666666"
            if hasattr(self, 'btn_create_video'):
                self.btn_create_video.pack_forget()
            if hasattr(self, 'btn_toggle_borehole_log'):
                self.btn_toggle_borehole_log.pack_forget()
            if hasattr(self, 'btn_show_tl'):
                self.btn_show_tl.pack(side=tk.RIGHT, padx=5)
        else:
            self.btn_type_eew.config(bg="#555555")
            self.btn_type_eew.default_bg = "#555555"
            self.btn_type_eew.hover_bg = "#666666"
            self.btn_type_monitor.config(bg="#2a6496")
            self.btn_type_monitor.default_bg = "#2a6496"
            self.btn_type_monitor.hover_bg = "#357ebd"
            if hasattr(self, 'btn_create_video'):
                self.btn_create_video.pack(side=tk.RIGHT)
            if hasattr(self, 'btn_toggle_borehole_log'):
                self.btn_toggle_borehole_log.pack(side=tk.RIGHT, padx=5)
            if hasattr(self, 'btn_show_tl'):
                self.btn_show_tl.pack_forget()
        self.on_log_type_change()

    def activate_panel(self, panel_name):
        self.log_target_panel_var.set(panel_name)
        if panel_name == "left":
            self.log_viewer_text = self.log_text_1
        else:
            self.log_viewer_text = self.log_text_2
        self.update_log_panel_headers()

    def update_log_panel_headers(self):
        self._update_header_layout("left")
        self._update_header_layout("right")

    def _update_header_layout(self, panel_name):
        active = self.log_target_panel_var.get()
        is_active = (active == panel_name)
        border_color = "#2a6496" if is_active else "#3c3f41"
        header_bg = "#3a3a3a" if is_active else "#2b2b2b"
        
        if panel_name == "left":
            pane = self.log_pane_left
            header = self.log_pane_left_header
            btn_close = self.btn_close_1
            btn_swap = self.btn_swap_1
            btn_split = self.btn_split_1
            btn_zoom_in = self.btn_zoom_in_1
            btn_zoom_out = self.btn_zoom_out_1
            visible = self.log_pane_left_visible
            other_visible = self.log_pane_right_visible
            path = self.selected_file_path_1
        else:
            pane = self.log_pane_right
            header = self.log_pane_right_header
            btn_close = self.btn_close_2
            btn_swap = self.btn_swap_2
            btn_split = self.btn_split_2
            btn_zoom_in = self.btn_zoom_in_2
            btn_zoom_out = self.btn_zoom_out_2
            visible = self.log_pane_right_visible
            other_visible = self.log_pane_left_visible
            path = self.selected_file_path_2
            
        pane.config(highlightbackground=border_color)
        header.config(bg=header_bg)
        
        # Update colors on header buttons
        for btn in [btn_close, btn_swap, btn_split, btn_zoom_in, btn_zoom_out]:
            btn.config(bg=header_bg)
            
        # First unpack everything
        btn_close.pack_forget()
        btn_swap.pack_forget()
        btn_split.pack_forget()
        btn_zoom_in.pack_forget()
        btn_zoom_out.pack_forget()
        
        # Pack only the appropriate ones in correct order
        if visible and other_visible:
            # Both visible: show close & swap, hide split
            btn_close.pack(side=tk.RIGHT, padx=2)
            btn_swap.pack(side=tk.RIGHT, padx=2)
        elif visible:
            # Only this visible: show split
            btn_split.pack(side=tk.RIGHT, padx=2)
            
        # Zoom buttons: show only if path is image/video
        show_zoom = path and path.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.mp4'))
        if show_zoom:
            btn_zoom_out.pack(side=tk.RIGHT, padx=2)
            btn_zoom_in.pack(side=tk.RIGHT, padx=2)

    def close_panel(self, panel_name):
        if panel_name == "left":
            if not self.log_pane_right_visible:
                return # Don't close the last panel
            self.log_paned_window.forget(self.log_pane_left)
            self.log_pane_left_visible = False
            self.activate_panel("right")
        else:
            if not self.log_pane_left_visible:
                return # Don't close the last panel
            self.log_paned_window.forget(self.log_pane_right)
            self.log_pane_right_visible = False
            self.activate_panel("left")

    def split_panel(self):
        self.log_paned_window.forget(self.log_pane_left)
        self.log_paned_window.forget(self.log_pane_right)
        
        self.log_paned_window.add(self.log_pane_left, minsize=100)
        self.log_paned_window.add(self.log_pane_right, minsize=100)
        
        self.log_pane_left_visible = True
        self.log_pane_right_visible = True
        
        self.log_sash_initialized = False
        self.adjust_log_panes()
        
        # Activate the currently set active panel
        self.activate_panel(self.log_target_panel_var.get())

    def swap_panels(self):
        # Swap paths
        self.selected_file_path_1, self.selected_file_path_2 = self.selected_file_path_2, self.selected_file_path_1
        # Swap zoom factors
        self.log_zoom_factor_1, self.log_zoom_factor_2 = self.log_zoom_factor_2, self.log_zoom_factor_1
        # Swap cached images
        self.current_log_image_1, self.current_log_image_2 = self.current_log_image_2, self.current_log_image_1
        
        # Re-render both panels
        self.render_file_path(self.selected_file_path_1, "left")
        self.render_file_path(self.selected_file_path_2, "right")

    def on_mousewheel_zoom(self, event, panel_name):
        if event.delta > 0:
            self.zoom_panel(panel_name, 1.05)
        elif event.delta < 0:
            self.zoom_panel(panel_name, 0.95)
        return "break"

    def on_pan_start(self, event, canvas):
        x = event.x_root - canvas.winfo_rootx()
        y = event.y_root - canvas.winfo_rooty()
        canvas.scan_mark(x, y)

    def on_pan_drag(self, event, canvas):
        x = event.x_root - canvas.winfo_rootx()
        y = event.y_root - canvas.winfo_rooty()
        canvas.scan_dragto(x, y, gain=1)

    def zoom_panel(self, panel_name, factor):
        if panel_name == "left":
            self.log_zoom_factor_1 = max(0.2, min(self.log_zoom_factor_1 * factor, 5.0))
        else:
            self.log_zoom_factor_2 = max(0.2, min(self.log_zoom_factor_2 * factor, 5.0))
            
        path = self.selected_file_path_1 if panel_name == "left" else self.selected_file_path_2
        if path:
            self.render_file_path(path, panel_name)

    def on_closing(self):
        """[X]ボタンが押されたときは、終了せずにウィンドウを非表示にするのみ"""
        self.root.withdraw()

    def start_ipc_server(self):
        """シングルインスタンスチェックおよび外部コマンド連携用のTCPソケットサーバーをスレッドで起動"""
        self.ipc_server_running = True
        self.ipc_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.ipc_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.ipc_socket.bind(('127.0.0.1', IPC_PORT))
            self.ipc_socket.listen(1)
            self.ipc_socket.settimeout(1.0)
        except Exception as e:
            # ポートがバインドできない場合はすでに起動しているとみなす（警告を出して処理は継続）
            print(f"[Warning] IPCポートのバインドに失敗しました (すでに実行中の可能性があります): {e}")
            return
            
        def ipc_server_loop():
            while self.ipc_server_running:
                try:
                    conn, addr = self.ipc_socket.accept()
                except socket.timeout:
                    continue
                except socket.error:
                    break
                
                try:
                    conn.settimeout(0.5)
                    data = conn.recv(1024).decode('utf-8').strip().lower()
                    if data:
                        # Tkinterのメインスレッドで安全にコマンドを処理
                        self.root.after(0, lambda cmd=data: self.handle_ipc_command(cmd))
                except Exception:
                    pass
                finally:
                    conn.close()
            
            try:
                self.ipc_socket.close()
            except Exception:
                pass

        threading.Thread(target=ipc_server_loop, daemon=True).start()

    def handle_ipc_command(self, cmd):
        """受信したIPCコマンドを処理する"""
        if cmd == "show":
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        elif cmd == "hide":
            self.root.withdraw()
        elif cmd == "toggle":
            if self.root.state() == "withdrawn":
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
            else:
                self.root.withdraw()
        elif cmd in ["stop", "exit"]:
            self.shutdown_app_completely()

    def shutdown_app_completely(self):
        """アプリケーションを完全に終了する（クリーンアップ処理含む）"""
        self.ipc_server_running = False
        if self.eq_system:
            self.eq_system.stop()
        self.play_session_counters["left"] += 1
        self.play_session_counters["right"] += 1
        self.root.destroy()

    def _run_git_pull(self):
        """終了/再起動前の git pull（Windows版では無効）

        ラズパイ版は本番機で main を自動追従するために実行するが、Windows版は
        配布用（frozen 時 PROJECT_ROOT が _MEIPASS の一時展開先でリポジトリでない）
        かつソース実行時も eq_app4win/ が git 管理外のミラーのため、実行しない。
        """
        return

    def _confirm_quit_app(self):
        """モニタ画面の[終了]ボタン: 確認のうえアプリを完全終了する。"""
        if messagebox.askyesno("アプリ終了",
                               "地震通知アプリを終了します。よろしいですか？",
                               icon="warning", parent=self.root):
            self._run_git_pull()
            self.shutdown_app_completely()

    def _confirm_restart_app(self):
        """モニタ画面の[再起動]ボタン: 確認のうえアプリを再起動する。"""
        if messagebox.askyesno("アプリ再起動",
                               "地震通知アプリを再起動します。よろしいですか？",
                               icon="warning", parent=self.root):
            self._run_git_pull()
            self.restart_app()

    def restart_app(self):
        """新しいインスタンスを起動してから、現在のインスタンスを完全終了する。

        シングルインスタンス機構(IPCポート)と競合しないよう、先に現インスタンスの
        ポートを解放し、新インスタンスは 'restart' 引数でポート解放を待ってから起動する。
        """
        import subprocess
        # 起動コマンド: frozen(exe)は実行ファイル単体、ソースは python + スクリプト
        if getattr(sys, 'frozen', False):
            args = [sys.executable, "restart"]
        else:
            args = [sys.executable, os.path.abspath(__file__), "restart"]
        # 先に現インスタンスのIPCポートを解放（新インスタンスがbindできるように）
        self.ipc_server_running = False
        try:
            self.ipc_socket.close()
        except Exception:
            pass
        # 新プロセスをデタッチ起動（親終了後も生存させる）
        try:
            kwargs = {}
            if sys.platform == 'win32':
                kwargs['creationflags'] = (subprocess.DETACHED_PROCESS
                                           | subprocess.CREATE_NEW_PROCESS_GROUP)
                kwargs['close_fds'] = True
            else:
                kwargs['start_new_session'] = True
            subprocess.Popen(args, **kwargs)
        except Exception as e:
            messagebox.showerror("再起動エラー",
                                 f"再起動に失敗しました:\n{e}", parent=self.root)
            return
        # 現インスタンスを完全終了
        self.shutdown_app_completely()

IPC_PORT = 58200

def check_and_send_ipc_command(command):
    """
    すでにアプリの別のインスタンスが起動しているか確認し、
    起動している場合はコマンドを送信して True を返す。
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(('127.0.0.1', IPC_PORT))
        s.sendall(command.encode('utf-8'))
        s.close()
        return True
    except socket.error:
        return False

def _instance_running():
    """既存インスタンスがIPCポートで待ち受けているかを確認する（コマンドは送らない）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect(('127.0.0.1', IPC_PORT))
        s.close()
        return True
    except socket.error:
        return False

def main():
    # コマンドライン引数の解析
    cmd = "toggle"  # 再度起動されたときのデフォルトは表示トグル切り替え
    relaunch = False  # 自身の再起動として起動されたか
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower().strip('-')
        if arg in ["show", "hide", "toggle", "stop", "exit"]:
            cmd = arg
        elif arg == "restart":
            relaunch = True

    if relaunch:
        # 再起動起動: 旧インスタンスがIPCポートを解放するまで待ってから通常起動する
        for _ in range(60):  # 最大約6秒
            if not _instance_running():
                break
            time.sleep(0.1)
    else:
        # 二重起動チェック
        if check_and_send_ipc_command(cmd):
            print(f"すでに実行中のプロセスにコマンド '{cmd}' を送信しました。本プロセスは終了します。")
            sys.exit(0)

        # 二重起動しておらず、コマンドが終了・非表示だった場合は何もしない
        if cmd in ["stop", "exit", "hide"]:
            print("アプリケーションは実行されていません。")
            sys.exit(0)

    root = tk.Tk()
    root.withdraw()
    splash = LoadingOverlay(root, title="システム起動", message="Earthquake Alert System を初期化しています...")
    splash.set_indeterminate()
    
    try:
        app = UnifiedEqApp(root, splash=splash)
        root.deiconify()
        splash.close()
        root.mainloop()
    except Exception as e:
        import traceback
        err_msg = traceback.format_exc()
        splash.close()
        root.deiconify()
        messagebox.showerror("起動エラー", f"アプリケーションの起動中にエラーが発生しました:\n\n{e}\n\n{err_msg}")
        sys.exit(1)

if __name__ == "__main__":
    main()
