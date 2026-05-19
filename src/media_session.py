"""Windows SMTC 기반 미디어 세션 폴러 (PowerShell 영구 프로세스).

PS7 → PS5.1 → 창 제목 파싱 순으로 폴백.
500ms 간격으로 JSON 출력: title, artist, position, duration, is_playing
"""
import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── PS 공통 초기화 헬퍼 (PS5.1용) ───────────────────────────
_PS51_INIT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
[System.Reflection.Assembly]::LoadFrom("C:\Windows\Microsoft.NET\Framework64\v4.0.30319\System.Runtime.WindowsRuntime.dll") | Out-Null
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime]
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType=WindowsRuntime]
$asTaskBase = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq "AsTask" -and $_.IsGenericMethodDefinition -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
function Await-Op { param($op, [Type]$t); return $asTaskBase.MakeGenericMethod($t).Invoke($null, @($op)).GetAwaiter().GetResult() }
"""

# ── PS7 폴링 스크립트 (pwsh 설치 시) ─────────────────────────
_PS7_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime]

function Get-BestSession($mgr) {
    $sessions = $mgr.GetSessions()
    foreach ($s in $sessions) {
        if ($s.GetPlaybackInfo().PlaybackStatus.ToString() -eq 'Playing') { return $s }
    }
    if ($sessions.Count -gt 0) { return $sessions[0] }
    return $null
}

$mgr = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync().GetAwaiter().GetResult()
while ($true) {
    try {
        $s = Get-BestSession $mgr
        if ($s) {
            $p  = $s.TryGetMediaPropertiesAsync().GetAwaiter().GetResult()
            $tl = $s.GetTimelineProperties()
            $pb = $s.GetPlaybackInfo()
            [PSCustomObject]@{
                title      = [string]$p.Title
                artist     = [string]$p.Artist
                position   = [double]$tl.Position.TotalSeconds
                duration   = [double]$tl.EndTime.TotalSeconds
                is_playing = ($pb.PlaybackStatus.ToString() -eq 'Playing')
            } | ConvertTo-Json -Compress
        } else {
            '{"title":"","artist":"","position":0.0,"duration":0.0,"is_playing":false}'
        }
    } catch {
        '{"title":"","artist":"","position":0.0,"duration":0.0,"is_playing":false}'
    }
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 500
}
"""

# ── PS5.1 폴링 스크립트 ──────────────────────────────────────
_PS51_SCRIPT = _PS51_INIT + r"""
function Get-BestSession($mgr) {
    $sessions = $mgr.GetSessions()
    foreach ($s in $sessions) {
        if ($s.GetPlaybackInfo().PlaybackStatus.ToString() -eq 'Playing') { return $s }
    }
    if ($sessions.Count -gt 0) { return $sessions[0] }
    return $null
}

$mgr = Await-Op ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
while ($true) {
    try {
        $s = Get-BestSession $mgr
        if ($s) {
            $p  = Await-Op ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
            $tl = $s.GetTimelineProperties()
            $pb = $s.GetPlaybackInfo()
            [PSCustomObject]@{
                title      = [string]$p.Title
                artist     = [string]$p.Artist
                position   = [double]$tl.Position.TotalSeconds
                duration   = [double]$tl.EndTime.TotalSeconds
                is_playing = ($pb.PlaybackStatus.ToString() -eq 'Playing')
            } | ConvertTo-Json -Compress
        } else {
            '{"title":"","artist":"","position":0.0,"duration":0.0,"is_playing":false}'
        }
    } catch {
        '{"title":"","artist":"","position":0.0,"duration":0.0,"is_playing":false}'
    }
    [Console]::Out.Flush()
    Start-Sleep -Milliseconds 500
}
"""

# ── 앨범아트 PS7 스크립트 (임시 파일 경유) ──────────────────
_ART_PS7_SCRIPT = r"""
try {
    $null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime]
    $null = [Windows.Storage.Streams.DataReader, Windows.Storage, ContentType=WindowsRuntime]
    $mgr = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync().GetAwaiter().GetResult()
    $sessions = $mgr.GetSessions()
    $s = $null
    foreach ($sess in $sessions) {
        if ($sess.GetPlaybackInfo().PlaybackStatus.ToString() -eq 'Playing') { $s = $sess; break }
    }
    if (-not $s -and $sessions.Count -gt 0) { $s = $sessions[0] }
    if ($s) {
        $p = $s.TryGetMediaPropertiesAsync().GetAwaiter().GetResult()
        if ($p.Thumbnail) {
            $stream = $p.Thumbnail.OpenReadAsync().GetAwaiter().GetResult()
            $size   = [int]$stream.Size
            $dr     = [Windows.Storage.Streams.DataReader]::new($stream)
            $dr.LoadAsync([uint]$size).GetAwaiter().GetResult() | Out-Null
            $bytes  = [System.Byte[]]::new($size)
            $dr.ReadBytes($bytes)
            $tmp = [System.IO.Path]::GetTempFileName()
            [System.IO.File]::WriteAllBytes($tmp, $bytes)
            Write-Output "OK:$tmp"
        }
    }
} catch {}
"""

# ── 앨범아트 PS5.1 스크립트 (AsStreamForRead + 임시 파일) ───
_ART_PS51_SCRIPT = _PS51_INIT + r"""
$null = [Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage, ContentType=WindowsRuntime]
$winrtAsm = [System.AppDomain]::CurrentDomain.GetAssemblies() | Where-Object { $_.GetName().Name -eq "System.Runtime.WindowsRuntime" } | Select-Object -First 1
$streamExtType = $winrtAsm.GetType("System.IO.WindowsRuntimeStreamExtensions")
$asStreamForRead = $streamExtType.GetMethods("Public,Static") | Where-Object { $_.Name -eq "AsStreamForRead" -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
try {
    $mgr = Await-Op ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
    $sessions = $mgr.GetSessions()
    $s = $null
    foreach ($sess in $sessions) {
        if ($sess.GetPlaybackInfo().PlaybackStatus.ToString() -eq 'Playing') { $s = $sess; break }
    }
    if (-not $s -and $sessions.Count -gt 0) { $s = $sessions[0] }
    if ($s) {
        $p = Await-Op ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
        if ($p.Thumbnail) {
            $stream = Await-Op ($p.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
            $dotNetStream = $asStreamForRead.Invoke($null, @($stream))
            $ms = [System.IO.MemoryStream]::new()
            $dotNetStream.CopyTo($ms)
            $bytes = $ms.ToArray()
            if ($bytes.Length -gt 0) {
                $tmp = [System.IO.Path]::GetTempFileName()
                [System.IO.File]::WriteAllBytes($tmp, $bytes)
                Write-Output "OK:$tmp"
            }
        }
    }
} catch {}
"""


class MediaInfo:
    __slots__ = ("title", "artist", "position", "duration", "is_playing")

    def __init__(self, title="", artist="", position=0.0, duration=0.0, is_playing=False):
        self.title: str = title
        self.artist: str = artist
        self.position: float = position
        self.duration: float = duration
        self.is_playing: bool = is_playing

    def is_empty(self) -> bool:
        return not self.title

    def song_id(self) -> str:
        return f"{self.artist}::{self.title}"


class MediaSessionPoller:
    def __init__(self):
        self._info = MediaInfo()
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._proc: Optional[subprocess.Popen] = None
        self._ps_exe: str = "powershell"

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="smtc-ps")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def get_current_info(self) -> MediaInfo:
        with self._lock:
            return MediaInfo(
                self._info.title, self._info.artist,
                self._info.position, self._info.duration,
                self._info.is_playing,
            )

    def get_album_art_base64(self) -> str:
        """앨범아트 취득 (SMTC 임시 파일 → 온라인 API 순으로 폴백)."""
        # 1) SMTC 썸네일 (PS 스크립트를 파일로 저장 후 -File 실행)
        result = self._run_ps_file("pwsh", _ART_PS7_SCRIPT) or \
                 self._run_ps_file("powershell", _ART_PS51_SCRIPT)
        if result and result.startswith("OK:"):
            tmp_path = result[3:].strip()
            try:
                with open(tmp_path, "rb") as f:
                    raw = f.read()
                os.unlink(tmp_path)
                if raw:
                    mime = "image/png" if raw[:4] == b"\x89PNG" else "image/jpeg"
                    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
            except Exception as e:
                logger.debug("앨범아트 파일 읽기 실패: %s", e)

        # 2) 온라인 폴백 — iTunes Search API
        return self._fetch_art_online()

    def _run_ps_file(self, exe: str, script: str, timeout: int = 10) -> str:
        """PS 스크립트를 임시 .ps1 파일로 저장한 뒤 -File 플래그로 실행."""
        tmp_ps = None
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(
                suffix=".ps1", mode="w", encoding="utf-8", delete=False
            ) as f:
                f.write(script)
                tmp_ps = f.name
            r = subprocess.run(
                [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", tmp_ps],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
            )
            return (r.stdout or "").strip()
        except (FileNotFoundError, OSError):
            return ""
        except Exception as e:
            logger.debug("%s -File 실행 오류: %s", exe, e)
            return ""
        finally:
            if tmp_ps:
                try:
                    os.unlink(tmp_ps)
                except Exception:
                    pass

    def _fetch_art_online(self) -> str:
        """iTunes Search API로 앨범아트 URL 조회 후 base64 반환."""
        try:
            import requests
            info = self.get_current_info()
            if not info.title:
                return ""
            query = f"{info.title} {info.artist}".strip()
            r = requests.get(
                "https://itunes.apple.com/search",
                params={"term": query, "entity": "musicTrack", "limit": 5},
                timeout=5,
            )
            if not r.ok:
                return ""
            for item in r.json().get("results", []):
                url = item.get("artworkUrl100", "")
                if not url:
                    continue
                url = url.replace("100x100bb", "600x600bb")
                img = requests.get(url, timeout=5)
                if img.ok and img.content:
                    raw = img.content
                    mime = "image/jpeg" if raw[:2] == b"\xff\xd8" else "image/png"
                    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
        except Exception as e:
            logger.debug("iTunes 앨범아트 실패: %s", e)
        return ""

    # ── 내부 루프 ─────────────────────────────────────────────────────

    def _run(self) -> None:
        proc = self._try_start("pwsh", _PS7_SCRIPT)
        if proc:
            self._ps_exe = "pwsh"
        else:
            proc = self._try_start("powershell", _PS51_SCRIPT)
            self._ps_exe = "powershell"

        if proc:
            self._proc = proc
            self._read_loop(proc)
        else:
            logger.warning("PowerShell SMTC 실패 → 창 제목 폴백")
            self._fallback_loop()

    def _try_start(self, exe: str, script: str) -> Optional[subprocess.Popen]:
        try:
            proc = subprocess.Popen(
                [exe, "-NoProfile", "-NonInteractive", "-Command", script],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8-sig",  # BOM 자동 제거 + UTF-8 디코딩
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            line = proc.stdout.readline()
            if line.strip().startswith("{"):
                self._parse_line(line)
                return proc
            proc.terminate()
            return None
        except (FileNotFoundError, OSError):
            return None
        except Exception as e:
            logger.debug("%s 시작 오류: %s", exe, e)
            return None

    def _read_loop(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            if not self._running:
                break
            self._parse_line(line)
        proc.wait()

    def _parse_line(self, line: str) -> None:
        line = line.strip()
        if not line or not line.startswith("{"):
            return
        try:
            d = json.loads(line)
            with self._lock:
                self._info = MediaInfo(
                    title=str(d.get("title", "")),
                    artist=str(d.get("artist", "")),
                    position=float(d.get("position", 0.0)),
                    duration=float(d.get("duration", 0.0)),
                    is_playing=bool(d.get("is_playing", False)),
                )
        except (json.JSONDecodeError, ValueError):
            pass

    # ── 창 제목 폴백 ──────────────────────────────────────────────────

    def _fallback_loop(self) -> None:
        import win32gui
        while self._running:
            info = MediaInfo()

            def _cb(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = win32gui.GetWindowText(hwnd)
                cls = win32gui.GetClassName(hwnd).lower()
                if "melon" in cls or "melon" in title.lower():
                    m = re.search(r"(.+?)\s*[-–]\s*(.+)", title)
                    if m:
                        info.artist = m.group(1).strip()
                        info.title = m.group(2).strip()
                        info.is_playing = True

            try:
                win32gui.EnumWindows(_cb, None)
            except Exception:
                pass
            with self._lock:
                self._info = info
            time.sleep(0.5)

    # ── 1회성 PS 실행 ─────────────────────────────────────────────────

    @staticmethod
    def _run_ps_once(exe: str, script: str, timeout: int = 10) -> str:
        try:
            r = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return (r.stdout or "").strip()
        except Exception:
            return ""
