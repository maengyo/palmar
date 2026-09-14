"""The page, in a real browser, against a real daemon.

Slow — each class starts Chrome — and skipped entirely where no Chrome is installed. Run it before
shipping and after touching anything in `palmar/web/`:

    python3 -m unittest tests.test_browser

What it cannot see: anything about GPU compositing. Chrome runs here with --disable-gpu and repaints
in software, so the drag trail in #17 is invisible to this file by construction, not by omission.
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.cdp import Browser, chrome_path
from tests.helpers import Daemon


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def test_it_loads_with_the_token_and_no_errors(self):
        self.assertTrue(self.b.ev("!!window.PALMAR_TOKEN"))
        self.assertGreaterEqual(self.b.ev("window.palmar.canvases.size"), 1)
        self.assertEqual(self.b.errors(), [])

    def test_the_script_does_not_hold_the_key(self):
        """The key's job ends when index.html is served. What is not held cannot be sent, and the
        page used to send it every ten seconds while the daemon was down (#14)."""
        self.assertIsNone(self.b.ev("typeof KEY === 'undefined' ? null : 'KEY exists'"))

    def test_the_attention_list_is_gone(self):
        """It was removed 2026-09-11 — it repeated the left list. Nothing named 'Attention' is left
        in a rail, and the elements it hung on are gone."""
        self.assertFalse(self.b.ev("!!document.getElementById('acts')"))
        self.assertFalse(self.b.ev("!!document.getElementById('act-new')"))
        headers = self.b.ev("[...document.querySelectorAll('.rail .rh')].map(e=>e.textContent).join('|')")
        self.assertNotIn("Attention", headers)

    def test_the_fold_button_moved_into_the_directories_header(self):
        """Its old home, the Attention header, is gone — so it lives in the Directories header now,
        or the right rail can no longer be folded from the rail."""
        header = self.b.ev("document.getElementById('fold-r').closest('.rh').textContent")
        self.assertIn("Directories", header)

    def test_a_quiet_worker_says_so_on_its_row(self):
        """The Attention list's one unique signal — a pane that reads 'working' but has printed
        nothing for STUCK_S — moved onto the pane's row in the left list (msgText).

        **This covers the drawing, not the feed.** It fills lastOutAt by hand, so it passed for a day
        while the daemon sent no `quiet` at all and the note could never appear in real use. What
        the daemon actually sends is tests/test_daemon.py::WhatProtocolPromises."""
        sid = self.open_one()

        def row_msg(quiet_secs):
            return self.b.ev("""(()=>{
              const s=window.palmar.sessions.get(%s);
              s.status='working';
              window.palmar.lastOutAt.set(%s, Date.now()/1000 - %d);
              window.palmar.renderList();
              const m=document.querySelector('.ses[data-id="%s"] .msg');
              return m?m.textContent:null;})()""" % (json.dumps(sid), json.dumps(sid), quiet_secs, sid))

        long_quiet = row_msg(400)          # STUCK_S is 300
        self.assertIsNotNone(long_quiet, "the pane has no row in the left list")
        self.assertIn("quiet", long_quiet, "a working-but-quiet pane did not say so on its row")
        # A pane that just printed is not quiet — the note must not stick around.
        self.assertNotIn("quiet", row_msg(5) or "", "a pane that just printed was called quiet")

    def test_a_terminal_opens_and_types_korean(self):
        sid = self.open_one()
        self.assertGreaterEqual(self.b.ev("window.palmar.tiles.size"), 1)
        self.b.ev("(()=>{window.palmar.tiles.get(%s).sendText('echo 한글도 잘 된다\\n'); return 1;})()"
                  % json.dumps(sid))
        time.sleep(2.5)
        text = self.b.ev("""(()=>{const b=window.palmar.tiles.get(%s).term.buffer.active,o=[];
          for(let i=0;i<b.length;i++){const l=b.getLine(i); if(l) o.push(l.translateToString(true));}
          return o.join('\\n');})()""" % json.dumps(sid))
        self.assertIn("한글도 잘 된다", text)

    def open_one(self):
        """Its own pane. Tests run in name order, and one that borrows a pane another test made
        passes or fails depending on the alphabet — this one did, the first time it ran."""
        sid = self.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          const r=await fetch('/api/sessions?token='+T,{method:'POST',
            headers:{'content-type':'application/json'},body:JSON.stringify({cwd:%s})});
          return (await r.json()).id;})()""" % json.dumps(self.d.home))
        time.sleep(3)
        return sid

    def test_a_bigger_window_is_a_bigger_terminal(self):
        """Not a camera: the rows and columns really grow (README "One terminal can fill the canvas")."""
        sid = self.open_one()
        before = self.b.ev("(()=>{const t=window.palmar.tiles.get(%s); return [t.term.cols,t.term.rows];})()"
                           % json.dumps(sid))
        self.b.ev("(()=>{const t=window.palmar.tiles.get(%s); t.el.style.width='900px'; "
                  "t.el.style.height='520px'; return 1;})()" % json.dumps(sid))
        time.sleep(0.6)
        self.b.ev("(()=>{window.palmar.tiles.get(%s).refit(); return 1;})()" % json.dumps(sid))
        time.sleep(0.8)
        after = self.b.ev("(()=>{const t=window.palmar.tiles.get(%s); return [t.term.cols,t.term.rows];})()"
                          % json.dumps(sid))
        self.assertGreater(after[0], before[0], "%s -> %s" % (before, after))
        self.assertGreater(after[1], before[1])


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class Shortcuts(unittest.TestCase):
    """The guidance has to name **this machine's** keys. The handling side always took both metaKey
    and ctrlKey; only the labels lied (#17)."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def keys_for(self, platform):
        script = ("Object.defineProperty(navigator,'platform',{get:()=>%s});"
                  "try{Object.defineProperty(navigator,'userAgentData',{get:()=>({platform:%s})});}"
                  "catch(e){}" % (json.dumps(platform), json.dumps(platform)))
        with Browser() as b:
            b.open(self.d.url, script=script)
            return b.ev("""(()=>{const k=document.getElementById('kmod');
              const keys=d=>[...d.querySelectorAll('kbd')].map(x=>x.textContent).join('+');
              // Copy is picked by its marker, not by a row number: rows get inserted (the rail-fold
              // shortcut did), and a fixed index would then read whatever slid into that slot.
              const copy=document.querySelector('.keys dt[data-mac-drops-shift]');
              return {search:[...k.querySelectorAll('kbd')].map(x=>x.textContent).join(''),
                      rows:[...document.querySelectorAll('.keys dt')].slice(0,4).map(keys),
                      copy:copy?keys(copy):null};})()""")

    def test_a_mac_is_told_about_command(self):
        r = self.keys_for("MacIntel")
        self.assertEqual(r["search"], "⌘K")
        self.assertEqual(r["rows"][0], "⌘+⏎")
        # **Shift stays on new canvas.** It is what separates it from new terminal, on every
        # platform — the rule that turns Ctrl+Shift+C into ⌘C matched on shape and took it away,
        # leaving two identical rows and one of them wrong.
        self.assertEqual(r["rows"][1], "⌘+Shift+⏎")
        self.assertEqual(r["copy"], "⌘+C")

    def test_everywhere_else_is_told_about_ctrl(self):
        r = self.keys_for("Linux x86_64")
        self.assertEqual(r["search"], "CtrlK")
        self.assertEqual(r["rows"][:2], ["Ctrl+⏎", "Ctrl+Shift+⏎"])
        self.assertEqual(r["copy"], "Ctrl+Shift+C")


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class Theme(unittest.TestCase):
    """Three states, and each has to say which one it is — the mark alone could not, and the only
    explanation was a title attribute nobody hovers (#16)."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def test_each_state_names_itself(self):
        seen = []
        for _ in range(3):
            seen.append(self.b.ev("""(()=>{const b=document.getElementById('theme');
              return [b.dataset.mode, (b.querySelector('b')||{}).textContent];})()"""))
            self.b.ev("document.getElementById('theme').click()")
            time.sleep(0.5)
        modes = [m for m, _ in seen]
        labels = [l for _, l in seen]
        self.assertEqual(len(set(labels)), 3, "two states read the same: %s" % labels)
        self.assertEqual(set(modes), {"system", "light", "dark"})
        self.assertIn("auto", labels)


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class RailFold(unittest.TestCase):
    """Fold a rail to widen the canvas (2026-09-11). A folded rail is a true 0, not clamped to its
    minimum, and the state persists so it survives a reload."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def cv(self):
        return self.b.ev("document.getElementById('cv').offsetWidth")

    def railvar(self, side):
        return self.b.ev("getComputedStyle(document.documentElement).getPropertyValue('--rail-%s').trim()" % side)

    def test_folding_a_rail_widens_the_canvas_to_a_true_zero(self):
        # start from a known state
        self.b.ev("document.getElementById('open-l').click(); document.getElementById('open-r').click()")
        time.sleep(0.3)
        wide0 = self.cv()
        self.b.ev("document.getElementById('fold-l').click()")
        time.sleep(0.3)
        self.assertEqual(self.railvar("l"), "0px", "a folded rail was clamped, not zeroed")
        self.assertGreater(self.cv(), wide0, "the canvas did not widen")
        # reopen restores the width, not zero
        self.b.ev("document.getElementById('open-l').click()")
        time.sleep(0.3)
        self.assertNotEqual(self.railvar("l"), "0px")
        self.assertAlmostEqual(self.cv(), wide0, delta=2)

    def test_the_shortcut_toggles(self):
        self.b.ev("document.getElementById('open-r').click()")   # ensure open
        time.sleep(0.2)
        self.b.ev("document.dispatchEvent(new KeyboardEvent('keydown',"
                  "{key:'\\\\',code:'Backslash',metaKey:true,shiftKey:true,bubbles:true}))")
        time.sleep(0.3)
        self.assertEqual(self.railvar("r"), "0px", "Cmd+Shift+\\ did not fold the right rail")
        self.b.ev("document.dispatchEvent(new KeyboardEvent('keydown',"
                  "{key:'\\\\',code:'Backslash',metaKey:true,shiftKey:true,bubbles:true}))")
        time.sleep(0.3)
        self.assertNotEqual(self.railvar("r"), "0px", "it did not toggle back open")

    def test_the_reopen_tab_is_there_only_while_folded(self):
        """**It is the only thing on screen that says which state a rail is in**, so it being wrong
        is worse than it being absent. It never hid at all: the JS set `hidden` correctly, and
        `.rail-open { display: grid }` beat the UA stylesheet's `[hidden] { display: none }`, so the
        tab sat there after unfolding and people read the state backwards (2026-09-11).

        Asserted on what is painted, not on the attribute — the attribute was always right."""
        def painted(which):
            return self.b.ev("""(()=>{const e=document.getElementById('open-%s');
              const r=e.getBoundingClientRect();
              return r.width>0 && r.height>0 && getComputedStyle(e).display!=='none';})()""" % which)

        self.b.ev("document.getElementById('open-l').click(); document.getElementById('open-r').click()")
        time.sleep(0.3)
        self.assertFalse(painted("l"), "the reopen tab is showing while the rail is open")
        self.assertFalse(painted("r"))

        self.b.ev("document.getElementById('fold-l').click()")
        time.sleep(0.3)
        self.assertTrue(painted("l"), "nothing offers to bring a folded rail back")
        self.assertFalse(painted("r"), "folding one rail showed the other one's tab")

        self.b.ev("document.getElementById('open-l').click()")
        time.sleep(0.3)
        self.assertFalse(painted("l"), "the tab stayed after the rail came back")

    def test_hidden_hides_everywhere(self):
        """The same trap caught .tabs and .mm before this, each patched on its own. One rule now
        covers them; this is what says it still does."""
        for which in ("mm", "keys", "webbox", "diagbox", "restore"):
            shown = self.b.ev("""(()=>{const e=document.getElementById('%s');
              if(!e) return null;
              return e.hidden && getComputedStyle(e).display!=='none';})()""" % which)
            self.assertNotEqual(shown, True, "#%s is marked hidden and still displayed" % which)

    def test_a_folded_rail_is_really_zero(self):
        """The grid column goes to 0 but the rail kept its 1px divider, which is both a line marking
        the edge of a rail that is not there and — at the window's edge — enough to raise a
        horizontal scrollbar."""
        self.b.ev("document.getElementById('open-l').click(); document.getElementById('open-r').click()")
        time.sleep(0.3)
        self.b.ev("document.getElementById('fold-l').click(); document.getElementById('fold-r').click()")
        time.sleep(0.4)
        for side, sel in (("left", ".rail.left"), ("right", ".rail.right")):
            w = self.b.ev("document.querySelector('%s').getBoundingClientRect().width" % sel)
            self.assertEqual(w, 0, "the folded %s rail is %spx wide, not 0" % (side, w))

    def test_folding_takes_the_top_bar_with_it(self):
        """The top bar's outer columns are the rails' widths, so the two line up down the screen —
        **but what sits in them up here does not fold.** Tied to a 0 column the logo was clipped to
        38px and the notify/theme/web/help buttons were laid out past the right edge of the window
        (reported 2026-09-11 as the screen not fitting)."""
        def col(sel):
            return self.b.ev("""(()=>{const e=document.querySelector('%s');
              const r=e.getBoundingClientRect();
              return {w:Math.round(r.width), clipped:e.scrollWidth > Math.ceil(r.width)+1,
                      offscreen: r.right > innerWidth + 1};})()""" % sel)

        self.b.ev("document.getElementById('open-l').click(); document.getElementById('open-r').click()")
        time.sleep(0.3)
        self.b.ev("document.getElementById('fold-l').click(); document.getElementById('fold-r').click()")
        time.sleep(0.4)
        left, right = col(".top .l"), col(".top .r")
        self.assertFalse(left["clipped"], "the logo is cut off when the left rail is folded")
        self.assertFalse(right["offscreen"], "the top-right buttons are off the screen")
        self.assertFalse(right["clipped"])

    def test_folding_can_only_reduce_sideways_scrolling(self):
        """Folding is asked for to get room. It used to leave the page's minimum width alone, so a
        window narrower than that went on scrolling sideways however much was folded away."""
        def scroll_w():
            return self.b.ev("document.documentElement.scrollWidth")
        self.b.ev("document.getElementById('open-l').click(); document.getElementById('open-r').click()")
        time.sleep(0.3)
        wide = scroll_w()
        self.b.ev("document.getElementById('fold-l').click()")
        time.sleep(0.4)
        one = scroll_w()
        self.b.ev("document.getElementById('fold-r').click()")
        time.sleep(0.4)
        both = scroll_w()
        self.assertLessEqual(one, wide, "folding a rail made the page wider")
        self.assertLessEqual(both, one, "folding the second rail made the page wider")

    def test_a_fold_survives_the_window_changing_size(self):
        """**The clamp floors a rail at RAIL_MIN**, so anything that re-applied a folded rail's own
        width sprang it back to 180px while the state still said folded: the rail looked open,
        pressing fold did nothing, and it had to be unfolded first. That is what a window resize did
        — minimise and restore, and the rail is back without being back (reported 2026-09-11)."""
        self.b.ev("document.getElementById('open-l').click()")
        time.sleep(0.3)
        self.b.ev("document.getElementById('fold-l').click()")
        time.sleep(0.4)
        self.assertEqual(self.railvar("l"), "0px")
        try:
            self.b.ws.call("Emulation.setDeviceMetricsOverride",
                           {"width": 760, "height": 560, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(0.5)
            self.assertEqual(self.railvar("l"), "0px", "a resize unfolded it behind the state")
        finally:
            self.b.ws.call("Emulation.clearDeviceMetricsOverride")
            time.sleep(0.5)
        self.assertEqual(self.railvar("l"), "0px", "restoring the window unfolded it behind the state")
        w = self.b.ev("document.querySelector('.rail.left').getBoundingClientRect().width")
        self.assertEqual(w, 0, "it is %spx wide while the state says folded" % w)

    def test_a_fold_survives_a_reload(self):
        self.b.ev("document.getElementById('open-l').click(); document.getElementById('open-r').click()")
        time.sleep(0.2)
        self.b.ev("document.getElementById('fold-r').click()")
        time.sleep(0.3)
        self.b.ws.call("Page.reload"); time.sleep(3.5)
        self.assertTrue(self.b.ev("document.body.classList.contains('fold-r')"),
                        "the right rail did not stay folded across a reload")
        self.assertFalse(self.b.ev("document.body.classList.contains('fold-l')"))


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class RestoreCard(unittest.TestCase):
    """A daemon stopped and came back. The canvases are already there; the terminals are offered."""

    def test_the_offer_appears_restores_and_arranges(self):
        with Daemon() as d:
            for n in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
                d.open_pane(name=n)
            time.sleep(1.2)
            d.restart()
            with Browser() as b:
                b.open(d.url)
                card = b.ev("""(()=>{const e=document.getElementById('restore');
                  return {hidden:e.hidden, head:(e.querySelector('.rs-h')||{}).textContent,
                          rows:[...e.querySelectorAll('.rs-n')].map(x=>x.textContent)};})()""")
                self.assertFalse(card["hidden"], "nothing was offered")
                self.assertIn("6", card["head"])
                self.assertIn("alpha", card["rows"])

                b.ev("document.querySelector('.rs-y').click()")
                time.sleep(6)
                self.assertEqual(b.ev("window.palmar.tiles.size"), 6)
                self.assertTrue(b.ev("document.getElementById('restore').hidden"),
                                "the card stayed after restoring")

                # **Laid out as a grid, not one long column.** firstFree fills a row before the
                # next, and at 520px wide only one tile fits across — six panes came back as a
                # single column 2,232px tall before arrangeCanvas existed.
                r = b.ev("""(()=>{const L=window.palmar.layout(), t=[];
                  for (const x of window.palmar.tiles.values()) if (L[x.id]) t.push(L[x.id]);
                  let over = 0;
                  for (let i=0;i<t.length;i++) for (let j=i+1;j<t.length;j++) {
                    const a=t[i], b=t[j];
                    if (a.x < b.x+b.w && a.x+a.w > b.x && a.y < b.y+b.h && a.y+a.h > b.y) over++;
                  }
                  return {n:t.length, overlaps:over,
                          cols:new Set(t.map(x=>x.x)).size, rows:new Set(t.map(x=>x.y)).size};})()""")
                self.assertEqual(r["overlaps"], 0, "restored panes overlap")
                self.assertGreater(r["cols"], 1, "six panes came back in one column")
                self.assertEqual(b.errors(), [])

    def test_opening_a_pane_another_way_retires_the_offer(self):
        """The card comes from a hello frame. If a pane appears by any other route — you opened one,
        a second browser did — the offer is stale: the daemon already stopped offering, and pressing
        the card now would double the panes. It must retire itself the moment a pane exists."""
        with Daemon() as d:
            for n in ("a", "b", "c"):
                d.open_pane(name=n)
            time.sleep(1.2)
            d.restart()
            with Browser() as b:
                b.open(d.url)
                self.assertFalse(b.ev("document.getElementById('restore').hidden"),
                                 "the offer should be showing")
                b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
                  await fetch('/api/sessions?token='+T,{method:'POST',
                    headers:{'content-type':'application/json'},
                    body:JSON.stringify({cwd:%s,name:'fresh'})});})()""" % json.dumps(d.home))
                time.sleep(2.5)
                self.assertTrue(b.ev("document.getElementById('restore').hidden"),
                                "the stale offer stayed up after a pane appeared")
                self.assertEqual(b.ev("window.palmar.tiles.size"), 1, "the panes doubled")

    def test_dismiss_puts_it_away(self):
        with Daemon() as d:
            d.open_pane(name="only")
            time.sleep(1.2)
            d.restart()
            with Browser() as b:
                b.open(d.url)
                self.assertFalse(b.ev("document.getElementById('restore').hidden"))
                b.ev("document.querySelector('.rs-n2').click()")
                time.sleep(1.5)
                self.assertTrue(b.ev("document.getElementById('restore').hidden"))
                self.assertEqual(b.ev("window.palmar.tiles.size"), 0)


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class ResizeGrip(unittest.TestCase):
    """The corner you drag to resize a window, with a real scrollbar beside it.

    **This class turns scrollbars back on.** Every other test here runs Chrome with
    --hide-scrollbars, which forces every scrollbar to zero width — a test about one would prove
    nothing there, and that is why this bug could not be seen before it was reported.

    What was wrong: the grip is 12px in the very corner, and xterm's viewport puts a 10px scrollbar
    down the right edge ending in the same place, so missing by a few pixels lands on the scrollbar
    and the cursor does not change (user report 2026-09-11). Measured here, the drawn box was already
    winning the hit test inside itself; what was missing was anywhere else to catch."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser(scrollbars=True).start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          await fetch('/api/sessions?token='+T,{method:'POST',
            headers:{'content-type':'application/json'},
            body:JSON.stringify({cwd:%s,name:'grip'})});})()""" % json.dumps(cls.d.home))
        time.sleep(3.5)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def probe(self):
        return self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()][0];
          const v=t.el.querySelector('.xterm-viewport');
          const g=t.el.querySelector('.grip'); const r=g.getBoundingClientRect();
          const hit=(x,y)=>{const e=document.elementFromPoint(x,y);
            return !!(e && (e===g||g.contains(e)||(e.classList&&e.classList.contains('grip'))));};
          const across=[];
          for(const fx of [0.15,0.5,0.85]) across.push(hit(r.left+r.width*fx, r.top+r.height*0.5));
          return {bar: v.offsetWidth - v.clientWidth,
                  across: across,
                  left6: hit(r.left-6, r.top+r.height/2),
                  above6: hit(r.left+r.width/2, r.top-6)};})()""")

    def test_there_really_is_a_scrollbar_to_compete_with(self):
        """The precondition. Without it the rest of this class is measuring nothing — which is
        exactly what --hide-scrollbars did."""
        self.assertGreater(self.probe()["bar"], 0,
                           "no scrollbar took any width, so this proves nothing")

    def test_the_drawn_corner_is_grabbable(self):
        self.assertEqual(self.probe()["across"], [True, True, True],
                         "the scrollbar is taking the hit test inside the grip itself")

    def test_you_can_catch_it_clear_of_the_scrollbar(self):
        """The part that actually changed. Before the fix a point six pixels to the left of the drawn
        grip belonged to the terminal, so the whole target was the 12px square in the corner."""
        p = self.probe()
        self.assertTrue(p["left6"], "the hit area does not reach left of the scrollbar")
        self.assertTrue(p["above6"], "the hit area does not reach above the corner")


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class KoreanUnderWslg(unittest.TestCase):
    """Typing Korean when the IME commits with no preedit — the WSLg case.

    wry turns preedit off (`set_enable_preedit(false)`, webkitgtk/mod.rs:360), ibus then sends its
    preedit to its own panel instead of to the page, and WebKit ends up firing **compositionend for
    a composition it never started** (WebKit bug 84394, ASSIGNED since 2012, reported against
    ibus-hangul). xterm takes that as "finish the composition you are in" and, never having been
    told where it began, sends the whole helper textarea — which it only empties on Enter or Ctrl-C.
    Typing 하이하이 sent ten characters for four (reported from WSLg, 2026-09-11, and reproduced
    below before the fix).

    **What this can and cannot claim.** The event sequence is synthesised, so it is not evidence
    about what WebKitGTK emits — that came from reading WebKit, ibus and wry. What it does hold is
    our own handling of that sequence, which is the part that lives here. Chrome refuses to carry
    `inputType` through the InputEvent constructor, so it is pinned on afterwards; without that the
    branch under test is never reached and this passes while proving nothing."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          await fetch('/api/sessions?token='+T,{method:'POST',
            headers:{'content-type':'application/json'},
            body:JSON.stringify({cwd:%s,name:'ime'})});})()""" % json.dumps(cls.d.home))
        time.sleep(3.5)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    PRELUDE = """
      const sleep=ms=>new Promise(r=>setTimeout(r,ms));
      const t=[...window.palmar.tiles.values()][0];
      const ta=t.termEl.querySelector('textarea');
      ta.value=''; t.term.focus(); await sleep(120);
      const mkInput=(type,data)=>{const e=new InputEvent('input',{data:data,bubbles:true});
        Object.defineProperty(e,'inputType',{value:type}); return e;};
      const out=[]; const sw=t.sendText; t.sendText=x=>{out.push(x); return sw(x);};
      const d=t.term.onData(x=>out.push(x));
    """

    def drive(self, body):
        return self.b.ev("(async()=>{%s\n%s\nawait sleep(450); d.dispose(); t.sendText=sw;\n"
                         "return {joined:out.join(''), ta:ta.value};})()" % (self.PRELUDE, body),
                         timeout=60)

    def test_a_commit_with_no_preedit_sends_it_once(self):
        """Four syllables in, four syllables out. Before the fix this was 하 · 하이 · 하이하 · 하이하이
        — the textarea going out whole, every time, ten characters for four."""
        r = self.drive("""for (const syl of ['하','이','하','이']) {
             ta.dispatchEvent(new KeyboardEvent('keydown',{key:'Unidentified',keyCode:229,bubbles:true}));
             ta.value += syl;
             ta.dispatchEvent(mkInput('insertFromComposition', syl));
             ta.dispatchEvent(new CompositionEvent('compositionend',{data:syl,bubbles:true}));
             await sleep(90); }""")
        self.assertEqual(r["joined"], "하이하이",
                         "the composed text went out more than once")
        self.assertEqual(r["ta"], "", "the helper textarea was left filling up")

    def test_a_real_composition_is_left_alone(self):
        """The end that *does* have a start still belongs to xterm. Dropping ends indiscriminately
        would break every IME on every other platform, which is most of them."""
        r = self.drive("""for (const syl of ['한','글']) {
             ta.dispatchEvent(new CompositionEvent('compositionstart',{data:'',bubbles:true}));
             ta.value = syl;
             ta.dispatchEvent(new CompositionEvent('compositionupdate',{data:syl,bubbles:true}));
             ta.dispatchEvent(mkInput('insertCompositionText', syl));
             ta.dispatchEvent(new CompositionEvent('compositionend',{data:syl,bubbles:true}));
             await sleep(90); ta.value=''; }""")
        self.assertEqual(r["joined"], "한글")

    def test_plain_typing_is_untouched(self):
        r = self.drive("t.term.input('abc'); await sleep(150);")
        self.assertEqual(r["joined"], "abc")


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class AcrossToABrowser(unittest.TestCase):
    """The `web` button: the window and the page are two views of one daemon, so getting from one to
    the other should not mean hunting through ~/.palmar for a file."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def open_panel(self):
        self.b.ev("document.getElementById('toweb').click()")
        time.sleep(1.2)

    def test_it_shows_this_daemon_s_address(self):
        self.open_panel()
        self.assertFalse(self.b.ev("document.getElementById('webbox').hidden"))
        self.assertEqual(self.b.ev("document.getElementById('web-url').value"), self.d.url)
        # select() leaves the field scrolled to its end, which hides the part that says where it is
        self.assertEqual(self.b.ev("document.getElementById('web-url').scrollLeft"), 0)

    def test_closing_it_does_not_leave_the_address_lying_about(self):
        """The page is not given the key at all normally (#14); having asked for it to show once,
        it should not go on holding it."""
        self.open_panel()
        self.b.ev("document.getElementById('web-x').click()")
        time.sleep(0.4)
        self.assertTrue(self.b.ev("document.getElementById('webbox').hidden"))
        self.assertEqual(self.b.ev("document.getElementById('web-url').value"), "")

    def test_the_typing_report_is_not_offered(self):
        """It is a tool for chasing an input bug, not a feature (2026-09-11). Still reachable from
        the console, which is what it is for."""
        self.assertFalse(self.b.ev("!!document.getElementById('diag')"))
        self.assertEqual(self.b.ev("typeof window.palmar.recordTyping"), "function")


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class NoTitleBar(unittest.TestCase):
    """`palmar-app --no-titlebar`: the system draws no title bar and palmar's own top bar is it.

    Under WSLg that bar is drawn by Windows and cannot be restyled from here, and it sits on top of
    the 44px one palmar draws anyway — two bars off the canvas (asked about 2026-09-11).

    The window half cannot be reached from here, so what is driven is the seam: the flag wry injects
    (`window.PALMAR_NATIVE`) and the messages the page posts back. **Which parts drag is read from
    the CSS**, where `-webkit-app-region` was already marked on every piece of the bar — the property
    does nothing in WebKitGTK, but it is an exact statement of intent, so it is obeyed rather than
    guessed at."""

    BARE = ("window.PALMAR_NATIVE={titlebar:false};"
            "window.__sent=[];window.ipc={postMessage:m=>window.__sent.push(m)};")

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def test_a_browser_tab_grows_no_window_buttons(self):
        """There is nothing to drag or close in a tab, and nothing is attached to it."""
        with Browser() as b:
            b.open(self.d.url)
            self.assertFalse(b.ev("document.body.classList.contains('bare')"))
            self.assertEqual(b.ev("document.querySelectorAll('.wctl').length"), 0)

    def test_the_bare_window_gets_them(self):
        with Browser() as b:
            b.open(self.d.url, script=self.BARE)
            self.assertTrue(b.ev("document.body.classList.contains('bare')"))
            self.assertEqual(b.ev("document.querySelectorAll('.wctl').length"), 3)

    def test_the_bar_drags_and_the_buttons_in_it_do_not(self):
        """Pressing the bar has to move the window; pressing something you meant to press must not.
        Both answers come from the app-region marks already in the stylesheet."""
        with Browser() as b:
            b.open(self.d.url, script=self.BARE)
            b.ev("""(()=>{document.querySelector('.top')
                 .dispatchEvent(new PointerEvent('pointerdown',{button:0,bubbles:true})); return 1;})()""")
            time.sleep(0.2)
            self.assertEqual(b.ev("window.__sent"), ["drag"])
            # a control inside the bar is marked no-drag, so it must not start a move
            b.ev("""(()=>{document.getElementById('help')
                 .dispatchEvent(new PointerEvent('pointerdown',{button:0,bubbles:true})); return 1;})()""")
            time.sleep(0.2)
            self.assertEqual(b.ev("window.__sent"), ["drag"], "pressing a button dragged the window")

    def test_the_buttons_say_what_they_are_for(self):
        with Browser() as b:
            b.open(self.d.url, script=self.BARE)
            for cls, msg in (("min", "minimize"), ("max", "maximize"), ("cls", "close")):
                b.ev("window.__sent=[]; document.querySelector('.wctl.%s').click()" % cls)
                time.sleep(0.15)
                self.assertEqual(b.ev("window.__sent"), [msg])


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class PushAside(unittest.TestCase):
    """Windows do not overlap. Drop one on another and the one that was there gets out of the way.

    What was wrong: nothing pushed. The canvas, the drag and the coordinate store had all been built and
    the pushing never had been (roadmap #8) — drag a window onto another and they simply sat on top of
    each other. decisions.md sets the rule: hold the window the hand just placed, move what it landed on
    by **exactly the overlap** and no further, take the **shortest** way out, carry on if that lands on a
    third, and say so in a toast with an undo.

    Geometry is set here by writing the coordinate store, because the interesting cases need windows in
    exact relative positions and three of them at once — more than a hand can drag into place one at a
    time without the earlier drags already pushing things. The store is what the real code reads
    (AGENTS.md "창은 스토어에서 직접 읽는다"), and one test below does drive a whole drag with real mouse
    events to show the store and the hand agree."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          for (const n of ['a','b','c'])
            await fetch('/api/sessions?token='+T,{method:'POST',
              headers:{'content-type':'application/json'},
              body:JSON.stringify({cwd:%s,name:n})});})()""" % json.dumps(cls.d.home))
        time.sleep(5)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def setUp(self):
        """Every test opens its own board (tests/README.md).

        Two things leak between tests here and both bit. **The switch** is sticky by design, so a test
        that turns it off hands the next one a dead feature. **The toast stays up for nine seconds**
        when it carries an undo — far longer than a test takes — so the next test's "did it announce
        anything?" reads the previous test's toast and passes or fails for the wrong reason."""
        self.b.ev("""(()=>{const el=document.getElementById('pushaside');
          if (el && !el.checked) { el.checked = true; el.dispatchEvent(new Event('change')); }
          const t = document.querySelector('.toast');
          if (t) { t.classList.remove('show'); t.textContent = ''; }
          return 1;})()""")

    # ── the bench ────────────────────────────────────────────────────────────
    JS = """
      const P = window.palmar, L = P.layout();
      const by = (n) => [...P.tiles.values()].find((t) => t.s.name === n);
      const put = (n, x, y, w, h) => { const t = by(n);
        L[t.id] = Object.assign({}, L[t.id], {x:x, y:y, w:w, h:h});
        // **Place it instantly.** .tile has a 0.35s transition on left/top, so for a third of a second
        // after this the element is somewhere between where it was and where it was put — and a test
        // that then grabs its title bar by getBoundingClientRect() grabs thin air. That is the same
        // mid-transition trap tidyCanvas and applyPush document; here it made one drag test fail
        // depending on where the *previous* test had left the window.
        t.el.style.transition='none';
        t.el.style.left=x+'px'; t.el.style.top=y+'px'; t.el.style.width=w+'px'; t.el.style.height=h+'px';
        void t.el.offsetWidth;                      // flush the layout while the transition is off
        t.el.style.transition='';
        return t.id; };
      const at = (n) => { const r = L[by(n).id]; return [r.x, r.y, r.w, r.h]; };
    """

    def bench(self, body):
        """Lay the three windows out, run `body`, and hand back whatever it returns."""
        return self.b.ev("(()=>{" + self.JS + "\n" + body + "})()")

    def test_it_pushes_by_the_overlap_and_no_further(self):
        """b sticks 40px into a. It should come to rest one gap clear of a — not a screen away, and not
        snapped to any grid. 'Exactly the overlap' is the whole rule."""
        r = self.bench("""
          put('a',100,100,200,200); put('b',260,100,200,200); put('c',900,900,200,200);
          const m = P.pushAside(by('a').s.canvas, by('a').id);
          P.applyPush(m);
          return {moved: m.length, a: at('a'), b: at('b'), c: at('c')};
        """)
        self.assertEqual(r["moved"], 1, "it moved something other than the one window in the way")
        self.assertEqual(r["a"], [100, 100, 200, 200], "the window the hand placed was moved")
        self.assertEqual(r["b"][:2], [312, 100], "b did not come to rest exactly one gap clear of a")
        self.assertEqual(r["c"], [900, 900, 200, 200], "a window nowhere near it was moved")

    def test_it_takes_the_shortest_way_out(self):
        """b overlaps a from above by 140px. Sideways costs 212px, downwards 272, upwards 152 — up wins.
        Getting this wrong is not a crash, it is a window that flies across the screen when a nudge
        would have done."""
        r = self.bench("""
          put('a',300,300,200,200); put('b',300,240,200,200); put('c',900,900,200,200);
          P.applyPush(P.pushAside(by('a').s.canvas, by('a').id));
          return {b: at('b')};
        """)
        self.assertEqual(r["b"][:2], [300, 88], "b went somewhere other than straight up, the near way")

    def test_a_push_that_lands_on_a_third_carries_on(self):
        """Three in a row, each overlapping the next. Pushing the first has to move both, and the third
        has to end up clear of the second rather than clear of the first."""
        r = self.bench("""
          put('a',100,100,200,200); put('b',260,100,200,200); put('c',420,100,200,200);
          const m = P.pushAside(by('a').s.canvas, by('a').id);
          P.applyPush(m);
          return {moved: m.length, b: at('b'), c: at('c')};
        """)
        self.assertEqual(r["moved"], 2, "the cascade stopped at the first window")
        self.assertEqual(r["b"][:2], [312, 100])
        self.assertEqual(r["c"][:2], [524, 100], "c settled against a instead of against b")

    def test_a_new_window_fills_a_gap_before_growing_the_canvas(self):
        """**They used to pile up downwards.** firstFree scanned the viewport only, so once that was
        full every new terminal went under the last one and the canvas grew for ever — even with the
        top half emptied by closing things (user, 2026-09-14)."""
        r = self.bench("""
          // Fill the viewport, then leave a hole near the top and a tall pile below it.
          put('a', 12, 12, 300, 300);
          put('b', 12, 1400, 300, 300);     // far below: the canvas is now much taller than the view
          put('c', 340, 12, 300, 300);
          // The gap at (12, 330) is free and inside the canvas the tiles already describe.
          const spot = P.firstFree(200, 160, by('a').s.canvas);
          return {spot: spot, deepest: 1400};
        """)
        self.assertLess(r["spot"]["y"], r["deepest"],
                        "it went below everything instead of using the gap: %r" % r["spot"])

    def test_it_still_goes_below_when_nothing_fits(self):
        """The fallback has to stay. A window wider than every gap has nowhere else to go."""
        r = self.bench("""
          put('a', 12, 12, 2000, 300); put('b', 12, 330, 2000, 300); put('c', 12, 650, 2000, 300);
          const spot = P.firstFree(1900, 400, by('a').s.canvas);
          return {spot: spot};
        """)
        self.assertGreaterEqual(r["spot"]["y"], 950, "it claimed a gap that cannot hold it")

    def test_nothing_overlapping_moves_nothing(self):
        """The ordinary case: most drags land in empty space. Nothing should move and no toast should
        appear — a toast on every drag would be noise."""
        r = self.bench("""
          put('a',100,100,200,200); put('b',400,100,200,200); put('c',700,100,200,200);
          return {moved: P.pushAside(by('a').s.canvas, by('a').id).length};
        """)
        self.assertEqual(r["moved"], 0)

    def test_it_never_pushes_a_window_off_the_top_or_left(self):
        """The canvas grows right and down without limit (⑩) but is pinned at 0 on the other two sides.
        A window squeezed against the origin must take a longer way out rather than a negative
        coordinate, which would put it somewhere no scrollbar reaches."""
        r = self.bench("""
          put('a',0,0,400,400); put('b',20,20,200,200); put('c',900,900,200,200);
          P.applyPush(P.pushAside(by('a').s.canvas, by('a').id));
          return {b: at('b')};
        """)
        self.assertGreaterEqual(r["b"][0], 0, "b was pushed to a negative x")
        self.assertGreaterEqual(r["b"][1], 0, "b was pushed to a negative y")
        self.assertTrue(r["b"][0] >= 412 or r["b"][1] >= 412,
                        "b is still sitting on top of a: " + repr(r["b"]))

    def test_a_pile_comes_apart(self):
        """Everything dropped on the same spot. This is the case the round limit exists for — it has to
        either resolve or leave the screen alone, never stop halfway."""
        r = self.bench("""
          put('a',200,200,200,200); put('b',200,200,200,200); put('c',200,200,200,200);
          P.applyPush(P.pushAside(by('a').s.canvas, by('a').id));
          const rs = ['a','b','c'].map(at).map((q) => ({x:q[0], y:q[1], w:q[2], h:q[3]}));
          let bad = 0;
          for (let i=0;i<rs.length;i++) for (let j=i+1;j<rs.length;j++) if (P.hits(rs[i], rs[j])) bad++;
          return {bad: bad, a: at('a')};
        """)
        self.assertEqual(r["bad"], 0, "windows are still overlapping after the push")
        self.assertEqual(r["a"], [200, 200, 200, 200], "the anchor moved out of the pile")

    # ── through the hand ─────────────────────────────────────────────────────
    def drag(self, name, dx, dy):
        """Grab a window by its title bar with real mouse events and drop it dx,dy away."""
        box = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name===%s);
          const r = t.el.querySelector('.tb').getBoundingClientRect();
          return {x: r.left + r.width/2, y: r.top + r.height/2};})()""" % json.dumps(name))
        x, y = box["x"], box["y"]
        send = lambda **kw: self.b.ws.call("Input.dispatchMouseEvent", dict(button="left", **kw))
        send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            send(type="mouseMoved", x=x + dx * i / 3, y=y + dy * i / 3, buttons=1)
        send(type="mouseReleased", x=x + dx, y=y + dy, clickCount=1, buttons=0)
        time.sleep(0.6)

    def test_a_real_drag_onto_another_window_pushes_it(self):
        """The path a person actually takes: press the title bar, move, let go. Everything above reads
        and writes the coordinate store; this is the one that shows the store and the hand agree."""
        self.bench("put('a',60,60,240,200); put('b',340,60,240,200); put('c',60,400,240,200); return 1;")
        self.drag("a", 220, 0)
        r = self.b.ev("""(()=>{const P=window.palmar, L=P.layout();
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          const at=(n)=>{const r=L[by(n).id]; return {x:r.x,y:r.y,w:r.w,h:r.h};};
          const a=at('a'), b=at('b');
          return {a:[a.x,a.y], b:[b.x,b.y], over: P.hits(a,b),
                  toast: document.querySelector('.toast').classList.contains('show'),
                  words: document.querySelector('.toast').textContent,
                  undo: !!document.querySelector('.toast .undo')};})()""")
        self.assertGreater(r["a"][0], 200, "the drag never moved the window: " + repr(r["a"]))
        self.assertFalse(r["over"], "they are still overlapping after the drop: " + repr(r))
        self.assertTrue(r["toast"], "nothing told the user a window had been moved")
        self.assertIn("moved 1 window", r["words"])
        # **No undo of its own any more.** The drag already took a snapshot, so one Ctrl Z puts the
        # move and the push back together — which is what a person means by "undo that". The toast
        # says which key rather than carrying a button that outlives nothing (2026-09-14).
        self.assertIn("Z undoes it", r["words"])

    def test_one_undo_puts_back_the_move_and_the_push(self):
        """**One way back for the whole gesture.** decisions.md asked for an undo by name, and the
        first one lived in the toast — which meant looking up a second too late left no way back at
        all (user, 2026-09-14). The drag takes a snapshot before it starts, so Ctrl Z returns the
        window you moved *and* whatever it pushed, in one press. Undoing only half of a gesture is
        not what anyone means by undo."""
        self.bench("put('a',60,60,240,200); put('b',340,60,240,200); put('c',60,400,240,200); return 1;")
        before = self.b.ev("""(()=>{const P=window.palmar,L=P.layout();
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return {a:[L[by('a').id].x,L[by('a').id].y], b:[L[by('b').id].x,L[by('b').id].y]};})()""")
        self.drag("a", 220, 0)
        moved = self.b.ev("""(()=>{const P=window.palmar,L=P.layout();
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return {a:[L[by('a').id].x,L[by('a').id].y], b:[L[by('b').id].x,L[by('b').id].y]};})()""")
        self.assertNotEqual(moved["a"], before["a"], "the drag did not move anything")
        self.assertNotEqual(moved["b"], before["b"], "nothing was pushed, so there is no push to undo")
        self.b.ev("window.palmar.undoLast()")
        time.sleep(0.4)
        after = self.b.ev("""(()=>{const P=window.palmar,L=P.layout();
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return {a:[L[by('a').id].x,L[by('a').id].y], b:[L[by('b').id].x,L[by('b').id].y],
                  saved: JSON.parse(localStorage.getItem('palmar-tiles')||'{}')[by('b').id]};})()""")
        self.assertEqual(after["b"], before["b"], "the pushed window did not go back")
        self.assertEqual(after["a"], before["a"], "the window I dragged did not go back")
        self.assertEqual([after["saved"]["x"], after["saved"]["y"]], before["b"],
                         "it moved on screen but left the old position saved")

    def test_growing_a_window_pushes_its_neighbour(self):
        """Resize goes through the same moment a drag does — the hand lets go, then the overlap is
        resolved. Shrinking is the asymmetric half: it opens a gap and pulls nothing back, because
        sizes and positions belong to the user and palmar only ever resolves overlap, never tiles
        (AGENTS.md "밀어내기를 타일링으로 바꾸지 마라")."""
        self.bench("put('a',60,60,240,200); put('b',420,60,240,200); put('c',60,400,240,200); return 1;")
        grip = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name==='a');
          const r = t.el.querySelector('.grip').getBoundingClientRect();
          return {x: r.left + r.width/2, y: r.top + r.height/2};})()""")
        x, y = grip["x"], grip["y"]
        send = lambda **kw: self.b.ws.call("Input.dispatchMouseEvent", dict(button="left", **kw))
        send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            send(type="mouseMoved", x=x + 60 * i, y=y, buttons=1)
        send(type="mouseReleased", x=x + 180, y=y, clickCount=1, buttons=0)
        time.sleep(0.8)
        r = self.b.ev("""(()=>{const P=window.palmar, L=P.layout();
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          const at=(n)=>{const q=L[by(n).id]; return {x:q.x,y:q.y,w:q.w,h:q.h};};
          const a=at('a'), b=at('b');
          return {aw: a.w, b:[b.x,b.y], over: P.hits(a,b)};})()""")
        self.assertGreater(r["aw"], 300, "the resize never happened: width " + repr(r["aw"]))
        self.assertFalse(r["over"], "the grown window is sitting on its neighbour: " + repr(r))
        self.assertGreater(r["b"][0], 420, "b did not move out of the way")

    # ── the switch ───────────────────────────────────────────────────────────
    def flip(self, on):
        """Flip the real checkbox in the shortcuts panel, the way a hand does."""
        return self.b.ev("""(()=>{const el=document.getElementById('pushaside');
          if(!el) return 'no switch';
          el.checked=%s; el.dispatchEvent(new Event('change'));
          return window.palmar.pushOn();})()""" % ("true" if on else "false"))

    def test_the_switch_is_there_and_starts_on(self):
        """Not overlapping is the decided behaviour, so the switch is an opt-out, not an opt-in — the
        opposite of Tidy automatically, which starts off because it moves windows you never touched."""
        r = self.b.ev("""(()=>{const el=document.getElementById('pushaside');
          return {there: !!el, checked: el && el.checked, on: window.palmar.pushOn(),
                  label: el && el.closest('label') && el.closest('label').textContent.trim()};})()""")
        self.assertTrue(r["there"], "no switch in the shortcuts panel")
        self.assertTrue(r["on"], "push-aside did not start on")
        self.assertTrue(r["checked"], "the switch does not show the state it is in")
        self.assertIn("Push", r["label"])

    def test_turning_it_off_leaves_the_overlap_alone(self):
        """Off has to mean **nothing moves**, not 'moves less'. The windows stay where they were put."""
        self.assertFalse(self.flip(False), "the switch did not turn it off")
        self.bench("put('a',60,60,240,200); put('b',340,60,240,200); put('c',60,400,240,200); return 1;")
        self.drag("a", 220, 0)
        r = self.b.ev("""(()=>{const P=window.palmar,L=P.layout();
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          const a=L[by('a').id], b=L[by('b').id];
          return {b:[b.x,b.y], over: P.hits(a,b),
                  toast: document.querySelector('.toast').classList.contains('show')};})()""")
        self.assertEqual(r["b"], [340, 60], "b moved with the switch off")
        self.assertTrue(r["over"], "they did not end up overlapping, so this proves nothing")
        self.assertFalse(r["toast"], "it announced a push that never happened")

    def test_turning_it_back_on_pushes_again(self):
        """The switch is not one-way."""
        self.flip(False)
        self.assertTrue(self.flip(True), "the switch did not turn it back on")
        self.bench("put('a',60,60,240,200); put('b',340,60,240,200); put('c',60,400,240,200); return 1;")
        self.drag("a", 220, 0)
        r = self.b.ev("""(()=>{const P=window.palmar,L=P.layout();
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return {over: P.hits(L[by('a').id], L[by('b').id])};})()""")
        self.assertFalse(r["over"], "it did not start pushing again")

    def test_only_the_off_state_is_written_down(self):
        """On is the default, so an empty store has to mean on — otherwise a browser that refuses
        localStorage, or a fresh profile, would silently come up with the feature off."""
        self.flip(False)
        off = self.b.ev("localStorage.getItem('palmar.push')")
        self.flip(True)
        on = self.b.ev("localStorage.getItem('palmar.push')")
        self.assertEqual(off, "0", "turning it off wrote nothing, so it will come back on after a reload")
        self.assertIsNone(on, "turning it back on left a key behind; the default must be an absent key")


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class UpdateNotice(unittest.TestCase):
    """When the daemon comes back on a new version, the page that is still open says so.

    What was missing: **the UI is served by the daemon.** Update palmar and the daemon has the new
    app.js while every tab already open goes on running the old one, with nothing to tell it apart.
    The socket drops on restart and the version is the first thing the new daemon says, so the check
    reaches nothing outside the machine — this is deliberately not an "is there a newer release"
    check, which would be palmar's first outbound connection ever and is a separate decision.

    It also answers "where did my shells go": ⑦=b says the daemon restarts only for an update, and
    the shell panes do not survive it."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def setUp(self):
        self.b.ev("""(()=>{const t=document.querySelector('.toast');
          if (t) { t.classList.remove('show'); t.textContent=''; } return 1;})()""")

    def toast(self):
        return self.b.ev("""(()=>{const t=document.querySelector('.toast');
          return {shown: t.classList.contains('show'), words: t.textContent,
                  action: !!t.querySelector('.undo')};})()""")

    def test_the_page_knows_which_daemon_it_attached_to(self):
        """The wiring. Without this the rest of the class tests a function nothing calls."""
        self.assertTrue(self.b.ev("window.palmar.daemonSeen()"),
                        "the hello handler never recorded the daemon version")

    def test_attaching_is_not_news(self):
        """Opening the page must not announce an update. The version at attach is simply this page's."""
        v = self.b.ev("window.palmar.daemonSeen()")
        self.assertFalse(self.b.ev("window.palmar.checkVersion({daemon:%s})" % json.dumps(v)))
        self.assertFalse(self.toast()["shown"], "it announced an update on the version it already had")

    def test_a_different_version_says_so_and_offers_a_reload(self):
        self.assertTrue(self.b.ev("window.palmar.checkVersion({daemon:'9.9.9'})"))
        t = self.toast()
        self.assertTrue(t["shown"], "nothing told the user the daemon had changed")
        self.assertIn("9.9.9", t["words"])
        self.assertIn("old one", t["words"])
        self.assertTrue(t["action"], "no way to act on it — the fix is a reload")
        # and it does not repeat itself for the same version
        self.b.ev("document.querySelector('.toast').classList.remove('show')")
        self.assertFalse(self.b.ev("window.palmar.checkVersion({daemon:'9.9.9'})"),
                         "it announced the same version twice")

    def test_a_daemon_that_does_not_say_is_not_an_error(self):
        """Older daemons send no `daemon` field. That is checkProtocol's case, not this one, and it
        must not throw on the way past."""
        self.assertFalse(self.b.ev("window.palmar.checkVersion({})"))
        self.assertFalse(self.b.ev("window.palmar.checkVersion(null)"))
        self.assertFalse(self.toast()["shown"])

    def test_the_reload_advice_matches_what_the_daemon_sends(self):
        """It used to ask for a **hard** refresh. Every reply carries `Cache-Control: no-store`, so an
        ordinary reload already fetches the new files — asking for a gesture nobody needs is how a
        notice trains people to ignore it."""
        import urllib.request
        req = urllib.request.Request(self.d.base + "/app.js", headers={"Origin": self.d.base})
        with urllib.request.urlopen(req, timeout=15) as r:
            cc = (r.headers.get("Cache-Control") or "").lower()
        self.assertIn("no-store", cc,
                      "static replies lost Cache-Control: no-store, so a plain reload may serve the old page")


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class JoiningPaths(unittest.TestCase):
    """`joinDir` — how the directory rail builds a child's path from its parent's.

    It was written for POSIX only: absolute meant "starts with /", and the separator was always "/".
    On Windows that makes `C:\\/Users`, which happens to work when it goes back to the daemon and then
    appears, wrong, in every path shown on screen."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def join(self, parent, name):
        return self.b.ev("window.palmar.joinDir(%s, %s)" % (json.dumps(parent), json.dumps(name)))

    def test_posix_is_unchanged(self):
        self.assertEqual(self.join("/Users/kim", "work"), "/Users/kim/work")
        self.assertEqual(self.join("", "/"), "/")
        self.assertEqual(self.join("/home", "kim"), "/home/kim")

    def test_a_drive_letter_is_absolute_too(self):
        """Roots arrive as absolute names. Without this, `C:\\` was joined onto its parent rather
        than replacing it."""
        self.assertEqual(self.join("", "C:\\"), "C:\\")
        self.assertEqual(self.join("/whatever", "D:\\"), "D:\\")

    def test_the_separator_is_the_parent_s(self):
        self.assertEqual(self.join("C:\\", "Users"), "C:\\Users")
        self.assertEqual(self.join("C:\\Users", "kim"), "C:\\Users\\kim")


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class ANewPaneFitsItsWindow(unittest.TestCase):
    """A terminal must fill its window the moment it opens, not once something resizes it.

    **What was wrong:** `fit()` ran in the same tick as `open()`, before the renderer had measured a
    cell, so it worked out a size and did not apply it — and the flag was set to "fitted" regardless.
    That flag is what the one "fit this pane later" path checks, so nothing ever tried again. The pane
    stayed at xterm's 80x24 default inside a smaller box: measured 2026-09-14, propose said 67x19
    while the terminal was 24 rows tall in a 20-row viewport, so three and a half rows sat below the
    visible area. Scrolling to the bottom did not reach the cursor, and resizing the window was the
    only way to see it (user report).

    **Not Windows.** It reproduces here, on a Mac, with the same numbers."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser(scrollbars=True).start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          await fetch('/api/sessions?token='+T,{method:'POST',
            headers:{'content-type':'application/json'},
            body:JSON.stringify({cwd:%s,name:'fitme'})});})()""" % json.dumps(cls.d.home))
        time.sleep(4)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def look(self):
        return self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()][0];
          const vp=t.el.querySelector('.xterm-viewport');
          const sc=t.el.querySelector('.xterm-screen');
          return {rows:t.term.rows, cols:t.term.cols, fitted:t.fitted,
                  want:t.fit.proposeDimensions(),
                  vpH:vp.clientHeight, vpW:vp.clientWidth,
                  scH:sc.offsetHeight, scW:sc.offsetWidth};})()""")

    def test_the_last_row_is_inside_the_window(self):
        """The symptom itself: rendered rows taller than the box means the bottom is unreachable."""
        r = self.look()
        self.assertLessEqual(r["scH"], r["vpH"] + 1,
                             "the terminal draws %dpx of rows into a %dpx box — %r"
                             % (r["scH"], r["vpH"], r))

    def test_it_is_not_wider_than_the_window_either(self):
        """The same miscount sideways, which is what puts a horizontal scrollbar over the last row."""
        r = self.look()
        self.assertLessEqual(r["scW"], r["vpW"] + 1,
                             "the terminal draws %dpx wide into a %dpx box — %r"
                             % (r["scW"], r["vpW"], r))

    def test_it_took_the_size_it_worked_out(self):
        r = self.look()
        self.assertEqual([r["rows"], r["cols"]], [r["want"]["rows"], r["want"]["cols"]],
                         "it is not the size it says it wants: %r" % r)

    def test_the_flag_means_what_it_says(self):
        """`fitted` gates the only path that fits a pane later. Setting it on a pane that did not fit
        is worse than leaving it false — it removes the retry and leaves no trace."""
        r = self.look()
        self.assertTrue(r["fitted"])
        self.assertEqual(r["rows"], r["want"]["rows"])


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class Grouping(unittest.TestCase):
    """Hold a window still over another and they travel together.

    A lighter thing than a canvas: a canvas is a different workbench, a group is a set that lives
    together on one (asked for 2026-09-14). It fits because push-aside already settled when overlap
    is allowed — windows may overlap while the hand is down, and only the drop resolves it — so the
    hold happens in a moment that already existed."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          for (const n of ['g1','g2','g3'])
            await fetch('/api/sessions?token='+T,{method:'POST',
              headers:{'content-type':'application/json'},
              body:JSON.stringify({cwd:%s,name:n})});})()""" % json.dumps(cls.d.home))
        time.sleep(5)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def setUp(self):
        """Every test opens its own board: no groups, nothing where it was left."""
        self.b.ev("""(()=>{const P=window.palmar, L=P.layout();
          for (const t of P.tiles.values()) if (L[t.id]) { const n=Object.assign({},L[t.id]); delete n.g; L[t.id]=n; }
          const t=document.querySelector('.toast'); if(t){t.classList.remove('show');t.textContent='';}
          return 1;})()""")

    JS = """
      const P = window.palmar, L = P.layout();
      const by = (n) => [...P.tiles.values()].find((t) => t.s.name === n);
      const put = (n, x, y, w, h) => { const t = by(n);
        L[t.id] = Object.assign({}, L[t.id], {x:x, y:y, w:w, h:h});
        t.el.style.transition='none';
        t.el.style.left=x+'px'; t.el.style.top=y+'px'; t.el.style.width=w+'px'; t.el.style.height=h+'px';
        void t.el.offsetWidth; t.el.style.transition='';
        return t.id; };
      const at = (n) => { const r = L[by(n).id]; return [r.x, r.y]; };
    """

    def bench(self, body):
        return self.b.ev("(()=>{" + self.JS + "\n" + body + "})()")

    def test_the_model_joins_and_leaves(self):
        """Without a hand: two windows join, a third joins the same group, one leaves."""
        r = self.bench("""
          const a = by('g1').id, b = by('g2').id, c = by('g3').id;
          P.joinGroups(a, b);
          const two = P.groupOf(a).length;
          P.joinGroups(c, b);
          const three = P.groupOf(a).length;
          P.leaveGroup(c);
          return {two: two, three: three, after: P.groupOf(a).length, cAlone: P.groupOf(c).length};
        """)
        self.assertEqual([r["two"], r["three"], r["after"], r["cAlone"]], [2, 3, 2, 1])

    def test_a_group_of_one_is_not_a_group(self):
        """Leaving a pair has to dissolve it, or the other window keeps a colour and a promise that
        no longer means anything."""
        r = self.bench("""
          P.joinGroups(by('g1').id, by('g2').id);
          P.leaveGroup(by('g1').id);
          return {a: P.groupOf(by('g1').id).length, b: P.groupOf(by('g2').id).length,
                  marked: document.querySelectorAll('.tile.grouped').length};
        """)
        self.assertEqual([r["a"], r["b"], r["marked"]], [1, 1, 0])

    def test_push_aside_moves_a_group_as_one_block(self):
        """**The reason groupRect exists.** Pushed window by window, a push could walk between two
        members and take the group apart — which is the one thing a group is for."""
        r = self.bench("""
          // g1 must actually land on g2, or there is nothing to push.
          put('g1', 40, 40, 260, 200);    // spans 40..300
          put('g2', 260, 40, 200, 200);   // g2 and g3 are a group, side by side — 260..460
          put('g3', 480, 40, 200, 200);
          P.joinGroups(by('g2').id, by('g3').id);
          const gap0 = at('g3')[0] - at('g2')[0];
          P.applyPush(P.pushAside(by('g1').s.canvas, by('g1').id));
          return {gap0: gap0, gap1: at('g3')[0] - at('g2')[0], g2: at('g2'), g3: at('g3')};
        """)
        self.assertEqual(r["gap1"], r["gap0"],
                         "the group was stretched: %r -> %r" % (r["g2"], r["g3"]))
        self.assertGreater(r["g2"][0], 260, "the group did not move out of the way at all")

    # ── through the hand ─────────────────────────────────────────────────────
    def press(self, name, **kw):
        box = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name===%s);
          const r=t.el.querySelector('.tb').getBoundingClientRect();
          return {x:r.left+r.width/2, y:r.top+r.height/2};})()""" % json.dumps(name))
        return box["x"], box["y"]

    def send(self, **kw):
        self.b.ws.call("Input.dispatchMouseEvent", dict(button="left", **kw))

    def test_holding_one_over_another_groups_them(self):
        """The gesture itself. Moving resets the hold, so it is dragged there and then left alone."""
        self.bench("put('g1',60,60,240,200); put('g2',360,60,240,200); put('g3',60,400,240,200); return 1;")
        x, y = self.press("g1")
        tx, ty = self.press("g2")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + (tx - x) * i / 3, y=y + (ty - y) * i / 3, buttons=1)
        # Still, over it, for longer than the hold. A jiggle inside GROUP_STILL must not reset it.
        # **Both facts in one round trip.** Asking twice let the group form between the questions, so
        # the highlight was gone by the time the second one arrived and the gesture looked invisible.
        lit = False
        for _ in range(14):
            st = self.b.ev("""(()=>{const P=window.palmar;
              const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
              return {lit: document.querySelectorAll('.tile.joining').length > 0,
                      n: P.groupOf(by('g1').id).length};})()""")
            lit = lit or st["lit"]
            if st["n"] == 2:
                break
            time.sleep(0.18)
            self.send(type="mouseMoved", x=tx + 1, y=ty, buttons=1)
        self.send(type="mouseReleased", x=tx + 1, y=ty, clickCount=1, buttons=0)
        time.sleep(0.4)
        n = self.b.ev("""(()=>{const P=window.palmar;
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return P.groupOf(by('g1').id).length;})()""")
        self.assertEqual(n, 2, "holding one window over another did not group them")
        self.assertTrue(lit, "nothing showed it was about to happen — the gesture is invisible")

    def test_dragging_a_member_moves_the_whole_group(self):
        self.bench("""put('g1',60,60,240,200); put('g2',360,60,240,200); put('g3',60,400,240,200);
                      P.joinGroups(by('g1').id, by('g2').id); return 1;""")
        before = self.bench("return {a: at('g1'), b: at('g2')};")
        x, y = self.press("g1")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x, y=y + 60 * i / 3, buttons=1)
        self.send(type="mouseReleased", x=x, y=y + 60, clickCount=1, buttons=0)
        time.sleep(0.5)
        after = self.bench("return {a: at('g1'), b: at('g2')};")
        self.assertGreater(after["a"][1], before["a"][1], "the dragged window did not move")
        self.assertEqual(after["b"][1] - before["b"][1], after["a"][1] - before["a"][1],
                         "the other member did not come with it: %r -> %r" % (before, after))

    def test_alt_drag_takes_one_out(self):
        """A group moves together, so there has to be a way to mean "just this one"."""
        self.bench("""put('g1',60,60,240,200); put('g2',360,60,240,200); put('g3',60,400,240,200);
                      P.joinGroups(by('g1').id, by('g2').id); return 1;""")
        before = self.bench("return {b: at('g2')};")
        x, y = self.press("g1")
        self.b.ws.call("Input.dispatchMouseEvent",
                       dict(button="left", type="mousePressed", x=x, y=y, clickCount=1,
                            buttons=1, modifiers=1))          # 1 = Alt
        for i in (1, 2, 3):
            self.b.ws.call("Input.dispatchMouseEvent",
                           dict(button="left", type="mouseMoved", x=x, y=y + 40 * i / 3,
                                buttons=1, modifiers=1))
        self.b.ws.call("Input.dispatchMouseEvent",
                       dict(button="left", type="mouseReleased", x=x, y=y + 40, clickCount=1,
                            buttons=0, modifiers=1))
        time.sleep(0.5)
        r = self.b.ev("""(()=>{const P=window.palmar;
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return {a: P.groupOf(by('g1').id).length, b: P.groupOf(by('g2').id).length};})()""")
        after = self.bench("return {b: at('g2')};")
        self.assertEqual([r["a"], r["b"]], [1, 1], "alt-drag did not take it out of the group")
        self.assertEqual(after["b"], before["b"], "the one left behind moved anyway")

    def test_moving_away_before_letting_go_cancels_it(self):
        """**Armed, not done.** It used to join in the middle of the drag, so carrying on somewhere
        else left you grouped to a window you had moved away from (user, 2026-09-14). The hold arms
        it; the release commits it."""
        self.bench("put('g1',60,60,240,200); put('g2',360,60,240,200); put('g3',60,400,240,200); return 1;")
        x, y = self.press("g1")
        tx, ty = self.press("g2")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + (tx - x) * i / 3, y=y + (ty - y) * i / 3, buttons=1)
        ready = False
        for _ in range(12):
            ready = ready or self.b.ev("!!document.querySelector('.tile.joinready')")
            if ready:
                break
            time.sleep(0.18)
            self.send(type="mouseMoved", x=tx + 1, y=ty, buttons=1)
        self.assertTrue(ready, "it never armed, so this proves nothing")
        # Still holding: carry on somewhere empty and let go there.
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=tx, y=ty + 260 * i / 3, buttons=1)
        self.send(type="mouseReleased", x=tx, y=ty + 260, clickCount=1, buttons=0)
        time.sleep(0.5)
        n = self.b.ev("""(()=>{const P=window.palmar;
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return P.groupOf(by('g1').id).length;})()""")
        self.assertEqual(n, 1, "it grouped anyway after the hand moved on")

    def test_grouping_lines_them_up(self):
        """A group that leaves everyone where they were is a colour, not a group. Sizes are the
        user's, so members are lined up rather than resized (AGENTS.md)."""
        r = self.bench("""
          put('g1', 40, 40, 200, 160);
          put('g2', 700, 380, 200, 160);   // far apart and out of line
          P.joinGroups(by('g1').id, by('g2').id);
          P.arrangeGroup(P.groupOf(by('g1').id));
          return {a: at('g1'), b: at('g2')};
        """)
        a, b = r["a"], r["b"]
        self.assertEqual(a[1], b[1], "they are not on one row: %r %r" % (a, b))
        self.assertLess(abs(a[0] - b[0]) + abs(a[1] - b[1]), 700,
                        "they are still scattered: %r %r" % (a, b))

    def test_it_lays_a_group_out_across_before_down(self):
        """**As wide as fits, with no empty cells.** The canvas grows downwards without limit and its
        width is finite, so a group laid out across spends the space there is instead of the space
        there always is. A log-of-the-ratio score was tried first and put three windows in a column
        that ran off the bottom of the screen — right by its own measure, and the measure was wrong
        for a group (2026-09-14)."""
        r = self.bench("""
          // Narrow enough that three fit across.
          put('g1', 30, 30, 240, 190); put('g2', 600, 320, 240, 190); put('g3', 30, 320, 240, 190);
          P.joinGroups(by('g1').id, by('g2').id);
          P.joinGroups(by('g3').id, by('g1').id);
          P.arrangeGroup(P.groupOf(by('g1').id));
          return {a: at('g1'), b: at('g2'), c: at('g3')};
        """)
        tops = {r["a"][1], r["b"][1], r["c"][1]}
        self.assertEqual(len(tops), 1, "three windows did not end up on one row: %r" % r)

    def test_it_wraps_rather_than_running_off_the_side(self):
        """Wide is preferred, not forced. Windows too wide to sit side by side have to wrap."""
        r = self.bench("""
          put('g1', 20, 20, 700, 160); put('g2', 20, 220, 700, 160);
          P.joinGroups(by('g1').id, by('g2').id);
          P.arrangeGroup(P.groupOf(by('g1').id));
          return {a: at('g1'), b: at('g2'), room: document.querySelector('.cv-scroll').clientWidth};
        """)
        self.assertNotEqual(r["a"][1], r["b"][1],
                            "two 700px windows were put side by side in %dpx: %r" % (r["room"], r))

    def test_the_frame_follows_the_shape_not_a_box_around_it(self):
        """**One cell per member, unioned — an ㄱ comes out an ㄱ** (user, 2026-09-14: "굳이 사각형
        안에 들어가게 하는 게 아니라 ㄱ 자 모양으로 묶어도 되잖아"). A bounding rectangle claimed a
        square the group did not use, which is what "it takes up space of its own accord" was about."""
        r = self.bench("""
          // Wide enough that only two fit across, so the third wraps and the shape is an ㄱ.
          put('g1', 30, 30, 400, 200); put('g2', 700, 300, 400, 200); put('g3', 30, 300, 400, 200);
          P.joinGroups(by('g1').id, by('g2').id);
          P.joinGroups(by('g3').id, by('g1').id);
          P.arrangeGroup(P.groupOf(by('g1').id));
          const boxes = [...document.querySelectorAll('.gbox')];
          const cells = [...boxes[0].children].map((c) => ({
            x: parseInt(c.style.left), y: parseInt(c.style.top),
            w: parseInt(c.style.width), h: parseInt(c.style.height)}));
          const ids = P.groupOf(by('g1').id);
          return {boxes: boxes.length, cells: cells, rect: P.groupRect(ids),
                  pointer: getComputedStyle(boxes[0]).pointerEvents,
                  at: ids.map((id) => [P.layout()[id].x, P.layout()[id].y])};
        """)
        self.assertEqual(r["boxes"], 1, "a group drew %d frames" % r["boxes"])
        self.assertEqual(len(r["cells"]), 3, "the frame is not one cell per member")
        self.assertEqual(r["pointer"], "none", "the frame would swallow drags")
        # Every member is covered by its own cell.
        for (mx, my), c in zip(r["at"], r["cells"]):
            self.assertLessEqual(c["x"], mx)
            self.assertLessEqual(c["y"], my)
        # **And the corner the group does not use is not covered.** That is the whole point: the
        # bounding box would reach it, the union does not.
        rect = r["rect"]
        corner = (rect["x"] + rect["w"] - 10, rect["y"] + rect["h"] - 10)
        inside = any(c["x"] <= corner[0] <= c["x"] + c["w"] and c["y"] <= corner[1] <= c["y"] + c["h"]
                     for c in r["cells"])
        self.assertFalse(inside,
                         "the frame covers the empty corner — it is still a bounding box: %r" % r["cells"])

    def test_it_wraps_into_an_L_rather_than_one_long_row(self):
        """Windows too wide to sit three across wrap, and that is allowed to look like an ㄱ now —
        the rule about never leaving an empty cell went away with the bounding box that made it
        necessary."""
        r = self.bench("""
          put('g1', 30, 30, 400, 200); put('g2', 700, 300, 400, 200); put('g3', 30, 300, 400, 200);
          P.joinGroups(by('g1').id, by('g2').id);
          P.joinGroups(by('g3').id, by('g1').id);
          P.arrangeGroup(P.groupOf(by('g1').id));
          return {at: P.groupOf(by('g1').id).map((id)=>[P.layout()[id].x, P.layout()[id].y])};
        """)
        rows = sorted({p[1] for p in r["at"]})
        self.assertEqual(len(rows), 2, "three 400px windows did not wrap onto two rows: %r" % r["at"])
        top = [p for p in r["at"] if p[1] == rows[0]]
        self.assertEqual(len(top), 2, "the top row is not full: %r" % r["at"])

    def test_growing_a_member_does_not_land_it_on_its_group_mates(self):
        """**A group is one block to the outside world**, so the ordinary push cannot see an overlap
        *between its own members* — and growing one is exactly what makes one. Reported and then
        measured (user, 2026-09-14): a member dragged out to 390px sat on top of the one beside it.
        The group is sorted out among itself first, then the canvas."""
        self.bench("""put('g1',40,40,240,180); put('g2',300,40,240,180); put('g3',40,260,240,180);
                      P.joinGroups(by('g1').id, by('g2').id);
                      P.joinGroups(by('g3').id, by('g1').id);
                      P.arrangeGroup(P.groupOf(by('g1').id)); return 1;""")
        grip = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name==='g1');
          const r=t.el.querySelector('.grip').getBoundingClientRect();
          return {x:r.left+r.width/2, y:r.top+r.height/2};})()""")
        x, y = grip["x"], grip["y"]
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + 150 * i / 3, y=y + 120 * i / 3, buttons=1)
        self.send(type="mouseReleased", x=x + 150, y=y + 120, clickCount=1, buttons=0)
        time.sleep(0.8)
        r = self.b.ev("""(()=>{const P=window.palmar, L=P.layout();
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          const ids = P.groupOf(by('g1').id);
          const bad = [];
          for (let i=0;i<ids.length;i++) for (let j=i+1;j<ids.length;j++)
            if (P.hits(L[ids[i]], L[ids[j]])) bad.push([i,j]);
          return {grew: L[by('g1').id].w, bad: bad,
                  at: ids.map((id)=>[L[id].x, L[id].y, L[id].w, L[id].h])};})()""")
        self.assertGreater(r["grew"], 240, "the resize never happened, so this proves nothing")
        self.assertEqual(r["bad"], [],
                         "members of one group are overlapping after a resize: %r" % r["at"])

    def test_the_group_still_holds_together_after_that(self):
        """Sorting a group out among itself must not scatter it — the members move, the group stays."""
        self.bench("""put('g1',40,40,200,160); put('g2',260,40,200,160);
                      P.joinGroups(by('g1').id, by('g2').id);
                      P.arrangeGroup(P.groupOf(by('g1').id)); return 1;""")
        r = self.b.ev("""(()=>{const P=window.palmar, L=P.layout();
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          const id = by('g1').id;
          L[id] = Object.assign({}, L[id], {w: 420});      // grown onto its mate
          by('g1').el.style.width = '420px';
          P.applyPush(P.pushAside(by('g1').s.canvas, id, {only: P.groupOf(id), solo: true}));
          return {n: P.groupOf(id).length,
                  over: P.hits(L[id], L[by('g2').id]),
                  frames: document.querySelectorAll('.gbox').length};})()""")
        self.assertEqual(r["n"], 2, "the group came apart")
        self.assertFalse(r["over"], "they are still overlapping")
        self.assertEqual(r["frames"], 1, "the frame did not survive")

    def test_a_group_survives_a_reload(self):
        """Membership rides with the position, so it comes back the same way (③ provisional)."""
        self.bench("P.joinGroups(by('g1').id, by('g2').id); return 1;")
        # **saveLayout is debounced 150ms.** Reloading inside that window loses the group — and the
        # position too, which it has always done. Waiting is the honest test of what is stored, not
        # of how fast it is written.
        time.sleep(0.6)
        self.b.open(self.d.url)
        time.sleep(4)
        n = self.b.ev("""(()=>{const P=window.palmar;
          const by=(x)=>[...P.tiles.values()].find(t=>t.s.name===x);
          return by('g1') ? P.groupOf(by('g1').id).length : -1;})()""")
        self.assertEqual(n, 2, "the group did not come back after a reload")


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class Undoing(unittest.TestCase):
    """One way back for everything that moves a window.

    Push-aside came with an undo in its toast and grouping came with another, and neither outlived
    the toast — so a person who looked up a second too late had no way back at all ("돌이킬 수가
    없네", 2026-09-14). One stack is less to learn and less to build, and it is what `Ctrl Z` means
    everywhere else.

    **Whole-canvas snapshots, not inverse operations.** An inverse has to be written once per
    operation and is wrong in a different way each time."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          for (const n of ['u1','u2'])
            await fetch('/api/sessions?token='+T,{method:'POST',
              headers:{'content-type':'application/json'},
              body:JSON.stringify({cwd:%s,name:n})});})()""" % json.dumps(cls.d.home))
        time.sleep(5)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def bench(self, body):
        js = """
          const P = window.palmar, L = P.layout();
          const by = (n) => [...P.tiles.values()].find((t) => t.s.name === n);
          const put = (n,x,y,w,h) => { const t=by(n);
            L[t.id]=Object.assign({},L[t.id],{x:x,y:y,w:w,h:h});
            t.el.style.transition='none';
            t.el.style.left=x+'px'; t.el.style.top=y+'px';
            t.el.style.width=w+'px'; t.el.style.height=h+'px';
            void t.el.offsetWidth; t.el.style.transition=''; };
          const at = (n) => { const r=L[by(n).id]; return [r.x,r.y]; };
        """
        return self.b.ev("(()=>{" + js + "\n" + body + "})()")

    def test_it_puts_a_move_back(self):
        r = self.bench("""
          put('u1', 100, 100, 200, 160);
          P.undoMark('a test move');
          put('u1', 500, 400, 200, 160);
          const moved = at('u1');
          P.undoLast();
          return {moved: moved, back: at('u1')};
        """)
        self.assertEqual(r["moved"], [500, 400])
        self.assertEqual(r["back"], [100, 100], "it did not put the window back")

    def test_it_puts_a_group_back(self):
        """Membership is part of the snapshot, so undoing a grouping ungroups."""
        r = self.bench("""
          put('u1', 60, 60, 200, 160); put('u2', 300, 60, 200, 160);
          P.undoMark('a test grouping');
          P.joinGroups(by('u1').id, by('u2').id);
          const grouped = P.groupOf(by('u1').id).length;
          P.undoLast();
          return {grouped: grouped, after: P.groupOf(by('u1').id).length,
                  frames: document.querySelectorAll('.gbox').length};
        """)
        self.assertEqual(r["grouped"], 2)
        self.assertEqual(r["after"], 1, "undo left them grouped")
        self.assertEqual(r["frames"], 0, "the frame outlived the group it named")

    def test_it_stops_at_the_bottom_rather_than_throwing(self):
        r = self.bench("""
          while (P.undoDepth()) P.undoLast();
          return {left: P.undoDepth(), again: P.undoLast()};
        """)
        self.assertEqual(r["left"], 0)
        self.assertFalse(r["again"], "undoing an empty stack claimed to have done something")

    def test_the_button_says_whether_there_is_anything_to_undo(self):
        """A button that does nothing when pressed is a button that lies — the same rule the tidy
        button already follows."""
        r = self.bench("""
          while (P.undoDepth()) P.undoLast();
          const off = document.getElementById('undo').disabled;
          P.undoMark('something');
          const on = document.getElementById('undo').disabled;
          return {off: off, on: on};
        """)
        self.assertTrue(r["off"], "it offered an undo with nothing to undo")
        self.assertFalse(r["on"], "it refused an undo that exists")

    def test_ctrl_z_is_not_taken_from_a_terminal(self):
        """Inside a terminal it belongs to whatever is running there — an editor's undo is not ours.
        The same rule Esc follows, and Ctrl-C."""
        depth = self.b.ev("""(()=>{const P=window.palmar;
          P.undoMark('a test mark');
          const before = P.undoDepth();
          const t = [...P.tiles.values()][0];
          const target = t.el.querySelector('.xterm') || t.el;
          target.dispatchEvent(new KeyboardEvent('keydown',
            {key:'z', ctrlKey:true, metaKey:true, bubbles:true, cancelable:true}));
          return {before: before, after: P.undoDepth()};})()""")
        self.assertEqual(depth["after"], depth["before"],
                         "Ctrl Z inside a terminal undid a canvas change")


if __name__ == "__main__":
    unittest.main()
