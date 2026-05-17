"""로컬 가사 캐시 — .cache/lyrics.json (디스크) + 메모리"""
import json
import os
import hashlib
from pathlib import Path
from typing import Optional, List, Tuple

CACHE_DIR = Path(__file__).parent.parent / ".cache"
CACHE_FILE = CACHE_DIR / "lyrics.json"
MAX_ENTRIES = 500


class CacheManager:
    def __init__(self):
        CACHE_DIR.mkdir(exist_ok=True)
        self._memory: dict = {}
        self._disk: dict = self._load_disk()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------
    def get(self, title: str, artist: str) -> Optional[List[Tuple[float, str]]]:
        key = self._key(title, artist)
        if key in self._memory:
            return self._memory[key]
        if key in self._disk:
            data = self._disk[key]
            self._memory[key] = data
            return data
        return None

    def set(self, title: str, artist: str, lyrics: List[Tuple[float, str]]) -> None:
        key = self._key(title, artist)
        serializable = [[t, text] for t, text in lyrics]
        self._memory[key] = lyrics
        self._disk[key] = serializable
        self._save_disk()

    def set_empty(self, title: str, artist: str) -> None:
        """가사 없음을 캐시 — 반복 API 호출 방지"""
        key = self._key(title, artist)
        self._memory[key] = []
        self._disk[key] = []
        self._save_disk()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------
    @staticmethod
    def _key(title: str, artist: str) -> str:
        raw = f"{title.lower().strip()}::{artist.lower().strip()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _load_disk(self) -> dict:
        if not CACHE_FILE.exists():
            return {}
        try:
            with CACHE_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            # [[timestamp, text], ...] → [(float, str), ...]
            converted = {}
            for k, v in data.items():
                if isinstance(v, list):
                    converted[k] = [(float(item[0]), str(item[1])) for item in v if len(item) == 2]
            return converted
        except Exception:
            return {}

    def _save_disk(self) -> None:
        try:
            # 최대 항목 수 유지
            if len(self._disk) > MAX_ENTRIES:
                keys = list(self._disk.keys())
                for k in keys[: len(keys) - MAX_ENTRIES]:
                    del self._disk[k]
            with CACHE_FILE.open("w", encoding="utf-8") as f:
                json.dump(self._disk, f, ensure_ascii=False)
        except Exception:
            pass
