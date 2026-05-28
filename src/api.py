import ctypes
import logging
import threading
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import webview
    from .config import Config
    from .media_session import MediaSessionPoller
    from .lyrics_engine import LyricsEngine

logger = logging.getLogger(__name__)

# Windows virtual key codes
_VK_PLAY_PAUSE = 0xB3
_VK_NEXT       = 0xB0
_VK_PREV       = 0xB1

# mp3-UI.png 이미지 비율 (세로/가로)
_V_IMG_H_RATIO = 1306 / 691


class LyricApi:
    def __init__(self, poller: "MediaSessionPoller", lyrics_engine: "LyricsEngine", config: "Config"):
        self._poller = poller
        self._lyrics_engine = lyrics_engine
        self._cfg = config
        self._win: "webview.Window | None" = None

        self._last_song_id = ""
        self._current_lyrics: list = []
        self._lyrics_lock = threading.Lock()
        self._lyrics_loading = False

        # SMTC position 실시간 추정용
        self._pos_base = 0.0
        self._pos_base_time = 0.0
        self._pos_was_playing = False
        self._paused_position = 0.0   # 일시정지 시점의 추정 위치
        self._prev_smtc_pos = 0.0     # 이전 SMTC raw position (seek 감지용)


    # ── 가사 상태 (JS가 300ms마다 폴링) ──
    def get_current_state(self) -> dict:
        info = self._poller.get_current_info()

        # fix: 새 곡 감지를 position 추정보다 먼저 처리
        # → 무정지 곡 전환(gapless) 시에도 타이머가 확실히 리셋됨
        if not info.is_empty():
            song_id = info.song_id()
            if song_id != self._last_song_id:
                self._last_song_id = song_id
                raw = info.position if info.position > 0.5 else 0.0
                self._pos_base = raw
                self._paused_position = raw   # 0.0이 아닌 실제 위치로 초기화
                self._prev_smtc_pos = info.position
                self._pos_base_time = time.monotonic()
                self._pos_was_playing = info.is_playing
                # 가사 즉시 클리어 후 백그라운드에서 로드 → JS 폴링 블로킹 X
                with self._lyrics_lock:
                    self._current_lyrics = []
                    self._lyrics_loading = True
                threading.Thread(
                    target=self._fetch_lyrics_bg,
                    args=(info.title, info.artist, song_id),
                    daemon=True,
                    name="lyrics-fetch",
                ).start()

        position = self._estimate_position(info)

        base = {
            "title": info.title,
            "artist": info.artist,
            "position": round(position, 2),
            "duration": round(info.duration, 2),
            "is_playing": info.is_playing,
            "song_id": info.song_id(),
        }

        if info.is_empty():
            return {**base, "status": "idle"}

        with self._lyrics_lock:
            lyrics = list(self._current_lyrics)
            loading = self._lyrics_loading

        if not info.is_playing:
            idx = self._lyrics_engine.get_current_index(lyrics, position)
            _, curr, _ = self._lyrics_engine.get_context(lyrics, idx)
            return {**base, "status": "paused", "current": curr, "prev": "", "next": ""}

        if not lyrics:
            return {**base, "status": "loading" if loading else "no_lyrics"}

        idx = self._lyrics_engine.get_current_index(lyrics, position)
        prev, curr, nxt = self._lyrics_engine.get_context(lyrics, idx)

        # 다음 가사까지 남은 ms — JS 타겟 스케줄링용 (없으면 None)
        ms_to_next = None
        if idx + 1 < len(lyrics):
            ms_to_next = max(0, round((lyrics[idx + 1][0] - position) * 1000))

        return {**base, "status": "playing",
                "prev": prev, "current": curr, "next": nxt,
                "ms_to_next": ms_to_next}

    def _fetch_lyrics_bg(self, title: str, artist: str, song_id: str) -> None:
        try:
            lyrics = self._lyrics_engine.get_lyrics(title, artist)
        except Exception as e:
            logger.warning("가사 로딩 실패: %s", e)
            lyrics = []
        with self._lyrics_lock:
            if self._last_song_id == song_id:  # 로딩 중 곡이 바뀌었으면 폐기
                self._current_lyrics = lyrics
            self._lyrics_loading = False

    # 타겟 스케줄링 도입 후 실제 지연: 렌더 ~16ms + 페이드 100ms → 120ms
    # 여유 80ms 포함해 200ms로 설정
    _LYRIC_LOOKAHEAD = 0.20

    def _estimate_position(self, info) -> float:
        """SMTC 500ms 폴링 공백을 monotonic 시계로 보완.

        Melon은 SMTC position을 재생 중에도 거의 갱신하지 않으므로
        'estimated vs SMTC' 비교는 하지 않는다.
        대신 SMTC position 자체가 크게 점프할 때만 seek로 판단한다.
        """
        now = time.monotonic()
        if info.is_playing:
            smtc_jump = abs(info.position - self._prev_smtc_pos)

            if smtc_jump > 3.0:
                # seek / 이전곡 / 처음부터 재생 감지 — pause→play 전환 중에도 적용
                new_base = info.position if info.position > 0.5 else 0.0
                self._pos_base = new_base
                self._paused_position = new_base
                self._pos_base_time = now
                self._prev_smtc_pos = info.position
            elif not self._pos_was_playing:
                # 일반 일시정지→재개 (seek 없음)
                self._pos_base = self._paused_position
                self._pos_base_time = now
                self._prev_smtc_pos = info.position
            else:
                self._prev_smtc_pos = info.position

            self._pos_was_playing = True
            estimated = self._pos_base + (now - self._pos_base_time) + self._LYRIC_LOOKAHEAD
            if info.duration > 0:
                estimated = min(estimated, info.duration)
            return estimated
        else:
            if self._pos_was_playing:
                # 재생→정지: 현재 추정 위치 저장
                est = self._pos_base + (now - self._pos_base_time)
                if info.duration > 0:
                    est = min(est, info.duration)
                self._paused_position = est
            self._pos_was_playing = False
            return self._paused_position

    # ── 앨범아트 ──
    def get_album_art(self) -> str:
        """base64 data URL 반환. 없으면 빈 문자열."""
        try:
            return self._poller.get_album_art_base64()
        except Exception:
            return ""

    # ── 미디어 컨트롤 ──
    def media_prev(self) -> None:
        self._send_vk(_VK_PREV)
        self._pos_base = 0.0
        self._paused_position = 0.0
        self._pos_base_time = time.monotonic()
        self._prev_smtc_pos = 0.0

    def media_play_pause(self) -> None:
        self._send_vk(_VK_PLAY_PAUSE)

    def media_next(self) -> None:
        self._send_vk(_VK_NEXT)
        self._pos_base = 0.0
        self._paused_position = 0.0
        self._pos_base_time = time.monotonic()
        self._prev_smtc_pos = 0.0

    # ── 볼륨 ──
    @staticmethod
    def _endpoint_vol():
        """pycaw로 IAudioEndpointVolume 인터페이스 반환."""
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetSpeakers().EndpointVolume

    def get_volume(self) -> int:
        """현재 시스템 마스터 볼륨 반환 (0–100). 실패 시 -1."""
        try:
            return int(self._endpoint_vol().GetMasterVolumeLevelScalar() * 100)
        except Exception as e:
            logger.debug("볼륨 읽기 오류: %s", e)
            return -1

    def set_volume(self, percent: int) -> None:
        """시스템 마스터 볼륨 설정 (0–100). 앱 슬라이더 % = 노트북 볼륨 % 1:1 매핑."""
        percent = max(0, min(100, int(percent)))
        try:
            self._endpoint_vol().SetMasterVolumeLevelScalar(percent / 100.0, None)
        except Exception as e:
            logger.debug("볼륨 설정 오류: %s", e)

    # ── 레이아웃 모드 전환 ──
    def set_layout_mode(self, mode: str) -> dict:
        """가로/세로 모드 전환 — 항상 기본 위치·설정으로 초기화."""
        if mode not in ("horizontal", "vertical"):
            return {"error": "invalid mode"}

        sw, sh, _ = self._work_area()

        if mode == "horizontal":
            w, h = sw, self._cfg.h_height
            x, y = 0, sh - h
        else:
            # 세로뷰 전환 시 설정 기본값 리셋
            self._cfg.opacity = 1.0
            self._cfg.font_size = 15
            self._cfg.font_size_dim = 12
            v_w = self._cfg.v_width
            w = v_w if (200 <= v_w <= sw // 2) else 360
            h = min(round(w * _V_IMG_H_RATIO), sh)
            x, y = sw - w, sh - h

        self._cfg.layout_mode = mode
        self._cfg.save()

        if self._win:
            self._move_window_atomic(x, y, w, h)

        self._apply_opacity()
        return {"mode": mode, "w": w, "h": h, "config": asdict(self._cfg)}

    # ── 설정 ───
    def get_config(self) -> dict:
        return asdict(self._cfg)

    _CFG_INT_LIMITS: dict = {
        "h_height": (30, 400), "v_width": (150, 800),
        "font_size": (8, 72), "font_size_dim": (6, 48),
        "h_font_size": (8, 48), "h_font_size_dim": (6, 36),
        "poll_interval_ms": (100, 2000),
    }

    def save_config(self, data: dict) -> None:
        for k, v in data.items():
            if not hasattr(self._cfg, k):
                continue
            try:
                typed = type(getattr(self._cfg, k))(v)
                if k in self._CFG_INT_LIMITS:
                    lo, hi = self._CFG_INT_LIMITS[k]
                    typed = max(lo, min(hi, typed))
                setattr(self._cfg, k, typed)
            except (TypeError, ValueError):
                pass
        self._cfg.save()
        self._apply_topmost()
        self._apply_opacity()

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
        """논리 픽셀 좌표를 물리 픽셀로 변환 후 원자적으로 창 이동·크기 변경."""
        try:
            import win32gui
            hwnd = win32gui.FindWindow(None, "Lyric")
            if hwnd:
                scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
                ctypes.windll.user32.MoveWindow(
                    hwnd,
                    round(x * scale), round(y * scale),
                    round(w * scale), round(h * scale),
                    True,
                )
                return
        except Exception as e:
            logger.debug("MoveWindow 실패, pywebview 폴백: %s", e)
        self._win.move(x, y)
        self._win.resize(w, h)

    def _apply_topmost(self) -> None:
        try:
            import win32gui, win32con
            hwnd = win32gui.FindWindow(None, "Lyric")
            if hwnd:
                flag = win32con.HWND_TOPMOST if self._cfg.pinned else win32con.HWND_NOTOPMOST
                win32gui.SetWindowPos(
                    hwnd, flag, 0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
                )
        except Exception as e:
            logger.debug("SetWindowPos 실패: %s", e)

    def _apply_opacity(self) -> None:
        """창 수준 투명도 설정 (WS_EX_LAYERED + LWA_ALPHA).
        가로 모드: 0.96 고정 / 세로 모드: 설정값 사용.
        """
        try:
            import win32gui
            hwnd = win32gui.FindWindow(None, "Lyric")
            if not hwnd:
                return
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            LWA_ALPHA = 0x00000002
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
            opacity = self._cfg.opacity if self._cfg.layout_mode == "vertical" else 1.0
            alpha = max(0, min(255, int(opacity * 255)))
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
        except Exception as e:
            logger.debug("투명도 설정 실패: %s", e)

    @staticmethod
    def _send_vk(vk: int) -> None:
        ctypes.windll.user32.keybd_event(vk, 0, 0x0001, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 0x0001 | 0x0002, 0)

    @staticmethod
    def _work_area() -> tuple[int, int, int]:
        """주 모니터 논리 픽셀 기준 (작업영역_너비, 작업영역_높이, 태스크바_높이) 반환.
        GetMonitorInfo(물리 픽셀) ÷ DPI스케일 = 논리 픽셀 → pywebview 좌표계와 일치.
        """
        try:
            import win32api, win32con
            scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
            mon = win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
            info = win32api.GetMonitorInfo(mon)
            work = info['Work']      # physical (left, top, right, bottom)
            full = info['Monitor']   # physical full screen rect
            sw = round((work[2] - work[0]) / scale)
            sh = round((work[3] - work[1]) / scale)
            fh = round((full[3] - full[1]) / scale)
            return sw, sh, fh - sh
        except Exception:
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
            return sw, sh - 48, 48
