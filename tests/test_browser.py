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

    def tearDown(self):
        """**A throw inside the drag handler is silent.** `overSideAtDrop` was never declared, so the
        arming block died on every pointermove and the gesture simply never finished — no failure and
        no message, the page carrying on as if nothing had happened (2026-09-14). The page already
        collects its own exceptions; this class only had to ask."""
        self.assertEqual(self.b.errors(), [], "the page threw while being driven")

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
        """The gesture itself: carried there, held, let go.

        **"Showed it was about to happen" is any of three marks**, because they replace one another as
        the hold runs: the target lights up on arrival, the gauge fills on the window in the hand, and
        the target goes to its armed mark at the end. Watching only for the first one made this fail
        under a loaded machine, where the first look already arrived after the hold was full."""
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
        for _ in range(20):
            st = self.b.ev("""(()=>{const P=window.palmar;
              const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
              return {lit: document.querySelectorAll('.tile.joining, .tile.joinready, .tile.arming').length > 0,
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

    def test_members_of_different_widths_still_sit_against_each_other(self):
        """**A grid of equal cells is only tidy while the windows are equal.** Laying every member out
        on the widest one's pitch left a hole beside each narrower one, so growing a member and
        shrinking it back left the far window hanging at a distance, still in the group (user,
        2026-09-14). A row is packed by the widths the windows actually have."""
        r = self.bench("""
          put('g1', 40, 40, 180, 160); put('g2', 260, 40, 380, 160); put('g3', 700, 40, 200, 160);
          P.joinGroups(by('g1').id, by('g2').id);
          P.joinGroups(by('g3').id, by('g1').id);
          const ids = P.groupOf(by('g1').id);
          P.settle(by('g1').id);
          const row = ids.map((id) => [L[id].x, L[id].y, L[id].w]).sort((a, b) => a[0] - b[0]);
          return {row: row, room: document.querySelector('.cv-scroll').clientWidth};
        """)
        row = r["row"]
        self.assertEqual(len({p[1] for p in row}), 1, "they did not stay on one row: %r" % row)
        for a, b in zip(row, row[1:]):
            self.assertEqual(b[0] - (a[0] + a[2]), 12,
                             "a hole opened between two members: %r" % row)

    def test_a_hand_is_not_a_clamp(self):
        """**The hold asked for stillness the hand cannot give.** Any pointer movement over six pixels
        reset the gauge, so on a real mouse it flickered near zero and never filled: no gauge, no
        preview, and grouping that seemed to want one exact pixel (user, 2026-09-14, on Windows —
        every earlier test sent the same coordinate twice and so never moved at all). What the gesture
        was asked for is "게이지가 오르며 그룹핑 할 곳에서 멀어지는 순간 초기화" — it is *leaving*
        that resets it, not moving. Held over the target, jittering the way a hand does, it fills."""
        self.bench("put('g1',60,60,240,200); put('g2',360,60,240,200); put('g3',60,400,240,200); return 1;")
        x, y = self.press("g1")
        tx, ty = self.press("g2")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + (tx - x) * i / 3, y=y + (ty - y) * i / 3, buttons=1)
        jitter = [(0, 0), (9, -7), (-8, 6), (11, 4), (-6, -9), (7, 8), (-10, 3)]
        best, n = 0, 1
        for k in range(22):
            time.sleep(0.12)
            dx, dy = jitter[k % len(jitter)]
            self.send(type="mouseMoved", x=tx + dx, y=ty + dy, buttons=1)
            st = self.b.ev("""(()=>{const e=document.querySelector('.tile.arming');
              const P=window.palmar, by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
              return {pct: e ? Number(e.style.getPropertyValue('--p')) : 0,
                      n: P.groupOf(by('g1').id).length};})()""")
            best = max(best, st["pct"])
            n = st["n"]
        self.send(type="mouseReleased", x=tx, y=ty, clickCount=1, buttons=0)
        time.sleep(0.5)
        n = self.b.ev("""(()=>{const P=window.palmar;
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return P.groupOf(by('g1').id).length;})()""")
        self.assertEqual(best, 100, "a jittering hand never filled the gauge: reached %s%%" % best)
        self.assertEqual(n, 2, "it never grouped, because the hand was not still enough")

    def spawn(self, name):
        """One extra window, so a test may close it without taking the class's fixtures with it."""
        self.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          await fetch('/api/sessions?token='+T,{method:'POST',
            headers:{'content-type':'application/json'},
            body:JSON.stringify({cwd:%s,name:%s})});})()""" % (json.dumps(self.d.home), json.dumps(name)))
        for _ in range(40):
            time.sleep(0.25)
            if self.b.ev("""[...window.palmar.tiles.values()].some(t=>t.s.name===%s)""" % json.dumps(name)):
                return
        self.fail("the extra window never arrived")

    def close(self, name):
        self.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          const t=[...window.palmar.tiles.values()].find(t=>t.s.name===%s);
          await fetch('/api/sessions/'+t.id+'?token='+T,{method:'DELETE'});})()""" % json.dumps(name))
        for _ in range(40):
            time.sleep(0.25)
            if not self.b.ev("""[...window.palmar.tiles.values()].some(t=>t.s.name===%s)""" % json.dumps(name)):
                return
        self.fail("the window never went away")

    def test_the_frame_is_back_after_a_reload(self):
        """The membership came back and the frame did not: paintGroups ran from the Tile constructor,
        before the tile was in `tiles`, so the second member of a restored pair could not see itself
        and drew nothing. Every browser but the one that made the group opened on it unframed."""
        self.bench("""put('g1', 40, 40, 240, 200); put('g2', 292, 40, 240, 200);
                      P.joinGroups(by('g1').id, by('g2').id); P.saveLayout(); return 1;""")
        time.sleep(0.6)                                # the save is debounced, and it has to land first
        self.b.ev("location.reload()")
        for _ in range(40):
            time.sleep(0.25)
            if self.b.ev("!!(window.palmar && [...window.palmar.tiles.values()].some(t=>t.s.name==='g2'))"):
                break
        time.sleep(0.5)
        r = self.b.ev("""(()=>{const P=window.palmar;
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return {n: P.groupOf(by('g1').id).length, boxes: document.querySelectorAll('.gbox').length};})()""")
        self.assertEqual(r["n"], 2, "the group did not survive the reload")
        self.assertEqual(r["boxes"], 1, "the group came back without its frame")

    def test_the_landing_box_fills_toward_the_far_edge(self):
        """**grow, the default.** The landing box fills from the edge against the target towards the
        far one, so where and how long are one picture ("예측 지점으로 사라락 확장되는 느낌",
        2026-09-15). It carries the side it fills from and the percentage, and at 100 it goes solid —
        let go and it joins. The ring is still there for whoever prefers it, chosen in the panel."""
        self.assertEqual(self.b.ev("window.palmar.holdStyle()"), "grow")
        self.assertTrue(self.b.ev("document.body.classList.contains('hold-grow')"))
        self.bench("put('g1',60,60,240,200); put('g2',420,60,240,200); put('g3',60,400,240,200); return 1;")
        x, y = self.press("g1")
        tx, ty = self.press("g2")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + (tx - x) * i / 3, y=y + (ty - y) * i / 3, buttons=1)
        seen = []
        for _ in range(26):
            time.sleep(0.1)
            self.send(type="mouseMoved", x=tx + 1, y=ty, buttons=1)
            g = self.b.ev("""(()=>{const g=document.querySelector('.ghost'); if (!g) return null;
              return {side: g.dataset.side, p: Number(g.style.getPropertyValue('--p')),
                      full: g.classList.contains('full'),
                      fill: getComputedStyle(g, '::before').display};})()""")
            if g:
                seen.append(g)
                if g["full"]:
                    break
        self.send(type="mouseReleased", x=tx + 1, y=ty, clickCount=1, buttons=0)
        time.sleep(0.4)
        self.assertTrue(seen, "no landing box ever showed")
        self.assertTrue(all(g["side"] == seen[0]["side"] for g in seen), "the side it fills from flickered: %r" % seen)
        self.assertEqual(seen[0]["fill"], "block", "the fill layer is not shown under the default style")
        self.assertTrue(seen[-1]["full"] and seen[-1]["p"] == 100, "it never filled up: %r" % seen[-1])
        self.assertLess(seen[0]["p"], seen[-1]["p"], "it did not fill over time: %r" % seen)

    def test_the_hold_style_is_the_persons_to_choose(self):
        r = self.b.ev("""(()=>{const s=document.getElementById('holdstyle'); s.value='ring';
          s.dispatchEvent(new Event('change'));
          return {style: window.palmar.holdStyle(), ring: document.body.classList.contains('hold-ring'),
                  grow: document.body.classList.contains('hold-grow'), kept: localStorage.getItem('palmar.hold')};})()""")
        self.addCleanup(lambda: self.b.ev("""(()=>{const s=document.getElementById('holdstyle'); s.value='grow';
          s.dispatchEvent(new Event('change')); return 1;})()"""))
        self.assertEqual([r["style"], r["ring"], r["grow"], r["kept"]], ["ring", True, False, "ring"])

    def test_a_still_hand_still_counts(self):
        """**A hold that only advances while you move is not a hold.** The whole block ran on
        pointermove, so the one gesture it exists for — putting a window down on another and keeping it
        there — froze the gauge at whatever it had reached, and the stiller the hand the less it
        filled. That is the other half of "게이지가 시간에 따라 올라가길 바랬는데" (2026-09-14): with no
        events arriving, elapsed time was never read. A clock drives it now. Not one pointer event is
        sent after it arrives."""
        self.bench("put('g1',60,60,240,200); put('g2',360,60,240,200); put('g3',60,400,240,200); return 1;")
        x, y = self.press("g1")
        tx, ty = self.press("g2")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + (tx - x) * i / 3, y=y + (ty - y) * i / 3, buttons=1)
        best = 0
        for _ in range(26):                       # nothing is sent in here: the hand has stopped
            time.sleep(0.12)
            best = max(best, self.b.ev("""(()=>{const e=document.querySelector('.tile.arming');
              return e ? Number(e.style.getPropertyValue('--p')) : 0;})()"""))
            if best >= 100:
                break
        self.send(type="mouseReleased", x=tx, y=ty, clickCount=1, buttons=0)
        time.sleep(0.5)
        n = self.b.ev("""(()=>{const P=window.palmar;
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return P.groupOf(by('g1').id).length;})()""")
        self.assertEqual(best, 100, "the gauge stopped when the hand did: reached %s%%" % best)
        self.assertEqual(n, 2, "a window held still on another never grouped")

    def test_a_frame_belongs_to_the_canvas_that_is_showing(self):
        """**The frames are drawn into the scroller, not into a canvas**, and .tile.other only hides the
        windows — so one canvas's group shape stayed on screen over the next canvas's windows, which is
        how the residue was reported as following the user around (2026-09-14: "다른 캔버스에도 그
        잔상이 남아 있는 경우가 많았어")."""
        self.bench("""put('g1', 60, 60, 240, 200); put('g2', 320, 60, 240, 200);
                      P.joinGroups(by('g1').id, by('g2').id); return 1;""")
        here = self.b.ev("document.querySelectorAll('.gbox').length")
        other = self.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          const r = await fetch('/api/canvases?token='+T,{method:'POST',
            headers:{'content-type':'application/json'},
            body:JSON.stringify({name:'second'})});
          const c = await r.json();
          window.palmar.switchCanvas(c.id);
          return c.id;})()""")
        time.sleep(0.6)
        away = self.b.ev("document.querySelectorAll('.gbox').length")
        self.b.ev("""(()=>{const P=window.palmar;
          const first=[...P.tiles.values()].find(t=>t.s.name==='g1').s.canvas;
          P.switchCanvas(first); return 1;})()""")
        time.sleep(0.6)
        back = self.b.ev("document.querySelectorAll('.gbox').length")
        self.assertEqual(here, 1, "the pair never drew a frame, so this proves nothing")
        self.assertEqual(away, 0, "the frame followed onto a canvas with none of its windows on it")
        self.assertEqual(back, 1, "the frame did not come back with its windows")

    def test_a_resized_group_closes_back_up(self):
        """Grow a member and the others make room; shrink it back and they have to come back. Driven
        through the grip, because the bug only existed in the release path: the group was arranged
        correctly and then persist() read the positions back off elements that were still sliding, and
        saved where they had been (user, 2026-09-14: "크기를 늘렸다가 줄이면 터미널끼리 붕떠있어")."""
        self.bench("""put('g1',40,40,220,180); put('g2',272,40,220,180); put('g3',504,40,220,180);
                      P.joinGroups(by('g1').id, by('g2').id);
                      P.joinGroups(by('g3').id, by('g1').id);
                      P.compactGroup(P.groupOf(by('g1').id)); return 1;""")
        row = """const ids = P.groupOf(by('g1').id);
                 const r = ids.map((id) => [L[id].x, L[id].y, L[id].w]).sort((a, b) => a[0] - b[0]);
                 const gaps = []; for (let i=1;i<r.length;i++)
                   gaps.push(r[i][1] === r[i-1][1] ? r[i][0] - (r[i-1][0] + r[i-1][2]) : 'wrapped');
                 return {row: r, gaps: gaps};"""
        def grip_drag(dx):
            g = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name==='g1');
              const r=t.el.querySelector('.grip').getBoundingClientRect();
              return {x:r.left+r.width/2, y:r.top+r.height/2};})()""")
            self.send(type="mousePressed", x=g["x"], y=g["y"], clickCount=1, buttons=1)
            for i in (1, 2, 3, 4):
                self.send(type="mouseMoved", x=g["x"] + dx * i / 4, y=g["y"], buttons=1)
                time.sleep(0.04)
            self.send(type="mouseReleased", x=g["x"] + dx, y=g["y"], clickCount=1, buttons=0)
            time.sleep(1.1)
        start = self.bench(row)
        grip_drag(200)
        grown = self.bench(row)
        grip_drag(-200)
        back = self.bench(row)
        self.assertEqual(start["gaps"], [12, 12], "the bench did not start tidy: %r" % start)
        self.assertGreater(grown["row"][0][2], 220, "the resize never happened: %r" % grown)
        self.assertEqual(grown["gaps"], [12, 12], "growing one left the group spread out: %r" % grown)
        self.assertEqual(back["gaps"], [12, 12], "shrinking it back left them floating: %r" % back)

    def test_closing_a_member_takes_the_frame_with_it(self):
        """**The frame outlived every window that explained it.** Closing a window dropped its entry
        from the store and nothing redrew the frames, so the coloured shape stayed on screen — and
        because it lives in the scroller rather than on a canvas, it showed on every other canvas too,
        and survived closing the rest of the board (user, 2026-09-14: "나머지 터미널을 다 지워도
        잔상이 남아"). A pair that loses one member also stops being a group."""
        self.spawn('gx')
        self.bench("""put('g1', 60, 60, 240, 200); put('gx', 320, 60, 240, 200);
                      P.joinGroups(by('g1').id, by('gx').id); return 1;""")
        before = self.b.ev("document.querySelectorAll('.gbox').length")
        self.close('gx')
        after = self.b.ev("""(()=>{const P=window.palmar;
          const by=(n)=>[...P.tiles.values()].find(t=>t.s.name===n);
          return {boxes: document.querySelectorAll('.gbox').length,
                  cells: document.querySelectorAll('.gcell').length,
                  marked: document.querySelectorAll('.tile.grouped').length,
                  still: P.groupOf(by('g1').id).length};})()""")
        self.assertEqual(before, 1, "the pair never drew a frame, so this proves nothing")
        self.assertEqual([after["boxes"], after["cells"]], [0, 0],
                         "the frame outlived the group: %r" % after)
        self.assertEqual(after["still"], 1, "the one left behind is still in a group of one")
        self.assertEqual(after["marked"], 0, "a window is still wearing the group mark")

    def test_a_group_closes_up_when_a_member_goes(self):
        """"그룹화된 터미널 안에서는 하나의 터미널의 크기가 줄어들거나 사라져도 같은 그룹끼리는 항상
        맞닿아 있게" (2026-09-14) — a group that keeps a hole where a window used to be is a group
        that has come apart."""
        self.spawn('gx')
        self.bench("""put('g1', 60, 60, 200, 160); put('gx', 272, 60, 200, 160);
                      put('g2', 484, 60, 200, 160);
                      P.joinGroups(by('g1').id, by('gx').id);
                      P.joinGroups(by('g2').id, by('g1').id);
                      P.compactGroup(P.groupOf(by('g1').id)); return 1;""")
        self.close('gx')
        r = self.bench("""const ids = P.groupOf(by('g1').id);
          const row = ids.map((id) => [L[id].x, L[id].y, L[id].w]).sort((a, b) => a[0] - b[0]);
          return {n: ids.length, row: row};""")
        self.assertEqual(r["n"], 2, "the group did not survive losing one member: %r" % r)
        a, b = r["row"]
        self.assertEqual(b[0] - (a[0] + a[2]), 12,
                         "the group kept the hole where the closed window had been: %r" % r["row"])

    def test_brushing_a_window_is_not_aiming_at_it(self):
        """**Two thresholds, not one.** Any overlap at all used to count, so at a window's edge the
        answer flickered between that window and nothing as the hand moved, and every flicker restarted
        the hold — which made the gauge look as though it rose with how deeply the windows overlapped
        rather than with time (user, 2026-09-14). A fifth of the dragged window has to be covered before
        it counts as aiming, and once aimed it is held until almost nothing is left."""
        r = self.bench("""
          const a = by('g1').id;
          put('g2', 360, 300, 240, 200);
          const look = (x, y, cur) => { put('g1', x, y, 240, 200);
                                        const h = P.paneOver(a, [a], cur); return h && h.id; };
          const b = by('g2').id;
          return {brush: look(588, 300, null),      // 12px of 240 — a brush
                  aimed: look(420, 300, null),      // 180px of 240 — plainly on it
                  kept:  look(570, 300, b) === b,   // slid back too far to take up, but already held
                  gone:  look(598, 300, b)};        // and now there is nothing left to hold
        """)
        self.assertIsNone(r["brush"], "a brush past counted as aiming at it")
        self.assertIsNotNone(r["aimed"], "plainly over it and it found nothing")
        self.assertTrue(r["kept"], "it dropped a target it was already holding")
        self.assertIsNone(r["gone"], "it held a target that is no longer under it")

    def test_a_member_wears_its_group_colour(self):
        """**The tint has to be on the window itself.** .tile.grouped reads --group, and --group was set
        on the frame — a sibling of the windows, not an ancestor — so the declaration was invalid and
        the border fell back to currentColor. It did change colour on joining, which is why it read as
        working; it was never the group's colour."""
        r = self.bench("""
          put('g1', 60, 60, 240, 200); put('g2', 320, 60, 240, 200);
          const lone = getComputedStyle(by('g3').el).borderTopColor;
          P.joinGroups(by('g1').id, by('g2').id);
          P.paintGroups ? P.paintGroups() : null;
          const a = by('g1').el, b = by('g2').el;
          return {set: a.style.getPropertyValue('--group'),
                  same: a.style.getPropertyValue('--group') === b.style.getPropertyValue('--group'),
                  border: getComputedStyle(a).borderTopColor, lone: lone,
                  text: getComputedStyle(a).color};
        """)
        self.assertTrue(r["set"], "no group colour was put on the window")
        self.assertTrue(r["same"], "two members of one group wear different colours")
        self.assertNotEqual(r["border"], r["lone"], "a member's border is the ordinary one")
        self.assertNotEqual(r["border"], r["text"],
                            "the border fell back to the text colour, so the mix is still invalid")

    def test_a_big_window_can_still_aim_at_a_small_one(self):
        """**The share is of the smaller of the two.** Read against the dragged window alone, a big
        window could never take a small one as a target at all — a default-sized pane would need more
        overlap than a minimum-sized window has area to give, so it would sit squarely on top of one and
        find nothing. Every other test here uses two windows of one size, where the two readings are the
        same number and the mistake is invisible."""
        r = self.bench("""
          const a = by('g1').id;
          put('g2', 400, 300, 230, 130);          // small
          put('g1', 380, 280, 520, 360);          // big, laid over most of it
          const on = P.paneOver(a, [a], null);
          put('g1', 610, 280, 520, 360);          // barely touching its right edge
          const off = P.paneOver(a, [a], null);
          return {on: on && on.id, off: off, small: L[by('g2').id].w * L[by('g2').id].h,
                  big: L[a].w * L[a].h};
        """)
        self.assertLess(r["small"], r["big"] * 0.2,
                        "the two windows are not lopsided enough to prove anything: %r" % r)
        self.assertIsNotNone(r["on"], "a big window laid over a small one found nothing")
        self.assertIsNone(r["off"], "it took a target it was only touching the edge of")

    def test_nothing_starts_until_the_quiet_moment_passes(self):
        """**Carrying one window across another must offer nothing.** The gauge and the landing box
        both wait out a beat first, so only staying starts them (user, 2026-09-14: "겹쳐진 후 일정시간이
        지나고 나서 게이지가 올라가야 해"). The target still lights up at once — that is what says the
        hold has something to hold on to."""
        self.bench("put('g1',60,60,240,200); put('g2',360,60,240,200); put('g3',60,400,240,200); return 1;")
        x, y = self.press("g1")
        tx, ty = self.press("g2")
        # **Timed inside the page.** Asking from here costs a round trip, and under a loaded machine
        # that trip alone outlasts the lead-in — the first answer then arrives after the gauge has
        # already started and the test reads as a failure. The page keeps its own timeline instead.
        self.b.ev("""(()=>{window.__t=[];window.__i=setInterval(()=>{
            const a=document.querySelector('.tile.arming');
            window.__t.push([performance.now(), !!a, !!document.querySelector('.ghost'),
                             !!document.querySelector('.tile.joining, .tile.joinready'),
                             a ? Number(a.style.getPropertyValue('--p')) : 0]);}, 20);return 1;})()""")
        stale = self.b.ev("document.querySelectorAll('.tile.joinready, .tile.joining, .tile.arming').length")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + (tx - x) * i / 3, y=y + (ty - y) * i / 3, buttons=1)
        self.send(type="mouseMoved", x=tx + 1, y=ty, buttons=1)
        for _ in range(26):
            time.sleep(0.12)
            if self.b.ev("!!document.querySelector('.tile.joinready')"):
                break
        self.send(type="mouseReleased", x=tx + 1, y=ty, clickCount=1, buttons=0)
        line = self.b.ev("(()=>{clearInterval(window.__i);return window.__t;})()")
        first = lambda k: next((r[0] for r in line if r[k]), None)
        lit, gauge, ghost = first(3), first(1), first(2)
        digest = "%d samples over %dms, %d lit, top gauge %s, joinready at start: %s" % (
            len(line), (line[-1][0] - line[0][0]) if line else 0,
            sum(1 for r in line if r[3]), max([r[4] for r in line] or [0]), stale)
        self.assertIsNotNone(lit, "the target never lit up at all — " + digest)
        self.assertIsNotNone(gauge, "the gauge never started at all — " + digest)
        self.assertIsNotNone(ghost, "the landing box never appeared")
        self.assertGreaterEqual(gauge - lit, 200,
                                "the gauge started %dms after they touched, with no quiet moment"
                                % (gauge - lit))
        self.assertGreaterEqual(ghost, gauge - 40,
                                "the landing box appeared before the gauge did")

    def test_it_lands_where_the_preview_said_it_would(self):
        """**The preview was telling the truth and something else undid it.** arrangeGroup writes the
        store directly; persist() reads the position back off the element, and a tile slides for 350ms,
        so persisting right after arranging saved a number from the middle of that slide — the old
        position (user, 2026-09-14: "예상 범위가 보이지만 실제로는 그 부분에 붙질 않아"). applyPush
        already carried the rule: write the intended value, never read it back."""
        self.bench("put('g1',60,60,240,200); put('g2',420,60,240,200); put('g3',60,420,240,200); return 1;")
        x, y = self.press("g1")
        tx, ty = self.press("g2")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + (tx - x) * i / 3, y=y + (ty - y) * i / 3, buttons=1)
        box = None
        for _ in range(26):
            time.sleep(0.12)
            self.send(type="mouseMoved", x=tx + 1, y=ty, buttons=1)
            box = self.b.ev("""(()=>{const g=document.querySelector('.ghost');
              if (!g) return null; const s=g.style;
              return [parseInt(s.left), parseInt(s.top)];})()""")
            if box and self.b.ev("!!document.querySelector('.tile.joinready')"):
                break
        self.assertIsNotNone(box, "no landing box was ever shown")
        self.send(type="mouseReleased", x=tx + 1, y=ty, clickCount=1, buttons=0)
        time.sleep(0.8)
        at = self.bench("return at('g1');")
        self.assertEqual(at, box, "it was shown %r and landed at %r" % (box, at))

    def test_the_notch_of_an_L_belongs_to_nobody(self):
        """**The outline is not the shape.** The frame draws an ㄱ, and the corner it leaves open is
        real space — but the push took the group's bounding box, so a window put in that corner was
        thrown straight back out (user, 2026-09-14: "ㄱ자 모양 그룹화 했을 때 남는 공간에 다른
        터미널 놓이는거 같다가도 ... 또 그 공간은 그룹의 공간이 되어서 다른 터미널을 놓을 수가
        없어"). Collision asks the windows now, not the box around them."""
        r = self.bench("""
          put('g1', 40, 40, 400, 160);     // the top bar of the ㄱ
          put('g2', 40, 212, 180, 160);    // the leg, down the left
          P.joinGroups(by('g1').id, by('g2').id);
          put('g3', 244, 212, 180, 160);   // the open corner: inside the box, touching no window
          const before = at('g3');
          const box = P.groupRect(P.groupOf(by('g1').id));
          const moves = P.pushAside(by('g3').s.canvas, by('g3').id);
          return {moves: moves.length, before: before, after: at('g3'), box: box,
                  a: at('g1'), b: at('g2')};
        """)
        box, g3 = r["box"], r["before"]
        self.assertTrue(box["x"] <= g3[0] and g3[0] + 180 <= box["x"] + box["w"]
                        and box["y"] <= g3[1] and g3[1] + 160 <= box["y"] + box["h"],
                        "the window is not in the notch, so this proves nothing: %r in %r" % (g3, box))
        self.assertEqual(r["moves"], 0, "the group claimed its own empty corner: %r" % (r,))
        self.assertEqual(r["after"], r["before"], "it was pushed out of the corner")

    def test_the_side_you_drop_on_is_where_it_lands(self):
        """**Any edge, not just the top one.** It used to hit-test the pointer, which during a drag is
        always on the dragged window's own title bar, so the only way to reach another window was to
        put that bar on top of it and every group stacked upwards (user, 2026-09-14: "위쪽 테두리에
        놔야만 하니까 그룹이 위로만 쌓임"). Overlap of the whole rectangle finds the target from any
        side, and the centres say which side it came from."""
        r = self.bench("""
          const a = by('g1').id;
          put('g2', 360, 300, 240, 200);          // the target, in the middle
          const side = (x, y) => { put('g1', x, y, 240, 200);
                                   const h = P.paneOver(a, [a]); return h && h.side; };
          return {right: side(500, 300), left: side(220, 300),
                  below: side(360, 440), above: side(360, 160),
                  clear: side(900, 300)};
        """)
        self.assertEqual([r["right"], r["left"], r["below"], r["above"]],
                         ["right", "left", "below", "above"])
        self.assertIsNone(r["clear"], "it found a target it is nowhere near")

    def test_shrinking_a_member_closes_the_gap_again(self):
        """**A group holds its own shape.** Growing one member pushes the rest along; shrinking it
        back used to leave the hole where it had been, because the push knows how to make room and
        nothing knew how to take it back (user, 2026-09-14: "크기를 다시 줄이면 그룹 안에서 빈공간이
        발생함"). Laying the group out again on every resize is what closes it."""
        r = self.bench("""
          put('g1', 40, 40, 240, 200); put('g2', 400, 40, 240, 200);
          P.joinGroups(by('g1').id, by('g2').id);
          const g = () => P.groupOf(by('g1').id);
          P.settle(by('g1').id);                            // push what overlaps, then close up
          const tight = at('g2')[0] - (at('g1')[0] + L[by('g1').id].w);
          put('g1', at('g1')[0], at('g1')[1], 400, 200);   // grow it
          P.settle(by('g1').id);
          const grown = at('g2')[0] - (at('g1')[0] + L[by('g1').id].w);
          put('g1', at('g1')[0], at('g1')[1], 240, 200);   // and back
          P.settle(by('g1').id);
          return {tight: tight, grown: grown,
                  back: at('g2')[0] - (at('g1')[0] + L[by('g1').id].w)};
        """)
        self.assertEqual(r["grown"], r["tight"], "growing it did not keep the group tight")
        self.assertEqual(r["back"], r["tight"],
                         "shrinking it left a hole: %spx instead of %spx" % (r["back"], r["tight"]))

    def test_the_gauge_fills_on_the_window_in_your_hand(self):
        """**You can see how long is left.** A hold with no gauge is a window that does nothing for
        most of a second and then surprises you (user, 2026-09-14: "몇초동안 잡고 있어야하는지가
        안보임 ... 쥐고 있는 터미널 테두리를 타고 게이지 차는 듯한 효과"). It rides the dragged
        window's own border, with a faint box showing where it would land, and moving off resets
        both — one drag, sampled on the way."""
        # **The dial itself, off the clock.** Under load a round trip can outlast the whole hold, so
        # whether it passes through the middle is asked of the gauge directly; the hand below proves
        # it is wired to the right window and comes down again.
        dial = self.bench("""
          const id = by('g1').id, read = () => { const e = by('g1').el;
            return e.classList.contains('arming') ? Number(e.style.getPropertyValue('--p')) : null; };
          const out = [];
          for (const p of [0, 1, 45, 99, 100]) { P.setGauge(id, p); out.push(read()); }
          P.setGauge(id, 0);
          return {steps: out, off: read()};
        """)
        self.assertEqual(dial["steps"], [None, 1, 45, 99, 100], "the gauge does not track the hold")
        self.assertIsNone(dial["off"], "the gauge does not come off")

        self.bench("put('g1',60,60,240,200); put('g2',360,60,240,200); put('g3',60,400,240,200); return 1;")
        x, y = self.press("g1")
        tx, ty = self.press("g2")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + (tx - x) * i / 3, y=y + (ty - y) * i / 3, buttons=1)
        look = """(()=>{const e=document.querySelector('.tile.arming');
          return {pct: e ? Number(e.style.getPropertyValue('--p')) : null,
                  ghost: !!document.querySelector('.ghost'),
                  mine: e ? e.querySelector('.tb .name').textContent : null};})()"""
        seen = []
        for _ in range(26):        # the quiet lead-in comes first, then the 900ms fill
            time.sleep(0.12)
            self.send(type="mouseMoved", x=tx + 1, y=ty, buttons=1)
            seen.append(self.b.ev(look))
        filling = [s for s in seen if s["pct"] is not None]
        self.assertTrue(filling, "no gauge showed at all while holding")
        self.assertEqual(filling[-1]["mine"], "g1", "the gauge is not on the window in the hand")
        self.assertEqual(filling[-1]["pct"], 100, "the gauge never filled: %r" % seen)
        self.assertTrue(any(s["ghost"] for s in seen), "nothing showed where it would land")
        # Off to an empty corner: the gauge and the preview both have to go.
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=tx, y=ty + 300 * i / 3, buttons=1)
        time.sleep(0.2)
        away = self.b.ev(look)
        self.send(type="mouseReleased", x=tx, y=ty + 300, clickCount=1, buttons=0)
        time.sleep(0.4)
        self.assertEqual([away["pct"], away["ghost"]], [None, False],
                         "the gauge stayed up after the hand moved on: %r" % away)

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

    def test_taking_one_out_of_the_middle_closes_the_hole(self):
        """Closing the middle window closed the group up; taking it out with Alt-drag left its hole
        behind (user, 2026-09-15). Both are "a member is gone" and both close up now."""
        self.bench("""put('g1',40,40,200,160); put('g2',252,40,200,160); put('g3',464,40,200,160);
                      P.joinGroups(by('g1').id, by('g2').id); P.joinGroups(by('g3').id, by('g1').id);
                      P.compactGroup(P.groupOf(by('g1').id)); return 1;""")
        x, y = self.press("g2")
        mods = dict(button="left", modifiers=1)                         # 1 = Alt
        self.b.ws.call("Input.dispatchMouseEvent", dict(mods, type="mousePressed", x=x, y=y, clickCount=1, buttons=1))
        for i in (1, 2, 3):
            self.b.ws.call("Input.dispatchMouseEvent", dict(mods, type="mouseMoved", x=x, y=y + 320 * i / 3, buttons=1))
        self.b.ws.call("Input.dispatchMouseEvent", dict(mods, type="mouseReleased", x=x, y=y + 320, clickCount=1, buttons=0))
        time.sleep(0.6)
        r = self.bench("return {n: P.groupOf(by('g1').id).length, a: at('g1'), c: at('g3'), out: P.groupOf(by('g2').id).length};")
        self.assertEqual([r["n"], r["out"]], [2, 1], "it did not leave the group: %r" % r)
        self.assertEqual(r["c"], [40 + 200 + 12, 40], "the hole it left was not closed: %r" % r)

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
        for _ in range(20):
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

    def test_grouping_pulls_them_together(self):
        """A group that leaves everyone where they were is a colour, not a group. Sizes are the
        user's, so members are lined up rather than resized (AGENTS.md). Closing up is gravity: each
        member goes **up, then left**, until it touches another or the group's own edge — so two windows
        far apart end up side by side, the second against the first (2026-09-15)."""
        r = self.bench("""
          put('g1', 40, 40, 200, 160);
          put('g2', 700, 380, 200, 160);   // far apart and out of line
          P.joinGroups(by('g1').id, by('g2').id);
          P.compactGroup(P.groupOf(by('g1').id));
          return {a: at('g1'), b: at('g2')};
        """)
        self.assertEqual(r["a"], [40, 40], "the first one moved: %r" % (r["a"],))
        self.assertEqual(r["b"], [40 + 200 + 12, 40], "the second is not against the first: %r" % (r["b"],))

    def test_closing_up_goes_up_before_left(self):
        """**Gravity, not a grid.** Three arrangers came before: by age, wide-first rows, rows as the
        hand left them — and each re-laid the group out after a drop or a resize and put a window
        somewhere nobody had shown. Closing up now only pulls: up as far as it goes, then left, against
        whatever it touches. Here the lone window above stays; the one that has nothing above it rises
        to sit beside it; the one under the first stays under it."""
        r = self.bench("""
          put('g1', 30, 30, 240, 190); put('g2', 600, 320, 240, 190); put('g3', 30, 320, 240, 190);
          P.joinGroups(by('g1').id, by('g2').id);
          P.joinGroups(by('g3').id, by('g1').id);
          P.compactGroup(P.groupOf(by('g1').id));
          return {a: at('g1'), b: at('g2'), c: at('g3')};
        """)
        self.assertEqual(r["a"], [30, 30], "the top-left one moved: %r" % r)
        self.assertEqual(r["b"], [30 + 240 + 12, 30], "the free one did not rise to sit beside it: %r" % r)
        self.assertEqual(r["c"], [30, 30 + 190 + 12], "the one underneath is not against the first: %r" % r)

    def test_it_lands_on_the_side_it_was_carried_to(self):
        """Every side, exactly where the preview said. dropInto is what the release calls: it puts the
        window at the previewed spot, joins, and pushes only what that displaces — nothing re-lays the
        group out afterwards, which is what used to move it (user, 2026-09-15)."""
        r = self.bench("""
          const a = by('g1').id, b = by('g2').id, out = {};
          for (const side of ['right', 'left', 'below', 'above']) {
            put('g2', 400, 300, 240, 200);
            put('g1', 60, 60, 240, 200);
            const spot = P.joinPreview(b, side, a);
            P.dropInto(a, b, side);
            out[side] = {spot: [spot.x, spot.y], me: at('g1'), target: at('g2')};
            P.leaveGroup(a);
          }
          return out;
        """)
        for side, v in r.items():
            self.assertEqual(v["me"], v["spot"], "%s: shown at %r, landed at %r" % (side, v["spot"], v["me"]))
            self.assertEqual(v["target"], [400, 300], "%s: the target moved to %r" % (side, v["target"]))

    def test_under_the_right_one_means_under_the_right_one(self):
        """Two grouped side by side; a third carried under the **right** one lands under the right one
        (user, 2026-09-15: it used to land under the left, then a row's height too low)."""
        r = self.bench("""
          put('g1', 40, 40, 240, 200); put('g2', 292, 40, 240, 200);
          P.joinGroups(by('g1').id, by('g2').id);
          put('g3', 60, 500, 240, 200);
          const spot = P.joinPreview(by('g2').id, 'below', by('g3').id);
          P.dropInto(by('g3').id, by('g2').id, 'below');
          return {spot: [spot.x, spot.y], c: at('g3'), a: at('g1'), b: at('g2')};
        """)
        self.assertEqual(r["c"], r["spot"], "shown at %r, landed at %r" % (r["spot"], r["c"]))
        self.assertEqual([r["a"], r["b"]], [[40, 40], [292, 40]], "the pair moved: %r" % r)

    def test_under_a_shorter_neighbour_touches_that_neighbour(self):
        """The case that broke the rows: a tall window on the left, a shorter one on its right, and a
        third carried under the short one. A grid of rows put it a whole row down — under the tall
        one's bottom, floating clear of the short one it was aimed at (user, 2026-09-15: "왼쪽 터미널의
        대각선에 그룹핑이 돼서 붕 떠 있게 돼"). It lands touching the short one, and closing up
        afterwards leaves it there: up is blocked by the short one, left by the tall one."""
        r = self.bench("""
          put('g1', 40, 40, 240, 320);     // tall
          put('g2', 292, 40, 240, 160);    // short, to its right
          P.joinGroups(by('g1').id, by('g2').id);
          put('g3', 60, 600, 240, 160);
          const spot = P.joinPreview(by('g2').id, 'below', by('g3').id);
          P.dropInto(by('g3').id, by('g2').id, 'below');
          const landed = at('g3');
          P.compactGroup(P.groupOf(by('g1').id));    // what a later resize or close would run
          return {spot: [spot.x, spot.y], landed: landed, after: at('g3'), a: at('g1'), b: at('g2')};
        """)
        self.assertEqual(r["landed"], r["spot"], "shown at %r, landed at %r" % (r["spot"], r["landed"]))
        self.assertEqual(r["landed"], [292, 40 + 160 + 12], "it is not touching the short one: %r" % r)
        self.assertEqual(r["after"], r["landed"], "closing up moved it away from where it was put: %r" % r)
        self.assertEqual([r["a"], r["b"]], [[40, 40], [292, 40]], "the pair moved: %r" % r)

    def test_an_L_is_a_top_row_with_more_in_it(self):
        """Three 400px windows, one alone on top and two below, closed up: the free one on the bottom
        row rises to sit beside the top one, the one underneath stays put — an ㄱ, two on top."""
        r = self.bench("""
          put('g1', 30, 30, 400, 200); put('g2', 700, 300, 400, 200); put('g3', 30, 300, 400, 200);
          P.joinGroups(by('g1').id, by('g2').id);
          P.joinGroups(by('g3').id, by('g1').id);
          P.compactGroup(P.groupOf(by('g1').id));
          return {at: P.groupOf(by('g1').id).map((id)=>[P.layout()[id].x, P.layout()[id].y])};
        """)
        rows = sorted({p[1] for p in r["at"]})
        self.assertEqual(len(rows), 2, "not two rows: %r" % r["at"])
        top = sorted(p for p in r["at"] if p[1] == rows[0])
        self.assertEqual(len(top), 2, "the top row is not the pair: %r" % r["at"])
        self.assertEqual(top[1][0] - top[0][0], 400 + 12, "the top row is not packed: %r" % top)

    def test_growing_a_member_does_not_land_it_on_its_group_mates(self):
        """**A group is one block to the outside world**, so the ordinary push cannot see an overlap
        *between its own members* — and growing one is exactly what makes one. Reported and then
        measured (user, 2026-09-14): a member dragged out to 390px sat on top of the one beside it.
        The group is sorted out among itself first, then the canvas."""
        self.bench("""put('g1',40,40,240,180); put('g2',300,40,240,180); put('g3',40,260,240,180);
                      P.joinGroups(by('g1').id, by('g2').id);
                      P.joinGroups(by('g3').id, by('g1').id);
                      P.compactGroup(P.groupOf(by('g1').id)); return 1;""")
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
                      P.compactGroup(P.groupOf(by('g1').id)); return 1;""")
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


class OneBoardForEveryBrowser(unittest.TestCase):
    """③, decided 2026-09-14. Grouping in Safari and switching to Chrome landed on a board with no
    groups and every window somewhere else — the board was in `localStorage`, which is per browser.
    It is the daemon's now: a second browser is a fresh Chrome profile here, with nothing of its own,
    and it has to open on the first one's board and follow its moves without reloading."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.a = Browser().start()
        cls.a.open(cls.d.url)
        cls.a.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          for (const n of ['g1','g2'])
            await fetch('/api/sessions?token='+T,{method:'POST',
              headers:{'content-type':'application/json'},
              body:JSON.stringify({cwd:%s,name:n})});})()""" % json.dumps(cls.d.home))
        time.sleep(4)

    @classmethod
    def tearDownClass(cls):
        cls.a.stop()
        cls.d.stop()

    JS = Grouping.JS

    def bench(self, b, body):
        return b.ev("(()=>{" + self.JS + "\n" + body + "})()")

    def board(self):
        return self.d.get("/api/layout")["layout"]

    def wait_for(self, fn, what, secs=6):
        end = time.time() + secs
        while time.time() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.2)
        self.fail("gave up waiting for " + what)

    def test_the_daemon_is_told_where_things_are(self):
        self.assertTrue(self.a.ev("window.palmar.layoutOnDaemon()"), "the page did not find the endpoint")
        self.bench(self.a, "put('g1', 333, 222, 300, 240); P.saveLayout(); return 1;")
        g1 = self.a.ev("[...window.palmar.tiles.values()].find(t=>t.s.name==='g1').id")
        r = self.wait_for(lambda: (lambda L: L.get(g1) if L.get(g1, {}).get("x") == 333 else None)(self.board()),
                          "the daemon to hear about the move")
        self.assertEqual([r["x"], r["y"], r["w"], r["h"]], [333, 222, 300, 240])

    def test_a_second_browser_opens_on_the_same_board(self):
        self.bench(self.a, "put('g1', 480, 96, 280, 220); put('g2', 60, 400, 280, 220); P.saveLayout(); return 1;")
        g1 = self.a.ev("[...window.palmar.tiles.values()].find(t=>t.s.name==='g1').id")
        self.wait_for(lambda: self.board().get(g1, {}).get("x") == 480, "the save")
        b = Browser().start()
        self.addCleanup(b.stop)
        b.open(self.d.url)
        at = self.wait_for(lambda: self.bench(b, "const t = by('g1'); return t && at('g1')[0] === 480 ? at('g1') : null;"),
                           "the second browser to show the window where the first put it")
        self.assertEqual(at, [480, 96])

    def test_an_open_browser_follows_without_reloading(self):
        b = Browser().start()
        self.addCleanup(b.stop)
        b.open(self.d.url)
        self.wait_for(lambda: self.bench(b, "return by('g2') ? 1 : 0;"), "the second browser to open")
        self.bench(self.a, "put('g2', 700, 300, 260, 200); P.saveLayout(); return 1;")
        at = self.wait_for(lambda: self.bench(b, "return at('g2')[0] === 700 ? at('g2') : null;"),
                           "the second browser to follow the move")
        self.assertEqual(at, [700, 300])
        # The element slides there over 350ms — the store is written at once, the window arrives after.
        got = self.wait_for(lambda: self.bench(b, "const e = by('g2').el; return e.offsetLeft === 700 ? [e.offsetLeft, e.offsetTop] : null;"),
                            "the window itself to arrive", secs=3)
        self.assertEqual(got, [700, 300], "the store moved but the window did not")

    def test_a_group_is_a_group_everywhere(self):
        self.bench(self.a, """put('g1', 40, 40, 240, 200); put('g2', 292, 40, 240, 200);
                              P.joinGroups(by('g1').id, by('g2').id); P.saveLayout(); return 1;""")
        b = Browser().start()
        self.addCleanup(b.stop)
        b.open(self.d.url)
        n = self.wait_for(lambda: self.bench(b, "return by('g1') && P.groupOf(by('g1').id).length === 2 ? 2 : 0;"),
                          "the group to appear in the second browser")
        self.assertEqual(n, 2)
        self.assertEqual(b.ev("document.querySelectorAll('.gbox').length"), 1, "grouped in the store, unframed on screen")


class RenamingByHand(unittest.TestCase):
    """Double-click the name on a window's title bar and it becomes editable — the same gesture the
    canvas tab has. It did not: the title bar takes pointer capture on pointerdown, and a captured
    pointer's click and dblclick are retargeted to the capturing element, so a dblclick listener on
    the name itself never fired (user, 2026-09-15: "캔버스 이름에서는 가능한데, 터미널은 안 돼")."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          await fetch('/api/sessions?token='+T,{method:'POST',headers:{'content-type':'application/json'},
            body:JSON.stringify({cwd:%s,name:'one'})});})()""" % json.dumps(cls.d.home))
        time.sleep(3)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def send(self, **kw):
        self.b.ws.call("Input.dispatchMouseEvent", dict(button="left", **kw))

    def test_double_clicking_the_name_edits_it(self):
        pos = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()][0];
          const r=t.el.querySelector('.tb .name').getBoundingClientRect();
          return {x:r.left+r.width/2, y:r.top+r.height/2};})()""")
        self.send(type="mousePressed", x=pos["x"], y=pos["y"], clickCount=1, buttons=1)
        self.send(type="mouseReleased", x=pos["x"], y=pos["y"], clickCount=1, buttons=0)
        time.sleep(0.08)
        self.send(type="mousePressed", x=pos["x"], y=pos["y"], clickCount=2, buttons=1)
        self.send(type="mouseReleased", x=pos["x"], y=pos["y"], clickCount=2, buttons=0)
        time.sleep(0.3)
        st = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()][0]; const inp=t.el.querySelector('.tb input');
          return {input: !!inp, focused: !!inp && document.activeElement === inp};})()""")
        self.assertTrue(st["input"], "no editor appeared on the name")
        self.assertTrue(st["focused"], "the editor appeared but something took the focus back")
        # Type a name and confirm it: the daemon has to hear it, which is what makes it a rename.
        self.b.ws.call("Input.insertText", {"text": "two"})
        self.b.ws.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        self.b.ws.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13})
        for _ in range(20):
            time.sleep(0.2)
            if any(s["name"] == "two" for s in self.d.panes()):
                break
        self.assertIn("two", [s["name"] for s in self.d.panes()], "the daemon never heard the new name")

    def test_a_single_click_still_drags(self):
        """The name is where a window is grabbed. Catching the double-click must not cost the drag."""
        before = self.b.ev("(()=>{const t=[...window.palmar.tiles.values()][0]; return [t.el.offsetLeft, t.el.offsetTop];})()")
        pos = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()][0];
          const r=t.el.querySelector('.tb .name').getBoundingClientRect();
          return {x:r.left+r.width/2, y:r.top+r.height/2};})()""")
        self.send(type="mousePressed", x=pos["x"], y=pos["y"], clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=pos["x"] + 40 * i, y=pos["y"] + 30 * i, buttons=1)
        self.send(type="mouseReleased", x=pos["x"] + 120, y=pos["y"] + 90, clickCount=1, buttons=0)
        time.sleep(0.6)
        after = self.b.ev("(()=>{const t=[...window.palmar.tiles.values()][0]; return [t.el.offsetLeft, t.el.offsetTop];})()")
        self.assertEqual([after[0] - before[0], after[1] - before[1]], [120, 90], "the drag by the name broke")
        self.assertFalse(self.b.ev("!![...window.palmar.tiles.values()][0].el.querySelector('.tb input')"),
                         "a single grab opened the editor")


class Palettes(unittest.TestCase):
    """Eight palettes, chosen one per side; the attribute carries only the one in effect, so a light
    choice never leaks into the dark theme and the other way round (2026-09-15: "테마 다 좋은데,
    다 적용해 줄 수 있나")."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def accent(self):
        return self.b.ev("getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()")

    def test_each_side_keeps_its_own_choice(self):
        r = self.b.ev("""(()=>{const P=window.palmar, root=document.documentElement;
          const theme=(m)=>{ if (m) root.dataset.theme=m; else delete root.dataset.theme;
                             document.querySelector('[data-mode]').click(); };   // cycle to re-run applyTheme
          const out = {};
          root.dataset.theme = 'dark'; document.querySelector('[data-mode]').click();   // → system
          document.querySelector('[data-mode]').click();                                // → light
          P.choosePalette('light', 'sky'); P.choosePalette('dark', 'graphite');
          out.lightPal = P.palette(); out.lightAccent = getComputedStyle(root).getPropertyValue('--accent').trim();
          document.querySelector('[data-mode]').click();                                // → dark
          out.darkPal = P.palette(); out.darkAccent = getComputedStyle(root).getPropertyValue('--accent').trim();
          out.kept = [localStorage.getItem('palmar.pal-light'), localStorage.getItem('palmar.pal-dark')];
          P.choosePalette('dark', 'earth');
          out.backToDefault = P.palette();
          out.theme = root.dataset.theme;
          return out;})()""")
        self.assertEqual(r["theme"], "dark", "the theme did not end up where the test thinks: %r" % r)
        self.assertEqual([r["lightPal"], r["lightAccent"]], ["sky", "#2f6fed"], "the light choice did not apply: %r" % r)
        self.assertEqual([r["darkPal"], r["darkAccent"]], ["graphite", "#7cb3ff"], "the dark choice did not apply: %r" % r)
        self.assertEqual(r["kept"], ["sky", "graphite"], "the choices were not kept")
        self.assertIsNone(r["backToDefault"], "the default palette still carries an attribute")

    def test_a_choice_survives_a_reload(self):
        self.b.ev("""(()=>{window.palmar.choosePalette('light', 'lilac'); const root=document.documentElement;
          root.dataset.theme='light'; localStorage.setItem('palmar-theme','light'); return 1;})()""")
        self.b.ev("location.reload()")
        for _ in range(40):
            time.sleep(0.25)
            if self.b.ev("!!(window.palmar && window.palmar.palette)"):
                break
        self.assertEqual(self.b.ev("window.palmar.palette()"), "lilac")
        self.assertEqual(self.accent(), "#6d4de6")
