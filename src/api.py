"""JS ↔ Python 브릿지 — pywebview js_api.

window.pywebview.api.method() 형태로 JS에서 호출.
반환값 있는 메서드는 JS에서 자동으로 Promise 처리됨.
"""
import ctypes
import json
import logging
import subprocess
import threading
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import webview
    from .config import Config
    from .media_session import MediaSessionPoller
    from .lyrics_engine import LyricsEngine

logger = logging.getLogger(__name__)

# Windows virtual key codes
_VK_PLAY_PAUSE  = 0xB3
_VK_NEXT        = 0xB0
_VK_PREV        = 0xB1
_VK_VOL_UP      = 0xAF
_VK_VOL_DOWN    = 0xAE
_VK_VOL_MUTE    = 0xAD
_HWND_TOPMOST   = -1
_HWND_NOTOPMOST = -2
_SWP_FLAGS      = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE


class LyricApi:
    def __init__(self, poller: "MediaSessionPoller", lyrics_engine: "LyricsEngine", config: "Config"):
        self._poller = poller
        self._lyrics_engine = lyrics_engine
        self._cfg = config
        self._win: "webview.Window | None" = None

        self._last_song_id = ""
        self._current_lyrics: list = []
        self._vol_estimate = 70   # 볼륨 추정값 (0–100)
        self._vol_lock = threading.Lock()

    # ── 가사 상태 (JS가 300ms마다 폴링) ──────────────────────────────

    def get_current_state(self) -> dict:
        info = self._poller.get_current_info()

        base = {
            "title": info.title,
            "artist": info.artist,
            "position": round(info.position, 2),
            "duration": round(info.duration, 2),
            "is_playing": info.is_playing,
            "song_id": info.song_id(),
        }

        if info.is_empty():
            return {**base, "status": "idle"}

        if not info.is_playing:
            # 일시정지 상태 — 곡 정보는 있지만 가사 갱신 안 함
            song_id = info.song_id()
            if song_id != self._last_song_id:
                self._last_song_id = song_id
                self._current_lyrics = self._lyrics_engine.get_lyrics(info.title, info.artist)
            idx = self._lyrics_engine.get_current_index(self._current_lyrics, info.position)
            _, curr, _ = self._lyrics_engine.get_context(self._current_lyrics, idx)
            return {**base, "status": "paused", "current": curr, "prev": "", "next": ""}

        song_id = info.song_id()
        if song_id != self._last_song_id:
            self._last_song_id = song_id
            self._current_lyrics = self._lyrics_engine.get_lyrics(info.title, info.artist)

        if not self._current_lyrics:
            return {**base, "status": "no_lyrics"}

        idx = self._lyrics_engine.get_current_index(self._current_lyrics, info.position)
        prev, curr, nxt = self._lyrics_engine.get_context(self._current_lyrics, idx)

        return {**base, "status": "playing", "prev": prev, "current": curr, "next": nxt}

    # ── 앨범아트 (곡 변경 시 JS가 1회 호출) ──────────────────────────

    def get_album_art(self) -> str:
        """base64 data URL 반환. 없으면 빈 문자열."""
        try:
            return self._poller.get_album_art_base64()
        except Exception:
            return ""

    # ── 미디어 컨트롤 ────────────────────────────────────────────────

    def media_prev(self) -> None:
        self._send_vk(_VK_PREV)

    def media_play_pause(self) -> None:
        self._send_vk(_VK_PLAY_PAUSE)

    def media_next(self) -> None:
        self._send_vk(_VK_NEXT)

    # ── 볼륨 ─────────────────────────────────────────────────────────

    def get_volume(self) -> int:
        """현재 시스템 마스터 볼륨 (0–100) 반환."""
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            iface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol = cast(iface, POINTER(IAudioEndpointVolume))
            return int(vol.GetMasterVolumeLevelScalar() * 100)
        except Exception:
            return self._vol_estimate

    def set_volume(self, percent: int) -> None:
        """시스템 마스터 볼륨 설정 (0–100)."""
        percent = max(0, min(100, int(percent)))
        try:
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            devices = AudioUtilities.GetSpeakers()
            iface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol = cast(iface, POINTER(IAudioEndpointVolume))
            vol.SetMasterVolumeLevelScalar(percent / 100, None)
        except Exception:
            # pycaw 없을 경우 VK 키 시뮬레이션 폴백
            self._vol_via_keys(percent)
        with self._vol_lock:
            self._vol_estimate = percent

    def _vol_via_keys(self, target: int) -> None:
        diff = target - self._vol_estimate
        key = _VK_VOL_UP if diff > 0 else _VK_VOL_DOWN
        for _ in range(abs(diff) // 2 + 1):
            self._send_vk(key)

    # ── 레이아웃 모드 전환 ────────────────────────────────────────────

    def set_layout_mode(self, mode: str) -> dict:
        """가로/세로 모드 전환 — 창 크기·위치 변경."""
        if mode not in ("horizontal", "vertical"):
            return {"error": "invalid mode"}

        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        tb_h = self._taskbar_height()

        if mode == "horizontal":
            w = sw
            h = self._cfg.h_height
            x = 0
            y = sh - h - tb_h
            self._cfg.h_x, self._cfg.h_y = x, y
        else:
            w = self._cfg.v_width
            h = sh - tb_h
            x = sw - w
            y = 0
            self._cfg.v_x, self._cfg.v_y = x, y

        self._cfg.layout_mode = mode
        self._cfg.save()

        if self._win:
            self._move_window_atomic(x, y, w, h)

        return {"mode": mode, "w": w, "h": h}

    # ── 설정 ─────────────────────────────────────────────────────────

    def get_config(self) -> dict:
        return asdict(self._cfg)

    def save_config(self, data: dict) -> None:
        for k, v in data.items():
            if not hasattr(self._cfg, k):
                continue
            try:
                setattr(self._cfg, k, type(getattr(self._cfg, k))(v))
            except (TypeError, ValueError):
                pass
        self._cfg.save()
        self._apply_topmost()

    def toggle_pin(self) -> dict:
        self._cfg.pinned = not self._cfg.pinned
        self._cfg.save()
        self._apply_topmost()
        return {"pinned": self._cfg.pinned}

    def reset_position(self) -> None:
        self._cfg.h_x, self._cfg.h_y = 0, -1
        self._cfg.v_x, self._cfg.v_y = -1, 0
        self._cfg.save()
        self.set_layout_mode(self._cfg.layout_mode)

    # ── 창 제어 ───────────────────────────────────────────────────────

    def close_app(self) -> None:
        if self._win:
            self._win.destroy()

    def save_window_state(self, x: int, y: int, w: int, h: int) -> None:
        if self._cfg.layout_mode == "horizontal":
            self._cfg.h_x, self._cfg.h_y = int(x), int(y)
            self._cfg.h_height = int(h)
        else:
            self._cfg.v_x, self._cfg.v_y = int(x), int(y)
            self._cfg.v_width = int(w)
        self._cfg.save()

    # ── 내부 유틸 ─────────────────────────────────────────────────────

    def _move_window_atomic(self, x: int, y: int, w: int, h: int) -> None:
        """위치와 크기를 한 번에 변경 — resize 후 move 순서 때문에 생기는 창 이탈 방지."""
        try:
            import win32gui
            hwnd = win32gui.FindWindow(None, "Lyric")
            if hwnd:
                # MoveWindow: 위치+크기를 원자적으로 설정
                ctypes.windll.user32.MoveWindow(hwnd, x, y, w, h, True)
                return
        except Exception as e:
            logger.debug("MoveWindow 실패, pywebview 폴백: %s", e)
        # pywebview 폴백 (move 먼저 — resize보다 move 우선으로 오프스크린 최소화)
        self._win.move(x, y)
        self._win.resize(w, h)

    def _apply_topmost(self) -> None:
        try:
            import win32gui
            hwnd = win32gui.FindWindow(None, "Lyric")
            if hwnd:
                flag = _HWND_TOPMOST if self._cfg.pinned else _HWND_NOTOPMOST
                ctypes.windll.user32.SetWindowPos(hwnd, flag, 0, 0, 0, 0, _SWP_FLAGS)
        except Exception as e:
            logger.debug("SetWindowPos 실패: %s", e)

    @staticmethod
    def _send_vk(vk: int) -> None:
        ctypes.windll.user32.keybd_event(vk, 0, 0x0001, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 0x0001 | 0x0002, 0)

    @staticmethod
    def _taskbar_height() -> int:
        try:
            import win32gui
            hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
            if hwnd:
                r = win32gui.GetWindowRect(hwnd)
                return r[3] - r[1]
        except Exception:
            pass
        return 48
