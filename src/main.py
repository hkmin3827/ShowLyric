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
        webview.start(func=self._on_start, debug=True)

    # ── 이벤트 콜백 ──────────────────────────────────────────────────

    def _on_start(self) -> None:
        """webview 루프 시작 후 별도 스레드에서 호출."""
        self._poller.start()
        # webview.screens는 start() 이후에만 유효 → 가로 모드 창 너비 정확히 재적용
        if self.config.layout_mode == "horizontal":
            self._fix_horizontal_width()
        self._tray = self._create_tray()
        threading.Thread(target=self._tray.run, daemon=True, name="tray").start()
        if self.config.pinned:
            self._api._apply_topmost()

    def _fix_horizontal_width(self) -> None:
        """가로 모드: pywebview가 인식하는 실제 화면 너비로 창 크기 보정."""
        try:
            screens = webview.screens
            if not screens:
                return
            sw = screens[0].width
            sh = screens[0].height
            tb_h = self._api._taskbar_height()
            h = self.config.h_height
            y = sh - h - tb_h
            self._api._move_window_atomic(0, y, sw, h)
        except Exception as e:
            import logging
            logging.getLogger(__name__).debug("가로 모드 너비 보정 실패: %s", e)

    def _on_loaded(self) -> None:
        cfg_json = json.dumps(asdict(self.config), ensure_ascii=False)
        # config만 주입 — initApp은 pywebviewready 이벤트가 발생할 때 JS에서 호출됨
        self._window.evaluate_js(f"window.__initialConfig = {cfg_json};")

    def _on_closed(self) -> None:
        self._save_state()
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
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        tb_h = self._api._taskbar_height()

        if self.config.layout_mode == "horizontal":
            w = sw
            h = self.config.h_height
            x = self.config.h_x if self.config.h_x >= 0 else 0
            y_saved = self.config.h_y
            y = y_saved if (0 <= y_saved <= sh - h) else (sh - h - tb_h)
        else:
            w = self.config.v_width
            h = sh - tb_h
            x_saved = self.config.v_x
            x = x_saved if (0 <= x_saved <= sw - w) else (sw - w)
            y = self.config.v_y if self.config.v_y >= 0 else 0
        return x, y, w, h

    def _save_state(self) -> None:
        try:
            import win32gui
            hwnd = win32gui.FindWindow(None, "Lyric")
            if hwnd:
                rect = win32gui.GetWindowRect(hwnd)
                x, y = rect[0], rect[1]
                w, h = rect[2] - rect[0], rect[3] - rect[1]
                if self.config.layout_mode == "horizontal":
                    self.config.h_x, self.config.h_y, self.config.h_height = x, y, h
                else:
                    self.config.v_x, self.config.v_y, self.config.v_width = x, y, w
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
