// palmar browser — the canvas UI. docs/protocol.md is the one contract with the daemon; trust only what it says.
//
// ① Nobody has picked the web stack yet. Vanilla JS here is not a decision, it is the default before one
//    (decisions.md ①: "기본값은 안 쓰는 것이고, 쓰자고 하려면 이유를 대야 한다"). No build step either.
//    Picking a framework rewrites this file — so state (sessions·tiles) and the DOM are tied as thinly as we can.
//
// Left provisional (places where a decision that belongs to a person is not pre-empted — only what it takes to run):
//   ③ position·size·z order live in localStorage 'palmar-tiles', keyed by session id. Exactly the provisional
//      that protocol.md's "위치·크기" section describes. The daemon knows only the pane's cols·rows.
//   ⑥ the status string only becomes a CSS class (wait/work/done/idle). Colors live only in style.css's --st-*.
//      idle and unknown share the same grey slot — once the mapping is decided, only STATUS_CLASS changes.
//   ⑩ where a new window goes: scan the grid for the first free slot, else the bottom. No push-aside (#23) yet
//      and no overlap setting. The default size DEFAULT_W/H is an undecided that hangs off ⑩ too.
//   ⑪⑫ the canvas list·order·names and a session's canvas·name **belong to the daemon** (protocol.md "캔버스").
//      This file holds a copy and swaps it out on hello. Three things are the browser's alone — the tab in view,
//      the fold of list groups (localStorage 'palmar-groups' · 'palmar-canvas-groups'), the minimap. The daemon
//      knows none of the three (protocol.md "없는 것"). Two PROVISIONALs: what a nameless canvas's
//      label is made from (⑪ undecided → canvasLabel alone), and which canvas a new session opens on
//      (⑪ "this canvas or that folder's canvas" undecided → launch alone).
//
// Flow control and reconnect take the shape of spike D (docs/spikes/2026-09-07/pipeline/palmar/web/app.js) as-is:
// binary frame → term.write(bytes, cb) → {"t":"ack","n":len} inside cb.

(() => {
'use strict';

const TOKEN = window.PALMAR_TOKEN || '';
// **This script does not hold the key.** The key rides in the address when `index.html` is fetched, the daemon
// checks it, and its job ends there (#14) — JavaScript has no use for it. The reconnect check used to hold it
// to confirm a new token with `GET /?k=`; that is gone, so it is gone here too. The safest secret is one you
// do not hold. It stays in the address — a refresh's HTML request goes out before any JavaScript does.
const enc = new TextEncoder();
const root = document.documentElement;
const $ = (sel, from) => (from || document).querySelector(sel);

// ── constants ───────────────────────────────────────────
// ⑥ provisional: status → class. The colors live only in CSS.
const STATUS_CLASS = { waiting: 'wait', working: 'work', done: 'done', idle: 'idle', unknown: 'idle' };
// Group order in the list: whatever is waiting goes on top (decisions.md "화면"). unknown rides in the idle group.
const GROUPS = [
  ['waiting', 'wait', 'waiting on you'],
  ['working', 'work', 'working'],
  ['done',    'done', 'done'],
  ['idle',    'idle', 'idle'],
];
const PILL = new Set(['waiting', 'working', 'done']);   // states that show a pill (mockup: idle has no pill)
// #31 ③: even after grouping by canvas, **the order inside a group is still GROUPS** (waiting → working → done → idle).
// unknown sits where idle sits (⑥ provisional, same rule as STATUS_CLASS).
const STATUS_RANK = { waiting: 0, working: 1, done: 2, idle: 3, unknown: 3 };
function statusRank(s) { const r = STATUS_RANK[s.status]; return r === undefined ? 3 : r; }
// #24: the second line of a list row has to read as human (mockup). Turn hook event names into short phrases —
// display only, never used to decide status.
// An event name we do not know shows through as-is (fallback).
const EVENT_PHRASE = {
  SessionStart: 'started', UserPromptSubmit: 'working…', PermissionRequest: 'needs your approval',
  Notification: 'notified', Stop: 'finished', SessionEnd: 'session ended',
};
const GRID = 22, GAP = 12;                              // scan for free slots on the same 22px pitch as the dot grid
const DEFAULT_W = 520, DEFAULT_H = 360;                 // ⑩ provisional default size
const MIN_W = 220, MIN_H = 110;
const LS_TILES = 'palmar-tiles';                        // ③ provisional
const LS_THEME = 'palmar-theme';
const LS_GROUPS = 'palmar-groups';                      // list group fold — the browser's alone (⑪)
const LS_CVGROUPS = 'palmar-canvas-groups';             // canvas group fold — keyed by canvas id (#31 ③)
// Where a session whose canvas is gone falls. The contract says this cannot happen (protocol.md: within one
// hello frame every session.canvas is in that canvases list), but it beats the list losing a session.
const OTHER_KEY = '__other';
const MM_PAD = 4;                                       // inner padding of the minimap box
// The mockup's .term is 11.5px. The WebGL renderer **floors** the cell width to device pixels (addon-webgl:
// device.char.width = Math.floor(charWidth × dpr)) — 11.5px × 0.6em = 6.9px became a 6px cell at dpr 1, so glyphs
// were clipped by 13% and 80 columns fit into 520px (measured, headless Chrome 152). JetBrains Mono's advance
// width is 0.6em, but Chrome measured 6.996px at 11.667px — short of 7 (measured). So leave a little room at
// 11.75px: 7.05px → 7 at dpr 1, 14 → 7 at dpr 2. Indistinguishable from 11.5 by eye. Other dpr values (1.5 and
// such) still get floored — that is the spot cate hit.
const FONT_PX = 11.75;
// Text size per pane (#25). The window stays put and only the text changes, so **rows and columns grow and
// shrink** — see more in the same spot, or see it bigger. What the browser's Ctrl− does to every pane, done to one.
const FONT_MIN = 6, FONT_MAX = 32, FONT_STEP = 1;

// This machine's key names. Guidance text only; the handling side always takes **both** metaKey and ctrlKey —
// nail the guidance to one side and it points at a key the other person does not have (⌘ used to do that).
const IS_MAC = /Mac|iPhone|iPad/i.test(
  (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '');
const KMOD = IS_MAC ? '⌘' : 'Ctrl';
const CLIP_HINT = IS_MAC ? '⌘C / ⌘V' : 'Ctrl+Shift+C / Ctrl+Shift+V';
const LS_CLIPHINT = 'palmar.cliphint';
let clipHintShown = false;
try { clipHintShown = localStorage.getItem(LS_CLIPHINT) === '1'; } catch (e) {}

// ── copy and paste ────────────────────────────────────────
// xterm.js **does not have this.** Selecting is where its job ends; putting it on the clipboard is the app's
// (measured 2026-09-08: the selected text is reachable via getSelection(), but no combination put it on the clipboard).
// **Ctrl+C is not taken away.** In a terminal that is interrupt — intercept it because something is selected
// and the hand reaching to stop a run copies instead. So ask for Shift as well, the Linux/Windows convention,
// and use ⌘ on a Mac. Both are accepted — whichever habit the hand has, it should work.
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
    // Chrome asks separately for read permission. If it is refused, the browser's own paste is still there.
    toast(['could not paste — the browser refused to read the clipboard',
           'allow clipboard for ' + location.origin + ', or use the browser\'s own paste']);
    return '';
  }
}
// The mockup's .term line-height 1.55 is relative to font-size, while xterm's lineHeight is relative to the
// font's own line height (≈1.3), so about 1.15 lands on the same look. A value a person has to eyeball.
const LINE_HEIGHT = 1.15;

// ── DOM handles ─────────────────────────────────────────
const cv = $('#cv'), cvScroll = $('#cv-scroll'), listEl = $('#list'), treeEl = $('#tree');
const tabsEl = $('#tabs'), mmEl = $('#mm'), mmWorldEl = $('#mm-w'), mmVpEl = $('#mm-vp');
const toastEl = $('#toast'), searchEl = $('#search');
const launchBtn = $('#launch'), launchPath = $('#launch-path');

// ── state ───────────────────────────────────────────────
// The truth is the daemon (AGENTS.md "구조"). sessions is a copy /events sent, swapped out on every hello.
const sessions = new Map();   // id → Session
const tiles = new Map();      // id → Tile
const items = new Map();      // id → list row DOM
const changedAt = new Map();  // id → when the browser saw status change (ms). Not in the protocol, so measured here
let focused = null;           // id of the tile in front
let maxed = null;             // the tile shown expanded
let zTop = 10;
let eventsWs = null, eventsRetry = 0;
let layout = loadLayout();    // ③ provisional: { id: {x,y,w,h,z} }
let saveTimer = null;
// ⑪ canvases. The list, the order and the names are the daemon's; what is here is a copy. **Only current is the
// browser's** — two windows have to be able to look at different canvases, so the daemon has no "current canvas" (protocol.md).
const canvases = new Map();   // id → Canvas
let canvasOrder = [];         // id[] — in the order the daemon gave
let current = null;           // id of the canvas being looked at
let groupsCollapsed = loadGroups();   // list group fold — the browser's alone
let cvCollapsed = loadCvGroups();     // canvas group fold — same place, keyed by canvas id (#31 ③)
let tabDragged = false;       // the click right after a drop is not a switch
let tabsPending = false;      // a tab-strip repaint deferred while a name is being edited
// #31 ①④ close. After sending DELETE, **do not delete here** — gone from /events deletes (there has to be one
// path that deletes for this to move in step with a second browser). Meanwhile only hold what is in flight.
const closing = new Set();    // session ids we sent DELETE for and have not seen gone for yet
let rowConfirm = null;        // session id of the list row whose confirm strip is open (the list is rebuilt wholesale)

// ── small tools ────────────────────────────────────────
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
// List group fold — that browser's taste, not a property of the session (protocol.md "없는 것"). Same place as the theme.
function loadGroups() {
  try { const v = JSON.parse(localStorage.getItem(LS_GROUPS) || '{}'); return v && typeof v === 'object' ? v : {}; }
  catch (e) { return {}; }
}
function saveGroups() { try { localStorage.setItem(LS_GROUPS, JSON.stringify(groupsCollapsed)); } catch (e) {} }
// The canvas group fold is keyed by **canvas id**. Put it in the same bucket as the status keys ('waiting'…)
// and there is no telling which side to prune, so it gets its own bucket. An id means something only while the
// daemon lives (protocol.md "아무것도 디스크에 안 쓴다"), so on save prune keys for canvases that are gone —
// otherwise they pile up every time the daemon comes back.
function loadCvGroups() {
  try { const v = JSON.parse(localStorage.getItem(LS_CVGROUPS) || '{}'); return v && typeof v === 'object' ? v : {}; }
  catch (e) { return {}; }
}
function saveCvGroups() {
  for (const k in cvCollapsed) {
    if (!cvCollapsed[k]) delete cvCollapsed[k];                        // unfolded is the default, so it is not written
    else if (k !== OTHER_KEY && !canvases.has(k)) delete cvCollapsed[k];
  }
  try { localStorage.setItem(LS_CVGROUPS, JSON.stringify(cvCollapsed)); } catch (e) {}
}

// ⑫ Rename in place — the tab and the tile title share this. A name goes in through textContent only
// (protocol.md "이름은 셸에 안 닿는다": no innerHTML). Cancel puts the original children back.
function inlineEdit(host, initial, commit, after) {
  if (host.querySelector('input')) return;
  const prev = [...host.childNodes];
  const inp = document.createElement('input');
  inp.className = 'ed';
  inp.type = 'text';
  inp.value = initial || '';
  inp.maxLength = 64;              // protocol.md "이름 규칙" 1–64. The server checks again — this one is a convenience
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
    if (after) after();      // release the repaint deferred while editing
  };
  inp.addEventListener('keydown', (ev) => {
    ev.stopPropagation();          // ⌘K·Esc belong to this field — do not hand them to the global shortcuts
    if (ev.key === 'Enter') { ev.preventDefault(); end(true); }
    else if (ev.key === 'Escape') { ev.preventDefault(); end(false); }
  });
  inp.addEventListener('blur', () => end(true));
  // A press over this field is not a tab drag or a tile drag
  for (const t of ['pointerdown', 'click', 'dblclick']) inp.addEventListener(t, (ev) => ev.stopPropagation());
}

// #31 ①④ Ask in place — **a terminal is work somebody is running. Ask once before destroying it.**
// Two reasons not to use `window.confirm`: (1) it freezes the whole page, /events frame handling included,
// (2) we do not get to decide how it looks — the same reason the status lights are not drawn as emoji (AGENTS.md).
// The confirm strip goes inside host, and while it is there host carries `.cfm-on` — CSS hides host's other children.
// Why not detach and reattach the children: a broadcast arriving meanwhile and touching host (Tile.update·noteOutput) breaks nothing.
let activeConfirm = null;   // one confirm strip at a time — opening a new one takes back the old (no listeners left adrift)
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
  // A press outside cancels. **Do not cancel on focusout** — Chrome (Mac) does not focus a button clicked with
  // the mouse, so the pointerdown pressing "Close" fires focusout first and that cancel lands ahead of the click.
  function outside(ev) { if (!row.contains(ev.target)) end(false); }
  document.addEventListener('pointerdown', outside, true);
  yes.addEventListener('click', (ev) => { ev.stopPropagation(); end(true); });
  no.addEventListener('click', (ev) => { ev.stopPropagation(); end(false); });
  for (const t of ['pointerdown', 'click', 'dblclick']) row.addEventListener(t, (ev) => ev.stopPropagation());
  row.addEventListener('keydown', (ev) => {
    ev.stopPropagation();                      // Esc belongs to this strip — do not hand it to un-expand
    if (ev.key === 'Escape') { ev.preventDefault(); end(false); }
  });
  host.appendChild(row);
  host.classList.add('cfm-on');
  yes.focus();                                 // closing has to work from the keyboard alone. Esc is cancel
  activeConfirm = { row, cancel: () => end(false) };
  return activeConfirm;
}

// #31 ① DELETE /api/sessions/<id>. **Do not delete here** — gone from /events deletes.
// Delete optimistically and also take gone and there are two paths that delete, and that is where a second
// browser drifts (protocol.md "낸 쪽도 방송을 되받는다"). On failure only undo the marking and say so in a toast.
async function closeSession(id) {
  if (closing.has(id)) return;
  closing.add(id);
  paintClosing(id);
  try {
    await api('DELETE', '/api/sessions/' + encodeURIComponent(id));
  } catch (e) {
    closing.delete(id);
    paintClosing(id);
    // **404 is success** — a second browser (or the shell exiting) closed it first, and what the user asked for
    // has already happened. Measured 2026-09-08 (f2.py, two browsers open the confirm on the same session and
    // press Close almost together): A got 204, B got 404, and only B raised a 'close terminal: Not Found' toast —
    // told it failed when it closed. The daemon is following the contract (protocol.md `DELETE`: 404 if absent).
    // `gone` is the only thing that deletes, so do nothing here — do not revive the reconnect either (so a 404
    // arriving before gone does not re-attach to a pane that is not there).
    if (e.status === 404) return;
    // It did not die — revive the pane reconnect that was held off (see onclose above)
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
  // parts: [{b:'bold'}, 'plain', {d:'dim'}], or a string
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

// ── theme (system / light / dark, localStorage 'palmar-theme') ──
const themeBtn = $('#theme');
const darkMq = matchMedia('(prefers-color-scheme: dark)');
function storedTheme() {
  try { const v = localStorage.getItem(LS_THEME); return v === 'light' || v === 'dark' ? v : null; }
  catch (e) { return null; }
}
function applyTheme(mode) {   // mode: 'light' | 'dark' | null (system)
  if (mode) root.dataset.theme = mode; else delete root.dataset.theme;
  themeBtn.dataset.mode = mode || 'system';
  // **The word is the point** (#16). Three marks cannot say which is which on their own; the only
  // explanation used to be the title attribute, which needs a hover nobody performs.
  const lab = themeBtn.querySelector('b');
  if (lab) lab.textContent = mode || 'auto';
  themeBtn.title = 'theme: ' + (mode || 'follows the system') + ' — click to change';
  try { if (mode) localStorage.setItem(LS_THEME, mode); else localStorage.removeItem(LS_THEME); } catch (e) {}
  if (typeof renderBadge === 'function') renderBadge(true);   // the favicon color reads --st-*
  rethemeTerminals();
}
function cycleTheme() {
  const cur = root.dataset.theme || null;
  applyTheme(cur === null ? 'light' : cur === 'light' ? 'dark' : null);
}
themeBtn.addEventListener('click', cycleTheme);
themeBtn.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); cycleTheme(); } });
if (darkMq.addEventListener) darkMq.addEventListener('change', () => { if (!root.dataset.theme) rethemeTerminals(); });

// xterm takes colors as values. Read the values out of the CSS variables and hand them over — so a color never lives in two places.
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

// ── path display ───────────────────────────────────────
// Treat the first entry of the roots list as home — protocol.md writes the order as "사용자 홈 + …" and marks
// home no other way.
let home = null;
function shortPath(p) {
  if (!p) return '';
  if (home && (p === home || p.startsWith(home + '/'))) return '~' + p.slice(home.length);
  return p;
}

// ── HTTP ────────────────────────────────────────────────
async function api(method, path, body) {
  const url = new URL(path, location.origin);
  url.searchParams.set('token', TOKEN);   // **on everything, reads included** (protocol.md "인증")
  const init = { method };
  if (body !== undefined) { init.headers = { 'content-type': 'application/json' }; init.body = JSON.stringify(body); }
  const r = await fetch(url, init);
  const text = await r.text();
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch (e) { data = null; } }
  // **Carry the status on the failure** — the caller has to tell "somebody got there first" (404·409) from a real failure.
  // The one line in the body becomes message as-is (protocol.md: a 4xx body is `{"error": "one line a person reads"}`).
  if (!r.ok) {
    const err = new Error((data && data.error) || (r.status + ' ' + r.statusText));
    err.status = r.status;
    throw err;
  }
  return data;
}

// ── tiles ───────────────────────────────────────────────
class Tile {
  constructor(s) {
    this.id = s.id;
    this.s = s;
    this.closed = false;
    this.ws = null;
    this.lastOffset = 0;     // from= for the next connect. hello.offset − hello.replayed + bytes received since
    this.base = 0;
    this.received = 0;
    this.sentCols = 0; this.sentRows = 0;
    this.retry = 0;
    this.lastLine = '';
    this.off = false;

    const e = this.el = el('div', 'tile');
    e.dataset.id = s.id;
    // If it belongs to another canvas it is only absent from the screen — session and websocket stay alive (principle 2)
    if (current !== null && s.canvas !== current) e.classList.add('other');
    const tb = el('div', 'tb');
    this.dotEl = el('span', 'dot');
    this.nameEl = el('span', 'name');
    this.pillEl = el('span', 'st');
    this.szEl = el('span', 'sz');
    this.rnEl = el('span', 'rn'); this.rnEl.title = 'rename (or double-click the name)';
    this.xpEl = el('span', 'xp'); this.xpEl.title = 'expand';
    // #31 ① close. **The box is the same body as .rn·.xp** (they sit in one CSS rule together — there is no
    // room for size·border·hover to drift apart) and it joins the same row. Only the drawing inside differs — two strokes (×).
    // The reason no glyph (✕) is the same as for .rn: every font draws it differently (AGENTS.md).
    // The only difference is that it is a <button> — destroying has to be reachable from the keyboard too
    // (#31 ④). It looks like the spans.
    this.clEl = el('button', 'cl'); this.clEl.type = 'button';
    this.clEl.title = 'close terminal'; this.clEl.setAttribute('aria-label', 'close terminal');
    tb.append(this.dotEl, this.nameEl, this.pillEl, this.szEl, this.rnEl, this.xpEl, this.clEl);
    // #25 text size per pane. Ctrl/⌘+wheel is also browser zoom, so it must be blocked — leave it and reaching
    // to enlarge one window enlarges the whole page. A plain wheel is left alone so xterm's scrollback lives.
    // **Take it on the capture phase.** On bubble, xterm's scrollback handler eats it at the child first, and it
    // only reaches here when the scroll is already at the very top or the very bottom (user report 2026-09-08).
    e.addEventListener('wheel', (ev) => {
      if (!ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault(); ev.stopPropagation();
      this.setFont((this.term.options.fontSize || FONT_PX) + (ev.deltaY < 0 ? FONT_STEP : -FONT_STEP));
    }, { passive: false, capture: true });
    // The size readout is the reset button — put it on something already there rather than add another button to the title bar.
    this.szEl.addEventListener('click', (ev) => { ev.stopPropagation(); this.setFont(FONT_PX); });
    this.termEl = el('div', 'term');
    this.gripEl = el('div', 'grip');
    e.append(tb, this.termEl, this.gripEl);

    // Position: whatever was saved, else a free slot (⑩ provisional)
    const saved = layout[s.id];
    const w = saved && saved.w >= MIN_W ? saved.w : DEFAULT_W;
    const h = saved && saved.h >= MIN_H ? saved.h : DEFAULT_H;
    let x, y;
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) { x = saved.x; y = saved.y; }
    else { const p = firstFree(w, h, s.canvas); x = p.x; y = p.y; }
    const z = saved && saved.z ? saved.z : ++zTop;
    zTop = Math.max(zTop, z);
    Object.assign(e.style, { left: x + 'px', top: y + 'px', width: w + 'px', height: h + 'px', zIndex: String(z) });
    // **Do not wipe the saved text size here.** This line rewrites the position, not every property of the pane —
    // overwriting wholesale threw f away, so a fresh open always came up at the default size (measured:
    // localStorage still held f while the screen showed the default).
    layout[s.id] = { x, y, w, h, z };
    if (saved && saved.f) layout[s.id].f = saved.f;
    saveLayout();
    cvScroll.appendChild(e);

    // xterm — the browser does the terminal emulation (AGENTS.md principle 1)
    this.term = new Terminal({
      // Read from **saved**, not layout[s.id] — the layout entry is rewritten above, so whatever gets
      // dropped in between, this value does not move.
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
    // Say what the shortcut is **once**, the first time text is selected. The copy shortcut differs per terminal
    // (here Ctrl+C is interrupt), so there is no finding it by pressing — selecting is the moment to say it.
    this.term.onSelectionChange(() => {
      if (clipHintShown || !this.term.hasSelection()) return;
      clipHintShown = true;
      try { localStorage.setItem(LS_CLIPHINT, '1'); } catch (e) {}
      toast([CLIP_HINT + ' to copy and paste', 'Ctrl+C stays as interrupt, the way a terminal expects']);
    });
    // Look at the key **before** xterm handles it. true hands it through, false takes it for us.
    this.term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== 'keydown') return true;
      // **xterm must not touch a key while the IME is composing.** If a keydown during composition has a keyCode
      // other than 229, xterm throws the whole composition away (`_compositionHelper.keydown` → `_finalizeComposition(false)`).
      // But the Korean IME on macOS sends the **real key code** in some browsers — and then composition breaks on
      // every character and "안녕하십니까" scatters into jamo (user report 2026-09-09).
      // Measured: the same input fed through as keyCode 229 gave '안녕하십니까'; fed with real key codes it fell apart.
      // Returning false here ends xterm's _keyDown right there and the composition survives. When composition
      // finishes, compositionend does its part, so nothing is lost.
      // Do not trust `ev.isComposing` alone — **Safari sometimes leaves that field unset** (Korean failing only in
      // Safari was exactly this, 2026-09-09). The textarea reports composition start and end reliably, so we
      // count it ourselves from that (below, after term.open).
      // xterm must not touch a key while composing — if a keydown during composition has a keyCode other than
      // 229, xterm throws the whole composition away (`_compositionHelper.keydown` → `_finalizeComposition(false)`).
      if (ev.keyCode === 229) return false;
      // **A modifier pressed on its own finishes nothing.** Tense consonants like ㄲ·ㅃ go through Shift, and
      // reading that as "the IME let go" shoots the character still being composed out on the spot
      // (the measured log has `keydown key="Shift"` right before ㄲ).
      if (ev.key === 'Shift' || ev.key === 'Control' || ev.key === 'Alt' ||
          ev.key === 'Meta' || ev.key === 'CapsLock') return true;
      // A real key that is not 229 arrived = the IME let go. **Send the character not yet sent first** —
      // otherwise Enter goes ahead of it and the last character is lost.
      this.kdSeen = true;
      if (this.imePending) this.imeFlush();
      if (this.composing || ev.isComposing) return false;
      // **This has to come before the mod check below.** Ctrl+Enter has no Shift, so it fails that condition,
      // and then xterm handles the key and cuts propagation so it never reaches the global shortcuts
      // (measured: Ctrl+Shift+Enter worked and only Ctrl+Enter did not).
      if (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey) && !ev.altKey) return false;
      const mod = ev.metaKey || (ev.ctrlKey && ev.shiftKey);
      if (!mod || ev.altKey) return true;
      const k = (ev.key || '').toLowerCase();
      if (k === 'c') {
        const sel = this.term.getSelection();
        if (!sel) return true;              // nothing selected — it belongs to the terminal
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
    // **Count composition ourselves.** The key handler above reads this — browsers fill `ev.isComposing` to
    // different degrees, and that difference is what broke Korean in Safari.
    // Clear it **immediately** on compositionend: clear it late and the key after the composition (Enter, say) gets swallowed too.
    this.composing = false;
    this.sawComposition = false;   // does this browser use composition events
    this.imePending = '';          // the character not committed yet (for browsers with no composition events)
    this.kdSeen = false;
    const ta = this.termEl.querySelector('textarea');
    if (ta) {
      ta.addEventListener('compositionstart', () => { this.composing = true; this.sawComposition = true; });
      ta.addEventListener('compositionend', () => { this.composing = false; });
      // **The flag has to die with its key.** `kdSeen` is cleared only inside the input listener below, but the
      // keys xterm handles itself (Enter·Backspace·Tab·arrows) end in `preventDefault` and no input arrives at
      // all. Then the flag stays on and **the first jamo of the next Korean typed** is misread as "that was a
      // plain key" and goes to the shell as a bare letter — "나" after one Enter comes out "ㄴ나".
      // A plain key goes keydown → input → keyup, so the flag lives until input sees it.
      ta.addEventListener('keyup', () => { this.kdSeen = false; });
      // Safety pin. If compositionstart arrives and end never does, that pane swallows keys wholesale —
      // rather than be stuck there, release on focus leaving.
      // If focus leaves with a key held down, keyup never gets here — clear on the way out too.
      ta.addEventListener('blur', () => { this.composing = false; this.kdSeen = false; this.imeFlush(); });
    }
    // ── browsers that emit no composition events (Safari) ───────────────
    // Measured (2026-09-09, Safari 18.6, Korean): `compositionstart`·`compositionend` **never arrive at all.**
    // It speaks only through `input` — `insertText` starts a new character and `insertReplacementText` swaps out
    // the last character still being composed. xterm sends only `insertText`, so **only the jamo being composed
    // goes out and the finished character never does**: that is how "안녕하십니까" became "ㅇㄴㅇㄴㅎㄴ까".
    //
    // **Take it on the parent, in capture.** xterm's input listener sits on the textarea and was attached before
    // ours, so on the same node theirs always runs first. The parent's capture phase comes before that, so we can stop it here.
    this.termEl.addEventListener('input', (ev) => {
      if (this.sawComposition) return;        // a browser that uses composition events — leave it to xterm
      const it = ev.inputType;
      if (it !== 'insertText' && it !== 'insertReplacementText') return;
      // **Tell a plain key from an IME key by their order.** A plain key goes keydown → (xterm sends) → input,
      // an IME key goes input → keydown (measured: space fires keydown first, Korean fires input first).
      // A keydown with keyCode 229 does not raise the flag, so no flag here means IME.
      const plain = this.kdSeen;
      this.kdSeen = false;
      if (plain) return;                      // xterm already sent it on keydown
      ev.stopPropagation();                   // block xterm's _inputEvent — we send it
      const d = ev.data || '';
      if (it === 'insertReplacementText') {
        this.imePending = d;                  // the character being composed changed — do not send yet
      } else {
        if (this.imePending) this.sendText(this.imePending);   // the previous character is committed
        this.imePending = d;
      }
    }, true);
    this.gl = tryWebgl(this.term, () => { this.gl = null; updateStatusBar(); });
    // Measuring while hidden (another canvas) gives 0 columns — refit() measures when it becomes visible
    this.fitted = false;
    if (this.visible()) { this.fit.fit(); this.fitted = true; }

    // Push out the character not committed yet. Called wherever the IME lets go.
    this.imeFlush = () => {
      if (!this.imePending) return;
      const d = this.imePending;
      this.imePending = '';
      this.sendText(d);
      const t2 = this.termEl.querySelector('textarea');
      if (t2 && t2.value) t2.value = '';   // Safari never clears this value — it grows without end
    };
    this.sendText = (d) => {
      if (this.ws && this.ws.readyState === 1) this.ws.send(enc.encode(d));
    };
    this.term.onData((d) => {
      if (this.ws && this.ws.readyState === 1) this.ws.send(enc.encode(d));
      if (this.s.status === 'done') sendSeen(this.id);   // typing in it means you have seen it
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

  // ⑪ Does it belong to this canvas? If current is null the **daemon does not know canvases**, so everything shows (renderTabs below).
  visible() { return current === null || this.s.canvas === current; }

  // ⑫ Rename. An empty name is sent as null (= clear the name) — the label goes back to the path.
  rename() {
    inlineEdit(this.nameEl, this.s.name || '', async (v) => {
      try { upsert(await api('PATCH', '/api/sessions/' + encodeURIComponent(this.id), { name: v || null })); }
      catch (e) { toast(['rename: ' + e.message]); }
    }, () => { if (!this.closed) this.update(this.s); });   // apply now whatever broadcast arrived while editing
  }

  // #31 ① Ask inside the title bar. While the confirm is open, CSS hides the rest of the title bar (.tb.cfm-on).
  askClose() { askClose(this.el.firstChild, 'Close this terminal?', () => closeSession(this.id)); }

  // ── pane channel /pty/<id> ──
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
      // If the size changed after it went out on the connect URL, say so
      if (this.term.cols !== this.sentCols || this.term.rows !== this.sentRows) this.sendResize();
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        let m = null;
        try { m = JSON.parse(ev.data); } catch (e) { return; }
        if (m && m.t === 'hello') {
          // offset is the absolute offset **after** the replay (protocol.md). The replay itself arrives as binary
          // right after, so the next from is built from the pre-replay position (offset − replayed) plus the bytes
          // actually received. Cut off mid-replay and only what arrived is counted.
          this.hello = m;
          // `| 0` truncates to 32 bits. `produced` is **every** byte that pane has ever put out, so a long build
          // log or a pane running `tail -f` goes past 2GB, the offset wraps negative, and every attach and detach
          // pulls the whole ring down again. `+` is exact to 2^53.
          this.base = (+m.offset || 0) - (+m.replayed || 0);
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
        // Flow control: ACK only after parsing is done (spike D)
        if (ws.readyState === 1) ws.send(JSON.stringify({ t: 'ack', n: bytes.length }));
      });
      this.noteOutput();
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.closed || !sessions.has(this.id)) return;
      // #31 ①: this is a pane we asked to be closed. The daemon may close the pane socket first and send gone
      // after, and knocking again in between gets a 404 from /pty/<id> and an error in the console (measured:
      // dev-stub --delay-gone 1.2). When gone arrives, dispose takes this tile away — **the session is not deleted here.**
      if (closing.has(this.id)) return;
      this.retry = Math.min(10000, this.retry ? this.retry * 2 : 500);
      setTimeout(() => this.connect(), this.retry);
    };
    ws.onerror = () => {};   // onclose follows
  }

  sendResize() {
    if (!this.ws || this.ws.readyState !== 1) return;
    if (this.term.cols === this.sentCols && this.term.rows === this.sentRows) return;
    this.sentCols = this.term.cols; this.sentRows = this.term.rows;
    this.ws.send(JSON.stringify({ t: 'resize', cols: this.term.cols, rows: this.term.rows }));
  }

  refit() {
    if (!this.visible()) return;            // another canvas — cannot measure (display:none)
    const d = this.fit.proposeDimensions();
    if (!d || !d.cols || !d.rows) return;   // no size yet
    this.fit.fit();
    this.fitted = true;
    this.sendResize();   // "this is the only thing that changes rows·columns" — sent only when the window size changed
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

  // The window is left alone. Change only the text and re-measure and rows·columns follow — and that resize
  // reaches the agent (refit → sendResize), so a TUI redraws at the new size.
  setFont(px) {
    const f = Math.max(FONT_MIN, Math.min(FONT_MAX, Math.round(px * 4) / 4));
    if (Math.abs(f - (this.term.options.fontSize || FONT_PX)) < 0.01) return;
    this.term.options.fontSize = f;
    this.refit();          // does fit → sendResize → showSize in one go
    this.persist();
  }

  update(s) {
    this.s = s;
    // **The daemon's rows·columns are the real ones.** When a second browser attaches to the same pane the PTY
    // changes to that one's size (daemon.attach), and the browser that attached first used to throw that broadcast
    // away and keep drawing at the old size — the shell wraps at 80 columns while the screen draws 120, and every
    // line after that is off. Setting `sentCols` first is the point: nothing is echoed back, so it does not
    // oscillate, and the moment this browser actually touches the window it asserts its own size again.
    if (s.cols && s.rows && (s.cols !== this.term.cols || s.rows !== this.term.rows)) {
      this.sentCols = s.cols; this.sentRows = s.rows;
      try { this.term.resize(s.cols, s.rows); } catch (e) { /* the pane is closing */ }
      this.showSize();
    }
    const cls = STATUS_CLASS[s.status] || 'idle';
    this.dotEl.className = 'dot ' + cls;
    // ⑫ A name a person gave wins. Without one, the path label as before.
    // While it is being edited, leave the field alone — a broadcast may arrive, but the hand comes first.
    if (!this.nameEl.querySelector('input')) {
      this.nameEl.textContent = '';
      if (s.name) this.nameEl.textContent = s.name;
      else this.nameEl.append((s.agent || 'shell') + ' ', el('span', null, '· ' + shortPath(s.cwd)));
    }
    if (PILL.has(s.status)) { this.pillEl.hidden = false; this.pillEl.className = 'st ' + cls; this.pillEl.textContent = s.status; }
    else { this.pillEl.hidden = true; }
  }

  // The list's "last line" — the first non-blank line above the cursor in the buffer. Never used to decide
  // status (we do not read the screen to decide status).
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
    if (f && Math.abs(f - FONT_PX) > 0.01) layout[this.id].f = f;   // the default is not written
    saveLayout();
  }

  dragify() {
    const bar = this.el.firstChild, grip = this.gripEl;
    let mode = null, sx = 0, sy = 0, ox = 0, oy = 0, ow = 0, oh = 0;
    const down = (m) => (ev) => {
      // Buttons, input fields and the confirm strip on the title bar are not a drag (#31: .cl and .cfm joined here)
      // Only .sz.own is excluded — the ordinary size readout is part of the title bar and should drag, and it
      // becomes a reset button only on a pane whose text size was changed (#25). Leave it in and pointerdown is
      // taken as a drag and no click fires.
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
      // Move the minimap rectangle along using **the value just computed** — do not ask the DOM again
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
      paintTidy();          // moving a window creates or removes slack to close up
      if (was === 'size') this.refit();   // tell the PTY only when the resize is let go (spike D)
      this.persist();
      renderMinimap();                    // the world may have grown — take the scale again
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
    gl.onContextLoss(() => { gl.dispose(); onLoss(); });   // on context loss xterm falls back to the DOM renderer
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

// ⑩ provisional: scan for a free slot. Push-aside is #23. **Only windows on the same canvas count** — canvases
// are different sheets of paper (⑪). Positions are read from the coordinate store, not the DOM: a tile on another
// canvas is display:none so its offsetLeft is 0, and trusting the DOM piles new windows onto the origin.
// (Same reason as AGENTS.md "창은 스토어에서 직접 읽는다".)
// Closing a window frees its slot. **The browser closes up below and to the right on its own** — it measures the
// scroll extent out to the farthest tile. Above and to the left it does not: the origin is pinned at 0, so empty
// space before the first tile still counts as 'content'. That is why closing the bottom window shrank the space
// and closing the top one did not.
//
// **Never automatically.** Empty space before the origin can only be removed by moving the content, and when it
// is above the viewport the visible windows jump (measured: a remaining window jumped 206px). Where a window was
// put is this program's promise — my window must not move because somebody else's closed. So it happens **only
// when a person asks** — for the person who asked, movement is not a surprise.
// Is there slack to close up on this canvas? Decides whether to dim the button — a button that does nothing
// when pressed is a button that lies.
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
  // **Write the intended value instead of reading it back.** `persist()` reads `offsetLeft`, but the position has
  // a transition on it, so that value is a **mid-move** one — save it as-is and the old position goes back in and
  // nothing appears to have happened (measured: pressing it left the positions unchanged). The drag path was
  // dodging this trap by turning the transition off with the `drag` class.
  for (const t of mine) {
    const r = layout[t.id];
    t.el.style.left = (r.x - sx) + 'px';
    t.el.style.top = (r.y - sy) + 'px';
    layout[t.id] = Object.assign({}, r, { x: r.x - sx, y: r.y - sy });
  }
  saveLayout();
  // Pull the viewport along too. If the content becomes shorter than the viewport the browser clips it to 0, but
  // by then everything fits on one screen anyway, so nothing is missed.
  // **Only for the canvas being looked at.** Every canvas shares the one scroll box (a hidden pane is just
  // display:none). With auto-tidy on, one pane disappearing on a canvas in the background would slide the view
  // you are looking at sideways — text moves while you touched nothing.
  if (canvasId === current) {
    cvScroll.scrollLeft = Math.max(0, l0 - sx);
    cvScroll.scrollTop = Math.max(0, t0 - sy);
  }
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

// Lay a canvas out as a grid. **Only used when a batch arrives at once** — a restore. One at a time,
// firstFree is right: it puts the new window where there is room and leaves everything else alone.
// A batch is different. firstFree fills a row before starting the next, and at 520px wide only one
// tile fits across a 912px canvas, so six restored panes came back as a single column 2,232px tall.
// Nothing overlapped; you just had to scroll past all of it.
//
// The column count is chosen so the block's shape is closest to the canvas you are looking at,
// measured on the **log** of the ratio so that twice-as-wide and half-as-wide count as equally wrong
// — plain subtraction always prefers the too-tall option and two panes came out stacked.
function arrangeCanvas(canvasId) {
  const mine = [...tiles.values()].filter((t) => t.s.canvas === canvasId && layout[t.id]);
  if (mine.length < 2) return;
  mine.sort((a, b) => (a.s.created || 0) - (b.s.created || 0));
  const w = Math.max(...mine.map((t) => layout[t.id].w));
  const h = Math.max(...mine.map((t) => layout[t.id].h));
  const vw = cvScroll.clientWidth || 1, vh = cvScroll.clientHeight || 1;
  const want = Math.log(vw / vh);
  let cols = 1, best = Infinity;
  for (let c = 1; c <= mine.length; c++) {
    const bw = c * (w + GAP) + GAP, bh = Math.ceil(mine.length / c) * (h + GAP) + GAP;
    const d = Math.abs(Math.log(bw / bh) - want);
    if (d < best) { best = d; cols = c; }
  }
  mine.forEach((t, i) => {
    const r = layout[t.id];
    const x = GAP + (i % cols) * (w + GAP), y = GAP + Math.floor(i / cols) * (h + GAP);
    t.el.style.left = x + 'px';
    t.el.style.top = y + 'px';
    layout[t.id] = Object.assign({}, r, { x, y });
  });
  saveLayout();
  renderMinimap();
  refreshOff();
  paintTidy();
}

// ── focus · expand ──────────────────────────────────────
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
  // done clears only when the user looks at that window — only focus a hand caused counts as "seen"
  if (opts.user && t.s.status === 'done') sendSeen(id);
  if (opts.keyboard !== false) t.term.focus();
}

// Go to that session. A click in the list and a click on a notification take the same path (#40).
function goToSession(id) {
  const tile = tiles.get(id);
  if (!tile) return;
  // ⑪ If it belongs to another canvas, cross over to that canvas and go to that window
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
  // Rows and columns really do grow — fit once after .tile's .35s transition ends
  const targets = new Set([tile, was].filter(Boolean));
  setTimeout(() => { for (const t of targets) if (!t.closed) t.refit(); refreshOff(); }, 380);
}
addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !maxed) return;
  // Esc inside a terminal belongs to the app (vim·claude) — do not take it. Only Esc pressed on the canvas or a rail un-expands.
  if (e.target && e.target.closest && e.target.closest('.xterm')) return;
  // **Do not take an Esc that already has an owner either.** This listener is attached before the other three,
  // so a later one calling `stopPropagation` does nothing — standing down here is the only way. Otherwise one
  // Esc that clears the search field or closes the shortcuts panel folds the expanded terminal with it.
  const t = e.target;
  if (t === searchEl) return;                                    // it clears the search field
  if (t && t.closest && t.closest('.rz')) return;                // it resets the rail width
  const keys = document.getElementById('keys');
  if (keys && !keys.hidden) return;                              // it closes the shortcuts panel
  setMax(maxed, false);
});
// #21: right after expanding, focus is inside the terminal so the Esc above does not fire. Let the hint pill be
// clicked back to the canvas as well (the terminal's Esc still goes to the app — this path is the pill click only).
$('.esc').addEventListener('click', () => { if (maxed) setMax(maxed, false); });
addEventListener('resize', () => {
  // When the window narrows, the current rail widths can push the canvas below its minimum — clamp again here.
  // **Fix only the widths and do the cleanup once below** — using setRail would repaint the minimap three times.
  railPut('l', railW('l')); railPut('r', railW('r'));
  if (maxed) maxed.refit(); renderMinimap(); refreshOff();
});

// ── rail width (#19) ───────────────────────────────────────
// The width lives in the CSS variables --rail-l·--rail-r and nowhere else, and .top and .body both read them —
// the two used to write the same value separately, so fixing one left the top bar and the body below out of line.
// **A narrower rail is a wider canvas.** Even with the windows unmoved, what is visible changes, so it needs
// **exactly the same cleanup** as a window resize (refit the expanded window · minimap scale · off-screen markers).
//: Auto-tidy. **It starts off** — it moves visible windows, and my window must not jump because somebody else's
//: closed. Leaving it on means the cost was known when it was turned on.
const LS_AUTOTIDY = 'palmar.autotidy';
let autoTidy = false;
try { autoTidy = localStorage.getItem(LS_AUTOTIDY) === '1'; } catch (e) {}

const LS_RAILS = 'palmar.rails';
const RAIL_DEF = { l: 256, r: 232 };
const RAIL_MIN = { l: 180, r: 160 };   // narrower than this and the labels break on the left, the top-row buttons on the right
const RAIL_MAX = 480;
const CANVAS_MIN = 320;                // the two rails may not push the canvas below this

function railClamp(side, px) {
  const other = side === 'l' ? railW('r') : railW('l');
  const room = innerWidth - other - CANVAS_MIN;
  return Math.round(Math.max(RAIL_MIN[side], Math.min(px, RAIL_MAX, room)));
}
function railW(side) {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--rail-' + side));
  return isFinite(v) ? v : RAIL_DEF[side];
}
function railPut(side, px) {   // fixes the width only. No cleanup
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
  // The saved value may not fit this window (moved to a smaller screen) — clamp again instead of using it as-is.
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
    // Do not save while dragging — write once when the hand lets go (so localStorage is not written 60 times a second).
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
  // Double-click resets to the default. So nobody drags themselves into a width that is hard to drag back out of.
  el.addEventListener('dblclick', () => setRail(side, RAIL_DEF[side]));
  // It has to be reachable from the keyboard too — the same reason the close control is a <button>.
  el.addEventListener('keydown', (ev) => {
    const step = ev.shiftKey ? 48 : 16;
    if (ev.key === 'ArrowLeft')  { ev.preventDefault(); setRail(side, railW(side) + (side === 'l' ? -step : step)); }
    else if (ev.key === 'ArrowRight') { ev.preventDefault(); setRail(side, railW(side) + (side === 'l' ? step : -step)); }
    else if (ev.key === 'Home' || ev.key === 'Escape') { ev.preventDefault(); setRail(side, RAIL_DEF[side]); }
  });
}

// ── off-canvas marker ("↗ off") ─────────────────────────
// **Positions are read from the coordinate store — the same reason as the minimap** (AGENTS.md "do not read DOM
// layout on every scroll", "read windows straight from the store"). The old version read offsetLeft/Top/Width/Height
// per window and forced **window count × 4** layouts per scroll batch (measured: 20 for 5 windows). The canvas
// grows without limit (⑩), so that number grows with the window count — cate's 374 repaints a second was this
// kind of thing. Now the viewport is measured once per batch.
function isOff(t, sl, st, vw, vh) {
  // An expanded window fills the canvas, so it cannot be off-screen — do not look at the old position left in the store
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
    // Measure the viewport once per batch (constant). Re-measuring per window forces one layout per window.
    const sl = cvScroll.scrollLeft, st = cvScroll.scrollTop;
    const vw = cvScroll.clientWidth, vh = cvScroll.clientHeight;
    // Something on another canvas is not "↗ off" — it gets a canvas label instead (⑪)
    // **Do not rebuild the list to flip one badge.** The old version called renderList() here, and while scrolling
    // that ran 60 times in 6 seconds (measured 2026-09-08: 16 windows, 3 of 5 runs at 62·62·60). Every .ses row was
    // torn down and rebuilt with fresh click listeners each time, and **a text selection held in the rail vanished**
    // (measured: empty string by the second second). The work is O(sessions) while the canvas grows without limit
    // (⑩) — exactly the shape AGENTS.md warns about.
    for (const t of tiles.values()) {
      const o = t.visible() ? isOff(t, sl, st, vw, vh) : false;
      if (o !== t.off) { t.off = o; paintOff(t.id); }
    }
  }, 80);
}
cvScroll.addEventListener('scroll', refreshOff, { passive: true });

// ── grab and drag the canvas ──────────────────────────────
// Press on empty space and drag and the view follows the hand (like a map). The scrollbars and the wheel stay;
// this rides on top of them — the canvas grows without end (⑩), so one way to travel far is too few.
// It starts **only on empty space**: a press on a tile belongs to the tile (title-bar drag · text selection · terminal input).
const PAN_SLOP = 3;      // below this much movement it is not a drag — that is what keeps a plain press alive
cvScroll.addEventListener('pointerdown', (ev) => {
  if (ev.button !== 0 || ev.target !== cvScroll) return;   // on the bare floor only
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
    // Scroll **the other way** so the grabbed point follows the hand. The browser stops at both ends on its own.
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

// ── canvas tabs (⑪) ────────────────────────────────────
// A tab is **a switch**. Not missing anything is the left list's job (decisions.md ⑪) — so the only thing a tab
// carries is one dot, no count and no close button. The order belongs to the daemon (protocol.md "the daemon owns
// the order"), so a drop sends all of them at once through POST /api/canvases/order — fix them one at a time and
// two browsers draw different tab strips.
const addTabEl = el('span', 'ib', '＋');
addTabEl.id = 'tab-add';
addTabEl.title = 'new canvas';

// PROVISIONAL — ⑪ has not decided "whether a person names a canvas or the name comes from the folder". The
// protocol carries only name: null and the browser makes the text (protocol.md "없는 것"). So it is one
// placeholder. **Being a placeholder, changing the order changes the text** (a "canvas 2" dragged to the front
// becomes "canvas 1") — this applies only to nameless canvases, and it is a property that disappears the moment a
// person gives a name. If naming from the folder is decided, only this one function changes.
// A nameless canvas's label is built from **the order it was created in (seq)**. Built from order, the labels swap
// the instant a tab is dragged to a new position, and to the user the canvas names appear to change on their own
// (report 2026-09-08). For an older daemon that does not know seq, fall back to the old way — back then position was the name.
function canvasLabel(c) {
  if (c && c.name) return c.name;
  return 'canvas ' + (c && c.seq ? c.seq : ((c ? c.order : 0) + 1));
}
function canvasById(id) { return canvases.get(id) || null; }

// The dot on a tab is not stored — it is computed (protocol.md "탭의 점"). On if anything on this canvas wants you.
// #18 removing a canvas. **The daemon decides the conditions** — what is here is not a copy of that rule but a mirror of it.
// The daemon refuses with 409 (1) if it holds any session, (2) if it is the last canvas (protocol.md).
// So the screen shows the handle **only when neither holds** — there is never a request that will be refused.
// No path is built that removes a canvas by killing the shells inside it: a screen action must not kill a running process.
function canvasEmpty(id) {
  for (const x of sessions.values()) if (x.canvas === id) return false;
  return true;
}
function canvasRemovable(id) { return canvasEmpty(id) && canvasOrder.length > 1; }

async function removeCanvas(id) {
  try {
    await api('DELETE', '/api/canvases/' + encodeURIComponent(id));
  } catch (e) {
    // Another browser may have opened a terminal meanwhile — then the daemon is right and we are late.
    if (e.status === 409) { toast([e.message, 'close its terminals first, then try again']); return; }
    if (e.status === 404) return;                 // another browser removed it first — what was asked for happened
    toast(['remove canvas: ' + e.message]);
  }
  // The tab is not removed here — the canvas_gone broadcast removes it (the same discipline as closing a terminal).
}

function canvasWant(id) {
  const mine = [];
  for (const s of sessions.values()) if (s.canvas === id) mine.push(s);
  return wantClass(mine);
}

function setCanvases(list) {          // hello · canvases — swap the whole thing out
  canvases.clear();
  canvasOrder = [];
  for (const c of [...(list || [])].sort((a, b) => a.order - b.order)) { canvases.set(c.id, c); canvasOrder.push(c.id); }
  if (!current || !canvases.has(current)) current = canvasOrder[0] || null;
  applyCanvas();
}
function putCanvas(c) {               // canvas — one was created or renamed. Applied idempotently by id
  const isNew = !canvases.has(c.id);
  canvases.set(c.id, c);
  if (isNew) canvasOrder.push(c.id);
  canvasOrder.sort((a, b) => canvases.get(a).order - canvases.get(b).order);
  if (!current) current = canvasOrder[0] || null;
  renderTabs();
  renderList();                       // the badge text follows the name
}
function dropCanvas(id) {             // canvas_gone — canvases follows right behind (contract)
  canvases.delete(id);
  canvasOrder = canvasOrder.filter((x) => x !== id);
  if (current === id) current = canvasOrder[0] || null;
  applyCanvas();
}

// Expansion does not follow the canvas. When an expanded window stops belonging to this canvas, un-expand it —
// leave .cv.has-max on and `.cv.has-max .cv-scroll { overflow: hidden }` means **the new canvas will not scroll
// with the wheel** (scrolling is the only way to reach a window pushed out under ⑩), and the Esc pill floats over
// a screen where nothing is expanded. Measured (2026-09-08, headless Chrome 152): expand on canvas 2, then tab
// over to canvas 1, and with has-max=true a deltaY 600 wheel left scrollTop at 0 while .esc's display was 'flex'.
function syncMax() { if (maxed && !maxed.visible()) setMax(maxed, false); }

// Switching canvases leaves the tiles alive — they are only absent from the screen. Re-measure only the ones that became visible.
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
  paintTidy();      // the button speaks about **the canvas being looked at**
  // The window may have been resized while it was hidden — fit once more after it has been drawn
  requestAnimationFrame(() => { for (const t of tiles.values()) if (t.visible()) t.refit(); });
}

// **The tab strip is patched, not rebuilt.** Every session frame calls renderTabs (the dot is computed), and the
// old version emptied #tabs wholesale each time. If a person was dragging a tab meanwhile, the dragged element fell
// out of the DOM and the still-live pointermove handler put the detached element back — and **the same tab existed
// twice** (measured 2026-09-08, headless Chrome 152 + a real CDP drag: one hook took tabs 20 → 21, 1 duplicate id,
// and the drop sent a 21-entry order and got 409 "canvas list changed" → the reorder vanished silently).
// And deferring the whole thing while a name was being edited let **the dot lie for over 5 seconds** (measured:
// daemon waiting=1 while every tab dot was off). The dot is the only signal a tab carries (⑪), so if it is wrong
// the tab strip is doing nothing.
// So: text·dot·selection are patched in place **always**, and only reordering is deferred during a drag or an edit.
const tabEls = new Map();     // canvas id → tab DOM. Live elements are reused
let tabsShown = null;         // the current tab last scrolled into view — so a strip a person pushed is not undone every frame

function makeTab(id) {
  const t = el('div', 'tab');
  t.dataset.id = id;
  t.tabIndex = 0;
  t.setAttribute('role', 'tab');
  const nm = el('span', 'nm');
  t.appendChild(nm);
  // The canvas object is replaced on every broadcast, so hold only the id and look it up each time
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
  // Do not touch the text of an element being edited — the hand comes first (inlineEdit puts an input in there)
  if (nm && !nm.querySelector('input') && nm.textContent !== label) nm.textContent = label;
  t.title = label + ' — double-click to rename';
  const cur = id === current;
  t.classList.toggle('cur', cur);
  t.setAttribute('aria-selected', cur ? 'true' : 'false');
  // One dot. The color is the status color as-is (⑥ undecided, no new colors). waiting does not clear on a click
  // (a hook clears it), done clears when that window is looked at — neither is "click the tab and it goes out".
  const dot = $('.dot', t), want = canvasWant(id);
  if (!want) { if (dot) dot.remove(); }
  else if (!dot) t.appendChild(el('span', 'dot ' + want));
  else if (dot.className !== 'dot ' + want) dot.className = 'dot ' + want;

  // #18 the remove handle. It sits **only on the tab being looked at** — the tab strip is a switch, not a place to
  // hang a button on every tab (protocol.md "탭의 점": one dot is all a tab carries). If it cannot be
  // removed, the handle is simply not there.
  const cx = $('.cx', t), can = cur && canvasRemovable(id);
  if (!can) { if (cx) cx.remove(); }
  else if (!cx) {
    const b = el('button', 'cx'); b.type = 'button';
    b.title = 'remove this canvas';
    b.setAttribute('aria-label', 'remove canvas ' + label);
    // No question asked. The handle is there **because** this canvas is empty, and all that disappears is a name
    // and a position. Put a confirm on the harmless thing and the confirm on the dangerous one (closing a
    // terminal) gets waved through out of habit.
    b.addEventListener('click', (ev) => { ev.stopPropagation(); removeCanvas(id); });
    b.addEventListener('pointerdown', (ev) => ev.stopPropagation());   // so the tab drag does not catch
    t.appendChild(b);
  }
  // The tab itself says why it cannot be removed — do not leave the missing handle to be guessed at.
  if (cur && !can) {
    t.title = label + (canvasEmpty(id)
      ? ' — the last canvas cannot be removed'
      : ' — has terminals; close them to remove this canvas');
  }
}

function renderTabs() {
  // **If no canvases arrived at all, take the tab strip down entirely.** This does **not** settle ⑪'s open item
  // ("whether to hide the tab strip when there is only one canvas") — by the contract there is never a moment with
  // zero canvases (protocol.md "데몬이 뜨면 캔버스가 하나 있다"), so zero means "this daemon does not
  // know canvases yet", and showing ＋ then would POST to a route that is not there. On such a daemon everything
  // sits on one screen, as if there were a single canvas.
  tabsEl.hidden = canvasOrder.length === 0;
  if (tabsEl.hidden) {
    for (const [, t] of tabEls) t.remove();
    tabEls.clear();
    addTabEl.remove();
    return;
  }
  // Remove elements for canvases that are gone. If the dragged one vanished, tabDrag's move gives up on its own (it checks there).
  for (const [id, t] of [...tabEls]) if (!canvases.has(id)) { t.remove(); tabEls.delete(id); }
  // A new canvas joins at the end (protocol.md "만들기는 끝에 붙는다") — it does not shove an element being edited
  for (const id of canvasOrder) if (!tabEls.has(id)) { const t = makeTab(id); tabEls.set(id, t); tabsEl.appendChild(t); }
  for (const id of canvasOrder) paintTab(id);
  // Only reordering is deferred. During a drag the DOM order differs from the daemon's on purpose, and during an
  // edit, moving elements drops the input's focus. What was deferred is released when the drag ends (tabDrag's up)
  // and when the edit ends (renameCanvas's after).
  if (tabsEl.querySelector('.ed') || tabsEl.querySelector('.tab.drag')) { tabsPending = true; return; }
  tabsPending = false;
  let node = tabsEl.firstChild;
  for (const id of canvasOrder) {
    const t = tabEls.get(id);
    if (node === t) { node = node.nextSibling; continue; }
    tabsEl.insertBefore(t, node);
  }
  if (tabsEl.lastChild !== addTabEl) tabsEl.appendChild(addTabEl);
  // At 20 tabs the strip overflows, and the scrollbar is hidden (.tabs { scrollbar-width: none }) — with the
  // current tab off-screen the strip looks like nothing is selected (measured: after crossing over via a canvas
  // badge in the left list, .tab.cur was at x=-1379).
  // Scroll it into view **only at the moment the current tab changes** — every frame would undo a strip a person pushed.
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
  }, () => renderTabs());   // release the reordering deferred while editing
}

addTabEl.addEventListener('click', newCanvas);   // a broadcast follows too, but it is idempotent by id (protocol.md)

// Reorder by dragging. Cross a neighbour's midpoint and the position changes; the order is sent once on drop.
// Reading rectangles here is not a scroll handler — a different place from the minimap's ban (reading layout on every scroll).
function tabDrag(tabEl) {
  tabEl.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 || ev.target.closest('.ed, .cx')) return;
    const startX = ev.clientX;
    let dragging = false, aborted = false;
    const move = (e2) => {
      // If the dragged element's canvas was removed meanwhile (another browser's DELETE), this element has already
      // fallen out of the strip. Not giving up here puts the detached element back and leaves a ghost in the strip.
      if (tabEl.parentNode !== tabsEl) { aborted = true; up(); return; }
      if (!dragging) {
        if (Math.abs(e2.clientX - startX) < 4) return;   // tells a press from a drag
        dragging = true;
        tabEl.classList.add('drag');
      }
      // Work out the destination in one step: before the **first** neighbour whose midpoint is right of the finger.
      // If there is none, the very end (before ＋). Swapping with one neighbour at a time only moves one slot when
      // a single move crosses several (measured).
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
      // Release the ordering deferred during the drag — including when it was given up (then the daemon's order is the source of truth)
      if (!dragging || aborted) { if (tabsPending) renderTabs(); return; }
      tabDragged = true;
      setTimeout(() => { tabDragged = false; }, 0);      // the click right after a drop is not a switch
      const order = [...tabsEl.querySelectorAll('.tab')].map((e) => e.dataset.id);
      if (order.join(',') === canvasOrder.join(',')) { if (tabsPending) renderTabs(); return; }
      try {
        setCanvases(await api('POST', '/api/canvases/order', { order }));
      } catch (e) {
        // A 409 means somebody created or removed one meanwhile — we already took that event, so redrawing is enough
        toast(['reorder: ' + e.message]);
        renderTabs();
      }
    };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
    addEventListener('pointercancel', up);
  });
}

// ── minimap (⑩ ⑪, spike J) ─────────────────────────────
// **There is one, for the canvas being looked at** — not one per canvas (⑪). It answers one question: "where am I
// inside this canvas". The rectangles' positions come from the coordinate store (layout); the DOM is never asked.
// **All the scroll handler does is a transform on the viewport rectangle** (compositing only). Spike J measured
// that reading layout would not hurt (60fps at 6000×4000 with 40 panes), but the reason it is written this way is
// the rule, not performance (AGENTS.md "창마다 값을 내리지 말고 스토어에서 직접 읽어라").
const mmRects = new Map();          // id → minimap rectangle DOM
let mmK = 1, mmOx = 0, mmOy = 0;    // scale and the centring margins
let cvW = 0, cvH = 0;               // canvas viewport size — held so it is not re-measured during a scroll

function mmSet(id, x, y, w, h) {    // place a rectangle using only values we already know
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
  // The world is as big as the canvas has grown (⑩: no limit) — it never gets smaller than the viewport
  let worldW = cvW, worldH = cvH;
  for (const t of list) {
    const r = layout[t.id];
    worldW = Math.max(worldW, r.x + r.w + GAP);
    worldH = Math.max(worldH, r.y + r.h + GAP);
  }
  mmK = Math.min(bw / worldW, bh / worldH);   // one scale for both axes. Stretch them apart and the shapes lie
  mmOx = MM_PAD + (bw - worldW * mmK) / 2;
  mmOy = MM_PAD + (bh - worldH * mmK) / 2;
  for (const t of list) {
    // The colors are the same --st-* as the status dots (⑥). No new colors were made for the minimap.
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
// The whole of the scroll handler. It reads scrollLeft/scrollTop and writes a transform. It never reads layout.
function mmMove() {
  mmVpEl.style.transform =
    'translate(' + (mmOx + cvScroll.scrollLeft * mmK) + 'px, ' + (mmOy + cvScroll.scrollTop * mmK) + 'px)';
}
cvScroll.addEventListener('scroll', mmMove, { passive: true });

// Press or drag to move the viewport. The minimap box's rectangle is measured once on press (never re-read during the drag).
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

// ── left rail: session list ─────────────────────────────
function msgText(s) {
  // The mockup's one line reads "what happened · last line". What happened is the hook event name (last_event);
  // the last line comes from the terminal buffer.
  const t = tiles.get(s.id);
  const parts = [];
  // A wait this pane left with nobody typing here is not one palmar can call your approval (#14).
  if (s.answered_elsewhere) parts.push('answered — not here');
  else if (s.last_event) parts.push(EVENT_PHRASE[s.last_event] || s.last_event);   // #24: event name → human words
  if (t && t.lastLine) parts.push(t.lastLine);
  if (parts.length) return parts.join(' · ');
  // Do not leave something with no hooks quietly grey (AGENTS.md principle 3) — say it in words
  if (s.status === 'unknown') return s.agent ? 'no hook event yet' : 'no hook seen — status unknown';
  return 'just opened';
}
// The confirm strip on a list row. Which row is open has to be held, or it does not survive a rebuild.
function openRowConfirm(it, s) {
  const h = askClose(it, 'Close this terminal?', () => closeSession(s.id),
                     () => { if (rowConfirm === s.id) rowConfirm = null; });
  if (h) rowConfirm = s.id;
}
function buildItem(s, pinned) {
  const cls = STATUS_CLASS[s.status] || 'idle';
  const t = tiles.get(s.id);
  // pinned: a row that stays even inside a folded canvas group — what is waiting is never hidden (#31 ③, decisions.md ⑪)
  const it = el('div', 'ses ' + cls + (s.id === focused ? ' cur' : '') +
                      (pinned ? ' pinned' : '') + (closing.has(s.id) ? ' closing' : ''));
  it.dataset.id = s.id;
  if (pinned) it.title = 'waiting on you — kept visible while this group is collapsed';
  it.appendChild(el('span', 'dot ' + cls));
  // ⑫ A name a person gave wins. Without one, the path label as before.
  const who = el('span', 'who');
  if (s.name) who.textContent = s.name;
  else { who.textContent = (s.agent || 'shell') + ' '; who.appendChild(el('span', null, shortPath(s.cwd))); }
  const ago = el('span', 'ago');
  // ⑪ Something on another canvas gets a canvas label — in **the same slot** as "↗ off". The two never appear
  // together: whether a window on another canvas is off-screen on this canvas is not a question anyone asks.
  // **Not while drawing grouped by canvas** — the header just above already names that canvas.
  // Say the same thing twice and the line between header and row blurs (user report 2026-09-08).
  if (!inCanvasGroup && current !== null && s.canvas !== current) {
    const label = canvasLabel(canvasById(s.canvas));
    const b = el('span', 'cvb', label);
    b.title = 'in ' + label + ' — click to go there';
    ago.appendChild(b);
  } else if (t && t.off) ago.appendChild(el('span', 'off', '↗ off'));
  it._ago = document.createTextNode(agoText(s.id));
  it._agoEl = ago;              // held so only "↗ off" can be flipped during a scroll (paintOff)
  ago.appendChild(it._ago);
  it._msg = el('span', 'msg', msgText(s));
  // #31 ④ Close from a list row too — the user pointed at both places. **The same box and the same question** as
  // the tile's, normally invisible and appearing when a hand reaches the row (hover) or focus enters it.
  // Being a <button>, it is reachable with Tab.
  const cl = el('button', 'cl');
  cl.type = 'button';
  cl.title = 'close terminal';
  cl.setAttribute('aria-label', 'close terminal — ' + (s.name || shortPath(s.cwd)));
  cl.addEventListener('click', (ev) => { ev.stopPropagation(); openRowConfirm(it, s); });
  it.append(who, ago, it._msg, cl);
  // The list is rebuilt wholesale on every session frame — restore an open confirm strip here (otherwise one hook makes it vanish)
  if (rowConfirm === s.id) openRowConfirm(it, s);
  it.addEventListener('click', () => goToSession(s.id));
  return it;
}
// Flip only "↗ off", in place. It is called from the scroll path, so it never rebuilds the list (see refreshOff).
// It uses the same rule as buildItem: something on another canvas gets a canvas label and no "↗ off" (⑪).
function paintOff(id) {
  const it = items.get(id), t = tiles.get(id), s = sessions.get(id);
  if (!it || !t || !s) return;
  const want = t.off && !(current !== null && s.canvas !== current);
  const has = $('.off', it._agoEl);
  if (want && !has) it._agoEl.insertBefore(el('span', 'off', '↗ off'), it._ago);
  else if (!want && has) has.remove();
}
// While searching, folds are ignored — otherwise a match hides inside a folded group and cannot be found
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

// ── list groups (#31 ③) ────────────────────────────────
// **Group by canvas — and yet what is waiting can never be hidden.**
//
// These two started out opposed. When the mockups were compared, the argument against canvas-first grouping was
// exactly "what is waiting gets buried inside a folded group", and **"what is waiting is on top, wherever it is"
// is the reason palmar exists** (decisions.md ⑪). Daily use made the user ask for canvas grouping (#31), so it
// groups — but the guarantee is held in two layers:
//   1. **A canvas group holding a waiting session floats to the top.** The rest keep the canvas order the
//      daemon gave (Array.sort is stable, so ties do not shuffle).
//   2. **A folded group still draws its waiting rows.** The fold hides only the rest, and how many are hidden is
//      said in one line under the group, "+N more, collapsed". The count in the header is always **the whole canvas**.
// So even folded, the waiting rows stay near the top of the list — it takes both layers failing to bury one.
// One row per session (no separate copy pinned above) — the same thing seen twice makes the counts lie.
let inCanvasGroup = false;    // are we drawing grouped by canvas — decides whether a row's canvas label is dropped
function renderByCanvas() {
  const buckets = new Map();
  for (const id of canvasOrder) buckets.set(id, []);
  for (const s of sessions.values()) {
    const k = buckets.has(s.canvas) ? s.canvas : OTHER_KEY;
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k).push(s);
  }
  const keys = [...buckets.keys()].filter((k) => buckets.get(k).length);
  // **The order is not changed.** The same order as the tab strip, exactly as the daemon gave it.
  // It used to lift a canvas that wanted you to the top (⑪'s "what is waiting is never missed"). Four other things
  // carry that guarantee now — the dot on the tab, the dot on the header, the waiting rows that stay even in a
  // folded group, and the notification when the window is covered. Meanwhile the cost kept being paid: **a list
  // your hand is on jumps in front of you.** A list that stays put is easier to scan, and the user asked for that (2026-09-08).
  for (const k of keys) {
    const arr = buckets.get(k);
    arr.sort((a, b) => statusRank(a) - statusRank(b) || a.created - b.created);   // inside a group, status order
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
    // The dot on a header means the same thing and wears the same color as the dot on a tab (protocol.md "the dot
    // on a tab"): something in here wants you. No new colors (⑥). When folded, this dot is **the only marker
    // pointing inside the fold**.
    const wc = wantClass(arr);
    if (wc) g.appendChild(el('span', 'dot ' + wc));
    g.appendChild(el('span', 'ct', String(arr.length)));       // folded or not, the count is **the whole canvas**
    g.addEventListener('click', () => toggleCvGroup(k));
    listEl.appendChild(g);
    for (const s of shown) {
      inCanvasGroup = true;
      const it = buildItem(s, off);
      inCanvasGroup = false;
      it.classList.add('cvrow');        // a row that belongs to a canvas group — indented one step
      items.set(s.id, it);
      listEl.appendChild(it);
    }
    if (arr.length > shown.length) {
      // Say here **how many of the hidden ones want you**. done does not stay as a row inside a folded group
      // (keeping it as a row would make the fold pointless), so this count and the header's dot stand in for it.
      const more = el('div', 'grest' + (hiddenWant ? ' wants' : ''),
                      '+' + (arr.length - shown.length) + ' more, collapsed'
                      + (hiddenWant ? ' · ' + hiddenWant + ' want' + (hiddenWant > 1 ? '' : 's') + ' you' : ''));
      more.title = 'expand ' + label;
      more.addEventListener('click', () => toggleCvGroup(k));
      listEl.appendChild(more);
    }
  }
}

// On a daemon that sent no canvases at all (the case where renderTabs takes the tab strip down), group by status
// the old way. **Here too what is waiting cannot be hidden** — that one group does not fold (below).
function renderByStatus() {
  const by = { waiting: [], working: [], done: [], idle: [] };
  for (const s of sessions.values()) (by[s.status] || by.idle).push(s);
  for (const k in by) by[k].sort((a, b) => a.created - b.created);   // rows do not move — creation order
  for (const [key, cls, label] of GROUPS) {
    const arr = by[key];
    if (!arr.length) continue;
    // **The waiting group does not fold.** In a canvas group the waiting rows survive a fold, but here the whole
    // group is what is waiting, so folding it hides every waiting thing at once — decisions.md ⑪ says "what is
    // waiting can never be hidden" with no conditions, and this path was the only one missing that guarantee.
    // No caret is drawn at all: a caret that is absent is more honest than one that does nothing when pressed.
    // By the contract there is never a moment with zero canvases (protocol.md: the last one gets 409), so today's
    // daemon never reaches this path — but a daemon that does not know canvases (the Bun build to come) has no
    // reason to lose that guarantee too.
    const pin = key === 'waiting';
    // Fold and unfold use the same caret and the same gesture as the tree on the right. Folded, **the count stays**.
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
  // #31 ④: rebuilding the list with a confirm strip open **drops that strip's focus onto body.**
  // The `yes.focus()` in askClose, reached through the openRowConfirm buildItem calls, does nothing because the row
  // is not attached to the document yet (focus() on a detached element is ignored). The strip still looks right
  // while being dead — integration measurement 2026-09-08: with a confirm open, **one hook on a different session**
  // was enough that Enter no longer pressed Close (0 DELETEs) and Escape went to the document instead of the strip.
  // Hook and resize broadcasts arrive constantly, so the keyboard path to close (the reason #31 ④ uses a <button>)
  // effectively disappears. So measure **before** the rebuild whether that strip held focus, and hand it back
  // **after** everything is attached. If it did not hold focus, do not touch it — never take somebody else's focus
  // (the search field, a terminal).
  //
  // **Measure which button it was, too.** Always handing it back to Close moves a hand resting on Cancel over to
  // Close on one broadcast, and the same Enter turns from giving up into destroying — measured 2026-09-08 (f3.py):
  // with focus on Cancel, **one hook on a different session** moved it to `cbtn yes`/'Close', and Enter there sent
  // `DELETE /api/sessions/<id>` and killed the session. The tile title bar is not rebuilt, so this cannot happen
  // there (in the same measurement it stayed on 'Cancel') — only the list needs this.
  const keepEl = (rowConfirm && document.activeElement && document.activeElement.closest &&
                  document.activeElement.closest('#list .cfm')) ? document.activeElement : null;
  const keepYes = keepEl ? keepEl.classList.contains('yes') : false;
  listEl.textContent = '';
  items.clear();
  if (canvasOrder.length) renderByCanvas(); else renderByStatus();
  // #31 ④: **if that row was not drawn this pass, the confirm is over.** A folded canvas group draws only the
  // waiting rows (renderByCanvas), so one hook pushing that session out of waiting quietly removes the row. Not
  // taking it back here leaves rowConfirm set, and **the moment that session becomes waiting again** buildItem
  // revives a destructive confirm strip nobody asked for (measured 2026-09-08, f1.py: the revived Close sat where
  // elementFromPoint picked it up, and it was on exactly the waiting row the user was going to press to answer).
  // Screening with `document.contains` matters — the tile's confirm stays put across a list rebuild.
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

// ── search (⌘K): filter session rows and folder rows by text ──
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
  // The tree side is findDirs's job below — **filtering only the rows already expanded was not a search.**
}

// The field up top says it searches "sessions and folders". It used to filter **only the rows already expanded**,
// so on a freshly opened screen folders were effectively unfindable — a promise the screen did not keep.
// Now it asks the daemon (`GET /api/dirs?find=`). Pasting a path in whole is answered the same way.
let findSeq = 0, findTimer = null;
function findDirs() {
  const q = searchEl.value.trim();
  clearTimeout(findTimer);
  if (!q) { findResults = null; renderTree(); return; }
  // A person is still typing. Wait until they stop, then ask once.
  findTimer = setTimeout(async () => {
    const mine = ++findSeq;
    try {
      const r = await api('GET', '/api/dirs?find=' + encodeURIComponent(q));
      if (mine !== findSeq) return;            // more was typed meanwhile — throw the late answer away
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
  if (q !== hadQuery) { hadQuery = q; renderList(); }   // ignoring folds turns on or off — rebuild the list
  else applyFilter();
  findDirs();
}
searchEl.addEventListener('input', onSearch);
addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); searchEl.focus(); searchEl.select(); }
  // **⌘T·⌘N cannot be used** — the browser takes them and preventDefault does not hold (new tab, new window).
  // Enter is free, and Shift alone separates "one more" (a terminal) from "something bigger" (a canvas).
  if (mod && e.key === 'Enter' && !e.altKey) {
    e.preventDefault();
    if (e.shiftKey) newCanvas(); else newTerminal();
  }
  if (e.key === 'Escape' && e.target === searchEl) { searchEl.value = ''; onSearch(); searchEl.blur(); }
});

// ── signals that reach outside (#40) ──────────────────────
// The status lights are only worth anything while palmar is being looked at. With an editor on top they reach
// nobody — and that is exactly the moment the lights are needed. They go out in two layers:
//   tab title·favicon — a glance while the browser is visible. Free.
//   notifications — an interruption when the window is covered. 127.0.0.1 is a secure context, so no HTTPS needed
//          (measured 2026-09-08: isSecureContext=true, Notification.permission='default').
const LS_NOTIFY = 'palmar.notify';
const NOTIFY_COALESCE_MS = 500;
//: The "wants you" statuses. **done is in too** — an agent with no hooks cannot produce waiting (#38 reads the
//: title, so only working/done/idle come out), and the codex used at work is exactly that case (#15).
const WANTS_YOU = new Set(['waiting', 'done']);

let notifyOn = false;
try { notifyOn = localStorage.getItem(LS_NOTIFY) === '1'; } catch (e) {}

function labelOf(s) { return s ? (s.name || shortPath(s.cwd)) : '?'; }

// The status class of the most urgent "wants you" in this bunch. null if there is none.
// **Do not look at waiting alone** — an agent read through its title (#38) cannot produce waiting and arrives as done.
// Looking only at waiting is why the tab and the folded group stayed dark when codex finished its work (user report 2026-09-08).
function wantClass(list) {
  let d = null;
  for (const s of list) {
    if (s.status === 'waiting') return 'wait';   // the one that is blocked always wins
    if (s.status === 'done') d = 'done';
  }
  return d;
}
function wantsYouIds() {
  const out = [];
  for (const s of sessions.values()) if (WANTS_YOU.has(s.status)) out.push(s.id);
  return out;
}

// The tab title and the favicon. **Colors are read out of the CSS --st-*** — no new color lives in JS (AGENTS.md).
const favEl = document.querySelector('link[rel="icon"]');
let badgeKey = null;
function renderBadge(force) {
  const n = wantsYouIds().length;
  const key = n + '|' + (document.documentElement.dataset.theme || 'system');
  if (!force && key === badgeKey) return;      // do not redraw the canvas on every session frame
  badgeKey = key;
  document.title = n ? '(' + n + ') palmar' : 'palmar';
  document.body.classList.toggle('wants', n > 0);     // light the last dot of the wordmark
  if (!favEl) return;
  const css = getComputedStyle(document.documentElement);
  // The favicon is the brand's `p` too (docs/brand/p.svg) — an amber dot inside means somebody is waiting, and
  // that is **the same rule and the same color** as the dot on the wordmark and on a tab. It does not say how
  // many: at 16px a number is unreadable, and this is a call, not a gauge.
  // Why not an SVG favicon: support varies by browser, and being isolated it cannot read the CSS variables either.
  // So the same path goes into a Path2D and is baked onto a canvas — a PNG shows up everywhere.
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const g = c.getContext('2d');
  if (!g) return;
  const ink = (css.getPropertyValue('--ink') || '').trim() || '#222';
  // p.svg's viewBox is "2 46 60 94". Fit by height and centre horizontally.
  const k = 64 * 0.88 / 94;
  g.translate((64 - 60 * k) / 2 - 2 * k, (64 - 94 * k) / 2 - 46 * k);
  g.scale(k, k);
  g.strokeStyle = ink; g.lineWidth = 11; g.lineCap = 'round'; g.lineJoin = 'round';
  try {
    g.stroke(new Path2D('M 13.50 57.50 V 128.50'));
    g.stroke(new Path2D('M 13.50 76.00 a 18.50 18.50 0 1 0 37.00 0 a 18.50 18.50 0 1 0 -37.00 0'));
  } catch (e) { return; }                            // no Path2D — leave the favicon alone
  g.beginPath(); g.arc(33, 76, 6, 0, Math.PI * 2);
  if (n) { g.fillStyle = (css.getPropertyValue('--st-wait') || '').trim() || '#d99a2b'; g.globalAlpha = 1; }
  else   { g.fillStyle = ink; g.globalAlpha = 0.34; }
  g.fill();
  try { favEl.href = c.toDataURL('image/png'); } catch (e) {}
}

const notifyQueue = new Set();
let notifyTimer = null;
let notifyAt = 0;              // when it actually rang last

// Called **only at the moment the status changes into one**. Ring again while a status persists and people
// turn notifications off entirely.
function onWantsYou(id) {
  if (!notifyOn || !('Notification' in window) || Notification.permission !== 'granted') return;
  // If palmar is being looked at, the status lights are enough. hasFocus catches both "another window is on top"
  // and "another tab is up" — visibilityState stays 'visible' with the window covered, so it is no use here.
  if (document.hasFocus()) return;
  notifyQueue.add(id);
  // **The first one rings immediately.** Handing it to a timer to coalesce made it late exactly when the window
  // was down — when a notification is needed most: Chrome defers setTimeout in a hidden tab by 1 second, and up to
  // a minute once it has been hidden a while. Only the ones that follow are bundled by the timer (same tag, so
  // each replaces the notification before it).
  if (Date.now() - notifyAt > NOTIFY_COALESCE_MS) { flushNotify(); return; }
  if (notifyTimer === null) notifyTimer = setTimeout(flushNotify, NOTIFY_COALESCE_MS);
}

function flushNotify() {
  if (notifyTimer !== null) { clearTimeout(notifyTimer); }
  notifyTimer = null;
  const ids = [...notifyQueue].filter((id) => {
    const s = sessions.get(id);
    return s && WANTS_YOU.has(s.status);      // resolved itself meanwhile — do not ring
  });
  notifyQueue.clear();
  if (!ids.length || document.hasFocus()) return;
  const one = ids.length === 1 ? sessions.get(ids[0]) : null;
  let n;
  try {
    n = new Notification(
      one ? labelOf(one) + ' wants you' : ids.length + ' terminals want you',
      { body: one ? one.cwd : ids.map((i) => labelOf(sessions.get(i))).join(', '),
        tag: 'palmar-wants-you' });          // same tag, so they replace rather than pile up
  } catch (e) { return; }
  notifyAt = Date.now();
  // A click that does not land on that terminal amounts to saying "go find it", which leaves the original problem standing.
  n.onclick = () => { window.focus(); goToSession(ids[0]); n.close(); };
}

// **Ring once when it is turned on.** A notification has to pass browser permission, the OS's do-not-disturb and
// focus assistance before it arrives, and blocked at any of those it is equally silent on screen. One send checks
// the whole chain at once — better to know now than to discover "I turned it on and nothing comes" later.
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
  // Ask only when there is a gesture turning it on — asking for permission the moment a page loads is the thing
  // everybody hates, and the browser demands a gesture anyway. Permission is per **origin, port included**, so
  // changing --port asks again.
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

// ── protocol version ────────────────────────────────────
// The version this page speaks. It pairs with PROTOCOL in the daemon's palmar/__init__.py.
// **While it is cloned and run from one place they cannot drift** — daemon and page are the same commit.
// Once it starts being deployed that changes: a new page cached by the browser meets a daemon that was not updated.
// When that happens, **say so** instead of behaving oddly in silence. Do not block — it usually still works, and
// blocking would block the way out (a reload, a daemon restart) along with it.
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

// ── what happened ─────────────────────────────────────────
// The status lights speak about **now**. The time you were away was kept nowhere — what finished, what asked, in
// what order. The daemon holds that (the time with the browser closed is precisely "the time you were away") and
// hands it over with hello on attach. All this side does is show it and take a click through to it.
const LS_SEEN_AT = 'palmar.seenAt';
const ACT_MAX = 60;                 // how many are kept on screen. The daemon holds more.
const ACT_CLASS = { waiting: 'wait', done: 'done', created: 'idle', gone: 'idle' };
let acts = [];
let seenAt = 0;
try { seenAt = Number(localStorage.getItem(LS_SEEN_AT)) || 0; } catch (e) {}
const actsEl = document.getElementById('acts');
const actNewEl = document.getElementById('act-new');

// **When it counts as seen**: while this window is in front. What piled up while it was covered is "the time you were away".
function markSeen() {
  if (!document.hasFocus()) return;
  seenAt = Date.now() / 1000;
  try { localStorage.setItem(LS_SEEN_AT, String(seenAt)); } catch (e) {}
}

// **It answers "what do I do now".** It started out as the place that wrote down "what happened while I was
// away", but that is the past and not the question a person actually asks — in front of eight terminals the
// question is *what is waiting on me, and which of those comes first*. The status lights only get as far as "what".
//
// Three bunches:
//   waiting — what wants you. **The longest wait is on top.** A pane that has waited 20 minutes and one that just
//            asked are not the same job.
//   looks stuck — says it is working but has printed nothing for more than STUCK_S. Nobody else reports this, and
//            we can know it because we hold the PTY. **It does not assert** — it may be thinking hard.
//   nothing else at all — only then does it show the past. It is worth something only when things are quiet.
const STUCK_S = 300;      // 5 minutes. Shorter and ordinary thinking time becomes "stuck"

//: The **time** of the last output. The `quiet` the daemon sends is the value at the moment that frame was built,
//: so while things go quiet no broadcast arrives and it stands still — turn it into a time on arrival and the clock runs from there.
const lastOutAt = new Map();
function quietFor(x) {
  const t = lastOutAt.get(x.id);
  return t === undefined ? null : Math.max(0, Date.now() / 1000 - t);
}

// ── the offer left by a daemon that stopped ────────────────────────────────────────────────
// The shells are gone and cannot come back (⑦=b — no handoff, and that stands). What can come back
// is **where you were**: each pane's name and the folder it was in when the daemon stopped, which is
// the folder you had `cd`-ed to, not the one it opened at. Canvases are already back — they are data.
const restoreEl = $('#restore');

// Keep the end of a path and drop the front. The rail is narrow and the tail is the part that says
// which folder this is — `…/deep/nested` tells you something, `~/Users/very/long/…` does not.
function tailPath(p, max = 26) {
  return p.length <= max ? p : '…' + p.slice(-(max - 1));
}

function renderRestore(offer) {
  if (!restoreEl) return;
  restoreEl.textContent = '';
  const ss = offer && offer.sessions;
  if (!ss || !ss.length) { restoreEl.hidden = true; return; }
  restoreEl.hidden = false;
  const head = el('div', 'rs-h', ss.length + (ss.length === 1 ? ' terminal from before' : ' terminals from before'));
  restoreEl.appendChild(head);
  // **Two lines, name first.** One line each put the name and the path in the same row and the rail
  // is narrow: the path won and the names came out as "a…" and "w…". The name is what tells one pane
  // from another, so it gets the line, and the folder sits under it in faint ink — the same shape the
  // left list already uses for a session row.
  for (const x of ss.slice(0, 8)) {
    const row = el('div', 'rs-r');
    row.append(el('div', 'rs-n', x.name || shortPath(x.cwd)));
    if (x.name) row.append(el('div', 'rs-p', tailPath(shortPath(x.cwd))));
    restoreEl.appendChild(row);
  }
  if (ss.length > 8) restoreEl.appendChild(el('div', 'rs-r', el('div', 'rs-p', '+ ' + (ss.length - 8) + ' more')));
  const act = el('div', 'rs-a');
  const yes = el('button', 'rs-y', 'Open again');
  const no = el('button', 'rs-n2', 'Dismiss');
  // **The daemon does it, so every open browser follows along.** Restoring in one tab and leaving
  // another showing the offer would be two truths about one workspace.
  yes.addEventListener('click', async () => {
    yes.disabled = no.disabled = true;
    yes.textContent = 'opening…';
    try {
      const made = await api('POST', '/api/restore');
      renderRestore(null);
      // The panes arrive as broadcasts, not in this reply — wait for the tiles, then place them.
      const cvs = [...new Set((made || []).map((x) => x.canvas))];
      setTimeout(() => { for (const c of cvs) arrangeCanvas(c); }, 400);
    }
    catch (e) { toast(['could not restore — ', { d: String(e.message || e) }]); yes.disabled = no.disabled = false; yes.textContent = 'Open them again'; }
  });
  no.addEventListener('click', async () => {
    no.disabled = true;
    try { await api('DELETE', '/api/restore'); } catch (e) {}
    renderRestore(null);
  });
  act.append(yes, no);
  restoreEl.appendChild(act);
}

function renderActs() {
  if (!actsEl) return;
  actsEl.textContent = '';
  const now = Date.now() / 1000;
  const wants = [], stuck = [];
  for (const x of sessions.values()) {
    if (WANTS_YOU.has(x.status)) wants.push(x);
    else if (x.status === 'working') { const q = quietFor(x); if (q !== null && q >= STUCK_S) stuck.push(x); }
  }
  wants.sort((a, b) => (a.since || now) - (b.since || now));      // the longest wait on top
  stuck.sort((a, b) => (quietFor(b) || 0) - (quietFor(a) || 0));
  const fresh = acts.filter((e) => e.t > seenAt).length;
  if (actNewEl) actNewEl.textContent = wants.length ? String(wants.length) : (fresh ? fresh + ' new' : '');

  const row = (x, cls, right, title) => {
    const r = el('div', 'act' + cls);
    r.appendChild(el('span', 'dot ' + (STATUS_CLASS[x.status] || 'idle')));
    const mid = el('span', 'nm');
    mid.appendChild(el('b', null, labelOf(x)));
    mid.appendChild(document.createTextNode(' '));
    mid.appendChild(el('span', 'wt',
      x.status === 'waiting' ? 'needs you'
      : x.answered_elsewhere ? 'answered — not here'    // the daemon saw no typing in this pane (#14)
      : 'finished'));
    r.appendChild(mid);
    r.appendChild(el('span', 'ago', right));
    r.title = title;
    r.addEventListener('click', () => goToSession(x.id));
    actsEl.appendChild(r);
  };

  if (wants.length) {
    for (const x of wants) {
      const held = now - (x.since || now);
      row(x, held > 600 ? ' aged' : '', agoShort(x.since || now),
          labelOf(x) + ' · ' + x.cwd + ' — waiting ' + agoShort(x.since || now));
    }
  }
  if (stuck.length) {
    actsEl.appendChild(el('div', 'act-h', 'quiet while working'));
    for (const x of stuck) {
      const r = el('div', 'act stuck');
      r.appendChild(el('span', 'dot work'));
      const mid = el('span', 'nm');
      mid.appendChild(el('b', null, labelOf(x)));
      mid.appendChild(document.createTextNode(' '));
      mid.appendChild(el('span', 'wt', 'nothing printed'));
      r.appendChild(mid);
      const q = quietFor(x) || 0;
      r.appendChild(el('span', 'ago', agoShort(now - q)));
      r.title = labelOf(x) + ' — says it is working but has printed nothing for '
              + agoShort(now - q) + '. It may be thinking, or it may be stuck.';
      r.addEventListener('click', () => goToSession(x.id));
      actsEl.appendChild(r);
    }
  }
  if (!wants.length && !stuck.length) {
    // It is quiet. Show the past **only then** — that is the only time there is room to look at it.
    if (!acts.length) {
      actsEl.appendChild(el('div', 'quiet', 'all quiet · nothing is waiting on you'));
      return;
    }
    actsEl.appendChild(el('div', 'act-h', 'all quiet · what happened'));
    for (const e of acts.slice().reverse().slice(0, ACT_MAX)) {
      const alive = sessions.has(e.id);
      const r = el('div', 'act' + (e.t > seenAt ? ' fresh' : '') + (alive ? '' : ' dead'));
      r.appendChild(el('span', 'dot ' + (ACT_CLASS[e.kind] || 'idle')));
      const mid = el('span', 'nm');
      mid.appendChild(el('b', null, e.name || shortPath(e.cwd)));
      mid.appendChild(document.createTextNode(' '));
      mid.appendChild(el('span', 'wt', e.what));
      r.appendChild(mid);
      r.appendChild(el('span', 'ago', agoShort(e.t)));
      r.title = (e.name || e.cwd) + ' · ' + e.what + (alive ? '' : ' · this terminal is gone');
      if (alive) r.addEventListener('click', () => goToSession(e.id));
      actsEl.appendChild(r);
    }
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

// ── session updates ─────────────────────────────────────
function upsert(s) {
  const old = sessions.get(s.id);
  // A pane exists, so any restore offer is stale (the daemon stops offering the moment one does —
  // see restore_offer). The card is shown from a hello frame and would otherwise sit there while a
  // pane you opened another way already fills the canvas; pressing it would then double the panes.
  if (!old && restoreEl && !restoreEl.hidden) renderRestore(null);
  if (!old) changedAt.set(s.id, (s.created || Date.now() / 1000) * 1000);
  // Only on the transition in. Moving between waiting ↔ done inside WANTS_YOU is not a new call.
  let wants = false;
  if (old && old.status !== s.status) {
    changedAt.set(s.id, Date.now());
    wants = WANTS_YOU.has(s.status) && !WANTS_YOU.has(old.status);
  }
  if (typeof s.quiet === 'number') lastOutAt.set(s.id, Date.now() / 1000 - s.quiet);
  sessions.set(s.id, s);
  // **Call after inserting.** The notification reads sessions again to build the name and the path, and calling
  // first finds the old session still sitting there and filters it out as "nothing is calling". Deferred behind
  // the 500ms timer the insert happened in between so it never showed; it surfaced the moment it rang immediately.
  if (wants) onWantsYou(s.id);
  renderBadge();
  let t = tiles.get(s.id);
  if (!t) {
    t = new Tile(s);
    tiles.set(s.id, t);
    t.off = false;
    refreshOff();    // a window another browser opened can land off-screen — the viewport is measured once in there
  } else {
    const wasVisible = !t.el.classList.contains('other');
    t.update(s);
    const on = t.visible();
    if (on !== wasVisible) {          // ⑪ the session moved canvas — it arrives in a single session frame
      t.el.classList.toggle('other', !on);
      if (on) t.refit(); else { t.off = false; syncMax(); }   // the expanded window went to somebody else's canvas
    }
  }
  renderTabs();      // the dot is computed — recount when status or canvas changes
  renderList();
  // The right-hand list reads status too. **Not calling here leaves it one step behind** — the `log` frame arrives
  // before `session` (contract: note comes before broadcast), so what was drawn then is still the old status (the same spot as `gone`).
  renderActs();
  renderMinimap();
  updateStatusBar();
  return t;
}
function remove(id) {
  const t = tiles.get(id);
  const wasIn = t && t.s ? t.s.canvas : null;   // the tidy applies to that canvas alone
  if (t) { if (maxed === t) setMax(t, false); t.dispose(); tiles.delete(id); }
  sessions.delete(id);
  lastOutAt.delete(id);
  notifyQueue.delete(id);
  renderBadge();
  changedAt.delete(id);
  closing.delete(id);                        // #31 ①: gone arrived — this is the one place that really deletes
  // Take back any confirm strip open on this session. Leave it and rebuild the list and the strip only falls out
  // of the DOM while the pointerdown listener hung on document stays (askClose's outside).
  if (rowConfirm === id && activeConfirm) activeConfirm.cancel();
  rowConfirm = rowConfirm === id ? null : rowConfirm;
  if (activeConfirm && !document.contains(activeConfirm.row)) activeConfirm.cancel();
  delete layout[id];   // an id is never reused — leave it and it piles up
  saveLayout();
  if (focused === id) focused = null;
  // If it is on, close up automatically. Otherwise **only light the button** to say there is slack to close up —
  // it moves windows, so nothing moves unless it was asked for.
  if (wasIn && autoTidy) tidyCanvas(wasIn);
  paintTidy();
  // A row in what-happened shows "is that session still there". `log` arrives before `gone` (contract: note comes
  // before broadcast), so the row drawn then is drawn as still alive — fix it here.
  renderActs();
  renderTabs();
  renderList();
  renderMinimap();
  updateStatusBar();
}
function reconcile(list) {
  const seen = new Set(list.map((s) => s.id));
  for (const id of [...sessions.keys()]) if (!seen.has(id)) remove(id);
  // Place the ones with a remembered position first, so a new one does not take that slot
  const ordered = [...list].sort((a, b) => (layout[a.id] ? 0 : 1) - (layout[b.id] ? 0 : 1) || a.created - b.created);
  for (const s of ordered) upsert(s);
  refreshOff();
}

// ── control channel /events ─────────────────────────────
function connectEvents() {
  const ws = new WebSocket(`ws://${location.host}/events?token=${encodeURIComponent(TOKEN)}`);
  eventsWs = ws;
  let opened = false;
  ws.onopen = () => { opened = true; eventsRetry = 0; setConnected(true); };
  ws.onmessage = (ev) => {
    let m = null;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (!m) return;
    // Within one hello frame every session.canvas is in this canvases list (protocol.md) — put the canvases in first
    if (m.t === 'hello') {
      checkProtocol(m); setCanvases(m.canvases || []); reconcile(m.sessions || []);
      renderRestore(m.restore);
      acts = m.log || []; renderActs();
    }
    else if (m.t === 'log' && m.e) pushAct(m.e);
    else if (m.t === 'session' && m.s) upsert(m.s);
    else if (m.t === 'gone' && m.id) remove(m.id);
    else if (m.t === 'canvas' && m.c) putCanvas(m.c);           // created or renamed
    else if (m.t === 'canvases' && m.cs) setCanvases(m.cs);     // the order changed — all of them, in order
    else if (m.t === 'canvas_gone' && m.id) dropCanvas(m.id);   // canvases follows right behind
  };
  ws.onclose = () => {
    if (eventsWs !== ws) return;
    eventsWs = null;
    setConnected(false);
    eventsRetry = Math.min(10000, eventsRetry ? eventsRetry * 2 : 1000);
    // Closed without ever opening = either there is no daemon (connection refused) or the handshake was refused
    // (403). The latter is the daemon having restarted with a new token — the token only ever arrives with
    // index.html (protocol.md "뜨기" 4), so fetch that again and compare.
    if (!opened) checkStaleToken();
    setTimeout(connectEvents, eventsRetry);
  };
  ws.onerror = () => {};
}
// When the daemon restarts (⑦=b: only on an update) a new token is minted and this page's is 403 forever. Fetch
// index.html again and reload only when the token baked into it differs from ours — if it is the same (the daemon
// is still not there) the retries simply continue. There is no loop: after a reload the tokens match.
let staleCheck = false;
let daemonBack = false;  // has "it restarted, reload" been said once — do not repeat it
async function checkStaleToken() {
  if (staleCheck) return;
  staleCheck = true;
  try {
    // **The key is never sent automatically.** This used to do `GET /?k=` to confirm the new token and reload
    // itself — convenient, but it meant spraying the key every 10 seconds **forever** while the daemon was down.
    // Any account can grab that port (a local port is not per-user), so if another account takes the spot while
    // the daemon is off, one lunch break hands the key over hundreds of times. A key persists, so once is enough
    // (#14's trade-off).
    //
    // Instead ask **with something that carries no secret** whether anyone is alive. `/app.js` is a public file
    // that returns 200 without a key (protocol.md "인증"), so nothing rides on this request.
    const r = await fetch('/app.js', { method: 'HEAD', cache: 'no-store' });
    if (!r.ok) return;
    // Somebody is listening and yet our websocket is refused = our token is stale (the daemon restarted).
    // **Do not do the reload on their behalf** here — the request a reload makes carries the key in the address
    // too, so doing it automatically is doing again exactly what was just prevented. It goes out only when a person presses.
    if (!daemonBack) {
      daemonBack = true;
      toast([{ b: 'palmar restarted' }, ' — reload this page to reconnect']);
    }
  } catch (e) {
    // Nobody is listening — there is no daemon. The retries continue.
    daemonBack = false;
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

// ── right rail: directories (folders only, read when expanded) ──
const tree = { roots: [] };   // node: { path, name, branch, hasChildren, depth, expanded, children, loading }
let selectedDir = null;

function joinDir(parent, name) {
  // protocol.md does not pin down whether name in the roots list is an absolute path or a name to join onto path — take both
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
    renderList();        // the path display shortens to ~
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
let findResults = null;      // { q, entries } — only while searching. null means the ordinary tree.

function renderTree() {
  treeEl.textContent = '';
  const hint0 = document.getElementById('tree-hint');
  if (hint0 && !findResults) hint0.textContent = 'folders only · read when expanded';
  if (findResults) {
    // **Show the matches flat.** Expanding down into the tree loses track of where you are looking.
    const hint = document.getElementById('tree-hint');
    if (hint) hint.textContent = 'matching folders · click one to open a terminal there';
    if (!findResults.entries.length) {
      treeEl.appendChild(el('div', 'hint2', findResults.error
        ? 'search failed: ' + findResults.error
        : 'no folder matches ' + JSON.stringify(findResults.q)));
      return;
    }
    for (const e of findResults.entries) {
      // Build exactly the shape selectDir expects (branch — not git)
      const n = { path: e.name, name: e.name, depth: 0, hasChildren: !!e.has_children, branch: e.git_branch };
      const r = el('div', 'row found' + (selectedDir && selectedDir.path === e.name ? ' sel' : ''));
      r.dataset.path = e.name;
      r.tabIndex = 0;
      r.appendChild(el('span', 'nm', shortPath(e.name)));
      if (e.git_branch) r.appendChild(el('span', 'br', e.git_branch));
      r.title = e.name;
      // Pick a match and that is the cwd — no need to dig through the tree.
      const pick = () => selectDir(n);   // **the same path** as picking from the tree
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
  // Re-read only the one that is expanded — never sweep all of home. The selected folder if it is expanded, else the roots list
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
// A new terminal. **The place comes from where the hand is** — the folder of the terminal being looked at, else
// the folder picked on the right, else home (the daemon's default). That is what makes one shortcut enough:
// picking a folder in the rail is **the first time only**, and after that "one more here" is far more common.
function nextCwd() {
  // **The pane the hand is actually on comes first.** `focused` is set when a person presses the screen, so
  // moving around by keyboard alone can leave it behind — look at where the real focus is first.
  const el = document.activeElement;
  if (el) {
    for (const t of tiles.values()) {
      if (t.el.contains(el) && t.s && t.s.cwd) return t.s.cwd;
    }
  }
  const t = tiles.get(focused);
  if (t && t.visible() && t.s && t.s.cwd) return t.s.cwd;
  return selectedDir ? selectedDir.path : null;
}

async function newTerminal(cwd) {
  const body = {};
  const c = cwd || nextCwd();
  if (c) body.cwd = c;                 // without it the daemon opens at home (protocol.md)
  if (current) body.canvas = current;
  try {
    const s = await api('POST', '/api/sessions', body);
    const t = upsert(s);
    t.el.classList.add('fresh');
    setTimeout(() => t.el.classList.remove('fresh'), 2500);
    t.el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    focusTile(s.id, { user: true });
    refreshOff();
  } catch (e) {
    toast(['open terminal: ' + e.message]);
  }
}

// A new canvas. **The same path** as pressing ＋ — the two places must not behave differently.
async function newCanvas() {
  try {
    const c = await api('POST', '/api/canvases', {});
    putCanvas(c);
    switchCanvas(c.id);
    const tab = tabsEl.querySelector('.tab.cur .nm');
    if (tab) renameCanvas(canvasById(c.id), tab);
  } catch (e) {
    toast(['new canvas: ' + e.message]);
  }
}

async function launch() {
  if (!selectedDir || launchBtn.disabled) return;
  launchBtn.disabled = true;
  try {
    // PROVISIONAL — ⑪'s open question ("this canvas or that folder's canvas") is about **what fills this one
    // field**, not about the protocol (protocol.md). For now it is the canvas being looked at.
    const body = { cwd: selectedDir.path };
    if (current) body.canvas = current;
    const s = await api('POST', '/api/sessions', body);
    // The session frame from /events may arrive first, or this response may — upsert takes either
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
  // **A bare Enter only.** An Enter with a modifier belongs to the global shortcuts (Ctrl/⌘+Enter = new terminal).
  // Take both and, once a folder has been clicked, that one press opens two terminals.
  if (e.key === 'Enter' && !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey &&
      e.target && e.target.closest && e.target.closest('.rail.right')) { e.preventDefault(); launch(); }
});

// A handle for poking around from the console and dev tools. No product behaviour leans on it.
window.palmar = { sessions, tiles, canvases, layout: () => layout,
                  canvas: () => current, groups: () => groupsCollapsed,
                  cvGroups: () => cvCollapsed, closing: () => [...closing],
                  // On screen the handle appears only when removal is possible, so the path that gets refused
                  // (#18's 409) can only be exercised from the console. The daemon blocks it anyway, so having it here adds no risk.
                  removeCanvas, watchInput, newTerminal, newCanvas,
                  // Auto-tidy only runs on a pane disappearing, and that moment is hard to create from outside.
                  // Expose **the same function** the button calls, unchanged.
                  tidyCanvas,
                  lastOutAt, renderActs };

// `palmar.watchInput()` from the console. **A real IME cannot be measured headless** — CDP's imitation of
// composition passes while reports say it fails on a real machine, so let that machine print what actually arrives.
// It separates where it breaks in one go: composition never arrives · it arrives but data does not go out · it goes out but is not shown.
// Input diagnostics run straight from the screen. **No need to open dev tools** — pointing people at the console
// was itself a wall (2026-09-09). It writes down what the browser emits while typing and puts it on screen.
function recordTyping(secs) {
  const box = document.getElementById('diagbox');
  const out = document.getElementById('diag-out');
  const hint = document.getElementById('diag-hint');
  const t = tiles.get(focused) || [...tiles.values()].find((x) => x.visible());
  if (!box || !t) { toast(['open a terminal first']); return; }
  const ta = t.termEl.querySelector('textarea');
  const L = [];
  const t0 = performance.now();
  const at = () => ((performance.now() - t0) / 1000).toFixed(2).padStart(6) + 's';
  const put = (...a) => L.push(at() + '  ' + a.join(' '));
  put('browser', navigator.userAgent);
  put('terminal', t.s.name || t.s.cwd, '· textarea', !!ta);
  const off = [];
  const on = (el, type, fn) => { el.addEventListener(type, fn); off.push(() => el.removeEventListener(type, fn)); };
  if (ta) {
    on(ta, 'keydown', (e) => put('keydown  ', 'key=' + JSON.stringify(e.key),
        'code=' + e.code, 'keyCode=' + e.keyCode, 'isComposing=' + e.isComposing));
    for (const type of ['compositionstart', 'compositionupdate', 'compositionend'])
      on(ta, type, (e) => put(type.padEnd(9), 'data=' + JSON.stringify(e.data)));
    on(ta, 'input', (e) => put('input    ', 'data=' + JSON.stringify(e.data),
        'type=' + e.inputType, 'value=' + JSON.stringify(e.target.value)));
  }
  const d = t.term.onData((x) => put('→ 데몬   ', JSON.stringify(x)));
  box.hidden = false;
  out.value = '';
  hint.textContent = 'recording — click the terminal and type 안녕하십니까 …';
  const tick = setInterval(() => { out.value = L.join('\n'); out.scrollTop = out.scrollHeight; }, 400);
  setTimeout(() => {
    clearInterval(tick); off.forEach((f) => f()); d.dispose();
    out.value = L.join('\n');
    hint.textContent = 'done — press Copy and paste it back.';
    // Once diagnostics start, a person has to click the terminal right away. Move focus onto the pane for them.
  }, (secs || 15) * 1000);
  setTimeout(() => { if (t.term) t.term.focus(); }, 60);
}

function watchInput(secs) {
  const t = tiles.get(focused) || [...tiles.values()][0];
  if (!t) { console.log('palmar: 열린 터미널이 없다'); return; }
  const ta = t.el.querySelector('textarea');
  const log = [];
  const at = () => ((performance.now() / 1000).toFixed(2) + 's');
  const say = (...a) => { log.push(a.join(' ')); console.log('palmar:', ...a); };
  say('보는 중 —', secs || 20, '초. 지금 한글을 쳐 보라.');
  say('  터미널:', t.s.name || t.s.cwd, '· textarea:', !!ta, '· 포커스:',
      document.activeElement === ta ? '이 판' : (document.activeElement || {}).tagName);
  const off = [];
  for (const type of ['compositionstart', 'compositionupdate', 'compositionend', 'beforeinput', 'input']) {
    const h = (e) => say(at(), type, JSON.stringify(e.data !== undefined ? e.data : (e.target && e.target.value)));
    if (ta) { ta.addEventListener(type, h); off.push(() => ta.removeEventListener(type, h)); }
  }
  const kh = (e) => say(at(), 'keydown', JSON.stringify(e.key), 'code=' + e.code, 'isComposing=' + e.isComposing);
  if (ta) { ta.addEventListener('keydown', kh); off.push(() => ta.removeEventListener('keydown', kh)); }
  const d = t.term.onData((x) => say(at(), '→ 데몬으로', JSON.stringify(x)));
  setTimeout(() => {
    off.forEach((f) => f()); d.dispose();
    say('끝. 아래를 통째로 복사해 보내라.');
    console.log('%c' + log.join('\n'), 'font-family:monospace');
  }, (secs || 20) * 1000);
}

// ── start ───────────────────────────────────────────────
function boot() {
  if (!window.Terminal || !window.FitAddon) {
    toast(['xterm.js is missing under palmar/web/vendor/ — see palmar/web/vendor/VERSIONS']);
    return;
  }
  // The shortcut guidance has to name this machine's keys. The handling side had long taken both metaKey and
  // ctrlKey (keydown below), but the guidance alone was nailed to ⌘, so on Linux·WSL it pointed at a key that is
  // not there. The default in the HTML is Ctrl — non-Mac is the wider case, and if detection fails, not changing is safer.
  if (IS_MAC) {
    const k = document.getElementById('kmod');
    if (k) k.firstElementChild.textContent = '⌘';
    // On a Mac, copy·paste·text size are ⌘ alone — no Shift. The guidance names that machine's keys too.
    for (const el of document.querySelectorAll('.keys kbd.mod')) el.textContent = '⌘';
    // **Only the rows marked for it.** Matching on "three keys with Shift in the middle" also caught
    // Ctrl+Shift+Enter, and new canvas needs Shift on a Mac too — both Enter rows came out reading
    // ⌘⏎ and one of them was wrong.
    for (const el of document.querySelectorAll('.keys dt[data-mac-drops-shift]')) {
      const ks = [...el.querySelectorAll('kbd')];
      if (ks.length === 3 && ks[1].textContent === 'Shift') ks[1].remove();
    }
  }
  // ── shortcuts panel ──
  const helpBtn = document.getElementById('help'), keysEl = document.getElementById('keys');
  const showKeys = (on) => {
    keysEl.hidden = !on;
    helpBtn.setAttribute('aria-expanded', on ? 'true' : 'false');
  };
  if (helpBtn && keysEl) {
    helpBtn.addEventListener('click', (e) => { e.stopPropagation(); showKeys(keysEl.hidden); });
    document.getElementById('keys-x').addEventListener('click', () => showKeys(false));
    // A press outside the panel or Esc closes it. Clicks inside the panel are swallowed.
    keysEl.addEventListener('click', (e) => e.stopPropagation());
    addEventListener('click', () => { if (!keysEl.hidden) showKeys(false); });
    addEventListener('keydown', (e) => { if (e.key === 'Escape' && !keysEl.hidden) showKeys(false); });
    const dg = document.getElementById('diag');
    if (dg) dg.addEventListener('click', () => { showKeys(false); recordTyping(15); });
    const dgx = document.getElementById('diag-x');
    if (dgx) dgx.addEventListener('click', () => { document.getElementById('diagbox').hidden = true; });
    const dgc = document.getElementById('diag-copy');
    if (dgc) dgc.addEventListener('click', async () => {
      const box = document.getElementById('diag-out');
      box.select();
      const okc = await clipWrite(box.value);
      dgc.textContent = okc ? 'Copied' : 'Copy';
      setTimeout(() => { dgc.textContent = 'Copy'; }, 1600);
    });
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
  // When the window comes back to the front, **show it as new for a moment** and then mark it seen. Clearing it
  // the instant it returns leaves no time to see what happened while you were away.
  addEventListener('focus', () => setTimeout(() => { markSeen(); renderActs(); }, 4000));
  // Time passes — make 'now' visibly become '3m'. The same interval as the list's ago.
  setInterval(renderActs, 15000);   // makes the waiting time visibly run
  setNotify(notifyOn && 'Notification' in window && Notification.permission === 'granted');
  applyTheme(storedTheme());
  renderBadge(true);
  renderTabs();        // before any canvas arrives the tab strip is down — it comes up when hello arrives
  connectEvents();
  loadRoots();
}
// If xterm measures a cell before the font arrives, the column count is off. Wait a moment (≤1.5s), and fall
// back to the next font if it does not come.
const fontWait = document.fonts && document.fonts.load
  ? Promise.race([document.fonts.load(FONT_PX + 'px "JetBrains Mono"').catch(() => null), new Promise((r) => setTimeout(r, 1500))])
  : Promise.resolve();
fontWait.then(boot, boot);
})();
