"""앱 설정 관리 — config.json 기반 영속성"""
import json
from dataclasses import dataclass, asdict
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config.json"


@dataclass
class Config:
    # 레이아웃
    layout_mode: str = "vertical"      # "horizontal" | "vertical"

    # 창 동작
    pinned: bool = False
    opacity: float = 1.0

    # 창 크기 / 위치 (레이아웃 모드별로 각각 저장)
    h_width: int = -1     # -1 = 화면 전체 너비
    h_height: int = 60
    h_x: int = 0
    h_y: int = -1         # -1 = 화면 하단 taskbar 위

    v_width: int = 300
    v_height: int = -1    # 사용 안 함 (Python에서 비율 계산)
    v_x: int = -1         # -1 = 화면 우측
    v_y: int = -1         # -1 = 화면 하단

    # 폰트 — 세로 뷰
    font_size: int = 15
    font_size_dim: int = 12
    # 폰트 — 가로 뷰
    h_font_size: int = 15
    h_font_size_dim: int = 10
    font_family: str = "Malgun Gothic"

    # 동작
    poll_interval_ms: int = 500

    @classmethod
    def load(cls) -> "Config":
        return cls()

    def save(self) -> None:
        try:
            with CONFIG_PATH.open("w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        except Exception:
            pass
