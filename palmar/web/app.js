// palmar 브라우저 — 캔버스 UI. 데몬과의 계약은 docs/protocol.md 하나다. 이 파일은 거기 적힌 것만 믿는다.
//
// ① 웹 스택은 아직 사람이 정하지 않았다. 이 파일이 vanilla JS 인 것은 결정이 아니라 결정 전의 기본값이다
//    (decisions.md ①: "기본값은 안 쓰는 것이고, 쓰자고 하려면 이유를 대야 한다"). 빌드 단계도 없다.
//    프레임워크를 고르게 되면 이 파일이 바뀐다 — 그래서 상태(sessions·tiles)와 DOM 을 최대한 얇게 이었다.
//
// 임시로 둔 것 (사람 몫의 결정을 미리 정하지 않은 자리 — 지금 도는 데 필요한 최소만):
//   ③ 좌표·크기·z 순서는 localStorage 'palmar-tiles' 에 session id 로 둔다. protocol.md "위치·크기" 절이
//      말하는 임시 그대로다. 데몬은 pane 의 cols·rows 만 안다.
//   ⑥ status 문자열을 CSS 클래스(wait/work/done/idle)로만 바꾼다. 색은 style.css 의 --st-* 에만 있다.
//      idle 과 unknown 은 같은 회색 자리에 둔다 — 매핑이 정해지면 STATUS_CLASS 한 곳만 바뀐다.
//   ⑩ 새 창 자리: 빈 격자 자리를 훑어 첫 빈 곳, 없으면 맨 아래. 밀어내기(#23)는 아직 없고 겹침 설정도 없다.
//      기본 크기 DEFAULT_W/H 도 ⑩ 에 딸린 미정이다.
//   ⑪⑫ 캔버스 목록·순서·이름과 세션의 canvas·name 은 **데몬이 갖는다**(protocol.md "캔버스"). 이 파일은
//      사본을 들고 hello 로 갈아 낀다. 브라우저에만 있는 것은 셋뿐이다 — 지금 보고 있는 탭, 목록 그룹의
//      접힘(localStorage 'palmar-groups' · 'palmar-canvas-groups'), 미니맵. 데몬은 그 셋을 모른다(protocol.md "없는 것").
//      PROVISIONAL 둘: 이름 없는 캔버스의 이름표를 무엇으로 만드는지(⑪ 미정 → canvasLabel 하나에 있다),
//      새 세션이 어느 캔버스에 뜨는지(⑪ "지금 캔버스인가 그 폴더의 캔버스인가" 미정 → launch 하나에 있다).
//
// 흐름 제어·재접속은 스파이크 D(docs/spikes/2026-09-07/pipeline/palmar/web/app.js)의 꼴을 그대로 가져왔다:
// 바이너리 프레임 → term.write(bytes, cb) → cb 안에서 {"t":"ack","n":len}.

(() => {
'use strict';

const TOKEN = window.PALMAR_TOKEN || '';
const enc = new TextEncoder();
const root = document.documentElement;
const $ = (sel, from) => (from || document).querySelector(sel);

// ── 상수 ────────────────────────────────────────────────
// ⑥ 임시: status → 클래스. 색은 CSS 에만 있다.
const STATUS_CLASS = { waiting: 'wait', working: 'work', done: 'done', idle: 'idle', unknown: 'idle' };
// 목록의 묶음 순서: 기다리는 것이 맨 위 (decisions.md "화면"). idle 묶음에 unknown 을 함께 둔다.
const GROUPS = [
  ['waiting', 'wait', 'waiting on you'],
  ['working', 'work', 'working'],
  ['done',    'done', 'done'],
  ['idle',    'idle', 'idle'],
];
const PILL = new Set(['waiting', 'working', 'done']);   // 알약을 보이는 상태 (목업: idle 은 알약 없음)
// #31 ③: 캔버스로 묶은 뒤에도 **묶음 안의 차례는 GROUPS 그대로다**(기다림 → 일하는 중 → 끝남 → 대기).
// unknown 은 idle 과 같은 자리(⑥ 임시, STATUS_CLASS 와 같은 규칙).
const STATUS_RANK = { waiting: 0, working: 1, done: 2, idle: 3, unknown: 3 };
function statusRank(s) { const r = STATUS_RANK[s.status]; return r === undefined ? 3 : r; }
// #24: 목록 둘째 줄은 사람 말이어야 한다(목업). 훅 이벤트명을 짧은 문구로 바꾼다 — 표시만이고 상태 판정엔 안 쓴다.
// 모르는 이벤트명은 그대로 보인다(fallback).
const EVENT_PHRASE = {
  SessionStart: 'started', UserPromptSubmit: 'working…', PermissionRequest: 'needs your approval',
  Notification: 'notified', Stop: 'finished', SessionEnd: 'session ended',
};
const GRID = 22, GAP = 12;                              // 점 격자와 같은 22px 간격으로 빈 자리를 훑는다
const DEFAULT_W = 520, DEFAULT_H = 360;                 // ⑩ 임시 기본 크기
const MIN_W = 220, MIN_H = 110;
const LS_TILES = 'palmar-tiles';                        // ③ 임시
const LS_THEME = 'palmar-theme';
const LS_GROUPS = 'palmar-groups';                      // 목록 그룹 접힘 — 브라우저에만 있는 것(⑪)
const LS_CVGROUPS = 'palmar-canvas-groups';             // 캔버스 묶음 접힘 — 캔버스 id 로 건다(#31 ③)
// 캔버스가 사라진 세션이 떨어지는 자리. 계약상 없어야 하지만(protocol.md: hello 한 프레임 안에서 모든
// session.canvas 가 그 canvases 안에 있다) 목록이 세션을 잃는 것보다는 낫다.
const OTHER_KEY = '__other';
const MM_PAD = 4;                                       // 미니맵 상자 안쪽 여백
// 목업 .term 은 11.5px 이다. WebGL 렌더러는 셀 폭을 장치 픽셀로 **내림**한다(addon-webgl: device.char.width =
// Math.floor(charWidth × dpr)) — 11.5px × 0.6em = 6.9px 가 dpr 1 에서 6px 셀이 되어 글자가 13% 잘리고 520px 에
// 80열이 들어갔다(실측, 헤드리스 크롬 152). JetBrains Mono 전진폭은 0.6em 이지만 크롬이 잰 값은 11.667px 에서
// 6.996px 로 7 에 못 미쳤다(실측). 그래서 조금 여유를 두어 11.75px: 7.05px → dpr 1 에서 7, dpr 2 에서 14 → 7.
// 눈으로 11.5 와 구분되지 않는다. 다른 dpr(1.5 등)에서는 여전히 내림이 있다 — cate 가 겪은 그 자리다.
const FONT_PX = 11.75;
// 판마다 글자 크기 (#25). 창은 그대로 두고 글자만 바꾸므로 **행·열이 늘고 준다** — 같은 자리에
// 더 많이 보거나, 크게 보거나. 브라우저의 Ctrl− 가 모든 판에 하는 일을 판 하나에만 하는 것이다.
const FONT_MIN = 6, FONT_MAX = 32, FONT_STEP = 1;

// 이 기계의 글쇠 이름. 안내에 쓰는 글자이고, 처리 쪽은 늘 metaKey 와 ctrlKey 를 **둘 다** 받는다 —
// 안내만 한쪽으로 박아 두면 다른 쪽 사람에게 없는 글쇠를 가리키게 된다(전에 ⌘ 가 그랬다).
const IS_MAC = /Mac|iPhone|iPad/i.test(
  (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '');
const KMOD = IS_MAC ? '⌘' : 'Ctrl';
const CLIP_HINT = IS_MAC ? '⌘C / ⌘V' : 'Ctrl+Shift+C / Ctrl+Shift+V';
const LS_CLIPHINT = 'palmar.cliphint';
let clipHintShown = false;
try { clipHintShown = localStorage.getItem(LS_CLIPHINT) === '1'; } catch (e) {}

// ── 복사·붙여넣기 ─────────────────────────────────────────
// xterm.js 에는 이게 **없다.** 고르는 것까지가 그것의 일이고, 클립보드에 넣는 것은 앱의 몫이다
// (실측 2026-09-08: 고른 글자는 getSelection() 으로 잡히는데 어떤 조합에도 클립보드로 안 갔다).
// **Ctrl+C 는 뺏지 않는다.** 터미널에서 그것은 인터럽트다 — 고른 것이 있다고 가로채면 도는 것을
// 멈추려던 손이 대신 복사를 한다. 그래서 리눅스·윈도우의 관례대로 Shift 를 함께 요구하고,
// 맥에서는 ⌘ 를 쓴다. 둘 다 받는다 — 어느 쪽 손버릇이든 되는 편이 낫다.
async function clipWrite(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    toast(['could not copy — the browser refused clipboard access', String(e.message || e)]);
    return false;
  }
}
async function clipRead() {
  try {
    return await navigator.clipboard.readText();
  } catch (e) {
    // 크롬은 읽기에 권한을 따로 묻는다. 거절되면 브라우저의 기본 붙여넣기가 아직 남아 있다.
    toast(['could not paste — the browser refused to read the clipboard',
           'allow clipboard for ' + location.origin + ', or use the browser\'s own paste']);
    return '';
  }
}
// 목업 .term 의 line-height 1.55 는 font-size 기준이고 xterm 의 lineHeight 는 글꼴 고유 줄높이(≈1.3) 기준이라
// 같은 눈높이를 내려면 1.15 쯤이다. 사람이 봐야 하는 값.
const LINE_HEIGHT = 1.15;

// ── DOM 손잡이 ──────────────────────────────────────────
const cv = $('#cv'), cvScroll = $('#cv-scroll'), listEl = $('#list'), treeEl = $('#tree');
const tabsEl = $('#tabs'), mmEl = $('#mm'), mmWorldEl = $('#mm-w'), mmVpEl = $('#mm-vp');
const toastEl = $('#toast'), searchEl = $('#search');
const launchBtn = $('#launch'), launchPath = $('#launch-path');

// ── 상태 ────────────────────────────────────────────────
// 진실은 데몬이다(AGENTS.md "구조"). sessions 는 /events 가 보내 준 사본이고 붙을 때마다 hello 로 갈아 낀다.
const sessions = new Map();   // id → Session
const tiles = new Map();      // id → Tile
const items = new Map();      // id → 목록 항목 DOM
const changedAt = new Map();  // id → status 가 바뀐 것을 브라우저가 본 시각(ms). 프로토콜에 없어 여기서 잰다
let focused = null;           // 앞에 있는 tile 의 id
let maxed = null;             // 펼쳐 보는 tile
let zTop = 10;
let eventsWs = null, eventsRetry = 0;
let layout = loadLayout();    // ③ 임시: { id: {x,y,w,h,z} }
let saveTimer = null;
// ⑪ 캔버스. 목록도 순서도 이름도 데몬의 것이고 여기 있는 것은 사본이다. **current 만 브라우저 것이다** —
// 창 둘이 서로 다른 캔버스를 볼 수 있어야 해서 데몬에 "현재 캔버스" 가 없다(protocol.md).
const canvases = new Map();   // id → Canvas
let canvasOrder = [];         // id[] — 데몬이 준 order 순
let current = null;           // 지금 보고 있는 캔버스 id
let groupsCollapsed = loadGroups();   // 목록 그룹 접힘 — 브라우저에만 있다
let cvCollapsed = loadCvGroups();     // 캔버스 묶음 접힘 — 같은 자리, 캔버스 id 로 건다(#31 ③)
let tabDragged = false;       // 끌어 놓은 직후의 click 은 전환이 아니다
let tabsPending = false;      // 이름을 고치는 동안 미뤄 둔 탭 줄 다시 그리기
// #31 ①④ 닫기. DELETE 를 낸 뒤 **여기서 지우지 않는다** — /events 의 gone 이 지운다(둘째 브라우저와 같이
// 움직이려면 지우는 길이 하나여야 한다). 그동안 무엇이 도는 중인지만 들고 있는다.
const closing = new Set();    // DELETE 를 냈고 아직 gone 이 안 온 세션 id
let rowConfirm = null;        // 확인 줄이 열려 있는 목록 행의 세션 id (목록은 통째로 다시 지어진다)

// ── 작은 도구 ──────────────────────────────────────────
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function cssVar(name) { return getComputedStyle(root).getPropertyValue(name).trim(); }
function loadLayout() {
  try { const v = JSON.parse(localStorage.getItem(LS_TILES) || '{}'); return v && typeof v === 'object' ? v : {}; }
  catch (e) { return {}; }
}
function saveLayout() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => { try { localStorage.setItem(LS_TILES, JSON.stringify(layout)); } catch (e) {} }, 150);
}
// 목록 그룹 접힘 — 그 브라우저의 취향이지 세션의 성질이 아니다(protocol.md "없는 것"). 테마와 같은 자리다.
function loadGroups() {
  try { const v = JSON.parse(localStorage.getItem(LS_GROUPS) || '{}'); return v && typeof v === 'object' ? v : {}; }
  catch (e) { return {}; }
}
function saveGroups() { try { localStorage.setItem(LS_GROUPS, JSON.stringify(groupsCollapsed)); } catch (e) {} }
// 캔버스 묶음의 접힘은 **캔버스 id** 로 건다. 상태 키('waiting'…)와 한 통에 두면 어느 쪽을 솎아야 할지
// 알 수 없어 통을 따로 뒀다. id 는 데몬이 살아 있는 동안만 뜻이 있으므로(protocol.md "아무것도 디스크에
// 안 쓴다") 저장할 때 지금 없는 캔버스의 키를 솎는다 — 데몬이 다시 뜰 때마다 쌓이지 않게.
function loadCvGroups() {
  try { const v = JSON.parse(localStorage.getItem(LS_CVGROUPS) || '{}'); return v && typeof v === 'object' ? v : {}; }
  catch (e) { return {}; }
}
function saveCvGroups() {
  for (const k in cvCollapsed) {
    if (!cvCollapsed[k]) delete cvCollapsed[k];                        // 펴 둔 것은 기본값이라 안 적는다
    else if (k !== OTHER_KEY && !canvases.has(k)) delete cvCollapsed[k];
  }
  try { localStorage.setItem(LS_CVGROUPS, JSON.stringify(cvCollapsed)); } catch (e) {}
}

// ⑫ 제자리에서 이름 고치기 — 탭과 타일 제목이 같이 쓴다. 이름은 textContent 로만 넣는다
// (protocol.md "이름은 셸에 안 닿는다": innerHTML 금지). 취소하면 있던 자식들을 그대로 되돌린다.
function inlineEdit(host, initial, commit, after) {
  if (host.querySelector('input')) return;
  const prev = [...host.childNodes];
  const inp = document.createElement('input');
  inp.className = 'ed';
  inp.type = 'text';
  inp.value = initial || '';
  inp.maxLength = 64;              // protocol.md "이름 규칙" 1–64. 서버도 다시 본다 — 여기 것은 편의다
  inp.spellcheck = false;
  host.textContent = '';
  host.appendChild(inp);
  inp.focus();
  inp.select();
  let done = false;
  const end = (save) => {
    if (done) return;
    done = true;
    const v = inp.value.trim();
    host.textContent = '';
    for (const n of prev) host.appendChild(n);
    if (save) commit(v);
    if (after) after();      // 고치는 동안 미뤄 둔 다시 그리기를 여기서 푼다
  };
  inp.addEventListener('keydown', (ev) => {
    ev.stopPropagation();          // ⌘K·Esc 는 이 칸의 것이다 — 전역 단축키에 안 넘긴다
    if (ev.key === 'Enter') { ev.preventDefault(); end(true); }
    else if (ev.key === 'Escape') { ev.preventDefault(); end(false); }
  });
  inp.addEventListener('blur', () => end(true));
  // 이 칸 위의 누름은 탭 끌기·타일 끌기가 아니다
  for (const t of ['pointerdown', 'click', 'dblclick']) inp.addEventListener(t, (ev) => ev.stopPropagation());
}

// #31 ①④ 제자리에서 묻기 — **터미널은 누군가 돌리고 있는 일이다. 부수기 전에 한 번 묻는다.**
// `window.confirm` 을 쓰지 않는 이유 둘: (1) 그것은 페이지 전체를 멈춰 /events 프레임 처리까지 멈춘다,
// (2) 생김새를 우리가 못 정한다 — 신호등을 이모지로 안 그리는 것과 같은 이유다(AGENTS.md).
// 확인 줄은 host 안에 놓이고, 있는 동안 host 는 `.cfm-on` 을 단다 — CSS 가 host 의 다른 자식을 내린다.
// 자식을 떼었다 붙이지 않는 이유: 그 사이에 방송이 와서 host 를 고쳐도(Tile.update·noteOutput) 안 부서진다.
let activeConfirm = null;   // 확인 줄은 한 번에 하나다 — 새로 열면 먼저 것을 거둔다(떠도는 리스너를 안 남긴다)
function askClose(host, question, onYes, onEnd) {
  if (host.querySelector('.cfm')) return null;
  if (activeConfirm) activeConfirm.cancel();
  const row = el('span', 'cfm');
  const yes = el('button', 'cbtn yes', 'Close');
  const no = el('button', 'cbtn', 'Cancel');
  yes.type = 'button'; no.type = 'button';
  yes.title = question;
  row.append(el('span', 'q', question), yes, no);
  let done = false;
  const end = (ok) => {
    if (done) return;
    done = true;
    document.removeEventListener('pointerdown', outside, true);
    row.remove();
    host.classList.remove('cfm-on');
    if (activeConfirm && activeConfirm.row === row) activeConfirm = null;
    if (onEnd) onEnd(ok);
    if (ok) onYes();
  };
  // 밖을 누르면 그만둔다. **focusout 으로 그만두면 안 된다** — 크롬(맥)은 단추를 마우스로 눌러도 포커스를
  // 안 주므로 "Close" 를 누르는 pointerdown 이 focusout 을 먼저 내고, 그 취소가 click 보다 앞서 들어온다.
  function outside(ev) { if (!row.contains(ev.target)) end(false); }
  document.addEventListener('pointerdown', outside, true);
  yes.addEventListener('click', (ev) => { ev.stopPropagation(); end(true); });
  no.addEventListener('click', (ev) => { ev.stopPropagation(); end(false); });
  for (const t of ['pointerdown', 'click', 'dblclick']) row.addEventListener(t, (ev) => ev.stopPropagation());
  row.addEventListener('keydown', (ev) => {
    ev.stopPropagation();                      // Esc 는 이 줄의 것이다 — 펼침 되돌리기에 안 넘긴다
    if (ev.key === 'Escape') { ev.preventDefault(); end(false); }
  });
  host.appendChild(row);
  host.classList.add('cfm-on');
  yes.focus();                                 // 키보드만으로 닫을 수 있어야 한다. Esc 가 그만두기다
  activeConfirm = { row, cancel: () => end(false) };
  return activeConfirm;
}

// #31 ① DELETE /api/sessions/<id>. **여기서 지우지 않는다** — /events 의 gone 이 지운다.
// 낙관적으로 지우고 gone 도 받으면 지우는 길이 둘이 되고, 그때 둘째 브라우저와 어긋난다(protocol.md
// "낸 쪽도 방송을 되받는다"). 실패하면 표시만 되돌리고 토스트로 말한다.
async function closeSession(id) {
  if (closing.has(id)) return;
  closing.add(id);
  paintClosing(id);
  try {
    await api('DELETE', '/api/sessions/' + encodeURIComponent(id));
  } catch (e) {
    closing.delete(id);
    paintClosing(id);
    // **404 는 성공이다** — 둘째 브라우저(또는 셸 종료)가 먼저 닫았고, 사용자가 시킨 결과는 이미 나 있다.
    // 2026-09-08 실측(f2.py, 브라우저 둘이 같은 세션의 확인을 열고 거의 동시에 Close): A 가 204,
    // B 가 404 를 받고 B 에만 'close terminal: Not Found' 토스트가 떴다 — 닫혔는데 실패를 통보받는다.
    // 데몬은 계약대로다(protocol.md `DELETE`: 없으면 404). 지우는 것은 `gone` 하나뿐이므로 여기서
    // 아무것도 안 한다 — 재접속도 되살리지 않는다(404 가 gone 보다 먼저 와도 없는 pane 에 다시 붙지 않게).
    if (e.status === 404) return;
    // 안 죽었다 — 그동안 미뤄 둔 pane 재접속을 여기서 되살린다(위 onclose 참고)
    const t = tiles.get(id);
    if (t && !t.closed && !t.ws) t.connect();
    toast(['close terminal: ' + e.message]);
  }
}
function paintClosing(id) {
  const on = closing.has(id);
  const t = tiles.get(id);
  if (t) t.el.classList.toggle('closing', on);
  const it = items.get(id);
  if (it) it.classList.toggle('closing', on);
}

let toastTimer = null;
function toast(parts) {
  // parts: [{b:'굵게'}, '보통', {d:'흐리게'}] 또는 문자열
  toastEl.textContent = '';
  for (const p of [].concat(parts)) {
    if (typeof p === 'string') toastEl.appendChild(document.createTextNode(p));
    else if (p.b != null) toastEl.appendChild(el('b', null, p.b));
    else if (p.d != null) toastEl.appendChild(el('span', 'd', p.d));
  }
  toastEl.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), 4500);
}
function agoText(id) {
  const at = changedAt.get(id) || 0;
  const d = Math.max(0, (Date.now() - at) / 1000);
  if (d < 5) return 'now';
  if (d < 60) return Math.floor(d) + 's';
  if (d < 3600) return Math.floor(d / 60) + 'm';
  if (d < 86400) return Math.floor(d / 3600) + 'h';
  return Math.floor(d / 86400) + 'd';
}

// ── 테마 (system / light / dark, localStorage 'palmar-theme') ──
const themeBtn = $('#theme');
const darkMq = matchMedia('(prefers-color-scheme: dark)');
function storedTheme() {
  try { const v = localStorage.getItem(LS_THEME); return v === 'light' || v === 'dark' ? v : null; }
  catch (e) { return null; }
}
function applyTheme(mode) {   // mode: 'light' | 'dark' | null(system)
  if (mode) root.dataset.theme = mode; else delete root.dataset.theme;
  themeBtn.dataset.mode = mode || 'system';
  themeBtn.title = 'theme: ' + (mode || 'system') + ' — click to change';
  try { if (mode) localStorage.setItem(LS_THEME, mode); else localStorage.removeItem(LS_THEME); } catch (e) {}
  if (typeof renderBadge === 'function') renderBadge(true);   // 파비콘 색은 --st-* 를 읽는다
  rethemeTerminals();
}
function cycleTheme() {
  const cur = root.dataset.theme || null;
  applyTheme(cur === null ? 'light' : cur === 'light' ? 'dark' : null);
}
themeBtn.addEventListener('click', cycleTheme);
themeBtn.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); cycleTheme(); } });
if (darkMq.addEventListener) darkMq.addEventListener('change', () => { if (!root.dataset.theme) rethemeTerminals(); });

// xterm 은 색을 값으로 받는다. 값은 CSS 변수에서 읽어 넘긴다 — 색이 두 곳에 있지 않게.
function termTheme() {
  return {
    background: cssVar('--tile'),
    foreground: cssVar('--term-ink'),
    cursor: cssVar('--accent'),
    cursorAccent: cssVar('--on-accent'),
    selectionBackground: cssVar('--accent-2'),
    selectionInactiveBackground: cssVar('--accent-2'),
  };
}
function rethemeTerminals() {
  const th = termTheme();
  for (const t of tiles.values()) t.term.options.theme = th;
}

// ── 경로 표시 ──────────────────────────────────────────
let home = null;   // 뿌리 목록의 첫 항목을 홈으로 본다 — protocol.md 는 "사용자 홈 + …" 순서로 적었고 홈 표시가 따로 없다
function shortPath(p) {
  if (!p) return '';
  if (home && (p === home || p.startsWith(home + '/'))) return '~' + p.slice(home.length);
  return p;
}

// ── HTTP ────────────────────────────────────────────────
async function api(method, path, body) {
  const url = new URL(path, location.origin);
  if (method !== 'GET') url.searchParams.set('token', TOKEN);   // 상태를 바꾸는 요청만 토큰 (protocol.md "인증")
  const init = { method };
  if (body !== undefined) { init.headers = { 'content-type': 'application/json' }; init.body = JSON.stringify(body); }
  const r = await fetch(url, init);
  const text = await r.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
  // 실패에 **상태를 실어 준다** — 부르는 쪽이 "남이 먼저 했다"(404·409)와 진짜 실패를 가려야 한다.
  // 몸의 한 줄은 그대로 message 다(protocol.md: 4xx 의 몸은 `{"error": "사람이 읽는 한 줄"}`).
  if (!r.ok) {
    const err = new Error((data && data.error) || (r.status + ' ' + r.statusText));
    err.status = r.status;
    throw err;
  }
  return data;
}

// ── 타일 ────────────────────────────────────────────────
class Tile {
  constructor(s) {
    this.id = s.id;
    this.s = s;
    this.closed = false;
    this.ws = null;
    this.lastOffset = 0;     // 다음 접속의 from=. hello.offset − hello.replayed + 그 뒤 받은 바이트
    this.base = 0;
    this.received = 0;
    this.sentCols = 0; this.sentRows = 0;
    this.retry = 0;
    this.lastLine = '';
    this.off = false;

    const e = this.el = el('div', 'tile');
    e.dataset.id = s.id;
    // 다른 캔버스의 것이면 화면에만 없다 — 세션도 웹소켓도 그대로 산다(원칙 2)
    if (current !== null && s.canvas !== current) e.classList.add('other');
    const tb = el('div', 'tb');
    this.dotEl = el('span', 'dot');
    this.nameEl = el('span', 'name');
    this.pillEl = el('span', 'st');
    this.szEl = el('span', 'sz');
    this.rnEl = el('span', 'rn'); this.rnEl.title = 'rename (or double-click the name)';
    this.xpEl = el('span', 'xp'); this.xpEl.title = 'expand';
    // #31 ① 닫기. **상자는 .rn·.xp 와 같은 몸이다**(같은 CSS 규칙 한 줄에 들어 있다 — 크기·테·hover 가
    // 갈릴 자리가 없다) 그리고 같은 자리에 이어 붙는다. 안의 그림만 다르다 — 획 둘(×).
    // 글리프(✕)를 안 쓴 이유는 .rn 과 같다: 폰트마다 다르게 그려진다(AGENTS.md).
    // <button> 인 것만 다르다 — 부수는 것은 키보드로도 닿아야 한다(#31 ④). 생김새는 span 과 같다.
    this.clEl = el('button', 'cl'); this.clEl.type = 'button';
    this.clEl.title = 'close terminal'; this.clEl.setAttribute('aria-label', 'close terminal');
    tb.append(this.dotEl, this.nameEl, this.pillEl, this.szEl, this.rnEl, this.xpEl, this.clEl);
    // #25 판마다 글자 크기. Ctrl/⌘+휠은 브라우저 확대이기도 하므로 반드시 막는다 — 안 막으면
    // 창 하나를 키우려다 페이지 전체가 커진다. 그냥 휠은 그대로 두어 xterm 의 스크롤백이 산다.
    // **캡처 단계로 받는다.** 버블로 받으면 xterm 의 스크롤백 처리기가 자식에서 먼저 먹어,
    // 스크롤이 맨 위나 맨 아래에 닿았을 때만 여기까지 온다(사용자 보고 2026-09-08).
    e.addEventListener('wheel', (ev) => {
      if (!ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault(); ev.stopPropagation();
      this.setFont((this.term.options.fontSize || FONT_PX) + (ev.deltaY < 0 ? FONT_STEP : -FONT_STEP));
    }, { passive: false, capture: true });
    // 크기 표시가 곧 되돌리기 단추다 — 제목 줄에 단추를 하나 더 붙이지 않으려고 이미 있는 것에 얹는다.
    this.szEl.addEventListener('click', (ev) => { ev.stopPropagation(); this.setFont(FONT_PX); });
    this.termEl = el('div', 'term');
    this.gripEl = el('div', 'grip');
    e.append(tb, this.termEl, this.gripEl);

    // 자리: 저장된 것이 있으면 그것, 없으면 빈 자리 (⑩ 임시)
    const saved = layout[s.id];
    const w = saved && saved.w >= MIN_W ? saved.w : DEFAULT_W;
    const h = saved && saved.h >= MIN_H ? saved.h : DEFAULT_H;
    let x, y;
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) { x = saved.x; y = saved.y; }
    else { const p = firstFree(w, h, s.canvas); x = p.x; y = p.y; }
    const z = saved && saved.z ? saved.z : ++zTop;
    zTop = Math.max(zTop, z);
    Object.assign(e.style, { left: x + 'px', top: y + 'px', width: w + 'px', height: h + 'px', zIndex: String(z) });
    // **저장된 글자 크기를 여기서 지우지 않는다.** 이 줄은 자리를 다시 적는 것이지 이 판의 성질을
    // 통째로 새로 쓰는 것이 아니다 — 통째로 덮어써서 f 가 날아갔고, 그래서 새로 열면 늘 기본
    // 크기로 떴다(실측: localStorage 에는 f 가 남아 있는데 화면은 기본값이었다).
    layout[s.id] = { x, y, w, h, z };
    if (saved && saved.f) layout[s.id].f = saved.f;
    saveLayout();
    cvScroll.appendChild(e);

    // xterm — 터미널 에뮬레이션은 브라우저가 한다 (AGENTS.md 원칙 1)
    this.term = new Terminal({
      // layout[s.id] 이 아니라 **saved** 에서 읽는다 — 위에서 layout 항목을 다시 쓰므로,
      // 그 사이에 무엇이 지워져도 이 값은 흔들리지 않는다.
      fontSize: (saved && saved.f) || FONT_PX,
      fontFamily: cssVar('--mono'),
      lineHeight: LINE_HEIGHT,
      theme: termTheme(),
      scrollback: 1000,
      cursorBlink: false,
      allowProposedApi: true,
    });
    this.fit = new FitAddon.FitAddon();
    this.term.loadAddon(this.fit);
    // 글자를 처음 골랐을 때 **한 번만** 단축키를 알려 준다. 복사 단축키는 터미널마다 달라서
    // (여기서는 Ctrl+C 가 인터럽트다) 눌러 보고 알 수가 없다 — 고르는 순간이 그걸 알려 줄 자리다.
    this.term.onSelectionChange(() => {
      if (clipHintShown || !this.term.hasSelection()) return;
      clipHintShown = true;
      try { localStorage.setItem(LS_CLIPHINT, '1'); } catch (e) {}
      toast([CLIP_HINT + ' to copy and paste', 'Ctrl+C stays as interrupt, the way a terminal expects']);
    });
    // xterm 이 키를 처리하기 **전에** 본다. true 면 그대로 넘기고, false 면 우리가 가져간다.
    this.term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== 'keydown') return true;
      const mod = ev.metaKey || (ev.ctrlKey && ev.shiftKey);
      if (!mod || ev.altKey) return true;
      const k = (ev.key || '').toLowerCase();
      if (k === 'c') {
        const sel = this.term.getSelection();
        if (!sel) return true;              // 고른 것이 없으면 터미널의 것이다
        ev.preventDefault();
        clipWrite(sel);
        return false;
      }
      if (k === 'v') {
        ev.preventDefault();
        clipRead().then((t) => { if (t) this.term.paste(t); });
        return false;
      }
      return true;
    });
    this.term.open(this.termEl);
    this.gl = tryWebgl(this.term, () => { this.gl = null; updateStatusBar(); });
    // 안 보이는 동안(다른 캔버스) 재면 열 수가 0 으로 나온다 — 보이게 될 때 refit() 이 잰다
    this.fitted = false;
    if (this.visible()) { this.fit.fit(); this.fitted = true; }

    this.term.onData((d) => {
      if (this.ws && this.ws.readyState === 1) this.ws.send(enc.encode(d));
      if (this.s.status === 'done') sendSeen(this.id);   // 치고 있으면 본 것이다
    });
    this.term.onBinary((d) => {
      const b = new Uint8Array(d.length);
      for (let i = 0; i < d.length; i++) b[i] = d.charCodeAt(i) & 255;
      if (this.ws && this.ws.readyState === 1) this.ws.send(b);
    });

    this.xpEl.addEventListener('click', (ev) => { ev.stopPropagation(); setMax(this, !this.el.classList.contains('max')); });
    this.rnEl.addEventListener('click', (ev) => { ev.stopPropagation(); this.rename(); });
    this.clEl.addEventListener('click', (ev) => { ev.stopPropagation(); this.askClose(); });
    this.nameEl.addEventListener('dblclick', (ev) => { ev.stopPropagation(); this.rename(); });
    e.addEventListener('pointerdown', () => focusTile(this.id, { user: true, keyboard: false }), true);
    this.dragify();
    this.update(s);
    this.showSize();
    this.connect();
  }

  rect() { return { x: this.el.offsetLeft, y: this.el.offsetTop, w: this.el.offsetWidth, h: this.el.offsetHeight }; }

  // ⑪ 이 캔버스의 것인가. current 가 null 이면 **캔버스를 모르는 데몬**이라 전부 보인다 (아래 renderTabs).
  visible() { return current === null || this.s.canvas === current; }

  // ⑫ 이름 바꾸기. 빈 이름은 null 로 보낸다(= 이름 지우기) — 이름표가 경로로 돌아간다.
  rename() {
    inlineEdit(this.nameEl, this.s.name || '', async (v) => {
      try { upsert(await api('PATCH', '/api/sessions/' + encodeURIComponent(this.id), { name: v || null })); }
      catch (e) { toast(['rename: ' + e.message]); }
    }, () => { if (!this.closed) this.update(this.s); });   // 고치는 동안 온 방송을 지금 반영한다
  }

  // #31 ① 제목줄 안에서 묻는다. 확인이 열려 있는 동안 제목줄의 다른 것은 CSS 가 내린다(.tb.cfm-on).
  askClose() { askClose(this.el.firstChild, 'Close this terminal?', () => closeSession(this.id)); }

  // ── pane 채널 /pty/<id> ──
  connect() {
    if (this.closed) return;
    const q = new URLSearchParams({
      token: TOKEN, cols: String(this.term.cols), rows: String(this.term.rows), from: String(this.lastOffset),
    });
    const ws = new WebSocket(`ws://${location.host}/pty/${encodeURIComponent(this.id)}?${q}`);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;
    this.hello = null;
    this.sentCols = this.term.cols; this.sentRows = this.term.rows;

    ws.onopen = () => {
      this.retry = 0;
      // 접속 URL 에 실은 뒤 크기가 바뀌었으면 알린다
      if (this.term.cols !== this.sentCols || this.term.rows !== this.sentRows) this.sendResize();
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        let m = null;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m && m.t === 'hello') {
          // offset 은 재생 **뒤** 의 절대 오프셋(protocol.md). 재생분은 곧 바이너리로 오므로,
          // 재생 전 위치(offset − replayed)에 실제로 받은 바이트를 더해 다음 from 을 만든다.
          // 재생 도중에 끊겨도 받은 만큼만 세어진다.
          this.hello = m;
          this.base = (m.offset | 0) - (m.replayed | 0);
          this.received = 0;
          this.lastOffset = this.base;
          this.s.alt = !!m.alt;
        }
        return;
      }
      const bytes = new Uint8Array(ev.data);
      this.received += bytes.length;
      if (this.hello) this.lastOffset = this.base + this.received;
      this.term.write(bytes, () => {
        // 흐름 제어: 파싱이 끝난 뒤에만 ACK 한다 (스파이크 D)
        if (ws.readyState === 1) ws.send(JSON.stringify({ t: 'ack', n: bytes.length }));
      });
      this.noteOutput();
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.closed || !sessions.has(this.id)) return;
      // #31 ①: 우리가 닫아 달라고 한 pane 이다. 데몬은 pane 소켓을 먼저 닫고 gone 을 그 뒤에 보낼 수 있는데,
      // 그 사이에 다시 두드리면 /pty/<id> 가 404 로 답해 콘솔에 오류가 남는다(실측: dev-stub --delay-gone 1.2).
      // gone 이 오면 dispose 가 이 타일을 거둔다 — **여기서 세션을 지우는 것이 아니다.**
      if (closing.has(this.id)) return;
      this.retry = Math.min(10000, this.retry ? this.retry * 2 : 500);
      setTimeout(() => this.connect(), this.retry);
    };
    ws.onerror = () => {};   // onclose 가 뒤따른다
  }

  sendResize() {
    if (!this.ws || this.ws.readyState !== 1) return;
    if (this.term.cols === this.sentCols && this.term.rows === this.sentRows) return;
    this.sentCols = this.term.cols; this.sentRows = this.term.rows;
    this.ws.send(JSON.stringify({ t: 'resize', cols: this.term.cols, rows: this.term.rows }));
  }

  refit() {
    if (!this.visible()) return;            // 다른 캔버스 — 잴 수 없다(display:none)
    const d = this.fit.proposeDimensions();
    if (!d || !d.cols || !d.rows) return;   // 아직 크기가 없다
    this.fit.fit();
    this.fitted = true;
    this.sendResize();   // "이것만이 행·열을 바꾼다" — 창 크기가 바뀌었을 때만 보낸다
    this.showSize();
  }

  showSize() {
    this.szEl.textContent = this.term.cols + '×' + this.term.rows;
    const f = this.term.options.fontSize;
    const own = f && Math.abs(f - FONT_PX) > 0.01;
    this.szEl.classList.toggle('own', !!own);
    this.szEl.title = own
      ? 'text ' + f + 'px — click to reset  ·  ' + KMOD + '+wheel to change'
      : this.term.cols + '×' + this.term.rows + ' — ' + KMOD + '+wheel over the terminal changes the text size';
  }

  // 창은 안 건드린다. 글자만 바꾸고 다시 재면 행·열이 따라온다 — 그리고 그 resize 가 에이전트에
  // 전해지므로(refit → sendResize) TUI 는 새 크기로 다시 그린다.
  setFont(px) {
    const f = Math.max(FONT_MIN, Math.min(FONT_MAX, Math.round(px * 4) / 4));
    if (Math.abs(f - (this.term.options.fontSize || FONT_PX)) < 0.01) return;
    this.term.options.fontSize = f;
    this.refit();          // fit → sendResize → showSize 를 한 번에 한다
    this.persist();
  }

  update(s) {
    this.s = s;
    const cls = STATUS_CLASS[s.status] || 'idle';
    this.dotEl.className = 'dot ' + cls;
    // ⑫ 사람이 준 이름이 이긴다. 없으면 지금까지의 경로 이름표 그대로.
    // 고치는 중이면 그 칸을 건드리지 않는다 — 방송이 와도 사람 손이 먼저다.
    if (!this.nameEl.querySelector('input')) {
      this.nameEl.textContent = '';
      if (s.name) this.nameEl.textContent = s.name;
      else this.nameEl.append((s.agent || 'shell') + ' ', el('span', null, '· ' + shortPath(s.cwd)));
    }
    if (PILL.has(s.status)) { this.pillEl.hidden = false; this.pillEl.className = 'st ' + cls; this.pillEl.textContent = s.status; }
    else { this.pillEl.hidden = true; }
  }

  // 목록의 "마지막 한 줄" — 버퍼에서 커서 위쪽으로 빈 줄이 아닌 첫 줄. 상태 판정에는 안 쓴다(화면을 읽어 상태를 정하지 않는다).
  noteOutput() {
    if (this.msgTimer) return;
    this.msgTimer = setTimeout(() => {
      this.msgTimer = null;
      const line = lastLine(this.term);
      if (line !== this.lastLine) {
        this.lastLine = line;
        const it = items.get(this.id);
        if (it) it._msg.textContent = msgText(this.s);
      }
    }, 400);
  }

  persist() {
    const r = this.rect();
    layout[this.id] = { x: r.x, y: r.y, w: r.w, h: r.h, z: parseInt(this.el.style.zIndex, 10) || 0 };
    const f = this.term.options.fontSize;
    if (f && Math.abs(f - FONT_PX) > 0.01) layout[this.id].f = f;   // 기본값은 안 적는다
    saveLayout();
  }

  dragify() {
    const bar = this.el.firstChild, grip = this.gripEl;
    let mode = null, sx = 0, sy = 0, ox = 0, oy = 0, ow = 0, oh = 0;
    const down = (m) => (ev) => {
      // 제목줄 위의 단추·입력칸·확인 줄은 끌기가 아니다 (#31: .cl 과 .cfm 이 여기 붙었다)
      // .sz.own 만 뺀다 — 평소의 크기 표시는 제목줄의 일부라 끌려야 하고, 글자 크기를 바꾼
      // 판에서만 그것이 되돌리기 단추가 된다(#25). 안 빼면 pointerdown 이 끌기로 잡혀 click 이 안 난다.
      if (ev.button !== 0 || ev.target.closest('.xp, .rn, .cl, .ed, .cfm, .sz.own') || this.el.classList.contains('max')) return;
      mode = m; sx = ev.clientX; sy = ev.clientY;
      ({ x: ox, y: oy, w: ow, h: oh } = this.rect());
      this.el.classList.add('drag');
      ev.currentTarget.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    };
    const move = (ev) => {
      if (!mode) return;
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      // 미니맵 사각형은 **방금 계산한 값**으로 같이 옮긴다 — DOM 에 다시 묻지 않는다
      if (mode === 'move') {
        const nx = Math.max(0, ox + dx), ny = Math.max(0, oy + dy);
        this.el.style.left = nx + 'px';
        this.el.style.top = ny + 'px';
        mmSet(this.id, nx, ny, ow, oh);
      } else {
        const nw = Math.max(MIN_W, ow + dx), nh = Math.max(MIN_H, oh + dy);
        this.el.style.width = nw + 'px';
        this.el.style.height = nh + 'px';
        mmSet(this.id, ox, oy, nw, nh);
      }
    };
    const up = () => {
      if (!mode) return;
      const was = mode; mode = null;
      this.el.classList.remove('drag');
      paintTidy();          // 창을 옮기면 거둘 것이 생기거나 없어진다
      if (was === 'size') this.refit();   // 크기 조절을 놓았을 때만 PTY 에 알린다 (스파이크 D)
      this.persist();
      renderMinimap();                    // 세계가 자랐을 수 있다 — 배율을 다시 잡는다
      refreshOff();
    };
    bar.addEventListener('pointerdown', down('move'));
    grip.addEventListener('pointerdown', down('size'));
    for (const t of [bar, grip]) {
      t.addEventListener('pointermove', move);
      t.addEventListener('pointerup', up);
      t.addEventListener('pointercancel', up);
    }
  }

  dispose() {
    this.closed = true;
    clearTimeout(this.msgTimer);
    if (this.ws) { try { this.ws.close(); } catch (e) {} this.ws = null; }
    try { this.term.dispose(); } catch (e) {}
    this.el.remove();
  }
}

function tryWebgl(term, onLoss) {
  if (!window.WebglAddon) return null;
  try {
    const gl = new WebglAddon.WebglAddon();
    gl.onContextLoss(() => { gl.dispose(); onLoss(); });   // 컨텍스트를 잃으면 xterm 이 DOM 렌더러로 돌아간다
    term.loadAddon(gl);
    return gl;
  } catch (e) {
    console.warn('palmar: WebGL unavailable, DOM renderer', e && e.message);
    return null;
  }
}

function lastLine(term) {
  const b = term.buffer.active;
  const top = b.baseY + b.cursorY;
  for (let y = top; y >= Math.max(0, top - 40); y--) {
    const l = b.getLine(y);
    if (!l) continue;
    const t = l.translateToString(true).trim();
    if (t) return t;
  }
  return '';
}

// ⑩ 임시: 빈 자리 훑기. 밀어내기는 #23. **같은 캔버스의 창만 본다** — 캔버스는 서로 다른 종이다(⑪).
// 자리는 DOM 이 아니라 좌표 스토어에서 읽는다: 다른 캔버스의 타일은 display:none 이라 offsetLeft 가 0 이고,
// DOM 을 믿으면 새 창이 원점에 몰린다. (AGENTS.md "창은 스토어에서 직접 읽는다" 와 같은 이유이기도 하다.)
// 창을 닫으면 그 자리는 빈다. **아래·오른쪽은 브라우저가 알아서 거둔다** — 스크롤 넓이를 가장 먼
// 타일까지로 재기 때문이다. 위·왼쪽은 안 거둬진다: 원점이 0 에 고정이라 첫 타일 앞의 빈 자리도
// 여전히 '내용' 으로 친다. 그래서 맨 아래 창을 닫으면 공간이 줄고 맨 위 창을 닫으면 안 줄었다.
//
// **자동으로는 안 한다.** 원점 앞의 빈 자리는 내용을 움직여야만 없앨 수 있고, 화면 위쪽에 있을 때는
// 보이는 창이 튄다(실측: 남은 창이 206px 뛰었다). 창을 놓아 둔 자리는 이 프로그램의 약속이라,
// 남의 창이 닫혔다고 내 창이 움직이면 안 된다. 그래서 **사람이 시킬 때만** 한다 — 시킨 사람에게는
// 움직이는 것이 놀랄 일이 아니다.
// 이 캔버스에 거둘 빈 자리가 있나. 단추를 흐리게 할지 정한다 — 눌러도 아무 일이 없으면
// 단추가 거짓말을 하는 것이다.
function tidySlack(canvasId) {
  const mine = [...tiles.values()].filter((t) => t.s.canvas === canvasId && layout[t.id]);
  if (!mine.length) return 0;
  return Math.max(0, Math.min(...mine.map((t) => layout[t.id].x)) - GAP)
       + Math.max(0, Math.min(...mine.map((t) => layout[t.id].y)) - GAP);
}

function paintTidy() {
  const b = document.getElementById('tidy');
  if (!b) return;
  const slack = current === null ? 0 : tidySlack(current);
  b.disabled = !slack;
  b.title = slack
    ? 'tidy this canvas — pull the windows back to the corner'
    : 'tidy this canvas — nothing to close up, it already starts at the corner';
}

function tidyCanvas(canvasId) {
  const mine = [...tiles.values()].filter((t) => t.s.canvas === canvasId && layout[t.id]);
  if (!mine.length) return false;
  const dx = Math.min(...mine.map((t) => layout[t.id].x)) - GAP;
  const dy = Math.min(...mine.map((t) => layout[t.id].y)) - GAP;
  if (dx <= 0 && dy <= 0) return false;
  const sx = Math.max(0, dx), sy = Math.max(0, dy);
  const l0 = cvScroll.scrollLeft, t0 = cvScroll.scrollTop;
  // **되돌려 읽지 않고 의도한 값을 적는다.** `persist()` 는 `offsetLeft` 를 읽는데, 자리에 전환이
  // 걸려 있어 그 값은 옮기는 **중간값**이다 — 그대로 저장하면 옛 자리가 다시 들어가 아무 일도 안
  // 일어난 것이 된다(실측: 눌러도 자리가 그대로였다). 끄는 길은 `drag` 클래스로 전환을 꺼서 이 함정을
  // 비껴가고 있었다.
  for (const t of mine) {
    const r = layout[t.id];
    t.el.style.left = (r.x - sx) + 'px';
    t.el.style.top = (r.y - sy) + 'px';
    layout[t.id] = Object.assign({}, r, { x: r.x - sx, y: r.y - sy });
  }
  saveLayout();
  // 보던 자리를 같이 당긴다. 내용이 화면보다 짧아지면 브라우저가 0 으로 깎는데, 그때는 어차피
  // 전부가 한 화면에 들어온 것이라 볼 것을 놓치지 않는다.
  cvScroll.scrollLeft = Math.max(0, l0 - sx);
  cvScroll.scrollTop = Math.max(0, t0 - sy);
  renderMinimap();
  refreshOff();
  paintTidy();
  return true;
}

function firstFree(w, h, canvasId) {
  const W = cvScroll.clientWidth, H = cvScroll.clientHeight;
  const rects = [];
  for (const t of tiles.values()) { if (t.s.canvas === canvasId && layout[t.id]) rects.push(layout[t.id]); }
  const hit = (x, y) => rects.some((r) => x < r.x + r.w + GAP && x + w + GAP > r.x && y < r.y + r.h + GAP && y + h + GAP > r.y);
  for (let y = GAP; y + h <= H; y += GRID)
    for (let x = GAP; x + w <= W; x += GRID)
      if (!hit(x, y)) return { x, y };
  const bottom = rects.reduce((m, r) => Math.max(m, r.y + r.h), 0);
  return { x: GAP, y: bottom ? bottom + GAP : GAP };
}

// ── 포커스 · 펼치기 ─────────────────────────────────────
function focusTile(id, opts) {
  opts = opts || {};
  const t = tiles.get(id);
  if (!t) return;
  if (focused !== id) {
    const prev = tiles.get(focused);
    if (prev) prev.el.classList.remove('focus');
    t.el.classList.add('focus');
    focused = id;
    t.el.style.zIndex = String(++zTop);
    t.persist();
    for (const [iid, it] of items) it.classList.toggle('cur', iid === id);
    renderMinimap();
  }
  // done 은 사용자가 그 창을 봐야 꺼진다 — 사용자의 손이 닿은 포커스만 "봤다" 로 친다
  if (opts.user && t.s.status === 'done') sendSeen(id);
  if (opts.keyboard !== false) t.term.focus();
}

// 그 세션 앞으로 간다. 목록 클릭과 알림 클릭이 같은 길을 쓴다 (#40).
function goToSession(id) {
  const tile = tiles.get(id);
  if (!tile) return;
  // ⑪ 다른 캔버스의 것이면 그 캔버스로 넘어가서 그 창으로 간다
  if (current !== null && sessions.has(id) && sessions.get(id).canvas !== current) switchCanvas(sessions.get(id).canvas);
  if (maxed && maxed !== tile) setMax(maxed, false);
  tile.el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
  focusTile(id, { user: true });
  tile.el.animate([{ transform: 'scale(1)' }, { transform: 'scale(1.012)' }, { transform: 'scale(1)' }], { duration: 260 });
}

function sendSeen(id) {
  if (eventsWs && eventsWs.readyState === 1) eventsWs.send(JSON.stringify({ t: 'seen', id }));
}

let prevScroll = null;
function setMax(tile, on) {
  for (const t of tiles.values()) t.el.classList.remove('max');
  const was = maxed; maxed = null;
  cv.classList.toggle('has-max', !!on);
  if (on) {
    prevScroll = { l: cvScroll.scrollLeft, t: cvScroll.scrollTop };
    cvScroll.scrollTo(0, 0);
    tile.el.classList.add('max');
    maxed = tile;
    focusTile(tile.id, { user: true });
  } else if (prevScroll) {
    cvScroll.scrollTo(prevScroll.l, prevScroll.t);
    prevScroll = null;
  }
  // 행·열이 진짜로 늘어난다 — .tile 의 .35s 전환이 끝난 뒤 한 번 맞춘다
  const targets = new Set([tile, was].filter(Boolean));
  setTimeout(() => { for (const t of targets) if (!t.closed) t.refit(); refreshOff(); }, 380);
}
addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !maxed) return;
  // 터미널 안의 Esc 는 앱(vim·claude)의 것이다 — 빼앗지 않는다. 캔버스·레일에서 누른 Esc 만 되돌린다.
  if (e.target && e.target.closest && e.target.closest('.xterm')) return;
  setMax(maxed, false);
});
// #21: 펼친 직후엔 포커스가 터미널 안이라 위 Esc 가 안 먹는다. 안내 알약을 눌러도 캔버스로 돌아가게 한다
// (터미널의 Esc 는 그대로 앱에 넘긴다 — 이 길은 알약 클릭만).
$('.esc').addEventListener('click', () => { if (maxed) setMax(maxed, false); });
addEventListener('resize', () => {
  // 창이 좁아지면 지금 레일 폭이 캔버스를 최소치 아래로 밀 수 있다 — 여기서 다시 가둔다.
  // **폭만 고치고 뒷정리는 아래에서 한 번만** 한다 — setRail 을 쓰면 미니맵을 세 번 다시 그린다.
  railPut('l', railW('l')); railPut('r', railW('r'));
  if (maxed) maxed.refit(); renderMinimap(); refreshOff();
});

// ── 레일 폭 (#19) ──────────────────────────────────────────
// 폭은 CSS 변수 --rail-l·--rail-r 하나에만 있고 .top 과 .body 가 그것을 함께 본다 — 전에는 둘이
// 같은 값을 따로 적고 있어서 한쪽만 고치면 위 줄과 아래 몸이 어긋났다.
// **레일이 좁아지면 캔버스가 넓어진다.** 창이 그대로여도 보이는 자리가 달라지므로, 창 크기가
// 바뀔 때와 **똑같은 뒷정리**가 필요하다(펼친 창 refit · 미니맵 눈금 · 화면 밖 표시).
//: 자동 정리. **꺼진 채로 시작한다** — 보이는 창을 움직이는 일이라, 남의 창이 닫혔다고 내 창이
//: 뛰면 안 된다. 켜 두면 그 대가를 알고 켠 것이다.
const LS_AUTOTIDY = 'palmar.autotidy';
let autoTidy = false;
try { autoTidy = localStorage.getItem(LS_AUTOTIDY) === '1'; } catch (e) {}

const LS_RAILS = 'palmar.rails';
const RAIL_DEF = { l: 256, r: 232 };
const RAIL_MIN = { l: 180, r: 160 };   // 이보다 좁으면 왼쪽은 이름표가, 오른쪽은 위 줄 단추가 깨진다
const RAIL_MAX = 480;
const CANVAS_MIN = 320;                // 레일 둘이 캔버스를 이만큼 아래로 밀지 못한다

function railClamp(side, px) {
  const other = side === 'l' ? railW('r') : railW('l');
  const room = innerWidth - other - CANVAS_MIN;
  return Math.round(Math.max(RAIL_MIN[side], Math.min(px, RAIL_MAX, room)));
}
function railW(side) {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--rail-' + side));
  return isFinite(v) ? v : RAIL_DEF[side];
}
function railPut(side, px) {   // 폭만 고친다. 뒷정리 없음
  document.documentElement.style.setProperty('--rail-' + side, railClamp(side, px) + 'px');
}
function setRail(side, px, save) {
  railPut(side, px);
  if (save !== false) saveRails();
  if (maxed) maxed.refit();
  renderMinimap();
  refreshOff();
}
function saveRails() {
  try { localStorage.setItem(LS_RAILS, JSON.stringify({ l: railW('l'), r: railW('r') })); } catch (e) {}
}
function loadRails() {
  let v = null;
  try { v = JSON.parse(localStorage.getItem(LS_RAILS) || 'null'); } catch (e) {}
  if (!v) return;
  // 저장된 값이 지금 창에 안 맞을 수 있다(작은 화면으로 옮겼다) — 그대로 쓰지 않고 다시 가둔다.
  for (const side of ['l', 'r']) if (typeof v[side] === 'number') railPut(side, v[side]);
}

function rzGrip(el, side) {
  el.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    el.setPointerCapture(ev.pointerId);
    el.classList.add('on');
    document.body.classList.add('rz-drag');
    const x0 = ev.clientX, w0 = railW(side);
    // 끄는 동안은 저장하지 않는다 — 손을 뗄 때 한 번만 쓴다(그 사이 localStorage 를 초당 60번 쓰지 않게).
    const move = (e2) => setRail(side, side === 'l' ? w0 + (e2.clientX - x0) : w0 - (e2.clientX - x0), false);
    const up = () => {
      el.classList.remove('on');
      document.body.classList.remove('rz-drag');
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
      removeEventListener('pointercancel', up);
      saveRails();
    };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
    addEventListener('pointercancel', up);
  });
  // 두 번 누르면 기본값으로. 끌어서 되돌리기 어려운 값을 만들어 놓고 못 빠져나오는 일이 없게 한다.
  el.addEventListener('dblclick', () => setRail(side, RAIL_DEF[side]));
  // 키보드로도 닿아야 한다 — 닫기 단추를 <button> 으로 둔 것과 같은 이유다.
  el.addEventListener('keydown', (ev) => {
    const step = ev.shiftKey ? 48 : 16;
    if (ev.key === 'ArrowLeft')  { ev.preventDefault(); setRail(side, railW(side) + (side === 'l' ? -step : step)); }
    else if (ev.key === 'ArrowRight') { ev.preventDefault(); setRail(side, railW(side) + (side === 'l' ? step : -step)); }
    else if (ev.key === 'Home' || ev.key === 'Escape') { ev.preventDefault(); setRail(side, RAIL_DEF[side]); }
  });
}

// ── 캔버스 밖 표시 ("↗ off") ────────────────────────────
// **자리는 좌표 스토어에서 읽는다 — 미니맵과 같은 이유다**(AGENTS.md "스크롤마다 DOM 레이아웃을 읽지 마라",
// "창은 스토어에서 직접 읽는다"). 옛 판은 창마다 offsetLeft/Top/Width/Height 를 읽어 스크롤 한 묶음마다
// **창 수 × 4번**의 레이아웃을 강제했다(실측: 창 5개에 20번). 캔버스는 무한히 자라므로(⑩) 그 값은 창 수를
// 따라 늘어난다 — cate 가 초당 374번 다시 그린 것이 이 종류다. 지금은 뷰포트를 한 묶음에 한 번만 잰다.
function isOff(t, sl, st, vw, vh) {
  // 펼친 창은 캔버스를 가득 채우니 화면 밖일 수 없다 — 스토어에 남아 있는 옛 자리를 보면 안 된다
  if (t.el.classList.contains('max')) return false;
  const r = layout[t.id];
  if (!r) return false;
  return r.x + r.w <= sl || r.y + r.h <= st || r.x >= sl + vw || r.y >= st + vh;
}
let offTimer = null;
function refreshOff() {
  if (offTimer) return;
  offTimer = setTimeout(() => {
    offTimer = null;
    // 뷰포트는 한 묶음에 한 번만 잰다(상수). 창마다 다시 재면 창 수만큼 레이아웃이 강제된다.
    const sl = cvScroll.scrollLeft, st = cvScroll.scrollTop;
    const vw = cvScroll.clientWidth, vh = cvScroll.clientHeight;
    // 다른 캔버스의 것은 "↗ off" 가 아니다 — 거기엔 캔버스 이름표가 붙는다(⑪)
    // **배지 하나 뒤집자고 목록을 다시 짓지 않는다.** 옛 판은 여기서 renderList() 를 불렀고, 스크롤하는
    // 동안 그것이 6초에 60번 돌았다(실측 2026-09-08: 창 16개, 5회 중 3회 62·62·60). 매번 모든 .ses 행이
    // 부서지고 새 click 리스너와 함께 다시 나서 **레일에 잡아 둔 글자 선택이 사라졌다**(실측: 두 번째
    // 초에 빈 문자열). 일은 O(세션 수)인데 캔버스는 무한히 자란다(⑩) — AGENTS.md 가 경고한 모양이다.
    for (const t of tiles.values()) {
      const o = t.visible() ? isOff(t, sl, st, vw, vh) : false;
      if (o !== t.off) { t.off = o; paintOff(t.id); }
    }
  }, 80);
}
cvScroll.addEventListener('scroll', refreshOff, { passive: true });

// ── 캔버스를 쥐고 끌기 ────────────────────────────────────
// 빈 자리를 눌러 끌면 화면이 손을 따라온다(지도와 같다). 스크롤 막대와 휠은 그대로 있고, 이건
// 그 위에 얹는 길이다 — 캔버스는 끝없이 자라므로(⑩) 멀리 가는 길이 하나뿐이면 좁다.
// **빈 자리에서만** 시작한다: 타일 위에서 눌린 것은 타일의 것이다(제목줄 끌기·글자 선택·터미널 입력).
const PAN_SLOP = 3;      // 이만큼 움직이기 전에는 끌기가 아니다 — 그래야 그냥 누르기가 살아 있다
cvScroll.addEventListener('pointerdown', (ev) => {
  if (ev.button !== 0 || ev.target !== cvScroll) return;   // 빈 바닥에서만
  const x0 = ev.clientX, y0 = ev.clientY;
  const l0 = cvScroll.scrollLeft, t0 = cvScroll.scrollTop;
  let on = false;
  const move = (e2) => {
    const dx = e2.clientX - x0, dy = e2.clientY - y0;
    if (!on) {
      if (Math.abs(dx) < PAN_SLOP && Math.abs(dy) < PAN_SLOP) return;
      on = true;
      cvScroll.classList.add('panning');
      try { cvScroll.setPointerCapture(ev.pointerId); } catch (e) {}
    }
    // 쥔 자리가 손을 따라오도록 **반대로** 스크롤한다. 브라우저가 알아서 양끝에서 멈춘다.
    cvScroll.scrollLeft = l0 - dx;
    cvScroll.scrollTop = t0 - dy;
  };
  const up = () => {
    cvScroll.classList.remove('panning');
    removeEventListener('pointermove', move);
    removeEventListener('pointerup', up);
    removeEventListener('pointercancel', up);
  };
  addEventListener('pointermove', move);
  addEventListener('pointerup', up);
  addEventListener('pointercancel', up);
});

// ── 캔버스 탭 (⑪) ──────────────────────────────────────
// 탭은 **전환기**다. 안 놓치는 일은 왼쪽 목록이 맡는다(decisions.md ⑪) — 그래서 탭에 붙는 것은 점 하나뿐이고
// 개수도 닫기 단추도 없다. 순서의 주인은 데몬이라(protocol.md "순서는 데몬이 갖는다") 끌어 놓으면 지금 있는
// 전부를 한 번에 POST /api/canvases/order 로 보낸다 — 하나씩 고치면 두 브라우저가 다른 탭 줄을 그린다.
const addTabEl = el('span', 'ib', '＋');
addTabEl.id = 'tab-add';
addTabEl.title = 'new canvas';

// PROVISIONAL — ⑪ 이 "캔버스 이름을 사람이 짓는지 폴더에서 따는지" 를 아직 안 정했다. 프로토콜은
// name: null 만 나르고 글자는 브라우저가 만든다(protocol.md "없는 것"). 그래서 자리표 하나로 둔다.
// **자리표라 순서를 바꾸면 글자도 바뀐다**(끌어서 앞으로 보낸 "canvas 2" 는 "canvas 1" 이 된다) —
// 이름 없는 캔버스에만 해당하고, 사람이 이름을 주면 사라지는 성질이다. 폴더에서 따기로 정해지면
// 이 함수 하나만 바뀐다.
// 이름 없는 캔버스의 이름표는 **만든 차례(seq)** 로 만든다. order 로 만들면 탭을 끌어 자리를 바꾼
// 순간 이름표가 서로 바뀌어, 사용자 눈에는 캔버스 이름이 저절로 바뀐 것으로 보인다(보고 2026-09-08).
// seq 를 모르는 옛 데몬에는 옛 길로 돌아간다 — 그때는 자리가 곧 이름이었다.
function canvasLabel(c) {
  if (c && c.name) return c.name;
  return 'canvas ' + (c && c.seq ? c.seq : ((c ? c.order : 0) + 1));
}
function canvasById(id) { return canvases.get(id) || null; }

// 탭의 점은 저장하지 않는다 — 계산한다(protocol.md "탭의 점"). 이 캔버스에 나를 부르는 것이 있으면 켠다.
// #18 캔버스 지우기. **조건은 데몬이 정한다** — 여기 있는 것은 그 규칙의 사본이 아니라 거울이다.
// 데몬은 (1) 세션이 하나라도 있으면, (2) 마지막 캔버스면 409 로 거절한다(protocol.md).
// 그래서 화면은 **그 둘이 아닐 때만** 손잡이를 보여 준다 — 거절당할 요청을 낼 일이 없다.
// 캔버스 안에서 셸을 죽이며 지우는 길은 만들지 않는다: 화면의 동작이 도는 프로세스를 죽이면 안 된다.
function canvasEmpty(id) {
  for (const x of sessions.values()) if (x.canvas === id) return false;
  return true;
}
function canvasRemovable(id) { return canvasEmpty(id) && canvasOrder.length > 1; }

async function removeCanvas(id) {
  try {
    await api('DELETE', '/api/canvases/' + encodeURIComponent(id));
  } catch (e) {
    // 다른 브라우저가 그 사이에 터미널을 하나 열었을 수 있다 — 그때는 데몬이 맞고 우리가 늦은 것이다.
    if (e.status === 409) { toast([e.message, 'close its terminals first, then try again']); return; }
    if (e.status === 404) return;                 // 다른 브라우저가 먼저 지웠다 — 시킨 대로 됐다
    toast(['remove canvas: ' + e.message]);
  }
  // 탭은 여기서 지우지 않는다 — canvas_gone 방송이 지운다(터미널 닫기와 같은 규율).
}

function canvasWant(id) {
  const mine = [];
  for (const s of sessions.values()) if (s.canvas === id) mine.push(s);
  return wantClass(mine);
}

function setCanvases(list) {          // hello · canvases — 전체를 갈아 낀다
  canvases.clear();
  canvasOrder = [];
  for (const c of [...(list || [])].sort((a, b) => a.order - b.order)) { canvases.set(c.id, c); canvasOrder.push(c.id); }
  if (!current || !canvases.has(current)) current = canvasOrder[0] || null;
  applyCanvas();
}
function putCanvas(c) {               // canvas — 하나가 생겼거나 이름이 바뀌었다. id 로 멱등하게 반영한다
  const isNew = !canvases.has(c.id);
  canvases.set(c.id, c);
  if (isNew) canvasOrder.push(c.id);
  canvasOrder.sort((a, b) => canvases.get(a).order - canvases.get(b).order);
  if (!current) current = canvasOrder[0] || null;
  renderTabs();
  renderList();                       // 배지 글자가 이름을 따라간다
}
function dropCanvas(id) {             // canvas_gone — 바로 뒤에 canvases 가 따라온다(계약)
  canvases.delete(id);
  canvasOrder = canvasOrder.filter((x) => x !== id);
  if (current === id) current = canvasOrder[0] || null;
  applyCanvas();
}

// 펼침은 캔버스를 따라가지 않는다. 펼친 창이 이 캔버스의 것이 아니게 되면 펼침을 푼다 —
// .cv.has-max 가 남으면 `.cv.has-max .cv-scroll { overflow: hidden }` 때문에 **새 캔버스가 휠로 안 굴러가고**
// (스크롤이 ⑩ 에서 밀려난 창에 닿는 유일한 길이다), 아무것도 안 펼쳐진 화면 위에 Esc 알약만 떠 있게 된다.
// 실측(2026-09-08, 헤드리스 크롬 152): 캔버스 2 에서 펼친 뒤 탭으로 캔버스 1 로 오면 has-max=true 인 채
// deltaY 600 휠에 scrollTop 이 0 그대로였고, .esc 의 display 는 'flex' 였다.
function syncMax() { if (maxed && !maxed.visible()) setMax(maxed, false); }

// 캔버스를 바꿔도 타일은 살아 있다 — 화면에만 없다. 보이게 된 것만 다시 잰다.
function applyCanvas() {
  syncMax();
  for (const t of tiles.values()) {
    const on = t.visible();
    t.el.classList.toggle('other', !on);
    if (on && !t.fitted) t.refit();
  }
  renderTabs();
  renderList();
  renderMinimap();
  refreshOff();
}
function switchCanvas(id) {
  if (!canvases.has(id) || id === current) return;
  current = id;
  applyCanvas();
  paintTidy();      // 단추는 **지금 보고 있는 캔버스**를 말한다
  // 숨어 있는 동안 창 크기가 달라졌을 수 있다 — 그려진 다음에 한 번 더 맞춘다
  requestAnimationFrame(() => { for (const t of tiles.values()) if (t.visible()) t.refit(); });
}

// **탭 줄은 다시 짓지 않고 고친다.** 세션 프레임 하나하나가 renderTabs 를 부르는데(점은 계산이다),
// 옛 판은 그때마다 #tabs 를 통째로 비웠다. 그 사이에 사람이 탭을 끌고 있으면 끌던 칸이 DOM 에서 떨어져
// 나가고, 아직 살아 있는 pointermove 핸들러가 그 떨어진 칸을 다시 끼워 넣어 **같은 탭이 둘이 됐다**
// (실측 2026-09-08, 헤드리스 크롬 152 + CDP 진짜 드래그: 훅 하나에 탭 20 → 21, id 중복 1,
// 놓을 때 21개짜리 order 를 보내 409 "canvas list changed" → 순서 바꾸기가 조용히 사라졌다).
// 그리고 이름을 고치는 중에는 통째로 미뤄서 **점이 5초 넘게 거짓말을 했다**(실측: 데몬 waiting=1 인데
// 탭의 점 전부 꺼짐). 점은 탭이 지고 있는 유일한 신호라(⑪) 그게 틀리면 탭 줄이 하는 일이 없다.
// 그래서: 글자·점·고름은 **언제나** 제자리에서 고치고, 자리 옮기기(순서)만 끌기·고치기 중에 미룬다.
const tabEls = new Map();     // canvas id → 탭 DOM. 살아 있는 칸을 다시 쓴다
let tabsShown = null;         // 마지막으로 화면 안으로 끌어다 놓은 현재 탭 — 사람이 민 줄을 매 프레임 되돌리지 않으려고

function makeTab(id) {
  const t = el('div', 'tab');
  t.dataset.id = id;
  t.tabIndex = 0;
  t.setAttribute('role', 'tab');
  const nm = el('span', 'nm');
  t.appendChild(nm);
  // 캔버스 객체는 방송마다 갈리므로 id 만 붙들고 그때그때 찾는다
  t.addEventListener('click', () => { if (!tabDragged) switchCanvas(id); });
  t.addEventListener('dblclick', (ev) => { ev.preventDefault(); const c = canvasById(id); if (c) renameCanvas(c, nm); });
  t.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); switchCanvas(id); }
    else if (ev.key === 'F2') { ev.preventDefault(); const c = canvasById(id); if (c) renameCanvas(c, nm); }
  });
  tabDrag(t);
  return t;
}

function paintTab(id) {
  const c = canvases.get(id), t = tabEls.get(id);
  if (!c || !t) return;
  const label = canvasLabel(c);
  const nm = $('.nm', t);
  // 고치는 중인 칸의 글자는 건드리지 않는다 — 사람 손이 먼저다(inlineEdit 이 그 안에 input 을 둔다)
  if (nm && !nm.querySelector('input') && nm.textContent !== label) nm.textContent = label;
  t.title = label + ' — double-click to rename';
  const cur = id === current;
  t.classList.toggle('cur', cur);
  t.setAttribute('aria-selected', cur ? 'true' : 'false');
  // 점 하나. 색은 상태 색 그대로다(⑥ 미정, 새 색 없음). waiting 은 눌러도 안 꺼지고(훅이 꺼 준다),
  // done 은 그 창을 봐야 꺼진다 — 둘 다 "탭을 누르면 꺼진다" 가 아니다.
  const dot = $('.dot', t), want = canvasWant(id);
  if (!want) { if (dot) dot.remove(); }
  else if (!dot) t.appendChild(el('span', 'dot ' + want));
  else if (dot.className !== 'dot ' + want) dot.className = 'dot ' + want;

  // #18 지우기 손잡이. **지금 보고 있는 탭에만** 둔다 — 탭 줄은 전환기라 탭마다 단추를 달지 않는다
  // (protocol.md "탭의 점": 탭에 붙는 것은 점 하나뿐이다). 지울 수 없으면 아예 없다.
  const cx = $('.cx', t), can = cur && canvasRemovable(id);
  if (!can) { if (cx) cx.remove(); }
  else if (!cx) {
    const b = el('button', 'cx'); b.type = 'button';
    b.title = 'remove this canvas';
    b.setAttribute('aria-label', 'remove canvas ' + label);
    // 물어보지 않는다. 이 캔버스는 **비어 있어서** 손잡이가 있는 것이고, 없어지는 것은 이름과 자리뿐이다.
    // 안 위험한 것에까지 확인을 붙이면, 정말 위험한 것(터미널 닫기)의 확인까지 습관으로 넘기게 된다.
    b.addEventListener('click', (ev) => { ev.stopPropagation(); removeCanvas(id); });
    b.addEventListener('pointerdown', (ev) => ev.stopPropagation());   // 탭 끌기가 안 걸리게
    t.appendChild(b);
  }
  // 왜 못 지우는지는 탭 자체가 말한다 — 손잡이가 없는 이유를 짐작하게 두지 않는다.
  if (cur && !can) {
    t.title = label + (canvasEmpty(id)
      ? ' — the last canvas cannot be removed'
      : ' — has terminals; close them to remove this canvas');
  }
}

function renderTabs() {
  // **캔버스를 하나도 못 받았으면 탭 줄을 아예 내린다.** 이건 ⑪ 의 열린 항목("캔버스가 하나일 때 탭 줄을
  // 숨길지")을 정한 것이 **아니다** — 계약상 캔버스가 0개인 순간은 없으므로(protocol.md "데몬이 뜨면
  // 캔버스가 하나 있다") 0개는 "이 데몬은 아직 캔버스를 모른다" 는 뜻이고, 그때 ＋ 를 보여 주면
  // 없는 경로로 POST 하게 된다. 그 데몬에서는 캔버스가 하나인 것처럼 전부가 한 화면에 있다.
  tabsEl.hidden = canvasOrder.length === 0;
  if (tabsEl.hidden) {
    for (const [, t] of tabEls) t.remove();
    tabEls.clear();
    addTabEl.remove();
    return;
  }
  // 없어진 캔버스의 칸을 뗀다. 끌던 칸이 사라졌으면 tabDrag 의 move 가 스스로 그만둔다(거기서 확인한다).
  for (const [id, t] of [...tabEls]) if (!canvases.has(id)) { t.remove(); tabEls.delete(id); }
  // 새 캔버스는 끝에 붙는다(protocol.md "만들기는 끝에 붙는다") — 고치는 중인 칸을 밀지 않는다
  for (const id of canvasOrder) if (!tabEls.has(id)) { const t = makeTab(id); tabEls.set(id, t); tabsEl.appendChild(t); }
  for (const id of canvasOrder) paintTab(id);
  // 자리 옮기기만 미룬다. 끄는 중이면 DOM 순서가 일부러 데몬과 다르고, 고치는 중이면 옮기다가 입력칸의
  // 포커스를 떨군다. 미룬 것은 끌기가 끝날 때(tabDrag 의 up)와 고치기가 끝날 때(renameCanvas 의 after) 푼다.
  if (tabsEl.querySelector('.ed') || tabsEl.querySelector('.tab.drag')) { tabsPending = true; return; }
  tabsPending = false;
  let node = tabsEl.firstChild;
  for (const id of canvasOrder) {
    const t = tabEls.get(id);
    if (node === t) { node = node.nextSibling; continue; }
    tabsEl.insertBefore(t, node);
  }
  if (tabsEl.lastChild !== addTabEl) tabsEl.appendChild(addTabEl);
  // 탭이 20개면 줄이 넘치는데 스크롤바를 감춰 뒀다(.tabs { scrollbar-width: none }) — 현재 탭이 화면 밖에
  // 있으면 아무것도 안 골라진 줄로 보인다(실측: 왼쪽 목록의 캔버스 배지로 넘어간 뒤 .tab.cur 가 x=-1379).
  // **현재 탭이 바뀐 순간에만** 끌어다 놓는다 — 매 프레임 하면 사람이 민 줄을 도로 되돌린다.
  if (current !== tabsShown) {
    tabsShown = current;
    const t = tabEls.get(current);
    if (t) t.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
}

function renameCanvas(c, host) {
  inlineEdit(host, c.name || '', async (v) => {
    try { putCanvas(await api('PATCH', '/api/canvases/' + encodeURIComponent(c.id), { name: v || null })); }
    catch (e) { toast(['rename: ' + e.message]); renderTabs(); }
  }, () => renderTabs());   // 고치는 동안 미뤄 둔 자리 옮기기를 여기서 푼다
}

addTabEl.addEventListener('click', async () => {
  try {
    const c = await api('POST', '/api/canvases', {});
    putCanvas(c);                       // 방송도 뒤따라 오지만 id 로 멱등하다(protocol.md "낸 쪽도 방송을 되받는다")
    switchCanvas(c.id);
    const tab = tabsEl.querySelector('.tab.cur .nm');
    if (tab) renameCanvas(canvasById(c.id), tab);   // 갓 만든 것은 바로 이름을 받게 한다
  } catch (e) {
    toast(['new canvas: ' + e.message]);
  }
});

// 끌어서 순서 바꾸기. 이웃의 가운데를 지나면 자리를 바꾸고, 놓을 때 한 번 보낸다.
// 여기서 사각형을 읽는 것은 스크롤 핸들러가 아니다 — 미니맵의 금지(스크롤마다 레이아웃 읽기)와 다른 자리다.
function tabDrag(tabEl) {
  tabEl.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 || ev.target.closest('.ed, .cx')) return;
    const startX = ev.clientX;
    let dragging = false, aborted = false;
    const move = (e2) => {
      // 끌던 칸의 캔버스가 그 사이에 지워졌으면(다른 브라우저가 DELETE) 이 칸은 이미 줄에서 떨어져 나갔다.
      // 여기서 그만두지 않으면 떨어진 칸을 도로 끼워 넣어 줄에 유령이 생긴다.
      if (tabEl.parentNode !== tabsEl) { aborted = true; up(); return; }
      if (!dragging) {
        if (Math.abs(e2.clientX - startX) < 4) return;   // 누르기와 끌기를 가른다
        dragging = true;
        tabEl.classList.add('drag');
      }
      // 갈 자리를 한 번에 셈한다: 손끝보다 오른쪽에 가운데가 있는 **첫** 이웃 앞. 없으면 맨 끝(＋ 앞).
      // 이웃과 하나씩 바꿔치기하면 한 번에 여러 칸을 건너뛴 움직임에서 한 칸밖에 못 간다(실측).
      let ref = addTabEl;
      for (const o of tabsEl.querySelectorAll('.tab')) {
        if (o === tabEl) continue;
        const r = o.getBoundingClientRect();
        if (e2.clientX < r.left + r.width / 2) { ref = o; break; }
      }
      if (tabEl.nextSibling !== ref) tabsEl.insertBefore(tabEl, ref);
    };
    const up = async () => {
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
      removeEventListener('pointercancel', up);
      tabEl.classList.remove('drag');
      // 끄는 동안 미뤄 둔 자리 맞추기를 푼다 — 그만둔 경우에도(그때는 데몬 순서가 정본이다)
      if (!dragging || aborted) { if (tabsPending) renderTabs(); return; }
      tabDragged = true;
      setTimeout(() => { tabDragged = false; }, 0);      // 놓은 직후의 click 은 전환이 아니다
      const order = [...tabsEl.querySelectorAll('.tab')].map((e) => e.dataset.id);
      if (order.join(',') === canvasOrder.join(',')) { if (tabsPending) renderTabs(); return; }
      try {
        setCanvases(await api('POST', '/api/canvases/order', { order }));
      } catch (e) {
        // 409 면 그 사이에 누가 만들거나 지운 것이다 — 우리가 이미 그 이벤트를 받았으니 다시 그리면 된다
        toast(['reorder: ' + e.message]);
        renderTabs();
      }
    };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
    addEventListener('pointercancel', up);
  });
}

// ── 미니맵 (⑩ ⑪, 스파이크 J) ────────────────────────────
// **지금 보고 있는 캔버스 것 하나뿐이다** — 캔버스마다 늘어놓지 않는다(⑪). 답하는 것은 "이 캔버스 안에서
// 내가 어디 있나" 하나다. 사각형의 자리는 좌표 스토어(layout)에서 오고 DOM 에는 묻지 않는다.
// **스크롤 핸들러가 하는 일은 뷰포트 사각형의 transform 하나뿐이다**(합성만). 스파이크 J 는 레이아웃을
// 읽어도 안 아프다고 쟀지만(6000×4000·pane 40 에서 60fps), 이렇게 짜는 이유는 성능이 아니라 규칙이다
// (AGENTS.md "창마다 값을 내리지 말고 스토어에서 직접 읽어라").
const mmRects = new Map();          // id → 미니맵 사각형 DOM
let mmK = 1, mmOx = 0, mmOy = 0;    // 배율과 가운데 맞춤 여백
let cvW = 0, cvH = 0;               // 캔버스 뷰포트 크기 — 스크롤 중에 다시 재지 않으려고 들고 있는다

function mmSet(id, x, y, w, h) {    // 우리가 아는 값으로만 사각형을 놓는다
  const e = mmRects.get(id);
  if (!e) return;
  e.style.left = (mmOx + x * mmK) + 'px';
  e.style.top = (mmOy + y * mmK) + 'px';
  e.style.width = Math.max(2, w * mmK) + 'px';
  e.style.height = Math.max(2, h * mmK) + 'px';
}
function renderMinimap() {
  const list = [];
  for (const t of tiles.values()) if (t.visible() && layout[t.id]) list.push(t);
  mmRects.clear();
  mmWorldEl.textContent = '';
  if (!list.length) { mmEl.hidden = true; return; }
  mmEl.hidden = false;
  cvW = cvScroll.clientWidth; cvH = cvScroll.clientHeight;
  const bw = mmEl.clientWidth - MM_PAD * 2, bh = mmEl.clientHeight - MM_PAD * 2;
  // 세계는 캔버스가 자란 만큼이다(⑩: 한계를 두지 않는다) — 뷰포트보다 작아지지는 않는다
  let worldW = cvW, worldH = cvH;
  for (const t of list) {
    const r = layout[t.id];
    worldW = Math.max(worldW, r.x + r.w + GAP);
    worldH = Math.max(worldH, r.y + r.h + GAP);
  }
  mmK = Math.min(bw / worldW, bh / worldH);   // 가로·세로 한 배율. 따로 늘이면 모양이 거짓말을 한다
  mmOx = MM_PAD + (bw - worldW * mmK) / 2;
  mmOy = MM_PAD + (bh - worldH * mmK) / 2;
  for (const t of list) {
    // 색은 상태 점과 같은 --st-* 다(⑥). 미니맵을 위한 새 색은 만들지 않았다.
    const e = el('div', 'mm-t ' + (STATUS_CLASS[t.s.status] || 'idle') + (t.id === focused ? ' cur' : ''));
    e.dataset.id = t.id;
    mmWorldEl.appendChild(e);
    mmRects.set(t.id, e);
    const r = layout[t.id];
    mmSet(t.id, r.x, r.y, r.w, r.h);
  }
  mmVpEl.style.width = Math.max(4, cvW * mmK) + 'px';
  mmVpEl.style.height = Math.max(4, cvH * mmK) + 'px';
  mmMove();
}
// 스크롤 핸들러의 전부. 읽는 것은 scrollLeft/scrollTop, 쓰는 것은 transform. 레이아웃은 안 읽는다.
function mmMove() {
  mmVpEl.style.transform =
    'translate(' + (mmOx + cvScroll.scrollLeft * mmK) + 'px, ' + (mmOy + cvScroll.scrollTop * mmK) + 'px)';
}
cvScroll.addEventListener('scroll', mmMove, { passive: true });

// 눌러서·끌어서 뷰포트를 옮긴다. 미니맵 상자의 사각형은 누를 때 한 번만 잰다(끄는 동안 다시 안 읽는다).
(() => {
  let box = null;
  const seek = (ev) => {
    if (!box || !mmK) return;
    cvScroll.scrollLeft = Math.max(0, (ev.clientX - box.left - mmOx) / mmK - cvW / 2);
    cvScroll.scrollTop = Math.max(0, (ev.clientY - box.top - mmOy) / mmK - cvH / 2);
  };
  const up = () => {
    box = null;
    removeEventListener('pointermove', seek);
    removeEventListener('pointerup', up);
    removeEventListener('pointercancel', up);
  };
  mmEl.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    box = mmEl.getBoundingClientRect();
    ev.preventDefault();
    seek(ev);
    addEventListener('pointermove', seek);
    addEventListener('pointerup', up);
    addEventListener('pointercancel', up);
  });
})();

// ── 왼쪽 레일: 세션 목록 ────────────────────────────────
function msgText(s) {
  // 목업의 한 줄은 "무슨 일 · 마지막 줄" 꼴이다. 무슨 일은 훅 이벤트명(last_event), 마지막 줄은 터미널 버퍼에서.
  const t = tiles.get(s.id);
  const parts = [];
  if (s.last_event) parts.push(EVENT_PHRASE[s.last_event] || s.last_event);   // #24: 이벤트명 → 사람 말
  if (t && t.lastLine) parts.push(t.lastLine);
  if (parts.length) return parts.join(' · ');
  // 훅이 없는 것을 조용히 회색으로 두지 않는다 (AGENTS.md 원칙 3) — 글자로 말한다
  if (s.status === 'unknown') return s.agent ? 'no hook event yet' : 'no hook seen — status unknown';
  return 'just opened';
}
// 목록 행의 확인 줄. 어느 행이 열려 있는지를 들고 있어야 다시 지어도 살아남는다.
function openRowConfirm(it, s) {
  const h = askClose(it, 'Close this terminal?', () => closeSession(s.id),
                     () => { if (rowConfirm === s.id) rowConfirm = null; });
  if (h) rowConfirm = s.id;
}
function buildItem(s, pinned) {
  const cls = STATUS_CLASS[s.status] || 'idle';
  const t = tiles.get(s.id);
  // pinned: 접힌 캔버스 묶음 안에서도 남아 있는 줄 — 기다리는 것은 절대 숨지 않는다(#31 ③, decisions.md ⑪)
  const it = el('div', 'ses ' + cls + (s.id === focused ? ' cur' : '') +
                      (pinned ? ' pinned' : '') + (closing.has(s.id) ? ' closing' : ''));
  it.dataset.id = s.id;
  if (pinned) it.title = 'waiting on you — kept visible while this group is collapsed';
  it.appendChild(el('span', 'dot ' + cls));
  // ⑫ 사람이 준 이름이 이긴다. 없으면 지금까지의 경로 이름표.
  const who = el('span', 'who');
  if (s.name) who.textContent = s.name;
  else { who.textContent = (s.agent || 'shell') + ' '; who.appendChild(el('span', null, shortPath(s.cwd))); }
  const ago = el('span', 'ago');
  // ⑪ 다른 캔버스의 것에는 캔버스 이름표가 붙는다 — "↗ off" 와 **같은 칸**이다. 둘이 같이 붙지는 않는다:
  // 다른 캔버스에 있는 창이 이 캔버스에서 화면 밖인지는 물음이 아니다.
  // **캔버스로 묶어 그리는 중이면 안 붙인다** — 바로 위 머리글이 이미 그 캔버스를 말한다.
  // 같은 말을 두 번 하면 머리글과 줄의 경계가 흐려진다(사용자 보고 2026-09-08).
  if (!inCanvasGroup && current !== null && s.canvas !== current) {
    const label = canvasLabel(canvasById(s.canvas));
    const b = el('span', 'cvb', label);
    b.title = 'in ' + label + ' — click to go there';
    ago.appendChild(b);
  } else if (t && t.off) ago.appendChild(el('span', 'off', '↗ off'));
  it._ago = document.createTextNode(agoText(s.id));
  it._agoEl = ago;              // 스크롤 중에 "↗ off" 하나만 뒤집으려고 붙들어 둔다(paintOff)
  ago.appendChild(it._ago);
  it._msg = el('span', 'msg', msgText(s));
  // #31 ④ 목록 행에서도 닫는다 — 사용자가 두 자리 다 짚었기 때문이다. 타일의 것과 **같은 상자·같은 물음**이고,
  // 평소엔 안 보이다가 행에 손이 닿거나(hover) 포커스가 들어오면 뜬다. <button> 이라 Tab 으로 닿는다.
  const cl = el('button', 'cl');
  cl.type = 'button';
  cl.title = 'close terminal';
  cl.setAttribute('aria-label', 'close terminal — ' + (s.name || shortPath(s.cwd)));
  cl.addEventListener('click', (ev) => { ev.stopPropagation(); openRowConfirm(it, s); });
  it.append(who, ago, it._msg, cl);
  // 목록은 세션 프레임마다 통째로 다시 지어진다 — 열려 있던 확인 줄을 여기서 되살린다(안 그러면 훅 하나에 사라진다)
  if (rowConfirm === s.id) openRowConfirm(it, s);
  it.addEventListener('click', () => goToSession(s.id));
  return it;
}
// "↗ off" 하나만 제자리에서 뒤집는다. 스크롤 경로에서 부르는 것이라 목록을 다시 짓지 않는다(refreshOff 참고).
// buildItem 과 같은 규칙을 쓴다: 다른 캔버스의 것에는 캔버스 이름표가 붙고 "↗ off" 는 안 붙는다(⑪).
function paintOff(id) {
  const it = items.get(id), t = tiles.get(id), s = sessions.get(id);
  if (!it || !t || !s) return;
  const want = t.off && !(current !== null && s.canvas !== current);
  const has = $('.off', it._agoEl);
  if (want && !has) it._agoEl.insertBefore(el('span', 'off', '↗ off'), it._ago);
  else if (!want && has) has.remove();
}
// 찾는 중에는 접힘을 무시한다 — 안 그러면 맞는 것이 접힌 그룹 안에 숨어 못 찾는다
function collapsed(key) { return !!groupsCollapsed[key] && !searchEl.value.trim(); }
function toggleGroup(key) {
  groupsCollapsed[key] = !groupsCollapsed[key];
  saveGroups();
  renderList();
}
function cvGroupOff(id) { return !!cvCollapsed[id] && !searchEl.value.trim(); }
function toggleCvGroup(id) {
  cvCollapsed[id] = !cvCollapsed[id];
  saveCvGroups();
  renderList();
}

// ── 목록의 묶음 (#31 ③) ────────────────────────────────
// **캔버스로 묶는다 — 그런데 기다리는 것은 절대 못 숨긴다.**
//
// 이 둘은 원래 서로 반대였다. 목업을 견줄 때 캔버스 우선 묶기를 반대한 근거가 정확히
// "접힌 묶음 안에 기다리는 것이 파묻힌다" 였고, **"기다리는 것이 어디 있든 맨 위" 는 palmar 가
// 존재하는 이유**다(decisions.md ⑪). 사용자가 매일 쓰면서 캔버스 묶기를 요구했으므로(#31) 묶되,
// 보장은 두 겹으로 지킨다:
//   1. **기다리는 세션이 있는 캔버스 묶음이 맨 위로 뜬다.** 나머지는 데몬이 준 캔버스 차례 그대로다
//      (Array.sort 는 안정 정렬이라 같은 편끼리는 차례가 안 흔들린다).
//   2. **접힌 묶음도 기다리는 줄은 그대로 그린다.** 접힘이 감추는 것은 나머지뿐이고, 감춘 개수는
//      묶음 아래 "+N more, collapsed" 한 줄로 말한다. 머리글의 수는 언제나 **캔버스 전체**다.
// 그래서 접어 두어도 기다리는 줄은 목록 맨 위 근처에 남는다 — 두 겹 다 없어야 파묻힌다.
// 줄은 한 세션에 하나다(위에 따로 복사해 두지 않는다) — 같은 것이 둘로 보이면 수가 거짓말을 한다.
let inCanvasGroup = false;    // 캔버스로 묶어 그리는 중인가 — 줄의 캔버스 이름표를 뺄지 정한다
function renderByCanvas() {
  const buckets = new Map();
  for (const id of canvasOrder) buckets.set(id, []);
  for (const s of sessions.values()) {
    const k = buckets.has(s.canvas) ? s.canvas : OTHER_KEY;
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k).push(s);
  }
  const keys = [...buckets.keys()].filter((k) => buckets.get(k).length);
  // **차례를 바꾸지 않는다.** 탭 줄과 같은 차례, 데몬이 준 차례 그대로다.
  // 전에는 나를 부르는 캔버스를 맨 위로 올렸다(⑪ 의 "기다리는 것은 못 놓친다"). 그 보장은 이제
  // 다른 넷이 지고 있다 — 탭의 점, 머리글의 점, 접힌 묶음에도 남는 기다리는 줄, 그리고 창이
  // 덮여 있을 때의 알림. 반면 값은 계속 나갔다: **손이 가 있는 목록이 눈앞에서 뛴다.**
  // 자리가 고정된 목록이 훑기 쉽고, 사용자가 그렇게 요구했다(2026-09-08).
  for (const k of keys) {
    const arr = buckets.get(k);
    arr.sort((a, b) => statusRank(a) - statusRank(b) || a.created - b.created);   // 묶음 안은 상태 차례
    const label = k === OTHER_KEY ? 'no canvas' : canvasLabel(canvasById(k));
    const off = cvGroupOff(k);
    const waiting = arr.filter((s) => s.status === 'waiting');
    const shown = off ? waiting : arr;
    const g = el('div', 'grp cvg' + (off ? ' collapsed' : '') + (k === current ? ' cur' : ''));
    g.dataset.canvas = k;
    const hiddenWant = off ? arr.filter((s) => WANTS_YOU.has(s.status) && !waiting.includes(s)).length : 0;
    g.title = (off ? 'expand ' : 'collapse ') + label +
              (waiting.length ? ' — ' + waiting.length + ' waiting stay visible either way' : '') +
              (hiddenWant ? ' — ' + hiddenWant + ' more want you, hidden by this fold' : '');
    g.append(el('span', 'car', off ? '▸' : '▾'), el('span', 'nm', label));
    // 머리글의 점은 탭의 점과 같은 뜻·같은 색이다(protocol.md "탭의 점"): 나를 부르는 것이 있다.
    // 새 색은 없다(⑥). 접혀 있을 때 이 점이 **접힌 묶음 안을 가리키는 유일한 표시**다.
    const wc = wantClass(arr);
    if (wc) g.appendChild(el('span', 'dot ' + wc));
    g.appendChild(el('span', 'ct', String(arr.length)));       // 접혀도 **캔버스 전체**의 수다
    g.addEventListener('click', () => toggleCvGroup(k));
    listEl.appendChild(g);
    for (const s of shown) {
      inCanvasGroup = true;
      const it = buildItem(s, off);
      inCanvasGroup = false;
      it.classList.add('cvrow');        // 캔버스 묶음에 딸린 줄 — 한 칸 들여쓴다
      items.set(s.id, it);
      listEl.appendChild(it);
    }
    if (arr.length > shown.length) {
      // 접힘이 감춘 것 중 **나를 부르는 것이 몇인지** 여기서 말한다. 접힌 묶음 안에서 done 은 줄로
      // 남지 않으므로(줄로 남기면 접힘이 쓸모없어진다) 이 수와 머리글의 점이 그 자리를 대신한다.
      const more = el('div', 'grest' + (hiddenWant ? ' wants' : ''),
                      '+' + (arr.length - shown.length) + ' more, collapsed'
                      + (hiddenWant ? ' · ' + hiddenWant + ' want' + (hiddenWant > 1 ? '' : 's') + ' you' : ''));
      more.title = 'expand ' + label;
      more.addEventListener('click', () => toggleCvGroup(k));
      listEl.appendChild(more);
    }
  }
}

// 캔버스를 하나도 못 받은 데몬(renderTabs 가 탭 줄을 내리는 그 경우)에서는 옛 길 그대로 상태로 묶는다.
// **여기서도 기다리는 것은 못 숨긴다** — 그 묶음만 접히지 않는다(아래).
function renderByStatus() {
  const by = { waiting: [], working: [], done: [], idle: [] };
  for (const s of sessions.values()) (by[s.status] || by.idle).push(s);
  for (const k in by) by[k].sort((a, b) => a.created - b.created);   // 목록의 자리는 안 움직인다 — 만든 순서
  for (const [key, cls, label] of GROUPS) {
    const arr = by[key];
    if (!arr.length) continue;
    // **기다리는 묶음은 접히지 않는다.** 캔버스 묶음에서는 접어도 기다리는 줄이 남지만, 여기서는
    // 묶음 전체가 기다리는 것이라 접는 순간 기다리는 것이 하나도 안 보인다 — decisions.md ⑪ 은
    // 조건 없이 "기다리는 것은 절대 못 숨긴다" 이고, 그 보장이 이 길에서만 빠져 있었다.
    // 캐럿을 아예 안 그린다: 눌러도 아무 일 없는 캐럿보다 없는 캐럿이 정직하다.
    // 계약상 캔버스가 0개인 순간은 없어(protocol.md: 마지막 하나는 409) 지금 데몬으로는 이 길에 닿지
    // 않지만, 캔버스를 모르는 데몬(갈아 끼울 Bun 판)이 그 보장까지 잃을 이유는 없다.
    const pin = key === 'waiting';
    // 접기·펴기는 오른쪽 트리와 같은 캐럿·같은 몸짓이다. 접혀도 **개수는 남는다**.
    const off = !pin && collapsed(key);
    const g = el('div', 'grp' + (off ? ' collapsed' : '') + (pin ? ' nofold' : ''));
    g.dataset.key = key;
    g.title = pin ? label + ' — always shown' : (off ? 'expand ' : 'collapse ') + label;
    g.append(el('span', 'car', pin ? '' : (off ? '▸' : '▾')), el('span', 'dot ' + cls), label,
             el('span', 'ct', String(arr.length)));
    if (!pin) g.addEventListener('click', () => toggleGroup(key));
    listEl.appendChild(g);
    if (off) continue;
    for (const s of arr) { const it = buildItem(s); items.set(s.id, it); listEl.appendChild(it); }
  }
}

function renderList() {
  // #31 ④: 확인 줄이 열린 채로 목록을 다시 지으면 **그 줄의 포커스가 body 로 떨어진다.**
  // buildItem 이 부르는 openRowConfirm → askClose 의 `yes.focus()` 는 그 행이 아직 document 에
  // 안 붙어 있어서 아무 일도 안 한다(detached 엘리먼트의 focus() 는 무시된다). 줄은 그대로 보이는데
  // 죽어 있게 된다 — 2026-09-08 통합 실측: 확인을 열어 둔 채 **다른 세션의 훅 하나**가 오면
  // 그 뒤로 Enter 가 Close 를 안 누르고(DELETE 0건), Escape 도 그 줄이 아니라 document 로 갔다.
  // 훅·resize 방송은 늘 오므로 키보드로 닫는 길(#31 ④ 가 <button> 을 쓴 이유)이 사실상 없어진다.
  // 그래서 다시 짓기 **전에** 그 줄이 포커스를 갖고 있었는지 재고, 다 붙인 **뒤에** 돌려준다.
  // 안 갖고 있었으면 건드리지 않는다 — 남이 쓰던 포커스(검색칸·터미널)를 뺏으면 안 된다.
  //
  // **어느 단추였는지도 같이 잰다.** 언제나 Close 로 돌려주면 사용자가 Cancel 에 둔 손이 방송 하나에
  // Close 로 옮겨 가고, 같은 Enter 가 그만두기에서 부수기로 바뀐다 — 2026-09-08 실측(f3.py):
  // Cancel 에 포커스를 두고 **다른 세션의 훅 하나**를 넣으니 `cbtn yes`/'Close' 로 옮겨 갔고
  // 그 자리의 Enter 가 `DELETE /api/sessions/<id>` 를 내 세션이 죽었다. 타일 제목줄은 다시 짓지
  // 않아 이런 일이 없다(같은 실측에서 'Cancel' 그대로였다) — 목록만 이 자리가 필요하다.
  const keepEl = (rowConfirm && document.activeElement && document.activeElement.closest &&
                  document.activeElement.closest('#list .cfm')) ? document.activeElement : null;
  const keepYes = keepEl ? keepEl.classList.contains('yes') : false;
  listEl.textContent = '';
  items.clear();
  if (canvasOrder.length) renderByCanvas(); else renderByStatus();
  // #31 ④: **이번 판에 그 줄이 안 그려졌으면 확인은 끝난 것이다.** 접힌 캔버스 묶음은 waiting 인 줄만
  // 그리므로(renderByCanvas), 훅 하나가 그 세션을 waiting 밖으로 밀면 줄이 조용히 빠진다. 여기서
  // 안 거두면 rowConfirm 이 남아, 그 세션이 **다시 waiting 이 되는 순간** buildItem 이 아무도 안 물은
  // 파괴 확인 줄을 되살린다(2026-09-08 실측 f1.py: 되살아난 Close 가 elementFromPoint 로 잡히는
  // 자리에 있었고, 하필 사용자가 답하러 누르러 가는 그 기다리는 줄이었다).
  // `document.contains` 로 가려 거두는 것이 중요하다 — 타일 쪽 확인은 목록을 다시 지어도 그대로 있다.
  if (rowConfirm && !items.has(rowConfirm)) {
    if (activeConfirm && !document.contains(activeConfirm.row)) activeConfirm.cancel();
    rowConfirm = null;
  }
  if (!sessions.size) {
    listEl.appendChild(el('div', 'empty', 'No terminals yet. Pick a folder on the right and press "Open terminal here".'));
  }
  $('#count').textContent = String(sessions.size);
  $('#sb-n').textContent = String(sessions.size);
  applyFilter();
  if (keepEl && rowConfirm) {
    const it = items.get(rowConfirm);
    const b = it && $(keepYes ? '.cfm .cbtn.yes' : '.cfm .cbtn:not(.yes)', it);
    if (b) b.focus();
  }
}
setInterval(() => { for (const [id, it] of items) it._ago.nodeValue = agoText(id); }, 10000);

// ── 검색 (⌘K): 세션 목록과 폴더 행을 글자로 거른다 ──
function applyFilter() {
  const q = searchEl.value.trim().toLowerCase();
  for (const [id, it] of items) {
    const s = sessions.get(id);
    const hay = ((s.name || '') + ' ' + (s.agent || 'shell') + ' ' + s.cwd + ' ' +
                 (canvasById(s.canvas) ? canvasLabel(canvasById(s.canvas)) : '') + ' ' +
                 it._msg.textContent).toLowerCase();
    it.classList.toggle('hide', !!q && !hay.includes(q));
  }
  for (const g of listEl.querySelectorAll('.grp')) {
    let n = g.nextElementSibling, any = false;
    while (n && n.classList.contains('ses')) { if (!n.classList.contains('hide')) any = true; n = n.nextElementSibling; }
    g.classList.toggle('hide', !!q && !any);
  }
  // 트리 쪽은 아래 findDirs 가 맡는다 — **펼친 행만 거르는 것은 검색이 아니었다.**
}

// 위 칸은 "sessions and folders" 를 찾는다고 말한다. 전에는 **이미 펼친 행만** 걸러 냈으므로
// 새로 연 화면에서는 폴더를 사실상 못 찾았다 — 화면이 지키지 않는 약속이었다.
// 이제 데몬에 묻는다(`GET /api/dirs?find=`). 경로를 그대로 붙여넣는 것도 같은 길로 답한다.
let findSeq = 0, findTimer = null;
function findDirs() {
  const q = searchEl.value.trim();
  clearTimeout(findTimer);
  if (!q) { findResults = null; renderTree(); return; }
  // 사람은 치는 중이다. 멎을 때까지 기다렸다 한 번만 묻는다.
  findTimer = setTimeout(async () => {
    const mine = ++findSeq;
    try {
      const r = await api('GET', '/api/dirs?find=' + encodeURIComponent(q));
      if (mine !== findSeq) return;            // 그 사이 더 쳤다 — 늦게 온 답은 버린다
      findResults = { q, entries: r.entries || [] };
    } catch (e) {
      if (mine !== findSeq) return;
      findResults = { q, entries: [], error: e.message };
    }
    renderTree();
  }, 180);
}
let hadQuery = false;
function onSearch() {
  const q = !!searchEl.value.trim();
  if (q !== hadQuery) { hadQuery = q; renderList(); }   // 접힘 무시가 켜지거나 꺼진다 — 목록을 다시 짠다
  else applyFilter();
  findDirs();
}
searchEl.addEventListener('input', onSearch);
addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); searchEl.focus(); searchEl.select(); }
  if (e.key === 'Escape' && e.target === searchEl) { searchEl.value = ''; onSearch(); searchEl.blur(); }
});

// ── 밖으로 나가는 신호 (#40) ──────────────────────────────
// 신호등은 palmar 를 보고 있을 때만 값이 있다. 에디터가 위에 떠 있으면 아무한테도 안 닿는데,
// 하필 그때가 신호등이 필요한 순간이다. 두 층으로 내보낸다:
//   탭 제목·파비콘 — 브라우저가 보일 때의 곁눈질. 공짜다.
//   알림 — 창이 덮였을 때의 끼어들기. 127.0.0.1 은 secure context 라 HTTPS 없이도 된다
//          (2026-09-08 실측: isSecureContext=true, Notification.permission='default').
const LS_NOTIFY = 'palmar.notify';
const NOTIFY_COALESCE_MS = 500;
//: "나를 부르는" 상태. **done 도 넣는다** — 훅이 없는 에이전트는 waiting 을 낼 수 없고(#38 은 제목으로
//: 읽으니 working/done/idle 만 나온다), 회사에서 쓰는 codex 가 정확히 그 경우다(#15).
const WANTS_YOU = new Set(['waiting', 'done']);

let notifyOn = false;
try { notifyOn = localStorage.getItem(LS_NOTIFY) === '1'; } catch (e) {}

function labelOf(s) { return s ? (s.name || shortPath(s.cwd)) : '?'; }

// 이 무리 중 가장 급한 "나를 부름" 의 상태 클래스. 없으면 null.
// **waiting 만 보면 안 된다** — 제목으로 읽는 에이전트(#38)는 waiting 을 낼 수 없어 done 으로 온다.
// 그것만 보던 탓에 codex 가 일을 끝내도 탭과 접힌 묶음이 깜깜했다(사용자 보고 2026-09-08).
function wantClass(list) {
  let d = null;
  for (const s of list) {
    if (s.status === 'waiting') return 'wait';   // 막혀 있는 쪽이 늘 이긴다
    if (s.status === 'done') d = 'done';
  }
  return d;
}
function wantsYouIds() {
  const out = [];
  for (const s of sessions.values()) if (WANTS_YOU.has(s.status)) out.push(s.id);
  return out;
}

// 탭 제목과 파비콘. **색은 CSS 의 --st-* 를 읽어 쓴다** — JS 에 색을 새로 두지 않는다(AGENTS.md).
const favEl = document.querySelector('link[rel="icon"]');
let badgeKey = null;
function renderBadge(force) {
  const n = wantsYouIds().length;
  const key = n + '|' + (document.documentElement.dataset.theme || 'system');
  if (!force && key === badgeKey) return;      // 세션 프레임마다 캔버스를 다시 그리지 않는다
  badgeKey = key;
  document.title = n ? '(' + n + ') palmar' : 'palmar';
  document.body.classList.toggle('wants', n > 0);     // 워드마크의 마지막 점을 켠다
  if (!favEl) return;
  const css = getComputedStyle(document.documentElement);
  // 파비콘도 브랜드의 `p` 다(docs/brand/p.svg) — 속의 점이 앰버면 누가 기다린다는 뜻이고,
  // 그건 워드마크·탭의 점과 **같은 규칙, 같은 색**이다. 몇 개인지는 말하지 않는다: 16px 에서
  // 숫자는 못 읽고, 게이지가 아니라 부름이다.
  // SVG 파비콘을 안 쓰는 이유: 브라우저마다 지원이 갈리고, 격리돼서 CSS 변수도 못 읽는다.
  // 그래서 같은 경로를 Path2D 에 넣어 캔버스로 굽는다 — PNG 는 어디서나 뜬다.
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d');
  if (!g) return;
  const ink = (css.getPropertyValue('--ink') || '').trim() || '#222';
  // p.svg 의 viewBox 는 "2 46 60 94". 높이로 맞추고 가로는 가운데로.
  const k = 64 * 0.88 / 94;
  g.translate((64 - 60 * k) / 2 - 2 * k, (64 - 94 * k) / 2 - 46 * k);
  g.scale(k, k);
  g.strokeStyle = ink; g.lineWidth = 11; g.lineCap = 'round'; g.lineJoin = 'round';
  try {
    g.stroke(new Path2D('M 13.50 57.50 V 128.50'));
    g.stroke(new Path2D('M 13.50 76.00 a 18.50 18.50 0 1 0 37.00 0 a 18.50 18.50 0 1 0 -37.00 0'));
  } catch (e) { return; }                            // Path2D 가 없으면 파비콘을 건드리지 않는다
  g.beginPath(); g.arc(33, 76, 6, 0, Math.PI * 2);
  if (n) { g.fillStyle = (css.getPropertyValue('--st-wait') || '').trim() || '#d99a2b'; g.globalAlpha = 1; }
  else   { g.fillStyle = ink; g.globalAlpha = 0.34; }
  g.fill();
  try { favEl.href = c.toDataURL('image/png'); } catch (e) {}
}

const notifyQueue = new Set();
let notifyTimer = null;
let notifyAt = 0;              // 마지막으로 실제로 울린 시각

// **상태로 바뀌는 순간에만** 부른다. 상태가 이어지는 동안 다시 울리면 사람들은 알림을 통째로 끈다.
function onWantsYou(id) {
  if (!notifyOn || !('Notification' in window) || Notification.permission !== 'granted') return;
  // palmar 를 보고 있으면 신호등으로 충분하다. hasFocus 는 "다른 창이 위에 있다" 와 "다른 탭이다" 를
  // 둘 다 잡는다 — visibilityState 는 창이 덮여도 'visible' 이라 여기서는 쓸 수 없다.
  if (document.hasFocus()) return;
  notifyQueue.add(id);
  // **첫 번째는 바로 울린다.** 모아서 보내려고 타이머에 맡겼더니 정작 창이 내려가 있을 때 —
  // 알림이 가장 필요한 그때 — 늦었다: 숨은 탭의 setTimeout 은 크롬이 1초, 오래 숨어 있으면 1분까지
  // 미룬다. 뒤이어 오는 것들만 타이머로 묶는다(같은 tag 라 앞의 알림을 갈아 끼운다).
  if (Date.now() - notifyAt > NOTIFY_COALESCE_MS) { flushNotify(); return; }
  if (notifyTimer === null) notifyTimer = setTimeout(flushNotify, NOTIFY_COALESCE_MS);
}

function flushNotify() {
  if (notifyTimer !== null) { clearTimeout(notifyTimer); }
  notifyTimer = null;
  const ids = [...notifyQueue].filter((id) => {
    const s = sessions.get(id);
    return s && WANTS_YOU.has(s.status);      // 그 사이 스스로 풀렸으면 안 울린다
  });
  notifyQueue.clear();
  if (!ids.length || document.hasFocus()) return;
  const one = ids.length === 1 ? sessions.get(ids[0]) : null;
  let n;
  try {
    n = new Notification(
      one ? labelOf(one) + ' wants you' : ids.length + ' terminals want you',
      { body: one ? one.cwd : ids.map((i) => labelOf(sessions.get(i))).join(', '),
        tag: 'palmar-wants-you' });          // 같은 tag 라 쌓이지 않고 갈아 끼워진다
  } catch (e) { return; }
  notifyAt = Date.now();
  // 눌렀는데 그 터미널로 안 가면 "가서 찾아봐" 라고 말하는 셈이라 원래 문제를 그대로 둔다.
  n.onclick = () => { window.focus(); goToSession(ids[0]); n.close(); };
}

// 켤 때 **한 번 울려 본다.** 알림은 브라우저 권한·OS 방해금지·집중 지원까지 여러 단계를 지나야
// 도착하고, 그중 어디서 막혀도 화면에서는 똑같이 조용하다. 한 번 보내 보면 그 사슬 전체가 한
// 번에 확인된다 — "켰는데 안 오네" 를 나중에 알아채는 것보다 지금 아는 편이 낫다.
function notifyTest() {
  try {
    const n = new Notification('palmar notifications are on', {
      body: 'This is the only one you did not ask for. From now on it speaks when a terminal wants you.',
      tag: 'palmar-test' });
    n.onclick = () => { window.focus(); n.close(); };
  } catch (e) {
    toast(['notifications were allowed, but the browser refused to show one', String(e.message || e)]);
  }
}

const bellEl = document.getElementById('bell');
function setNotify(on) {
  notifyOn = on;
  try { if (on) localStorage.setItem(LS_NOTIFY, '1'); else localStorage.removeItem(LS_NOTIFY); } catch (e) {}
  if (bellEl) {
    bellEl.dataset.on = on ? '1' : '0';
    bellEl.title = on ? 'notifications on — click to turn off'
                      : 'notify me when a terminal wants me (off)';
  }
}
async function toggleNotify() {
  if (notifyOn) { setNotify(false); return; }
  if (!('Notification' in window)) { toast(['this browser has no Notification API']); return; }
  let perm = Notification.permission;
  // 켜는 손짓이 있을 때만 묻는다 — 뜨자마자 권한을 묻는 것은 모두가 싫어하는 짓이고,
  // 브라우저도 손짓을 요구한다. 권한은 **포트까지 포함한 origin** 별이라 --port 를 바꾸면 다시 묻는다.
  if (perm === 'default') { try { perm = await Notification.requestPermission(); } catch (e) { perm = 'denied'; } }
  if (perm !== 'granted') {
    toast(['notifications are blocked for ' + location.origin,
           perm === 'denied' ? 'the browser is refusing — allow them for this site in its settings'
                             : 'allow them in the browser, then try again']);
    return;
  }
  setNotify(true);
  notifyTest();
}
if (bellEl) {
  bellEl.addEventListener('click', toggleNotify);
  bellEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleNotify(); }
  });
}

// ── 프로토콜 판 ─────────────────────────────────────────
// 이 페이지가 아는 판. 데몬의 palmar/__init__.py PROTOCOL 과 짝이다.
// **한 곳에서 클론해 쓰는 동안은 어긋날 수가 없다** — 데몬과 페이지가 같은 커밋이니까.
// 배포되기 시작하면 달라진다: 브라우저가 캐시한 새 페이지가 안 올린 데몬을 만난다.
// 그때 조용히 이상하게 구는 대신 **말한다**. 막지는 않는다 — 대개는 그래도 돌아가고,
// 막아 버리면 고칠 방법(새로고침·데몬 재시작)까지 같이 막힌다.
const PROTOCOL = 1;
let protocolWarned = false;
function checkProtocol(m) {
  const v = m.v;
  if (v === undefined || v === PROTOCOL || protocolWarned) return;
  protocolWarned = true;
  const older = v < PROTOCOL;
  toast([
    'this page speaks protocol ' + PROTOCOL + ', the daemon speaks ' + (v === null ? '?' : v),
    older ? 'the daemon is older — restart it after pulling'
          : 'this page is older — reload with a hard refresh',
    m.daemon ? 'daemon ' + m.daemon : '',
  ].filter(Boolean));
}

// ── 있었던 일 ─────────────────────────────────────────────
// 신호등은 **지금**을 말한다. 자리를 비운 사이는 아무 데도 안 남아 있었다 — 무엇이 끝났고
// 무엇이 물어봤는지, 어떤 차례로. 데몬이 그것을 갖고(브라우저를 닫아 둔 동안이야말로 "없는 동안"
// 이니까) 붙을 때 hello 로 함께 준다. 여기서는 보여 주고, 눌러서 그리로 가는 일만 한다.
const LS_SEEN_AT = 'palmar.seenAt';
const ACT_MAX = 60;                 // 화면에 두는 개수. 데몬은 더 갖고 있다.
const ACT_CLASS = { waiting: 'wait', done: 'done', created: 'idle', gone: 'idle' };
let acts = [];
let seenAt = 0;
try { seenAt = Number(localStorage.getItem(LS_SEEN_AT)) || 0; } catch (e) {}
const actsEl = document.getElementById('acts');
const actNewEl = document.getElementById('act-new');

// **본 것으로 치는 때**: 이 창이 앞에 있을 때. 덮여 있는 동안 쌓인 것이 곧 "없는 동안" 이다.
function markSeen() {
  if (!document.hasFocus()) return;
  seenAt = Date.now() / 1000;
  try { localStorage.setItem(LS_SEEN_AT, String(seenAt)); } catch (e) {}
}

function renderActs() {
  if (!actsEl) return;
  actsEl.textContent = '';
  const fresh = acts.filter((e) => e.t > seenAt).length;
  if (actNewEl) actNewEl.textContent = fresh ? fresh + ' new' : '';
  if (!acts.length) {
    actsEl.appendChild(el('div', 'hint2', 'nothing yet — what finishes or asks for you shows up here'));
    return;
  }
  for (const e of acts.slice().reverse().slice(0, ACT_MAX)) {
    const alive = sessions.has(e.id);
    const row = el('div', 'act' + (e.t > seenAt ? ' fresh' : '') + (alive ? '' : ' dead'));
    row.appendChild(el('span', 'dot ' + (ACT_CLASS[e.kind] || 'idle')));
    const mid = el('span', 'nm');
    mid.appendChild(el('b', null, e.name || shortPath(e.cwd)));
    mid.appendChild(document.createTextNode(' '));
    mid.appendChild(el('span', 'wt', e.what));
    row.appendChild(mid);
    row.appendChild(el('span', 'ago', agoShort(e.t)));
    row.title = (e.name || e.cwd) + ' · ' + e.what + (alive ? '' : ' · this terminal is gone');
    if (alive) row.addEventListener('click', () => goToSession(e.id));
    actsEl.appendChild(row);
  }
}

function agoShort(t) {
  const d = Math.max(0, Date.now() / 1000 - t);
  if (d < 45) return 'now';
  if (d < 3600) return Math.round(d / 60) + 'm';
  if (d < 86400) return Math.round(d / 3600) + 'h';
  return Math.round(d / 86400) + 'd';
}

function pushAct(e) {
  acts.push(e);
  if (acts.length > ACT_MAX * 2) acts = acts.slice(-ACT_MAX);
  renderActs();
}

// ── 세션 반영 ───────────────────────────────────────────
function upsert(s) {
  const old = sessions.get(s.id);
  if (!old) changedAt.set(s.id, (s.created || Date.now() / 1000) * 1000);
  // 전이할 때만이다. WANTS_YOU 안에서 waiting ↔ done 으로 옮겨 다니는 것은 새 부름이 아니다.
  let wants = false;
  if (old && old.status !== s.status) {
    changedAt.set(s.id, Date.now());
    wants = WANTS_YOU.has(s.status) && !WANTS_YOU.has(old.status);
  }
  sessions.set(s.id, s);
  // **넣은 다음에 부른다.** 알림은 sessions 에서 다시 읽어 이름과 경로를 만드는데, 먼저 부르면
  // 그때 거기 있는 것은 아직 옛 세션이라 "부르는 것이 없다" 로 걸러진다. 500ms 타이머로 미룰
  // 때는 그 사이에 넣어져서 안 보였고, 즉시 울리게 바꾸자마자 드러났다.
  if (wants) onWantsYou(s.id);
  renderBadge();
  let t = tiles.get(s.id);
  if (!t) {
    t = new Tile(s);
    tiles.set(s.id, t);
    t.off = false;
    refreshOff();    // 남의 브라우저가 연 창은 화면 밖에 놓일 수 있다 — 뷰포트는 저기서 한 번만 잰다
  } else {
    const wasVisible = !t.el.classList.contains('other');
    t.update(s);
    const on = t.visible();
    if (on !== wasVisible) {          // ⑪ 세션이 캔버스를 옮겼다 — session 프레임 하나로 온다
      t.el.classList.toggle('other', !on);
      if (on) t.refit(); else { t.off = false; syncMax(); }   // 펼친 창이 남의 캔버스로 갔다
    }
  }
  renderTabs();      // 점은 계산이다 — status 나 canvas 가 바뀌면 다시 센다
  renderList();
  renderMinimap();
  updateStatusBar();
  return t;
}
function remove(id) {
  const t = tiles.get(id);
  const wasIn = t && t.s ? t.s.canvas : null;   // 정리는 그 캔버스에만 한다
  if (t) { if (maxed === t) setMax(t, false); t.dispose(); tiles.delete(id); }
  sessions.delete(id);
  notifyQueue.delete(id);
  renderBadge();
  changedAt.delete(id);
  closing.delete(id);                        // #31 ①: gone 이 왔다 — 여기가 진짜로 지우는 유일한 자리다
  // 이 세션에 열려 있던 확인 줄은 거둔다. 그냥 두고 목록을 다시 지으면 그 줄은 DOM 에서만 떨어지고
  // document 에 걸어 둔 pointerdown 리스너가 남는다(askClose 의 outside).
  if (rowConfirm === id && activeConfirm) activeConfirm.cancel();
  rowConfirm = rowConfirm === id ? null : rowConfirm;
  if (activeConfirm && !document.contains(activeConfirm.row)) activeConfirm.cancel();
  delete layout[id];   // id 는 다시 쓰이지 않는다 — 남기면 쌓인다
  saveLayout();
  if (focused === id) focused = null;
  // 켜 뒀으면 자동으로 거둔다. 아니면 **단추만 켜서** 거둘 것이 생겼다고 말한다 —
  // 창을 움직이는 일이라 시키지 않았으면 안 움직인다.
  if (wasIn && autoTidy) tidyCanvas(wasIn);
  paintTidy();
  // 있었던 일의 줄은 "그 세션이 아직 있나" 를 보여 준다. `log` 가 `gone` 보다 먼저 오므로
  // (계약: note 가 broadcast 전이다) 그때 그린 줄은 아직 살아 있는 것으로 그려진다 — 여기서 고친다.
  renderActs();
  renderTabs();
  renderList();
  renderMinimap();
  updateStatusBar();
}
function reconcile(list) {
  const seen = new Set(list.map((s) => s.id));
  for (const id of [...sessions.keys()]) if (!seen.has(id)) remove(id);
  // 자리를 기억하는 것 먼저 놓아야 새 것이 그 자리를 차지하지 않는다
  const ordered = [...list].sort((a, b) => (layout[a.id] ? 0 : 1) - (layout[b.id] ? 0 : 1) || a.created - b.created);
  for (const s of ordered) upsert(s);
  refreshOff();
}

// ── 제어 채널 /events ───────────────────────────────────
function connectEvents() {
  const ws = new WebSocket(`ws://${location.host}/events?token=${encodeURIComponent(TOKEN)}`);
  eventsWs = ws;
  let opened = false;
  ws.onopen = () => { opened = true; eventsRetry = 0; setConnected(true); };
  ws.onmessage = (ev) => {
    let m = null;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (!m) return;
    // hello 한 프레임 안에서 모든 session.canvas 가 이 canvases 안에 있다(protocol.md) — 캔버스를 먼저 넣는다
    if (m.t === 'hello') {
      checkProtocol(m); setCanvases(m.canvases || []); reconcile(m.sessions || []);
      acts = m.log || []; renderActs();
    }
    else if (m.t === 'log' && m.e) pushAct(m.e);
    else if (m.t === 'session' && m.s) upsert(m.s);
    else if (m.t === 'gone' && m.id) remove(m.id);
    else if (m.t === 'canvas' && m.c) putCanvas(m.c);           // 생겼거나 이름이 바뀌었다
    else if (m.t === 'canvases' && m.cs) setCanvases(m.cs);     // 순서가 바뀌었다 — order 순 전체
    else if (m.t === 'canvas_gone' && m.id) dropCanvas(m.id);   // 바로 뒤에 canvases 가 따라온다
  };
  ws.onclose = () => {
    if (eventsWs !== ws) return;
    eventsWs = null;
    setConnected(false);
    eventsRetry = Math.min(10000, eventsRetry ? eventsRetry * 2 : 1000);
    // 열리지도 못하고 닫혔다 = 데몬이 없거나(연결 거부) 핸드셰이크에서 거절됐다(403). 뒤쪽은 데몬이 다시 떠
    // 토큰이 바뀐 경우다 — 토큰은 index.html 로만 오니(protocol.md "뜨기" 4) 그걸 다시 받아 비교한다.
    if (!opened) checkStaleToken();
    setTimeout(connectEvents, eventsRetry);
  };
  ws.onerror = () => {};
}
// 데몬이 재시작하면(⑦=b: 업데이트 때만) 토큰이 새로 나고 이 페이지의 것은 영원히 403 이다. index.html 을 다시 받아
// 심긴 토큰이 우리 것과 다를 때만 새로 고친다 — 같으면(데몬이 아직 없다) 그냥 재시도가 이어진다. 되돌이표는 없다:
// 새로 고친 뒤에는 토큰이 같다.
let staleCheck = false;
async function checkStaleToken() {
  if (staleCheck) return;
  staleCheck = true;
  try {
    const r = await fetch('/', { cache: 'no-store' });
    if (!r.ok) return;
    const m = /window\.PALMAR_TOKEN="([^"]*)"/.exec(await r.text());
    if (m && m[1] && m[1] !== TOKEN) {
      toast([{ b: 'daemon restarted' }, ' — reloading']);
      setTimeout(() => location.reload(), 600);
    }
  } catch (e) {
    // 데몬이 없다 — 재시도가 이어진다
  } finally {
    staleCheck = false;
  }
}
function setConnected(on) {
  $('#sb-daemon').classList.toggle('down', !on);
  updateStatusBar(on ? null : 'reconnecting…');
}
function updateStatusBar(note) {
  let gl = 0, dom = 0;
  for (const t of tiles.values()) (t.gl ? gl++ : dom++);
  const parts = [];
  if (note) parts.push(note);
  if (tiles.size) parts.push('webgl ' + gl + ' · dom ' + dom);
  $('#sb-right').textContent = parts.join(' · ');
}
$('#sb-host').textContent = location.host;

// ── 오른쪽 레일: 디렉터리 (폴더만, 펼칠 때만 읽는다) ──────
const tree = { roots: [] };   // node: { path, name, branch, hasChildren, depth, expanded, children, loading }
let selectedDir = null;

function joinDir(parent, name) {
  // 뿌리 목록의 name 이 절대 경로인지, path 에 붙이는 이름인지 protocol.md 가 못 박지 않았다 — 둘 다 받는다
  if (!name) return parent || '';
  if (name.startsWith('/')) return name;
  if (!parent) return name;
  return parent.replace(/\/$/, '') + '/' + name;
}
function makeNode(parentPath, e, depth) {
  return { path: joinDir(parentPath, e.name), name: e.name, branch: e.git_branch || null,
           hasChildren: !!e.has_children, depth, expanded: false, children: null, loading: false };
}
async function loadRoots() {
  try {
    const d = await api('GET', '/api/dirs');
    tree.roots = (d.entries || []).map((e) => makeNode(d.path || '', e, 0));
    home = tree.roots.length ? tree.roots[0].path : null;
    if (!selectedDir && tree.roots.length) selectDir(tree.roots[0]);
    renderTree();
    renderList();        // 경로 표시가 ~ 로 줄어든다
    for (const t of tiles.values()) t.update(t.s);
  } catch (e) {
    toast(['directories: ' + e.message]);
  }
}
async function expandNode(n) {
  n.loading = true; renderTree();
  try {
    const d = await api('GET', '/api/dirs?path=' + encodeURIComponent(n.path));
    n.children = (d.entries || []).map((e) => makeNode(d.path || n.path, e, n.depth + 1));
    n.expanded = true;
  } catch (e) {
    toast([n.name + ': ' + e.message]);
  } finally {
    n.loading = false; renderTree();
  }
}
function collapseNode(n) { n.expanded = false; renderTree(); }
function selectDir(n) {
  selectedDir = n;
  launchPath.textContent = '';
  launchPath.append(el('b', null, shortPath(n.path)));
  if (n.branch) launchPath.append(' · ' + n.branch);
  launchBtn.disabled = false;
  renderTree();
}
let findResults = null;      // { q, entries } — 검색 중일 때만. null 이면 평소의 트리다.

function renderTree() {
  treeEl.textContent = '';
  const hint0 = document.getElementById('tree-hint');
  if (hint0 && !findResults) hint0.textContent = 'folders only · read when expanded';
  if (findResults) {
    // **찾은 것을 평평하게 보여 준다.** 트리 속으로 펼쳐 들어가면 어디를 보고 있는지 잃는다.
    const hint = document.getElementById('tree-hint');
    if (hint) hint.textContent = 'matching folders · click one to open a terminal there';
    if (!findResults.entries.length) {
      treeEl.appendChild(el('div', 'hint2', findResults.error
        ? 'search failed: ' + findResults.error
        : 'no folder matches ' + JSON.stringify(findResults.q)));
      return;
    }
    for (const e of findResults.entries) {
      // selectDir 이 기대하는 모양 그대로 만든다 (branch — git 가 아니다)
      const n = { path: e.name, name: e.name, depth: 0, hasChildren: !!e.has_children, branch: e.git_branch };
      const r = el('div', 'row found' + (selectedDir && selectedDir.path === e.name ? ' sel' : ''));
      r.dataset.path = e.name;
      r.tabIndex = 0;
      r.appendChild(el('span', 'nm', shortPath(e.name)));
      if (e.git_branch) r.appendChild(el('span', 'br', e.git_branch));
      r.title = e.name;
      // 찾은 것을 고르면 그것이 곧 cwd 다 — 트리를 파고들 필요가 없다.
      const pick = () => selectDir(n);   // 트리에서 고르는 것과 **같은 길**이다
      r.addEventListener('click', pick);
      r.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); pick(); launch(); } });
      treeEl.appendChild(r);
    }
    return;
  }
  const walk = (n, i) => {
    const r = el('div', 'row' + (n.depth === 0 ? ' root' : '') + (n === selectedDir ? ' sel' : '') +
                        (n.depth === 0 && n.path !== home ? ' dim' : '') + (n.loading ? ' loading' : ''));
    r.dataset.path = n.path;
    r.tabIndex = 0;
    r.style.paddingLeft = (8 + n.depth * 16) + 'px';
    if (n.depth === 0 && i > 0) r.style.marginTop = '8px';
    const car = el('span', 'car' + (n.hasChildren ? '' : ' none'), n.expanded ? '▾' : '▸');
    car.addEventListener('click', (ev) => { ev.stopPropagation(); if (n.expanded) collapseNode(n); else if (n.hasChildren) expandNode(n); });
    r.appendChild(car);
    r.appendChild(el('span', 'nm', n.depth === 0 ? (n.path === home ? '~' : n.path) : n.name));
    if (n.branch) r.appendChild(el('span', 'br', n.branch));
    r.addEventListener('click', () => selectDir(n));
    r.addEventListener('dblclick', () => { if (n.expanded) collapseNode(n); else if (n.hasChildren) expandNode(n); });
    r.addEventListener('keydown', (ev) => {
      if (ev.key === 'ArrowRight' && n.hasChildren && !n.expanded) { ev.preventDefault(); expandNode(n); }
      else if (ev.key === 'ArrowLeft' && n.expanded) { ev.preventDefault(); collapseNode(n); }
    });
    treeEl.appendChild(r);
    if (n.expanded && n.children) n.children.forEach((c, j) => walk(c, j));
  };
  tree.roots.forEach(walk);
  applyFilter();
}
$('#refresh').addEventListener('click', () => {
  // 펼친 것 하나만 다시 읽는다 — 홈 전체를 훑지 않는다. 고른 폴더가 펼쳐져 있으면 그것, 아니면 뿌리 목록
  if (selectedDir && (selectedDir.expanded || selectedDir.hasChildren)) expandNode(selectedDir);
  else loadRoots();
});
$('#mkdir').addEventListener('click', async () => {
  if (!selectedDir) { toast(['select a folder first']); return; }
  const name = prompt('New folder in ' + shortPath(selectedDir.path));
  if (!name || !name.trim()) return;
  try {
    await api('POST', '/api/dirs', { path: selectedDir.path, name: name.trim() });
    selectedDir.hasChildren = true;
    await expandNode(selectedDir);
    toast([{ b: 'Created ' + name.trim() }, ' in ' + shortPath(selectedDir.path)]);
  } catch (e) {
    toast(['new folder: ' + e.message]);
  }
});
async function launch() {
  if (!selectedDir || launchBtn.disabled) return;
  launchBtn.disabled = true;
  try {
    // PROVISIONAL — ⑪ 의 열린 질문("지금 캔버스인가 그 폴더의 캔버스인가")은 **이 한 칸을 무엇으로
    // 채우느냐** 이지 프로토콜이 아니다(protocol.md). 지금은 보고 있는 캔버스로 둔다.
    const body = { cwd: selectedDir.path };
    if (current) body.canvas = current;
    const s = await api('POST', '/api/sessions', body);
    // /events 의 session 프레임이 먼저 올 수도, 이 응답이 먼저 올 수도 있다 — upsert 는 둘 다 받는다
    const t = upsert(s);
    t.el.classList.add('fresh');
    setTimeout(() => t.el.classList.remove('fresh'), 2500);
    t.el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    focusTile(s.id, { user: true });
    refreshOff();
    toast([{ b: 'Opened shell' }, ' in ' + shortPath(s.cwd)]);
  } catch (e) {
    toast(['open terminal: ' + e.message]);
  } finally {
    launchBtn.disabled = !selectedDir;
  }
}
launchBtn.addEventListener('click', launch);
addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target && e.target.closest && e.target.closest('.rail.right')) { e.preventDefault(); launch(); }
});

// 콘솔·개발 도구에서 들여다보는 손잡이. 제품 동작은 이것에 기대지 않는다.
window.palmar = { sessions, tiles, canvases, layout: () => layout,
                  canvas: () => current, groups: () => groupsCollapsed,
                  cvGroups: () => cvCollapsed, closing: () => [...closing],
                  // 화면에서는 지울 수 있을 때만 손잡이가 나오므로, 거절당하는 길(#18 의 409)은
                  // 콘솔에서만 태워 볼 수 있다. 데몬이 어차피 막으므로 여기 두는 것이 위험을 늘리지 않는다.
                  removeCanvas };

// ── 시작 ────────────────────────────────────────────────
function boot() {
  if (!window.Terminal || !window.FitAddon) {
    toast(['xterm.js is missing under palmar/web/vendor/ — see palmar/web/vendor/VERSIONS']);
    return;
  }
  // 단축키 안내는 이 기계의 글쇠를 말해야 한다. 처리 쪽은 진작 metaKey 와 ctrlKey 를 둘 다 받고
  // 있었는데(아래 keydown) 안내만 ⌘ 로 박혀 있어서, 리눅스·WSL 에서는 없는 글쇠를 가리켰다.
  // HTML 의 기본값은 Ctrl 이다 — 맥이 아닌 곳이 더 넓고, 못 알아보면 안 바꾸는 편이 안전하다.
  if (IS_MAC) {
    const k = document.getElementById('kmod');
    if (k) k.firstElementChild.textContent = '⌘';
    // 맥에서는 복사·붙여넣기·글자 크기가 ⌘ 하나다 — Shift 없이. 안내도 그 기계의 글쇠를 말한다.
    for (const el of document.querySelectorAll('.keys kbd.mod')) el.textContent = '⌘';
    for (const el of document.querySelectorAll('.keys dt')) {
      const ks = [...el.querySelectorAll('kbd')];
      if (ks.length === 3 && ks[1].textContent === 'Shift') ks[1].remove();
    }
  }
  // ── 단축키 판 ──
  const helpBtn = document.getElementById('help'), keysEl = document.getElementById('keys');
  const showKeys = (on) => {
    keysEl.hidden = !on;
    helpBtn.setAttribute('aria-expanded', on ? 'true' : 'false');
  };
  if (helpBtn && keysEl) {
    helpBtn.addEventListener('click', (e) => { e.stopPropagation(); showKeys(keysEl.hidden); });
    document.getElementById('keys-x').addEventListener('click', () => showKeys(false));
    // 판 밖을 누르거나 Esc 로 닫는다. 판 안의 클릭은 삼킨다.
    keysEl.addEventListener('click', (e) => e.stopPropagation());
    addEventListener('click', () => { if (!keysEl.hidden) showKeys(false); });
    addEventListener('keydown', (e) => { if (e.key === 'Escape' && !keysEl.hidden) showKeys(false); });
    const sw = document.getElementById('autotidy');
    if (sw) {
      sw.checked = autoTidy;
      sw.addEventListener('change', () => {
        autoTidy = sw.checked;
        try { if (autoTidy) localStorage.setItem(LS_AUTOTIDY, '1'); else localStorage.removeItem(LS_AUTOTIDY); } catch (e) {}
      });
    }
  }
  const tidyBtn = document.getElementById('tidy');
  if (tidyBtn) tidyBtn.addEventListener('click', () => tidyCanvas(current));
  paintTidy();
  window.palmar.tidyCanvas = tidyCanvas;
  loadRails();
  rzGrip(document.getElementById('rz-l'), 'l');
  rzGrip(document.getElementById('rz-r'), 'r');
  // 창이 앞으로 돌아오면 **잠깐 새것으로 보여 준 뒤** 본 것으로 넘긴다. 돌아오자마자 지워 버리면
  // 없는 동안 무슨 일이 있었는지 볼 새가 없다.
  addEventListener('focus', () => setTimeout(() => { markSeen(); renderActs(); }, 4000));
  // 시각은 흐른다 — 'now' 가 '3m' 이 되는 것을 보이게 한다. 목록의 ago 와 같은 주기다.
  setInterval(renderActs, 30000);
  setNotify(notifyOn && 'Notification' in window && Notification.permission === 'granted');
  applyTheme(storedTheme());
  renderBadge(true);
  renderTabs();        // 캔버스가 오기 전에는 탭 줄이 내려가 있다 — hello 가 오면 그때 뜬다
  connectEvents();
  loadRoots();
}
// 글꼴이 도착하기 전에 xterm 이 셀을 재면 열 수가 어긋난다. 잠깐(≤1.5s) 기다리고, 안 오면 뒷 글꼴로 간다.
const fontWait = document.fonts && document.fonts.load
  ? Promise.race([document.fonts.load(FONT_PX + 'px "JetBrains Mono"').catch(() => null), new Promise((r) => setTimeout(r, 1500))])
  : Promise.resolve();
fontWait.then(boot, boot);
})();
