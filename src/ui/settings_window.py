"""설정 창 — 오버레이 고정, 투명도, 글자 크기, 위치 초기화 등."""
from typing import Callable

import customtkinter as ctk

from ..config import Config


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, parent, config: Config, on_apply: Callable, on_toggle_pin: Callable):
        super().__init__(parent)
        self.config = config
        self._on_apply = on_apply
        self._on_toggle_pin = on_toggle_pin

        self.title("⚙️  Lyric 설정")
        self.geometry("360x460")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.grab_set()  # 모달 동작

        self._build_ui()

    # ── UI 구성 ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)

        # 제목
        ctk.CTkLabel(
            self, text="⚙️  설정", font=ctk.CTkFont(size=18, weight="bold")
        ).grid(row=0, column=0, pady=(20, 4))

        ctk.CTkLabel(
            self, text="우클릭 메뉴로도 빠르게 전환 가능",
            font=ctk.CTkFont(size=11), text_color="#888888"
        ).grid(row=1, column=0, pady=(0, 16))

        frame = ctk.CTkScrollableFrame(self, width=320, height=330)
        frame.grid(row=2, column=0, padx=20, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)

        row = 0

        # ── 오버레이 고정 ──
        row = self._section(frame, row, "📌  오버레이 고정")
        self._pin_var = ctk.BooleanVar(value=self.config.pinned)
        pin_switch = ctk.CTkSwitch(
            frame,
            text="고정하면 frameless + 항상 위 오버레이",
            variable=self._pin_var,
            font=ctk.CTkFont(size=12),
        )
        pin_switch.grid(row=row, column=0, sticky="w", padx=16, pady=(0, 12))
        row += 1

        # ── 투명도 ──
        row = self._section(frame, row, "🌫️  투명도")
        self._opacity_var = ctk.DoubleVar(value=self.config.opacity)
        self._opacity_label = ctk.CTkLabel(
            frame, text=f"{int(self.config.opacity * 100)}%",
            font=ctk.CTkFont(size=12)
        )
        self._opacity_label.grid(row=row, column=0, sticky="e", padx=16)
        row += 1
        ctk.CTkSlider(
            frame,
            from_=0.2, to=1.0, number_of_steps=80,
            variable=self._opacity_var,
            command=self._update_opacity_label,
        ).grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 12))
        row += 1

        # ── 현재 가사 글자 크기 ──
        row = self._section(frame, row, "🔤  현재 가사 글자 크기")
        self._font_var = ctk.IntVar(value=self.config.font_size)
        self._font_label = ctk.CTkLabel(
            frame, text=f"{self.config.font_size}px",
            font=ctk.CTkFont(size=12)
        )
        self._font_label.grid(row=row, column=0, sticky="e", padx=16)
        row += 1
        ctk.CTkSlider(
            frame,
            from_=10, to=36, number_of_steps=26,
            variable=self._font_var,
            command=self._update_font_label,
        ).grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 12))
        row += 1

        # ── 이전/다음 가사 글자 크기 ──
        row = self._section(frame, row, "🔡  이전/다음 가사 글자 크기")
        self._font_dim_var = ctk.IntVar(value=self.config.font_size_dim)
        self._font_dim_label = ctk.CTkLabel(
            frame, text=f"{self.config.font_size_dim}px",
            font=ctk.CTkFont(size=12)
        )
        self._font_dim_label.grid(row=row, column=0, sticky="e", padx=16)
        row += 1
        ctk.CTkSlider(
            frame,
            from_=8, to=24, number_of_steps=16,
            variable=self._font_dim_var,
            command=self._update_font_dim_label,
        ).grid(row=row, column=0, sticky="ew", padx=16, pady=(0, 12))
        row += 1

        # ── 위치 초기화 ──
        row = self._section(frame, row, "📍  창 위치")
        ctk.CTkButton(
            frame,
            text="우측 기본 위치로 초기화",
            width=200,
            font=ctk.CTkFont(size=12),
            fg_color="#333333",
            hover_color="#444444",
            command=self._reset_position,
        ).grid(row=row, column=0, pady=(0, 16), padx=16, sticky="w")
        row += 1

        # ── 버튼 영역 ──
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.grid(row=3, column=0, pady=16, padx=20, sticky="ew")
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            btn_frame, text="저장", command=self._save,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, padx=(0, 6), sticky="ew")

        ctk.CTkButton(
            btn_frame, text="취소",
            fg_color="#333333", hover_color="#444444",
            font=ctk.CTkFont(size=13),
            command=self.destroy,
        ).grid(row=0, column=1, padx=(6, 0), sticky="ew")

    @staticmethod
    def _section(frame, row: int, title: str) -> int:
        ctk.CTkLabel(
            frame, text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=8, pady=(8, 4))
        return row + 1

    # ── 콜백 ──────────────────────────────────────────────────────────

    def _update_opacity_label(self, val) -> None:
        self._opacity_label.configure(text=f"{int(float(val) * 100)}%")

    def _update_font_label(self, val) -> None:
        self._font_label.configure(text=f"{int(float(val))}px")

    def _update_font_dim_label(self, val) -> None:
        self._font_dim_label.configure(text=f"{int(float(val))}px")

    def _reset_position(self) -> None:
        self.config.position_x = -1
        self.config.position_y = -1
        self.config.save()

    def _save(self) -> None:
        pin_changed = self._pin_var.get() != self.config.pinned

        self.config.opacity = round(self._opacity_var.get(), 2)
        self.config.font_size = int(self._font_var.get())
        self.config.font_size_dim = int(self._font_dim_var.get())
        self.config.save()

        self._on_apply()

        if pin_changed:
            self._on_toggle_pin()

        self.destroy()
