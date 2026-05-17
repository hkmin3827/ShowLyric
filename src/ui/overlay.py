"""메인 가사 오버레이 창.

[고정 모드] frameless + always-on-top + 반투명 + 드래그 이동
[일반 모드] 일반 창 + 크기 조절 가능
높이에 따라 자동으로 1줄 / 3줄 표시 전환.
"""
import ctypes
import tkinter as tk
from typing import Callable, List, Optional, Tuple

import customtkinter as ctk

from ..config import Config
from ..lyrics_engine import LyricLine

_MULTI_LINE_MIN_H = 140
_FADE_STEPS = 10
_FADE_MS = 18

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class LyricOverlay(ctk.CTk):
    def __init__(self, config: Config, on_settings: Callable):
        super().__init__()
        self.config = config
        self._on_settings = on_settings

        # 상태
        self._current_lyric = ""
        self._prev_lyric = ""
        self._next_lyric = ""
        self._song_label_text = ""
        self._drag_x = 0
        self._drag_y = 0
        self._anim_id: Optional[str] = None

        self._build_window()
        self._build_ui()
        self._setup_bindings()
        self._apply_pin(self.config.pinned, first_time=True)

    # ── 창 초기화 ─────────────────────────────────────────────────────

    def _build_window(self) -> None:
        self.title("🎵 Lyric")
        self._place_window()
        self.configure(fg_color=self.config.bg_color)
        self.resizable(True, True)
        self.minsize(200, 50)

    def _place_window(self) -> None:
        w = self.config.window_width
        h = self.config.window_height
        if self.config.position_x >= 0:
            self.geometry(f"{w}x{h}+{self.config.position_x}+{self.config.position_y}")
        else:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            x = sw - w - 24
            y = (sh - h) // 2
            self.geometry(f"{w}x{h}+{x}+{y}")

    # ── UI 구성 ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._frame = ctk.CTkFrame(
            self,
            fg_color=self.config.bg_color,
            corner_radius=14,
        )
        self._frame.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self._frame.grid_rowconfigure(0, weight=1)
        self._frame.grid_columnconfigure(0, weight=1)

        inner = ctk.CTkFrame(self._frame, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="nsew", padx=12, pady=8)
        inner.grid_columnconfigure(0, weight=1)
        inner.grid_rowconfigure(0, weight=0)
        inner.grid_rowconfigure(1, weight=1)
        inner.grid_rowconfigure(2, weight=0)

        font_dim = ctk.CTkFont(family=self.config.font_family, size=self.config.font_size_dim)
        font_main = ctk.CTkFont(
            family=self.config.font_family,
            size=self.config.font_size,
            weight="bold",
        )

        self._prev_label = ctk.CTkLabel(
            inner,
            text="",
            font=font_dim,
            text_color=self.config.text_color_dim,
            anchor="center",
            wraplength=self.config.window_width - 30,
        )
        self._prev_label.grid(row=0, column=0, sticky="ew")

        self._curr_label = ctk.CTkLabel(
            inner,
            text="♪  재생 중인 곡 없음",
            font=font_main,
            text_color=self.config.text_color_current,
            anchor="center",
            wraplength=self.config.window_width - 30,
        )
        self._curr_label.grid(row=1, column=0, sticky="nsew")

        self._next_label = ctk.CTkLabel(
            inner,
            text="",
            font=font_dim,
            text_color=self.config.text_color_dim,
            anchor="center",
            wraplength=self.config.window_width - 30,
        )
        self._next_label.grid(row=2, column=0, sticky="ew")

        self._inner = inner

    # ── 고정 / 일반 전환 ──────────────────────────────────────────────

    def _apply_pin(self, pinned: bool, first_time: bool = False) -> None:
        self.config.pinned = pinned

        self.withdraw()
        self.overrideredirect(pinned)
        self.attributes("-topmost", pinned)
        self.attributes("-alpha", self.config.opacity if pinned else 1.0)

        if pinned:
            self._set_tool_window()

        if not first_time:
            self._save_position()

        self.deiconify()

    def _set_tool_window(self) -> None:
        """작업 표시줄에 표시 안 되도록 WS_EX_TOOLWINDOW 설정."""
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            if hwnd == 0:
                hwnd = self.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    def toggle_pin(self) -> None:
        self._apply_pin(not self.config.pinned)
        self.config.save()

    # ── 가사 업데이트 (외부 호출) ─────────────────────────────────────

    def update_lyrics(
        self,
        prev: str,
        current: str,
        next_: str,
        song_info: str = "",
    ) -> None:
        if current == self._current_lyric:
            return
        self._prev_lyric = prev
        self._next_lyric = next_
        self._song_label_text = song_info
        self._animate_change(current)

    def show_idle(self, message: str = "♪  재생 중인 곡 없음") -> None:
        if self._current_lyric == message:
            return
        self._current_lyric = message
        self._prev_lyric = ""
        self._next_lyric = ""
        self._curr_label.configure(text=message)
        self._prev_label.configure(text="")
        self._next_label.configure(text="")

    def show_no_lyrics(self, song_info: str) -> None:
        self.show_idle(f"🔍 가사 없음  |  {song_info}")

    # ── 가사 페이드 애니메이션 ────────────────────────────────────────

    def _animate_change(self, new_text: str) -> None:
        if self._anim_id:
            self.after_cancel(self._anim_id)
        self._fade_out(new_text, _FADE_STEPS)

    def _fade_out(self, new_text: str, step: int) -> None:
        if step > 0:
            ratio = step / _FADE_STEPS
            color = _blend(self.config.text_color_current, self.config.bg_color, ratio)
            self._curr_label.configure(text_color=color)
            self._anim_id = self.after(
                _FADE_MS, lambda: self._fade_out(new_text, step - 1)
            )
        else:
            self._current_lyric = new_text
            self._curr_label.configure(text=new_text)
            self._update_dim_labels()
            self._fade_in(_FADE_STEPS)

    def _fade_in(self, step: int) -> None:
        if step > 0:
            ratio = 1 - step / _FADE_STEPS
            color = _blend(self.config.text_color_current, self.config.bg_color, ratio)
            self._curr_label.configure(text_color=color)
            self._anim_id = self.after(
                _FADE_MS, lambda: self._fade_in(step - 1)
            )
        else:
            self._curr_label.configure(text_color=self.config.text_color_current)
            self._anim_id = None

    def _update_dim_labels(self) -> None:
        h = self.winfo_height()
        show_multi = h >= _MULTI_LINE_MIN_H
        self._prev_label.configure(text=self._prev_lyric if show_multi else "")
        self._next_label.configure(text=self._next_lyric if show_multi else "")

    # ── 드래그 ────────────────────────────────────────────────────────

    def _setup_bindings(self) -> None:
        for widget in (self, self._frame, self._inner,
                       self._prev_label, self._curr_label, self._next_label):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)
            widget.bind("<Button-3>", self._show_menu)

        self.bind("<Configure>", self._on_resize)

    def _drag_start(self, event: tk.Event) -> None:
        self._drag_x = event.x_root - self.winfo_x()
        self._drag_y = event.y_root - self.winfo_y()

    def _drag_move(self, event: tk.Event) -> None:
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.geometry(f"+{x}+{y}")

    def _drag_end(self, _event: tk.Event) -> None:
        self._save_position()

    def _on_resize(self, _event: tk.Event) -> None:
        self._update_dim_labels()
        # wraplength 동적 업데이트
        w = self.winfo_width()
        wrap = max(100, w - 30)
        self._curr_label.configure(wraplength=wrap)
        self._prev_label.configure(wraplength=wrap)
        self._next_label.configure(wraplength=wrap)

    # ── 우클릭 메뉴 ───────────────────────────────────────────────────

    def _show_menu(self, event: tk.Event) -> None:
        menu = tk.Menu(self, tearoff=0, bg="#1a1a1a", fg="#ffffff",
                       activebackground="#6C63FF", activeforeground="#ffffff",
                       borderwidth=0)
        pin_label = "📌 고정 해제" if self.config.pinned else "📌 오버레이로 고정"
        menu.add_command(label=pin_label, command=self.toggle_pin)
        menu.add_separator()
        menu.add_command(label="⚙️  설정", command=self._on_settings)
        menu.add_separator()
        menu.add_command(label="✕  닫기", command=self.quit)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ── 유틸 ─────────────────────────────────────────────────────────

    def _save_position(self) -> None:
        self.config.position_x = self.winfo_x()
        self.config.position_y = self.winfo_y()
        self.config.window_width = self.winfo_width()
        self.config.window_height = self.winfo_height()
        self.config.save()

    def apply_config(self) -> None:
        """설정 창에서 변경 후 호출 — 색상/투명도/폰트 즉시 반영."""
        self.configure(fg_color=self.config.bg_color)
        self._frame.configure(fg_color=self.config.bg_color)
        self.attributes("-alpha", self.config.opacity if self.config.pinned else 1.0)

        font_main = ctk.CTkFont(
            family=self.config.font_family,
            size=self.config.font_size,
            weight="bold",
        )
        font_dim = ctk.CTkFont(
            family=self.config.font_family,
            size=self.config.font_size_dim,
        )
        self._curr_label.configure(
            font=font_main,
            text_color=self.config.text_color_current,
        )
        self._prev_label.configure(font=font_dim, text_color=self.config.text_color_dim)
        self._next_label.configure(font=font_dim, text_color=self.config.text_color_dim)


# ── 유틸 함수 ─────────────────────────────────────────────────────────

def _blend(c1: str, c2: str, t: float) -> str:
    """두 hex 색상을 t(0~1) 비율로 섞기. t=1 → c1, t=0 → c2."""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 * t + r2 * (1 - t))
    g = int(g1 * t + g2 * (1 - t))
    b = int(b1 * t + b2 * (1 - t))
    return f"#{r:02x}{g:02x}{b:02x}"
