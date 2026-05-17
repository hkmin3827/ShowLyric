"""입력 검증 + API 응답 새니타이징"""
import re
from typing import Any, Optional

_MAX_LEN = 500
_ALLOWED_PATTERN = re.compile(r"[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # 제어문자 제거


def sanitize_text(text: Any, max_len: int = _MAX_LEN) -> str:
    """외부 텍스트를 정제"""
    if not isinstance(text, str):
        return ""
    cleaned = "".join(_ALLOWED_PATTERN.findall(text))
    return cleaned[:max_len].strip()


def validate_lrclib_response(data: Any) -> Optional[dict]:
    """LRCLIB API 응답 구조 검증"""
    if not isinstance(data, dict):
        return None
    required = {"id", "trackName", "artistName"}
    if not required.issubset(data.keys()):
        return None
    return data


def sanitize_search_query(query: str) -> str:
    """검색어에서 위험 문자 제거"""
    cleaned = re.sub(r"[<>\"';&|`$\\]", "", query)
    return cleaned[:200].strip()
