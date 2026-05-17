# 🎵 쇼리릭 (ShowLyric)

> Windows용 실시간 가사 오버레이 앱 — 현재 재생 중인 음악의 가사를 화면에 실시간으로 표시합니다.

---

## 목차

- [개요](#개요)
- [기술 스택](#기술-스택)
- [프로젝트 구조](#프로젝트-구조)
- [동작 방식](#동작-방식)
- [서비스 플로우](#서비스-플로우)
- [설치 및 실행](#설치-및-실행)
- [설정](#설정)
- [레이아웃 모드](#레이아웃-모드)

---

## 개요

쇼리릭은 Windows SMTC(System Media Transport Controls)를 통해 현재 재생 중인 곡 정보를 실시간으로 감지하고, LRCLIB.net에서 싱크 가사를 가져와 화면 오버레이로 표시하는 개인용 데스크톱 앱입니다.

**핵심 특징:**
- 모든 SMTC 지원 플레이어 호환 (멜론, Spotify, Windows Media Player 등)
- 가사 싱크 정확도: 300ms 폴링
- 가로 모드(화면 하단 바) / 세로 모드(iPod 스타일) 두 가지 레이아웃
- 앨범아트 표시 (SMTC 썸네일 → iTunes API 폴백)
- 시스템 트레이 상주, 항상 위 고정 옵션
- 가사 로컬 캐시 (반복 API 호출 없음)

---

## 기술 스택

| 분류 | 기술 | 버전 | 용도 |
|------|------|------|------|
| **런타임** | Python | 3.11+ | 백엔드 전체 |
| **UI 렌더링** | pywebview + WebView2 | 6.2+ | 프레임리스 오버레이 창 |
| **시스템 트레이** | pystray | 0.19+ | 트레이 아이콘·메뉴 |
| **미디어 감지** | Windows SMTC (WinRT) | — | 현재 재생곡 폴링 |
| **SMTC 브리지** | PowerShell 5.1 | — | WinRT → JSON 변환 |
| **가사 API** | LRCLIB.net | — | 싱크 가사 검색 |
| **앨범아트 폴백** | iTunes Search API | — | 썸네일 없을 때 대체 |
| **볼륨 제어** | pycaw + comtypes | — | 시스템 마스터 볼륨 |
| **이미지 처리** | Pillow | 10+ | 트레이 아이콘 생성 |
| **창 관리** | pywin32 (win32gui) | 306+ | 창 위치·상태 저장 |
| **프론트엔드** | HTML5 + CSS3 + Vanilla JS | — | 젤리 핑크 테마 UI |
| **보안** | 자체 validator.py | — | API 응답 새니타이징 |

---

## 프로젝트 구조

```
lyric-app/
│
├── run.py                      # 진입점 — python run.py 로 실행
│
├── src/                        # Python 백엔드 패키지
│   ├── __init__.py
│   ├── main.py                 # App 클래스: 창 생성·트레이·이벤트 조율
│   ├── api.py                  # LyricApi: JS↔Python 브리지 (pywebview js_api)
│   ├── lyrics_engine.py        # 가사 조회·파싱·위치 동기화
│   ├── media_session.py        # Windows SMTC 폴링 (PowerShell 서브프로세스)
│   ├── cache_manager.py        # 가사 캐시 (메모리 + 디스크)
│   ├── config.py               # 설정 관리 (config.json 읽기·쓰기)
│   │
│   ├── security/
│   │   └── validator.py        # 입력값 검증·새니타이징
│   │
│   └── ui/                     # (예비 — 현재 미사용)
│       ├── overlay.py
│       └── settings_window.py
│
├── frontend/                   # 웹 UI (pywebview로 로드)
│   ├── index.html              # 전체 HTML 구조 (가로뷰·세로뷰·설정 패널)
│   ├── app.js                  # 폴링 루프·이벤트 바인딩·가사 렌더링
│   └── style.css               # 젤리 핑크 테마 (글라스모피즘)
│
├── .cache/
│   └── lyrics.json             # 가사 로컬 캐시 (최대 500곡)
│
├── config.json                 # 창 크기·위치·레이아웃 등 사용자 설정
├── requirements.txt            # pip 의존성 목록
├── .env.example                # 환경변수 샘플
│
├── tasks/
│   ├── todo.md                 # 개발 작업 체크리스트
│   └── progress.md             # 완료 작업 기록
│
└── SECURITY.md                 # 보안 비상 매뉴얼
```

---

## 동작 방식

### 1. 미디어 감지 (media_session.py)

Windows SMTC를 직접 쿼리하기 위해 PowerShell 서브프로세스를 영구 실행합니다.

```
Python ──spawn──▶ PowerShell 5.1 프로세스
                    │
                    ├─ WinRT Assembly 로드 (System.Runtime.WindowsRuntime.dll)
                    ├─ GlobalSystemMediaTransportControlsSessionManager 초기화
                    └─ 500ms 루프: 현재 재생 정보 → JSON → stdout
                                    │
Python ◀──readline──┘
  (title, artist, position, duration, is_playing)
```

- pwsh(PS7) 설치 시 우선 사용, 없으면 PS5.1 폴백
- PS5.1에서 WinRT 비동기 API를 reflection으로 동기 호출 (`AsTask<T>` 제네릭 메서드)
- 출력 인코딩: `[Console]::OutputEncoding = UTF8` → Python에서 `utf-8-sig` 디코딩

### 2. 가사 조회 (lyrics_engine.py)

```
get_lyrics(title, artist)
    │
    ├─ 1) CacheManager.get() → 캐시 히트 시 즉시 반환
    │
    ├─ 2) LRCLIB /api/get (정확한 매칭)
    │       ├─ 200 OK → syncedLyrics(LRC) 파싱
    │       └─ 404 → /api/search 폴백 (퍼지 검색)
    │
    ├─ 3) 아티스트 없이 재시도
    │
    └─ 4) 결과 없음 → 빈 배열 캐시 (재요청 방지)
```

- LRC 형식: `[mm:ss.xx] 가사 텍스트` → `(timestamp_seconds, text)` 리스트로 변환
- 타임스탬프 없는 plain 가사는 3초 간격으로 균등 배치

### 3. JS-Python 브리지 (api.py)

pywebview의 `js_api` 기능을 통해 JS에서 Python 메서드를 직접 호출합니다.

```javascript
// JS 쪽 (300ms마다 폴링)
const state = await window.pywebview.api.get_current_state();
```

| JS 호출 | Python 메서드 | 반환값 |
|---------|--------------|--------|
| `get_current_state()` | `LyricApi.get_current_state()` | 현재 가사·재생 상태 dict |
| `get_album_art()` | `LyricApi.get_album_art()` | base64 data URL |
| `set_layout_mode(mode)` | `LyricApi.set_layout_mode()` | 창 위치·크기 변경 |
| `set_volume(pct)` | `LyricApi.set_volume()` | pycaw로 시스템 볼륨 설정 |
| `toggle_pin()` | `LyricApi.toggle_pin()` | 항상 위 고정 토글 |
| `save_config(data)` | `LyricApi.save_config()` | config.json 저장 |

> **주의:** `LyricApi`의 공개 속성은 pywebview가 자동으로 JS에 노출합니다. 내부용 속성은 반드시 `_` 언더스코어로 시작해야 합니다 (예: `self._win`, `self._cfg`).

### 4. UI 렌더링 (frontend/)

pywebview가 `frontend/index.html`을 WebView2(Chromium 기반)로 렌더링합니다.

- **가로 모드**: 화면 하단 전체 너비 바 — 앨범아트 + 가사(이전·현재·다음) + 미디어 컨트롤
- **세로 모드**: 우측 고정 패널 — iPod 스타일 휠 + 앨범아트 + 진행바 + 가사

---

## 서비스 플로우

```
[사용자가 멜론/Spotify 등에서 음악 재생]
            │
            ▼
[Windows SMTC에 현재 재생 정보 등록]
            │
            ▼ (500ms마다)
[PowerShell SMTC 폴러]
  → title, artist, position, duration, is_playing
            │
            ▼
[MediaSessionPoller._parse_line()]
  → MediaInfo 객체에 저장 (thread-safe lock)
            │
            ▼ (JS에서 300ms마다 get_current_state() 호출)
[LyricApi.get_current_state()]
  ┌─ 곡 변경 감지 (song_id 비교)?
  │     YES → LyricsEngine.get_lyrics(title, artist)
  │              ├─ 캐시 히트 → 즉시 반환
  │              └─ LRCLIB API 호출 → LRC 파싱 → 캐시 저장
  └─ 재생 위치 → get_current_index() → get_context()
                   → (이전가사, 현재가사, 다음가사)
            │
            ▼
[JS updateLyrics()] → 화면 가사 업데이트 (fade 애니메이션)

[앨범아트 — 곡 변경 시 1회]
  1) SMTC 썸네일 → PowerShell로 임시 파일 추출 → base64
  2) 실패 시 iTunes Search API → 600×600 이미지 → base64
            │
            ▼
[img 태그 src = data:image/jpeg;base64,...]
```

---

## 설치 및 실행

### 사전 조건
- Windows 10/11 (SMTC 필수)
- Python 3.11 이상
- WebView2 런타임 (엣지 브라우저 설치 시 자동 포함)

### 설치

```bash
# 저장소 클론 후
cd lyric-app
pip install -r requirements.txt
```

### 실행

```bash
python run.py
```

### 개발 모드 (DevTools 열기)

`src/main.py`에서 `debug=True`로 변경:
```python
webview.start(func=self._on_start, debug=True)
```

---

## 설정

`config.json`에 자동 저장됩니다. 앱 내 설정 패널(우클릭 또는 ⚙ 버튼)에서 변경 가능합니다.

| 키 | 기본값 | 설명 |
|----|--------|------|
| `layout_mode` | `"horizontal"` | 레이아웃 모드 |
| `pinned` | `false` | 항상 위 고정 |
| `opacity` | `0.96` | 창 불투명도 |
| `h_height` | `88` | 가로 모드 높이(px) |
| `v_width` | `360` | 세로 모드 너비(px) |
| `font_size` | `17` | 현재 가사 폰트 크기 |
| `font_size_dim` | `12` | 이전·다음 가사 폰트 크기 |

---

## 레이아웃 모드

### 가로 모드 (Horizontal)
```
┌─────────────────────────────────────────────────────────────┐
│ [앨범아트]  이전 가사                               ◀ ⏯ ▶ 🔊│
│            ★ 현재 가사 (크고 굵게)                  [세로↕] │
│            다음 가사                                         │
└─────────────────────────────────────────────────────────────┘
(화면 하단 고정, 전체 너비)
```

### 세로 모드 (Vertical)
```
┌──────────┐
│ 쇼리릭 ↔📌⚙│
├──────────┤
│ [앨범아트] │
├──────────┤
│ 곡명      │
│ 아티스트  │
├──────────┤
│ ━━━━━━━○ │  ← 진행바
│ 0:00  3:45│
├──────────┤
│ 이전 가사 │
│ 현재 가사 │
│ 다음 가사 │
├──────────┤
│  MENU    │
│◀ (●) ▶  │  ← iPod 휠
│  VOL     │
└──────────┘
(화면 우측 고정, 전체 높이)
```
