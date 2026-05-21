import ctypes
import json
import logging
import os
import threading
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))

# 앱 종료 시 WebView2 삭제 후 pywebview 내부에서 발생하는 무해한 ObjectDisposedException 억제
class _IgnoreDisposed(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "ObjectDisposedException" not in str(record.getMessage())

logging.getLogger("pywebview").addFilter(_IgnoreDisposed())

import webview
import pystray
from PIL import Image, ImageDraw

from .api import LyricApi
from .cache_manager import CacheManager
from .config import Config
from .lyrics_engine import LyricsEngine
from .media_session import MediaSessionPoller

_FRONTEND = Path(__file__).parent.parent / "frontend" / "index.html"
_V_IMG_H_RATIO = 1306 / 691  # mp3-UI.png 이미지 세로/가로 비율


class App:
    def __init__(self):
        self.config = Config.load()
        cache = CacheManager()
        lyrics_engine = LyricsEngine(cache)
        self._poller = MediaSessionPoller()
        self._api = LyricApi(self._poller, lyrics_engine, self.config)
        self._tray: pystray.Icon | None = None

        x, y, w, h = self._initial_geometry()

        self._window = webview.create_window(
            title="Lyric",           # FindWindow 키 — 변경 금지
            url=_FRONTEND.as_uri(),
            js_api=self._api,
            width=w, height=h, x=x, y=y,
            frameless=True,
            on_top=self.config.pinned,
            background_color="#f8a0c8",
            resizable=True,
            min_size=(180, 50),
        )
        self._api._win = self._window
        self._window.events.closed  += self._on_closed
        self._window.events.loaded  += self._on_loaded

    # ── 실행 ─────────────────────────────────────────────────────────

    def run(self) -> None:
        webview.start(func=self._on_start, debug=False)

    # ── 이벤트 콜백 ──────────────────────────────────────────────────

    def _on_start(self) -> None:
        """webview 루프 시작 후 별도 스레드에서 호출."""
        self._poller.start()
        self._tray = self._create_tray()
        threading.Thread(target=self._tray.run, daemon=True, name="tray").start()
        if self.config.pinned:
            self._api._apply_topmost()

    def _on_loaded(self) -> None:
        cfg_json = json.dumps(asdict(self.config), ensure_ascii=False)
        # config만 주입 — initApp은 pywebviewready 이벤트가 발생할 때 JS에서 호출됨
        self._window.evaluate_js(f"window.__initialConfig = {cfg_json};")
        self._api._apply_opacity()

    def _on_closed(self) -> None:
        # 종료 시 창 상태 저장 안 함 — DPI 스케일링으로 값 왜곡 방지
        # 설정 변경은 save_config/set_layout_mode에서 즉시 저장됨
        self._poller.stop()
        if self._tray:
            self._tray.stop()

    # ── 트레이 ───────────────────────────────────────────────────────

    def _create_tray(self) -> pystray.Icon:
        img = self._make_icon()

        def on_show(icon, item):
            try:
                self._window.show()
            except Exception:
                pass

        def on_settings(icon, item):
            try:
                self._window.evaluate_js(
                    "document.getElementById('settings-panel').classList.toggle('hidden');"
                )
            except Exception:
                pass

        def on_quit(icon, item):
            icon.stop()
            try:
                self._window.destroy()
            except Exception:
                pass

        menu = pystray.Menu(
            pystray.MenuItem("쇼리릭", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("표시", on_show),
            pystray.MenuItem("⚙  설정", on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", on_quit),
        )
        return pystray.Icon("쇼리릭", img, "🎵 쇼리릭", menu)

    # ── 유틸 ─────────────────────────────────────────────────────────

    def _initial_geometry(self) -> tuple[int, int, int, int]:
        """_work_area() 기준 논리 픽셀로 초기 창 위치·크기 계산."""
        sw, sh, _ = self._api._work_area()
        if self.config.layout_mode == "horizontal":
            w, h = sw, self.config.h_height
            x, y = 0, sh - h
        else:
            v_w = self.config.v_width
            w = v_w if (200 <= v_w <= sw // 2) else 360
            h = min(round(w * _V_IMG_H_RATIO), sh)
            x, y = sw - w, sh - h
        return x, y, w, h

    def _save_state(self) -> None:
        # 세로 모드: 위치/크기 저장 안 함 — DPI 스케일링으로 값이 계속 바뀌는 문제 방지
        # 가로 모드: 높이만 저장 (전체 너비는 항상 화면 너비라 저장 불필요)
        if self.config.layout_mode == "horizontal":
            try:
                import win32gui
                hwnd = win32gui.FindWindow(None, "Lyric")
                if hwnd:
                    rect = win32gui.GetWindowRect(hwnd)
                    h = rect[3] - rect[1]
                    self.config.h_height = h
            except Exception:
                pass
        self.config.save()

    @staticmethod
    def _make_icon() -> Image.Image:
        sz = 64
        img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # 핑크 원 배경
        d.ellipse([2, 2, sz-2, sz-2], fill=(232, 112, 168, 230))
        # 흰 하이라이트 (젤리 느낌)
        d.ellipse([8, 8, 34, 30], fill=(255, 255, 255, 90))
        # 음표
        d.rectangle([36, 14, 42, 42], fill=(255, 255, 255, 220))
        d.ellipse([22, 36, 40, 50], fill=(255, 255, 255, 220))
        d.arc([36, 14, 52, 28], start=0, end=180, fill=(255, 255, 255, 220), width=4)
        return img


def main() -> None:
    App().run()
