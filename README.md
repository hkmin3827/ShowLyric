# 쇼리릭 (ShowLyric)

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
- [UI 커스터마이징](#ui-커스터마이징)

---

## 개요

쇼리릭은 Windows SMTC(System Media Transport Controls)를 통해 현재 재생 중인 곡 정보를 실시간으로 감지하고, LRCLIB.net에서 싱크 가사를 가져와 프레임리스 오버레이 창으로 표시하는 개인용 데스크톱 앱입니다.

**핵심 특징:**
- 모든 SMTC 지원 플레이어 호환 (멜론, Spotify, Windows Media Player 등)
- 가사 싱크 정확도: 150ms 폴링 + 80ms 선행 보상(lookahead)
- 가로 모드(화면 하단 바) / 세로 모드(핑크 MP3 플레이어 스타일) 두 가지 레이아웃
- 앨범아트 표시 (SMTC 썸네일 → iTunes API 폴백)
- 세로 모드 투명 배경 (Win32 컬러키 방식 — MP3 기기 외부 영역 투명화)
- 시스템 트레이 상주, 항상 위 고정 옵션
- 가사 로컬 캐시 (최대 500곡, 반복 API 호출 없음)
- DPI 스케일 대응 창 위치 계산 (GetMonitorInfo + DPI 스케일 변환)

---

## 기술 스택

| 분류 | 기술 | 버전 | 용도 |
|------|------|------|------|
| **런타임** | Python | 3.11+ | 백엔드 전체 |
| **UI 렌더링** | pywebview + WebView2 | 4.4.0+ | 프레임리스 오버레이 창 |
| **시스템 트레이** | pystray | 0.19+ | 트레이 아이콘·메뉴 |
| **미디어 감지** | Windows SMTC (WinRT) | — | 현재 재생곡 폴링 |
| **SMTC 브리지** | PowerShell 5.1 / 7 | — | WinRT → JSON 변환 |
| **가사 API** | LRCLIB.net | — | 싱크 가사 검색 |
| **앨범아트 폴백** | iTunes Search API | — | 썸네일 없을 때 대체 |
| **볼륨 제어** | pycaw + comtypes | — | 시스템 마스터 볼륨 (EndpointVolume) |
| **HTTP** | requests | 2.31.0+ | LRCLIB / iTunes API 호출 |
| **환경변수** | python-dotenv | 1.0.0+ | .env 로드 |
| **이미지 처리** | Pillow | 10+ | 트레이 아이콘 생성 |
| **창 관리** | pywin32 (win32gui, win32api) | 306+ | 창 위치·DPI 계산 |
| **프론트엔드** | HTML5 + CSS3 + Vanilla JS | — | 젤리 핑크 테마 UI |
| **보안** | 자체 validator.py | — | API 응답 새니타이징 |

---

## 프로젝트 구조

```
lyric-app/
│
├── run.py                      # 진입점 — python run.py 로 실행
├── start_showlyric.vbs         # 콘솔 창 없이 실행 (더블클릭 실행용)
├── install_autostart.bat       # 시작 프로그램 등록·해제
│
├── src/                        # Python 백엔드 패키지
│   ├── __init__.py
│   ├── main.py                 # App 클래스: 창 생성·트레이·이벤트 조율
│   ├── api.py                  # LyricApi: JS↔Python 브리지 (pywebview js_api)
│   ├── lyrics_engine.py        # 가사 조회·파싱·위치 동기화
│   ├── media_session.py        # Windows SMTC 폴링 (PowerShell 서브프로세스)
│   ├── cache_manager.py        # 가사 캐시 (메모리 + 디스크)
│   ├── config.py               # 설정 기본값 정의 (앱 시작 시 항상 이 값 사용)
│   │
│   ├── security/
│   │   └── validator.py        # 입력값 검증·새니타이징
│   │
│   └── ui/                     # [레거시] customtkinter 기반 구 버전 UI (미사용)
│       ├── overlay.py
│       └── settings_window.py
│
├── frontend/                   # 웹 UI (pywebview로 로드)
│   ├── index.html              # 전체 HTML 구조 (가로뷰·세로뷰·설정 패널)
│   ├── app.js                  # 폴링 루프·이벤트 바인딩·가사 렌더링
│   ├── style.css               # 젤리 핑크 테마 (글라스모피즘)
│   ├── UI/                     # 이미지 에셋
│   │   ├── mp3-UI.png          # 세로 모드 MP3 플레이어 이미지 (691×1306px)
│   │   ├── background-image.png # 가로 모드 배경
│   │   ├── app-logo.png        # 앱 로고
│   │   └── app-logo.ico        # 앱 아이콘
│   └── reference/              # 디자인 참고 이미지
│
├── .cache/
│   └── lyrics.json             # 가사 로컬 캐시 (최대 500곡)
│
├── config.json                 # 세션 중 임시 설정 저장 (앱 재시작 시 무시됨)
├── requirements.txt            # pip 의존성 목록
├── .env.example                # 환경변수 샘플
│
├── docs/
│   └── 쇼리릭_프로젝트_문서.docx  # 상세 기술 문서
│
├── tasks/
│   ├── todo.md                 # 개발 작업 체크리스트
│   └── progress.md             # 완료 작업 기록
│
└── SECURITY.md                 # 보안 비상 매뉴얼
```

---

## 동작 방식

### 0. 시작 시퀀스

```
App.__init__
  ├─ Config.load()          → 항상 기본값 반환 (config.json 무시)
  ├─ _initial_geometry()    → _work_area()로 논리 픽셀 기준 초기 창 위치 계산
  └─ webview.create_window() → 논리 픽셀 좌표로 창 생성

webview.start(func=_on_start)
  └─ _on_start()            → MediaSessionPoller 시작, 트레이 생성
      └─ _on_loaded()       → JS에 초기 config 주입, 투명도 적용
```

### 1. 미디어 감지 (media_session.py)

Windows SMTC를 직접 쿼리하기 위해 PowerShell 서브프로세스를 영구 실행합니다.

```
Python ──spawn──▶ PowerShell (pwsh 우선, 없으면 PS5.1 폴백)
                    │
                    ├─ WinRT Assembly 로드
                    ├─ GlobalSystemMediaTransportControlsSessionManager 초기화
                    ├─ 다중 세션: Playing 상태 세션 우선 선택
                    └─ 500ms 루프: 현재 재생 정보 → JSON → stdout
                                    │
Python ◀──readline──┘
  (title, artist, position, duration, is_playing)
```

- pwsh(PS7) 설치 시 우선 사용, 없으면 PS5.1 폴백
- PS5.1에서 WinRT 비동기 API를 reflection으로 동기 호출 (`AsTask<T>` 제네릭 메서드)
- 앨범아트: SMTC 썸네일(임시 파일 경유) → iTunes Search API 폴백

### 2. 가사 조회 (lyrics_engine.py)

```
get_lyrics(title, artist)
    │
    ├─ 1) CacheManager.get()         → 캐시 히트 시 즉시 반환
    ├─ 2) LRCLIB /api/get            → 200: LRC 파싱 / 404: /api/search 폴백
    ├─ 3) 아티스트 없이 재시도
    └─ 4) 결과 없음 → 빈 배열 캐시  (재요청 방지)
```

- LRC 형식: `[mm:ss.xx] 가사 텍스트` → `(timestamp_seconds, text)` 리스트
- plain 가사(타임스탬프 없음): 3초 간격 균등 배치
- 캐시 키: `SHA-256(title::artist)[:16]`

### 3. 재생 위치 실시간 추정 (api.py)

SMTC position은 500ms마다 업데이트되므로, 그 사이 갭을 monotonic 시계로 보완합니다.

```python
estimated = pos_base + (now - pos_base_time) + 0.08  # 80ms lookahead
```

### 4. JS-Python 브리지 (api.py)

pywebview의 `js_api` 기능으로 JS에서 Python 메서드를 직접 호출합니다.

| JS 호출 | Python 메서드 | 동작 |
|---------|--------------|------|
| `get_current_state()` | `LyricApi.get_current_state()` | 현재 가사·재생 상태 (150ms마다 폴링) |
| `get_album_art()` | `LyricApi.get_album_art()` | base64 data URL 반환 |
| `set_layout_mode(mode)` | `LyricApi.set_layout_mode()` | 모드 전환 + 기본값 리셋 + 창 이동 |
| `get_volume()` | `LyricApi.get_volume()` | 현재 시스템 볼륨 반환 (0–100) |
| `set_volume(pct)` | `LyricApi.set_volume()` | pycaw EndpointVolume 직접 설정 (1:1 매핑) |
| `toggle_pin()` | `LyricApi.toggle_pin()` | HWND_TOPMOST 토글 |
| `save_config(data)` | `LyricApi.save_config()` | 설정 저장 + 투명도 재적용 |
| `reset_position()` | `LyricApi.reset_position()` | 창 위치 기본값으로 초기화 |
| `save_window_state(x,y,w,h)` | `LyricApi.save_window_state()` | 현재 창 위치·크기 저장 |
| `media_prev()` | `LyricApi.media_prev()` | 이전 곡 (VK 키 이벤트) |
| `media_play_pause()` | `LyricApi.media_play_pause()` | 재생/정지 (VK 키 이벤트) |
| `media_next()` | `LyricApi.media_next()` | 다음 곡 (VK 키 이벤트) |
| `close_app()` | `LyricApi.close_app()` | 앱 종료 |

### 5. 창 위치 계산 (_work_area)

DPI 스케일링 환경에서 pywebview(논리 픽셀)와 Win32 API(물리 픽셀) 좌표계 불일치를 해결합니다.

```python
scale = GetDpiForSystem() / 96.0
info  = GetMonitorInfo(primary_monitor)   # 물리 픽셀
sw    = round(work_width  / scale)        # 논리 픽셀 → pywebview에 전달
sh    = round(work_height / scale)

# MoveWindow 호출 시 다시 물리 픽셀로 변환
MoveWindow(hwnd, round(x*scale), round(y*scale), round(w*scale), round(h*scale))
```

### 6. 설정 초기화 아키텍처

앱 실행 및 모드 전환 시 항상 `config.py` 기본값에서 시작합니다.

- `Config.load()` → 항상 `cls()` 반환 (config.json 읽지 않음)
- 세션 중 설정 변경 → 메모리(Config 객체) + config.json 임시 기록
- 앱 재시작 → 기본값으로 리셋
- 가로→세로 전환 → `set_layout_mode()`에서 opacity=1.0, font=17 강제 리셋

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
            ▼ (JS에서 150ms마다 get_current_state() 호출)
[LyricApi.get_current_state()]
  ┌─ 곡 변경 감지 (song_id 비교)?
  │     YES → LyricsEngine.get_lyrics(title, artist)
  │              ├─ 캐시 히트 → 즉시 반환
  │              └─ LRCLIB API 호출 → LRC 파싱 → 캐시 저장
  └─ 재생 위치 추정(80ms lookahead) → get_current_index() → get_context()
                   → (이전가사, 현재가사, 다음가사)
            │
            ▼
[JS updateLyrics()] → 화면 가사 업데이트 (100ms fade 애니메이션)

[앨범아트 — 곡 변경 시 1회]
  1) SMTC 썸네일 → PowerShell 임시 파일 추출 → base64
  2) 실패 시 iTunes Search API → 600×600 이미지 → base64
```

---

## 설치 및 실행

### 사전 조건
- Windows 10 / 11 (SMTC 필수)
- Python 3.11 이상
- WebView2 런타임 (Microsoft Edge 설치 시 자동 포함)

### 설치

```bash
cd lyric-app
pip install -r requirements.txt
```

### 실행

```bash
python run.py
```

콘솔 창 없이 실행 (더블클릭):
```
start_showlyric.vbs 더블클릭
```

### 자동 시작 등록 (선택)

```bash
install_autostart.bat
```
시작 프로그램에 등록하거나 해제합니다.

### 배포용 exe 빌드 (선택)

```bash
pip install pyinstaller
pyinstaller showlyric.spec
# 결과물: dist\쇼리릭\쇼리릭.exe
```

### 개발 모드 (DevTools)

`src/main.py`에서 `debug=True`로 변경:
```python
webview.start(func=self._on_start, debug=True)  # 기본값: False
```

---

## 설정

설정 기본값은 `src/config.py`에서 관리합니다. `config.json`은 세션 중 임시 저장용이며 앱 재시작 시 무시됩니다.

| 필드 | 기본값 | 설명 |
|------|--------|------|
| `layout_mode` | `"vertical"` | 시작 레이아웃 모드 |
| `pinned` | `false` | 항상 위 고정 |
| `opacity` | `1.0` | 세로 모드 창 불투명도 (1.0 = 완전 불투명) |
| `h_height` | `60` | 가로 모드 창 높이(px) |
| `h_font_size` | `15` | 가로 모드 현재 가사 폰트(px) |
| `h_font_size_dim` | `10` | 가로 모드 이전·다음 가사 폰트(px) |
| `v_width` | `300` | 세로 모드 창 너비(px) — 높이는 비율로 자동 계산 |
| `font_size` | `17` | 세로 모드 현재 가사 폰트(px) |
| `font_size_dim` | `12` | 세로 모드 이전·다음 가사 폰트(px) |
| `font_family` | `"Malgun Gothic"` | 폰트 패밀리 |
| `poll_interval_ms` | `500` | SMTC 폴링 간격(ms) |

---

## 레이아웃 모드

### 가로 모드 (Horizontal)

```
┌─────────────────────────────────────────────────────────────┐
│ [앨범아트]  이전 가사                               ◀ ⏯ ▶ 🔊│
│            ★ 현재 가사 (크고 굵게)                  📌 [세로↕]│
│            다음 가사                                         │
└─────────────────────────────────────────────────────────────┘
(화면 하단 고정, 전체 너비)
```

- 투명도 항상 1.0 고정 (사용자 변경 불가)
- 전환 시마다 기본값으로 리셋

### 세로 모드 (Vertical)

```
┌──────────┐
│ [가로↔] 📌 ⚙ │
├──────────┤  ← LCD 오버레이 (mp3-UI.png 위)
│ [앨범아트]│
│ 곡명      │
│ 아티스트  │
│ ━━━━━━○  │  ← 진행바
├──────────┤
│ 이전 가사 │
│ 현재 가사 │
│ 다음 가사 │
├──────────┤
│  [MENU]  │
│◀  (●)  ▶│  ← 클릭휠 하드웨어 버튼
│  [VOL]   │
└──────────┘
(화면 우측 하단 고정)
```

- MP3 플레이어 이미지(mp3-UI.png) 배경에 LCD 오버레이 방식
- 투명 배경: Win32 컬러키(LWA_COLORKEY) 방식으로 기기 외부 영역 투명화
- 투명도 사용자 설정 가능 (기본 1.0)
- 전환 시마다 기본값으로 리셋

---

## UI 커스터마이징

### 창 크기·폰트 기본값 변경 → `src/config.py`

```python
h_height: int = 60      # 가로 모드 높이
v_width: int = 300       # 세로 모드 너비 (높이는 비율 자동)
font_size: int = 17      # 세로 현재 가사
h_font_size: int = 15    # 가로 현재 가사
```

### 스타일·버튼 위치 변경 → `frontend/style.css`

| 수정 대상 | CSS 클래스 |
|-----------|-----------|
| 가로뷰 전체 | `.h-view` |
| 가로뷰 가사 박스 | `.h-lyrics` |
| 세로뷰 MP3 바디 | `.mp3-body` |
| LCD 오버레이 위치 | `.lcd-overlay` (top/left/width/height %) |
| MENU 버튼 | `.hw-m` |
| 이전 곡 버튼 | `.hw-prev` |
| 재생/정지 버튼 | `.hw-play` |
| 다음 곡 버튼 | `.hw-next` |
| 볼륨 버튼 | `.hw-vol` |

버튼 `top` / `left` 값은 `mp3-body` 크기 대비 `%`입니다.  
mp3-UI.png 기준: LCD 상단 ≈ 9%, 클릭휠 중심 ≈ 79%
