import tkinter as tk
from tkinter import ttk
import json

def flatten_dict(d, parent_key='', sep='.'):
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)

def extract_warn_areas(d):
    res = {}
    if "WarnArea" in d and isinstance(d["WarnArea"], list):
        for area in d["WarnArea"]:
            if "Chiiki" in area:
                shindo = area.get("Shindo1", area.get("Shindo2", "不明"))
                res[area["Chiiki"]] = shindo
    return res

KEY_MAPPING = {
    "Magunitude": "M (マグニチュード)",
    "Depth": "深さ (Depth)",
    "Hypocenter": "震源地",
    "MaxIntensity": "最大震度",
    "Latitude": "緯度 (Latitude)",
    "Longitude": "経度 (Longitude)",
    "CodeType": "発表区分",
    "Issue.Source": "発表元",
    "Issue.Status": "発表ステータス",
    "Accuracy.Epicenter": "震央の精度",
    "Accuracy.Depth": "深さの精度",
    "Accuracy.Magnitude": "マグニチュードの精度",
    "MaxIntChange.String": "最大震度変化",
    "MaxIntChange.Reason": "最大震度変化理由",
    "isSea": "isSea (海域地震)",
    "isTraining": "isTraining (訓練報)",
    "isAssumption": "isAssumption (推定震源)",
    "isWarn": "isWarn (警報)",
    "isFinal": "isFinal (最終報)",
    "isCancel": "isCancel (取消)"
}

def translate_key(k):
    # Strip "Earthquake." prefix if present for mapping
    search_k = k.replace("Earthquake.", "")
    return KEY_MAPPING.get(search_k, search_k)

def diff_dicts(old_d, new_d):
    old_areas = extract_warn_areas(old_d)
    new_areas = extract_warn_areas(new_d)
    
    import copy
    od = copy.deepcopy(old_d)
    nd = copy.deepcopy(new_d)
    if "WarnArea" in od: del od["WarnArea"]
    if "WarnArea" in nd: del nd["WarnArea"]

    old_flat = flatten_dict(od)
    new_flat = flatten_dict(nd)
    
    changes = []
    # Keys we want to ignore in the diff
    ignore_keys = {"OriginalId", "Issue.Time", "Earthquake.OriginTime", "OriginTime", "Issue.Serial", "Serial", "OriginalText", "isFinal", "EventID", "AnnouncedTime"}
    
    for k in new_flat:
        if any(ik in k for ik in ignore_keys) or k in ignore_keys:
            continue
        
        tk_key = translate_key(k)
        
        if k in old_flat:
            if old_flat[k] != new_flat[k]:
                changes.append((tk_key, old_flat[k], new_flat[k]))
        else:
            changes.append((tk_key, None, new_flat[k]))
            
    # Area diffs
    for chiiki, shindo in new_areas.items():
        if chiiki in old_areas:
            if old_areas[chiiki] != shindo:
                changes.append((f"地域予測 ({chiiki})", old_areas[chiiki], shindo))
        else:
            changes.append((f"地域予測 ({chiiki})", None, shindo))
            
    for chiiki in old_areas:
        if chiiki not in new_areas:
            changes.append((f"地域予測 ({chiiki})", old_areas[chiiki], "解除/削除"))

    return changes

class TimelineCanvas(tk.Canvas):
    def __init__(self, parent, on_report_click=None, on_diff_click=None, bg="#1e1e1e", **kwargs):
        super().__init__(parent, bg=bg, highlightthickness=0, **kwargs)
        self.on_report_click = on_report_click 
        self.on_diff_click = on_diff_click
        
        self.yview_val = 0.0
        self.xview_val = 0.0
        
        self.bind("<Configure>", self._on_configure)
        # Windows scroll
        self.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<Shift-MouseWheel>", self._on_mousewheel_x)
        # Linux/Raspberry Pi scroll (X11 Button events)
        self.bind("<Button-4>", self._on_scroll_up)
        self.bind("<Button-5>", self._on_scroll_down)
        self.bind("<Shift-Button-4>", self._on_scroll_left)
        self.bind("<Shift-Button-5>", self._on_scroll_right)
        self.bind("<Enter>", self._bind_mousewheel)
        self.bind("<Leave>", self._unbind_mousewheel)

        self.timeline_data = [] # List of dicts: {"title": str, "timestamp": str, "diffs": list, "report_idx": int}

    def set_data(self, data):
        """
        data format:
        [
            {
                "title": "第3報 (最終)",
                "timestamp": "19:46:25",
                "diffs": [("Earthquake.Magnitude", 4.5, 5.0), ...],
                "report_idx": 2,  # original index in the list
                "is_final": True
            },
            ...
        ]
        Data should be in descending order (newest first).
        """
        self.timeline_data = data
        self._draw_timeline()

    def _on_configure(self, event):
        self._draw_timeline()

    def _bind_mousewheel(self, event):
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_all("<Shift-MouseWheel>", self._on_mousewheel_x)
        self.bind_all("<Button-4>", self._on_scroll_up)
        self.bind_all("<Button-5>", self._on_scroll_down)
        self.bind_all("<Shift-Button-4>", self._on_scroll_left)
        self.bind_all("<Shift-Button-5>", self._on_scroll_right)

    def _unbind_mousewheel(self, event):
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Shift-MouseWheel>")
        self.unbind_all("<Button-4>")
        self.unbind_all("<Button-5>")
        self.unbind_all("<Shift-Button-4>")
        self.unbind_all("<Shift-Button-5>")

    def _on_mousewheel(self, event):
        if str(self.cget("state")) != "disabled":
            self.yview_scroll(int(-1*(event.delta/120)), "units")

    def _on_mousewheel_x(self, event):
        if str(self.cget("state")) != "disabled":
            self.xview_scroll(int(-1*(event.delta/120)), "units")

    def _on_scroll_up(self, event):
        if str(self.cget("state")) != "disabled":
            self.yview_scroll(-1, "units")

    def _on_scroll_down(self, event):
        if str(self.cget("state")) != "disabled":
            self.yview_scroll(1, "units")

    def _on_scroll_left(self, event):
        if str(self.cget("state")) != "disabled":
            self.xview_scroll(-1, "units")

    def _on_scroll_right(self, event):
        if str(self.cget("state")) != "disabled":
            self.xview_scroll(1, "units")

    def _draw_timeline(self):
        self.delete("all")
        if not self.timeline_data:
            return
            
        w = self.winfo_width()
        
        # Drawing parameters
        x_line = 30
        y_start = 30
        y_spacing = 30
        dot_radius = 6
        text_x = 55

        NEUTRAL_KEYS = {"緯度 (Latitude)", "経度 (Longitude)", "深さ (Depth)", "震央の精度", "深さの精度", "マグニチュードの精度"}

        current_y = y_start
        max_x = 0

        # Draw the continuous line
        total_height = y_start
        # Pre-calculate heights (24px per diff row + 28px header, minimum 40px)
        heights = []
        for item in self.timeline_data:
            diffs = item.get("diffs", [])
            if diffs:
                h = max(40, 28 + len(diffs) * 24)
            else:
                h = 40
            heights.append(h)
        
        if len(self.timeline_data) > 1:
            total_height_line = y_start + sum(heights[:-1])
            self.create_line(x_line, y_start, x_line, total_height_line, fill="#555555", width=2)
            
        for i, item in enumerate(self.timeline_data):
            # Draw dot
            color = "#a0522d" if item.get("is_final") else "#2a6496"
            
            # Invisible clickable box for the whole report area
            box_id = self.create_rectangle(
                10, current_y - 15, w - 10, current_y + heights[i] - 10,
                fill="", outline="", tags=f"report_{item['report_idx']}"
            )
            
            dot = self.create_oval(
                x_line - dot_radius, current_y - dot_radius,
                x_line + dot_radius, current_y + dot_radius,
                fill=color, outline="white", width=2,
                tags=f"report_{item['report_idx']}"
            )
            
            # Draw title
            title_text = f"{item['title']} - {item['timestamp']}"
            t_id = self.create_text(
                text_x, current_y, text=title_text, fill="white",
                font=("Meiryo", 10, "bold"), anchor="w",
                tags=f"report_{item['report_idx']}"
            )
            
            # Draw diffs
            diff_y = current_y + 25
            if item.get("diffs"):
                for diff in item["diffs"]:
                    key, old_v, new_v = diff
                    
                    diff_color = "#e0e0e0"
                    if key in NEUTRAL_KEYS:
                        diff_color = "#ffd93d"
                    else:
                        try:
                            ov = float(old_v) if old_v is not None else 0.0
                            nv = float(new_v) if new_v is not None else 0.0
                            if nv > ov:
                                diff_color = "#ff6b6b"
                            elif nv < ov:
                                diff_color = "#4ecdc4"
                        except (ValueError, TypeError):
                            diff_color = "#ffd93d"
                    
                    if old_v is None:
                        diff_str = f"  (新規) → {new_v}"
                    else:
                        diff_str = f"  {old_v} → {new_v}"
                        
                    # Create key text
                    k_id = self.create_text(
                        text_x + 5, diff_y + 2, text=key, fill=diff_color,
                        font=("Meiryo", 9), anchor="w"
                    )
                    
                    # Create button-like background for key ONLY
                    bounds = self.bbox(k_id)
                    if bounds:
                        diff_tag = (f"diff_{item['report_idx']}_{key}",)
                        bg_id = self.create_rectangle(
                            bounds[0]-4, bounds[1]-2, bounds[2]+4, bounds[3]+2,
                            fill="#333333", outline="#555555", tags=diff_tag
                        )
                        self.tag_lower(bg_id, k_id)

                        # Add tags to key text (tuple avoids Tcl whitespace-splitting in tag names)
                        self.itemconfig(k_id, tags=diff_tag)
                        
                        # Bind diff click to key
                        tag = diff_tag[0]
                        self.tag_bind(tag, "<Button-1>", lambda e, k=key: self._handle_diff_click(k))
                        self.tag_bind(tag, "<Enter>", lambda e, t=tag, i=bg_id: self._on_diff_hover_enter(i))
                        self.tag_bind(tag, "<Leave>", lambda e, t=tag, i=bg_id: self._on_diff_hover_leave(i))
                        
                        # Create value text right after the key
                        v_id = self.create_text(
                            bounds[2] + 4, diff_y + 2, text=diff_str, fill=diff_color,
                            font=("Meiryo", 9), anchor="w"
                        )
                        v_bounds = self.bbox(v_id)
                        if v_bounds and v_bounds[2] > max_x:
                            max_x = v_bounds[2]

                    diff_y += 24
            else:
                no_diff_y = current_y + heights[i] // 2
                d_id = self.create_text(
                    text_x, no_diff_y, text="(変更なし / 初期データ)", fill="#888888",
                    font=("Meiryo", 9, "italic"), anchor="w"
                )
                b = self.bbox(d_id)
                if b and b[2] > max_x: max_x = b[2]
                
            current_y += heights[i]
            
        # Update scrollregion
        self.config(scrollregion=(0, 0, max(w, max_x + 20), current_y + 20))
        
        # Bind clicks to tags
        for item in self.timeline_data:
            tag = f"report_{item['report_idx']}"
            self.tag_bind(tag, "<Button-1>", lambda e, idx=item['report_idx']: self._handle_click(idx))
            self.tag_bind(tag, "<Enter>", lambda e, t=tag: self._on_hover_enter(t))
            self.tag_bind(tag, "<Leave>", lambda e, t=tag: self._on_hover_leave(t))
            
    def _handle_click(self, idx):
        if self.on_report_click:
            self.on_report_click(idx)
            
    def _handle_diff_click(self, key):
        if self.on_diff_click:
            self.on_diff_click(key)

    def _on_hover_enter(self, tag):
        self.config(cursor="hand2")
        
    def _on_hover_leave(self, tag):
        self.config(cursor="")

    def _on_diff_hover_enter(self, bg_id):
        self.config(cursor="hand2")
        self.itemconfig(bg_id, fill="#444444", outline="#777777")
        
    def _on_diff_hover_leave(self, bg_id):
        self.config(cursor="")
        self.itemconfig(bg_id, fill="#333333", outline="#555555")
