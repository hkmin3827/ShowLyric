"""앱 설정 관리 — config.json 기반 영속성"""
import json
from dataclasses import dataclass, asdict
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"


@dataclass
class Config:
    # 레이아웃
    layout_mode: str = "horizontal"   # "horizontal" | "vertical"

    # 창 동작
    pinned: bool = False
    opacity: float = 0.96

    # 창 크기 / 위치 (레이아웃 모드별로 각각 저장)
    h_width: int = -1     # -1 = 화면 전체 너비
    h_height: int = 88
    h_x: int = 0
    h_y: int = -1         # -1 = 화면 하단 taskbar 위

    v_width: int = 360
    v_height: int = -1    # -1 = 화면 전체 높이
    v_x: int = -1         # -1 = 화면 우측
    v_y: int = 0

    # 폰트
    font_size: int = 17
    font_size_dim: int = 12
    font_family: str = "Malgun Gothic"

    # 동작
    poll_interval_ms: int = 500

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            return cls()
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            return cls(**valid)
        except Exception:
            return cls()

    def save(self) -> None:
        try:
            with CONFIG_PATH.open("w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        except Exception:
            pass
