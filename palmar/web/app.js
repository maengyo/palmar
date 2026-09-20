// palmar browser — the canvas UI. docs/protocol.md is the one contract with the daemon; trust only what it says.
//
// ① Nobody has picked the web stack yet. Vanilla JS here is not a decision, it is the default before one
//    (decisions.md ①: "기본값은 안 쓰는 것이고, 쓰자고 하려면 이유를 대야 한다"). No build step either.
//    Picking a framework rewrites this file — so state (sessions·tiles) and the DOM are tied as thinly as we can.
//
// Left provisional (places where a decision that belongs to a person is not pre-empted — only what it takes to run):
//   ③ position·size·z order live on the daemon (decided 2026-09-14; see saveLayout). Below stands the provisional
//      that protocol.md's "위치·크기" section describes. The daemon knows only the pane's cols·rows.
//   ⑥ the status string only becomes a CSS class (wait/work/done/idle). Colors live only in style.css's --st-*.
//      idle and unknown share the same grey slot — once the mapping is decided, only STATUS_CLASS changes.
//   ⑩ where a new window goes: scan the grid for the first free slot, else the bottom — and since the canvas
//      grows without limit, that search never fails, so an opening window has nothing to push. Push-aside
//      (#8, see pushAside below) is what a **hand** placing a window sets off. There is no overlap setting,
//      by decision. The default size DEFAULT_W/H is an undecided that hangs off ⑩ too.
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
const LS_THEME = 'palmar-theme';
// Palettes — one choice per side, the browser's taste like the theme. The defaults (paper, earth)
// are the tokens on :root and in the dark blocks and write nothing to the store.
const LS_PAL_L = 'palmar.pal-light', LS_PAL_D = 'palmar.pal-dark';
const PALETTES = { light: ['paper', 'sky', 'porcelain', 'lilac'], dark: ['earth', 'graphite', 'deep', 'midnight'] };
let palLight = 'paper', palDark = 'earth';
try {
  const l = localStorage.getItem(LS_PAL_L), d = localStorage.getItem(LS_PAL_D);
  if (PALETTES.light.indexOf(l) >= 0) palLight = l;
  if (PALETTES.dark.indexOf(d) >= 0) palDark = d;
} catch (e) {}
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
// **The default text size, and the person can change it.** 11.75 was small on a big screen, and the
// wheel moved in quarter-pixels so a tick often did nothing visible (user, 2026-09-15). A whole pixel a
// tick now, and this is a `let`: the options list writes it, panes that never set their own follow it.
let FONT_PX = 13;
const LS_FONT = 'palmar.font';
try { const v = parseFloat(localStorage.getItem(LS_FONT)); if (v >= 8 && v <= 24) FONT_PX = v; } catch (e) {}
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
    toast(['could not copy — the browser refused clipboard access:', { d: String(e.message || e) }]);
    return false;
  }
}
async function clipRead() {
  try {
    return await navigator.clipboard.readText();
  } catch (e) {
    // Chrome asks separately for read permission. If it is refused, the browser's own paste is still there.
    toast(['could not paste — the browser refused to read the clipboard;',
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
const toastEl = $('#toast'), searchEl = $('#search'), searchBox = $('#searchbox');

// **Installable as an app.** Edge and Chrome install a page as an app of its own — icon, window, Start
// Menu entry — from a web app manifest. Its start_url has to carry the key, and index.html never does
// (#14), so the link is built here from the address this page was opened with: the key is already in
// location, so nothing is exposed that was not. Without a key (an older way in) there is no link.
try {
  const k = new URLSearchParams(location.search).get('k');
  if (k) {
    const l = document.createElement('link');
    l.rel = 'manifest';
    l.href = '/manifest.webmanifest?k=' + encodeURIComponent(k);
    document.head.appendChild(l);
  }
} catch (e) {}
// The search field floats now (2026-09-15) — the same input, shown on Ctrl/⌘K or from the options
// list, so everything that reads it (the list's fold, the tree's filter) is untouched.
function showSearch(on) {
  if (!searchBox) return;
  searchBox.hidden = !on;
  if (on) { searchEl.focus(); searchEl.select(); }
  else if (searchEl.value) { searchEl.value = ''; searchEl.dispatchEvent(new Event('input')); }
}

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
let layout = {};              // ③ { id: {x,y,w,h,z,…} } — what hello brings, nothing before it
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
//: ③ **decided (2026-09-14): the layout lives on the daemon.** Positions, sizes, z, text size and group
//: membership are one object, kept in `~/.palmar/layout.json` and carried in the hello frame. Two
//: browsers on one daemon then see one arrangement: grouping in Safari and switching to Chrome used to
//: land on a board with no groups and every window somewhere else (user, 2026-09-14).
//: **The browser keeps no copy (2026-09-15).** It kept one as a hand-over for a daemon whose file was
//: empty — and localStorage is per origin, so two daemons on one address share it: the board of the
//: palmar on Windows, PDF window and all, was handed to the fresh one started in WSL (user, 2026-09-15).
//: A different key is a different daemon, and a different daemon starts with nothing.
try { localStorage.removeItem('palmar-tiles'); } catch (e) {}   // the copy older pages left behind
const CLIENT = Math.random().toString(36).slice(2, 10);   // who saved — a page ignores its own broadcast
let layoutRev = 0;
let layoutOnDaemon = null;        // null until hello says; false when the daemon predates the endpoint
//: **A watch for a bug nobody here can reproduce.** A group walks out of line over many drags — a
//: row of two becomes a staircase, a pixel or so at a time (user, 2026-09-17). Twenty long drags on
//: this machine, over other windows, in and out of the viewport, at four scale factors, never moved
//: one member relative to another. So instead of another theory: every save compares each group's
//: internal offsets with the last ones and, when they differ without the group having been joined or
//: closed up, prints the offsets and the stack that got here. The stack is the point — it names the
//: function that moved one member and not the other, which is the one thing all the guessing was for.
//:
//: Off unless `palmar.watchgroups` is set, and it only ever reads and prints.
const LS_WATCH = 'palmar.watchgroups';
let watchOn = false;
try { watchOn = localStorage.getItem(LS_WATCH) === '1'; } catch (e) {}
const watchWas = new Map();
function watchGroups() {
  if (!watchOn) return;
  const now = new Map();
  for (const t of tiles.values()) {
    const r = layout[t.id];
    if (r && r.g) { if (!now.has(r.g)) now.set(r.g, []); now.get(r.g).push([t.id, r.x, r.y]); }
  }
  for (const [g, list] of now) {
    list.sort((a, b) => (a[0] < b[0] ? -1 : 1));
    const shape = list.map(([, x, y]) => (x - list[0][1]) + ',' + (y - list[0][2])).join(' | ');
    const was = watchWas.get(g);
    const ids = list.map(([id]) => id).join(' ');
    // A member joining or leaving changes the shape for an honest reason; only a set that stayed
    // the same and moved anyway is worth a word.
    if (was && was.ids === ids && was.shape !== shape) {
      console.warn('[palmar] group ' + g + ' changed shape\n  was: ' + was.shape +
                   '\n  now: ' + shape + '\n' + new Error('here').stack);
    }
    watchWas.set(g, { ids: ids, shape: shape });
  }
  for (const g of [...watchWas.keys()]) if (!now.has(g)) watchWas.delete(g);
}
function saveLayout() {
  watchGroups();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(pushLayout, 150);
}
async function pushLayout() {
  if (layoutOnDaemon === false) return;
  try {
    const r = await api('PUT', '/api/layout', { layout, by: CLIENT });
    if (r && typeof r.rev === 'number') layoutRev = Math.max(layoutRev, r.rev);
  } catch (e) {
    if (e.status === 404) layoutOnDaemon = false;   // an older daemon: the browser store carries on alone
  }
}
// What hello brought is the board — an empty one included.
function takeLayout(m) {
  if (!m || typeof m.layout !== 'object' || m.layout === null) { layoutOnDaemon = false; return; }
  layoutOnDaemon = true;
  layoutRev = Math.max(layoutRev, m.layout_rev || 0);
  layout = m.layout; placeAll();
}
// Another browser saved. **Not while a hand is down here** — our own save follows the release and has
// the last word anyway, and moving the window under a hand is the one thing that must not happen.
function layoutArrived(m) {
  if (!m || typeof m.layout !== 'object' || m.layout === null || m.by === CLIENT) return;
  if (typeof m.rev === 'number') { if (m.rev <= layoutRev) return; layoutRev = m.rev; }
  if (document.querySelector('.tile.drag')) return;
  layout = m.layout;
  placeAll();
}
// Put every live window where the store says. Text size is left to the next open — it needs the
// terminal rebuilt, and a size somebody else set is not worth that mid-session.
function placeAll() {
  for (const t of tiles.values()) {
    const r = layout[t.id];
    if (!r || !Number.isFinite(r.x) || !Number.isFinite(r.y)) continue;
    const w = r.w >= MIN_W ? r.w : t.el.offsetWidth, h = r.h >= MIN_H ? r.h : t.el.offsetHeight;
    Object.assign(t.el.style, { left: r.x + 'px', top: r.y + 'px', width: w + 'px', height: h + 'px' });
    if (r.z) { t.el.style.zIndex = String(r.z); zTop = Math.max(zTop, r.z); }
    t.fitted = false;
    if (t.visible()) t.refit();
  }
  paintGroups();
  renderMinimap();
  refreshOff();
  paintTidy();
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
  // parts: [{b:'bold'}, 'plain', {d:'dim'}, {a:'undo', on:fn}, {spin:1}], or a string.
  // **The toast is one line of prose.** Parts are laid end to end with a space between, and the
  // punctuation is the caller's — a dash before a second clause, a colon before an error. It used to
  // be a flex row with a gap, and two plain strings in a row merged into one item with no gap at all
  // ("…:57891the browser is refusing", user, 2026-09-15).
  toastEl.textContent = '';
  let act = false;
  for (const p of [].concat(parts)) {
    if (p === '' || p == null) continue;
    if (toastEl.childNodes.length) toastEl.appendChild(document.createTextNode(' '));
    if (typeof p === 'string') toastEl.appendChild(document.createTextNode(p));
    else if (p.b != null) toastEl.appendChild(el('b', null, p.b));
    else if (p.d != null) toastEl.appendChild(el('span', 'd', p.d));
    // A ring going round, for a line that is saying "still working" rather than telling you a result.
    else if (p.spin) toastEl.appendChild(el('span', 'spin'));
    else if (p.a != null) {
      // A real button, so it is reachable by keyboard. The toast is pointer-events:none until .show,
      // which is what keeps a faded-out one from swallowing clicks on the canvas underneath.
      const b = el('button', 'undo', p.a);
      b.addEventListener('click', () => { toastEl.classList.remove('show'); if (p.on) p.on(); });
      toastEl.appendChild(b);
      act = true;
    }
  }
  toastEl.classList.add('show');
  clearTimeout(toastTimer);
  // A toast you are meant to press needs longer than one you only read.
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), act ? 9000 : 4500);
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
// Which palette is in effect right now: the dark choice when the page is dark, the light one
// otherwise. One attribute, so the CSS never has to reason about theme × palette.
function applyPalette() {
  const dark = root.dataset.theme === 'dark' || (!root.dataset.theme && darkMq.matches);
  const pal = dark ? palDark : palLight;
  if (pal === 'paper' || pal === 'earth') delete root.dataset.pal; else root.dataset.pal = pal;
}
function choosePalette(side, name) {
  if (side === 'light' && PALETTES.light.indexOf(name) >= 0) palLight = name;
  if (side === 'dark' && PALETTES.dark.indexOf(name) >= 0) palDark = name;
  try {
    if (palLight === 'paper') localStorage.removeItem(LS_PAL_L); else localStorage.setItem(LS_PAL_L, palLight);
    if (palDark === 'earth') localStorage.removeItem(LS_PAL_D); else localStorage.setItem(LS_PAL_D, palDark);
  } catch (e) {}
  applyPalette();
  if (typeof renderBadge === 'function') renderBadge(true);
  rethemeTerminals();
}
darkMq.addEventListener('change', applyPalette);   // the system flipped: the other side's choice takes over
function applyTheme(mode) {   // mode: 'light' | 'dark' | null (system)
  if (mode) root.dataset.theme = mode; else delete root.dataset.theme;
  applyPalette();
  themeBtn.dataset.mode = mode || 'system';
  // The button says "theme" and opens the list (2026-09-15), and beside it, fainter, **the state in a
  // word** — #16 still holds: three marks cannot say which is which on their own, and a title
  // attribute needs a hover nobody performs.
  const st = themeBtn.querySelector('small');
  if (st) st.textContent = mode || 'auto';
  themeBtn.title = 'theme: ' + (mode || 'follows the system') + ' — click for the list';
  markThemeMenu();
  try { if (mode) localStorage.setItem(LS_THEME, mode); else localStorage.removeItem(LS_THEME); } catch (e) {}
  if (typeof renderBadge === 'function') renderBadge(true);   // the favicon color reads --st-*
  rethemeTerminals();
}
// ── popups: one at a time ──
// Every popup closes on a press outside it, and a press on another popup's button is not "outside" —
// that button stops the event so its own popup is not closed by it — so two could be up at once
// (user, 2026-09-15). Each popup registers how it closes, and opening one closes the rest.
const popups = new Map();     // element → its close()
function registerPopup(el, close) { if (el) popups.set(el, close); }
function closePopups(keep) { for (const [el, close] of popups) if (el !== keep && !el.hidden) close(); }

// ── the theme list: System · Light (a palette) · Dark (a palette) ──
const themeMenu = $('#theme-menu');
function markThemeMenu() {
  if (!themeMenu) return;
  const mode = root.dataset.theme || null;
  for (const b of themeMenu.querySelectorAll('.tm-i')) {
    const v = b.dataset.pick;
    const on = v === 'system' ? mode === null
             : v === 'light:' + palLight ? mode === 'light'
             : v === 'dark:' + palDark ? mode === 'dark' : false;
    const chosen = v === 'light:' + palLight || v === 'dark:' + palDark;   // this side's palette, even when the other side is showing
    b.classList.toggle('on', on);
    b.classList.toggle('chosen', chosen && !on);
    b.setAttribute('aria-checked', on ? 'true' : 'false');
  }
}
function showThemeMenu(open) {
  if (!themeMenu) return;
  if (open) closePopups(themeMenu);
  themeMenu.hidden = !open;
  themeBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (open) {
    const r = themeBtn.getBoundingClientRect();
    themeMenu.style.top = (r.bottom + 6) + 'px';
    themeMenu.style.right = Math.max(8, window.innerWidth - r.right) + 'px';
    markThemeMenu();
    const cur = themeMenu.querySelector('.tm-i.on') || themeMenu.querySelector('.tm-i');
    if (cur) cur.focus();
  }
}
function pickTheme(v) {
  if (v === 'system') applyTheme(null);
  else {
    const [side, name] = v.split(':');
    choosePalette(side, name);
    applyTheme(side);
  }
  showThemeMenu(false);
  themeBtn.focus();
}
registerPopup(themeMenu, () => showThemeMenu(false));
themeBtn.addEventListener('click', (e) => { e.stopPropagation(); showThemeMenu(themeMenu && themeMenu.hidden); });
themeBtn.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); showThemeMenu(themeMenu && themeMenu.hidden); } });
if (themeMenu) {
  themeMenu.addEventListener('click', (e) => {
    e.stopPropagation();
    const b = e.target.closest('.tm-i');
    if (b) pickTheme(b.dataset.pick);
  });
  themeMenu.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { showThemeMenu(false); themeBtn.focus(); return; }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const items = [...themeMenu.querySelectorAll('.tm-i')];
    const i = items.indexOf(document.activeElement);
    items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
  });
  document.addEventListener('click', () => { if (!themeMenu.hidden) showThemeMenu(false); });
}
if (darkMq.addEventListener) darkMq.addEventListener('change', () => { if (!root.dataset.theme) rethemeTerminals(); });

// xterm takes colors as values. Read the values out of the CSS variables and hand them over — so a color never lives in two places.
//: **The sixteen colours a terminal actually prints in.** Only background, foreground and the cursor were
//: ever set, so the ANSI palette stayed xterm's own — built for a dark terminal. On a light theme a
//: program printing "bright white" (ls, prompts, spinners) drew near-white on a white tile and the typing
//: was hard to read (user, 2026-09-15: "light 테마일때 터미널에서 타이핑 하는 글자 잘 안보이는데").
//: The light set is dark-on-light throughout — brightWhite included, which is the one that matters.
const ANSI_LIGHT = {
  black: '#2a2d36', red: '#b03028', green: '#2f7d3a', yellow: '#8a6a00',
  blue: '#2f5fd0', magenta: '#8a3fa8', cyan: '#0f7c86', white: '#4b5263',
  brightBlack: '#6b7280', brightRed: '#c0392f', brightGreen: '#237a2e', brightYellow: '#9a6b00',
  brightBlue: '#3b6fe0', brightMagenta: '#9b4bbd', brightCyan: '#0e8a94', brightWhite: '#1f2229',
};
const ANSI_DARK = {
  black: '#3a3630', red: '#d4655c', green: '#6aa96f', yellow: '#d9a441',
  blue: '#7fa6e0', magenta: '#c08ad0', cyan: '#6fc2c9', white: '#ded8cd',
  brightBlack: '#8a8377', brightRed: '#e08078', brightGreen: '#86c08a', brightYellow: '#e8bb63',
  brightBlue: '#9dbcec', brightMagenta: '#d0a3dd', brightCyan: '#8fd4da', brightWhite: '#f6f1e8',
};
function isDarkNow() {
  return root.dataset.theme === 'dark' || (!root.dataset.theme && darkMq.matches);
}
function termTheme() {
  return Object.assign({
    background: cssVar('--tile'),
    foreground: cssVar('--term-ink'),
    cursor: cssVar('--accent'),
    cursorAccent: cssVar('--on-accent'),
    selectionBackground: cssVar('--accent-2'),
    selectionInactiveBackground: cssVar('--accent-2'),
  }, isDarkNow() ? ANSI_DARK : ANSI_LIGHT);
}
// The base text size, from the options list. Panes that never set their own follow it.
function setBaseFont(px) {
  const f = Math.max(FONT_MIN, Math.min(FONT_MAX, Math.round(px)));
  if (f === FONT_PX) return;
  const was = FONT_PX;
  FONT_PX = f;
  try { localStorage.setItem(LS_FONT, String(f)); } catch (e) {}
  for (const t of tiles.values()) {
    const own = layout[t.id] && layout[t.id].f;
    if (own && Math.abs(own - was) > 0.01) continue;      // this pane was set by hand — leave it
    if (layout[t.id]) { const n = Object.assign({}, layout[t.id]); delete n.f; layout[t.id] = n; }
    t.setFont(f);
  }
  saveLayout();
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
//: **One frame for every window on the canvas.** A terminal and a viewer (2026-09-15) share the title
//: bar, the grip, the placement from the board and the text-size wheel; only what fills the body differs.
//: Lifted out of Tile's constructor rather than written twice, so the two cannot drift apart.
function buildFrame(t, s) {
    const e = t.el = el('div', 'tile');
    e.dataset.id = s.id;
    // If it belongs to another canvas it is only absent from the screen — session and websocket stay alive (principle 2)
    if (current !== null && s.canvas !== current) e.classList.add('other');
    const tb = el('div', 'tb');
    t.dotEl = el('span', 'dot');
    t.nameEl = el('span', 'name');
    t.pillEl = el('span', 'st');
    t.szEl = el('span', 'sz');
    t.rnEl = el('span', 'rn'); t.rnEl.title = 'rename (or double-click the name)';
    // A viewer's two: hand the file to the machine's own program, and edit it here. Hidden on a
    // terminal by CSS — the frame is shared, so both are built either way.
    t.owEl = el('span', 'vw-open'); t.owEl.title = 'open with the system default program';
    t.edEl = el('span', 'vw-edit'); t.edEl.title = 'edit';
    t.xpEl = el('span', 'xp'); t.xpEl.title = 'expand';
    // **Wired here, in the shared frame.** It used to be wired in Tile's constructor, so a viewer drew the
    // button and nothing happened when it was pressed (user, 2026-09-15).
    t.xpEl.addEventListener('click', (ev) => { ev.stopPropagation(); setMax(t, !t.el.classList.contains('max')); });
    // #31 ① close. **The box is the same body as .rn·.xp** (they sit in one CSS rule together — there is no
    // room for size·border·hover to drift apart) and it joins the same row. Only the drawing inside differs — two strokes (×).
    // The reason no glyph (✕) is the same as for .rn: every font draws it differently (AGENTS.md).
    // The only difference is that it is a <button> — destroying has to be reachable from the keyboard too
    // (#31 ④). It looks like the spans.
    t.clEl = el('button', 'cl'); t.clEl.type = 'button';
    t.clEl.title = 'close terminal'; t.clEl.setAttribute('aria-label', 'close terminal');
    tb.append(t.dotEl, t.nameEl, t.pillEl, t.szEl, t.rnEl, t.owEl, t.edEl, t.xpEl, t.clEl);
    // #25 text size per pane. Ctrl/⌘+wheel is also browser zoom, so it must be blocked — leave it and reaching
    // to enlarge one window enlarges the whole page. A plain wheel is left alone so xterm's scrollback lives.
    // **Take it on the capture phase.** On bubble, xterm's scrollback handler eats it at the child first, and it
    // only reaches here when the scroll is already at the very top or the very bottom (user report 2026-09-08).
    e.addEventListener('wheel', (ev) => {
      if (!ev.ctrlKey && !ev.metaKey) return;
      ev.preventDefault(); ev.stopPropagation();
      t.setFont(Math.round(t.term.options.fontSize || FONT_PX) + (ev.deltaY < 0 ? FONT_STEP : -FONT_STEP));
    }, { passive: false, capture: true });
    // The size readout is the reset button — put it on something already there rather than add another button to the title bar.
    t.szEl.addEventListener('click', (ev) => { ev.stopPropagation(); t.setFont(FONT_PX); });
    t.termEl = el('div', 'term');
    t.gripEl = el('div', 'grip');
    e.append(tb, t.termEl, t.gripEl);

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
    // **Rewrite the position, keep everything else.** This line owns four numbers and a stacking
    // order. Every other field on the entry belongs to somebody else, and the way it kept going wrong
    // was a named list of what to copy back — the list is always one behind. The text size went that
    // way first (a fresh open came up at the default while the store still held `f`), then group
    // membership, then the group's **name**, which lasted until the next reload and then the board
    // forgot it (2026-09-19). Four times is enough: nothing is listed, the entry is kept and the five
    // values this line actually knows are written over it.
    layout[s.id] = Object.assign({}, saved || {}, { x, y, w, h, z });
    saveLayout();
    cvWorld.appendChild(e);
    // The frame is painted by upsert, **after** this tile is in `tiles` — painted from here it cannot
    // see itself, so the second member of a restored pair drew nothing and the group came back
    // unframed in every browser but the one that made it (measured 2026-09-14).

    return saved;
}

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

    const saved = buildFrame(this, s);
    const e = this.el, tb = e.firstChild;   // the rest of this constructor speaks of the element as `e`

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
      // A link in terminal output (OSC 8) is somebody else's text: only http(s), and opened detached
      // so the new page gets no handle on this one (review, 2026-09-15).
      linkHandler: {
        activate(e, uri) {
          let u; try { u = new URL(uri); } catch (err) { return; }
          if (u.protocol !== 'http:' && u.protocol !== 'https:') return;
          window.open(u.href, '_blank', 'noopener,noreferrer');
        },
      },
    });
    this.fit = new FitAddon.FitAddon();
    this.term.loadAddon(this.fit);
    // Say what the shortcut is **once**, the first time text is selected. The copy shortcut differs per terminal
    // (here Ctrl+C is interrupt), so there is no finding it by pressing — selecting is the moment to say it.
    this.term.onSelectionChange(() => {
      if (clipHintShown || !this.term.hasSelection()) return;
      clipHintShown = true;
      try { localStorage.setItem(LS_CLIPHINT, '1'); } catch (e) {}
      toast([CLIP_HINT + ' to copy and paste — Ctrl+C stays as interrupt, the way a terminal expects']);
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
    // ── a compositionend for a composition that never started ───────────
    // **WebKit ends compositions it never began.** `Editor::confirmComposition()` dispatches
    // `compositionend` unconditionally, with no guard on whether one was open (WebKit bug 84394,
    // ASSIGNED since 2012, reported against ibus-hangul). Normally you never see it, because a
    // preedit opened the composition first. Under wry it opens none: wry calls
    // `set_enable_preedit(false)` unconditionally (webkitgtk/mod.rs:360, added for fcitx), ibus
    // then drops IBUS_CAP_PREEDIT_TEXT, and the preedit goes to the ibus panel instead of to the
    // page — so `compositionstart` and `compositionupdate` never arrive and only the end does.
    //
    // xterm takes that end as "finish the composition you are in", and having never been told where
    // it began, sends **the whole helper textarea** — which xterm only empties on Enter or Ctrl-C.
    // Measured: typing 하이하이 sent 하 · 하이 · 하이하 · 하이하이, ten characters for four typed,
    // which is exactly what was reported from WSLg (2026-09-11).
    //
    // So an end with no start is dropped here, before xterm sees it. What remains is xterm's own
    // keyCode-229 path, which computes the one-syllable difference correctly on its own. **The
    // parent, in capture**, for the same reason as the input handler below: on the textarea itself
    // xterm's listener was registered first and would run first.
    this.compOpen = false;
    this.termEl.addEventListener('compositionstart', () => { this.compOpen = true; }, true);
    this.termEl.addEventListener('compositionend', (ev) => {
      if (!this.compOpen) { ev.stopPropagation(); return; }
      this.compOpen = false;
    }, true);

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
      if (it !== 'insertText' && it !== 'insertReplacementText' && it !== 'insertFromComposition') return;
      // **Tell a plain key from an IME key by their order.** A plain key goes keydown → (xterm sends) → input,
      // an IME key goes input → keydown (measured: space fires keydown first, Korean fires input first).
      // A keydown with keyCode 229 does not raise the flag, so no flag here means IME.
      const plain = this.kdSeen;
      this.kdSeen = false;
      if (plain) return;                      // xterm already sent it on keydown
      ev.stopPropagation();                   // block xterm's _inputEvent — we send it
      const d = ev.data || '';
      if (it === 'insertFromComposition') {
        // **Already final.** This is what WebKitGTK sends for an IME commit when there is no preedit
        // (wry turns preedit off), so unlike the two below there is nothing still being composed and
        // nothing to hold back. Measured on WSLg: without this the event fell through, nothing here
        // sent it, and the only thing that did was xterm finishing a composition it never started —
        // which sent the whole textarea (see the compositionend note above).
        if (this.imePending) { this.sendText(this.imePending); this.imePending = ''; }
        this.sendText(d);
        // WebKitGTK never empties the helper textarea, and anything that reads its value later reads
        // a buffer that only grows. Nothing should be left behind in it.
        if (ev.target && ev.target.value) ev.target.value = '';
        return;
      }
      if (it === 'insertReplacementText') {
        this.imePending = d;                  // the character being composed changed — do not send yet
      } else {
        if (this.imePending) this.sendText(this.imePending);   // the previous character is committed
        this.imePending = d;
      }
    }, true);
    this.gl = tryWebgl(this.term, () => { this.gl = null; updateStatusBar(); });
    // Measuring while hidden (another canvas) gives 0 columns — refit() measures when it becomes visible.
    // **And not in this tick even when it is visible.** Straight after `open()` the renderer has not
    // measured a cell yet, so `fit()` computes a size and leaves the terminal at its 80×24 default —
    // while the old code set `fitted = true` regardless, which is the flag the one "fit this pane
    // later" path checks. So it never ran and the pane stayed 24 rows tall inside a 20-row box:
    // three and a half rows below the visible area, and scrolling to the bottom did not reach the
    // cursor until something resized the window (measured 2026-09-14: propose 67×19, term 80×24,
    // screen 384px in a 328px viewport). Next frame, once layout has happened.
    this.fitted = false;
    this.fitTries = 0;
    if (this.visible()) requestAnimationFrame(() => { if (!this.closed) this.refit(); });
    this.watchFit();

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

    this.rnEl.addEventListener('click', (ev) => { ev.stopPropagation(); this.rename(); });
    this.clEl.addEventListener('click', (ev) => { ev.stopPropagation(); this.askClose(); });
    // Double-click on the name is caught in dragify's down(), not here: the title bar takes pointer
    // capture on pointerdown, and a captured pointer's click and dblclick are retargeted to the
    // capturing element — a dblclick listener on the name itself never fires (measured 2026-09-15).
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

  //: Is the terminal standing taller than the box holding it — which means its bottom rows, and the
  //: prompt among them, are clipped with no way to scroll to them.
  overflows() {
    const scr = this.termEl && this.termEl.querySelector('.xterm-screen');
    return !!scr && scr.offsetHeight > this.termEl.clientHeight + 1;
  }

  //: **A pane checks itself for a while after it opens, and stops.** The cell size can change under a
  //: fit after the fit is over — the real font arriving after boot gave up waiting for it, the WebGL
  //: renderer settling on dimensions of its own (it floors the cell to device pixels, which is a real
  //: change at a scale factor like Windows' 1.125 and none at all at 1) — and then nothing calls
  //: `refit` again, so nothing notices. Two guesses at *when* that happens were both wrong (user,
  //: 2026-09-18: the boot-time `document.fonts.ready` sweep does not reach a pane opened later, and
  //: the check inside `refit` needs somebody to call `refit`). So it stops guessing at the moment and
  //: watches for the state instead — four reads of two numbers over six seconds, and then never
  //: again. Running the repair by hand in the console is what proved this was the whole of it.
  watchFit() {
    FIT_CHECKS.forEach((ms) => setTimeout(() => {
      if (this.closed || !this.visible() || !this.overflows()) return;
      remeasure(this.term);
      this.fitted = false;
      this.refit();
    }, ms));
  }

  refit() {
    if (!this.visible()) return;            // another canvas — cannot measure (display:none)
    const d = this.fit.proposeDimensions();
    if (!d || !d.cols || !d.rows) return;   // no size yet
    this.fit.fit();
    // **Believe the terminal, not the call.** `fit()` can work out a size and not apply it — the
    // renderer has to have measured a cell first — and the flag used to be set either way, so a pane
    // that never fitted was recorded as fitted and nothing tried again.
    this.fitted = (this.term.rows === d.rows && this.term.cols === d.cols);
    if (!this.fitted) {
      // Bounded: a pane that cannot settle must not hold a frame callback for the rest of the
      // session. Eight frames is well past the one or two this actually takes.
      if (++this.fitTries <= 8) requestAnimationFrame(() => { if (!this.closed) this.refit(); });
      return;
    }
    this.fitTries = 0;
    // **A fit is only as good as the cell it measured.** `fit()` divides the box by a cell size the
    // renderer has already worked out, so if that changes afterwards — a font arriving late, the
    // WebGL renderer handing over to the DOM one — the row count stays and the rows get taller. The
    // terminal then stands taller than the box holding it and the bottom rows are **clipped with no
    // way to scroll to them**: the prompt is down there (user, 2026-09-18, measured in their pane:
    // 24 rows, screen 403px, box 328px, scrollHeight == clientHeight). Whatever the cause, the same
    // sentence is true every time — a terminal must never be taller than its box — so that is what
    // is checked, rather than one more cause guessed at.
    if (this.overflows()) {
      if (++this.fitTries <= 8) {
        // **And fitting again is not enough.** `fit()` divides the box by the cell size the terminal
        // has **cached**, so a second pass on a stale cell hands back the same wrong row count, calls
        // it fitted, and nothing changes however often it runs (user, 2026-09-18: still 24 rows,
        // 408px in a 328px box, after the refit added here). It has to be told to measure again.
        remeasure(this.term);
        this.fitted = false;
        requestAnimationFrame(() => { if (!this.closed) this.refit(); });
        return;
      }
      this.fitTries = 0;
    }
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
    // Whole pixels: a quarter-pixel step is a tick that does nothing you can see, and on a canvas
    // renderer it is a tick that does nothing at all.
    const f = Math.max(FONT_MIN, Math.min(FONT_MAX, Math.round(px)));
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
      else this.nameEl.append((s.fg || s.agent || 'shell') + ' ', el('span', null, '· ' + shortPath(s.cwd)));   // what runs in it, by name
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

  //: `at` is "you already know where this is — do not measure it", which is the rule the whole file
  //: keeps (applyPush, tidyCanvas, the drop handler): write the intended value, never read it back.
  //: The drag was the one place still breaking it — it wrote every member's position and then asked
  //: `offsetLeft` what it had written. **This is hygiene, not a fix for anything measured.** It was
  //: put in while chasing a group that walks out of line over many drags, on the theory that
  //: `offsetLeft` rounds two windows different ways at a fractional scale factor; that theory is
  //: wrong — measured at 1, 1.125, 1.25 and 1.5, the rounding is the same for every window
  //: (2026-09-17). What remains true is that nothing good comes of measuring what you already know.
  persist(at) {
    const r = this.rect();
    if (at) { r.x = at.x; r.y = at.y; }
    // **Keep everything this does not own.** It used to build the entry from nothing and copy back a
    // named list of fields, and the list is what goes wrong: the text size was thrown away that way
    // once, group membership would have gone the same way on every drag, and the group's **name**
    // did — it lasted until the first drag and then the board forgot it (2026-09-19, the third time).
    // Position, size and stacking are what a window knows about itself. Everything else on the entry
    // belongs to somebody else and is none of this function's business.
    const e = Object.assign({}, layout[this.id] || {},
      { x: r.x, y: r.y, w: r.w, h: r.h, z: parseInt(this.el.style.zIndex, 10) || 0 });
    const f = this.term.options.fontSize;
    if (f && Math.abs(f - FONT_PX) > 0.01) e.f = f; else delete e.f;   // the default is not written
    layout[this.id] = e;
    saveLayout();
  }

  dragify() {
    const bar = this.el.firstChild, grip = this.gripEl;
    let mode = null, sx = 0, sy = 0, ox = 0, oy = 0, ow = 0, oh = 0;
    // Group state for one drag: who moves with me, where each started, and the hold that makes a group.
    let party = [], starts = new Map(), solo = false;
    let overId = null, overSince = 0, armed = null, overSide = null;
    let overSideAtDrop = null;   // the side the hold was armed on, read once on release
    let holdTimer = null;       // the clock behind the hold, so a still hand still counts
    let nameTap = null;         // the last pointerdown on the name — two of them close together is a rename
    const down = (m) => (ev) => {
      // Buttons, input fields and the confirm strip on the title bar are not a drag (#31: .cl and .cfm joined here)
      // Only .sz.own is excluded — the ordinary size readout is part of the title bar and should drag, and it
      // becomes a reset button only on a pane whose text size was changed (#25). Leave it in and pointerdown is
      // taken as a drag and no click fires.
      if (ev.button !== 0 || ev.target.closest('.xp, .rn, .cl, .ed, .cfm, .sz.own') || this.el.classList.contains('max')) return;
      // **A double-click on the name is a rename, and it has to be caught here.** This handler takes
      // pointer capture, and a captured pointer's click and dblclick go to the capturing element —
      // so the dblclick listener the name used to carry never fired, while the canvas tab, which
      // captures nothing, renamed fine (user, 2026-09-15). Two pointerdowns on the name within 400ms
      // and a few pixels of each other are that double-click; the second one starts no drag.
      if (m === 'move' && ev.target.closest('.name')) {
        const now = Date.now();
        if (nameTap && now - nameTap.t < 400 && Math.abs(ev.clientX - nameTap.x) < 6 && Math.abs(ev.clientY - nameTap.y) < 6) {
          nameTap = null;
          ev.preventDefault();
          this.rename();
          return;
        }
        nameTap = { t: now, x: ev.clientX, y: ev.clientY };
      }
      if (!layout[this.id]) this.persist();      // the board entry went missing — do not read undefined below
      mode = m; sx = ev.clientX; sy = ev.clientY;
      ({ x: ox, y: oy, w: ow, h: oh } = this.rect());
      // **Alt takes one window out of its group.** A group moves together, so there has to be a way to
      // mean "just this one" — and it is the same gesture that leaves it behind when you let go.
      // One mark for the whole gesture — the move, whatever it pushes, and a group it forms.
      undoMark(m === 'move' ? 'moving a window' : 'resizing a window', this.s.canvas);
      solo = !!(ev.altKey && m === 'move' && layout[this.id] && layout[this.id].g);
      party = (m === 'move' && !solo) ? groupOf(this.id) : [this.id];
      carrying = (party.length > 1 && layout[this.id]) ? layout[this.id].g || null : null;
      starts = new Map(party.map((id) => [id, { x: layout[id].x, y: layout[id].y }]));
      overId = null; overSince = 0; armed = null; overSide = null; overSideAtDrop = null;
      for (const id of party) { const t = tiles.get(id); if (t) t.el.classList.add('drag'); }
      this.el.classList.add('drag');
      // Every frame, not every 60ms: at 60ms the ring moved in visible steps ("조금 끊기는 거 같아").
      if (m === 'move') { clearInterval(holdTimer); holdTimer = setInterval(hold, 16); }
      ev.currentTarget.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    };
    // **The hold.** Overlapping a window that is not already travelling with me, for long enough.
    //
    // **Leaving resets it, not moving.** This used to also reset on any movement past a few pixels, on
    // the theory that a hold is a window held still — and a hand cannot hold still. On a real mouse the
    // gauge flickered near zero and never filled, so there was no gauge, no preview, and a gesture that
    // seemed to need one exact pixel (user, 2026-09-14, on Windows). Every test had sent the same
    // coordinate twice and so had never moved at all. The dwell alone keeps a window you are only
    // crossing from arming: nobody spends 900ms on the way past.
    //
    // **And a clock drives it, not only the hand.** It used to run on pointermove alone, so the one
    // gesture it is for — putting a window down on another and keeping it there — froze the gauge at
    // whatever it had reached, and the stiller the hand the less it filled. A hold that only advances
    // while you move is not a hold.
    const hold = () => {
      // The clock outlives nothing: a window closed mid-drag stops it here rather than in dispose(),
      // which cannot see this closure.
      if (this.closed) { clearInterval(holdTimer); holdTimer = null; return; }
      if (mode !== 'move') return;
      const hit = paneOver(this.id, party, overId);
      const under = hit && hit.id;
      if (under !== overId) {
        if (overId) markHold(overId, false);
        setGauge(this.id, 0);
        showGhost(null);
        armed = null;                       // moved on — nothing is going to be joined
        overId = under; overSince = under ? Date.now() : 0;
        overSide = hit && hit.side;
        if (under) markHold(under, true);
      } else if (overId) {
        // **The side is latched once armed.** Centre-to-centre flips on a pixel near the diagonal, and
        // the drop reads overSideAtDrop — so without this the preview and the landing can disagree.
        overSide = overSideAtDrop || hit.side;
        // **A quiet moment first.** Landing on a window starts nothing for a beat, so carrying one
        // across another shows no gauge and offers no landing spot — only staying does ("겹쳐진 후
        // 일정시간이 지나고 나서 게이지가 올라가야 해", 2026-09-14). The target still lights up at
        // once, which is what says the hold has something to hold on to.
        const pct = (Date.now() - overSince - GROUP_LEAD_MS) / GROUP_HOLD_MS * 100;
        setGauge(this.id, pct);                        // 0 or less takes the ring off
        showGhost(pct > 0 ? joinBlock(overId, overSide, this.id) : null, overSide, pct);
      }
      if (overId && Date.now() - overSince >= GROUP_LEAD_MS + GROUP_HOLD_MS) {
        // **Armed, not done.** It used to join here, in the middle of the drag, so carrying on
        // somewhere else left you grouped to a window you had moved away from — "잡은 걸 놓지
        // 않고 다시 다른 공간으로 옮기면 그룹핑이 취소가 되어야 자연스럽지" (2026-09-14). The hold
        // arms it and the release commits it; moving away disarms it, which is what the reset
        // branch above already does.
        armed = overId;
        overSideAtDrop = overSide;
        markHold(overId, 'ready');
        setGauge(this.id, 100);
        showGhost(joinBlock(overId, overSide, this.id), overSide, 100);
      }
    };
    const move = (ev) => {
      if (!mode) return;
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      // Move the minimap rectangle along using **the value just computed** — do not ask the DOM again
      if (mode === 'move') {
        // **A window may be carried past the origin.** It used to be clamped there, which made the
        // room around the canvas somewhere you could look but not put anything — pan into the empty
        // space above and to the left, try to drag a window there, and it stopped dead at the edge of
        // the windows already placed (user, 2026-09-17). Board coordinates go negative now; the world
        // holds the origin far enough in that the pad still starts at zero.
        // A whole number of pixels, and the same one for everybody: a group under the hand is one
        // rigid thing, and nothing downstream should ever have a fraction to make a decision about.
        const mdx = Math.round(dx), mdy = Math.round(dy);
        for (const id of party) {
          const t = tiles.get(id), st = starts.get(id), r = layout[id];
          if (!t || !r) continue;
          const nx2 = st.x + mdx, ny2 = st.y + mdy;
          t.el.style.left = nx2 + 'px';
          t.el.style.top = ny2 + 'px';
          // **The store, so the frame can follow.** paintGroups reads the union out of `layout`,
          // and a frame that stays behind while its windows move is worse than no frame.
          layout[id] = Object.assign({}, r, { x: nx2, y: ny2 });
          mmSet(id, nx2, ny2, r.w, r.h);
        }
        if (party.length > 1) paintGroups();
        hold();
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
      // Whatever it does from here on, it may glide there — and it has to be told, because a drop
      // that moves nothing paints nothing and the frame would keep the drag's stiffness for good.
      carrying = null;
      paintGroups();
      clearInterval(holdTimer); holdTimer = null;
      if (overId) { markHold(overId, false); overId = null; }
      setGauge(this.id, 0);
      showGhost(null);
      for (const id of party) { const t = tiles.get(id); if (t) t.el.classList.remove('drag'); }
      this.el.classList.remove('drag');
      if (solo) {
        undoMark('leaving a group', this.s.canvas);
        const left = leaveGroup(this.id);
        solo = false;
        // What is left closes up, as it does when a member is closed — taking one out of the middle
        // used to leave its hole behind (user, 2026-09-15).
        if (left) compactGroup(groupMembers(left));
        if (left) toast([{ b: this.nameEl.textContent || 'window' }, 'left its group — ' + KMOD + 'Z puts it back']);
      }
      // The drag wrote every member's position into the store as it went; saving it is not an
      // excuse to go and measure the screen again (see persist).
      const told = (t) => (was === 'move' && layout[t.id]) ? { x: layout[t.id].x, y: layout[t.id].y } : null;
      for (const id of party) { const t = tiles.get(id); if (t && t !== this) t.persist(told(t)); }
      paintTidy();          // moving a window creates or removes slack to close up
      if (was === 'size') this.refit();   // tell the PTY only when the resize is let go (spike D)
      this.persist(told(this));
      // **Everything below places windows, and placing writes the store directly.** persist() reads the
      // position back off the element, and .tile slides for 350ms, so a persist that follows an
      // arrangement reads a number from the middle of that slide and saves the *old* position —
      // the same trap applyPush documents ("write the intended value; never read it back"). That is
      // why a window did not land where its preview said and why a resized group stayed spread out:
      // the layout was correct for one frame and then overwritten (measured 2026-09-14).
      let joined = false;
      if (armed) {
        undoMark('grouping', this.s.canvas);
        const ids = dropInto(this.id, armed, overSideAtDrop || 'right');   // exactly where the preview was
        joined = true;
        const n = ids.length;
        toast([{ b: 'grouped ' + n + (n > 1 ? ' windows' : ' window') },
               '— they move together; ' + (IS_MAC ? '⌥' : 'Alt') + '-drag takes one out, ' + KMOD + 'Z undoes this']);
        armed = null;
      }
      // **Resizing inside a group re-lays the group out.** Growing a member pushed the others away
      // and shrinking it left the hole behind, because push-aside only ever resolves overlap and
      // never pulls anything back — which is right for the canvas ("밀어내기를 타일링으로 바꾸지
      // 마라") and wrong inside a group, where the whole point is a block that stays a block
      // (user, 2026-09-14). Sizes are still the user's; only the positions are set.
      // A resize says which way it grew, so what it displaces is pushed that way and a row stays a row.
      let grew = null;
      if (was === 'size') {
        const r = layout[this.id] || {}, dw = (r.w || ow) - ow, dh = (r.h || oh) - oh;
        if (dw > 0 || dh > 0) grew = dw >= dh ? 'r' : 'b';
      }
      settle(this.id, { compact: !joined, dir: grew });   // whatever it landed on gets out of the way; the group closes up
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

//: **A viewer is a window that shows a file.** Same frame, same board, same groups and pushes as a
//: terminal — it lives in `tiles` under an id of its own (`v:` + a hash of the path), so everything that
//: walks the tiles treats it as one more window. It is not a session: the daemon never hears of it, and
//: it comes back after a reload from the board alone (kind, path, canvas). Read-only: the shell beside it
//: is where files are changed (2026-09-15).
const VIEW_MAX_LINES = 20000;
//: **One window, several ways of showing a file** (2026-09-15). Text with line numbers; an image; a
//: comma-separated file as a table; HTML rendered in a frame that is sandboxed, so a page cannot reach
//: palmar around it; a PDF handed to the browser's own viewer — no library is vendored for any of it.
//: A spreadsheet (.xlsx) is not here: that one needs a library, and that is a decision to take, not to
//: slip in. The button beside expand hands the file to the machine's own program instead.
function viewMode(path, ctype) {
  const ext = (path.match(/\.[^.\\/]+$/) || [''])[0].toLowerCase();
  if (ctype.startsWith('image/')) return 'image';
  if (ctype.startsWith('application/pdf')) return 'pdf';
  if (ext === '.csv' || ext === '.tsv') return 'csv';
  if (ext === '.html' || ext === '.htm') return 'html';
  return 'text';
}
// A separated-values file, by the rule everyone actually uses: quotes protect a separator, and two
// quotes inside quotes are one quote.
function parseSV(text, sep) {
  const rows = [[]];
  let cell = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; }
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === sep) { rows[rows.length - 1].push(cell); cell = ''; }
    else if (c === '\n') { rows[rows.length - 1].push(cell); cell = ''; rows.push([]); }
    else if (c !== '\r') cell += c;
  }
  rows[rows.length - 1].push(cell);
  if (rows.length && rows[rows.length - 1].every((x) => x === '')) rows.pop();
  return rows;
}
function viewerId(path) {
  let h = 5381;
  for (let i = 0; i < path.length; i++) h = ((h * 33) ^ path.charCodeAt(i)) >>> 0;
  return 'v:' + h.toString(36) + path.length.toString(36);
}
function viewerSession(id, path, canvas) {
  const name = path.split(/[\\/]/).pop() || path;
  return { id, kind: 'file', path, name, cwd: path.replace(/[\\/][^\\/]*$/, '') || path, canvas, status: 'idle', cols: 0, rows: 0 };
}
class Viewer {
  constructor(id, path, canvas) {
    this.id = id;
    this.s = viewerSession(id, path, canvas);
    this.closed = false;
    this.off = false;
    // What the rest of the page asks of a window's terminal, answered harmlessly.
    this.blobUrl = null;
    this.term = { rows: 0, cols: 0, options: {}, focus() {}, blur() {}, dispose() {}, resize() {},
                  hasSelection: () => false, getSelection: () => '', textarea: null };
    const saved = buildFrame(this, this.s);
    layout[this.id] = Object.assign({}, layout[this.id], { kind: 'file', path, canvas });
    this.el.classList.add('viewer');
    this.el.dataset.path = path;
    this.termEl.className = 'view';
    this.termEl.tabIndex = 0;
    this.mode = 'text';
    this.text = null;          // what was read, while it is being edited
    this.mtime = '';           // the stamp a save has to match
    this.canEdit = false;
    this.editing = false;
    this.dirty = false;
    this.barEl = el('div', 'view-bar');
    this.barEl.hidden = true;
    this.el.appendChild(this.barEl);
    this.owEl.addEventListener('click', (ev) => { ev.stopPropagation(); this.openWith(); });
    this.edEl.addEventListener('click', (ev) => { ev.stopPropagation(); this.toggleEdit(); });
    this.szEl.hidden = true;                      // there are no columns to say
    this.clEl.title = 'close'; this.clEl.setAttribute('aria-label', 'close viewer');
    this.clEl.addEventListener('click', (ev) => { ev.stopPropagation(); this.close(); });
    this.el.addEventListener('pointerdown', () => focusTile(this.id, { user: true, keyboard: false }), true);
    this.dragify();
    this.update(this.s);
    this.setFont((saved && saved.f) || FONT_PX, true);
    this.load();
  }
  visible() { return current === null || this.s.canvas === current; }
  rect() { return { x: this.el.offsetLeft, y: this.el.offsetTop, w: this.el.offsetWidth, h: this.el.offsetHeight }; }
  persist(at) { Tile.prototype.persist.call(this, at); }
  dragify() { Tile.prototype.dragify.call(this); }
  rename() {}
  refit() {}
  sendResize() {}
  showSize() {}
  noteOutput() {}
  connect() {}
  askClose() { this.close(); }
  setFont(px, quiet) {
    const f = Math.max(FONT_MIN, Math.min(FONT_MAX, px));
    this.termEl.style.fontSize = f + 'px';
    this.term.options.fontSize = f;
    if (!quiet) this.persist();
  }
  update(s) {
    this.s = s;
    this.dotEl.className = 'dot idle';
    this.pillEl.hidden = true;
    if (!this.nameEl.querySelector('input')) {
      this.nameEl.textContent = '';
      this.nameEl.append(s.name + ' ', el('span', null, '· ' + shortPath(s.cwd)));
    }
  }
  // Hand it to the machine: `open` on macOS, `xdg-open` on Linux, the shell's own on Windows.
  async openWith() {
    try {
      await api('POST', '/api/open', { path: this.s.path });
      toast([{ b: this.s.name }, ' — opening it with the system default']);
    } catch (e) {
      toast(['could not open it — ' + e.message]);
    }
  }
  // Editing is plain text in a plain box: an editor's own undo, its own selection, its own IME.
  toggleEdit(on) {
    if (!this.canEdit) { toast(['this one is read-only here — the shell beside it can change it']); return; }
    const want = on === undefined ? !this.editing : on;
    if (want === this.editing) return;
    this.editing = want;
    this.el.classList.toggle('editing', want);
    this.edEl.title = want ? 'stop editing' : 'edit';
    if (want) this.showEditor(); else { this.dirty = false; this.render(); }
    this.say();
  }
  showEditor() {
    const box = this.termEl;
    box.className = 'view editor';
    box.textContent = '';
    const ta = el('textarea', 'ed-area');
    ta.value = this.text == null ? '' : this.text;
    ta.spellcheck = false;
    ta.addEventListener('input', () => { this.dirty = true; this.say(); });
    ta.addEventListener('keydown', (ev) => {
      if ((ev.metaKey || ev.ctrlKey) && (ev.key === 's' || ev.key === 'S')) { ev.preventDefault(); ev.stopPropagation(); this.save(); }
    });
    box.appendChild(ta);
    this.ta = ta;
    ta.focus();
  }
  // The line under the title: what state this file is in, and the way out of a clash.
  say(msg, actions) {
    const bar = this.barEl;
    bar.textContent = '';
    if (!msg && !this.editing) { bar.hidden = true; return; }
    if (!msg && this.truncated) msg = 'a file this size is shown from the top, and is read-only';
    bar.hidden = false;
    bar.append(el('span', 'm', msg || (this.dirty ? 'edited — ' + KMOD + 'S saves' : 'editing — ' + KMOD + 'S saves')));
    for (const [label, fn] of actions || []) {
      const b = el('button', 'vb', label);
      b.type = 'button';
      b.addEventListener('click', (ev) => { ev.stopPropagation(); fn(); });
      bar.appendChild(b);
    }
  }
  async save(force) {
    if (!this.ta) return;
    const body = this.ta.value;
    const url = new URL('/api/file', location.origin);
    url.searchParams.set('path', this.s.path);
    url.searchParams.set('token', TOKEN);
    if (!force && this.mtime) url.searchParams.set('mtime', this.mtime);
    try {
      const r = await fetch(url, { method: 'PUT', headers: { 'content-type': 'text/plain; charset=utf-8' }, body });
      if (r.status === 409) {
        // Something else wrote it — an agent in the terminal beside this window is the case this is for.
        this.say('it changed on disk since you opened it', [
          ['Reload', () => { this.toggleEdit(false); this.load(); }],
          ['Overwrite', () => this.save(true)],
        ]);
        return;
      }
      if (!r.ok) {
        let m = r.status + ' ' + r.statusText;
        try { const j = await r.json(); if (j && j.error) m = j.error; } catch (e) {}
        this.say('could not save — ' + m);
        return;
      }
      const j = await r.json();
      this.mtime = String(j.mtime);
      this.text = body;
      this.dirty = false;
      this.say('saved');
      setTimeout(() => { if (!this.dirty && this.editing) this.say(); }, 1600);
    } catch (e) {
      this.say('could not save — ' + (e.message || e));
    }
  }
  render() {
    const box = this.termEl;
    box.className = 'view ' + this.mode;
    box.textContent = '';
    if (this.mode === 'image') {
      const img = el('img');
      img.alt = this.s.name;
      img.src = this.blobUrl;
      box.appendChild(img);
      return;
    }
    if (this.mode === 'pdf') {
      const f = el('iframe', 'doc');
      f.src = this.blobUrl;
      box.appendChild(f);
      return;
    }
    if (this.mode === 'html') {
      const f = el('iframe', 'doc');
      // **Sandboxed, and no exception to it.** The page is somebody's file; without this it would run
      // its script in palmar's own origin, next to the token.
      f.setAttribute('sandbox', '');
      // The file's own markup cannot loosen a policy that is already there — policies only intersect —
      // so an <img src="https://…"> in a cloned repository's page does not phone home when it is
      // looked at (review, 2026-09-15). Inline styles and data: images still show.
      f.srcdoc = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: blob:; style-src \'unsafe-inline\'; font-src data:">' + this.text;
      box.appendChild(f);
      return;
    }
    if (this.mode === 'csv') {
      const rows = parseSV(this.text, this.s.path.toLowerCase().endsWith('.tsv') ? '\t' : ',');
      const tbl = el('table', 'sv');
      const head = el('tr');
      head.appendChild(el('th', 'n', ''));
      (rows[0] || []).forEach((c) => head.appendChild(el('th', null, c)));
      tbl.appendChild(head);
      rows.slice(1, VIEW_MAX_LINES).forEach((r, i) => {
        const tr = el('tr');
        tr.appendChild(el('td', 'n', String(i + 1)));
        r.forEach((c) => tr.appendChild(el('td', null, c)));
        tbl.appendChild(tr);
      });
      box.appendChild(tbl);
      return;
    }
    const lines = (this.text || '').split('\n');
    if (lines.length && lines[lines.length - 1] === '') lines.pop();
    const frag = document.createDocumentFragment();
    const n = Math.min(lines.length, VIEW_MAX_LINES);
    for (let i = 0; i < n; i++) {
      const l = el('div', 'l');
      l.append(el('span', 'n', String(i + 1)), el('span', 'c', lines[i]));
      frag.appendChild(l);
    }
    if (lines.length > n) frag.appendChild(el('div', 'view-msg', '… ' + (lines.length - n) + ' more lines'));
    box.appendChild(frag);
  }
  async load() {
    const box = this.termEl;
    box.textContent = 'reading…';
    const url = new URL('/api/file', location.origin);
    url.searchParams.set('path', this.s.path);
    url.searchParams.set('token', TOKEN);
    try {
      const r = await fetch(url);
      if (!r.ok) {
        let msg = r.status + ' ' + r.statusText;
        try { const j = await r.json(); if (j && j.error) msg = j.error; } catch (e) {}
        box.textContent = ''; box.appendChild(el('div', 'view-msg', msg));
        return;
      }
      const ctype = r.headers.get('content-type') || '';
      this.mtime = r.headers.get('x-palmar-mtime') || '';
      this.canEdit = r.headers.get('x-palmar-editable') === '1';
      this.truncated = parseInt(r.headers.get('x-palmar-truncated') || '0', 10) || 0;
      this.el.classList.toggle('can-edit', this.canEdit);
      this.mode = viewMode(this.s.path, ctype);
      if (this.mode === 'image' || this.mode === 'pdf') {
        const blob = await r.blob();
        if (this.closed) return;               // closed while it was loading — do not make a URL nobody revokes
        if (this.blobUrl) URL.revokeObjectURL(this.blobUrl);
        this.blobUrl = URL.createObjectURL(blob);
        this.text = null;
      } else {
        this.text = await r.text();
        if (this.closed) return;
      }
      this.dirty = false;
      this.render();
      if (this.truncated) {
        const mb = (n) => (n / 1048576).toFixed(n < 10 * 1048576 ? 1 : 0) + ' MB';
        this.say('showing the first ' + mb(this.text ? this.text.length : 0) + ' of ' + mb(this.truncated) + ' — read-only');
      } else this.say();
    } catch (e) {
      box.textContent = ''; box.appendChild(el('div', 'view-msg', 'could not read it — ' + (e.message || e)));
    }
  }
  close() {
    if (this.closed) return;
    const cv = this.s.canvas;
    const g = layout[this.id] && layout[this.id].g;
    undoMark('closing a viewer', cv);
    this.dispose();
    tiles.delete(this.id);
    delete layout[this.id];
    // What is left of its group closes up, and the canvas tidies if that is on — the same two things
    // closing a terminal does (review, 2026-09-15).
    if (g) {
      const rest = dissolveIfAlone(g);
      if (rest.length > 1) compactGroup(rest);
    }
    if (focused === this.id) focused = null;
    if (autoTidy) tidyCanvas(cv);
    saveLayout();
    paintGroups(); renderMinimap(); refreshOff(); paintTidy();
  }
  dispose() {
    this.closed = true;
    if (this.blobUrl) { try { URL.revokeObjectURL(this.blobUrl); } catch (e) {} this.blobUrl = null; }
    this.el.remove();
  }
}
function openViewer(path, canvas, at) {
  const cv = canvas || current;
  const id = viewerId(path);
  let v = tiles.get(id);
  if (v) {
    // Already open. If it is sitting on another canvas, bring it to this one — the click has to do
    // something, and a window hidden elsewhere looked like nothing happened (review, 2026-09-15).
    if (at) {                                // dragged again: it moves to where it was let go
      v.el.style.left = at.x + 'px'; v.el.style.top = at.y + 'px';
      layout[id] = Object.assign({}, layout[id], { x: at.x, y: at.y });
      v.el.classList.add('opening');
      setTimeout(() => v.el.classList.remove('opening'), 260);
      saveLayout(); settle(id);
    }
    if (v.s.canvas !== cv) {
      v.s.canvas = cv;
      layout[id] = Object.assign({}, layout[id], { canvas: cv });
      v.el.classList.toggle('other', !v.visible());
      saveLayout(); paintGroups(); renderMinimap(); refreshOff();
    }
    focusTile(id, { user: true });
    v.el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
    return v;
  }
  v = new Viewer(id, path, cv);
  tiles.set(id, v);
  if (at) {                                  // dropped: land under the hand, not in the first free slot
    v.el.style.left = at.x + 'px'; v.el.style.top = at.y + 'px';
    layout[id] = Object.assign({}, layout[id], { x: at.x, y: at.y });
  }
  v.el.classList.add('opening');
  setTimeout(() => v.el.classList.remove('opening'), 260);
  v.persist();
  settle(id);
  paintGroups(); renderMinimap(); refreshOff();
  focusTile(id, { user: true, keyboard: false });
  v.el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
  return v;
}
//: **A path out of a `file://` URL.** Finder and Explorer hand a drag over as `text/uri-list`, which
//: is the only part of a dropped file a page is allowed to see as a path at all — `dataTransfer.files`
//: gives a name and bytes and no location, and the daemon opens files by path.
//:
//: Windows spells it `file:///C:/x/y`, which is a leading slash and forward slashes over a path that
//: has neither, and a share is `file://server/share/x`. Percent-decoding is not optional: one space in
//: a folder name and the path is wrong.
function fileUrlToPath(s) {
  if (!/^file:/i.test(s)) return (s[0] === '/' || /^[A-Za-z]:[\\/]/.test(s)) ? s : null;
  let u;
  try { u = new URL(s); } catch (e) { return null; }
  let p;
  try { p = decodeURIComponent(u.pathname); } catch (e) { return null; }
  if (u.host && u.host !== 'localhost') return '\\\\' + u.host + p.replace(/\//g, '\\');
  if (/^\/[A-Za-z]:/.test(p)) return p.slice(1).replace(/\//g, '\\');
  return p;
}
function droppedPaths(dt) {
  let raw = '';
  try { raw = (dt && dt.getData('text/uri-list')) || ''; } catch (e) {}
  if (!raw) { try { raw = (dt && dt.getData('text/plain')) || ''; } catch (e) {} }
  const out = [];
  for (const line of raw.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t[0] === '#') continue;        // a uri-list may carry comments
    const p = fileUrlToPath(t);
    if (p && out.indexOf(p) < 0) out.push(p);
  }
  return out;
}
function carriesFiles(dt) {
  if (!dt || !dt.types) return false;
  const t = Array.prototype.slice.call(dt.types);
  return t.indexOf('Files') >= 0 || t.indexOf('text/uri-list') >= 0;
}

// **Drag a file out of the rail and let go on the canvas**, or in from Finder or Explorer: it opens
// where it was dropped. The drop point is in canvas coordinates, so it lands under the hand rather
// than in the first free slot.
cvScroll.addEventListener('dragover', (ev) => {
  if (!dragFile && !carriesFiles(ev.dataTransfer)) return;
  ev.preventDefault();
  try { ev.dataTransfer.dropEffect = 'copy'; } catch (e) {}
  cv.classList.add('dropping');
});
cvScroll.addEventListener('dragleave', (ev) => { if (ev.target === cvScroll) cv.classList.remove('dropping'); });
cvScroll.addEventListener('drop', (ev) => {
  cv.classList.remove('dropping');
  const paths = dragFile ? [dragFile] : droppedPaths(ev.dataTransfer);
  dragFile = null;
  // **Take the drop even when there is nothing in it we can open.** Left to the browser, a file let go
  // on a page it does not accept is *navigated to* — palmar replaced by a PDF, and the board with it.
  if (!carriesFiles(ev.dataTransfer) && !paths.length) return;
  ev.preventDefault();
  if (!paths.length) { openDropped(ev.dataTransfer, at0(ev)); return; }
  const at = at0(ev);
  // Several at once land in a short cascade rather than exactly on top of one another.
  paths.forEach((p, i) => openViewer(p, current, { x: at.x + i * GAP * 2, y: at.y + i * GAP * 2 }));
});

// The board's coordinates start at the world's origin, not the scroller's (see "the world").
function at0(ev) {
  const box = cvScroll.getBoundingClientRect();
  return { x: Math.round(ev.clientX - box.left + cvScroll.scrollLeft - originX - 60),
           y: Math.round(ev.clientY - box.top + cvScroll.scrollTop - originY - 15) };
}

//: **When the drop came without a location.** Which is Windows, every time: `text/uri-list` is not
//: there and `dataTransfer.files` gives a name, a size, a modification time and the bytes — never a
//: path. The bytes are what an upload wants, and an upload is not what this is: a viewer here is a
//: window onto the file **on disk**, and text and Markdown save back to it. A copy in a temporary
//: folder would look identical and quietly stop being the file you dropped.
//:
//: So palmar looks for it, by the three facts it was given, under the same roots everything else is
//: floored by. **Only when exactly one file agrees on all three does it open** — two files sharing a
//: name, a byte count and a modification time is not something to guess between, and opening the
//: wrong one silently is worse than opening nothing.
async function openDropped(dt, at) {
  const files = dt && dt.files ? Array.prototype.slice.call(dt.files) : [];
  if (!files.length) {
    toast(['nothing in that drop palmar can open']);
    return;
  }
  let i = 0;
  // **Say that something is happening.** Finding a dropped file means sweeping disks, which takes as
  // long as it takes — and until it finished, letting go of a file did nothing you could see (user,
  // 2026-09-18). The canvas shows the busy cursor and the toast names the file being looked for; both
  // are cleared by whatever the search turns out to say.
  cv.classList.add('finding');
  try {
    for (const f of files) {
      toast([{ spin: 1 }, 'looking for', { b: f.name }, '—',
             { d: 'a dropped file carries no path, so palmar searches your drives' }]);
      let hits = [];
      try {
        const r = await api('GET', '/api/files?name=' + encodeURIComponent(f.name));
        hits = (r && r.files) || [];
      } catch (e) {
        toast(['could not look for ' + f.name + ' —', { d: String(e.message || e) }]);
        return;
      }
      // Modification time to the second: a file system keeps it more coarsely than the browser reports it.
      const same = hits.filter((h) => h.size === f.size &&
                                      Math.abs(h.mtime * 1000 - f.lastModified) < 2000);
      if (same.length === 1) {
        openViewer(same[0].path, current, { x: at.x + i * GAP * 2, y: at.y + i * GAP * 2 });
        toast([{ b: f.name }, '—', { d: shortPath(same[0].path) }]);
        i++;
      } else if (same.length > 1) {
        toast([{ b: f.name }, 'is in ' + same.length + ' places and they are identical —',
               { d: 'open it from the folder rail so palmar knows which' }]);
      } else if (hits.length) {
        // **Found the name and not the file.** Saying "not found" for this sent the search looking
        // for a fault it did not have (2026-09-18). Size and time are what tell two files of a name
        // apart, so when they disagree the numbers are the answer, not a guess at which was meant.
        const h = hits[0];
        toast([{ b: f.name }, 'is on disk but not the one that was dropped —',
               { d: 'dropped ' + f.size + ' bytes at ' + new Date(f.lastModified).toLocaleString() +
                    ' · found ' + h.size + ' at ' + new Date(h.mtime * 1000).toLocaleString() }]);
      } else {
        toast([{ b: f.name }, 'was not found on your drives —',
               { d: 'a dropped file carries no path; open it from the folder rail, which reaches anywhere' }]);
      }
    }
  } finally {
    cv.classList.remove('finding');
  }
}

// After the board arrives: every viewer it names comes back, on the canvas it was on.
function restoreViewers() {
  let fixed = false;
  for (const [id, r] of Object.entries(layout)) {
    if (!id.startsWith('v:') || r.kind !== 'file' || !r.path || tiles.has(id)) continue;
    // **Never trust a canvas the page does not know.** A dead id — after a daemon restart, or a canvas
    // somebody removed — hides the window on every tab and there is no way back to it (review,
    // 2026-09-15). The repair is saved, so the board stops carrying the dead id.
    const cv = canvases.has(r.canvas) ? r.canvas : current;
    if (cv !== r.canvas) fixed = true;
    tiles.set(id, new Viewer(id, r.path, cv));
  }
  if (fixed) saveLayout();
  paintGroups();
  renderMinimap();
  refreshOff();
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
  // **Either way.** A window carried into the slack sits at a negative coordinate, and pulling the
  // canvas back to its corner from out there is closing up just as much as pulling it in from below
  // and to the right.
  return Math.abs(Math.min(...mine.map((t) => layout[t.id].x)) - GAP)
       + Math.abs(Math.min(...mine.map((t) => layout[t.id].y)) - GAP);
}

//: **It does two things now, and only one of them can run out.** The button was lit by slack alone —
//: by whether the windows had drifted off the corner — so on a canvas that was already tidy it went
//: grey, and the other half of what it does, taking you back to what you were working in, could not
//: be reached at all (user, 2026-09-17: "특정 터미널을 옮겨야만 버튼이 활성화되서 불편한거같아").
//: A canvas with a window on it always has somewhere to take you. Only an empty one has not.
function paintTidy() {
  const b = document.getElementById('tidy');
  if (!b) return;
  const any = current !== null && [...tiles.values()].some((t) => t.s.canvas === current && layout[t.id]);
  const slack = current === null ? 0 : tidySlack(current);
  const g = document.getElementById('gather');
  if (g) {
    const plan = current === null ? null : gatherPlan(current);
    g.disabled = !plan;
    g.title = plan ? 'bring them closer — keeps the arrangement, closes the gaps'
                   : 'bring them closer — there is nothing to close up';
  }
  b.disabled = !any;
  b.title = !any ? 'tidy this canvas — nothing on it yet'
    : slack ? 'tidy this canvas — pull the windows back to the corner'
            : 'tidy this canvas — already at the corner; this takes you there';
}

//: **Bring them closer, until they touch** (asked 2026-09-17; the first rule was "keep the
//: arrangement, close the gaps", and the user looked at it and asked for tighter — "테트리스처럼 좌우
//: 창들이 맞닿게끔", 2026-09-20). Squeezing each axis on its own left windows meeting only at a
//: corner, because closing a horizontal gap and a vertical one separately never makes two windows
//: sit side by side. So each one is slid instead: left until it meets something, then up, a few
//: passes until nothing moves.
//:
//: **A group is one thing that slides.** Everything a group owns is in one block and moves together,
//: so the one arrangement that must not change does not — and blocks collide by their outer
//: rectangle, which can leave a little air beside an L-shaped group. That is the conservative way
//: round: a gap is a gap, an overlap would be a bug.
//:
//: **The corner does not move.** Gather brings things together; pulling the whole lot to the origin
//: is tidy's job, and doing both here would make one button two.
function gatherBlocks(canvasId) {
  const mine = [...tiles.values()].filter((t) => t.s.canvas === canvasId && layout[t.id]);
  if (mine.length < 2) return null;
  const byGroup = new Map();
  for (const t of mine) {
    const k = layout[t.id].g || t.id;
    if (!byGroup.has(k)) byGroup.set(k, []);
    byGroup.get(k).push(t.id);
  }
  return [...byGroup.values()].map((ids) => {
    const r = groupRect(ids);
    return { ids, x: r.x, y: r.y, w: r.w, h: r.h };
  });
}

//: How far left this block can go before it meets one already placed. Only the blocks sharing rows
//: with it can be in the way; the rest are beside it on the other axis. Never past the corner, and
//: never to the right — sliding is one way.
function slideLeft(b, placed, x0) {
  let edge = x0;
  for (const o of placed) {
    if (o.y >= b.y + b.h + GAP || o.y + o.h + GAP <= b.y) continue;   // not in the same rows
    edge = Math.max(edge, o.x + o.w + GAP);
  }
  return Math.max(x0, Math.min(b.x, edge));
}
//: The same reading upwards: only the blocks sharing columns can stop it.
function slideUp(b, placed, y0) {
  let edge = y0;
  for (const o of placed) {
    if (o.x >= b.x + b.w + GAP || o.x + o.w + GAP <= b.x) continue;   // not in the same columns
    edge = Math.max(edge, o.y + o.h + GAP);
  }
  return Math.max(y0, Math.min(b.y, edge));
}

//: What `gather` would do, as {id: {x, y}} — worked out without touching anything, so the button can
//: know whether it has anything to do and a test can ask the same question the button asks.
function gatherPlan(canvasId) {
  const blocks = gatherBlocks(canvasId);
  if (!blocks) return null;
  const x0 = Math.min(...blocks.map((b) => b.x));
  const y0 = Math.min(...blocks.map((b) => b.y));
  const was = new Map(blocks.map((b) => [b, { x: b.x, y: b.y }]));
  // Nearest the corner first, so each one slides against what is already settled.
  const order = [...blocks].sort((m, n) => (m.y + m.x) - (n.y + n.x));
  for (let pass = 0; pass < 3; pass++) {
    let moved = false;
    const placed = [];
    for (const b of order) {
      const bx = slideLeft(b, placed, x0);
      if (bx !== b.x) { b.x = bx; moved = true; }
      const by = slideUp(b, placed, y0);
      if (by !== b.y) { b.y = by; moved = true; }
      placed.push(b);
    }
    if (!moved) break;
  }
  const plan = {};
  let moved = false;
  for (const b of blocks) {
    const from = was.get(b), dx = b.x - from.x, dy = b.y - from.y;
    if (dx || dy) moved = true;
    for (const id of b.ids) {
      const r = layout[id];
      plan[id] = { x: r.x + dx, y: r.y + dy };
    }
  }
  return moved ? plan : null;
}

function gatherCanvas(canvasId) {
  const plan = gatherPlan(canvasId);
  if (!plan) return false;
  for (const [id, at] of Object.entries(plan)) {
    const t = tiles.get(id);
    if (!t) continue;
    // Write the intended value; never read it back — .tile slides for 350ms (see tidyCanvas).
    t.el.style.left = at.x + 'px';
    t.el.style.top = at.y + 'px';
    layout[id] = Object.assign({}, layout[id], { x: at.x, y: at.y });
  }
  saveLayout();
  renderMinimap();
  refreshOff();
  paintTidy();
  paintGroups();
  return true;
}

function tidyCanvas(canvasId, byHand) {
  const mine = [...tiles.values()].filter((t) => t.s.canvas === canvasId && layout[t.id]);
  if (!mine.length) return false;
  const dx = Math.min(...mine.map((t) => layout[t.id].x)) - GAP;
  const dy = Math.min(...mine.map((t) => layout[t.id].y)) - GAP;
  const sx = dx, sy = dy;                    // signed: the corner may be above and to the left
  // Nothing to close up is not nothing to do: by hand it still goes and looks. Left to itself
  // (auto-tidy) it stops here, because a run that moves nothing must not move the view either.
  const l0 = cvScroll.scrollLeft, t0 = cvScroll.scrollTop;
  // **Write the intended value instead of reading it back.** `persist()` reads `offsetLeft`, but the position has
  // a transition on it, so that value is a **mid-move** one — save it as-is and the old position goes back in and
  // nothing appears to have happened (measured: pressing it left the positions unchanged). The drag path was
  // dodging this trap by turning the transition off with the `drag` class.
  if (!sx && !sy && !byHand) return false;
  // **Pressing it gives back the room you pulled out.** Slack reached by hand is kept for the
  // session so that it cannot vanish under you — but tidy is the ask, so here it goes, and the
  // scrollbar comes back to the windows (user, 2026-09-17).
  if (byHand) worldSeen.delete(canvasId);
  if (sx || sy) {
    for (const t of mine) {
      const r = layout[t.id];
      t.el.style.left = (r.x - sx) + 'px';
      t.el.style.top = (r.y - sy) + 'px';
      layout[t.id] = Object.assign({}, r, { x: r.x - sx, y: r.y - sy });
    }
    saveLayout();
  }
  if (byHand && canvasId === current) sizeWorld();
  // **Go and look at the corner.** The view used to be nudged by exactly what the windows moved, so
  // that nothing slid under the eye — and once the canvas had a square of slack around it that
  // compensation became exact, which made pressing tidy do nothing visible at all: the windows went
  // to the corner and the view went with them ("tidy 버튼 누르면 보고있는 화면에서 살짝 흔들리기만",
  // user, 2026-09-17). A button whose whole job is "pull them back to the corner" has to leave you
  // looking at the corner. Smoothly, because the windows themselves glide there on a transition.
  //
  // **Only for the canvas being looked at.** Every canvas shares the one scroll box (a hidden pane is
  // just display:none). With auto-tidy on, one pane disappearing on a canvas in the background would
  // slide the view you are looking at sideways — text moves while you touched nothing. That is why
  // auto-tidy keeps the old behaviour: it was not asked for, so it must not move the view.
  if (canvasId === current) {
    if (byHand) {
      // **Look at a window, not at a corner.** The corner of the box the windows make is not
      // somewhere a window has to be: one at the top right and one at the bottom left and that
      // corner is empty canvas between them, which is what pressing tidy left you staring at
      // (user, 2026-09-17). So the view goes to whichever window is nearest the corner, with the
      // same GAP of room around it that the corner itself would have had.
      // **And if you were working in one, that is the one it shows you.** The window in front is the
      // one you last touched, so tidy takes you back to it rather than to whatever happens to lie
      // nearest the corner — with its group, because a member on its own is half a thing to look at
      // (user, 2026-09-17). Nothing in front, or in front on another canvas: the corner-most window.
      const mine_ = mine.map((t) => layout[t.id]);
      const front = tiles.get(focused);
      const lead = (front && front.s.canvas === canvasId && layout[focused])
        ? blockOf(groupOf(focused))
        : mine_.reduce((a, q) => (a && a.x + a.y <= q.x + q.y ? a : q), null);
      const smooth = !matchMedia('(prefers-reduced-motion: reduce)').matches;
      cvScroll.scrollTo({ left: Math.max(0, originX + lead.x - GAP),
                          top: Math.max(0, originY + lead.y - GAP),
                          behavior: smooth ? 'smooth' : 'auto' });
    } else {
      cvScroll.scrollLeft = Math.max(0, l0 - sx);
      cvScroll.scrollTop = Math.max(0, t0 - sy);
    }
  }
  renderMinimap();
  refreshOff();
  paintTidy();
  paintGroups();   // every window on the canvas just moved, and the frame is drawn from where they are
  return !!(sx || sy);
}

// Where a new window goes: **the first gap anywhere on the canvas, and only then below everything.**
//
// It used to scan the viewport alone, so once that was full every new terminal went under the last
// one and the canvas grew downwards for ever — even with the top half emptied by closing things
// (user, 2026-09-14). The canvas is already as big as its contents; looking at all of it costs the
// same scan over a slightly larger box and reuses the room that is actually there.
//
// **Reading order**, left to right then down, because that is where the eye expects the next thing.
// The grid is the dot grid, so a new window lands aligned with the ones already placed.
function firstFree(w, h, canvasId) {
  const rects = [];
  for (const t of tiles.values()) { if (t.s.canvas === canvasId && layout[t.id]) rects.push(layout[t.id]); }
  // As big as the viewport, or as big as what is already on the canvas — whichever is larger. The
  // extra row and column of slack let a window land just past the current edge rather than starting
  // a new pile below, which is the case that made this look broken.
  const W = Math.max(cvScroll.clientWidth, rects.reduce((m, r) => Math.max(m, r.x + r.w), 0) + GAP + w);
  const H = Math.max(cvScroll.clientHeight, rects.reduce((m, r) => Math.max(m, r.y + r.h), 0) + GAP + h);
  const hit = (x, y) => rects.some((r) => x < r.x + r.w + GAP && x + w + GAP > r.x && y < r.y + r.h + GAP && y + h + GAP > r.y);
  // **Where you are looking, first** (user, 2026-09-17). The canvas is far bigger than the screen, and
  // a new terminal opening at the board's top-left corner is a new terminal you have to go and find.
  // This is a first pass, not the rule: scanning the viewport and stopping there is what used to pile
  // windows downwards for ever once it was full (2026-09-14, the test below this one), so if nothing
  // in view can hold it the whole canvas is searched exactly as before.
  const vx = Math.round(cvScroll.scrollLeft - originX), vy = Math.round(cvScroll.scrollTop - originY);
  for (let y = vy + GAP; y + h <= vy + cvScroll.clientHeight; y += GRID)
    for (let x = vx + GAP; x + w <= vx + cvScroll.clientWidth; x += GRID)
      if (!hit(x, y)) return { x, y };
  // **And when it cannot, the nearest gap to the view rather than the board's corner.** With a
  // window or two in sight there is often no room left in view for a third, and falling straight
  // back to the top-left of the board put it somewhere the person had to go and find — the very
  // thing the pass above was for (user, 2026-09-17: "그 다음 터미널은 기존의 원점에서 생성되는거
  // 같아"). Same scan, same gaps, same fallback under it; it just takes the closest one now.
  const cx = vx + cvScroll.clientWidth / 2, cy = vy + cvScroll.clientHeight / 2;
  let best = null, bestD = Infinity;
  for (let y = GAP; y + h <= H; y += GRID) {
    const ody = y + h / 2 - cy;
    if (ody > 0 && ody * ody >= bestD) break;      // every row below this one is further still
    for (let x = GAP; x + w <= W; x += GRID) {
      if (hit(x, y)) continue;
      const ox = x + w / 2 - cx, d = ox * ox + ody * ody;
      if (d < bestD) { bestD = d; best = { x: x, y: y }; }
    }
  }
  if (best) return best;
  // Nothing fits anywhere — every gap is smaller than this window. Below the lot, and the canvas grows.
  const bottom = rects.reduce((m, r) => Math.max(m, r.y + r.h), 0);
  return { x: GAP, y: bottom ? bottom + GAP : GAP };
}

// ── push-aside (decisions.md "겹치지 않는다. 새 창이 옆을 민다") ──────────────────────
// Windows do not overlap. Land one on another and the one that was there moves — by exactly the overlap
// and no further ("겹친 만큼만 최단 거리로"), and if that lands it on a third, the third goes the same way.
// **This is not tiling.** Sizes belong to the user; the only thing palmar resolves is overlap
// (AGENTS.md "밀어내기를 타일링으로 바꾸지 마라"). Shrinking a window therefore pulls nothing back.
//
// It runs **when the hand lets go**, not during the drag. Pushing live means the path you drag along
// bulldozes whatever it crosses even when you carry straight on past it, and the window you were aiming
// at has already fled by the time you arrive. On drop, the neighbours slide out from under the window
// you just placed — which is also what the toast then says happened.
//
// Where it does not run: opening a window. firstFree already puts it where there is room, and since the
// canvas grows without limit (⑩) that search cannot fail, so there is never anything to push. The other
// half of the decision — a **new** terminal sits down and shoves — needs somewhere on screen for a person
// to aim it, and there is no click-to-place yet. That is a person's call, not this file's.
// ── undo (2026-09-14, 사용자) ───────────────────────────────────────────────
// **One way back for everything that moves a window.** Push-aside came with an undo in its toast,
// grouping came with another, and neither survived the toast going away — so a person who looked up
// a second too late had no way back at all ("돌이킬 수가 없네"). A single stack is less to learn and
// less to build: every operation that touches the layout takes a snapshot first, and `Ctrl Z` puts
// the last one back.
//
// **Whole-canvas snapshots, not inverse operations.** An inverse has to be written once per
// operation and is wrong in a different way each time; a snapshot of the twenty-odd numbers on a
// canvas is small, and restoring it cannot be subtly wrong.
const UNDO_MAX = 40;
const undoStack = [];

function layoutSnap(canvasId) {
  const out = {};
  for (const t of tiles.values()) {
    if (t.s.canvas !== canvasId || !layout[t.id]) continue;
    const r = layout[t.id];
    out[t.id] = { x: r.x, y: r.y, w: r.w, h: r.h, g: r.g || null,
                  kind: r.kind || null, path: r.path || null, canvas: r.canvas || null };
  }
  return out;
}

//: Call **before** the change, with a name for the toast. Returns nothing — a no-op when the canvas
//: is unknown, which is the `current === null` case (a daemon that does not know canvases).
function undoMark(label, canvasId) {
  if (canvasId === undefined) canvasId = current;
  undoStack.push({ label, canvas: canvasId, snap: layoutSnap(canvasId) });
  if (undoStack.length > UNDO_MAX) undoStack.shift();
  paintUndo();
}

function undoLast() {
  const step = undoStack.pop();
  if (!step) { toast(['nothing to undo on this canvas']); return false; }
  for (const [id, was] of Object.entries(step.snap)) {
    // A viewer that was closed comes back: it is not a session, so nothing else would ever re-create it,
    // and "undid closing a viewer" that restored nothing was a lie (review, 2026-09-15).
    if (!tiles.get(id) && was.kind === 'file' && was.path) {
      layout[id] = { x: was.x, y: was.y, w: was.w, h: was.h, kind: 'file', path: was.path,
                     canvas: canvases.has(was.canvas) ? was.canvas : current };
      tiles.set(id, new Viewer(id, was.path, layout[id].canvas));
    }
    const t = tiles.get(id);
    if (!t || !layout[id]) continue;
    const next = Object.assign({}, layout[id], { x: was.x, y: was.y, w: was.w, h: was.h });
    if (was.g) next.g = was.g; else delete next.g;
    layout[id] = next;
    // **Write the intended value; never read it back.** left/top are mid-transition numbers while
    // the slide runs — the trap tidyCanvas and applyPush both carry a note about.
    t.el.style.left = was.x + 'px';
    t.el.style.top = was.y + 'px';
    t.el.style.width = was.w + 'px';
    t.el.style.height = was.h + 'px';
  }
  saveLayout();
  paintGroups();
  renderMinimap();
  refreshOff();
  paintTidy();
  paintUndo();
  toast([{ b: 'undid' }, step.label]);
  return true;
}

function paintUndo() {
  const b = document.getElementById('undo');
  if (!b) return;
  const n = undoStack.length;
  b.disabled = !n;
  b.title = n ? 'undo ' + undoStack[n - 1].label + ' (' + KMOD + 'Z)'
              : 'undo — nothing has moved yet (' + KMOD + 'Z)';
}

// ── groups (2026-09-14, 사용자) ─────────────────────────────────────────────
// **Hold a window still over another and they travel together.** Asked for as "끌어다가 다른
// 터미널 위에 올려놓고 몇 초 이상 가만히 두면 그 두 터미널은 그룹화" — a lighter thing than a
// canvas: a canvas is a different workbench, a group is a set that lives together on one.
//
// It fits here because push-aside already settled when overlap is allowed: **windows may overlap
// while the hand is down**, and only the drop resolves it. So the hold happens in a moment that
// already exists, and nothing had to be loosened to make room for it.
//
// Membership rides in the same store as the position (⑩ provisional, `palmar-tiles`) — and lands in
// the same undecided as the coordinates do (③, #2). A group is an id, kept on each member.
const GROUP_LEAD_MS = 280;      // quiet moment after the overlap before the gauge starts
const GROUP_HOLD_MS = 1100;     // and this long filling, over another window, and they join (1500 read as slow)
const OVER_TAKE = 0.20;         // this much of the dragged window covered before it takes a target
const OVER_KEEP = 0.05;         // and it holds that target until this little is left

//: **A group lives on one canvas** — "a canvas is a different workbench, a group is a set that lives
//: together on one". Membership is a single field in the store and the daemon may move a session to
//: another canvas without touching it, which left the moved window still in the party: dragged
//: invisibly from a canvas it is no longer on, and packed into a block it cannot be seen in.
function groupOf(id) {
  const g = layout[id] && layout[id].g;
  if (!g) return [id];
  const me = tiles.get(id);
  const cv = me && me.s.canvas;
  const out = [];
  for (const t of tiles.values())
    if (layout[t.id] && layout[t.id].g === g && (!cv || t.s.canvas === cv)) out.push(t.id);
  return out.length ? out : [id];
}

//: The rectangle a group occupies — the union of its members. Push-aside moves **blocks**, and a
//: group is one block; without this a push could walk through the middle of a group and take it apart.
function groupRect(ids) {
  let r = null;
  for (const id of ids) {
    const q = layout[id];
    if (!q) continue;
    if (!r) { r = { x: q.x, y: q.y, w: q.w, h: q.h }; continue; }
    const x2 = Math.max(r.x + r.w, q.x + q.w), y2 = Math.max(r.y + r.h, q.y + q.h);
    r.x = Math.min(r.x, q.x); r.y = Math.min(r.y, q.y);
    r.w = x2 - r.x; r.h = y2 - r.y;
  }
  return r;
}

// A group as one movable thing: the bounding box for moving it, `cells` for deciding what it hits.
function blockOf(ids) {
  const r = groupRect(ids);
  if (!r) return null;
  r.cells = ids.filter((id) => layout[id]).map((id) => {
    const q = layout[id];
    return { dx: q.x - r.x, dy: q.y - r.y, w: q.w, h: q.h };
  });
  return r;
}

//: Which window the one being dragged is over, and **which side of it** — the target with the
//: largest overlap, and the direction from its centre to ours.
//:
//: **The pointer was the wrong question.** It used to hit-test `elementsFromPoint` at the cursor,
//: and the cursor is on the title bar, which is the dragged window's **top edge** — so the only way
//: to reach another window was to put your top edge on it, and every group grew upwards
//: (user, 2026-09-14). It also meant a member sticking out of a ragged group could not be aimed at,
//: because the pointer never got near it. The rectangle knows all of that; the pointer never did.
//:
//: **It has to hold on to the one it found.** Any overlap at all used to count, so at the edge of a
//: window the answer flickered between that window and nothing as the hand moved, and every flicker
//: restarted the hold. The gauge then climbed only as fast as the overlap was deep enough to keep
//: the answer steady — which is exactly how it was reported: "게이지가 시간에 따라 올라가길 바랬는데
//: 지금은 얼마나 터미널이 많이 겹치느냐에 따라 게이지가 올라가는 것 같아" (2026-09-14). So there are
//: two thresholds, not one: a fifth of the dragged window has to be over something before it is taken
//: as a target, and once taken it is kept until almost nothing is left of the overlap. `current` is
//: the target already being held.
function paneOver(id, ignore, current) {
  const me = layout[id];
  if (!me) return null;
  // **A share of the smaller of the two, not of the one in your hand.** Measured against the dragged
  // window alone, a big window could never take a small one as a target at all: a default 520x360 pane
  // would need 37,000px² of overlap and a window at the minimum size has only 24,000px² to give. Every
  // test used two windows of one size, where the two readings are the same number.
  let best = null, bestArea = 0, curArea = 0, bestOf = 1, curOf = 1;
  for (const t of tiles.values()) {
    if (ignore && ignore.indexOf(t.id) >= 0) continue;
    if (!t.visible() || t.s.canvas !== tiles.get(id).s.canvas) continue;
    const r = layout[t.id];
    if (!r) continue;
    const w = Math.min(me.x + me.w, r.x + r.w) - Math.max(me.x, r.x);
    const h = Math.min(me.y + me.h, r.y + r.h) - Math.max(me.y, r.y);
    if (w <= 0 || h <= 0) continue;
    const area = w * h;
    const of = Math.max(1, Math.min(me.w * me.h, r.w * r.h));   // the smaller window is the measure
    if (t.id === current) { curArea = area; curOf = of; }
    if (area > bestArea) { bestArea = area; best = t.id; bestOf = of; }
  }
  // Stay with the one already held unless it has nearly slid off, or something else is clearly more
  // covered — "clearly" so that two candidates a few pixels apart cannot trade the hold back and forth.
  let held = false;
  if (curArea >= curOf * OVER_KEEP && bestArea < curArea * 1.5) { best = current; bestArea = curArea; held = true; }
  if (!best) return null;
  if (!held && bestArea < bestOf * OVER_TAKE) return null;   // only brushing it, and not already held
  // **The side is where the hand is carrying it**, measured centre to centre and taken on the axis
  // it has moved furthest along — so nudging it rightwards means "to the right", not "slightly down".
  const r = layout[best];
  const dx = (me.x + me.w / 2) - (r.x + r.w / 2);
  const dy = (me.y + me.h / 2) - (r.y + r.h / 2);
  const side = Math.abs(dx) >= Math.abs(dy) ? (dx >= 0 ? 'right' : 'left')
                                            : (dy >= 0 ? 'below' : 'above');
  return { id: best, side };
}

//: Where it would land: beside the target, on that side, at the size it already is. **Not the exact
//: final position** — the group re-arranges after joining — but the side it will end up on, which is
//: the thing a person is choosing while they hold it there.
//: **What joins is the block, not the window in the hand.** Putting only that window beside the
//: target left the one behind it standing exactly on top of the target's own mate — the whole window,
//: 220x150 of it, in seven of the thirty-two ways two pairs of windows can meet (measured
//: 2026-09-17, after the user: "두 그룹 끼리 묶을때는 묶은 직후 두 터미널이 완전히 겹쳐있는 경우도
//: 있어"). The push that follows could not save it either: it resolves what the *anchor* overlaps,
//: and the anchor was the one window that had landed cleanly. So the rectangle that has to clear the
//: target is the carried group's, and everyone inside keeps their place in it.
function joinBlock(overId, side, meId) {
  const r = layout[overId], blk = blockOf(groupOf(meId));
  if (!r || !blk) return null;
  const at = (x, y) => ({ x: x, y: y, w: blk.w, h: blk.h, blk: blk });
  // **And it clears the mates standing in its way, but only those.** Aiming at one member of a group
  // and clearing only that member dropped the block straight onto the member behind it — under a
  // column, or to the left of a row. Clearing the target's whole block instead would have been the
  // easy answer and the wrong one: it would undo "under a shorter neighbour touches that neighbour",
  // where a tall window two columns over has no business setting the landing height. So the cross
  // axis stays where the aim put it, and only the mates that actually stand in that band count.
  const vert = side === 'above' || side === 'below';
  const band = groupOf(overId).map((id) => layout[id]).filter((q) => q && (vert
    ? (q.x < r.x + blk.w && q.x + q.w > r.x)
    : (q.y < r.y + blk.h && q.y + q.h > r.y)));
  if (side === 'right') return at(Math.max.apply(null, band.map((q) => q.x + q.w)) + GAP, r.y);
  if (side === 'left') return at(Math.min.apply(null, band.map((q) => q.x)) - GAP - blk.w, r.y);
  if (side === 'below') return at(r.x, Math.max.apply(null, band.map((q) => q.y + q.h)) + GAP);
  return at(r.x, Math.min.apply(null, band.map((q) => q.y)) - GAP - blk.h);
}
//: The same answer narrowed to one window — where *this* window ends up once its block has landed.
//: Carried alone, the block is the window, and this is what it has always been.
function joinPreview(overId, side, meId) {
  const b = joinBlock(overId, side, meId), me = layout[meId];
  if (!b || !me) return null;
  return { x: b.x + (me.x - b.blk.x), y: b.y + (me.y - b.blk.y), w: me.w, h: me.h };
}

//: **Two ways of showing the hold, and the person picks.** `ring` runs a line round the border of the
//: window in the hand; `grow` fills the landing box from the edge that touches the target towards the
//: far one, so where and how long are one picture ("예측 지점으로 사라락 확장되는 느낌", 2026-09-15);
//: `both` shows both. grow is the default: the eye is on the landing box, not on the window under the
//: hand, and one clock is calmer than two. The choice is the browser's, like the theme.
const LS_HOLD = 'palmar.hold';
const HOLD_STYLES = ['grow', 'ring', 'both'];
let holdStyle = 'grow';
try { const v = localStorage.getItem(LS_HOLD); if (HOLD_STYLES.indexOf(v) >= 0) holdStyle = v; } catch (e) {}
function applyHoldStyle(v) {
  holdStyle = HOLD_STYLES.indexOf(v) >= 0 ? v : 'grow';
  for (const h of HOLD_STYLES) document.body.classList.toggle('hold-' + h, h === holdStyle);
  try { if (holdStyle === 'grow') localStorage.removeItem(LS_HOLD); else localStorage.setItem(LS_HOLD, holdStyle); } catch (e) {}
}
applyHoldStyle(holdStyle);

let ghostEl = null;
// The landing box, and how far the hold has come: `side` is the edge it fills from — the one against
// the target — and `pct` how much of it is filled. 100 is armed, and the box says so by going solid.
function showGhost(box, side, pct) {
  if (!box) { if (ghostEl) { ghostEl.remove(); ghostEl = null; } return; }
  if (!ghostEl) { ghostEl = el('div', 'ghost'); cvWorld.appendChild(ghostEl); }
  ghostEl.style.left = box.x + 'px';
  ghostEl.style.top = box.y + 'px';
  ghostEl.style.width = box.w + 'px';
  ghostEl.style.height = box.h + 'px';
  ghostEl.dataset.side = side || 'right';
  const p = Math.max(0, Math.min(100, pct || 0));
  ghostEl.style.setProperty('--p', p.toFixed(1));
  ghostEl.classList.toggle('full', p >= 100);
}

//: The gauge, and then the moment it is full.
//:
//: **A hold nobody can see is a hold nobody trusts.** The gesture is standing still, which from the
//: inside is indistinguishable from nothing happening, so "how much longer" has to be on screen —
//: as a line travelling the dragged window's own border, because that is the window the answer is
//: about ("테두리를 타고 게이지 차는 듯한 효과", 2026-09-14). Full circle means it will join; moving
//: away empties it at once.
function markHold(id, on) {
  const t = tiles.get(id);
  if (!t) return;
  t.el.classList.toggle('joining', on === true);
  t.el.classList.toggle('joinready', on === 'ready');
}

function setGauge(id, pct) {
  const t = tiles.get(id);
  if (!t) return;
  if (pct <= 0) {
    t.el.classList.remove('arming');
    t.el.style.removeProperty('--p');
    return;
  }
  t.el.classList.add('arming');
  t.el.style.setProperty('--p', Math.min(100, pct).toFixed(1));   // not rounded: whole percents are steps
}

function undoJoin(changed) {
  if (!changed) return;
  for (const c of changed) {
    if (!layout[c.id]) continue;
    const next = Object.assign({}, layout[c.id]);
    if (c.was) next.g = c.was; else delete next.g;
    layout[c.id] = next;
  }
  saveLayout();
  paintGroups();
}

//: Lay a group's members out as one tidy block, from where the block already is.
//:
//: **A group that leaves everyone where they were is not a group, it is a colour.** The windows keep
//: whatever sizes you gave them (palmar never resizes a window — AGENTS.md), so they are lined up by
//: their tops on rows as wide as the widest member, which is the arrangement that looks deliberate
//: without pretending to be a tiler ("딱딱 나름 정렬되게", 2026-09-14).
// Every member of a group, live or not — what is left of a group is what has to close up.
function groupMembers(g) {
  return [...tiles.values()].filter((t) => layout[t.id] && layout[t.id].g === g).map((t) => t.id);
}

function place(id, x, y) {
  const t = tiles.get(id);
  layout[id] = Object.assign({}, layout[id], { x, y });
  if (t) { t.el.style.left = x + 'px'; t.el.style.top = y + 'px'; }
}

//: **Joining puts the window exactly where its preview was, and nothing else moves it.** Three
//: arrangers came before this one — by age, then wide-first rows, then rows as the hand left them —
//: and each re-laid the whole group out after the drop, so the window landed somewhere the preview
//: had not shown: beside instead of below, under the left one instead of the right, a row's height
//: under a shorter neighbour (user, 2026-09-15: "붕 떠 있게 돼"). The preview is a promise. What the
//: drop displaces is pushed out of the way inside the group by exactly the overlap — the same push
//: as the canvas — and that is all.
function dropInto(meId, targetId, side) {
  const spot = joinBlock(targetId, side || 'right', meId);
  //: **A group lands as the group you carried.** This used to place the window in the hand and
  //: nothing else, so carrying a pair onto an outside window put one of them beside the target and
  //: left the other wherever the drag had ended — a row came down stacked, and the two were still
  //: grouped, so it looked as though the group had rearranged itself (user, 2026-09-17: "좌우로
  //: 붙어있던게 움직이다보면 상하 배치로 바뀔 때도 있어"). The mates are read **before** the join,
  //: or the target's own group would be carried too, and every one of them moves by the step the
  //: preview asks of the hand's window. The ghost still shows that window, and it still lands
  //: exactly there; the rest keep the shape they had when you picked them up.
  const carried = groupOf(meId);
  if (spot) {
    const dx = spot.x - spot.blk.x, dy = spot.y - spot.blk.y;
    for (const id of carried) {
      const r = layout[id];
      if (r) place(id, r.x + dx, r.y + dy);
    }
  }
  joinGroups(meId, targetId);
  const ids = groupOf(meId);
  const me = tiles.get(meId);
  if (me && pushOn) {
    const inner = pushAside(me.s.canvas, meId, { only: ids, solo: true });
    if (inner.length) applyPush(inner);
  }
  saveLayout();
  paintGroups();
  return ids;
}

//: **Closing up means re-attaching what came loose — and nothing else moves.** Gravity came first:
//: every member pulled up, then left. It closed holes, and it also tidied groups nobody had asked it
//: to: a window sitting under a short neighbour, with nothing to its left at that height, slid left
//: the first time the group was so much as moved (user, 2026-09-15: "벽끼리 맞닿아 있기만 하면 딱
//: 좋은데 … 움직일 때 배치가 바뀌네"). Touching is the whole invariant. So: split the group into the
//: pieces that still touch each other; the piece holding the anchor — or, with none, the top-left
//: member — stays put; every other piece moves **as one block**, up until it meets the fixed set or
//: the group's top, then left the same way, and joins it. A group whose members all touch is left
//: exactly as it is. Members touch when a GAP or less separates them, on either axis.
function adjacent(a, b) {
  return a.x <= b.x + b.w + GAP && a.x + a.w + GAP >= b.x &&
         a.y <= b.y + b.h + GAP && a.y + a.h + GAP >= b.y;
}
function compactGroup(ids, anchorId) {
  const mine = ids.filter((id) => layout[id] && tiles.get(id));
  if (mine.length < 2) return false;
  const x0 = Math.min(...mine.map((id) => layout[id].x));
  const y0 = Math.min(...mine.map((id) => layout[id].y));
  const pieces = () => {
    const seen = new Set(), out = [];
    for (const id of mine) {
      if (seen.has(id)) continue;
      const piece = [id]; seen.add(id);
      for (let i = 0; i < piece.length; i++)
        for (const o of mine)
          if (!seen.has(o) && adjacent(layout[piece[i]], layout[o])) { seen.add(o); piece.push(o); }
      out.push(piece);
    }
    return out;
  };
  let moved = false;
  for (let pass = 0; pass < 8; pass++) {
    const ps = pieces();
    if (ps.length < 2) break;
    const topLeft = [...mine].sort((a, b) => (layout[a].y - layout[b].y) || (layout[a].x - layout[b].x))[0];
    const fixedIdx = ps.findIndex((pc) => pc.indexOf(anchorId) >= 0 || (anchorId == null && pc.indexOf(topLeft) >= 0));
    const fixed = ps[fixedIdx >= 0 ? fixedIdx : 0];
    const loose = ps.find((pc, i) => i !== (fixedIdx >= 0 ? fixedIdx : 0));
    // Up: the block may rise until one of its members meets a fixed member below which it sits.
    let dy = Infinity;
    for (const id of loose) {
      const m = layout[id];
      let floor = y0;
      for (const f of fixed) { const r = layout[f];
        if (r.y + r.h <= m.y && r.x < m.x + m.w && r.x + r.w > m.x) floor = Math.max(floor, r.y + r.h + GAP); }
      dy = Math.min(dy, m.y - floor);
    }
    dy = Math.max(0, dy);
    for (const id of loose) if (dy) place(id, layout[id].x, layout[id].y - dy);
    // Then left, the same way.
    let dx = Infinity;
    for (const id of loose) {
      const m = layout[id];
      let wall = x0;
      for (const f of fixed) { const r = layout[f];
        if (r.x + r.w <= m.x && r.y < m.y + m.h && r.y + r.h > m.y) wall = Math.max(wall, r.x + r.w + GAP); }
      dx = Math.min(dx, m.x - wall);
    }
    dx = Math.max(0, dx);
    for (const id of loose) if (dx) place(id, layout[id].x - dx, layout[id].y);
    if (!dx && !dy) break;          // nothing to move it against — it stays where it is
    moved = true;
  }
  if (moved) { saveLayout(); paintGroups(); }
  return moved;
}

//: **A group's name rides on its members.** A table of its own would need pruning — a name whose
//: group has lost every window is an orphan nobody sweeps up — and this way the name has exactly the
//: life `g` already has. Every member carries the same string; this reads the first that has one, so
//: a member joining without it is not a group losing its name.
function groupName(g) {
  for (const t of tiles.values()) {
    const r = layout[t.id];
    if (r && r.g === g && r.gn) return r.gn;
  }
  return '';
}
function setGroupName(g, name) {
  const n = (name || '').trim().slice(0, 64);
  for (const t of tiles.values()) {
    const r = layout[t.id];
    if (!r || r.g !== g) continue;
    const e = Object.assign({}, r);
    if (n) e.gn = n; else delete e.gn;
    layout[t.id] = e;
  }
  saveLayout();
  paintGroups();
}

function newGroupId() {
  return 'g' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

//: Join `a`'s group and `b`'s group into one. Returns the ids that changed, for the undo.
function joinGroups(a, b) {
  const mine = groupOf(a), theirs = groupOf(b);
  const g = (layout[a] && layout[a].g) || (layout[b] && layout[b].g) || newGroupId();
  const changed = [];
  for (const id of mine.concat(theirs)) {
    if (!layout[id] || layout[id].g === g) continue;
    changed.push({ id, was: layout[id].g || null });
    layout[id] = Object.assign({}, layout[id], { g });
  }
  saveLayout();
  paintGroups();
  return changed;
}

// A group of one is not a group. Both ways out of a group end here — walking away from it, and being
// closed — so that neither can leave a window wearing a colour and a promise that means nothing.
// Returns what is left of the group, which is what a caller has to close up.
function dissolveIfAlone(g) {
  const rest = [...tiles.values()].filter((t) => layout[t.id] && layout[t.id].g === g);
  if (rest.length === 1) {
    const only = Object.assign({}, layout[rest[0].id]);
    delete only.g;
    layout[rest[0].id] = only;
    return [];
  }
  return rest.map((t) => t.id);
}

function leaveGroup(id) {
  if (!layout[id] || !layout[id].g) return null;
  const was = layout[id].g;
  const left = Object.assign({}, layout[id]);
  delete left.g;
  layout[id] = left;
  dissolveIfAlone(was);
  saveLayout();
  paintGroups();
  return was;
}

//: The colour is derived from the id, so a group looks the same on every browser without the daemon
//: knowing anything about groups (protocol.md "없는 것").
//: **One frame drawn behind the members, not a tint on each.** A border on every window said "these
//: four are related" four times and left the middle empty; a single translucent rectangle says it
//: once and gives the group an edge you can see it keep ("큰 테두리의 사각형 안에… 반투명의 배경색",
//: 2026-09-14). It sits under the tiles and takes no pointer events, so nothing about dragging,
//: hit-testing or the resize grip changes.
const GROUP_PAD = 10;

function groupHue(g) {
  let n = 0;
  for (let i = 0; i < g.length; i++) n = (n * 31 + g.charCodeAt(i)) >>> 0;
  return n % 360;
}

function paintGroups() {
  const want = new Map();
  for (const t of tiles.values()) {
    const g = layout[t.id] && layout[t.id].g;
    t.el.classList.toggle('grouped', !!g);
    // **The tint has to be on the window itself.** .tile.grouped reads --group, and --group was set on
    // the frame — a sibling of the windows, not an ancestor — so the whole declaration was invalid and
    // the border fell back to currentColor. It did change colour, which is why it read as working; it
    // was never the group's colour.
    if (!g) t.el.style.removeProperty('--group');
    if (!g || !t.visible()) continue;
    if (!want.has(g)) want.set(g, []);
    want.get(g).push(t.id);
  }
  const keep = new Set();
  for (const [g, ids] of want) {
    if (ids.length < 2) continue;               // a group of one draws nothing
    const r = groupRect(ids);
    if (!r) continue;
    keep.add(g);
    let box = groupBoxes.get(g);
    if (!box) {
      box = el('div', 'gbox');
      cvWorld.appendChild(box);
      groupBoxes.set(g, box);
    }
    box.style.setProperty('--group', 'hsl(' + groupHue(g) + ' 70% 55%)');
    box.classList.toggle('carried', g === carrying);
    // The wrapper is only a coordinate origin; the shape is the cells inside it.
    box.style.left = '0px'; box.style.top = '0px';
    box.style.width = '0px'; box.style.height = '0px';
    // **One padded cell per member, and the shape is their union.** Opaque children inside a
    // translucent parent: overlapping padding does not compound into darker seams the way stacked
    // translucent boxes would, so an ㄱ reads as one shape rather than two rectangles that met.
    // **The name sits on the frame, above its top-left corner.** Double-click to change it, which is
    // the gesture a canvas tab and a window's name already use — one thing to learn, not three.
    let tag = box.gTag;
    if (!tag) {
      tag = box.gTag = el('div', 'gname');
      tag.title = 'name this group — double-click';
      tag.addEventListener('dblclick', (ev) => {
        ev.stopPropagation();
        inlineEdit(tag, groupName(g), (v) => setGroupName(g, v), () => paintGroups());
      });
      box.appendChild(tag);
    }
    const cells = [...box.children].filter((c) => c !== tag);
    for (let i = cells.length; i < ids.length; i++) box.insertBefore(el('div', 'gcell'), tag);
    while (box.children.length - 1 > ids.length) box.firstChild.remove();
    const hue = 'hsl(' + groupHue(g) + ' 70% 55%)';
    ids.forEach((id, i) => {
      const q = layout[id];
      const t = tiles.get(id);
      if (t) t.el.style.setProperty('--group', hue);
      const c = box.children[i];
      // **No floor at zero.** These used to be clamped, from when a window could not be carried past
      // the origin. Once it could, the frame stopped following its own windows up and to the left and
      // sat piled at the corner instead — where it looked like it had latched onto whatever window
      // happened to be there ("백그라운드 색깔이 다른 터미널에 붙게 되", user, 2026-09-17). Pressing
      // tidy appeared to repair it, because tidy puts everything back on the positive side.
      c.style.left = (q.x - GROUP_PAD) + 'px';
      c.style.top = (q.y - GROUP_PAD) + 'px';
      c.style.width = (q.w + GROUP_PAD * 2) + 'px';
      c.style.height = (q.h + GROUP_PAD * 2) + 'px';
    });
    // Above the corner of the whole shape, not of whichever member happened to be first.
    if (!tag.querySelector('input')) tag.textContent = groupName(g);
    tag.classList.toggle('empty', !tag.textContent);
    tag.style.left = (r.x - GROUP_PAD) + 'px';
    tag.style.top = (r.y - GROUP_PAD) + 'px';
  }
  for (const [g, box] of groupBoxes) {
    if (keep.has(g)) continue;
    box.remove();
    groupBoxes.delete(g);
  }
}
const groupBoxes = new Map();
//: **The frame does not glide while it is being carried.** A cell slides to its new place on a 350ms
//: transition, which is right when a group is pushed aside or closes up — and wrong under the hand,
//: where the windows themselves have their transition off (the `drag` class) and the tint was left
//: a third of a second behind them: it hung over whatever the group had just left, so a member could
//: be clear of a window while its colour still covered it (user, 2026-09-17). The group in the hand
//: is named here for the length of the drag, and paintGroups takes its transition off for that long.
let carrying = null;

const PUSH_ROUNDS = 20;                 // termcanvas's limit, the number decisions.md names

// The gap counts: two windows a hair apart read as touching, and that gap is the grid the eye already sees.
function hits(a, b) {
  if (a.cells || b.cells) {
    for (const p of cellsOf(a)) for (const q of cellsOf(b)) if (touches(p, q)) return true;
    return false;
  }
  return touches(a, b);
}

function touches(a, b) {
  return a.x < b.x + b.w + GAP && a.x + a.w + GAP > b.x &&
         a.y < b.y + b.h + GAP && a.y + a.h + GAP > b.y;
}

//: **The outline is not the shape.** A block may hold several windows, and a group laid out as an ㄱ
//: has a corner inside its own bounding box that belongs to nobody. Taking the box as the block claims
//: that corner: the frame drew an ㄱ and the physics pushed a rectangle, so a window dropped in the
//: notch was thrown back out (user, 2026-09-14: "그 공간은 그룹의 공간이 되어서 다른 터미널을 놓을
//: 수가 없어"). A block therefore carries `cells` — its windows, each an offset from its own corner —
//: and collision asks the windows, not the box. A block with no `cells` is a plain rectangle.
function cellsOf(b) {
  if (!b.cells) return [b];
  return b.cells.map((c) => ({ x: b.x + c.dx, y: b.y + c.dy, w: c.w, h: c.h }));
}

// The four ways out of `p`, each the exact distance that clears the gap and not a pixel more.
// Far enough to clear **every** cell of `p`, not just the one that happened to be hit — a group moved
// only clear of its first overlap lands on the next window along.
function shove(p, r, dir) {
  const ps = cellsOf(p);
  const rs = r.cells || [{ dx: 0, dy: 0, w: r.w, h: r.h }];
  let R = -Infinity, L = Infinity, B = -Infinity, T = Infinity;
  for (const a of ps) {
    for (const c of rs) {
      const b = { x: r.x + c.dx, y: r.y + c.dy, w: c.w, h: c.h };
      // Sideways only clears a pair that shares rows; up and down only one that shares columns. A pair
      // that shares neither cannot be in the way whatever happens on that axis.
      if (a.y < b.y + b.h + GAP && a.y + a.h + GAP > b.y) {
        R = Math.max(R, a.x + a.w + GAP - c.dx);
        L = Math.min(L, a.x - GAP - c.w - c.dx);
      }
      if (a.x < b.x + b.w + GAP && a.x + a.w + GAP > b.x) {
        B = Math.max(B, a.y + a.h + GAP - c.dy);
        T = Math.min(T, a.y - GAP - c.h - c.dy);
      }
    }
  }
  const ways = [
    { d: 'r', x: R, y: r.y, by: Math.abs(R - r.x) },
    { d: 'b', x: r.x, y: B, by: Math.abs(B - r.y) },
    { d: 'l', x: L, y: r.y, by: Math.abs(L - r.x) },
    { d: 't', x: r.x, y: T, by: Math.abs(T - r.y) },
  ].filter((w) => isFinite(w.x) && isFinite(w.y));
  // The canvas grows right and down without limit but is pinned at 0 on the other two sides, so left and
  // up can run out of room; right and down never do, so there is always a way out. Ties go to the two
  // directions the canvas grows in, which is why those are first in the list.
  const open = ways.filter((w) => w.x >= 0 && w.y >= 0);
  const best = open.reduce((m, w) => (w.by < m.by ? w : m));
  // **Keep going the way it was already going — when it is barely further.** A row of windows slides
  // over as a row instead of scattering, and every step of a cascade leads away from the window that
  // started it, which is what makes it stop. That rule came in because a neighbour *a few pixels*
  // closer to the bottom than to the right went under a window that had grown sideways, breaking the
  // row that the gesture meant to keep.
  //
  // **A few pixels is the whole of it, and it was taken as any number at all.** Two grouped windows
  // side by side, and growing the left one downward left three pixels of shared column with the one
  // beside it — three pixels out to the right, or four hundred and sixteen down past the bottom it
  // had just grown. It went down, every time, and each further resize sent it down again (user,
  // 2026-09-18, measured: b moved from y=200 to y=616). So the preference wins a tie or near enough
  // to one, and never a landslide.
  const same = dir && open.find((w) => w.d === dir);
  return (same && same.by - best.by <= GAP) ? same : best;
}

// Resolve every overlap on one canvas while holding `anchorId` still — the window the hand just placed is
// the one that keeps its position, everything else gets out of its way. Returns the moves it takes, which
// is also what undo needs; [] when nothing overlapped.
//: `opts.only` — consider just these windows. `opts.solo` — every window is its own block, group or
//: not. Together they mean "sort this group out among itself", which is what a **resize** needs:
//: growing one member makes it overlap its own group-mates, and the ordinary run treats the whole
//: group as one block, so nobody resolved that (user, 2026-09-14 — measured: a member grown to 390px
//: sat on top of the one beside it).
function pushAside(canvasId, anchorId, opts) {
  opts = opts || {};
  const only = opts.only ? new Set(opts.only) : null;
  const mine = [...tiles.values()].filter(
    (t) => t.s.canvas === canvasId && layout[t.id] && (!only || only.has(t.id)));
  if (mine.length < 2) return [];
  // **Blocks, not windows.** A group travels together, so it is pushed together — one rectangle for
  // the whole set. Without this a push could walk between two members and take the group apart, which
  // is the one thing a group is for (2026-09-14).
  const key = (id) => (opts.solo ? id : ((layout[id] && layout[id].g) || id));
  const members = new Map();          // block key → the ids inside it
  for (const t of mine) {
    const k = key(t.id);
    if (!members.has(k)) members.set(k, []);
    members.get(k).push(t.id);
  }
  // **Work on a copy.** A run that hits the round limit has to leave the screen exactly as it was, rather
  // than stop halfway with windows parked where nobody asked for them.
  const box = new Map();
  for (const [k, ids] of members) box.set(k, blockOf(ids));
  const start = new Map([...box].map(([k, r]) => [k, { x: r.x, y: r.y }]));
  const anchorKey = key(anchorId);
  // **A resize pushes the way it grew.** The first wave used to pick the shortest way out, and a
  // neighbour a few pixels closer to the bottom than to the right went *under* the window that had
  // grown sideways — the row was broken by exactly the gesture meant to keep it, and closing up
  // only ever pulls up and left, so nothing put it back (2026-09-15). `opts.dir` is the direction
  // the anchor grew in; a drop passes none and the shortest way still decides.
  let wave = [{ id: anchorKey, dir: opts.dir || null }];
  for (let round = 0; wave.length; round++) {
    if (round >= PUSH_ROUNDS) return [];
    const next = new Map();
    for (const { id, dir } of wave) {
      const p = box.get(id);
      if (!p) continue;
      for (const [oid, r] of box) {
        if (oid === id || oid === anchorKey || !hits(p, r)) continue;
        const w = shove(p, r, dir);
        r.x = w.x; r.y = w.y;
        next.set(oid, { id: oid, dir: w.d });   // shoved twice in one wave: the later shove has the last word
      }
    }
    wave = [...next.values()];
  }
  // Back from blocks to windows: every member moves by its block's delta.
  const moves = [];
  for (const [k, r] of box) {
    const from = start.get(k);
    const dx = r.x - from.x, dy = r.y - from.y;
    if (!dx && !dy) continue;
    for (const id of members.get(k)) {
      const was = layout[id];
      moves.push({ id, x0: was.x, y0: was.y, x: was.x + dx, y: was.y + dy });
    }
  }
  return moves;
}

// Put the moves on screen. The CSS transition on .tile does the sliding, so this is also the animation.
function applyPush(moves) {
  for (const m of moves) {
    const t = tiles.get(m.id), r = layout[m.id];
    if (!t || !r) continue;
    // **Write the intended value; never read it back.** left/top are mid-transition numbers while the
    // slide runs — the same trap tidyCanvas documents, and saving one of those puts the old position back.
    t.el.style.left = m.x + 'px';
    t.el.style.top = m.y + 'px';
    layout[m.id] = Object.assign({}, r, { x: m.x, y: m.y });
  }
  saveLayout();
  paintGroups();     // a pushed group carries its frame with it
  renderMinimap();
  refreshOff();
  paintTidy();
}

// Called when a window is let go. The name in the toast is read off the title bar rather than rebuilt from
// the session, so it always says the words that are on the window itself.
function settle(anchorId, opts) {
  opts = opts || {};
  const t = tiles.get(anchorId);
  if (!t) return;
  // **Inside the group first, then the canvas.** A group is one block to the outside world, so the
  // ordinary run cannot see an overlap *between its own members* — which is exactly what growing one
  // of them makes. Sorting the group out first also settles its outer shape, so the run after it
  // works from the rectangle the group really ends up with. Between the two the group closes up
  // (compactGroup) — not on a join, where the preview has already said where everything is.
  const mates = groupOf(anchorId);
  const inner = (pushOn && mates.length > 1)
    ? pushAside(t.s.canvas, anchorId, { only: mates, solo: true, dir: opts.dir })
    : [];
  if (inner.length) applyPush(inner);
  if (opts.compact !== false && mates.length > 1) compactGroup(mates, anchorId);
  if (!pushOn) return;                  // the switch in the shortcuts panel — closing up is not pushing
  const outer = pushAside(t.s.canvas, anchorId);
  if (outer.length) applyPush(outer);
  const moves = inner.concat(outer);
  if (!moves.length) return;
  // **No undo of its own any more.** The drag that caused it already took a snapshot, so one Ctrl Z
  // puts back the move and the push together — which is what a person means by "undo that".
  toast([{ b: t.nameEl.textContent || 'window' },
         'moved ' + moves.length + (moves.length > 1 ? ' windows' : ' window') + ' aside —',
         { d: KMOD + 'Z undoes it' }]);
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
//: **What a maximised window fills is the viewport, and nothing it sits inside measures that.** The
//: CSS said `calc(100% - 20px)`, which worked while tiles were children of the scroller; they live in
//: `.cv-world` now, which is 0x0 on purpose because it is only an origin — so 100% was 0 and pressing
//: expand turned the window into a dot (user, 2026-09-18). The view is scrolled to the pad's corner
//: first, so the visible region starts at pad zero; a tile inside `.cv-world` renders at
//: `origin + left`, which is why the margin has the origin taken off it.
const MAX_PAD = 10;
function maxBox(tile) {
  tile.el.style.setProperty('--max-l', (MAX_PAD - originX) + 'px');
  tile.el.style.setProperty('--max-t', (MAX_PAD - originY) + 'px');
  tile.el.style.setProperty('--max-w', (cvScroll.clientWidth - MAX_PAD * 2) + 'px');
  tile.el.style.setProperty('--max-h', (cvScroll.clientHeight - MAX_PAD * 2) + 'px');
}
function setMax(tile, on) {
  for (const t of tiles.values()) t.el.classList.remove('max');
  const was = maxed; maxed = null;
  cv.classList.toggle('has-max', !!on);
  if (on) {
    prevScroll = { l: cvScroll.scrollLeft, t: cvScroll.scrollTop };
    cvScroll.scrollTo(0, 0);
    tile.el.classList.add('max');
    maxBox(tile);                  // after the class, so the scrollbars are already gone from clientWidth
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
  // **Ctrl/⌘ Z, and only outside a terminal.** Inside one it belongs to whatever is running there —
  // an editor's undo is not ours to take (the same rule Esc follows just below, and Ctrl-C above).
  if ((e.key === 'z' || e.key === 'Z') && (e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey) {
    if (e.target && e.target.closest && e.target.closest('.xterm, input, textarea')) return;
    e.preventDefault();
    undoLast();
    return;
  }
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
  railSync('l'); railSync('r');
  if (maxed) { maxBox(maxed); maxed.refit(); } renderMinimap(); refreshOff();
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

//: Push-aside, and a switch for it. **It starts on** — not overlapping is the decided behaviour
//: (decisions.md "겹치지 않는다"), and unlike auto-tidy what it moves is only ever what your own hand just
//: landed on. The switch exists because the person who owns that decision asked for one after using it
//: (2026-09-14), which reverses "겹치기 설정을 만들지 마라" from 2026-09-07.
//: **Only the off state is written.** On is the default, so an empty localStorage means on, and a browser
//: that refuses storage gets the behaviour rather than the exception.
//: Off does not mean chaos: focusTile already raises the window you click, which is what makes a stack
//: usable instead of merely untidy.
const LS_PUSH = 'palmar.push';
let pushOn = true;
try { pushOn = localStorage.getItem(LS_PUSH) !== '0'; } catch (e) {}

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
//: **The one place that decides a rail's width, folding included.** railClamp floors at RAIL_MIN, so
//: anything that re-applies a folded rail's own width springs it back to 180px while the fold state
//: still says folded — the rail looks open, pressing fold does nothing, and you have to unfold it
//: first. That is what a window resize did (reported 2026-09-11: minimise and restore, and the rail
//: is back without being back). Folding is a 0 that has to be set past the clamp, so every path that
//: sets a width has to come through here.
const RAIL_STRIP = 30;    // a folded rail keeps this much: the strip that brings it back (2026-09-15)
function railSync(side) {
  if (railFolded[side]) document.documentElement.style.setProperty('--rail-' + side, RAIL_STRIP + 'px');
  else railPut(side, railW(side));
}
function setRail(side, px, save) {
  railPut(side, px);
  if (save !== false) saveRails();
  if (maxed) { maxBox(maxed); maxed.refit(); }
  renderMinimap();
  refreshOff();
}
function saveRails() {
  try { localStorage.setItem(LS_RAILS, JSON.stringify({ l: railW('l'), r: railW('r') })); } catch (e) {}
}

// ── collapsing a rail to reclaim the canvas ────────────────────────────────────────────────
// A collapsed rail is 0 wide (the grid column vanishes) with the body marked so the grip and the
// header hide; a small tab in its place brings it back. The width it had is kept, so expanding
// returns to it rather than the default.
const LS_FOLD = 'palmar.railfold';
const railFolded = { l: false, r: false };
const preFold = { l: RAIL_DEF.l, r: RAIL_DEF.r };

function applyFold(side) {
  const folded = railFolded[side];
  document.body.classList.toggle('fold-' + side, folded);
  // **0, past the clamp** when folded — railClamp floors at RAIL_MIN, so folding through it would
  // leave a 180px empty strip. Coming back, the remembered width rather than the current 0.
  if (folded) railSync(side);
  else railPut(side, preFold[side]);
  const fold = $('#fold-' + side), open = $('#open-' + side);
  if (open) open.hidden = !folded;
  if (fold) fold.setAttribute('aria-expanded', String(!folded));
  if (maxed) { maxBox(maxed); maxed.refit(); }
  renderMinimap();
  refreshOff();
}

function setFold(side, folded) {
  if (railFolded[side] === folded) return;
  if (folded) preFold[side] = railW(side) || RAIL_DEF[side];   // remember the width to come back to
  railFolded[side] = folded;
  applyFold(side);
  try { localStorage.setItem(LS_FOLD, JSON.stringify(railFolded)); } catch (e) {}
}

function loadFold() {
  let v = null;
  try { v = JSON.parse(localStorage.getItem(LS_FOLD) || 'null'); } catch (e) {}
  if (!v) return;
  for (const side of ['l', 'r']) if (v[side]) { railFolded[side] = true; applyFold(side); }
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
function refreshOff(fromScroll) {
  if (!fromScroll) sizeWorld();          // the board moved; the world may have grown. Never on scroll.
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
cvScroll.addEventListener('scroll', () => refreshOff(true), { passive: true });

// ── the world ───────────────────────────────────────────
//: **How far the canvas scrolls is ours to say.** It runs to where the windows are, and no further.
//:
//: It used to run a whole screen past them on every side — nine squares with the windows in the
//: middle one, the user's own rule earlier the same day — so that any window could be brought to the
//: middle of the screen. The cost was the gesture everybody actually makes: dragging the bar to the
//: bottom to see the bottom window took you a screen past it, into nothing ("여백이 밑에 많고
//: 스크롤이 존재하니 터미널을 지나치게되네", 2026-09-17). A scrollbar is a promise about where the
//: content is, and a screen of empty space at the end of it is a broken one.
//:
//: **The slack is still there; you take it with your hand.** Grab the bare canvas and pan past the
//: end and the room appears as you go — exactly as much as you asked for, and kept for the rest of
//: the session, so the far window can still be brought to the middle of the screen and held there.
//: tidy gives it back, and so does a reload.
//:
//: **And it never shrinks under you.** Room you pulled out stays out until you ask for it to go.
//: That one rule also fixes the drag: the area cannot shrink mid-drag, so the browser never clamps
//: the scroll, so the window follows the hand. Freezing it for the length of a gesture — the other
//: way to fix that — would have snapped the view 888px sideways on release (measured).
const worldSeen = new Map();              // canvas id → the largest extent that canvas has had this session
let worldCanvas = null;
let cvPad = null, cvWorld = null;
let originX = 0, originY = 0;

// Built once, before anything is put on the canvas. The floor first so it stays under the windows.
cvPad = el('div', 'cv-pad');
cvWorld = el('div', 'cv-world');
cvScroll.appendChild(cvPad);
cvScroll.appendChild(cvWorld);

//: **Make the terminal measure a character again.** It caches the cell size and re-measures only
//: when a font option *changes* — the options service fires on `rawOptions[k] !== v`, so writing the
//: same value back is nothing at all. When the cell changes underneath it for any other reason — the
//: font arriving after boot gave up waiting for it, a renderer handing over — the cache is stale and
//: every `fit()` after that divides the box by a number that is no longer true. A hair up and back
//: is two real changes, so it measures twice and settles on what is actually there, and the option
//: ends where it began.
//: When a pane looks itself over after opening — see `watchFit`. Four reads, then it stops.
const FIT_CHECKS = [250, 900, 2500, 6000];
function remeasure(term) {
  try {
    const f = term.options.fontSize;
    term.options.fontSize = f + 0.01;
    term.options.fontSize = f;
  } catch (e) {}
}

//: The room the windows occupy, floored at the viewport — the same number the minimap scales to.
function contentExtent() {
  // `lx`/`ly` are at most 0 and `hx`/`hy` at least the viewport, so the span is never smaller than
  // one screen — and a window carried into the slack pulls the near edge negative rather than
  // being stopped at it.
  let lx = 0, ly = 0, hx = cvScroll.clientWidth, hy = cvScroll.clientHeight;
  for (const t of tiles.values()) {
    const r = t.visible() && layout[t.id];
    if (!r) continue;
    lx = Math.min(lx, r.x - GAP);
    ly = Math.min(ly, r.y - GAP);
    hx = Math.max(hx, r.x + r.w + GAP);
    hy = Math.max(hy, r.y + r.h + GAP);
  }
  return { lx, ly, hx, hy, w: hx - lx, h: hy - ly };
}

//: **Panning past the end makes the room.** The scroll stops where the windows do, so this is how
//: the slack is reached: ask for a board position, and if the pad cannot show it, the pad grows by
//: exactly the shortfall first. Board coordinates, not pad ones — the origin moves underneath while
//: this runs, and a board coordinate does not.
function panTo(bx, by) {
  const seen = worldSeen.get(current) || { ox: 0, oy: 0, w: 0, h: 0 };
  const ox = Math.max(seen.ox, -bx), oy = Math.max(seen.oy, -by);
  const w = Math.max(seen.w, ox + bx + cvScroll.clientWidth);
  const h = Math.max(seen.h, oy + by + cvScroll.clientHeight);
  if (ox !== seen.ox || oy !== seen.oy || w !== seen.w || h !== seen.h) {
    worldSeen.set(current, { ox: ox, oy: oy, w: w, h: h });
    sizeWorld();                 // lays the pad out and carries the scroll along with the origin
    renderMinimap();
  }
  cvScroll.scrollLeft = originX + bx;
  cvScroll.scrollTop = originY + by;
}

//: **Seeing the whole canvas at once, and nothing else.** Asked for 2026-09-19 and decided as
//: view-only, which is the version that does not touch xterm's cell arithmetic — the windows are
//: drawn smaller, not resized, so no terminal is told anything and nothing has to be measured again.
//:
//: **And it does not scroll.** That is what keeps it small: every place that turns a screen point
//: into a board point would otherwise need the scale folded into it — the drop point, the minimap,
//: the pan, where a new window goes — and five conversions is where this kind of feature goes wrong.
//: Fitted, the whole board is on screen, so there is nothing to scroll and one conversion is left:
//: the click that takes you back, which lands you at 1:1 with what you clicked in the middle.
let fitting = false;
function fitScale() {
  const c = contentExtent();
  const k = Math.min(1, (cvScroll.clientWidth - 8) / Math.max(1, c.w),
                        (cvScroll.clientHeight - 8) / Math.max(1, c.h));
  return { k: k, c: c };
}
function setFit(on, at) {
  const was = fitting;
  fitting = !!on;
  cv.classList.toggle('fit', fitting);
  const b = document.getElementById('fit');
  if (b) b.setAttribute('aria-pressed', String(fitting));
  if (was && !fitting && at) {
    // Back to 1:1 with what was clicked in the middle of the screen. The only screen-to-board
    // conversion this feature has, and it happens once.
    const box = cvScroll.getBoundingClientRect(), f = at.k, c = at.c;
    const bx = (at.x - box.left) / f + c.lx, by = (at.y - box.top) / f + c.ly;
    sizeWorld();
    panTo(Math.round(bx - cvScroll.clientWidth / 2), Math.round(by - cvScroll.clientHeight / 2));
    return;
  }
  sizeWorld();
  renderMinimap();
  refreshOff();
}

function sizeWorld() {
  if (!cvPad) return;
  if (fitting) {
    // The pad is the screen — there is nowhere to scroll — and the world is moved and scaled so the
    // near edge of the windows' own room lands in the corner.
    const { k, c } = fitScale();
    cvPad.style.width = cvScroll.clientWidth + 'px';
    cvPad.style.height = cvScroll.clientHeight + 'px';
    cvWorld.style.left = '0px';
    cvWorld.style.top = '0px';
    cvWorld.style.transform = 'translate(' + (-c.lx * k) + 'px,' + (-c.ly * k) + 'px) scale(' + k + ')';
    cvScroll.scrollTo(0, 0);
    return;
  }
  cvWorld.style.transform = '';
  const c = contentExtent();
  const seen = worldSeen.get(current) || { ox: 0, oy: 0, w: 0, h: 0 };
  // Where board zero sits inside the pad: however far the windows have gone the other side of it,
  // plus whatever room the hand has pulled out. `c.lx` is negative or zero, so this only ever adds.
  const ox = Math.max(seen.ox, -c.lx), oy = Math.max(seen.oy, -c.ly);
  // And it ends at the far edge of them. `c.hx`/`c.hy` are at least one viewport, so the pad is
  // never smaller than the screen it is shown in.
  const w = Math.max(seen.w, ox + c.hx), h = Math.max(seen.h, oy + c.hy);
  worldSeen.set(current, { ox, oy, w, h });
  const dx = ox - originX, dy = oy - originY;
  const same = worldCanvas === current;
  worldCanvas = current;
  originX = ox; originY = oy;
  cvWorld.style.left = ox + 'px';
  cvWorld.style.top = oy + 'px';
  cvPad.style.width = w + 'px';
  cvPad.style.height = h + 'px';
  // **Moving the origin must not slide the canvas under the hand.** Everything inside .cv-world
  // shifts by the same amount, so the scroll goes with it and the screen does not change.
  if (same) {
    if (dx) cvScroll.scrollLeft += dx;
    if (dy) cvScroll.scrollTop += dy;
  } else {
    // A different canvas: start where its windows start rather than wherever the last one was left.
    cvScroll.scrollLeft = ox + c.lx;
    cvScroll.scrollTop = oy + c.ly;
  }
}

// ── grab and drag the canvas ──────────────────────────────
// Press on empty space and drag and the view follows the hand (like a map). The scrollbars and the wheel stay;
// this rides on top of them — the canvas grows without end (⑩), so one way to travel far is too few.
// It starts **only on empty space**: a press on a tile belongs to the tile (title-bar drag · text selection · terminal input).
const PAN_SLOP = 3;      // below this much movement it is not a drag — that is what keeps a plain press alive
cvScroll.addEventListener('pointerdown', (ev) => {
  if (ev.button !== 0 || ev.target !== cvScroll) return;   // on the bare floor only
  const x0 = ev.clientX, y0 = ev.clientY;
  const b0x = cvScroll.scrollLeft - originX, b0y = cvScroll.scrollTop - originY;
  let on = false;
  const move = (e2) => {
    const dx = e2.clientX - x0, dy = e2.clientY - y0;
    if (!on) {
      if (Math.abs(dx) < PAN_SLOP && Math.abs(dy) < PAN_SLOP) return;
      on = true;
      cvScroll.classList.add('panning');
      try { cvScroll.setPointerCapture(ev.pointerId); } catch (e) {}
    }
    // Scroll **the other way** so the grabbed point follows the hand. The browser used to stop it at
    // both ends; now the end moves instead, which is the only way left to reach the slack.
    panTo(b0x - dx, b0y - dy);
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
// ASCII '+', not the fullwidth '＋' it used to be. That character lives in the CJK
// Halfwidth/Fullwidth Forms block, so a machine with no CJK font draws it as an empty box —
// which is what the add-canvas button looked like under WSLg (user report 2026-09-11). The same
// reason the close button is drawn in CSS rather than set as ✕ (AGENTS.md): chrome should not
// depend on a glyph that the machine may not have.
const addTabEl = el('span', 'ib plus', '+');
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
  // A viewer is a window too. Counting sessions alone offered the remove handle on a canvas that had
  // viewers on it, and removing it stranded them where nothing could reach them (review, 2026-09-15).
  for (const t of tiles.values()) if (t.s.kind === 'file' && t.s.canvas === id) return false;
  return true;
}
function canvasRemovable(id) { return canvasEmpty(id) && canvasOrder.length > 1; }

async function removeCanvas(id) {
  try {
    await api('DELETE', '/api/canvases/' + encodeURIComponent(id));
  } catch (e) {
    // Another browser may have opened a terminal meanwhile — then the daemon is right and we are late.
    if (e.status === 409) { toast([e.message + ' — close its terminals first, then try again']); return; }
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
  // **The frames belong to the canvas that is showing.** They are drawn into the scroller, not into a
  // canvas, and .tile.other only hides the windows — so without this the shape of one canvas's group
  // stays on screen over the next one's windows, which is how the residue was reported as following
  // the user from canvas to canvas (2026-09-14). paintGroups skips a tile that is not visible, so one
  // call both takes the old canvas's frames away and brings this one's back.
  paintGroups();
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
    let dragging = false, aborted = false, ref = null;
    // **The strip moves like a browser's.** The carried tab follows the finger; the others slide out of
    // its way as the destination changes; the DOM is reordered once, on release, and the carried tab
    // settles into its slot. It used to reorder the DOM on every move, so tabs jumped a slot at a time
    // as the finger crossed a midpoint (user, 2026-09-15: "크롬에서 탭 순서 바꿀 때처럼").
    const others = () => [...tabsEl.querySelectorAll('.tab')].filter((o) => o !== tabEl);
    const slide = () => {
      const all = [...tabsEl.querySelectorAll('.tab')];
      const di = all.indexOf(tabEl);
      const ti = ref === addTabEl ? all.length : all.indexOf(ref);
      const W = tabEl.getBoundingClientRect().width + 4;          // its width plus the strip's gap
      all.forEach((o, i) => {
        if (o === tabEl) return;
        let dx = 0;
        if (di < ti && i > di && i < ti) dx = -W;                   // carried rightwards: these move left
        else if (di > ti && i >= ti && i < di) dx = W;              // carried leftwards: these move right
        o.style.transform = dx ? 'translateX(' + dx + 'px)' : '';
      });
    };
    const move = (e2) => {
      // If the dragged element's canvas was removed meanwhile (another browser's DELETE), this element has already
      // fallen out of the strip. Not giving up here puts the detached element back and leaves a ghost in the strip.
      if (tabEl.parentNode !== tabsEl) { aborted = true; up(); return; }
      if (!dragging) {
        if (Math.abs(e2.clientX - startX) < 4) return;   // tells a press from a drag
        dragging = true;
        tabEl.classList.add('drag');
        tabsEl.classList.add('reordering');
      }
      tabEl.style.transform = 'translateX(' + (e2.clientX - startX) + 'px)';
      // Work out the destination in one step: before the **first** neighbour whose midpoint is right of the finger.
      // If there is none, the very end (before ＋). Swapping with one neighbour at a time only moves one slot when
      // a single move crosses several (measured). Midpoints are read from where the tabs *stand*, not where
      // they have slid to — each neighbour keeps its slot in the DOM until release.
      let next = addTabEl;
      for (const o of others()) {
        const r = o.getBoundingClientRect();
        const mid = r.left + r.width / 2 - (parseFloat(o.style.transform.replace(/[^-\d.]/g, '')) || 0);
        if (e2.clientX < mid) { next = o; break; }
      }
      if (next !== ref) { ref = next; slide(); }
    };
    const up = async () => {
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
      removeEventListener('pointercancel', up);
      // Land: reorder the DOM once, then let every transform go — the neighbours are already where their
      // new slots are, so nothing jumps; the carried tab glides the last few pixels into its own.
      if (dragging && !aborted && ref && ref !== tabEl.nextSibling && tabEl.parentNode === tabsEl) tabsEl.insertBefore(tabEl, ref);
      for (const o of others()) o.style.transform = '';
      tabEl.classList.remove('drag');
      tabEl.style.transform = '';
      tabsEl.classList.remove('reordering');
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
let mmLx = 0, mmLy = 0;             // the near edge of what the windows reach — it can be negative
let cvW = 0, cvH = 0;               // canvas viewport size — held so it is not re-measured during a scroll

function mmSet(id, x, y, w, h) {    // place a rectangle using only values we already know
  const e = mmRects.get(id);
  if (!e) return;
  e.style.left = (mmOx + (x - mmLx) * mmK) + 'px';
  e.style.top = (mmOy + (y - mmLy) * mmK) + 'px';
  e.style.width = Math.max(2, w * mmK) + 'px';
  e.style.height = Math.max(2, h * mmK) + 'px';
}
// The minimap can be put away (user, 2026-09-15). Off is the only state written, like push-aside.
const LS_MM = 'palmar.minimap';
let minimapOn = true;
try { minimapOn = localStorage.getItem(LS_MM) !== '0'; } catch (e) {}
function setMinimap(on) {
  minimapOn = !!on;
  try { if (minimapOn) localStorage.removeItem(LS_MM); else localStorage.setItem(LS_MM, '0'); } catch (e) {}
  renderMinimap();
}
function renderMinimap() {
  if (!minimapOn) { mmEl.hidden = true; return; }
  const list = [];
  for (const t of tiles.values()) if (t.visible() && layout[t.id]) list.push(t);
  mmRects.clear();
  mmWorldEl.textContent = '';
  if (!list.length) { mmEl.hidden = true; return; }
  mmEl.hidden = false;
  cvW = cvScroll.clientWidth; cvH = cvScroll.clientHeight;
  const bw = mmEl.clientWidth - MM_PAD * 2, bh = mmEl.clientHeight - MM_PAD * 2;
  // **What the windows reach, near edge included** — the same span sizeWorld uses, so the two cannot
  // disagree. It never gets smaller than the viewport (⑩: no limit the other way).
  const ext = contentExtent();
  const worldW = ext.w, worldH = ext.h;
  mmLx = ext.lx; mmLy = ext.ly;
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
  // **The minimap keeps drawing the windows' own room, not the slack around it** (user, 2026-09-17) —
  // otherwise exploring empty space would shrink the scale and push the windows into a corner. The
  // price is that panning into the slack takes this rectangle off the edge, which is the truth.
  mmVpEl.style.transform =
    'translate(' + (mmOx + (cvScroll.scrollLeft - originX - mmLx) * mmK) + 'px, ' +
                   (mmOy + (cvScroll.scrollTop - originY - mmLy) * mmK) + 'px)';
}
cvScroll.addEventListener('scroll', mmMove, { passive: true });

// Press or drag to move the viewport. The minimap box's rectangle is measured once on press (never re-read during the drag).
(() => {
  let box = null;
  const seek = (ev) => {
    if (!box || !mmK) return;
    // The minimap draws the windows' own room, so what comes out of it is a board coordinate.
    cvScroll.scrollLeft = Math.max(0, originX + mmLx + (ev.clientX - box.left - mmOx) / mmK - cvW / 2);
    cvScroll.scrollTop = Math.max(0, originY + mmLy + (ev.clientY - box.top - mmOy) / mmK - cvH / 2);
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
  if (s.name && s.fg) parts.push(s.fg);                       // a named pane: what runs in it, on the second line
  if (s.answered_elsewhere) parts.push('answered — not here');
  else if (s.last_event) parts.push(EVENT_PHRASE[s.last_event] || s.last_event);   // #24: event name → human words
  // Only palmar can say this — it holds the PTY. A pane that reads "working" but has printed nothing for a
  // while (STUCK_S). It does not accuse: it may be thinking hard. This was the Attention list's one unique
  // signal; when that list was removed it moved here, onto the row that already shows the pane.
  if (s.status === 'working') {
    const q = quietFor(s);
    if (q !== null && q >= STUCK_S) parts.push('quiet ' + agoShort(lastOutAt.get(s.id)));
  }
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
  // Unnamed: what is in front — claude, vim, whatever runs — else the agent word, else "shell".
  else { who.textContent = (s.fg || s.agent || 'shell') + ' '; who.appendChild(el('span', null, shortPath(s.cwd))); }
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
  cl.setAttribute('aria-label', 'close terminal — ' + (labelOf(s)));
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
    listEl.appendChild(el('div', 'empty', 'No terminals yet. Press \uff0b or ' + KMOD + '\u23ce to open one at home.'));
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
setInterval(() => {
  for (const [id, it] of items) {
    it._ago.nodeValue = agoText(id);
    const s = sessions.get(id);
    if (s && it._msg) it._msg.textContent = msgText(s);   // makes "quiet 6m" run without rebuilding the list
  }
  // **And the net under `watchFit`.** That one runs for six seconds after a pane opens, which is
  // where the cell has been seen to change under a fit — but a pane lives much longer than that, and
  // a terminal standing taller than its box has its prompt somewhere you cannot scroll to. Two
  // numbers per visible pane on a clock that was already running, and only a pane that is actually
  // wrong costs anything after that.
  for (const t of tiles.values()) {
    if (t.closed || !t.visible() || !t.overflows || !t.overflows()) continue;
    remeasure(t.term);
    t.fitted = false;
    t.refit();
  }
}, 10000);

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
// Esc in the field: a search input clears itself on Esc; with nothing left to clear, the box goes away.
searchEl.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !searchEl.value) { e.preventDefault(); showSearch(false); } });
const searchX = $('#search-x');
if (searchX) searchX.addEventListener('click', () => showSearch(false));   // the plain way out (user, 2026-09-15)
addEventListener('keydown', (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); showSearch(true); searchEl.select(); }
  // **⌘T·⌘N cannot be used** — the browser takes them and preventDefault does not hold (new tab, new window).
  // Enter is free, and Shift alone separates "one more" (a terminal) from "something bigger" (a canvas).
  if (mod && e.key === 'Enter' && !e.altKey) {
    e.preventDefault();
    if (e.shiftKey) newCanvas(); else newTerminal();
  }
  // Fold a rail to widen the canvas. \\ (backslash) toggles the left, Shift+\\ the right —
  // a key the browser does not already claim, unlike ⌘W/⌘T. Toggles: press again to bring it back.
  if (mod && (e.key === '\\' || e.code === 'Backslash') && !e.altKey) {
    e.preventDefault();
    const side = e.shiftKey ? 'r' : 'l';
    setFold(side, !railFolded[side]);
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

//: **An unnamed pane is called by what runs in it.** `claude · palmer` while claude runs, the folder
//: alone at a prompt; a name a person gave is never overwritten (⑫) — for a named pane the command
//: goes on the row's second line instead. The name comes from the daemon (session.fg, comm_of), so it
//: is whatever is in front, not a list (2026-09-15).
function labelOf(s) {
  if (!s) return '?';
  if (s.name) return s.name;
  return s.fg ? s.fg + ' · ' + shortPath(s.cwd) : shortPath(s.cwd);
}

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
// **When this window has no Notification API** (palmar's own window on a Mac is WKWebView, which has
// none), the daemon says it for us: POST /api/notify hands the title and body to the OS. hello says
// whether the daemon can, on this machine.
let daemonNotify = false;
const hasNotificationAPI = () => 'Notification' in window;
const canRing = () => hasNotificationAPI() ? Notification.permission === 'granted' : daemonNotify;
// `palmar` typed while palmar is open asks the daemon, and the daemon asks every page, to come to the
// front. palmar's own window does it over IPC (tao's set_focus); a browser gets window.focus(), which
// it may or may not honour — the daemon also asks the OS where it can (focus_existing).
function comeForward() {
  try {
    if (window.PALMAR_NATIVE && window.ipc && typeof window.ipc.postMessage === 'function') window.ipc.postMessage('focus');
    else window.focus();
  } catch (e) {}
}
function notifyViaDaemon(title, body) {
  return api('POST', '/api/notify', { title, body });
}

// Called **only at the moment the status changes into one**. Ring again while a status persists and people
// turn notifications off entirely.
function onWantsYou(id) {
  if (!notifyOn || !canRing()) return;
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
  const title = one ? labelOf(one) + ' wants you' : ids.length + ' terminals want you';
  const body = one ? one.cwd : ids.map((i) => labelOf(sessions.get(i))).join(', ');
  if (!hasNotificationAPI()) {              // the daemon says it; nothing to click, so nothing to wire
    notifyViaDaemon(title, body).catch(() => {});
    notifyAt = Date.now();
    return;
  }
  let n;
  try {
    n = new Notification(title, { body, tag: 'palmar-wants-you' });   // same tag, so they replace rather than pile up
  } catch (e) { return; }
  notifyAt = Date.now();
  // A click that does not land on that terminal amounts to saying "go find it", which leaves the original problem standing.
  n.onclick = () => { window.focus(); goToSession(ids[0]); n.close(); };
}

// **Ring once when it is turned on.** A notification has to pass browser permission, the OS's do-not-disturb and
// focus assistance before it arrives, and blocked at any of those it is equally silent on screen. One send checks
// the whole chain at once — better to know now than to discover "I turned it on and nothing comes" later.
const NOTIFY_TEST_BODY = 'This is the only one you did not ask for. From now on it speaks when a terminal wants you.';
function notifyTest() {
  if (!hasNotificationAPI()) {
    notifyViaDaemon('palmar notifications are on', NOTIFY_TEST_BODY)
      .catch((e) => toast(['the daemon could not notify:', { d: String(e.message || e) }]));
    return;
  }
  try {
    const n = new Notification('palmar notifications are on', { body: NOTIFY_TEST_BODY, tag: 'palmar-test' });
    n.onclick = () => { window.focus(); n.close(); };
  } catch (e) {
    toast(['notifications were allowed, but the browser refused to show one:', { d: String(e.message || e) }]);
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
  if (!hasNotificationAPI()) {
    // palmar's own window: the daemon notifies, when it can on this machine.
    if (daemonNotify) { setNotify(true); notifyTest(); }
    else toast(['this window has no Notification API, and the daemon has no way to notify on this machine']);
    return;
  }
  let perm = Notification.permission;
  // Ask only when there is a gesture turning it on — asking for permission the moment a page loads is the thing
  // everybody hates, and the browser demands a gesture anyway. Permission is per **origin, port included**, so
  // changing --port asks again.
  if (perm === 'default') { try { perm = await Notification.requestPermission(); } catch (e) { perm = 'denied'; } }
  if (perm !== 'granted') {
    toast(['notifications are blocked for ' + location.origin + ' —',
           perm === 'denied' ? 'the browser is refusing; allow them for this site in its settings'
                             : 'allow them in the browser, then try again']);
    return;
  }
  setNotify(true);
  notifyTest();
}
if (bellEl) {
  bellEl.addEventListener('click', () => {
    bellEl.classList.remove('ping'); void bellEl.offsetWidth; bellEl.classList.add('ping');   // one ring, every press
    toggleNotify();
  });
  bellEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleNotify(); }
  });
}

// ── new versions ────────────────────────────────────────
//: **The only thing palmar would send off this machine, and it is off until somebody asks.** The
//: daemon does the asking and the daemon keeps the switch (`~/.palmar/settings.json`) — a switch in
//: `localStorage` would mean Chrome knew and Edge did not, and would say nothing at all while no page
//: was open. ③ moved the board out of `localStorage` on 2026-09-14 for exactly that reason.
//:
//: `hello` brings the state, and an `update` frame brings a change another browser made or an answer
//: that just came back. **The daemon decides what `on` means, not this checkbox** — `$PALMAR_UPDATE_CHECK`
//: can overrule the file, and a switch that shows the opposite of the truth is worse than no switch.
const updateBox = document.getElementById('updatecheck');
let updateTold = null;              // the version already said, so a second frame does not repeat it
function applyUpdate(u) {
  if (!u) return;
  if (updateBox) updateBox.checked = !!u.on;
  if (!u.latest || u.latest === updateTold) return;
  updateTold = u.latest;
  toast([{ b: 'palmar ' + u.latest + ' is out' }, '— update whenever it suits you;',
          { a: 'what changed', on: () => window.open(u.url, '_blank', 'noopener,noreferrer') }]);
}
if (updateBox) {
  updateBox.addEventListener('change', async () => {
    const want = updateBox.checked;
    try {
      applyUpdate(await api('POST', '/api/settings', { update_check: want }));
    } catch (e) {
      updateBox.checked = !want;    // it did not take — do not leave it looking as though it had
      toast(['could not change that:', { d: String(e.message || e) }]);
    }
  });
}

// ── the window's first size ─────────────────────────────
//: **Chromium's default app window is nearly square, and this page does not fit in it.** An --app or
//: installed-PWA window with no saved bounds has its width cut to 1050 DIP while it keeps almost the
//: whole work-area height (Chromium's WindowSizer). Measured on the user's Windows machine
//: 2026-09-16: a 1536x912 work area gave 1050x892 — 1.18:1, and `.win` has a min-width of 1120, so
//: the right rail was cut off and the page scrolled sideways. Dragging it wider did not stick.
//:
//: So the page gives the window palmar's own shape. **Once**, because doing it every load would undo
//: the size somebody chose. **Only in an app window** — a tab ignores resizeTo and then reports the
//: size it refused to take, so running it there would change nothing and lie about it afterwards.
//:
//: **It is the height that is wrong, not only the width** (user, 2026-09-17). The first version set
//: the width and passed `outerHeight` straight back, so a window that was already wide enough was
//: left exactly as square as Chromium had made it: 1280x1029 on a 2560x1392 work area, 1.24:1, and
//: 1302x893 on Windows. Worse, the guard read `want > outerWidth`, so on both of those it declined to
//: do anything at all. Width alone was never the shape that was wrong.
//:
//: So: **never narrower** than Chromium gave it and never wider than the work area — taking width
//: away from someone with the screen for it is not ours to do — and the height follows from that
//: width at the proportions palmar's own window opens at. "Never shorter" was the rule before and it
//: is gone on purpose: on the one load this runs, nobody has chosen a height yet, so there is no
//: choice to take away, and keeping it was the whole reason the square window survived the fix.
const LS_SIZED = 'palmar.sized';
const FIRST_W = 1280, FIRST_H = 820;   // what palmar's own window opens at (app/src/main.rs)
// The rule on its own, away from the screen and the window, because the arithmetic is the part that
// has been wrong both times and it is the part a test can hold on to.
function firstWindowSize(availW, availH, outerW) {
  const w = Math.min(availW - 40, Math.max(FIRST_W, outerW));
  return { w: w, h: Math.min(availH - 40, Math.round(w * FIRST_H / FIRST_W)) };
}
function sizeWindowOnce() {
  try {
    if (localStorage.getItem(LS_SIZED) === '1') return;
    localStorage.setItem(LS_SIZED, '1');
  } catch (e) { return; }    // cannot remember having done it, so do not do it at all
  try {
    if (!matchMedia('(display-mode: standalone)').matches) return;
    const want = firstWindowSize(screen.availWidth, screen.availHeight, outerWidth);
    if (want.w !== outerWidth || want.h !== outerHeight) resizeTo(want.w, want.h);
  } catch (e) {}
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
  if (v === undefined || v === PROTOCOL || protocolWarned) return false;
  protocolWarned = true;
  const older = v < PROTOCOL;
  toast([
    'this page speaks protocol ' + PROTOCOL + ', the daemon speaks ' + (v === null ? '?' : v) + ' —',
    older ? 'the daemon is older; restart it after pulling' : 'this page is older',
    m.daemon ? { d: '(daemon ' + m.daemon + ')' } : '',
    // Every reply carries `Cache-Control: no-store`, so an ordinary reload really does fetch the new files —
    // this used to say "with a hard refresh", which asked for a gesture that was never needed.
    older ? '' : { a: 'reload', on: () => location.reload() },
  ].filter(Boolean));
  return true;
}

//: **The version of the daemon this page attached to.** The UI is served by the daemon, so an update swaps
//: the daemon's copy of this very file while the tab you are looking at goes on running the old one — and
//: the tab has no way to notice by itself. The socket drops when the daemon restarts and the version is the
//: first thing the new one says, so this costs one comparison and reaches nothing outside the machine.
//:
//: **It is not an "is there a newer release" check.** palmar opens no outbound connection; whether it ever
//: should is a separate decision and #23 has to settle first, because there is nothing to check against yet.
//:
//: A restart means more than a stale screen. ⑦=b: the daemon comes back only for an update, and when it does
//: the shell panes are gone and the agents returned through `--resume`. So this line is also the answer to
//: "where did my shells go".
let daemonSeen = null;
function checkVersion(m) {
  const v = m && m.daemon;
  if (!v) return false;                     // a daemon older than this field — checkProtocol has that case
  if (daemonSeen === null) { daemonSeen = v; return false; }   // the attach itself is not news
  if (v === daemonSeen) return false;
  daemonSeen = v;
  toast([{ b: 'palmar ' + v }, '— the daemon restarted on a new version; this screen is still the old one',
         { a: 'reload', on: () => location.reload() }]);
  return true;
}

// ── "working, but quiet" ──────────────────────────────────
// The Attention list that used to sit here was removed (see index.html) — it repeated the left list and helped
// nobody. Its one signal nothing else had — a pane that reads **working** but has printed nothing for a while —
// moved into the left list's row (msgText). We can tell only because we hold the PTY. It does not accuse.
const STUCK_S = 300;      // 5 minutes. Shorter and ordinary thinking time reads as "quiet"

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

function agoShort(t) {
  const d = Math.max(0, Date.now() / 1000 - t);
  if (d < 45) return 'now';
  if (d < 3600) return Math.round(d / 60) + 'm';
  if (d < 86400) return Math.round(d / 3600) + 'h';
  return Math.round(d / 86400) + 'd';
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
    if (layout[s.id] && layout[s.id].g) paintGroups();   // now it can be seen — see the Tile constructor
  } else {
    const wasVisible = !t.el.classList.contains('other');
    t.update(s);
    const on = t.visible();
    if (on !== wasVisible) {          // ⑪ the session moved canvas — it arrives in a single session frame
      t.el.classList.toggle('other', !on);
      if (on) t.refit(); else { t.off = false; syncMax(); }   // the expanded window went to somebody else's canvas
      paintGroups();                                          // a group lives on one canvas — its frame moves with it
    }
  }
  renderTabs();      // the dot is computed — recount when status or canvas changes
  renderList();
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
  // **A closed window leaves its group properly.** Dropping the entry was not enough: the frame is
  // drawn from live tiles but only *redrawn* when something asks, and nothing asked here — so the
  // coloured shape stayed on screen, showed on every other canvas too (the frame lives in the scroller,
  // not in a canvas), and outlived every window on the board (user, 2026-09-14). A pair that loses one
  // member also has to stop being a group, and what is left of a bigger one closes up, because a group
  // that stays touching is the whole of what a group is.
  const wasG = layout[id] && layout[id].g;
  // **One way back, even for this.** Closing a window closes its group up, which moves windows nobody
  // asked to move — and the rule is that everything which moves a window can be undone. It is the one
  // carve-out from "my window must not move because somebody else's closed": inside the group only,
  // never a window outside it, because a group that keeps a hole has come apart.
  if (wasG) undoMark('closing a window', wasIn);
  delete layout[id];   // an id is never reused — leave it and it piles up
  if (wasG) {
    const rest = dissolveIfAlone(wasG);
    if (rest.length > 1) compactGroup(rest);
  }
  saveLayout();
  paintGroups();
  if (focused === id) focused = null;
  // If it is on, close up automatically. Otherwise **only light the button** to say there is slack to close up —
  // it moves windows, so nothing moves unless it was asked for.
  if (wasIn && autoTidy) tidyCanvas(wasIn);
  paintTidy();
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
  // **The store outlives the browser; the sessions do not.** remove() is the only thing that drops an
  // entry, and on a fresh load `sessions` is empty, so nothing ever reaches it for a pane that died
  // while the page was shut. The entries themselves are harmless — an id is never reused — but `g` is
  // not: the survivor of a pair whose partner died in the meantime came back still wearing its group,
  // marked as grouped, in a group of one, with no frame to explain it.
  let dropped = false;
  for (const id of Object.keys(layout)) if (!seen.has(id) && !id.startsWith('v:')) { delete layout[id]; dropped = true; }   // v: is a viewer, not a session
  if (dropped) {
    for (const g of new Set(Object.values(layout).map((r) => r.g).filter(Boolean))) dissolveIfAlone(g);
    saveLayout();
    paintGroups();
  }
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
      // One toast, not two: a protocol mismatch is the louder half of the same news, and the second call
      // would overwrite the first in the same strip.
      if (!checkProtocol(m)) checkVersion(m);
      daemonNotify = !!m.notify;
      applyUpdate(m.update);
      if (!hasNotificationAPI() && !daemonNotify && notifyOn) setNotify(false);   // nothing here can ring
      takeLayout(m);                       // before the sessions are placed, so they land where the daemon says
      setCanvases(m.canvases || []); reconcile(m.sessions || []);
      restoreViewers();                    // the windows that are not sessions
      renderRestore(m.restore);
    }
    else if (m.t === 'session' && m.s) upsert(m.s);
    else if (m.t === 'gone' && m.id) remove(m.id);
    else if (m.t === 'canvas' && m.c) putCanvas(m.c);           // created or renamed
    else if (m.t === 'canvases' && m.cs) setCanvases(m.cs);     // the order changed — all of them, in order
    else if (m.t === 'canvas_gone' && m.id) dropCanvas(m.id);   // canvases follows right behind
    else if (m.t === 'layout') layoutArrived(m);                 // another browser moved something
    else if (m.t === 'focus') comeForward();                     // `palmar` typed while this is open
    else if (m.t === 'update') applyUpdate(m);                   // the switch moved, or an answer came back
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
//: **Which renderer is actually live**, read off the DOM rather than off our own intent. The WebGL
//: addon puts two canvases under .xterm-screen; the DOM renderer builds .xterm-rows and no canvas.
//: `t.gl` only says the addon was constructed without throwing, and on WSLg the difference between
//: that and a working renderer is the whole question — a status line that reports what we hoped for
//: is worse than none.
function rendererOf(t) {
  const el = t && t.el;
  if (!el) return 'dom';
  return el.querySelectorAll('.xterm-screen canvas').length >= 2 ? 'webgl' : 'dom';
}
function updateStatusBar(note) {
  let gl = 0, dom = 0;
  for (const t of tiles.values()) (rendererOf(t) === 'webgl' ? gl++ : dom++);
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
  // Absolute on either platform: a leading slash, or a drive letter. Without the second, a Windows
  // root like `C:\` was joined onto its parent instead of replacing it.
  if (name.startsWith('/') || /^[A-Za-z]:[\\/]/.test(name)) return name;
  if (!parent) return name;
  // **The separator is the parent's.** Building `C:\/Users` happens to work on Windows, but it comes
  // straight back to the daemon as a cwd and then into every path shown on screen.
  const sep = /^[A-Za-z]:[\\/]/.test(parent) || parent.indexOf('\\') >= 0 ? '\\' : '/';
  return parent.replace(/[\\/]$/, '') + sep + name;
}
function makeNode(parentPath, e, depth) {
  return { path: joinDir(parentPath, e.name), name: e.name, branch: e.git_branch || null,
           hasChildren: !!e.has_children, depth, expanded: false, children: null, loading: false,
           isHome: !!e.home, kind: e.kind || 'dir', size: e.size };
}
async function loadRoots() {
  try {
    const d = await api('GET', '/api/dirs');
    tree.roots = (d.entries || []).map((e) => makeNode(d.path || '', e, 0));
    // **home is the marked root, not the first one.** `/` leads the list so the tree can climb to
    // the top (2026-09-11), and taking the first root as home turned `/` into `~`.
    const homeNode = tree.roots.find((n) => n.isHome) || tree.roots.find((n) => n.path !== '/') || tree.roots[0];
    home = homeNode ? homeNode.path : null;
    if (!selectedDir && homeNode) selectDir(homeNode);   // start on home, not at /
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
//: **Three cues, not one.** A folder carries a folder mark in the accent and its name in full ink; a
//: file carries a page mark in the faint ink, its name a step back, and its size on the right. Drawn,
//: not typed: a glyph would be a different shape on every machine (AGENTS.md).
function rowIcon(kind) {
  const i = el('span', 'ic ' + kind);
  i.innerHTML = kind === 'dir'
    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4.2l2 2.4h8.8A1.5 1.5 0 0 1 21 9.9v8.6A1.5 1.5 0 0 1 19.5 20h-15A1.5 1.5 0 0 1 3 18.5z"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h7.5L19 8.5V21H6z"/><path d="M13.5 3v5.5H19"/></svg>';
  return i;
}
function fmtSize(n) {
  if (!Number.isFinite(n)) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + ' K';
  return (n / 1048576).toFixed(1) + ' M';
}
// A found folder: open the tree down to it, one /api/dirs a step, from the root that holds it.
async function revealDir(path) {
  // **The longest root that holds it.** `/` matches everything, and taking the first match walked the
  // whole machine from the top instead of starting at home (review, 2026-09-15).
  const root = tree.roots
    .filter((r) => path === r.path || path.startsWith(r.path.replace(/\/$/, '') + '/'))
    .sort((a, b) => b.path.length - a.path.length)[0];
  if (!root) return;
  let n = root;
  while (n) {
    if (n.path === path) { selectDir(n); if (!n.expanded) await expandNode(n); return; }
    if (!n.expanded) await expandNode(n);
    n = (n.children || []).find((c) => path === c.path || path.startsWith(c.path + '/'));
  }
}
// The selected folder is where "new folder" and "refresh" act. Picking one no longer arms a launcher —
// the rail is for looking, and a folder opens on a click (2026-09-15).
function selectDir(n) {
  selectedDir = n;
  selectedPath = n.path;
  renderTree();
}
let selectedPath = null;      // what carries the mark — a folder or a file
let dragFile = null;          // the path being dragged onto the canvas, while it is being dragged
let findResults = null;      // { q, entries } — only while searching. null means the ordinary tree.

function renderTree() {
  treeEl.textContent = '';
  const hint0 = document.getElementById('tree-hint');
  if (hint0 && !findResults) hint0.textContent = 'click a file to view it · read when expanded';
  if (findResults) {
    // **Show the matches flat.** Expanding down into the tree loses track of where you are looking.
    const hint = document.getElementById('tree-hint');
    if (hint) hint.textContent = 'matching folders · click one to look inside';
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
      // Pick a match and the tree opens down to it — the rail is for looking inside (2026-09-15).
      const pick = () => {
        // Put the search away for good: an answer still in flight would otherwise land after it and
        // put the results back (review, 2026-09-15).
        clearTimeout(findTimer); findSeq++;
        findResults = null; searchEl.value = ''; showSearch(false);
        revealDir(n.path);
      };
      r.addEventListener('click', pick);
      r.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); pick(); } });
      treeEl.appendChild(r);
    }
    return;
  }
  const walk = (n, i) => {
    const r = el('div', 'row' + (n.depth === 0 ? ' root' : '') + (n === selectedDir || n.path === selectedPath ? ' sel' : '') +
                        (n.depth === 0 && n.path !== home ? ' dim' : '') + (n.loading ? ' loading' : ''));
    r.dataset.path = n.path;
    r.tabIndex = 0;
    r.style.paddingLeft = (8 + n.depth * 16) + 'px';
    if (n.depth === 0 && i > 0) r.style.marginTop = '8px';
    if (n.kind === 'file') {
      // **A click marks it; opening is a double-click, or a drag onto the canvas** (user, 2026-09-15:
      // "클릭했을 때 표시만 해주고 … 더블 클릭했을 때 열리거나, 파일을 캔버스로 잡아 끌었을 때").
      // A single click that opened a window made every glance at a folder cost a window.
      r.classList.add('file');
      r.setAttribute('role', 'treeitem');
      r.setAttribute('aria-label', n.name + ', file');
      r.draggable = true;
      r.appendChild(el('span', 'car none', ''));
      r.appendChild(rowIcon('file'));
      r.appendChild(el('span', 'nm', n.name));
      r.appendChild(el('span', 'fsz', fmtSize(n.size)));
      r.title = n.path + ' — double-click to open, or drag it onto the canvas';
      r.addEventListener('click', () => { selectedPath = n.path; selectedDir = null; renderTree(); });
      r.addEventListener('dblclick', (ev) => { ev.preventDefault(); openViewer(n.path); });
      r.addEventListener('dragstart', (ev) => {
        dragFile = n.path;
        r.classList.add('dragging');
        try { ev.dataTransfer.setData('text/plain', n.path); ev.dataTransfer.effectAllowed = 'copy'; } catch (e) {}
      });
      r.addEventListener('dragend', () => { dragFile = null; r.classList.remove('dragging'); cv.classList.remove('dropping'); });
      r.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); openViewer(n.path); } });
      treeEl.appendChild(r);
      return;
    }
    r.setAttribute('role', 'treeitem');
    r.setAttribute('aria-expanded', n.expanded ? 'true' : 'false');
    // has_children counts folders only; a folder of nothing but files must still open — so a click always
    // asks (expandNode goes to the daemon), and only the caret is greyed when no folder is known below.
    // **Known to hold something** — either the listing said so, or opening it proved it. A folder whose
    // children arrived but whose `has_children` was stale used to keep a hidden caret (2026-09-15).
    const holds = n.hasChildren || (n.children && n.children.length > 0);
    const car = el('span', 'car' + (holds ? '' : ' none'), n.expanded ? '▾' : '▸');
    car.addEventListener('click', (ev) => { ev.stopPropagation(); if (n.expanded) collapseNode(n); else expandNode(n); });
    r.appendChild(car);
    if (n.depth > 0) r.appendChild(rowIcon('dir'));
    r.appendChild(el('span', 'nm', n.depth === 0 ? (n.path === home ? '~' : n.path) : n.name));
    if (n.branch) r.appendChild(el('span', 'br', n.branch));
    // One click: select it and open it — a folder is for looking inside, not for arming a button (2026-09-15).
    r.addEventListener('click', () => { selectDir(n); if (n.expanded) collapseNode(n); else expandNode(n); });
    r.addEventListener('keydown', (ev) => {
      if (ev.key === 'ArrowRight' && !n.expanded) { ev.preventDefault(); expandNode(n); }
      else if (ev.key === 'ArrowLeft' && n.expanded) { ev.preventDefault(); collapseNode(n); }
      else if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); selectDir(n); if (n.expanded) collapseNode(n); else expandNode(n); }
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

// **Always at home.** It used to open in the folder of the terminal you were on, or the folder picked in
// the rail; the rail is a viewer now and the person asked for one rule (2026-09-15: "터미널은 항상 기본
// 폴더에서"). Without a cwd the daemon opens at home (protocol.md).
async function newTerminal() {
  const body = {};
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

addEventListener('keydown', (e) => {
  // **A bare Enter only.** An Enter with a modifier belongs to the global shortcuts (Ctrl/⌘+Enter = new terminal).
  // Take both and, once a folder has been clicked, that one press opens two terminals.
});

// A handle for poking around from the console and dev tools. No product behaviour leans on it.
window.palmar = { sessions, tiles, canvases, layout: () => layout,
                  canvas: () => current, groups: () => groupsCollapsed,
                  cvGroups: () => cvCollapsed, closing: () => [...closing],
                  // On screen the handle appears only when removal is possible, so the path that gets refused
                  // (#18's 409) can only be exercised from the console. The daemon blocks it anyway, so having it here adds no risk.
                  // switchCanvas because the frames are drawn into the scroller rather than into a
                  // canvas, so what happens to them on a switch is a thing a test has to be able to ask.
                  // upsert is how a session frame becomes a window — it is what a reload runs, so it is
                  // also the only way a test can ask "rebuild this pane exactly as a reload would".
                  upsert,
                  removeCanvas, watchInput, newTerminal, newCanvas, switchCanvas, openViewer, viewerId, toast,
                  // Dropping a file in from Finder or Explorer: the path parsing is the part with edges.
                  fileUrlToPath, droppedPaths,
                  // Auto-tidy only runs on a pane disappearing, and that moment is hard to create from outside.
                  // Expose **the same function** the button calls, unchanged.
                  // paintTidy with it: the button's enabled state is what a person actually sees,
                  // and a test that writes the board directly has to be able to bring it up to date.
                  tidyCanvas, paintTidy, gatherCanvas, gatherPlan, setFit, fitScale,
                  fitting: () => fitting,
                  // Push-aside. A test drives the real drag with mouse events; these are here so the geometry
                  // can also be asked directly — the cascade and the round limit need more windows than a
                  // hand can comfortably drag into place one at a time.
                  pushAside, applyPush, hits, firstFree, firstWindowSize, focusTile, panTo,
                  // The world: how far the canvas scrolls and where the board's origin sits. A test
                  // has to be able to ask both, because the bug they fix was arithmetic nobody could see.
                  sizeWorld, contentExtent, origin: () => ({ x: originX, y: originY }),
                  // Groups: the model is testable without a hand, the gesture needs one.
                  groupOf, groupRect, joinGroups, leaveGroup, groupName, setGroupName, paneOver, joinPreview, joinBlock, setGauge, compactGroup, dropInto, settle, paintGroups,
                  // Undo: one way back for everything that moves a window.
                  undoMark, undoLast, undoDepth: () => undoStack.length,
                  // Path joining is platform-shaped and the platform it gets wrong has no Chrome
                  // here — so it is tested directly rather than by driving the rail.
                  joinDir,
                  // Update notice. daemonSeen being set proves the hello handler feeds it; checkVersion is
                  // here because the alternative is restarting a daemon on a different version mid-test.
                  checkVersion, daemonSeen: () => daemonSeen,
                  // The switch's state, read-only — a test flips the real checkbox and checks this followed.
                  pushOn: () => pushOn,
                  // The layout store: saveLayout pushes it to the daemon, client is who this page is
                  // to the daemon — a test with two browsers needs to tell the two apart.
                  saveLayout, client: () => CLIENT, layoutOnDaemon: () => layoutOnDaemon,
                  holdStyle: () => holdStyle,
                  palette: () => root.dataset.pal || null, choosePalette, applyTheme, labelOf,
                  baseFont: () => FONT_PX, setBaseFont, termTheme, minimapOn: () => minimapOn,
                  // renderList forces a synchronous rebuild — the test uses it to check the "quiet while
                  // working" note without waiting on the 10s refresh. lastOutAt feeds quietFor.
                  lastOutAt, renderList,
                  // No longer a button; this is how it is reached now.
                  recordTyping };

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
      on(ta, type, (e) => { comps.push(type); put(type.padEnd(9), 'data=' + JSON.stringify(e.data)); });
    on(ta, 'input', (e) => {
      inputs.push({ type: e.inputType, data: e.data, value: e.target.value });
      put('input    ', 'data=' + JSON.stringify(e.data),
          'type=' + e.inputType, 'value=' + JSON.stringify(e.target.value));
    });
  }
  // Kept as data as well as text: the verdict below is computed from these, so a report can be
  // **read out loud** instead of pasted. Someone on a locked-down machine cannot copy anything off
  // it, and a one-line answer crosses that gap where a 200-line log does not.
  const sent = [];            // {via: 'xterm'|'ime', text}
  const inputs = [];          // {type, data, value}
  const comps = [];           // composition event names, in order
  const d = t.term.onData((x) => { sent.push({ via: 'xterm', text: x }); put('→ 데몬   ', JSON.stringify(x)); });
  // **There are two ways to the socket and this has to see both.** xterm's onData is one; the IME
  // path for browsers that emit no composition events writes straight through sendText and never
  // touches onData. A report that showed only the first would say nothing was sent while characters
  // were appearing on screen — which is exactly the shape of the bug this gets used for.
  const sendWas = t.sendText;
  t.sendText = (x) => { sent.push({ via: 'ime', text: x }); put('→ 데몬(ime)', JSON.stringify(x)); return sendWas(x); };
  off.push(() => { t.sendText = sendWas; });
  box.hidden = false;
  out.value = '';
  hint.textContent = 'recording — click the terminal and type 안녕하십니까 …';
  const tick = setInterval(() => { out.value = L.join('\n'); out.scrollTop = out.scrollHeight; }, 400);
  setTimeout(() => {
    clearInterval(tick); off.forEach((f) => f()); d.dispose();
    const v = verdictOf({ sent: sent, inputs: inputs, comps: comps });
    out.value = v + '\n' + '─'.repeat(60) + '\n' + L.join('\n');
    hint.textContent = 'done — read the VERDICT line back, or press Copy for the whole thing.';
    // Once diagnostics start, a person has to click the terminal right away. Move focus onto the pane for them.
  }, (secs || 15) * 1000);
  setTimeout(() => { if (t.term) t.term.focus(); }, 60);
}

//: **One line somebody can read aloud.** A long log is the right thing when it can be pasted; on a
//: machine where nothing can leave, what is needed is a verdict, and the report can work that out
//: itself. It names which of the two senders fired and whether what it sent was growing — which is
//: exactly the fork that decides where an input bug gets fixed.
function verdictOf(r) {
  const bits = [];
  const xterm = r.sent.filter((x) => x.via === 'xterm');
  const ime = r.sent.filter((x) => x.via === 'ime');
  if (!r.sent.length && !r.inputs.length) return 'VERDICT: nothing was typed — try again and type into the terminal';
  bits.push('sent ' + r.sent.length + ' (xterm ' + xterm.length + ' · ime ' + ime.length + ')');

  // Growing means each send starts with the one before it: "하" then "하이" then "하이하". That is the
  // signature of a whole buffer going out again rather than the new piece of it.
  const growth = (list) => {
    let n = 0;
    for (let i = 1; i < list.length; i++) {
      const a = list[i - 1].text, b = list[i].text;
      if (b.length > a.length && b.indexOf(a) === 0) n++;
    }
    return n;
  };
  const gx = growth(xterm), gi = growth(ime);
  if (gx || gi) bits.push('GROWING: ' + (gx >= gi ? 'xterm ×' + gx : 'ime ×' + gi));
  else bits.push('no growth');

  bits.push('composition ' + (r.comps.length ? r.comps.join('/') : 'none'));

  // A textarea that never gets emptied is the other classic shape of this bug.
  const vals = r.inputs.map((i) => (i.value || '').length);
  if (vals.length > 1 && vals[vals.length - 1] > vals[0] && vals[vals.length - 1] > 4) {
    bits.push('TEXTAREA GREW to ' + vals[vals.length - 1]);
  }
  const types = [...new Set(r.inputs.map((i) => i.type))];
  if (types.length) bits.push('inputType ' + types.join('+'));
  const longest = r.sent.reduce((m, x) => Math.max(m, x.text.length), 0);
  bits.push('longest send ' + longest);
  return 'VERDICT: ' + bits.join(' · ');
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
  sizeWindowOnce();
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
  // ── options list, and the shortcuts panel behind one of its items ──
  const optBtn = document.getElementById('options'), optMenu = document.getElementById('options-menu');
  const keysEl = document.getElementById('keys');
  const showKeys = (on) => { if (on) closePopups(keysEl); keysEl.hidden = !on; };
  registerPopup(keysEl, () => showKeys(false));
  const showOptions = (on) => {
    if (!optMenu) return;
    if (on) closePopups(optMenu);
    optMenu.hidden = !on;
    optBtn.setAttribute('aria-expanded', on ? 'true' : 'false');
    if (on) {
      const r = optBtn.getBoundingClientRect();
      optMenu.style.top = (r.bottom + 6) + 'px';
      optMenu.style.right = Math.max(8, window.innerWidth - r.right) + 'px';
    }
  };
  if (optBtn && optMenu) {
    registerPopup(optMenu, () => showOptions(false));
    optBtn.addEventListener('click', (e) => { e.stopPropagation(); showOptions(optMenu.hidden); });
    optMenu.addEventListener('click', (e) => {
      e.stopPropagation();                                 // a checkbox or select inside stays open
      const b = e.target.closest('.tm-i');
      if (!b) return;
      showOptions(false);
      if (b.dataset.do === 'keys') showKeys(true);
      if (b.dataset.do === 'search') showSearch(searchBox.hidden);   // the item is a switch: again puts it away
    });
    addEventListener('click', () => { if (!optMenu.hidden) showOptions(false); });
    addEventListener('keydown', (e) => { if (e.key === 'Escape' && !optMenu.hidden) showOptions(false); });
  }
  if (keysEl) {
    document.getElementById('keys-x').addEventListener('click', () => showKeys(false));
    // A press outside the panel or Esc closes it. Clicks inside the panel are swallowed.
    keysEl.addEventListener('click', (e) => e.stopPropagation());
    addEventListener('click', () => { if (!keysEl.hidden) showKeys(false); });
    addEventListener('keydown', (e) => { if (e.key === 'Escape' && !keysEl.hidden) showKeys(false); });
    // The typing report is no longer offered on screen — it is a tool for chasing an input bug, not
    // a feature (asked for 2026-09-11). `palmar.recordTyping(15)` still brings it up.
    // ── across to a browser ──────────────────────────────────────────
    // **The address is fetched when the panel opens and not kept.** The page is otherwise never
    // given the key (#14), and there is no reason for it to hold one longer than the moment it is
    // being shown. Opening is done by the daemon, so the key never has to cross over at all — which
    // also means it works on WSL, where the browser worth opening is on the Windows side.
    const webBox = document.getElementById('webbox');
    const webUrl = document.getElementById('web-url');
    const showWeb = (on) => {
      if (!webBox) return;
      if (on) closePopups(webBox);
      webBox.hidden = !on;
      const b = document.getElementById('toweb');
      if (b) b.setAttribute('aria-expanded', on ? 'true' : 'false');
      if (!on && webUrl) webUrl.value = '';      // do not leave it lying about
    };
    const towebEl = document.getElementById('toweb');
    // A press outside the box or Esc closes it, like every other popup here — it used to stay up
    // until its × was found (user, 2026-09-15, on Windows). The button's own click must not count
    // as "outside", and clicks inside the box are swallowed.
    if (webBox) {
      registerPopup(webBox, () => showWeb(false));
      webBox.addEventListener('click', (e) => e.stopPropagation());
      addEventListener('click', () => { if (!webBox.hidden) showWeb(false); });
      addEventListener('keydown', (e) => { if (e.key === 'Escape' && !webBox.hidden) showWeb(false); });
    }
    if (towebEl) towebEl.addEventListener('click', async (e) => {
      e.stopPropagation();
      showKeys(false);
      showWeb(true);
      if (webUrl) webUrl.value = 'asking the daemon…';
      try {
        const r = await api('GET', '/api/address');
        if (webUrl) {
          webUrl.value = r.url;
          webUrl.focus(); webUrl.select();
          webUrl.scrollLeft = 0;      // select() leaves it scrolled to the end, hiding the host
        }
      } catch (e) {
        if (webUrl) webUrl.value = '';
        toast(['could not get the address — ', { d: String(e.message || e) }]);
      }
    });
    const webX = document.getElementById('web-x');
    if (webX) webX.addEventListener('click', () => showWeb(false));
    const webOpen = document.getElementById('web-open');
    if (webOpen) webOpen.addEventListener('click', async () => {
      webOpen.disabled = true;
      try {
        await api('POST', '/api/address/open');
        toast(['opening a browser…']);
        showWeb(false);
      } catch (e) {
        // On a machine with no browser to open this is the ordinary answer, not a fault — the
        // address is right there to copy instead.
        toast(['could not open a browser — copy the address instead']);
      }
      webOpen.disabled = false;
    });
    const webCopy = document.getElementById('web-copy');
    if (webCopy) webCopy.addEventListener('click', async () => {
      if (!webUrl || !webUrl.value) return;
      try {
        await navigator.clipboard.writeText(webUrl.value);
        toast(['address copied']);
      } catch (e) {
        // Clipboard permission is refused often enough that the selection is the real fallback.
        webUrl.focus(); webUrl.select();
        toast(['could not copy — it is selected, press ' + (IS_MAC ? '⌘C' : 'Ctrl+C')]);
      }
    });
    addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && webBox && !webBox.hidden) showWeb(false);
    });

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
    const uf = document.getElementById('uifont');
    if (uf) {
      let face = 'system';
      try { if (localStorage.getItem('palmar.uifont') === 'mono') face = 'mono'; } catch (e) {}
      document.body.classList.toggle('ui-mono', face === 'mono');
      uf.value = face;
      uf.addEventListener('change', () => {
        document.body.classList.toggle('ui-mono', uf.value === 'mono');
        try { if (uf.value === 'mono') localStorage.setItem('palmar.uifont', 'mono'); else localStorage.removeItem('palmar.uifont'); } catch (e) {}
      });
    }
    const mmsw = document.getElementById('minimap');
    if (mmsw) {
      mmsw.checked = minimapOn;
      mmsw.addEventListener('change', () => setMinimap(mmsw.checked));
    }
    const nt = document.getElementById('newterm');
    if (nt) {
      nt.addEventListener('click', () => newTerminal());
      nt.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); newTerminal(); } });
    }
    const fsel = document.getElementById('fontsize');
    if (fsel) {
      fsel.value = String(Math.round(FONT_PX));
      if (fsel.selectedIndex < 0) fsel.value = '13';
      fsel.addEventListener('change', () => setBaseFont(parseFloat(fsel.value)));
    }
    const hsel = document.getElementById('holdstyle');
    if (hsel) {
      hsel.value = holdStyle;
      hsel.addEventListener('change', () => applyHoldStyle(hsel.value));
    }
    const psw = document.getElementById('pushaside');
    if (psw) {
      psw.checked = pushOn;
      psw.addEventListener('change', () => {
        pushOn = psw.checked;
        // **Write only the off state.** See LS_PUSH above — the default has to survive an empty store.
        try { if (pushOn) localStorage.removeItem(LS_PUSH); else localStorage.setItem(LS_PUSH, '0'); } catch (e) {}
      });
    }
  }
  const fitBtn = document.getElementById('fit');
  if (fitBtn) fitBtn.addEventListener('click', () => setFit(!fitting));
  // Anywhere on the fitted canvas takes you back, to what you pointed at. The tiles do not take the
  // click — while fitted they take nothing, which is the whole of "view only".
  cvScroll.addEventListener('click', (ev) => {
    if (!fitting) return;
    const f = fitScale();
    setFit(false, { x: ev.clientX, y: ev.clientY, k: f.k, c: f.c });
  });
  const tidyBtn = document.getElementById('tidy');
  // `byHand`: a person pressed it, so the view is allowed to go where the windows went. Auto-tidy
  // (the two calls above, on a pane disappearing) must not — nobody asked for that one.
  if (tidyBtn) tidyBtn.addEventListener('click', () => { undoMark('tidying up'); tidyCanvas(current, true); });
  const gatherBtn = document.getElementById('gather');
  if (gatherBtn) gatherBtn.addEventListener('click', () => {
    undoMark('bringing them closer');
    if (!gatherCanvas(current)) toast(['nothing to close up — they are already together']);
  });
  // The floating new-terminal button. Folding the right rail took "Open terminal here" with it and
  // left no way to open one by hand (user, 2026-09-15); this one shows only while that rail is folded
  // and does what Ctrl/⌘⏎ does — a terminal in the folder of the one you are on.
  const fab = document.getElementById('fab-new');
  if (fab) fab.addEventListener('click', () => newTerminal());
  paintTidy();
  const undoBtn = document.getElementById('undo');
  if (undoBtn) undoBtn.addEventListener('click', () => undoLast());
  paintUndo();
  window.palmar.tidyCanvas = tidyCanvas;
  loadRails();
  rzGrip(document.getElementById('rz-l'), 'l');
  rzGrip(document.getElementById('rz-r'), 'r');
  // Fold controls: the header buttons collapse, the tabs left behind bring the rail back.
  for (const side of ['l', 'r']) {
    const f = $('#fold-' + side), o = $('#open-' + side);
    if (f) f.addEventListener('click', () => setFold(side, true));
    if (o) o.addEventListener('click', () => setFold(side, false));
  }
  loadFold();

  // ── when the window has no title bar of its own ─────────────────
  // palmar's own top bar becomes it. **The regions were already marked**: style.css says
  // `-webkit-app-region: drag` on .top and `no-drag` on everything in it you can press. That property
  // does nothing in WebKitGTK — it is a Chromium and WebView2 feature — but it is an exact statement
  // of which parts should move the window, so it is read here and acted on instead of guessed at.
  //
  // Only the window (app/, started with --no-titlebar) sets this. In a browser tab there is nothing
  // to drag and nothing is attached.
  const native = window.PALMAR_NATIVE;
  if (native && native.titlebar === false && window.ipc && window.ipc.postMessage) {
    document.body.classList.add('bare');
    const draggable = (el) => {
      for (let n = el; n && n !== document.body; n = n.parentElement) {
        const region = getComputedStyle(n).getPropertyValue('-webkit-app-region').trim();
        if (region === 'no-drag') return false;
        if (region === 'drag') return true;
      }
      return false;
    };
    const top = $('.top');
    if (top) {
      top.addEventListener('pointerdown', (ev) => {
        if (ev.button !== 0 || !draggable(ev.target)) return;
        // The window manager takes the pointer from here; the page never sees the move.
        window.ipc.postMessage('drag');
      });
      top.addEventListener('dblclick', (ev) => {
        if (!draggable(ev.target)) return;
        window.ipc.postMessage('maximize');
      });
    }
    // Without a title bar there are no window buttons either, so the top bar grows a set.
    const r = $('.top .r');
    if (r) {
      const mk = (cls, label, msg) => {
        const b = el('button', 'wctl ' + cls, '');
        b.type = 'button';
        b.title = label;
        b.setAttribute('aria-label', label);
        b.addEventListener('click', (ev) => { ev.stopPropagation(); window.ipc.postMessage(msg); });
        return b;
      };
      r.append(mk('min', 'minimise', 'minimize'),
               mk('max', 'maximise', 'maximize'),
               mk('cls', 'close', 'close'));
    }
  }
  setNotify(notifyOn && (hasNotificationAPI() ? Notification.permission === 'granted' : true));   // hello settles the window case
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
// **And if it turns up after that, everything measured with the wrong cell.** The race above gives
// up at 1.5s and boots on the fallback face, which is right — a page that never opens is worse than
// one with the wrong column count. But nothing used to happen when the real font then arrived: every
// pane kept the row count it had worked out from a cell of a different size, and a terminal standing
// taller than its box has its bottom rows clipped with nowhere to scroll (user, 2026-09-18). This
// costs nothing when the font was already there — `ready` has resolved and every fit is a no-op.
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(() => {
    for (const t of tiles.values()) if (t.visible()) { remeasure(t.term); t.refit(); }
  }).catch(() => {});
}
})();
