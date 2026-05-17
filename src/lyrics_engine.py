"""가사 엔진 — LRCLIB.net API 호출 + LRC 파싱 + 재생 위치 동기화."""
import re
import time
import logging
from typing import List, Optional, Tuple

import requests

from .cache_manager import CacheManager
from .security.validator import sanitize_text, sanitize_search_query, validate_lrclib_response

logger = logging.getLogger(__name__)

_LRCLIB_BASE = "https://lrclib.net/api"
_TIMEOUT = 8  # 초
_LRC_PATTERN = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)")

LyricLine = Tuple[float, str]   # (timestamp_seconds, text)


class LyricsEngine:
    def __init__(self, cache: CacheManager):
        self._cache = cache
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "lyric-app/1.0 (personal-use)",
            "Accept": "application/json",
        })

    def get_lyrics(self, title: str, artist: str) -> List[LyricLine]:
        """캐시 → LRCLIB 순으로 가사 조회. 타임스탬프 포함."""
        cached = self._cache.get(title, artist)
        if cached is not None:
            return cached

        lyrics = self._fetch_synced(title, artist)
        if lyrics:
            self._cache.set(title, artist, lyrics)
            return lyrics

        # 아티스트 없이 재시도 (feat. 표기 등으로 검색 실패 시)
        if artist:
            lyrics = self._fetch_synced(title, "")
            if lyrics:
                self._cache.set(title, artist, lyrics)
                return lyrics

        self._cache.set_empty(title, artist)
        return []

    @staticmethod
    def get_current_index(lyrics: List[LyricLine], position: float) -> int:
        """재생 위치(초)에 해당하는 가사 인덱스 반환."""
        if not lyrics:
            return -1
        idx = 0
        for i, (ts, _) in enumerate(lyrics):
            if ts <= position:
                idx = i
            else:
                break
        return idx

    @staticmethod
    def get_context(
        lyrics: List[LyricLine], idx: int
    ) -> Tuple[str, str, str]:
        """(이전 가사, 현재 가사, 다음 가사) 반환."""
        if not lyrics or idx < 0:
            return "", "", ""
        prev_text = lyrics[idx - 1][1] if idx > 0 else ""
        curr_text = lyrics[idx][1]
        next_text = lyrics[idx + 1][1] if idx < len(lyrics) - 1 else ""
        return prev_text, curr_text, next_text

    # ── LRCLIB 호출 ──────────────────────────────────────────────────

    def _fetch_synced(self, title: str, artist: str) -> Optional[List[LyricLine]]:
        params: dict = {"track_name": sanitize_search_query(title)}
        if artist:
            params["artist_name"] = sanitize_search_query(artist)

        try:
            resp = self._session.get(
                f"{_LRCLIB_BASE}/get",
                params=params,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as e:
            logger.warning("LRCLIB 요청 실패: %s", e)
            return None

        if resp.status_code == 404:
            return self._fetch_via_search(title, artist)  # /search 폴백
        if resp.status_code != 200:
            logger.warning("LRCLIB 응답 오류: %s", resp.status_code)
            return None

        try:
            data = resp.json()
        except ValueError:
            return None

        if not validate_lrclib_response(data):
            return None

        synced = data.get("syncedLyrics") or ""
        if synced:
            return self._parse_lrc(synced)

        # 타임스탬프 없는 일반 가사 → 균등 분배
        plain = sanitize_text(data.get("plainLyrics") or "")
        if plain:
            return self._distribute_plain(plain)

        return None

    def _fetch_via_search(self, title: str, artist: str) -> Optional[List[LyricLine]]:
        """/api/get 404 시 /api/search 폴백."""
        q = sanitize_search_query(f"{title} {artist}".strip())
        try:
            resp = self._session.get(
                f"{_LRCLIB_BASE}/search",
                params={"q": q},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as e:
            logger.warning("LRCLIB 검색 실패: %s", e)
            return None

        if resp.status_code != 200:
            return None
        try:
            results = resp.json()
        except ValueError:
            return None
        if not isinstance(results, list):
            return None

        for item in results:
            if not validate_lrclib_response(item):
                continue
            synced = item.get("syncedLyrics") or ""
            if synced:
                return self._parse_lrc(synced)
            plain = sanitize_text(item.get("plainLyrics") or "")
            if plain:
                return self._distribute_plain(plain)
        return None

    # ── LRC 파싱 ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_lrc(lrc_text: str) -> List[LyricLine]:
        lines: List[LyricLine] = []
        for match in _LRC_PATTERN.finditer(lrc_text):
            mins, secs, ms_raw, text = match.groups()
            ms = int(ms_raw.ljust(3, "0"))
            timestamp = int(mins) * 60 + int(secs) + ms / 1000
            cleaned = sanitize_text(text).strip()
            if cleaned:
                lines.append((timestamp, cleaned))
        return sorted(lines, key=lambda x: x[0])

    @staticmethod
    def _distribute_plain(plain: str) -> List[LyricLine]:
        """타임스탬프 없는 가사를 3초 간격으로 균등 배치."""
        lines = [sanitize_text(l).strip() for l in plain.splitlines() if l.strip()]
        return [(i * 3.0, text) for i, text in enumerate(lines)]
