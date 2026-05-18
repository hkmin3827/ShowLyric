"""JS ↔ Python 브릿지 — pywebview js_api.

window.pywebview.api.method() 형태로 JS에서 호출.
반환값 있는 메서드는 JS에서 자동으로 Promise 처리됨.
"""
import ctypes
import logging
import subprocess
import threading
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Optional

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

# ── Windows Core Audio API — PowerShell 영구 프로세스 ─────────────────
# pycaw에 의존하지 않고 모든 Windows Vista+ 환경에서 동작
_VOL_PS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$WarningPreference = 'SilentlyContinue'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
public class _MMEnumCls {}

[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface _IMMEnum {
    int EnumAudioEndpoints();
    _IMMDev GetDefaultAudioEndpoint(int dataFlow, int role);
}

[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface _IMMDev {
    [return: MarshalAs(UnmanagedType.Interface)]
    object Activate([MarshalAs(UnmanagedType.LPStruct)] Guid iid,
                    int dwClsCtx, IntPtr pActivationParams);
}

[ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface _IAudioVol {
    int RegisterControlChangeNotify(IntPtr p);
    int UnregisterControlChangeNotify(IntPtr p);
    int GetChannelCount(out int n);
    int SetMasterVolumeLevel(float f, ref Guid g);
    int SetMasterVolumeLevelScalar(float f, ref Guid g);
    int GetMasterVolumeLevel(out float f);
    int GetMasterVolumeLevelScalar(out float f);
}
'@ -ErrorAction Stop -WarningAction SilentlyContinue

try {
    $e = [_IMMEnum](New-Object _MMEnumCls)
    $d = $e.GetDefaultAudioEndpoint(0, 1)
    $id = [Guid]"5CDF2C82-841E-4546-9722-0CF74078229A"
    $v = [_IAudioVol]$d.Activate($id, 23, [IntPtr]::Zero)
    Write-Output "READY"
} catch {
    Write-Output "ERROR"
    exit 1
}
[Console]::Out.Flush()

while ($true) {
    $line = [Console]::ReadLine()
    if ($null -eq $line) { break }
    try {
        if ($line -eq "GET") {
            $f = [float]0
            $v.GetMasterVolumeLevelScalar([ref]$f) | Out-Null
            Write-Output "VOL:$([int][Math]::Round($f * 100))"
        } elseif ($line -match '^SET:(\d+)$') {
            $pct = [Math]::Max(0, [Math]::Min(100, [int]$Matches[1]))
            $g = [Guid]::Empty
            $v.SetMasterVolumeLevelScalar($pct / 100.0, [ref]$g) | Out-Null
            Write-Output "OK"
        } else {
            Write-Output "UNKNOWN"
        }
    } catch {
        Write-Output "ERR"
    }
    [Console]::Out.Flush()
}
"""


class _VolumePS:
    """Windows Core Audio API 볼륨 제어 — 영구 PowerShell 프로세스."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._ready = False
        # 앱 시작 지연 없이 백그라운드에서 PS 프로세스 초기화
        threading.Thread(target=self._start, daemon=True, name="vol-ps").start()

    def _start(self) -> None:
        try:
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _VOL_PS],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            # 경고/빈줄이 앞에 나올 수 있으므로 최대 30줄까지 READY/ERROR 탐색
            ready = False
            for _ in range(30):
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line == "READY":
                    ready = True
                    break
                if line.startswith("ERROR"):
                    logger.warning("볼륨 PS 오류: %s", line)
                    break
                # 경고/기타 줄은 건너뜀
                if line:
                    logger.debug("볼륨 PS 초기화 중: %s", line)

            if ready:
                with self._lock:
                    self._proc = proc
                    self._ready = True
                logger.debug("볼륨 PS 프로세스 준비 완료")
            else:
                logger.warning("볼륨 PS 프로세스 READY 수신 실패")
                proc.terminate()
        except Exception as e:
            logger.warning("볼륨 PS 프로세스 시작 오류: %s", e)

    def get(self) -> Optional[int]:
        with self._lock:
            if not self._ready:
                return None
            try:
                self._proc.stdin.write("GET\n")
                self._proc.stdin.flush()
                line = self._proc.stdout.readline().strip()
                if line.startswith("VOL:"):
                    return int(line[4:])
            except Exception as e:
                logger.debug("볼륨 GET 오류: %s", e)
                self._ready = False
        return None

    def set(self, percent: int) -> bool:
        with self._lock:
            if not self._ready:
                return False
            try:
                self._proc.stdin.write(f"SET:{percent}\n")
                self._proc.stdin.flush()
                self._proc.stdout.readline()  # OK 소비
                return True
            except Exception as e:
                logger.debug("볼륨 SET 오류: %s", e)
                self._ready = False
        return False

    def stop(self) -> None:
        with self._lock:
            if self._proc:
                try:
                    self._proc.terminate()
                except Exception:
                    pass


class LyricApi:
    def __init__(self, poller: "MediaSessionPoller", lyrics_engine: "LyricsEngine", config: "Config"):
        self._poller = poller
        self._lyrics_engine = lyrics_engine
        self._cfg = config
        self._win: "webview.Window | None" = None

        self._last_song_id = ""
        self._current_lyrics: list = []
        self._vol_ps = _VolumePS()   # Windows Core Audio API (PS 영구 프로세스)

        # 가사 position 실시간 추정용
        self._pos_base = 0.0
        self._pos_base_time = 0.0
        self._pos_was_playing = False

    # ── 가사 상태 (JS가 300ms마다 폴링) ──────────────────────────────

    def get_current_state(self) -> dict:
        info = self._poller.get_current_info()

        # SMTC는 500ms 간격 업데이트 → 경과 시간을 더해 실시간 position 추정
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

        if not info.is_playing:
            song_id = info.song_id()
            if song_id != self._last_song_id:
                self._last_song_id = song_id
                self._current_lyrics = self._lyrics_engine.get_lyrics(info.title, info.artist)
            idx = self._lyrics_engine.get_current_index(self._current_lyrics, position)
            _, curr, _ = self._lyrics_engine.get_context(self._current_lyrics, idx)
            return {**base, "status": "paused", "current": curr, "prev": "", "next": ""}

        song_id = info.song_id()
        if song_id != self._last_song_id:
            self._last_song_id = song_id
            self._current_lyrics = self._lyrics_engine.get_lyrics(info.title, info.artist)

        if not self._current_lyrics:
            return {**base, "status": "no_lyrics"}

        idx = self._lyrics_engine.get_current_index(self._current_lyrics, position)
        prev, curr, nxt = self._lyrics_engine.get_context(self._current_lyrics, idx)

        return {**base, "status": "playing", "prev": prev, "current": curr, "next": nxt}

    def _estimate_position(self, info) -> float:
        """SMTC position 업데이트 공백을 monotonic 경과 시간으로 보완."""
        now = time.monotonic()
        if info.is_playing:
            # position이 0.5초 이상 바뀌었거나 재생 시작 시 기준 갱신
            if abs(info.position - self._pos_base) > 0.5 or not self._pos_was_playing:
                self._pos_base = info.position
                self._pos_base_time = now
            self._pos_was_playing = True
            estimated = self._pos_base + (now - self._pos_base_time)
            # duration을 넘지 않도록 클램프
            if info.duration > 0:
                estimated = min(estimated, info.duration)
            return estimated
        else:
            self._pos_base = info.position
            self._pos_base_time = now
            self._pos_was_playing = False
            return info.position

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
        """현재 시스템 마스터 볼륨 반환 (0–100).

        Windows Core Audio API를 PowerShell 프로세스를 통해 호출.
        PS 준비 전이면 -1을 반환해 JS에서 슬라이더를 비활성화 상태로 유지.
        """
        vol = self._vol_ps.get()
        return vol if vol is not None else -1

    def set_volume(self, percent: int) -> None:
        """시스템 마스터 볼륨 설정 (0–100)."""
        percent = max(0, min(100, int(percent)))
        self._vol_ps.set(percent)

    # ── 레이아웃 모드 전환 ────────────────────────────────────────────

    def set_layout_mode(self, mode: str) -> dict:
        """가로/세로 모드 전환 — 창 크기·위치 변경."""
        if mode not in ("horizontal", "vertical"):
            return {"error": "invalid mode"}

        try:
            import webview as _wv
            screens = _wv.screens
            sw = screens[0].width if screens else ctypes.windll.user32.GetSystemMetrics(0)
            sh = screens[0].height if screens else ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
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
