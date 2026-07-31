"""
util_audio_player.py  —  Windows 用音声再生モジュール (pygame.mixer)

インターフェース (Pi 版と同一):
    play_audio(path, volume=90)        — WAV を1ファイル再生
    play_audio_list(file_list, volume=90) — WAV リストを順番に再生
    stop_audio()                       — 再生を即時停止
"""
import os
import threading

try:
    import pygame
    pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
    pygame.mixer.init()
    _available = True
except Exception as _e:
    print(f"[util_audio_player] pygame 初期化失敗: {_e}。音声なしで動作します。")
    _available = False

_stop_event = threading.Event()
_lock       = threading.Lock()


def play_audio(path: str, volume: int = 90) -> None:
    if not _available:
        return
    if not os.path.exists(path):
        print(f"[util_audio_player] ファイルが見つかりません: {path}")
        return

    vol = max(0.0, min(1.0, volume / 100.0))
    with _lock:
        _stop_event.clear()
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(vol)
            pygame.mixer.music.play()
        except Exception as e:
            print(f"[util_audio_player] 再生エラー: {e}")
            return

    # 再生完了 or 停止シグナルを待つ
    while pygame.mixer.music.get_busy():
        if _stop_event.is_set():
            pygame.mixer.music.stop()
            return
        pygame.time.wait(30)


def play_audio_list(file_list: list, volume: int = 90) -> None:
    _stop_event.clear()
    for path in file_list:
        if _stop_event.is_set():
            break
        play_audio(path, volume)


def stop_audio() -> None:
    _stop_event.set()
    if _available:
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
