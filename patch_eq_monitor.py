"""eq_monitor_nied.py へ地中(borehole)画像対応を適用した一回限りの移行スクリプト（適用済み）"""
import os
import re

# 環境依存の絶対パスを避け、このスクリプトの位置から解決する
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(_PROJECT_ROOT, 'eq_src', 'eq_monitor_nied.py')
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update _save_buffered_frames
new_loop = '''        for buf_img, buf_b_img, buf_time_str, buf_intensity in frames_to_save:

            # 地表画像の生成
            save_filename = f"eqlog_s_{buf_time_str}_lv{buf_intensity:.1f}.png"
            save_path = os.path.join(self.current_alert_dir, "surface", save_filename)
            try:
                buf_img.save(save_path)
                
                # 地中画像の生成
                if buf_b_img is not None:
                    b_filename = f"eqlog_b_{buf_time_str}_lv{buf_intensity:.1f}.png"
                    b_save_path = os.path.join(self.current_alert_dir, "borehole", b_filename)
                    buf_b_img.save(b_save_path)
            except Exception as e:
                self.logger.warning(f"Failed to save buffered image {save_filename}: {e}")'''

content = re.sub(
    r'        for buf_img, buf_time_str, buf_intensity in frames_to_save:.*?(?=\n\n|\n\s+self\.logger\.info)',
    new_loop,
    content,
    flags=re.DOTALL
)

# 2. Update _process_current_image to fetch borehole and append to buffer
content = re.sub(
    r'(\s+global_intensity, global_condition_met = self\._analyze_cropped_image\(img, time_str, trigger_alert=False\)\s+)(.*?self\.recent_frames\.append\(\(img\.copy\(\), time_str, global_intensity\)\))',
    r'\1\n            fetched_b_img = None\n            if getattr(self, \'borehole_enabled\', True):\n                fetched_b_img = self._fetch_borehole_image(time_str)\n            \n            b_copy = fetched_b_img.copy() if fetched_b_img else None\n            self.recent_frames.append((img.copy(), b_copy, time_str, global_intensity))',
    content,
    flags=re.DOTALL
)

# 3. Update confirm_fn
content = re.sub(
    r'confirm_fn = \(lambda: self\._borehole_confirms\(time_str\)\) if self\.borehole_enabled else None',
    r'confirm_fn = (lambda: self._borehole_confirms(time_str, fetched_b_img)) if self.borehole_enabled else None',
    content
)

# 4. Update the active alert save block (don't fetch b_img again, use fetched_b_img)
content = re.sub(
    r'                        # 地中データの取得・保存 \(地震発生時のみ\)\s+if getattr\(self, \'borehole_enabled\', True\):\s+b_img = self\._fetch_borehole_image\(time_str\)\s+if b_img:\s+b_filename = f\"eqlog_b_\{time_str\}_lv\{current_intensity:\.1f\}\.png\"\s+b_save_path = os\.path\.join\(self\.current_alert_dir, \"borehole\", b_filename\)\s+b_img\.save\(b_save_path\)\s+b_img\.close\(\)',
    r'''                        # 地中データの取得・保存 (地震発生時のみ)
                        if getattr(self, 'borehole_enabled', True) and fetched_b_img:
                            b_filename = f"eqlog_b_{time_str}_lv{current_intensity:.1f}.png"
                            b_save_path = os.path.join(self.current_alert_dir, "borehole", b_filename)
                            fetched_b_img.save(b_save_path)''',
    content,
    flags=re.DOTALL
)

# 5. Update _borehole_confirms
content = re.sub(
    r'def _borehole_confirms\(self, time_str: str\) -> bool:',
    r'def _borehole_confirms(self, time_str: str, b_img: Image.Image = None) -> bool:',
    content
)

content = re.sub(
    r'crop = self\._fetch_borehole_crop\(time_str\)',
    r'crop = self._fetch_borehole_crop(time_str, b_img)',
    content
)

# 6. Update _fetch_borehole_crop
content = re.sub(
    r'def _fetch_borehole_crop\(self, time_str: str\):',
    r'def _fetch_borehole_crop(self, time_str: str, img: Image.Image = None):',
    content
)

content = re.sub(
    r'img = self\._fetch_borehole_image\(time_str\)\s+if img is None:\s+return None',
    r'if img is None:\n            img = self._fetch_borehole_image(time_str)\n        if img is None:\n            return None',
    content
)

# Remove img.close() in _fetch_borehole_crop
content = re.sub(
    r'crop = img\.crop\(\(left, top, right, bottom\)\)\s+img\.close\(\)',
    r'crop = img.crop((left, top, right, bottom))',
    content
)

# 7. Add fetched_b_img.close() at the end of _process_current_image
content = re.sub(
    r'            img\.close\(\)\s+image_stream\.close\(\)',
    r'            img.close()\n            image_stream.close()\n            if fetched_b_img:\n                fetched_b_img.close()',
    content
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print('Patched successfully!')
