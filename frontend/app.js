"use strict";

const S = {
  mode: "horizontal", // 'horizontal' | 'vertical'
  isPlaying: false,
  lastSongId: null,
  config: {},
  volPanelOpen: false,
  settingsOpen: false,
};

// ── DOM 캐시 ──────────────────────────────────────────────
const $ = (id) => document.getElementById(id);
const app = $("app");
const hView = $("h-view");
const vView = $("v-view");
const settPanel = $("settings-panel");

// 가로 뷰
const hArt = $("h-art");
const hArtPh = $("h-art-ph");
const hPrev = $("h-prev");
const hCurr = $("h-curr");
const hNext = $("h-next");

// 세로 뷰
const vArt = $("v-art");
const vArtPh = $("v-art-ph");
const vPrev = $("v-prev");
const vCurr = $("v-curr");
const vNext = $("v-next");
const vTitle = $("v-title");
const vArtist = $("v-artist");
const progFill = $("prog-fill");
const progThumb = $("prog-thumb");
const vTimeNow = $("v-time-now");
const vTimeTotal = $("v-time-total");
const vVolPanel = $("v-vol-panel");
const vVolSlider = $("v-vol");
const vVolVal = $("v-vol-val");

// ══════════════════════════════════════════════════════════
// 초기화
// ══════════════════════════════════════════════════════════

window.initApp = async function () {
  if (!window.pywebview || !window.pywebview.api) {
    console.warn("pywebview API가 아직 준비되지 않았습니다.");
    return;
  }
  if (window._appInitialized) return; // 중복 호출 방지
  window._appInitialized = true;

  S.config = await api("get_config");
  applyConfig(S.config);
  bindEvents();
  startVolumeSync();
  tick();
};

window.addEventListener("pywebviewready", () => window.initApp());

function api(method, ...args) {
  return window.pywebview.api[method](...args);
}

// ══════════════════════════════════════════════════════════
// 메인 폴링 루프 (150ms)
// ══════════════════════════════════════════════════════════
let _volSyncCounter = 0;
let _tickRunning = true;
let _tickErrCount = 0;

async function tick() {
  if (!_tickRunning) return;
  let nextDelay = 100;
  try {
    const d = await api("get_current_state");
    _tickErrCount = 0;
    updatePlayState(d.is_playing);
    updateSongInfo(d);
    updateProgress(d.position, d.duration);
    updateLyrics(d);
    // 약 15초마다 실제 시스템 볼륨을 슬라이더와 동기화
    if (++_volSyncCounter >= 150) {
      _volSyncCounter = 0;
      syncVolumeFromSystem();
    }
    // 타겟 스케줄링: 다음 가사 직전에 정확히 폴링
    // 100ms 이내면 그 시점에, 100ms 초과면 100ms 고정 (progress 업데이트 유지)
    if (
      d.status === "playing" &&
      typeof d.ms_to_next === "number" &&
      d.ms_to_next > 0
    ) {
      nextDelay = Math.max(15, Math.min(100, d.ms_to_next));
    }
  } catch (e) {
    // 연속 3회 이상 실패 시 앱 종료 중으로 판단하고 루프 중단
    if (++_tickErrCount >= 3) {
      _tickRunning = false;
      return;
    }
    console.warn("tick:", e);
  }
  setTimeout(tick, nextDelay);
}

async function syncVolumeFromSystem() {
  try {
    const vol = await api("get_volume");
    if (vol >= 0) {
      $("h-vol").value = vol;
      vVolSlider.value = vol;
      vVolVal.textContent = vol + "%";
    }
  } catch {}
}

// ── 재생 상태 아이콘 ─────────────────────────────────────
function updatePlayState(playing) {
  S.isPlaying = playing;
  // 가로 뷰
  toggleClass($("h-play-btn").querySelector(".ico-play"), "hidden", playing);
  toggleClass($("h-play-btn").querySelector(".ico-pause"), "hidden", !playing);
  // 세로 뷰 휠
  toggleClass($("w-play").querySelector(".ico-play"), "hidden", playing);
  toggleClass($("w-play").querySelector(".ico-pause"), "hidden", !playing);
  // 세로 뷰 LCD 내부
  toggleClass($("v-lcd-play").querySelector(".ico-play"), "hidden", playing);
  toggleClass($("v-lcd-play").querySelector(".ico-pause"), "hidden", !playing);
}

// ── 곡 정보 + 앨범아트 (song_id 변경 시만 갱신) ──────────
async function updateSongInfo(d) {
  if (!d.title) return;
  if (d.song_id === S.lastSongId) return;
  S.lastSongId = d.song_id;

  // 곡명 / 아티스트
  vTitle.textContent = d.title || "재생 중인 곡 없음";
  vArtist.textContent = d.artist || "—";

  // 앨범아트 — base64 즉시 파싱, 파일 저장 없음
  try {
    const artData = await api("get_album_art");
    if (artData && artData.startsWith("data:")) {
      setAlbumArt(artData);
    } else {
      clearAlbumArt();
    }
  } catch {
    clearAlbumArt();
  }
}

function setAlbumArt(dataUrl) {
  // 가로 뷰
  hArt.src = dataUrl;
  hArt.style.display = "";
  hArtPh.style.display = "none";
  // 세로 뷰
  vArt.src = dataUrl;
  vArt.style.display = "";
  vArtPh.style.display = "none";
}

function clearAlbumArt() {
  hArt.style.display = "none";
  hArtPh.style.display = "";
  vArt.style.display = "none";
  vArtPh.style.display = "";
}

// ── 진행 바 ───────────────────────────────────────────────
function updateProgress(pos, dur) {
  if (!dur || dur <= 0) {
    progFill.style.width = "0%";
    progThumb.style.left = "0%";
    vTimeNow.textContent = fmtTime(pos);
    vTimeTotal.textContent = "—";
    return;
  }
  const pct = Math.min(100, (pos / dur) * 100).toFixed(1);
  progFill.style.width = pct + "%";
  progThumb.style.left = pct + "%";
  vTimeNow.textContent = fmtTime(pos);
  vTimeTotal.textContent = fmtTime(dur);
}

function fmtTime(secs) {
  if (!secs || secs < 0) return "0:00";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60)
    .toString()
    .padStart(2, "0");
  return `${m}:${s}`;
}

// ── 가사 ──────────────────────────────────────────────────
function updateLyrics(d) {
  if (d.status === "idle") {
    fadeLyric(hCurr, "♪  재생 중인 곡 없음");
    fadeLyric(vCurr, "♪  재생 중인 곡 없음");
    setDim(hPrev, "");
    setDim(hNext, "");
    setDim(vPrev, "");
    setDim(vNext, "");
    return;
  }
  if (d.status === "no_lyrics") {
    fadeLyric(hCurr, `🔍 지원하는 가사가 없습니다.`);
    fadeLyric(vCurr, `🔍 지원하는 가사가 없습니다.`);
    setDim(hPrev, "");
    setDim(hNext, d.artist ? `${d.artist} – ${d.title}` : "");
    setDim(vPrev, "");
    setDim(vNext, d.artist ? `${d.artist} – ${d.title}` : "");
    return;
  }
  fadeLyric(hCurr, d.current || "");
  fadeLyric(vCurr, d.current || "");
  setDim(hPrev, d.prev || "");
  setDim(hNext, d.next || "");
  setDim(vPrev, d.prev || "");
  setDim(vNext, d.next || "");
}

function fadeLyric(el, text) {
  if (el.dataset.txt === text) return;
  el.dataset.txt = text;
  el.classList.add("fading");
  setTimeout(() => {
    el.textContent = text;
    el.classList.remove("fading");
  }, 100);
}

function setDim(el, text) {
  el.textContent = text;
}

// ══════════════════════════════════════════════════════════
// 이벤트 바인딩
// ══════════════════════════════════════════════════════════
function bindEvents() {
  // 미디어 컨트롤 (가로)
  $("h-prev-btn").addEventListener("click", () => api("media_prev"));
  $("h-play-btn").addEventListener("click", () => api("media_play_pause"));
  $("h-next-btn").addEventListener("click", () => api("media_next"));

  // 미디어 컨트롤 (세로 휠)
  $("w-prev").addEventListener("click", () => api("media_prev"));
  $("w-play").addEventListener("click", () => api("media_play_pause"));
  $("w-next").addEventListener("click", () => api("media_next"));
  $("w-menu").addEventListener("click", toggleSettings);
  $("w-vol").addEventListener("click", toggleVolPanel);

  // 미디어 컨트롤 (세로 LCD 내부)
  $("v-lcd-prev").addEventListener("click", () => api("media_prev"));
  $("v-lcd-play").addEventListener("click", () => api("media_play_pause"));
  $("v-lcd-next").addEventListener("click", () => api("media_next"));

  // 모드 전환
  $("h-mode-btn").addEventListener("click", () => switchMode("vertical"));
  $("v-mode-btn").addEventListener("click", () => switchMode("horizontal"));

  // 고정 토글 (가로/세로 모두)
  async function handlePin() {
    const r = await api("toggle_pin");
    S.config.pinned = r.pinned;
    const op = r.pinned ? "1" : "0.55";
    $("v-pin-btn").style.opacity = op;
    $("h-pin-btn").style.opacity = op;
  }
  $("v-pin-btn").addEventListener("click", handlePin);
  $("h-pin-btn").addEventListener("click", handlePin);

  // 볼륨 슬라이더
  $("h-vol").addEventListener("input", (e) => {
    api("set_volume", parseInt(e.target.value));
  });
  vVolSlider.addEventListener("input", (e) => {
    const v = parseInt(e.target.value);
    vVolVal.textContent = v + "%";
    api("set_volume", v);
    // 가로 슬라이더도 동기화
    $("h-vol").value = v;
  });

  // 진행바 클릭 — 향후 seek 기능 자리 (현재 SMTC에서 seek 미지원)
  $("prog-track").addEventListener("click", (e) => {
    const rect = $("prog-track").getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    // seek 기능 미구현 — 시각적 피드백만
    progFill.style.width = (ratio * 100).toFixed(1) + "%";
    progThumb.style.left = (ratio * 100).toFixed(1) + "%";
  });

  // 설정 패널
  $("v-settings-btn").addEventListener("click", toggleSettings);
  $("s-close").addEventListener("click", closeSettings);
  $("s-cancel").addEventListener("click", closeSettings);
  $("s-save").addEventListener("click", saveSettings);
  $("s-reset").addEventListener("click", () => api("reset_position"));

  // 설정 슬라이더 실시간 레이블
  $("s-opacity").addEventListener("input", (e) => {
    $("s-opacval").textContent = e.target.value + "%";
  });
  $("s-fontsize").addEventListener("input", (e) => {
    $("s-fontval").textContent = e.target.value + "px";
    document.documentElement.style.setProperty(
      "--font-sz",
      e.target.value + "px",
    );
  });

  // 우클릭 → 세로 모드에서만 설정
  document.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (S.mode === "vertical") toggleSettings();
  });
}

// ── 모드 전환 ────────────────────────────────────────────
async function switchMode(mode) {
  await api("set_layout_mode", mode);
  S.mode = mode;
  app.dataset.mode = mode;
  toggleClass(hView, "hidden", mode === "vertical");
  toggleClass(vView, "hidden", mode === "horizontal");

  if (mode === "horizontal") {
    document.documentElement.style.setProperty(
      "--font-sz",
      (S.config.h_font_size || 14) + "px",
    );
    document.documentElement.style.setProperty(
      "--font-sz-dim",
      (S.config.h_font_size_dim || 10) + "px",
    );
  } else {
    // 세로 모드: 디폴트값으로 초기화 후 사용자 설정 가능
    const defaults = { font_size: 15, font_size_dim: 12, opacity: 1.0 };
    Object.assign(S.config, defaults);
    await api("save_config", defaults);
    document.documentElement.style.setProperty("--font-sz", "15px");
    document.documentElement.style.setProperty("--font-sz-dim", "12px");
  }
}

// ── 볼륨 패널 토글 ────────────────────────────────────────
function toggleVolPanel() {
  S.volPanelOpen = !S.volPanelOpen;
  toggleClass(vVolPanel, "hidden", !S.volPanelOpen);
}

// ── 설정 ──────────────────────────────────────────────────
function toggleSettings() {
  S.settingsOpen = !S.settingsOpen;
  toggleClass(settPanel, "hidden", !S.settingsOpen);
  if (S.settingsOpen) syncSettingsUI();
}
function closeSettings() {
  S.settingsOpen = false;
  settPanel.classList.add("hidden");
}
function syncSettingsUI() {
  $("s-pinned").checked = S.config.pinned || false;
  $("s-opacity").value = Math.round((S.config.opacity || 0.96) * 100);
  $("s-opacval").textContent = $("s-opacity").value + "%";
  $("s-fontsize").value = S.config.font_size || 15;
  $("s-fontval").textContent = $("s-fontsize").value + "px";
}
async function saveSettings() {
  const updated = {
    pinned: $("s-pinned").checked,
    opacity: parseInt($("s-opacity").value) / 100,
    font_size: parseInt($("s-fontsize").value),
  };
  Object.assign(S.config, updated);
  await api("save_config", updated);
  document.documentElement.style.setProperty(
    "--font-sz",
    updated.font_size + "px",
  );
  const pinOp = updated.pinned ? "1" : "0.55";
  $("v-pin-btn").style.opacity = pinOp;
  $("h-pin-btn").style.opacity = pinOp;
  closeSettings();
}

// ── 볼륨 초기 동기화 ──────────────────────────────────────
async function startVolumeSync() {
  try {
    const vol = await api("get_volume");
    if (vol >= 0) {
      $("h-vol").value = vol;
      vVolSlider.value = vol;
      vVolVal.textContent = vol + "%";
    }
  } catch {
    /* 무시 */
  }
}

// ── config 적용 ───────────────────────────────────────────
function applyConfig(cfg) {
  S.mode = cfg.layout_mode || "horizontal";
  app.dataset.mode = S.mode;
  toggleClass(hView, "hidden", S.mode === "vertical");
  toggleClass(vView, "hidden", S.mode === "horizontal");

  if (S.mode === "horizontal") {
    document.documentElement.style.setProperty(
      "--font-sz",
      (cfg.h_font_size || 14) + "px",
    );
    document.documentElement.style.setProperty(
      "--font-sz-dim",
      (cfg.h_font_size_dim || 10) + "px",
    );
  } else {
    document.documentElement.style.setProperty(
      "--font-sz",
      (cfg.font_size || 15) + "px",
    );
    document.documentElement.style.setProperty(
      "--font-sz-dim",
      (cfg.font_size_dim || 12) + "px",
    );
  }

  const pinOp = cfg.pinned ? "1" : "0.55";
  $("v-pin-btn").style.opacity = pinOp;
  $("h-pin-btn").style.opacity = pinOp;
}

// ── 유틸 ──────────────────────────────────────────────────
function toggleClass(el, cls, condition) {
  if (condition) el.classList.add(cls);
  else el.classList.remove(cls);
}
