"""The page, in a real browser, against a real daemon.

Slow — each class starts Chrome — and skipped entirely where no Chrome is installed. Run it before
shipping and after touching anything in `palmar/web/`:

    python3 -m unittest tests.test_browser

What it cannot see: anything about GPU compositing. Chrome runs here with --disable-gpu and repaints
in software, so the drag trail in #17 is invisible to this file by construction, not by omission.
"""
from __future__ import annotations

import json
import urllib.parse
import os
import sys
import shutil
import tempfile
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
        """The button reads "theme" and opens a list now (2026-09-15); the state still stands beside
        it in a word, because the mark alone cannot say which of three it is."""
        seen = []
        for pick in ("system", "light:paper", "dark:earth"):
            self.b.ev("document.getElementById('theme').click()")
            self.b.ev("document.querySelector('#theme-menu [data-pick=%s]').click()" % json.dumps(pick))
            time.sleep(0.2)
            seen.append(self.b.ev("""(()=>{const b=document.getElementById('theme');
              return [b.dataset.mode, (b.querySelector('small')||{}).textContent];})()"""))
        modes = [m for m, _ in seen]
        labels = [l for _, l in seen]
        self.assertEqual(len(set(labels)), 3, "two states read the same: %s" % labels)
        self.assertEqual(set(modes), {"system", "light", "dark"})
        self.assertIn("auto", labels)


@unittest.skipIf(chrome_path() is None, "no Chrome on this machine")
class RailFold(unittest.TestCase):
    """Fold a rail to widen the canvas (2026-09-11). A folded rail keeps a 30px strip — the button that
    brings it back lives there, where the fold button was, and there is room for what belongs there later
    (user, 2026-09-15; it used to go to a true 0 with a 15px tab floating over the canvas). It is still
    far below RAIL_MIN, so nothing clamps it back open, and the state survives a reload."""

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

    STRIP = "30px"

    def test_folding_a_rail_widens_the_canvas_to_the_strip(self):
        # start from a known state
        self.b.ev("document.getElementById('open-l').click(); document.getElementById('open-r').click()")
        time.sleep(0.3)
        wide0 = self.cv()
        self.b.ev("document.getElementById('fold-l').click()")
        time.sleep(0.3)
        self.assertEqual(self.railvar("l"), self.STRIP, "a folded rail was clamped, not reduced to the strip")
        self.assertGreater(self.cv(), wide0, "the canvas did not widen")
        # reopen restores the width, not the strip
        self.b.ev("document.getElementById('open-l').click()")
        time.sleep(0.3)
        self.assertNotEqual(self.railvar("l"), self.STRIP)
        self.assertAlmostEqual(self.cv(), wide0, delta=2)

    def test_the_shortcut_toggles(self):
        self.b.ev("document.getElementById('open-r').click()")   # ensure open
        time.sleep(0.2)
        self.b.ev("document.dispatchEvent(new KeyboardEvent('keydown',"
                  "{key:'\\\\',code:'Backslash',metaKey:true,shiftKey:true,bubbles:true}))")
        time.sleep(0.3)
        self.assertEqual(self.railvar("r"), self.STRIP, "Cmd+Shift+\\ did not fold the right rail")
        self.b.ev("document.dispatchEvent(new KeyboardEvent('keydown',"
                  "{key:'\\\\',code:'Backslash',metaKey:true,shiftKey:true,bubbles:true}))")
        time.sleep(0.3)
        self.assertNotEqual(self.railvar("r"), self.STRIP, "it did not toggle back open")

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

    def test_a_folded_rail_is_a_strip_with_the_button_in_it(self):
        """30px, its own contents hidden, and the button that brings it back standing in the strip at
        the top — where the fold button was, so the two are in the same place either way."""
        self.b.ev("document.getElementById('open-l').click(); document.getElementById('open-r').click()")
        time.sleep(0.3)
        self.b.ev("document.getElementById('fold-l').click(); document.getElementById('fold-r').click()")
        time.sleep(0.4)
        for side, sel in (("left", ".rail.left"), ("right", ".rail.right")):
            w = self.b.ev("document.querySelector('%s').getBoundingClientRect().width" % sel)
            self.assertEqual(w, 30, "the folded %s rail is %spx wide, not the 30px strip" % (side, w))
        r = self.b.ev("""(()=>{const o=document.getElementById('open-l').getBoundingClientRect();
          const body=document.querySelector('.body').getBoundingClientRect();
          return {w: Math.round(o.width), tall: o.height > body.height - 2, top: Math.round(o.top - body.top)};})()""")
        self.assertEqual(r["w"], 30, "the strip is not the rail's width: %r" % r)
        self.assertTrue(r["tall"], "the strip does not run the height of the body: %r" % r)
        self.assertLess(r["top"], 2, "the strip does not start at the top: %r" % r)

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
        self.assertEqual(self.railvar("l"), self.STRIP)
        try:
            self.b.ws.call("Emulation.setDeviceMetricsOverride",
                           {"width": 760, "height": 560, "deviceScaleFactor": 1, "mobile": False})
            time.sleep(0.5)
            self.assertEqual(self.railvar("l"), self.STRIP, "a resize unfolded it behind the state")
        finally:
            self.b.ws.call("Emulation.clearDeviceMetricsOverride")
            time.sleep(0.5)
        self.assertEqual(self.railvar("l"), self.STRIP, "restoring the window unfolded it behind the state")
        w = self.b.ev("document.querySelector('.rail.left').getBoundingClientRect().width")
        self.assertEqual(w, 30, "it is %spx wide while the state says folded (the strip is 30)" % w)

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
            b.ev("""(()=>{document.getElementById('options')
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
class TheWorldAroundTheWindows(unittest.TestCase):
    """How far the canvas scrolls, and that a drag does not fight it.

    What was wrong, both measured on 2026-09-16. `.cv-scroll` was an `overflow:auto` box holding
    absolutely positioned windows, so how far it scrolled was whatever the browser worked out from
    their boxes — `max(viewport, their bounding box)` and not a pixel more.

    (A) **A window could never be put in the middle of the screen.** At full scroll the furthest
    window's far edge sits flush against the viewport's, so its centre lands `(viewport - window)/2`
    short — a constant, whatever the window's position. Measured 189px across and 168.5px down.

    (B) **Dragging the window that alone defined that area moved it nowhere.** Every pointermove
    wrote the window's left/top, the browser shrank the area in the same frame, and the scroll was
    clamped by exactly as much — so screen position = left - scrollLeft did not change. Over 24
    samples the hand travelled 888x556px and the window's screen x stayed 634 the whole way.

    Both are one rule now (user, 2026-09-17): take the room the windows occupy, call it the middle
    square, and lay nine of them out three by three. And **it never shrinks while the page is open**,
    which is what stops the drag fighting the scroll — no shrink, no clamp."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        # Scrollbars on: hiding them makes clientWidth 14px wider than a person ever sees, and these
        # tests are all about scroll arithmetic.
        cls.b = Browser(scrollbars=True).start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          await fetch('/api/sessions?token='+T,{method:'POST',
            headers:{'content-type':'application/json'},
            body:JSON.stringify({cwd:%s,name:'far'})});})()""" % json.dumps(cls.d.home))
        time.sleep(5)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def setUp(self):
        """**The room the hand pulls out is kept for the session, on purpose** — so tests that read an
        exact scroll range have to start from a known one, or the first test to pan leaves its slack
        lying around for every test after it. tidy is the page's own way of giving it back."""
        self.b.ev("(()=>{window.palmar.tidyCanvas(window.palmar.canvas(), true); return 1;})()")
        time.sleep(0.6)

    PUT = """
      const P = window.palmar, L = P.layout(), S = document.getElementById('cv-scroll');
      const by = (n) => [...P.tiles.values()].find((t) => t.s.name === n);
      const put = (n, x, y, w, h) => { const t = by(n);
        L[t.id] = Object.assign({}, L[t.id], {x:x, y:y, w:w, h:h});
        t.el.style.transition='none';
        t.el.style.left=x+'px'; t.el.style.top=y+'px'; t.el.style.width=w+'px'; t.el.style.height=h+'px';
        void t.el.offsetWidth; t.el.style.transition='';
        P.sizeWorld();
        return t.id; };
    """

    def js(self, body):
        return self.b.ev("(()=>{" + self.PUT + "\n" + body + "})()")

    def test_the_scroll_runs_to_the_windows_and_no_further(self):
        """**Nine squares became one.** The canvas used to scroll a whole screen past the windows on
        every side, so that any of them could be brought to the middle — and dragging the bar to the
        bottom to see the bottom window took you a screen past it into nothing, which is the gesture
        everybody actually makes (user, 2026-09-17). The slack is still reachable; it is taken by
        hand now, and the tests below this one are about that."""
        r = self.js("""
          put('far', 1600, 1100, 520, 360);
          const pad = document.querySelector('.cv-pad'), w = document.querySelector('.cv-world');
          return {padW: pad.offsetWidth, padH: pad.offsetHeight,
                  ox: parseFloat(w.style.left), oy: parseFloat(w.style.top),
                  content: P.contentExtent()};
        """)
        c = r["content"]
        self.assertEqual(r["padW"], c["hx"] - c["lx"], "the scroll does not end at the far window")
        self.assertEqual(r["padH"], c["hy"] - c["ly"], "the scroll does not end below the last window")
        self.assertEqual(r["ox"], -c["lx"], "board zero is not where the near edge puts it")
        self.assertEqual(r["oy"], -c["ly"], "board zero is not where the near edge puts it")

    def test_scrolling_to_the_bottom_stops_at_the_bottom_window(self):
        """The gesture everybody makes: drag the bar to the bottom to see the bottom window. With a
        screen of slack under it that took you a screen past it, into nothing (user, 2026-09-17)."""
        r = self.js("""
          put('far', 12, 1600, 520, 360);
          S.scrollTop = 1e7; S.scrollLeft = 1e7;
          const q = by('far').el.getBoundingClientRect(), box = S.getBoundingClientRect();
          return {below: Math.round(box.bottom - q.bottom), right: Math.round(box.right - q.right),
                  view: [S.clientWidth, S.clientHeight]};""")
        self.assertLess(r["below"], 40, "a screen of nothing under the last window: " + repr(r))
        self.assertGreaterEqual(r["below"], 0, "it scrolled past the bottom of the window: " + repr(r))

    def test_a_hand_on_the_bare_canvas_makes_room_past_the_end(self):
        """The slack is not gone, it is taken: grab the floor and pull, and the room appears as you
        go. With a real hand, because the whole point is the gesture."""
        before = self.js("""
          put('far', 12, 12, 520, 360); S.scrollTop = 1e7;
          const pad = document.querySelector('.cv-pad');
          return {pad: [pad.offsetWidth, pad.offsetHeight], at: Math.round(S.scrollTop)};""")
        spot = self.b.ev("""(()=>{const S=document.getElementById('cv-scroll');
          const b=S.getBoundingClientRect();
          for (let fx=0.7; fx>0.3; fx-=0.05) for (let fy=0.3; fy<0.7; fy+=0.05) {
            const x=b.left+b.width*fx, y=b.top+b.height*fy;
            if (document.elementFromPoint(x,y)===S) return {x:x, y:y};
          } return null;})()""")
        self.assertTrue(spot, "no bare canvas to grab")
        self.b.ws.call("Input.dispatchMouseEvent",
                       dict(type="mousePressed", button="left", x=spot["x"], y=spot["y"],
                            clickCount=1, buttons=1))
        for i in range(1, 13):
            self.b.ws.call("Input.dispatchMouseEvent",
                           dict(type="mouseMoved", button="left", x=spot["x"], y=spot["y"] - 40 * i, buttons=1))
            time.sleep(0.03)
        self.b.ws.call("Input.dispatchMouseEvent",
                       dict(type="mouseReleased", button="left", x=spot["x"], y=spot["y"] - 480,
                            clickCount=1, buttons=0))
        time.sleep(0.5)
        after = self.js("""const pad = document.querySelector('.cv-pad');
          return {pad: [pad.offsetWidth, pad.offsetHeight], at: Math.round(S.scrollTop)};""")
        self.assertGreater(after["pad"][1], before["pad"][1],
                           "pulling past the end made no room: %r -> %r" % (before, after))
        self.assertGreater(after["at"], before["at"],
                           "the view did not follow the hand past the end: %r -> %r" % (before, after))

    def test_the_hand_pulls_out_the_room_the_scrollbar_does_not_offer(self):
        """(A), which the slack was for and which the scrollbar no longer reaches on its own: bringing
        the far window to the middle of the screen. Setting the scroll is not enough any more — the
        pad ends at that window's far edge, so the browser clamps it, and it lands short by exactly
        (viewport - window)/2, the number this test was born measuring. Panning there makes the room
        on the way, and then it centres."""
        short = self.js("""
          const id = put('far', 1600, 1100, 520, 360);
          const L2 = P.layout()[id], W = document.querySelector('.cv-world');
          S.scrollLeft = parseFloat(W.style.left) + L2.x + L2.w / 2 - S.clientWidth / 2;
          S.scrollTop  = parseFloat(W.style.top)  + L2.y + L2.h / 2 - S.clientHeight / 2;
          const r2 = by('far').el.getBoundingClientRect(), box = S.getBoundingClientRect();
          return {cx: r2.left + r2.width / 2 - box.left, cy: r2.top + r2.height / 2 - box.top,
                  vx: S.clientWidth / 2, vy: S.clientHeight / 2};""")
        self.assertGreater(abs(short["cx"] - short["vx"]), 2,
                           "the scrollbar reached past the windows on its own: " + repr(short))
        r = self.js("""
          const L2 = P.layout()[by('far').id];
          P.panTo(L2.x + L2.w / 2 - S.clientWidth / 2, L2.y + L2.h / 2 - S.clientHeight / 2);
          const r2 = by('far').el.getBoundingClientRect(), box = S.getBoundingClientRect();
          return {cx: r2.left + r2.width / 2 - box.left, cy: r2.top + r2.height / 2 - box.top,
                  vx: S.clientWidth / 2, vy: S.clientHeight / 2};""")
        self.assertLess(abs(r["cx"] - r["vx"]), 2, "it could not be centred across: " + repr(r))
        self.assertLess(abs(r["cy"] - r["vy"]), 2, "it could not be centred down: " + repr(r))

    def test_tidy_gives_back_the_room_the_hand_pulled_out(self):
        """Room reached by hand is kept for the session so it cannot vanish under you. tidy is the
        one place it is asked for back, and then the scrollbar means the windows again."""
        r = self.js("""
          put('far', 12, 12, 520, 360);      // already at the corner: tidy moves nothing, only gives back
          const pad = document.querySelector('.cv-pad');
          const tight = [pad.offsetWidth, pad.offsetHeight];
          P.panTo(3000, 2500);
          const pulled = [pad.offsetWidth, pad.offsetHeight];
          P.tidyCanvas(P.canvas(), true);
          return {tight: tight, pulled: pulled, after: [pad.offsetWidth, pad.offsetHeight]};""")
        self.assertGreater(r["pulled"][0], r["tight"][0], "panning past the end made no room: " + repr(r))
        self.assertGreater(r["pulled"][1], r["tight"][1], "panning past the end made no room: " + repr(r))
        self.assertEqual(r["after"], r["tight"], "tidy did not give the room back: " + repr(r))

    def test_expand_fills_the_screen_and_not_a_dot(self):
        """**A maximised window fills the viewport, and nothing it sits inside measures that.** The CSS
        said `calc(100% - 20px)`, which was right while tiles were children of the scroller; they live
        in `.cv-world` now, and that layer is 0x0 on purpose because it is only an origin. 100% of
        zero is zero, so pressing expand turned the window into a dot (user, 2026-09-18). The second
        half of this test is the one a naive fix fails: panned into the slack the origin is not zero,
        and a tile's `left` is measured from it."""
        for where in ("at the corner", "panned into the slack"):
            self.js("put('far', 40, 40, 520, 360); return 1;")
            if where != "at the corner":
                self.js("P.panTo(-400, -300); return 1;")
                time.sleep(0.4)
            r = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()][0];
              t.xpEl.click(); return 1;})()""")
            time.sleep(1.0)
            got = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()][0];
              const q=t.el.getBoundingClientRect(), S=document.getElementById('cv-scroll');
              const b=S.getBoundingClientRect();
              return {w:Math.round(q.width), h:Math.round(q.height),
                      l:Math.round(q.left-b.left), t:Math.round(q.top-b.top),
                      vw:S.clientWidth, vh:S.clientHeight};})()""")
            self.b.ev("(()=>{[...window.palmar.tiles.values()][0].xpEl.click(); return 1;})()")
            time.sleep(0.6)
            self.assertEqual([got["w"], got["h"]], [got["vw"] - 20, got["vh"] - 20],
                             "expanded window does not fill the screen, %s: %r" % (where, got))
            self.assertEqual([got["l"], got["t"]], [10, 10],
                             "expanded window is not at the screen's corner, %s: %r" % (where, got))

    def test_a_terminal_that_outgrew_its_box_climbs_back_into_it(self):
        """**The prompt was below the bottom of the pane with no way to scroll to it.** `fit()` divides
        the box by the cell size xterm has cached, and xterm re-measures that only when a font option
        *changes* — so when the cell changes for any other reason (the real font arriving after boot
        gave up waiting 1.5s for it; the WebGL renderer settling on dimensions of its own, which
        floors the cell to device pixels and so is a real change at Windows' 1.125 and none at 1),
        every later fit divides by a number that is no longer true and agrees with itself.

        Two guesses at *when* were both wrong: a boot-time `document.fonts.ready` sweep does not reach
        a pane opened afterwards, and the check inside `refit` needs somebody to call `refit`. So this
        does not test a cause or a moment. It breaks the state — the terminal ends up with more rows
        than fit, which is all the user could ever see — and asks only that palmar climb back out of
        it **with nobody calling anything.**"""
        self.js("put('far', 40, 40, 520, 360); return 1;")
        read = """(()=>{const t=[...window.palmar.tiles.values()][0];
          const s=t.el.querySelector('.xterm-screen');
          return {rows:t.term.rows, screen:Math.round(s.offsetHeight),
                  box:Math.round(t.termEl.clientHeight)};})()"""
        was = self.b.ev(read)
        self.assertLessEqual(was["screen"], was["box"], "it did not start out fitting: %r" % (was,))
        self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()][0];
          t.term.resize(t.term.cols, t.term.rows + 8); return 1;})()""")
        time.sleep(0.4)
        broke = self.b.ev(read)
        self.assertGreater(broke["screen"], broke["box"],
                           "it did not actually break — this test proves nothing: %r" % (broke,))
        end = time.time() + 14
        now = broke
        while time.time() < end:
            time.sleep(1.0)
            now = self.b.ev(read)
            if now["screen"] <= now["box"]:
                break
        self.assertLessEqual(now["screen"], now["box"],
                             "nobody noticed the prompt was off the bottom: %r" % (now,))

    def test_writing_a_font_option_back_unchanged_measures_nothing(self):
        """Why `remeasure` moves the value at all. This is xterm's rule, not ours, and the whole fix
        above rests on it — if a future version starts firing on an identical write, the hair up and
        back can go."""
        r = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()][0];
          let fired = 0;
          const off = t.term.onResize(() => fired++);
          const before = t.term._core._renderService.dimensions.css.cell.height;
          t.term._core._renderService.dimensions.css.cell.height = 13.67;
          const f = t.term.options.fontSize;
          t.term.options.fontSize = f;                       // the same value: nothing happens
          const same = t.term._core._renderService.dimensions.css.cell.height;
          t.term.options.fontSize = f + 0.01;
          t.term.options.fontSize = f;                       // moved and back: it measures
          const moved = t.term._core._renderService.dimensions.css.cell.height;
          off.dispose();
          return {before: before, afterSame: same, afterMoved: moved};})()""")
        self.assertEqual(r["afterSame"], 13.67, "an identical write re-measured after all: %r" % (r,))
        self.assertAlmostEqual(r["afterMoved"], r["before"], places=1,
                               msg="moving the value and back did not re-measure: %r" % (r,))

    def test_the_world_does_not_shrink_while_the_page_is_open(self):
        """The slack you panned into does not vanish under you. It goes on a reload, not before."""
        r = self.js("""
          put('far', 1600, 1100, 520, 360);
          const big = document.querySelector('.cv-pad').offsetWidth;
          put('far', 40, 40, 520, 360);              // back into the corner
          return {big: big, after: document.querySelector('.cv-pad').offsetWidth};
        """)
        self.assertEqual(r["after"], r["big"], "the world shrank under the user: " + repr(r))

    def test_the_first_window_takes_palmars_own_shape(self):
        """**Chromium's app window is nearly square and this page does not fit in it.** Every case
        here is a work area somebody measured, and every one of them was wrong under the first rule,
        which set the width and handed `outerHeight` straight back: on two of the three it declined
        to do anything at all, because the window was already wide enough and width was never the
        thing that was wrong. The shape palmar's own window opens at is 1280x820."""
        for avail_w, avail_h, outer_w, want in [
            (1536, 912, 1050, (1280, 820)),    # Windows, no saved bounds — the case that started it
            (1536, 912, 1302, (1302, 834)),    # Windows, the same machine after Chromium had its way
            (2560, 1392, 1280, (1280, 820)),   # the shape the user sent on 2026-09-17: 1280x1029
            (1024, 700, 900, (984, 630)),      # a work area too small for palmar's own size
        ]:
            got = self.b.ev("(()=>{const s=window.palmar.firstWindowSize(%d,%d,%d);return [s.w,s.h];})()"
                            % (avail_w, avail_h, outer_w))
            self.assertEqual(got, list(want),
                             "a %dx%d work area holding a %dpx window: %r" % (avail_w, avail_h, outer_w, got))
            self.assertLessEqual(got[0], avail_w, "wider than the screen it is on")
            self.assertLessEqual(got[1], avail_h, "taller than the screen it is on")
            self.assertGreaterEqual(got[0], min(outer_w, avail_w - 40), "it took width away from the window")

    def test_the_world_is_sized_without_being_told(self):
        """The other tests here call sizeWorld through the bench, which proves the arithmetic and not
        that anything runs it. This one touches nothing and reads what the page did on its own — the
        gap that let "the slack is there" and "the slack appears" be two different things."""
        r = self.b.ev("""(()=>{const S=document.getElementById('cv-scroll');
          const pad=document.querySelector('.cv-pad'), w=document.querySelector('.cv-world');
          return {pad: !!pad, world: !!w, padW: pad?pad.offsetWidth:0, padH: pad?pad.offsetHeight:0,
                  ox: w?parseFloat(w.style.left):0, oy: w?parseFloat(w.style.top):0,
                  cw: S.clientWidth, ch: S.clientHeight, ext: window.palmar.contentExtent()};})()""")
        self.assertTrue(r["pad"] and r["world"], "the world was never built: " + repr(r))
        self.assertGreaterEqual(r["padW"], r["cw"] - 2, "the floor is narrower than the screen: " + repr(r))
        self.assertGreaterEqual(r["padH"], r["ch"] - 2, "the floor is shorter than the screen: " + repr(r))
        self.assertEqual(r["padW"], r["ext"]["hx"] - r["ext"]["lx"], "the floor is not the windows' room: " + repr(r))
        self.assertEqual(r["padH"], r["ext"]["hy"] - r["ext"]["ly"], "the floor is not the windows' room: " + repr(r))
        self.assertEqual([r["ox"], r["oy"]], [-r["ext"]["lx"], -r["ext"]["ly"]],
                         "board zero is not where the near edge puts it: " + repr(r))

    def test_a_window_can_be_carried_past_the_origin(self):
        """The slack was somewhere you could look but not put anything: the drag clamped every window
        at board zero, so panning into the empty space above and to the left and dragging a window
        there stopped it dead at the edge of the ones already placed (user, 2026-09-17). And because
        a window was always pinned to the corner, tidy had nothing to close up and its button sat
        disabled — one cause, two complaints."""
        # **Scroll to it first.** The world is three screens wide, so a window placed by the bench is
        # very often nowhere near the view — and a title bar that is off screen cannot be grabbed.
        self.js("""
          const id = put('far', 60, 50, 300, 200);
          const o = P.origin(), q = P.layout()[id];
          S.scrollLeft = o.x + q.x - 420;      // room on screen to carry it past the origin
          S.scrollTop  = o.y + q.y - 300;
          return 1;""")
        time.sleep(0.3)
        box = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name==='far');
          const r = t.el.querySelector('.tb').getBoundingClientRect();
          return {x: r.left + r.width/2, y: r.top + r.height/2};})()""")
        send = lambda **kw: self.b.ws.call("Input.dispatchMouseEvent", dict(button="left", **kw))
        send(type="mousePressed", x=box["x"], y=box["y"], clickCount=1, buttons=1)
        for i in range(1, 11):
            send(type="mouseMoved", x=box["x"] - 30 * i - (i % 3), y=box["y"] - 22 * i - (i % 5), buttons=1)
        send(type="mouseReleased", x=box["x"] - 300, y=box["y"] - 220, clickCount=1, buttons=0)
        time.sleep(0.8)
        r = self.b.ev("""(()=>{const P=window.palmar, L=P.layout(), o=P.origin();
          const t=[...P.tiles.values()].find(t=>t.s.name==='far'); const q=L[t.id];
          const pad=document.querySelector('.cv-pad');
          const mm=document.getElementById('mm'), mb=mm.getBoundingClientRect();
          const inside=[...mm.querySelectorAll('.mm-t')].every(e=>{const b=e.getBoundingClientRect();
            return b.left>=mb.left-1&&b.top>=mb.top-1&&b.right<=mb.right+1&&b.bottom<=mb.bottom+1;});
          return {x:q.x, y:q.y, onPad:[o.x+q.x, o.y+q.y], padW:pad.offsetWidth, padH:pad.offsetHeight,
                  mmInside:inside, tidyOff:document.getElementById('tidy').disabled};})()""")
        self.assertLess(r["x"], 0, "the window was still stopped at the origin: " + repr(r))
        self.assertLess(r["y"], 0, "the window was still stopped at the origin: " + repr(r))
        # **The pad has to have grown to hold it.** A board coordinate may be negative; a place on the
        # scrolled canvas may not, or the window would sit where nothing can scroll to.
        self.assertGreaterEqual(r["onPad"][0], 0, "it landed off the front of the canvas: " + repr(r))
        self.assertGreaterEqual(r["onPad"][1], 0, "it landed off the top of the canvas: " + repr(r))
        self.assertTrue(r["mmInside"], "the minimap drew a window outside its own box: " + repr(r))
        self.assertFalse(r["tidyOff"], "tidy still says there is nothing to close up: " + repr(r))
        back = self.b.ev("""(()=>{const P=window.palmar, L=P.layout();
          const t=[...P.tiles.values()].find(t=>t.s.name==='far');
          document.getElementById('tidy').click();
          return [L[t.id].x, L[t.id].y];})()""")
        self.assertEqual(back, [12, 12], "tidy did not pull it back to the corner: " + repr(back))

    def test_tidy_leaves_you_looking_at_the_corner(self):
        """Pressing it used to do nothing you could see. The view was nudged by exactly what the
        windows moved so that nothing slid under the eye, and once the canvas had a square of slack
        that compensation became exact — the windows went to the corner and the view went with them
        ("tidy 버튼 누르면 보고있는 화면에서 살짝 흔들리기만", user, 2026-09-17). A button whose job is
        "pull them back to the corner" has to leave you looking at the corner."""
        self.js("""
          put('far', 500, 400, 300, 200);
          const o = P.origin();
          S.scrollLeft = o.x + 900; S.scrollTop = o.y + 700;   // a long way from the corner
          P.paintTidy();
          return 1;""")
        off = self.b.ev("document.getElementById('tidy').disabled")
        self.assertFalse(off, "tidy says there is nothing to close up, with a window at 500,400")
        self.b.ev("document.getElementById('tidy').click()")
        time.sleep(1.5)
        r = self.b.ev("""(()=>{const P=window.palmar, L=P.layout(), S=document.getElementById('cv-scroll');
          const t=[...P.tiles.values()].find(t=>t.s.name==='far');
          const r=t.el.getBoundingClientRect(), b=S.getBoundingClientRect();
          return {at:[L[t.id].x, L[t.id].y], onScreen:[Math.round(r.left-b.left), Math.round(r.top-b.top)]};})()""")
        self.assertEqual(r["at"], [12, 12], "the window was not pulled to the corner: " + repr(r))
        # And you can see it: near the top-left of the viewport rather than a screen away.
        self.assertLess(r["onScreen"][0], 80, "the view did not follow to the corner: " + repr(r))
        self.assertLess(r["onScreen"][1], 80, "the view did not follow to the corner: " + repr(r))
        self.assertGreaterEqual(r["onScreen"][0], 0, "it went past the corner: " + repr(r))

    def test_dragging_the_window_that_defines_the_world_moves_it_on_screen(self):
        """(B), with a real hand. The window used to stay put on screen however far the hand went."""
        # Scroll to where the old code's maximum was — the far edge of the windows' own room. Going
        # to the real maximum now lands in the slack, with the window off screen and nothing to grab.
        self.js("""
          put('far', 1600, 1100, 520, 360);
          const c = P.contentExtent(), o = P.origin();
          S.scrollLeft = o.x + c.w - S.clientWidth;
          S.scrollTop  = o.y + c.h - S.clientHeight;
          return 1;""")
        before = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name==='far');
          const r=t.el.getBoundingClientRect(); return {x:r.left, y:r.top};})()""")
        box = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name==='far');
          const r = t.el.querySelector('.tb').getBoundingClientRect();
          return {x: r.left + r.width/2, y: r.top + r.height/2};})()""")
        x, y = box["x"], box["y"]
        send = lambda **kw: self.b.ws.call("Input.dispatchMouseEvent", dict(button="left", **kw))
        send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        # **No two steps alike.** A hand does not move in equal increments (AGENTS.md; and an earlier
        # test that did exactly that hid a bug), so the offsets are uneven and never repeat.
        steps = [(-37 * i - (i % 3), -23 * i - (i % 5)) for i in range(1, 13)]
        for dx, dy in steps:
            send(type="mouseMoved", x=x + dx, y=y + dy, buttons=1)
        last = steps[-1]
        send(type="mouseReleased", x=x + last[0], y=y + last[1], clickCount=1, buttons=0)
        time.sleep(0.6)
        after = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name==='far');
          const r=t.el.getBoundingClientRect(); return {x:r.left, y:r.top};})()""")
        moved = (after["x"] - before["x"], after["y"] - before["y"])
        # The hand went `last`; the window should have gone with it, give or take the drop settling.
        self.assertLess(abs(moved[0] - last[0]), 12,
                        "the window did not follow the hand across: hand %r, window %r" % (last, moved))
        self.assertLess(abs(moved[1] - last[1]), 12,
                        "the window did not follow the hand down: hand %r, window %r" % (last, moved))


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
          return {a:[L[by('a').id].x,L[by('a').id].y], b:[L[by('b').id].x,L[by('b').id].y], bid: by('b').id};})()""")
        self.assertEqual(after["b"], before["b"], "the pushed window did not go back")
        self.assertEqual(after["a"], before["a"], "the window I dragged did not go back")
        time.sleep(0.5)                       # the save is debounced; the daemon is the only store now
        saved = self.d.get("/api/layout")
        saved = saved.get("layout", saved)[after["bid"]]
        self.assertEqual([saved["x"], saved["y"]], before["b"], "it moved on screen but left the old position saved")

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

class TidyWithTheWindowsFarApart(unittest.TestCase):
    """Two windows, far apart, and its own board — TheWorldAroundTheWindows has exactly one window
    on purpose, because its arithmetic is about the one that alone defines the world."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser(scrollbars=True).start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          for (const n of ['far','near'])
            await fetch('/api/sessions?token='+T,{method:'POST',
              headers:{'content-type':'application/json'},
              body:JSON.stringify({cwd:%s,name:n})});})()""" % json.dumps(cls.d.home))
        time.sleep(6)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def setUp(self):
        # The scale is a view and views last, so a test that stood back leaves the next one looking
        # from further away. Back to 1:1 before each, like the tidy the other classes do.
        self.b.ev("(()=>{window.palmar.setZoom(1); return 1;})()")
        time.sleep(0.3)

    def tearDown(self):
        self.assertEqual(self.b.errors(), [], "the page threw while being driven")

    PUT = TheWorldAroundTheWindows.PUT

    def js(self, body):
        return self.b.ev("(()=>{" + self.PUT + "\n" + body + "})()")

    def test_tidy_shows_you_the_group_you_were_working_in(self):
        """The window in front is the one you last touched, so that is what tidy takes you back to —
        with its group, because a member on its own is half a thing to look at (user, 2026-09-17)."""
        self.js("""
          put('far', 1800, 60, 300, 200);      // top right, and the one we will be working in
          put('near', 60, 1400, 300, 200);     // bottom left, the one nearest the corner
          P.focusTile(by('far').id, {keyboard: false});
          const o = P.origin(); S.scrollLeft = o.x + 400; S.scrollTop = o.y + 400;
          P.paintTidy(); return 1;""")
        self.b.ev("document.getElementById('tidy').click()")
        time.sleep(1.5)
        seen = self.b.ev("""(()=>{const P=window.palmar, S=document.getElementById('cv-scroll');
          const b=S.getBoundingClientRect();
          return [...P.tiles.values()].filter((t)=>{const q=t.el.getBoundingClientRect();
            return q.right>b.left && q.left<b.right && q.bottom>b.top && q.top<b.bottom;})
            .map((t)=>t.s.name).sort();})()""")
        self.assertIn("far", seen, "tidy did not go back to the window in front: %r" % (seen,))

    def test_a_full_view_sends_the_next_window_next_door_not_home(self):
        """Two windows in sight leave no room for a third, and falling straight back to the top-left of
        the board put it where the person had to go and find it — the very thing looking in view first
        was for (user, 2026-09-17). The gap it takes is the nearest one to the view."""
        r = self.js("""
          put('far', 1500, 1200, 520, 360); put('near', 2032, 1200, 520, 360);
          const o = P.origin(); S.scrollLeft = o.x + 1488; S.scrollTop = o.y + 1188;
          return {spot: P.firstFree(520, 360, by('far').s.canvas),
                  view: [S.scrollLeft - o.x, S.scrollTop - o.y, S.clientWidth, S.clientHeight]};""")
        vx, vy, vw, vh = r["view"]
        sx, sy = r["spot"]["x"], r["spot"]["y"]
        self.assertNotEqual([sx, sy], [12, 12], "it went home to the board's corner")
        near = max(abs(sx + 260 - (vx + vw / 2)), abs(sy + 180 - (vy + vh / 2)))
        self.assertLess(near, max(vw, vh), "it landed a long way from the view: %r, looking at %r"
                        % (r["spot"], r["view"]))

    def test_tidy_is_there_even_when_there_is_nothing_to_close_up(self):
        """The button was lit by slack alone, so on a canvas already at the corner it went grey and the
        other half of what it does — taking you back to what you were working in — could not be
        reached (user, 2026-09-17). Only an empty canvas has nowhere to take you."""
        self.js("put('far', 12, 12, 300, 200); put('near', 330, 12, 300, 200); P.paintTidy(); return 1;")
        self.assertEqual(self.js("return P.tidyCanvas(P.canvas(), false);"), False,
                         "there was slack after all — this test is not testing what it says")
        self.assertFalse(self.b.ev("document.getElementById('tidy').disabled"),
                         "tidy went grey on a canvas that still has windows to go and look at")
        # And pressing it takes you there rather than doing nothing at all.
        self.js("const o = P.origin(); S.scrollLeft = o.x + 900; S.scrollTop = o.y + 700; return 1;")
        self.b.ev("document.getElementById('tidy').click()")
        time.sleep(1.5)
        seen = self.b.ev("""(()=>{const P=window.palmar, S=document.getElementById('cv-scroll');
          const b=S.getBoundingClientRect();
          return [...P.tiles.values()].filter((t)=>{const q=t.el.getBoundingClientRect();
            return q.right>b.left && q.left<b.right && q.bottom>b.top && q.top<b.bottom;}).length;})()""")
        self.assertTrue(seen, "pressing it left the view on empty canvas")

    def test_a_new_window_opens_where_you_are_looking(self):
        """The canvas is far bigger than the screen, and a new terminal at the board's corner is one
        you have to go and find (user, 2026-09-17). The whole-canvas scan stays underneath it."""
        r = self.js("""
          put('far', 12, 12, 300, 200); put('near', 12, 240, 300, 200);
          const o = P.origin(); S.scrollLeft = o.x + 1500; S.scrollTop = o.y + 1200;
          return {spot: P.firstFree(300, 200, by('far').s.canvas),
                  view: [S.scrollLeft - o.x, S.scrollTop - o.y, S.clientWidth, S.clientHeight]};""")
        vx, vy, vw, vh = r["view"]
        sx, sy = r["spot"]["x"], r["spot"]["y"]
        self.assertTrue(vx <= sx and sx + 300 <= vx + vw and vy <= sy and sy + 200 <= vy + vh,
                        "it opened off screen: spot %r, looking at %r" % (r["spot"], r["view"]))

    def test_ctrl_and_the_wheel_over_the_empty_canvas_stands_back(self):
        """Asked for 2026-09-20: "빈 캔버스에 마우스를 올리고 ctrl+휠로 확대 축소". The windows are
        drawn smaller, not resized — so the two things this has to prove are that the drawing did
        shrink and that the **board** did not move: no terminal was told a new size, and what the
        daemon holds is the same numbers it held before.

        And the point under the pointer stays where it is. Zooming about the corner slides what you
        were looking at off the screen, and then the gesture is a chore rather than a look."""
        self.js("put('far', 40, 40, 400, 300); put('near', 2200, 1600, 400, 300); return 1;")
        # Looking at the middle of the board, with room on every side: standing back has to pull
        # more board into view, and at the very corner there is none to pull and the scroll clamps.
        self.js("P.panTo(700, 500); return 1;")
        time.sleep(0.4)
        # A point on the bare floor, well clear of both windows, and the board coordinate under it.
        spot = self.js("""const b = S.getBoundingClientRect();
          const x = b.left + S.clientWidth * 0.7, y = b.top + S.clientHeight * 0.55;
          return {x: x, y: y, over: document.elementFromPoint(x, y).id,
                  board: P.boardFromClient(x, y),
                  drawn: by('far').el.getBoundingClientRect().width,
                  box: Object.assign({}, P.layout()[by('far').id])};""")
        self.assertEqual(spot["over"], "cv-scroll", "the test pointed at a window, not at empty canvas")
        self.b.ws.call("Input.dispatchMouseEvent",
                       dict(type="mouseWheel", x=spot["x"], y=spot["y"],
                            deltaX=0, deltaY=120, modifiers=2))      # 2 = Ctrl
        time.sleep(0.6)
        after = self.js("""return {zoom: P.zoom(),
                  drawn: by('far').el.getBoundingClientRect().width,
                  board: P.boardFromClient(%r, %r),
                  box: Object.assign({}, P.layout()[by('far').id])};""" % (spot["x"], spot["y"]))
        self.assertLess(after["zoom"], 0.95, "ctrl and the wheel did not stand back: %r" % (after,))
        self.assertLess(after["drawn"], spot["drawn"] * 0.95,
                        "the window is not drawn any smaller: %r vs %r" % (after["drawn"], spot["drawn"]))
        for k in ("x", "y", "w", "h"):
            self.assertEqual(after["box"][k], spot["box"][k],
                             "the board moved under a view change: %r → %r" % (spot["box"], after["box"]))
        for k in ("x", "y"):
            self.assertLess(abs(after["board"][k] - spot["board"][k]), 12,
                            "what was under the pointer slid away: %r → %r" % (spot["board"], after["board"]))

    def test_over_a_window_the_same_gesture_is_that_window_s_text(self):
        """Both are wanted, so the target decides (#25 is the older of the two). Neither is ever the
        browser's own zoom — that is what the preventDefault in each handler is for."""
        self.js("put('far', 40, 40, 400, 300); P.panTo(0, 0); return 1;")
        time.sleep(0.3)
        was = self.js("""const t = by('far'), r = t.termEl.getBoundingClientRect();
          return {font: t.term.options.fontSize, zoom: P.zoom(),
                  x: r.left + r.width / 2, y: r.top + r.height / 2};""")
        self.b.ws.call("Input.dispatchMouseEvent",
                       dict(type="mouseWheel", x=was["x"], y=was["y"],
                            deltaX=0, deltaY=-120, modifiers=2))
        time.sleep(0.5)
        now = self.js("return {font: by('far').term.options.fontSize, zoom: P.zoom()};")
        self.assertGreater(now["font"], was["font"], "the pane's text did not grow: %r → %r" % (was, now))
        self.assertEqual(now["zoom"], was["zoom"], "the canvas stood back instead: %r" % (now,))

    def test_coming_back_from_far_out_lands_on_the_window_you_were_in(self):
        """Reported 2026-09-20: "ctrl+휠로 화면 축소 후에 see the whole canvas 버튼 누르면 화면이
        이상한 곳으로 가있어. 미니맵은 정상으로 돌아오는데."

        Standing back far enough and the whole board is smaller than the screen, so **the middle of
        the screen is empty canvas past everything** — and coming back to 1:1 held exactly that.
        Measured before the fix: the view landed at board 375,292 with both windows off the top
        left, while the minimap, which reads the same model, was right. It lands on the window you
        were working in now, in the middle, which is the other half of what was asked for."""
        self.js("""put('far', 40, 40, 400, 300); put('near', 700, 500, 400, 300);
          P.panTo(0, 0); P.focusTile(by('far').id, {keyboard: false}); return 1;""")
        time.sleep(0.5)
        spot = self.js("""const b = S.getBoundingClientRect();
          return {x: b.left + S.clientWidth / 2, y: b.top + S.clientHeight / 2,
                  over: document.elementFromPoint(b.left + S.clientWidth / 2, b.top + S.clientHeight / 2).id};""")
        self.assertEqual(spot["over"], "cv-scroll", "the test pointed at a window, not at empty canvas")
        for _ in range(5):
            self.b.ws.call("Input.dispatchMouseEvent",
                           dict(type="mouseWheel", x=spot["x"], y=spot["y"],
                                deltaX=0, deltaY=180, modifiers=2))
            time.sleep(0.25)
        time.sleep(0.5)
        out = self.js("""const pad = document.querySelector('.cv-pad');
          return {zoom: P.zoom(), view: P.viewBoard(), pad: [pad.offsetWidth, pad.offsetHeight],
                  client: [S.clientWidth, S.clientHeight]};""")
        self.assertLess(out["zoom"], 0.7, "it did not stand far enough back to show the fault: %r" % (out,))
        self.assertGreater(out["view"]["w"], out["pad"][0],
                           "the screen does not reach past the board — the fault needs that: %r" % (out,))
        self.b.ev("document.getElementById('fit').click()")
        time.sleep(1.0)
        back = self.js("""const b = S.getBoundingClientRect(), q = by('far').el.getBoundingClientRect();
          return {zoom: P.zoom(), mid: [q.left + q.width / 2 - b.left, q.top + q.height / 2 - b.top],
                  want: [S.clientWidth / 2, S.clientHeight / 2]};""")
        self.assertEqual(back["zoom"], 1, "it did not come back to 1:1: %r" % (back,))
        for i, side in enumerate(("across", "down")):
            self.assertLess(abs(back["mid"][i] - back["want"][i]), 24,
                            "the window it came back to is not in the middle %s: %r" % (side, back))

    def test_standing_back_does_not_leave_the_room_behind(self):
        """The floor has to cover the screen, and under 1:1 the screen shows more board than there
        is — so the pad is given the shortfall. Only the pad: write it into the world and standing
        back once would leave several screens of slack that come back to 1:1 with you. Measured
        before the fix: 1112 wide before, 1636 after."""
        self.js("put('far', 40, 40, 400, 300); put('near', 700, 500, 400, 300); P.panTo(0, 0); return 1;")
        time.sleep(0.4)
        pad = "const p = document.querySelector('.cv-pad'); return [p.offsetWidth, p.offsetHeight];"
        was = self.js(pad)
        self.js("P.setZoom(0.4); return 1;")
        time.sleep(0.5)
        small = self.js(pad)
        self.assertGreaterEqual(small[0], self.js("return S.clientWidth;") - 1,
                                "the floor stopped covering the screen: %r" % (small,))
        self.js("P.setZoom(1); return 1;")
        time.sleep(0.5)
        self.assertEqual(self.js(pad), was, "standing back and coming home left slack behind")

    def test_standing_back_shows_them_all_and_the_windows_still_work(self):
        """The button is the shortcut, not the feature. What it replaced was a mode: no scrolling, no
        pointer on a window, a click to come back out. This one is a scale and nothing else, so the
        canvas still scrolls and a window still takes the pointer while it is on."""
        self.js("put('far', 40, 40, 400, 300); return 1;")
        self.js("put('near', 60, 1500, 400, 300); P.panTo(0, 0); return 1;")   # a known place to start
        time.sleep(0.4)
        look = """const b = S.getBoundingClientRect();
          const t0 = [...P.tiles.values()][0];
          return {seen: [...P.tiles.values()].filter((t) => {const q = t.el.getBoundingClientRect();
                    return q.width > 1 && q.right > b.left && q.left < b.right && q.bottom > b.top && q.top < b.bottom;})
                    .map((t) => t.s.name).sort(),
                  zoom: P.zoom(), pe: getComputedStyle(t0.el).pointerEvents,
                  pressed: document.getElementById('fit').getAttribute('aria-pressed')};"""
        before = self.js(look)
        self.assertEqual(before["seen"], ["far"], "both were already on screen: %r" % (before,))
        self.b.ev("document.getElementById('fit').click()")
        time.sleep(0.8)
        back = self.js(look)
        self.assertEqual(back["seen"], ["far", "near"], "standing back did not show them all: %r" % (back,))
        self.assertLess(back["zoom"], 1, "it moved the view instead of the scale: %r" % (back,))
        self.assertEqual(back["pressed"], "true", "the button does not read as on")
        self.assertEqual(back["pe"], "auto", "the windows stopped taking input — that was the old mode")
        self.b.ev("document.getElementById('fit').click()")
        time.sleep(0.8)
        home = self.js(look)
        self.assertEqual(home["zoom"], 1, "a second press did not come back to 1:1: %r" % (home,))
        self.assertEqual(home["pressed"], "false")

    def test_tidy_does_not_leave_you_staring_between_two_windows(self):
        """One window at the top right and one at the bottom left: the corner of the box they make is
        empty canvas, and pressing tidy went and looked at it (user, 2026-09-17). The view goes to
        whichever window is nearest that corner instead, so something is always on screen."""
        self.js("""
          put('far', 1800, 60, 300, 200);      // top right
          put('near', 60, 1400, 300, 200);     // bottom left
          const o = P.origin(); S.scrollLeft = o.x + 900; S.scrollTop = o.y + 700;
          P.paintTidy(); return 1;""")
        self.b.ev("document.getElementById('tidy').click()")
        time.sleep(1.5)
        r = self.b.ev("""(()=>{const P=window.palmar, S=document.getElementById('cv-scroll');
          const b=S.getBoundingClientRect();
          const seen=[...P.tiles.values()].filter((t)=>{const q=t.el.getBoundingClientRect();
            return q.right>b.left && q.left<b.right && q.bottom>b.top && q.top<b.bottom;})
            .map((t)=>t.s.name);
          return {seen: seen.sort()};})()""")
        self.assertTrue(r["seen"], "tidy left the view on empty canvas: " + repr(r))

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
        """Every test opens its own board: no groups, nothing where it was left.

        **And the frames go with the membership.** Dropping `g` from the store leaves the previous
        test's `.gbox` on screen until something repaints, and a test asking the document for
        `.gbox .gcell` then reads a leftover instead of its own — which passed alone and failed in
        company (2026-09-19)."""
        self.b.ev("""(()=>{const P=window.palmar, L=P.layout();
          for (const t of P.tiles.values()) if (L[t.id]) {
            const n=Object.assign({},L[t.id]); delete n.g; delete n.gn; L[t.id]=n; }
          P.paintGroups();
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

    def grip(self, name):
        g = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name===%s);
          const r=t.el.querySelector('.grip').getBoundingClientRect();
          return {x:r.left+r.width/2, y:r.top+r.height/2};})()""" % json.dumps(name))
        return g["x"], g["y"]

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

    def test_moving_a_group_keeps_its_arrangement(self):
        """**Touching is the whole invariant.** Gravity — every member pulled up, then left — closed
        holes and also tidied groups nobody had asked it to: a window under a short neighbour, with
        nothing to its left at that height, slid left the first time the group was so much as moved
        (user, 2026-09-15: "벽끼리 맞닿아 있기만 하면 딱 좋은데 … 움직일 때 배치가 바뀌네"). A group
        whose members all touch is left exactly as it is: dragged by one member, every member moves by
        the same amount and nothing else changes."""
        self.bench("""put('g1', 40, 40, 240, 160);     // short, left
                      put('g2', 292, 40, 240, 300);    // tall, right
                      put('g3', 292, 352, 240, 160);   // under the tall one: touching it, nothing to its left
                      P.joinGroups(by('g1').id, by('g2').id); P.joinGroups(by('g3').id, by('g1').id); return 1;""")
        before = self.bench("return {a: at('g1'), b: at('g2'), c: at('g3')};")
        x, y = self.press("g1")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + 90 * i / 3, y=y + 60 * i / 3, buttons=1)
        self.send(type="mouseReleased", x=x + 90, y=y + 60, clickCount=1, buttons=0)
        time.sleep(0.6)
        after = self.bench("return {a: at('g1'), b: at('g2'), c: at('g3')};")
        for k in "abc":
            self.assertEqual([after[k][0] - before[k][0], after[k][1] - before[k][1]], [90, 60],
                             "%s did not simply move with the group: %r -> %r" % (k, before, after))

    def test_a_group_joins_as_the_group_you_carried(self):
        """**The mates come too.** Joining placed the window in the hand at the preview spot and left
        everyone else at the end of the drag, so carrying a pair onto an outside window put one of
        them beside the target and dropped the other where the hand happened to stop: a row came down
        as a stack, still grouped, so it read as the group rearranging itself (user, 2026-09-17:
        "좌우로 붙어있던게 움직이다보면 상하 배치로 바뀔 때도 있어"). The hand's window still lands
        exactly where its ghost was; the rest keep the shape they were picked up in."""
        self.bench("""put('g1',420,420,240,160); put('g2',672,420,240,160); put('g3',60,60,240,160);
                      P.joinGroups(by('g1').id, by('g2').id); return 1;""")
        before = self.bench("return {a: at('g1'), b: at('g2')};")
        spots = self.bench("""return ['left','right','above','below']
          .map((s) => P.joinBlock(by('g3').id, s, by('g1').id)).map((p) => [p.x, p.y]);""")
        x, y = self.press("g1")
        tx, ty = self.press("g3")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x + (tx - x) * i / 3, y=y + (ty - y) * i / 3, buttons=1)
        for _ in range(20):
            n = self.bench("return P.groupOf(by('g3').id).length;")
            if n == 3:
                break
            time.sleep(0.18)
            self.send(type="mouseMoved", x=tx + 1, y=ty, buttons=1)
        self.send(type="mouseReleased", x=tx + 1, y=ty, clickCount=1, buttons=0)
        time.sleep(0.8)
        after = self.bench("return {a: at('g1'), b: at('g2'), c: at('g3')};")
        self.assertEqual(self.bench("return P.groupOf(by('g1').id).length;"), 3,
                         "the hold did not join the group to the window it was held over")
        self.assertEqual([after["b"][0] - after["a"][0], after["b"][1] - after["a"][1]],
                         [before["b"][0] - before["a"][0], before["b"][1] - before["a"][1]],
                         "the two stopped standing side by side: %r -> %r" % (before, after))
        # Which side it latched onto is the hand's business (that is its own test); what matters here
        # is that the block landed against the target, on one of the four sides, and exactly. The
        # four are read **before** the drag: afterwards g3 is in the group, so the block is a
        # different shape and its own previews no longer describe the drop that happened. g1 is the
        # block's top-left corner here, so where the block went is where g1 went.
        self.assertIn(after["a"], spots,
                      "the block did not land against the target: %r, wanted one of %r" % (after, spots))

    def test_the_carried_group_does_not_leave_its_colour_behind(self):
        """The cells slide to their new place on a transition, which is right when a group is pushed
        aside and wrong under the hand: the windows have theirs switched off while they are dragged,
        so the tint hung a third of a second behind them, over whatever the group had just left
        (user, 2026-09-17). Three windows so that two of them make a group with a frame to watch."""
        self.bench("""put('g1',300,300,200,150); put('g2',512,300,200,150);
                      P.joinGroups(by('g1').id, by('g2').id); return 1;""")
        x, y = self.press("g1")
        self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
        for i in (1, 2, 3):
            self.send(type="mouseMoved", x=x - 40 * i, y=y - 30 * i, buttons=1)
        # Read it while the hand is still down: this is the only moment the lag existed in.
        mid = self.b.ev("""(()=>{const c=document.querySelector('.gbox .gcell');
          const t=[...window.palmar.tiles.values()].find(t=>t.s.name==='g1');
          const cr=c.getBoundingClientRect(), tr=t.el.getBoundingClientRect();
          return {glides: getComputedStyle(c).transitionProperty !== 'none',
                  dx: Math.round(tr.left - cr.left), dy: Math.round(tr.top - cr.top)};})()""")
        self.send(type="mouseReleased", x=x - 120, y=y - 90, clickCount=1, buttons=0)
        time.sleep(0.6)
        self.assertFalse(mid["glides"], "the tint was still on a transition while it was being carried")
        self.assertEqual([mid["dx"], mid["dy"]], [10, 10],
                         "the tint was not sitting under its own window mid-drag: %r" % (mid,))
        after = self.b.ev("""(()=>{const c=document.querySelector('.gbox .gcell');
          return getComputedStyle(c).transitionProperty !== 'none';})()""")
        self.assertTrue(after, "the tint never got its glide back after the drag")

    def test_a_drag_does_not_grind_the_group_out_of_line(self):
        """**A drag is a rigid translation, ten times over.** The user has a group that walks out of
        line over many drags — a row of two becoming a staircase (2026-09-17) — and this does not
        reproduce it: not here, not over other windows, not in and out of the viewport, not at 1.125.
        What it does hold down is the property that would have to break for that to happen: a hand
        moving a group changes no member's position relative to any other, and leaves none of them on
        a fraction of a pixel. It is a fence, not the catch. The catch is the watch behind
        `palmar.watchgroups`, which prints the stack of whatever really moves one member alone."""
        self.bench("""put('g1',200,200,220,150); put('g2',432,200,220,150);
                      P.joinGroups(by('g1').id, by('g2').id); return 1;""")
        before = self.bench("return {a: at('g1'), b: at('g2')};")
        for n in range(10):
            x, y = self.press("g1")
            self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
            for i in (1, 2, 3):
                self.send(type="mouseMoved", x=x + 7.37 * i / 3, y=y + 5.61 * i / 3, buttons=1)
            self.send(type="mouseReleased", x=x + 7.37, y=y + 5.61, clickCount=1, buttons=0)
            time.sleep(0.45)
            now = self.bench("return {a: at('g1'), b: at('g2')};")
            self.assertEqual([now["b"][0] - now["a"][0], now["b"][1] - now["a"][1]],
                             [before["b"][0] - before["a"][0], before["b"][1] - before["a"][1]],
                             "drag %d put the group out of line: %r" % (n + 1, now))
            for k in "ab":
                for v in now[k]:
                    self.assertEqual(v, int(v), "a window came to rest on half a pixel: %r" % (now,))

    def test_the_frame_follows_its_windows_past_the_origin(self):
        """The group's tint is drawn cell by cell, and each cell used to be pinned at zero — from when
        a window could not be carried past the origin. Once it could, the frame stopped following its
        own windows up and to the left and piled against the corner instead, where it read as having
        latched onto whatever window was sitting there (user, 2026-09-17)."""
        r = self.bench("""put('g1',-200,-150,240,160); put('g2',52,-150,240,160);
          P.joinGroups(by('g1').id, by('g2').id); P.sizeWorld(); P.paintGroups();
          return [...document.querySelectorAll('.gcell')]
            .map((c) => [parseFloat(c.style.left), parseFloat(c.style.top)])
            .sort((a, b) => a[0] - b[0]);""")
        self.assertEqual(r, [[-210, -160], [42, -160]],
                         "the tint did not go where its windows went: %r" % (r,))

    def test_growing_one_member_does_not_send_its_neighbour_down_the_diagonal(self):
        """**The way it grew wins a tie, not a landslide.** A resize tells push-aside which way the
        window grew, so that a neighbour a few pixels closer to the bottom than to the right does not
        go *under* a window that grew sideways — the row broken by the gesture meant to keep it. That
        preference was then taken as any distance at all: two grouped windows side by side, grow the
        left one **downward**, and three pixels of shared column become four hundred and sixteen down
        past the bottom it had just grown. Every further resize sent it down again (user, 2026-09-18,
        measured: 200 to 616). Three pixels out to the right was always there."""
        self.bench("""put('g1',200,200,300,200); put('g2',512,200,300,200);
                      P.joinGroups(by('g1').id, by('g2').id); return 1;""")
        rowY = self.bench("return at('g2')[1];")
        for grew, wide, tall in (("sideways", 120, 4), ("downward", 3, 200), ("downward again", 3, 150)):
            x, y = self.grip("g1")
            self.send(type="mousePressed", x=x, y=y, clickCount=1, buttons=1)
            for i in (1, 2, 3, 4):
                self.send(type="mouseMoved", x=x + wide * i / 4, y=y + tall * i / 4, buttons=1)
                time.sleep(0.03)
            self.send(type="mouseReleased", x=x + wide, y=y + tall, clickCount=1, buttons=0)
            time.sleep(1.2)
            now = self.bench("""const A=L[by('g1').id], B=L[by('g2').id];
              return {a:[A.x,A.y,A.w,A.h], b:[B.x,B.y,B.w,B.h]};""")
            self.assertEqual(now["b"][1], rowY,
                             "growing %s took the neighbour off the row: %r" % (grew, now))
            self.assertGreaterEqual(now["b"][0], now["a"][0] + now["a"][2],
                                    "the neighbour ended up on top of it: %r" % (now,))

    def test_gathering_pulls_them_together_until_they_touch(self):
        """Asked for after a session spent flinging windows about (2026-09-17). The first rule was
        "keep the arrangement, close the gaps", and squeezing each axis on its own left windows
        meeting only at a corner — closing a horizontal gap and a vertical one separately never makes
        two windows sit side by side. Looked at, it was asked to be tighter: "테트리스처럼 좌우 창들이
        맞닿게끔" (2026-09-20). Each block slides left until it meets something, then up.

        **A group is one thing that slides**, so the one arrangement that must not change does not."""
        self.bench("""put('g1',40,40,220,150); put('g2',272,40,220,150); put('g3',1400,1200,220,150);
                      P.joinGroups(by('g1').id, by('g2').id); P.paintTidy(); return 1;""")
        was = self.bench("return {a: at('g1'), b: at('g2'), c: at('g3')};")
        self.assertFalse(self.b.ev("document.getElementById('gather').disabled"),
                         "there are gaps and the button says there are not")
        self.b.ev("document.getElementById('gather').click()")
        time.sleep(1.0)
        now = self.bench("""const A=L[by('g1').id], B=L[by('g2').id], C=L[by('g3').id];
          const box=(p,q)=>Math.min(p.x+p.w,q.x+q.w)>Math.max(p.x,q.x) &&
                           Math.min(p.y+p.h,q.y+q.h)>Math.max(p.y,q.y);
          return {a:[A.x,A.y,A.w,A.h], b:[B.x,B.y,B.w,B.h], c:[C.x,C.y,C.w,C.h],
                  over: box(A,B)||box(A,C)||box(B,C)};""")
        self.assertFalse(now["over"], "gathering put windows on top of each other: %r" % (now,))
        self.assertEqual([now["b"][0] - now["a"][0], now["b"][1] - now["a"][1]],
                         [was["b"][0] - was["a"][0], was["b"][1] - was["a"][1]],
                         "the group's own arrangement changed: %r" % (now,))
        self.assertEqual(now["a"][:2], was["a"], "the corner-most window moved — everything comes to it")
        # **Touching, not merely nearer.** One GAP from the bottom of what is above it, or from the
        # right of what is beside it — a corner-to-corner finish is what this rule replaced.
        A, C = now["a"], now["c"]
        gaps = [C[1] - (A[1] + A[3]), C[0] - (now["b"][0] + now["b"][2])]
        self.assertIn(12, gaps, "it stopped short of touching anything: %r" % (now,))
        self.assertTrue(self.b.ev("document.getElementById('gather').disabled"),
                        "there is nothing left to close up and the button still offers to")

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

class TwoGroupsMeeting(unittest.TestCase):
    """Four windows, because the smallest case that goes wrong is a pair carried onto a pair.

    Its own board: the matrix below places all four itself for every one of the thirty-two runs, and
    a fourth window left lying around in Grouping would be one more thing for its pushes to find."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          for (const n of ['g1','g2','g3','g4'])
            await fetch('/api/sessions?token='+T,{method:'POST',
              headers:{'content-type':'application/json'},
              body:JSON.stringify({cwd:%s,name:n})});})()""" % json.dumps(cls.d.home))
        time.sleep(6)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def tearDown(self):
        self.assertEqual(self.b.errors(), [], "the page threw while being driven")

    JS = Grouping.JS

    def bench(self, body):
        return self.b.ev("(()=>{" + self.JS + "\n" + body + "})()")

    def test_two_groups_meeting_never_stack_a_window_on_a_window(self):
        """**Every way two pairs can meet.** Placing only the window in the hand put the one behind it
        exactly on top of the target's own mate — the whole window, 220x150 of it, in seven of these
        thirty-two (user, 2026-09-17: "묶은 직후 두 터미널이 완전히 겹쳐있는 경우도 있어"). Clearing
        only the target and not the mate standing behind it left two more. The push cannot save either
        one: it resolves what the *anchor* overlaps, and the anchor is the window that landed cleanly.
        Nothing overlaps, and the pair you carried is still the pair you carried."""
        W, H, G = 220, 150, 12
        for mine in ("row", "col"):
            for theirs in ("row", "col"):
                for target in ("g3", "g4"):
                    for side in ("left", "right", "above", "below"):
                        r = self.bench("""
                          const W=%d,H=%d,G=%d;
                          if ('%s'==='row') { put('g1',100,600,W,H); put('g2',100+W+G,600,W,H); }
                          else              { put('g1',100,600,W,H); put('g2',100,600+H+G,W,H); }
                          if ('%s'==='row') { put('g3',700,200,W,H); put('g4',700+W+G,200,W,H); }
                          else              { put('g3',700,200,W,H); put('g4',700,200+H+G,W,H); }
                          P.joinGroups(by('g1').id, by('g2').id);
                          P.joinGroups(by('g3').id, by('g4').id);
                          const rel = () => [L[by('g2').id].x - L[by('g1').id].x,
                                             L[by('g2').id].y - L[by('g1').id].y];
                          const was = rel();
                          P.dropInto(by('g1').id, by('%s').id, '%s');
                          P.settle(by('g1').id, {compact: false});
                          const ns = ['g1','g2','g3','g4'], over = [];
                          for (let i=0;i<4;i++) for (let j=i+1;j<4;j++) {
                            const p=L[by(ns[i]).id], q=L[by(ns[j]).id];
                            const w=Math.min(p.x+p.w,q.x+q.w)-Math.max(p.x,q.x);
                            const h=Math.min(p.y+p.h,q.y+q.h)-Math.max(p.y,q.y);
                            if (w>0 && h>0) over.push(ns[i]+'/'+ns[j]+' '+w+'x'+h);
                          }
                          return {over: over, kept: rel()[0]===was[0] && rel()[1]===was[1]};
                        """ % (W, H, G, mine, theirs, target, side))
                        where = "carrying a %s onto a %s, aiming %s of %s" % (mine, theirs, side, target)
                        self.assertEqual(r["over"], [], "windows ended up on top of each other — " + where)
                        self.assertTrue(r["kept"], "the pair you carried came apart — " + where)


class WhatAWindowDoesNotOwn(unittest.TestCase):
    """Its own board, because it tears a pane down and builds it again — which is what a reload does,
    and a bad neighbour to every test sharing a browser with it."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)
        cls.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          await fetch('/api/sessions?token='+T,{method:'POST',
            headers:{'content-type':'application/json'},
            body:JSON.stringify({cwd:%s,name:'g1'})});})()""" % json.dumps(cls.d.home))
        time.sleep(5)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def tearDown(self):
        self.assertEqual(self.b.errors(), [], "the page threw while being driven")

    JS = Grouping.JS

    def bench(self, body):
        return self.b.ev("(()=>{" + self.JS + "\n" + body + "})()")

    def test_placing_a_window_keeps_everything_it_does_not_own(self):
        """**Four times, the same trap.** Building a pane's frame writes its position and used to copy
        a *named list* of the other fields back, and the list is always one behind: the text size went
        first (a fresh open at the default while the store still held it), then group membership, then
        the group's name, which lasted until the next reload. Nothing is listed now — the entry is
        kept and only what that code knows is written over it. This test writes a field nobody has
        invented yet, which is the only way to check for the fifth time."""
        r = self.bench("""const t = by('g1'), id = t.id;
          L[id] = Object.assign({}, L[id], {g:'gkeep', gn:'이름', f:16, zz:'미래에 생길 것'});
          t.el.remove(); P.tiles.delete(id);
          P.upsert(t.s);                                  // rebuilt exactly as a reload rebuilds it
          const now = P.layout()[id] || {};
          return {g: now.g||null, gn: now.gn||null, f: now.f||null, zz: now.zz||null,
                  x: Number.isFinite(now.x), w: Number.isFinite(now.w)};""")
        self.assertTrue(r["x"] and r["w"], "it stopped writing the position it does own: %r" % (r,))
        self.assertEqual([r["g"], r["gn"], r["f"]], ["gkeep", "이름", 16],
                         "rebuilding the frame dropped a field it does not own: %r" % (r,))
        self.assertEqual(r["zz"], "미래에 생길 것",
                         "a field added later would be dropped — the list came back: %r" % (r,))

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
    """Eight palettes behind one theme button: System, then one per side under Light and Dark
    (2026-09-15: "theme 으로 바꾸고 누르면 리스트 나오고 system light dark 대분류 해서 안에서 선택").
    Picking under a side sets that theme and that palette; the attribute carries only the palette in
    effect, so a light choice never leaks into the dark theme."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def state(self):
        return self.b.ev("""(()=>{const root=document.documentElement;
          return {theme: root.dataset.theme || null, pal: window.palmar.palette(),
                  accent: getComputedStyle(root).getPropertyValue('--accent').trim(),
                  open: !document.getElementById('theme-menu').hidden,
                  on: [...document.querySelectorAll('#theme-menu .tm-i.on')].map(b=>b.dataset.pick)};})()""")

    def pick(self, v):
        self.b.ev("document.getElementById('theme').click()")
        self.assertTrue(self.state()["open"], "the list did not open")
        self.b.ev("document.querySelector('#theme-menu [data-pick=%s]').click()" % json.dumps(v))

    def test_the_list_sets_theme_and_palette_together(self):
        self.pick("light:sky")
        st = self.state()
        self.assertEqual([st["theme"], st["pal"], st["accent"], st["open"]], ["light", "sky", "#2f6fed", False], st)
        self.assertEqual(st["on"], ["light:sky"], "the list does not mark what is in effect: %r" % st)
        self.pick("dark:graphite")
        st = self.state()
        self.assertEqual([st["theme"], st["pal"], st["accent"]], ["dark", "graphite", "#7cb3ff"], st)
        self.pick("system")
        st = self.state()
        self.assertIsNone(st["theme"], "system did not clear the explicit theme: %r" % st)
        self.assertEqual(st["on"], ["system"])
        kept = self.b.ev("[localStorage.getItem('palmar.pal-light'), localStorage.getItem('palmar.pal-dark'), localStorage.getItem('palmar-theme')]")
        self.assertEqual(kept, ["sky", "graphite", None], "the choices were not kept the way the theme is")

    def test_a_choice_survives_a_reload(self):
        self.pick("light:lilac")
        self.b.ev("location.reload()")
        for _ in range(40):
            time.sleep(0.25)
            if self.b.ev("!!(window.palmar && window.palmar.palette)"):
                break
        st = self.state()
        self.assertEqual([st["theme"], st["pal"], st["accent"]], ["light", "lilac", "#6d4de6"], st)

    def test_escape_and_a_click_outside_close_it(self):
        self.b.ev("document.getElementById('theme').click()")
        self.assertTrue(self.state()["open"])
        self.b.ws.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        self.assertFalse(self.state()["open"], "Escape did not close the list")
        self.b.ev("document.getElementById('theme').click()")
        self.b.ev("document.body.click()")
        self.assertFalse(self.state()["open"], "a click outside did not close the list")


class TopRow(unittest.TestCase):
    """One row up top (2026-09-15). The canvas tabs used to sit on a bar of their own over the canvas
    while the top bar spent its middle on a search box; the tabs moved up, the search went behind
    Ctrl/⌘K, the ? became an options list with the shortcuts as one item, and the canvas took the
    difference. The buttons in that row are the same 26px box as everywhere else."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def test_the_tabs_are_in_the_top_row_and_the_canvas_starts_right_under_it(self):
        r = self.b.ev("""(()=>{const top=document.querySelector('.top').getBoundingClientRect();
          const tabs=document.getElementById('tabs').getBoundingClientRect();
          const cv=document.getElementById('cv').getBoundingClientRect();
          return {tabsInTop: tabs.top >= top.top && tabs.bottom <= top.bottom + 1,
                  gap: Math.round(cv.top - top.bottom), topH: Math.round(top.height),
                  rects: {top: [top.top, top.bottom], tabs: [tabs.top, tabs.bottom], scrollY: window.scrollY,
                          c: (()=>{const r=document.querySelector('.top .c').getBoundingClientRect(); return [r.top, r.bottom];})()},
                  search: !!document.querySelector('.top .search')};})()""")
        self.assertTrue(r["tabsInTop"], "the tab strip is not in the top row: %r" % r)
        self.assertLessEqual(r["gap"], 1, "something still sits between the top row and the canvas: %r" % r)
        self.assertEqual(r["topH"], 36)
        self.assertFalse(r["search"], "the search box is still on the bar")

    def test_the_buttons_up_there_are_not_tiny(self):
        r = self.b.ev("""(()=>{const h=(id)=>Math.round(document.getElementById(id).getBoundingClientRect().height);
          return {undo: h('undo'), tidy: h('tidy'), options: h('options'),
                  tab: parseFloat(getComputedStyle(document.querySelector('.tab')).fontSize)};})()""")
        for k in ("undo", "tidy", "options"):
            self.assertGreaterEqual(r[k], 26, "%s is %spx tall" % (k, r[k]))
        self.assertGreaterEqual(r["tab"], 13, "the canvas tab text is %spx" % r["tab"])

    def test_the_row_is_drawings_and_every_one_of_them_has_a_name(self):
        """undo was the only word in a row of drawings, so it read as a different kind of control
        (user, 2026-09-20). It is a drawing now — which means the name it used to carry on its face
        has to be carried somewhere a screen reader still finds it."""
        r = self.b.ev("""(()=>{const out={};
          for (const id of ['undo','tidy','gather','fit']) {
            const b = document.getElementById(id);
            out[id] = {svg: !!b.querySelector('svg'), text: b.textContent.trim(),
                       name: b.getAttribute('aria-label') || b.title};
          }
          return out;})()""")
        for k, v in r.items():
            self.assertTrue(v["svg"], "%s is not a drawing: %r" % (k, v))
            self.assertEqual(v["text"], "", "%s still shows a word: %r" % (k, v))
            self.assertTrue(v["name"], "%s has no name for a screen reader: %r" % (k, v))

    def test_the_options_list_holds_the_shortcuts_and_the_settings(self):
        r = self.b.ev("""(()=>{document.getElementById('options').click();
          const m=document.getElementById('options-menu');
          const out={open: !m.hidden, has: ['pushaside','autotidy','holdstyle'].map(id=>!!m.querySelector('#'+id))};
          m.querySelector('[data-do="keys"]').click();
          out.keys = !document.getElementById('keys').hidden; out.closed = m.hidden;
          out.inKeys = !!document.querySelector('#keys #holdstyle');
          return out;})()""")
        self.assertTrue(r["open"], "the options list did not open")
        self.assertEqual(r["has"], [True, True, True], "a setting is missing from the list: %r" % r)
        self.assertTrue(r["keys"], "the shortcuts item did not open the shortcuts")
        self.assertTrue(r["closed"], "picking an item left the list open")
        self.assertFalse(r["inKeys"], "the settings are still under the shortcuts too")
        self.b.ev("document.getElementById('keys-x').click()")

    def test_an_unnamed_pane_is_called_by_what_runs_in_it(self):
        """A name a person gave is never overwritten (⑫); an unnamed pane says what is in front."""
        r = self.b.ev("""(()=>{const L=window.palmar.labelOf;
          return [L({name:null, cwd:'/Users/x/work/palmar', fg:'claude'}),
                  L({name:null, cwd:'/Users/x/work/palmar', fg:null}),
                  L({name:'build', cwd:'/Users/x/work/palmar', fg:'claude'})];})()""")
        self.assertTrue(r[0].startswith("claude · "), "an unnamed pane does not lead with the command: %r" % r)
        self.assertNotIn("claude", r[1], "a pane at a prompt is called by a command: %r" % r)
        self.assertEqual(r[2], "build", "a given name was overwritten: %r" % r)

    def test_the_window_says_what_runs_in_it(self):
        """End to end: start something in a pane and its title and its list row say so — the daemon
        names the foreground command and the page shows it on an unnamed pane."""
        if sys.platform == "win32":
            self.skipTest("Windows has no foreground process group — the title is the way in (#30)")
        from tests.helpers import WS
        s = self.d.open_pane(self.d.home, canvas=self.b.ev("window.palmar.canvas()"))
        for _ in range(40):
            time.sleep(0.25)
            if self.b.ev("[...window.palmar.tiles.values()].some(t=>t.id===%s)" % json.dumps(s["id"])):
                break
        w = WS(self.d, "/pty/%s?token=%s&cols=80&rows=24" % (s["id"], self.d.token))
        self.addCleanup(w.close)
        w.recv_json()
        w.send(b"sleep 30\r", opcode=0x2)
        seen = None
        for _ in range(60):
            time.sleep(0.25)
            seen = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.id===%s);
              const row=[...document.querySelectorAll('.ses')].find(r=>r.textContent.includes('sleep'));
              return {title: t ? t.nameEl.textContent : null, row: !!row};})()""" % json.dumps(s["id"]))
            if seen["title"] and seen["title"].startswith("sleep"):
                break
        self.assertTrue(seen and seen["title"].startswith("sleep"), "the window never said sleep was running: %r" % seen)
        self.assertTrue(seen["row"], "the list row does not say it")
        w.send(b"\x03", opcode=0x2)
        self.d.delete("/api/sessions/" + s["id"])

    def test_every_button_up_there_is_the_same_height(self):
        r = self.b.ev("""(()=>{const out={};
          for (const id of ['bell','theme','toweb','undo','tidy','options','mkdir','refresh'])
            out[id]=Math.round(document.getElementById(id).getBoundingClientRect().height);
          return out;})()""")
        self.assertEqual(set(r.values()), {26}, "the buttons do not share one height: %r" % r)

    def test_the_right_hand_buttons_never_sit_on_undo_and_tidy(self):
        """Dragging the rails narrow used to spill the right-hand buttons over undo and tidy: the
        top bar's right column was the rail's width and its content is wider than that."""
        r = self.b.ev("""(()=>{const root=document.documentElement;
          root.style.setProperty('--rail-l','120px'); root.style.setProperty('--rail-r','120px');
          const t=document.getElementById('tidy').getBoundingClientRect(), b=document.getElementById('bell').getBoundingClientRect();
          root.style.removeProperty('--rail-l'); root.style.removeProperty('--rail-r');
          return {tidyRight: Math.round(t.right), bellLeft: Math.round(b.left)};})()""")
        self.assertLessEqual(r["tidyRight"], r["bellLeft"], "the buttons overlap: %r" % r)

    def test_the_search_has_a_way_out_and_the_item_is_a_switch(self):
        self.b.ev("document.getElementById('options').click(); document.querySelector('#options-menu [data-do=\"search\"]').click()")
        self.assertFalse(self.b.ev("document.getElementById('searchbox').hidden"), "the item did not open it")
        self.b.ev("document.getElementById('search-x').click()")
        self.assertTrue(self.b.ev("document.getElementById('searchbox').hidden"), "the × did not close it")
        self.b.ev("document.getElementById('options').click(); document.querySelector('#options-menu [data-do=\"search\"]').click()")
        self.b.ev("document.getElementById('options').click(); document.querySelector('#options-menu [data-do=\"search\"]').click()")
        self.assertTrue(self.b.ev("document.getElementById('searchbox').hidden"), "the item pressed again did not close it")

    def test_the_web_box_header_is_not_clipped(self):
        self.b.ev("document.getElementById('toweb').click()")
        time.sleep(0.3)
        r = self.b.ev("(()=>{const h=document.querySelector('#webbox .keys-h'); return {sw:h.scrollWidth, cw:h.clientWidth, txt:h.textContent.trim()};})()")
        self.assertLessEqual(r["sw"], r["cw"] + 1, "the header is clipped: %r" % r)
        self.b.ev("document.body.click()")

    def test_a_carried_tab_slides_its_neighbours_and_lands_once(self):
        """Like a browser's strip: the carried tab follows the finger, the others slide out of its way,
        and the order changes once, on release (user, 2026-09-15). It used to reorder the DOM on every
        move, a slot at a time as the finger crossed a midpoint."""
        self.b.ev("""(async()=>{const T=window.PALMAR_TOKEN;
          await fetch('/api/canvases?token='+T,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:'zeta'})});})()""")
        for _ in range(40):
            time.sleep(0.2)
            if self.b.ev("document.querySelectorAll('#tabs .tab').length") >= 2:
                break
        names = lambda: self.b.ev("[...document.querySelectorAll('#tabs .tab .nm')].map(e=>e.textContent)")
        before = names()
        first = self.b.ev("(()=>{const r=document.querySelector('#tabs .tab').getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+r.height/2, w:r.width};})()")
        last = self.b.ev("(()=>{const t=[...document.querySelectorAll('#tabs .tab')].pop(); const r=t.getBoundingClientRect(); return {x:r.right-4, y:r.top+r.height/2};})()")
        send = lambda **kw: self.b.ws.call("Input.dispatchMouseEvent", dict(button="left", **kw))
        send(type="mousePressed", x=first["x"], y=first["y"], clickCount=1, buttons=1)
        for i in (1, 2, 3, 4):
            send(type="mouseMoved", x=first["x"] + (last["x"] - first["x"]) * i / 4, y=first["y"], buttons=1)
        time.sleep(0.25)
        mid = self.b.ev("""(()=>{const tabs=[...document.querySelectorAll('#tabs .tab')];
          return {order: tabs.map(t=>t.querySelector('.nm').textContent),
                  carried: tabs[0].style.transform, slid: tabs.slice(1).map(t=>t.style.transform)};})()""")
        self.assertEqual(mid["order"], before, "the DOM was reordered in the middle of the drag: %r" % mid)
        self.assertIn("translateX", mid["carried"], "the carried tab does not follow the finger: %r" % mid)
        self.assertTrue(any("translateX(-" in x for x in mid["slid"]), "no neighbour slid out of the way: %r" % mid)
        send(type="mouseReleased", x=last["x"], y=first["y"], clickCount=1, buttons=0)
        time.sleep(0.8)
        after = names()
        self.assertEqual(after[-1], before[0], "the carried tab did not land at the end: %r -> %r" % (before, after))
        self.assertEqual(self.b.ev("[...document.querySelectorAll('#tabs .tab')].filter(t=>t.style.transform).length"), 0, "a transform was left behind")
        daemon = [c["name"] or None for c in self.d.get("/api/canvases")]
        self.assertEqual(daemon[-1], "zeta" if before[0] == "zeta" else daemon[-1], "the daemon was not told")

    def test_opening_one_popup_closes_the_others(self):
        r = self.b.ev("""(()=>{const h=(id)=>document.getElementById(id).hidden;
          document.getElementById('options').click();
          const a = !h('options-menu');
          document.getElementById('theme').click();
          const b = [h('options-menu'), !h('theme-menu')];
          document.getElementById('toweb').click();
          const c = [h('theme-menu'), !h('webbox')];
          document.body.click();
          return {a, b, c, end: h('webbox')};})()""")
        self.assertTrue(r["a"])
        self.assertEqual(r["b"], [True, True], "the theme button left the options list open: %r" % r)
        self.assertEqual(r["c"], [True, True], "the web button left the theme list open: %r" % r)
        self.assertTrue(r["end"])

    def test_a_narrow_rail_keeps_its_buttons(self):
        # 160px is the narrowest the right rail can be dragged (RAIL_MIN.r); below that the buttons cannot fit by arithmetic.
        r = self.b.ev("""(()=>{const root=document.documentElement; root.style.setProperty('--rail-r','160px');
          const rail=document.querySelector('.rail.right').getBoundingClientRect();
          const ok=(id)=>{const b=document.getElementById(id).getBoundingClientRect(); return b.width>=26 && b.right<=rail.right+1 && b.left>=rail.left-1;};
          const out={mkdir: ok('mkdir'), refresh: ok('refresh')};
          root.style.removeProperty('--rail-r'); return out;})()""")
        self.assertEqual(r, {"mkdir": True, "refresh": True}, "a rail header button is cut off or gone: %r" % r)

    def test_the_close_question_can_be_pressed(self):
        # On the canvas being viewed — a tab drag earlier in this class may have made another one current.
        s = self.d.open_pane(self.d.home, canvas=self.b.ev("window.palmar.canvas()"))
        for _ in range(40):
            time.sleep(0.25)
            if self.b.ev("[...window.palmar.tiles.values()].some(t=>t.id===%s)" % json.dumps(s["id"])):
                break
        self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.id===%s);
          t.el.querySelector('.tb .cl').click(); return 1;})()""" % json.dumps(s["id"]))
        time.sleep(0.3)
        r = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.id===%s);
          const b=t.el.querySelector('.cbtn'); if (!b) return null;
          const cs=getComputedStyle(b); const r=b.getBoundingClientRect();
          return {h: Math.round(r.height), fs: parseFloat(cs.fontSize), n: t.el.querySelectorAll('.cbtn').length,
                  connected: b.isConnected, disp: cs.display, tb: getComputedStyle(t.el.querySelector('.tb')).display};})()""" % json.dumps(s["id"]))
        self.assertIsNotNone(r, "no confirm strip appeared")
        self.assertGreaterEqual(r["h"], 26, "the buttons are still small: %r" % r)
        self.assertGreaterEqual(r["fs"], 12, "the text is still small: %r" % r)
        self.d.delete("/api/sessions/" + s["id"])

    def test_the_web_box_closes_like_the_other_popups(self):
        """It used to stay up until its × was found (user, 2026-09-15, on Windows)."""
        self.b.ev("document.getElementById('toweb').click()")
        time.sleep(0.3)
        self.assertFalse(self.b.ev("document.getElementById('webbox').hidden"), "the box did not open")
        self.b.ev("document.getElementById('webbox').click()")
        self.assertFalse(self.b.ev("document.getElementById('webbox').hidden"), "a click inside closed it")
        self.b.ev("document.body.click()")
        self.assertTrue(self.b.ev("document.getElementById('webbox').hidden"), "a click outside did not close it")
        self.b.ev("document.getElementById('toweb').click()")
        time.sleep(0.2)
        self.b.ws.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        self.assertTrue(self.b.ev("document.getElementById('webbox').hidden"), "Escape did not close it")

    def test_the_options_button_is_a_square(self):
        r = self.b.ev("(()=>{const b=document.getElementById('options').getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)];})()")
        self.assertEqual(r, [26, 26], "the options button is %r, not a 26px square" % (r,))

    def test_a_round_button_stands_in_for_the_folded_rail(self):
        """＋ lives in the terminal list's header now; fold that rail and a round button floats over the
        canvas in its place and does what Ctrl/⌘⏎ does (2026-09-15)."""
        shown = lambda: self.b.ev("getComputedStyle(document.getElementById('fab-new')).display !== 'none'")
        self.assertFalse(shown(), "the button shows while the rail is open")
        self.b.ev("document.getElementById('fold-l').click()")
        time.sleep(0.3)
        self.assertTrue(shown(), "the button did not appear when the rail folded")
        before = len(self.d.panes())
        self.b.ev("document.getElementById('fab-new').click()")
        for _ in range(30):
            time.sleep(0.3)
            if len(self.d.panes()) > before:
                break
        self.assertEqual(len(self.d.panes()), before + 1, "pressing it opened no terminal")
        self.b.ev("document.getElementById('open-l').click()")
        time.sleep(0.3)
        self.assertFalse(shown(), "the button stayed after the rail came back")

    def test_the_terminal_list_has_its_own_plus(self):
        before = len(self.d.panes())
        self.b.ev("document.getElementById('newterm').click()")
        for _ in range(30):
            time.sleep(0.3)
            if len(self.d.panes()) > before:
                break
        self.assertEqual(len(self.d.panes()), before + 1, "＋ in the terminal list opened nothing")

    def test_the_minimap_can_be_put_away(self):
        self.d.open_pane(self.d.home, canvas=self.b.ev("window.palmar.canvas()"))
        for _ in range(30):
            time.sleep(0.25)
            if not self.b.ev("document.getElementById('mm').hidden"):
                break
        self.assertFalse(self.b.ev("document.getElementById('mm').hidden"), "the minimap never showed with a window on the canvas")
        r = self.b.ev("""(()=>{const s=document.getElementById('minimap'); s.checked=false; s.dispatchEvent(new Event('change'));
          return {hidden: document.getElementById('mm').hidden, kept: localStorage.getItem('palmar.minimap'), on: window.palmar.minimapOn()};})()""")
        self.assertEqual([r["hidden"], r["kept"], r["on"]], [True, "0", False], r)
        self.b.ev("""(()=>{const s=document.getElementById('minimap'); s.checked=true; s.dispatchEvent(new Event('change')); return 1;})()""")
        self.assertFalse(self.b.ev("document.getElementById('mm').hidden"), "it did not come back")

    def test_search_is_behind_the_shortcut(self):
        self.assertTrue(self.b.ev("document.getElementById('searchbox').hidden"))
        mod = 4 if self.b.ev("navigator.platform.startsWith('Mac')") else 2      # ⌘ or Ctrl
        self.b.ws.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "k", "code": "KeyK", "modifiers": mod, "windowsVirtualKeyCode": 75})
        self.b.ws.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": "k", "code": "KeyK", "modifiers": mod, "windowsVirtualKeyCode": 75})
        time.sleep(0.2)
        r = self.b.ev("(()=>({shown: !document.getElementById('searchbox').hidden, focused: document.activeElement === document.getElementById('search')}))()")
        self.assertEqual([r["shown"], r["focused"]], [True, True], "Ctrl/⌘K did not bring the search up: %r" % r)
        self.b.ws.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27})
        time.sleep(0.2)
        self.assertTrue(self.b.ev("document.getElementById('searchbox').hidden"), "Escape did not put the search away")


class Viewing(unittest.TestCase):
    """The right rail is for looking at what is in a folder (2026-09-15, (가)): files are rows, a file
    opens in a window of its own on the canvas — the same frame as a terminal, on the same board — and
    terminals always open at home. The launcher ("Open terminal here") is gone."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.docs = os.path.join(cls.d.home, "docs")
        os.makedirs(cls.docs)
        with open(os.path.join(cls.docs, "notes.md"), "w", encoding="utf-8") as fh:
            fh.write("# 메모\nline two\nline three\n")
        cls.b = Browser().start()
        cls.b.open(cls.d.url)
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def row(self, name):
        return self.b.ev("""(()=>{const r=[...document.querySelectorAll('#tree .row')].find(r=>r.querySelector('.nm')&&r.querySelector('.nm').textContent===%s);
          return r ? {file: r.classList.contains('file'), path: r.dataset.path} : null;})()""" % json.dumps(name))

    def open_home(self):
        if self.row("docs"):
            return                      # already open — a click on ~ would fold it again
        for _ in range(40):             # the roots arrive after the page does
            if self.b.ev("[...document.querySelectorAll('#tree .row')].some(r=>r.querySelector('.nm').textContent==='~')"):
                break
            time.sleep(0.2)
        self.b.ev("""(()=>{const r=[...document.querySelectorAll('#tree .row')].find(r=>r.querySelector('.nm').textContent==='~'); r.click(); return 1;})()""")
        for _ in range(40):
            time.sleep(0.2)
            if self.row("docs"):
                return
        self.fail("home did not open")

    def open_docs(self):
        """Idempotent: a click on an open folder folds it, so only click when its file is not showing."""
        self.open_home()
        if self.row("notes.md"):
            return
        self.b.ev("""(()=>{[...document.querySelectorAll('#tree .row')].find(r=>r.querySelector('.nm').textContent==='docs').click(); return 1;})()""")
        for _ in range(40):
            time.sleep(0.2)
            if self.row("notes.md"):
                return
        self.fail("docs did not open")

    def test_a_folder_opens_on_a_click_and_lists_its_files(self):
        self.open_docs()
        r = self.row("notes.md")
        self.assertTrue(r and r["file"], "the file is not in the tree: %r" % r)
        self.assertFalse(self.b.ev("!!document.getElementById('launch')"), "the launcher is still there")

    def test_a_file_opens_in_a_window_on_the_canvas(self):
        self.open_docs()
        r = self.b.ev("""(()=>{const r=[...document.querySelectorAll('#tree .row.file')].find(r=>r.querySelector('.nm').textContent==='notes.md');
          r.click();
          // The tree is rebuilt on a click, so the marked row is the new element, not this one.
          const again = [...document.querySelectorAll('#tree .row.file')].find(x=>x.querySelector('.nm').textContent==='notes.md');
          const marked = again.classList.contains('sel'), opened = !!document.querySelector('.tile.viewer');
          again.dispatchEvent(new MouseEvent('dblclick', {bubbles: true}));
          return {marked, opened};})()""")
        self.assertTrue(r["marked"], "a click did not mark the file")
        self.assertFalse(r["opened"], "a single click opened a window — it should only mark (2026-09-15)")
        got = None
        for _ in range(40):
            time.sleep(0.2)
            got = self.b.ev("""(()=>{const v=document.querySelector('.tile.viewer'); if (!v) return null;
              const id=v.dataset.id; const P=window.palmar;
              return {inTiles: P.tiles.has(id), onBoard: !!P.layout()[id] && P.layout()[id].kind==='file',
                      title: v.querySelector('.tb .name').textContent, lines: v.querySelectorAll('.view .l').length,
                      first: (v.querySelector('.view .l .c')||{}).textContent};})()""")
            if got and got["lines"]:
                break
        self.assertIsNotNone(got, "no viewer window appeared")
        self.assertTrue(got["inTiles"] and got["onBoard"], "the viewer is not a window on the board: %r" % got)
        self.assertTrue(got["title"].startswith("notes.md"), got)
        self.assertEqual([got["lines"], got["first"]], [3, "# 메모"], got)

    def test_a_viewer_comes_back_after_a_reload(self):
        path = os.path.join(self.docs, "notes.md")
        self.b.ev("window.palmar.openViewer(%s); window.palmar.saveLayout(); 1" % json.dumps(path))
        vid = self.b.ev("window.palmar.viewerId(%s)" % json.dumps(path))
        for _ in range(40):                       # the save is debounced and then a round trip; reload only once the board has it
            time.sleep(0.2)
            if vid in self.d.get("/api/layout")["layout"]:
                break
        self.b.ev("location.reload()")
        for _ in range(40):
            time.sleep(0.25)
            if self.b.ev("!!(window.palmar && document.querySelector('.tile.viewer'))"):
                break
        self.assertTrue(self.b.ev("!!document.querySelector('.tile.viewer')"), "the viewer did not come back")
        # Paths on the board are the daemon's resolved ones (/private/var on a Mac); compare resolved.
        paths = [os.path.realpath(p) for p in self.b.ev("[...document.querySelectorAll('.tile.viewer')].map(v=>v.dataset.path)")]
        self.assertIn(os.path.realpath(path), paths, "the viewer that came back shows something else: %r" % paths)

    def test_dragging_a_file_onto_the_canvas_opens_it_where_it_was_dropped(self):
        """The other way in (user, 2026-09-15). The drop point is in canvas coordinates, so the window
        lands under the hand rather than in the first free slot."""
        self.open_docs()
        # A clean canvas: an earlier test may have left this file open, and then the drag would move
        # that window rather than open one.
        self.b.ev("[...window.palmar.tiles.values()].filter(t=>t.s.kind==='file').forEach(v=>v.close()); 1")
        r = self.b.ev("""(()=>{const row=[...document.querySelectorAll('#tree .row.file')].find(r=>r.querySelector('.nm').textContent==='notes.md');
          const dt = new DataTransfer();
          row.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
          const cv = document.getElementById('cv-scroll'), b = cv.getBoundingClientRect();
          const at = {x: b.left + 420, y: b.top + 300};
          cv.dispatchEvent(new DragEvent('dragover', {bubbles: true, dataTransfer: dt, clientX: at.x, clientY: at.y}));
          const lit = document.getElementById('cv').classList.contains('dropping');
          cv.dispatchEvent(new DragEvent('drop', {bubbles: true, dataTransfer: dt, clientX: at.x, clientY: at.y}));
          row.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt}));
          const v = document.querySelector('.tile.viewer');
          return {lit, opened: !!v, x: v ? v.offsetLeft : null, y: v ? v.offsetTop : null,
                  still: document.getElementById('cv').classList.contains('dropping')};})()""")
        self.assertTrue(r["lit"], "the canvas did not say it would take the file")
        self.assertTrue(r["opened"], "the drop opened nothing")
        self.assertFalse(r["still"], "the canvas is still lit after the drop")
        self.assertTrue(340 < r["x"] < 380 and 270 < r["y"] < 300,
                        "it did not land under the hand: %r" % ((r["x"], r["y"]),))

    def test_a_new_terminal_opens_at_home_whatever_is_focused(self):
        deep = os.path.join(self.d.home, "docs")
        s = self.d.open_pane(deep, canvas=self.b.ev("window.palmar.canvas()"))
        for _ in range(40):
            time.sleep(0.25)
            if self.b.ev("[...window.palmar.tiles.values()].some(t=>t.id===%s)" % json.dumps(s["id"])):
                break
        self.b.ev("(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.id===%s); t.el.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true})); return 1;})()" % json.dumps(s["id"]))
        before = {p["id"] for p in self.d.panes()}
        self.b.ev("document.getElementById('fab-new') && window.palmar.newTerminal(); 1")
        new = None
        for _ in range(40):
            time.sleep(0.25)
            fresh = [p for p in self.d.panes() if p["id"] not in before]
            if fresh:
                new = fresh[0]
                break
        self.assertIsNotNone(new, "no terminal opened")
        self.assertEqual(os.path.realpath(new["cwd"]), os.path.realpath(self.d.home), "it opened somewhere other than home: %r" % new["cwd"])


class TextSizeAndColours(unittest.TestCase):
    """The default text size is the person's (options list), the wheel moves a whole pixel a tick, and a
    light theme prints in dark ANSI colours — a program's "bright white" used to be white on white
    (user, 2026-09-15)."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def test_the_default_is_thirteen_and_the_options_list_changes_it(self):
        self.assertEqual(self.b.ev("window.palmar.baseFont()"), 13)
        r = self.b.ev("""(()=>{const s=document.getElementById('fontsize'); s.value='16';
          s.dispatchEvent(new Event('change'));
          return [window.palmar.baseFont(), localStorage.getItem('palmar.font')];})()""")
        self.assertEqual(r, [16, "16"])
        self.b.ev("window.palmar.setBaseFont(13)")

    def test_a_wheel_tick_is_a_whole_pixel(self):
        s = self.d.open_pane(self.d.home, canvas=self.b.ev("window.palmar.canvas()"))
        self.addCleanup(self.d.delete, "/api/sessions/" + s["id"])
        for _ in range(40):
            time.sleep(0.25)
            if self.b.ev("[...window.palmar.tiles.values()].some(t=>t.id===%s)" % json.dumps(s["id"])):
                break
        sizes = self.b.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.id===%s);
          const out=[t.term.options.fontSize];
          for (let i=0;i<3;i++) { t.el.dispatchEvent(new WheelEvent('wheel',{deltaY:-100, ctrlKey:true, bubbles:true, cancelable:true}));
                                  out.push(t.term.options.fontSize); }
          return out;})()""" % json.dumps(s["id"]))
        steps = [round(b - a, 3) for a, b in zip(sizes, sizes[1:])]
        self.assertEqual(steps, [1, 1, 1], "a tick is not a whole pixel: %r" % sizes)

    def test_a_light_theme_prints_in_dark_ink(self):
        r = self.b.ev("""(()=>{const root=document.documentElement;
          root.dataset.theme='light'; const light=window.palmar.termTheme();
          root.dataset.theme='dark';  const dark=window.palmar.termTheme();
          delete root.dataset.theme;
          return {light, dark};})()""")
        lum = lambda h: (lambda r, g, b: 0.2126 * r + 0.7152 * g + 0.0722 * b)(
            *[int(h.lstrip("#")[i:i+2], 16) / 255 for i in (0, 2, 4)])
        for key in ("brightWhite", "white", "foreground"):
            self.assertLess(lum(r["light"][key]), 0.35, "%s is too pale for a light theme: %s" % (key, r["light"][key]))
            self.assertGreater(lum(r["dark"][key]), 0.5, "%s is too dark for a dark theme: %s" % (key, r["dark"][key]))
        self.assertNotEqual(r["light"]["brightWhite"], r["dark"]["brightWhite"])


class ViewerModes(unittest.TestCase):
    """One window, several ways of showing a file (2026-09-15): text with line numbers, a table for
    separated values, a sandboxed frame for HTML, the browser's own viewer for a PDF — no library
    vendored for any of it. Text and Markdown can also be edited here; a save that would land on top of
    somebody else's write is refused."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.dir = os.path.join(cls.d.home, "files")
        os.makedirs(cls.dir)
        cls.write("notes.md", "# 메모\nsecond\n")
        cls.write("rows.csv", 'a,b\n1,"x,y"\n')
        cls.write("page.html", "<h1>hi</h1><script>window.parent.__pwned = 1</script>")
        cls.b = Browser().start()
        cls.b.open(cls.d.url)
        time.sleep(1.2)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    @classmethod
    def write(cls, name, text):
        p = os.path.join(cls.dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def open_file(self, name):
        path = os.path.join(self.dir, name)
        self.b.ev("[...window.palmar.tiles.values()].filter(t=>t.s.kind==='file').forEach(v=>v.close()); 1")
        self.b.ev("window.palmar.openViewer(%s); 1" % json.dumps(path))
        for _ in range(40):
            time.sleep(0.2)
            got = self.b.ev("""(()=>{const v=[...window.palmar.tiles.values()].find(t=>t.s.kind==='file');
              return v && v.mode && !v.termEl.textContent.startsWith('reading') ? v.mode : null;})()""")
            if got:
                return path
        self.fail("the viewer never loaded " + name)

    def test_a_file_url_becomes_a_path_on_either_platform(self):
        """Finder and Explorer hand a drag over as `text/uri-list`, which is the only part of a dropped
        file a page may see as a location at all — `dataTransfer.files` gives a name and bytes and no
        path, and the daemon opens files by path. Windows spells it `file:///C:/x`, a leading slash and
        forward slashes over a path that has neither, and a share is `file://server/share/x`.
        Percent-decoding is not optional: one space in a folder name and the path is wrong."""
        for uri, want in [
            ("file:///Users/x/a%20file.pdf", "/Users/x/a file.pdf"),
            ("file:///C:/Users/x/note.csv", "C:\\Users\\x\\note.csv"),
            ("file://server/share/report.pdf", "\\\\server\\share\\report.pdf"),
            ("file:///tmp/%ED%95%9C%EA%B8%80.txt", "/tmp/한글.txt"),
            ("https://example.com/x.pdf", None),          # not a file: nothing to open
            ("/plain/path.txt", "/plain/path.txt"),
        ]:
            got = self.b.ev("window.palmar.fileUrlToPath(%s)" % json.dumps(uri))
            self.assertEqual(got, want, "%s came out as %r" % (uri, got))
        two = self.b.ev("""(()=>{const dt={types:['text/uri-list'],
          getData:(t)=>t==='text/uri-list'?'file:///tmp/one.txt\\r\\n# comment\\r\\nfile:///tmp/two.pdf\\r\\n':''};
          return window.palmar.droppedPaths(dt);})()""")
        self.assertEqual(two, ["/tmp/one.txt", "/tmp/two.pdf"], "a uri-list of two: %r" % (two,))

    def test_a_file_dragged_in_from_the_desktop_opens_in_a_viewer(self):
        """Asked for 2026-09-18. **And the page must take the drop even when it cannot use it** — left
        to the browser, a file let go on a page that does not accept it is *navigated to*: palmar
        replaced by the PDF, and the board with it."""
        path = self.write("dragged in.csv", "a,b\n1,2\n")
        uri = "file://" + urllib.parse.quote(path)
        self.b.ev("[...window.palmar.tiles.values()].filter(t=>t.s.kind==='file').forEach(v=>v.close()); 1")
        r = self.b.ev("""(()=>{
          const S=document.getElementById('cv-scroll'), b=S.getBoundingClientRect();
          const dt=new DataTransfer(); dt.setData('text/uri-list', %s);
          const at={clientX:b.left+300, clientY:b.top+200, dataTransfer:dt, bubbles:true, cancelable:true};
          const over=new DragEvent('dragover', at); S.dispatchEvent(over);
          const drop=new DragEvent('drop', at); S.dispatchEvent(drop);
          return {over: over.defaultPrevented, drop: drop.defaultPrevented};})()""" % json.dumps(uri))
        self.assertTrue(r["over"], "the canvas did not offer to take the file")
        self.assertTrue(r["drop"], "the drop was left to the browser, which navigates away from palmar")
        got = None
        for _ in range(40):
            time.sleep(0.2)
            got = self.b.ev("""(()=>{const v=[...window.palmar.tiles.values()].find(t=>t.s.kind==='file');
              return v ? {path:(window.palmar.layout()[v.id]||{}).path, mode:v.mode||null} : null;})()""")
            if got and got["mode"]:
                break
        self.assertEqual(got and got["path"], path, "it did not open what was dropped: %r" % (got,))
        self.assertEqual(got["mode"], "csv", "it opened, but not as the kind of file it is: %r" % (got,))
        self.assertEqual(self.b.ev("location.pathname"), "/", "the page navigated away from palmar")

    def test_a_drop_with_no_path_in_it_is_found_on_disk(self):
        """**Windows, every time.** `text/uri-list` is not there and `dataTransfer.files` gives a name,
        a size, a modification time and the bytes — never a location (user, 2026-09-18). The bytes are
        what an upload wants, and an upload is not what this is: a viewer here is a window onto the
        file **on disk**, and text and Markdown save back to it, so a copy in a temporary folder would
        look identical and quietly stop being the file that was dropped. palmar goes and finds it by
        the three facts it was given. The drop built here carries no uri-list at all."""
        path = self.write("found by name.csv", "a,b\n1,2\n")
        mtime = int(os.path.getmtime(path) * 1000)
        self.b.ev("[...window.palmar.tiles.values()].filter(t=>t.s.kind==='file').forEach(v=>v.close()); 1")
        r = self.b.ev("""(()=>{
          const S=document.getElementById('cv-scroll'), b=S.getBoundingClientRect();
          const dt=new DataTransfer();
          dt.items.add(new File([%s], %s, {type:'text/csv', lastModified: %d}));
          const at={clientX:b.left+320, clientY:b.top+210, dataTransfer:dt, bubbles:true, cancelable:true};
          S.dispatchEvent(new DragEvent('dragover', at));
          const drop=new DragEvent('drop', at); S.dispatchEvent(drop);
          return {types:[...dt.types], uri:dt.getData('text/uri-list'), taken:drop.defaultPrevented};})()"""
          % (json.dumps("a,b\n1,2\n"), json.dumps("found by name.csv"), mtime))
        self.assertEqual(r["uri"], "", "this drop was supposed to carry no path — it proves nothing")
        self.assertTrue(r["taken"], "the drop was left to the browser, which navigates away")
        # **Wait for the file that was dropped, not for any file window.** Asking for the first one
        # open reads whatever a neighbouring test left behind (measured: 'text' from another file,
        # only inside a full run).
        got, seen = None, []
        for _ in range(60):
            time.sleep(0.2)
            seen = self.b.ev("""(()=>[...window.palmar.tiles.values()].filter(t=>t.s.kind==='file')
              .map(v=>({path:(window.palmar.layout()[v.id]||{}).path, mode:v.mode||null})))()""")
            got = next((v for v in seen if v["path"] and
                        os.path.realpath(v["path"]) == os.path.realpath(path)), None)
            if got and got["mode"]:
                break
        self.assertTrue(got, "the dropped file never opened — what did: %r" % (seen,))
        self.assertEqual(got["mode"], "csv", "it opened, but not as the kind of file it is: %r" % (got,))

    def test_while_it_looks_the_canvas_says_so(self):
        """Finding a dropped file means sweeping disks, which takes as long as it takes — and until it
        finished, letting go of a file did nothing you could see (user, 2026-09-18).

        **Watched, not sampled.** The first version polled every 100ms for the busy state and passed
        here, where a large home makes the search take seconds, while failing on all three CI runners,
        where an empty home finishes it before the first look (2026-09-19). How long the search takes
        is a property of the machine; that the canvas says so while it runs is a property of palmar.
        An observer set up before the drop records the states as they happen, so the assertion does
        not depend on catching one."""
        self.b.ev("[...window.palmar.tiles.values()].filter(t=>t.s.kind==='file').forEach(v=>v.close()); 1")
        self.b.ev("""(()=>{
          window.__seen = {busy:false, cursor:null, ring:false, turning:false, done:false,
                           still: matchMedia('(prefers-reduced-motion: reduce)').matches};
          const cv = document.getElementById('cv'), S = document.getElementById('cv-scroll');
          const look = () => {
            if (cv.classList.contains('finding')) {
              window.__seen.busy = true;
              window.__seen.cursor = getComputedStyle(S).cursor;
            } else if (window.__seen.busy) window.__seen.done = true;
            const sp = document.querySelector('.toast .spin');
            if (sp) {
              window.__seen.ring = true;
              window.__seen.turning = getComputedStyle(sp).animationName !== 'none';
            }
          };
          window.__mo = new MutationObserver(look);
          window.__mo.observe(document.body, {subtree:true, attributes:true, childList:true});
          return 1;})()""")
        self.b.ev("""(()=>{
          const S=document.getElementById('cv-scroll'), b=S.getBoundingClientRect();
          const dt=new DataTransfer();
          dt.items.add(new File(['x'], 'nowhere at all.csv', {type:'text/csv'}));
          const at={clientX:b.left+300, clientY:b.top+200, dataTransfer:dt, bubbles:true, cancelable:true};
          S.dispatchEvent(new DragEvent('dragover', at));
          S.dispatchEvent(new DragEvent('drop', at)); return 1;})()""")
        said = ""
        for _ in range(80):
            time.sleep(0.25)
            said = self.b.ev("(document.querySelector('.toast')||{}).textContent||''")
            if "was not found" in said:
                break
        seen = self.b.ev("(()=>{window.__mo.disconnect(); return window.__seen;})()")
        self.assertIn("nowhere at all.csv", said, "it never said how the search ended: %r" % (said,))
        self.assertTrue(seen["busy"], "nothing said the search was running: %r" % (seen,))
        self.assertEqual(seen["cursor"], "wait", "the canvas was busy and did not look it: %r" % (seen,))
        # **A ring either way.** The cursor set belongs to the platform; this one is ours, so it has to
        # be there on every machine. Whether it *turns* is the machine's business — under
        # prefers-reduced-motion it must stay a ring and stop, which is the promise the CSS makes, and
        # asserting the animation flatly failed on a macOS runner that has that setting on (2026-09-19,
        # the second time in two days a test of mine described this machine rather than palmar).
        self.assertTrue(seen["ring"], "no ring at all: %r" % (seen,))
        self.assertEqual(seen["turning"], not seen["still"],
                         "the ring turns when it should not, or the other way about: %r" % (seen,))
        self.assertTrue(seen["done"], "the busy state was never taken off again: %r" % (seen,))
        self.assertFalse(self.b.ev("document.getElementById('cv').classList.contains('finding')"),
                         "the busy cursor was left on after the search ended")

    def test_two_files_it_cannot_tell_apart_open_neither(self):
        """Same name, same bytes, same time in two places. Opening one of them silently is worse than
        opening nothing — the person is the only one who knows which they meant."""
        body = "same,file\n9,9\n"
        a = self.write("twin.csv", body)
        other = os.path.join(self.dir, "elsewhere")
        if not os.path.isdir(other):
            os.makedirs(other)
        b = os.path.join(other, "twin.csv")
        with open(b, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.utime(b, (os.path.getatime(a), os.path.getmtime(a)))
        self.b.ev("[...window.palmar.tiles.values()].filter(t=>t.s.kind==='file').forEach(v=>v.close()); 1")
        self.b.ev("""(()=>{
          const S=document.getElementById('cv-scroll'), b=S.getBoundingClientRect();
          const dt=new DataTransfer();
          dt.items.add(new File([%s], 'twin.csv', {type:'text/csv', lastModified: %d}));
          const at={clientX:b.left+320, clientY:b.top+210, dataTransfer:dt, bubbles:true, cancelable:true};
          S.dispatchEvent(new DragEvent('dragover', at));
          S.dispatchEvent(new DragEvent('drop', at)); return 1;})()"""
          % (json.dumps(body), int(os.path.getmtime(a) * 1000)))
        # The search has a deadline of its own and may sweep a drive before giving up, so wait for the
        # word rather than for a guess at how long it takes.
        said = ""
        for _ in range(60):
            time.sleep(0.25)
            said = self.b.ev("(document.querySelector('.toast')||{}).textContent||''")
            if "twin.csv" in said:
                break
        open_now = self.b.ev("[...window.palmar.tiles.values()].filter(t=>t.s.kind==='file').length")
        self.assertEqual(open_now, 0, "it guessed between two files it cannot tell apart")
        self.assertIn("twin.csv", said, "it opened nothing and said nothing: %r" % (said,))

    def test_text_shows_with_line_numbers_and_says_it_can_be_edited(self):
        # Its own file: the editing test writes notes.md, and these two share a browser.
        self.write("plain.md", "# 메모\nsecond\n")
        self.open_file("plain.md")
        r = self.b.ev("""(()=>{const v=[...window.palmar.tiles.values()].find(t=>t.s.kind==='file');
          return {mode: v.mode, lines: v.termEl.querySelectorAll('.l').length, canEdit: v.canEdit,
                  editShown: getComputedStyle(v.edEl).display !== 'none',
                  openShown: getComputedStyle(v.owEl).display !== 'none', stamp: !!v.mtime};})()""")
        self.assertEqual([r["mode"], r["lines"], r["canEdit"]], ["text", 2, True], r)
        self.assertTrue(r["editShown"] and r["openShown"], "the viewer's buttons are not shown: %r" % r)
        self.assertTrue(r["stamp"], "no stamp came with the file, so a save could not be checked")

    def test_a_separated_file_is_a_table(self):
        self.open_file("rows.csv")
        r = self.b.ev("""(()=>{const v=[...window.palmar.tiles.values()].find(t=>t.s.kind==='file');
          const cells=[...v.termEl.querySelectorAll('tr')].map(tr=>[...tr.children].map(c=>c.textContent));
          return {mode: v.mode, cells};})()""")
        self.assertEqual(r["mode"], "csv")
        self.assertEqual(r["cells"], [["", "a", "b"], ["1", "1", "x,y"]],
                         "a quoted comma did not stay in its cell: %r" % r["cells"])

    def test_html_renders_in_a_frame_that_cannot_reach_out(self):
        self.open_file("page.html")
        r = self.b.ev("""(()=>{const v=[...window.palmar.tiles.values()].find(t=>t.s.kind==='file');
          const f=v.termEl.querySelector('iframe');
          return {mode: v.mode, frame: !!f, sandbox: f && f.getAttribute('sandbox'), pwned: !!window.__pwned};})()""")
        self.assertEqual(r["mode"], "html")
        self.assertTrue(r["frame"], "no frame was made")
        self.assertEqual(r["sandbox"], "", "the frame is not sandboxed: %r" % r)
        time.sleep(0.5)
        self.assertFalse(self.b.ev("!!window.__pwned"), "the page's script ran in palmar's own origin")

    def test_editing_saves_and_a_clash_is_refused_then_can_be_forced(self):
        path = self.open_file("notes.md")
        self.b.ev("""(()=>{const v=[...window.palmar.tiles.values()].find(t=>t.s.kind==='file');
          v.toggleEdit(true); v.ta.value = 'edited by hand\\n'; v.ta.dispatchEvent(new Event('input')); return 1;})()""")
        self.assertTrue(self.b.ev("""(()=>{const v=[...window.palmar.tiles.values()].find(t=>t.s.kind==='file');
          return v.editing && v.dirty && !v.barEl.hidden;})()"""), "it did not go into editing")
        self.b.ev("[...window.palmar.tiles.values()].find(t=>t.s.kind==='file').save(); 1")
        for _ in range(40):
            time.sleep(0.2)
            with open(path, encoding="utf-8") as fh:
                if fh.read() == "edited by hand\n":
                    break
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "edited by hand\n", "the save did not reach the file")
        # Somebody else writes it, and the next save is refused with a way out.
        time.sleep(0.05)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("theirs\n")
        self.b.ev("""(()=>{const v=[...window.palmar.tiles.values()].find(t=>t.s.kind==='file');
          v.ta.value='mine\\n'; v.ta.dispatchEvent(new Event('input')); v.save(); return 1;})()""")
        bar = None
        for _ in range(40):
            time.sleep(0.2)
            bar = self.b.ev("""(()=>{const v=[...window.palmar.tiles.values()].find(t=>t.s.kind==='file');
              return {msg: v.barEl.textContent, buttons: [...v.barEl.querySelectorAll('.vb')].map(b=>b.textContent)};})()""")
            if bar["buttons"]:
                break
        self.assertIn("changed on disk", bar["msg"], bar)
        self.assertEqual(bar["buttons"], ["Reload", "Overwrite"], bar)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "theirs\n", "it was overwritten anyway")
        self.b.ev("""(()=>{const v=[...window.palmar.tiles.values()].find(t=>t.s.kind==='file');
          [...v.barEl.querySelectorAll('.vb')].find(b=>b.textContent==='Overwrite').click(); return 1;})()""")
        for _ in range(40):
            time.sleep(0.2)
            with open(path, encoding="utf-8") as fh:
                if fh.read() == "mine\n":
                    break
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "mine\n", "Overwrite did not force it through")


class Toasts(unittest.TestCase):
    """A toast is one line of prose. It used to be a flex row with a gap between its parts, and two
    plain strings in a row merged into one flex item with no gap at all — the notification toast read
    "…:57891the browser is refusing" (user, 2026-09-15). Now the parts are laid end to end with a space
    and the punctuation is the caller's."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.b = Browser().start()
        cls.b.open(cls.d.url)

    @classmethod
    def tearDownClass(cls):
        cls.b.stop()
        cls.d.stop()

    def test_every_part_is_a_word_apart_from_the_next(self):
        r = self.b.ev("""(()=>{window.palmar.toast(['blocked for http://x:1 —', 'the browser is refusing',
                          {d:'why'}, {b:'bold'}, {a:'undo', on:()=>{}}]);
          const t=document.querySelector('.toast');
          return {text:t.textContent, shown:t.classList.contains('show'), display:getComputedStyle(t).display};})()""")
        self.assertEqual(r["text"], "blocked for http://x:1 — the browser is refusing why bold undo")
        self.assertTrue(r["shown"])
        self.assertNotEqual(r["display"], "flex", "a flex toast merges adjacent text parts and loses the space")

    def test_an_empty_part_leaves_no_gap_behind(self):
        text = self.b.ev("window.palmar.toast(['a', '', null, 'b']); document.querySelector('.toast').textContent")
        self.assertEqual(text, "a b")


class ADifferentKeyIsADifferentDaemon(unittest.TestCase):
    """localStorage is per origin, and two daemons on one port are one origin. The browser used to
    keep a copy of the board as a hand-over for a daemon whose file was empty — and handed the board
    of the palmar on Windows, PDF window and all, to the fresh one started in WSL on the same port
    (user, 2026-09-15). A different key is a different daemon, and it starts with nothing."""

    def test_a_fresh_daemon_on_the_same_port_starts_with_nothing(self):
        first = Daemon().start()
        b = Browser().start()
        self.addCleanup(b.stop)
        b.open(first.url)
        time.sleep(1.0)
        path = os.path.join(first.home, "notes.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# hello\n")
        b.ev("window.palmar.openViewer(%s); 1" % json.dumps(path))
        for _ in range(30):
            time.sleep(0.2)
            if any(k.startswith("v:") for k in first.get("/api/layout")["layout"]):
                break
        else:
            first.stop()
            self.fail("the viewer never reached the first daemon's board")
        port = first.port
        first.stop()
        fresh = Daemon()
        fresh.port = port                      # the same origin, as far as the browser can tell
        fresh.start()
        self.addCleanup(fresh.stop)
        self.assertEqual(fresh.port, port, "the port was not free again; the test cannot say anything")
        b.open(fresh.url)
        time.sleep(1.5)
        r = b.ev("""(()=>{const P=window.palmar; return {
          viewers: [...P.tiles.values()].filter(t => t.s && t.s.kind === 'file').length,
          keys: Object.keys(P.layout()).filter(k => k.startsWith('v:')),
          copy: localStorage.getItem('palmar-tiles')};})()""")
        self.assertEqual(r["viewers"], 0, "a window from the previous daemon came back")
        self.assertEqual(r["keys"], [], "the previous daemon's board leaked through the browser")
        self.assertIsNone(r["copy"], "the browser still keeps a copy of the board")
        time.sleep(0.5)
        self.assertEqual([k for k in fresh.get("/api/layout")["layout"] if k.startswith("v:")], [],
                         "and it was pushed to the fresh daemon")


class InstallableAsAnApp(unittest.TestCase):
    """The page links its manifest itself, from the address it was opened with — so it can be
    installed as an app from Edge or Chrome while index.html carries no key (#14)."""

    def test_the_manifest_link_is_built_from_the_address(self):
        d = Daemon().start()
        self.addCleanup(d.stop)
        b = Browser().start()
        self.addCleanup(b.stop)
        b.open(d.url)
        time.sleep(0.8)
        key = d.url.split("k=", 1)[1]
        r = b.ev("""(async()=>{const l=document.querySelector('link[rel=manifest]');
          if(!l) return {href:null};
          const m=await (await fetch(l.href)).json(); return {href:l.href, start:m.start_url, display:m.display};})()""")
        self.assertEqual(r["href"], d.url.split("/?", 1)[0] + "/manifest.webmanifest?k=" + key)
        self.assertEqual(r["start"], "/?k=" + key)
        self.assertEqual(r["display"], "standalone")


class AWindowWithoutTheNotificationAPI(unittest.TestCase):
    """palmar's own window on a Mac is WKWebView, which has no Notification API — the bell used to
    say so and stop. Now the daemon notifies for it (POST /api/notify), when hello says it can."""

    def test_the_bell_turns_on_and_the_daemon_says_it(self):
        box = tempfile.mkdtemp(prefix="palmar-notify-")
        self.addCleanup(shutil.rmtree, box, ignore_errors=True)
        note = os.path.join(box, "said")
        say = os.path.join(box, "say.sh")
        with open(say, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%s\\n" "$@" > ' + note + '\n')
        os.chmod(say, 0o755)
        d = Daemon(env={"PALMAR_NOTIFIER": say}).start()
        self.addCleanup(d.stop)
        b = Browser().start()
        self.addCleanup(b.stop)
        b.open(d.url, script="delete window.Notification")     # what WKWebView looks like to the page
        time.sleep(1.0)
        self.assertFalse(b.ev("'Notification' in window"))
        b.ev("document.getElementById('bell').click(); 1")
        end = time.time() + 6
        while time.time() < end and not os.path.exists(note):
            time.sleep(0.1)
        self.assertTrue(os.path.exists(note), "the daemon was never asked to notify")
        with open(note) as fh:
            self.assertEqual(fh.read().split("\n")[0], "palmar notifications are on")
        self.assertEqual(b.ev("document.getElementById('bell').dataset.on"), "1", "the bell did not turn on")


class ComingForward(unittest.TestCase):
    """The daemon's focus event: a browser page calls window.focus(); palmar's own window is told over
    IPC, since a webview cannot raise itself from the page."""

    def test_the_page_asks_for_the_front_the_way_its_window_allows(self):
        d = Daemon().start()
        self.addCleanup(d.stop)
        b = Browser().start()
        self.addCleanup(b.stop)
        b.open(d.url, script="window.__focused=0; window.focus=()=>{window.__focused++};")
        time.sleep(0.8)
        d.raw("POST", "/api/focus")
        end = time.time() + 5
        while time.time() < end and not b.ev("window.__focused"):
            time.sleep(0.1)
        self.assertEqual(b.ev("window.__focused"), 1, "the page did not call window.focus()")
        b.ev("window.PALMAR_NATIVE={titlebar:true}; window.ipc={postMessage:(m)=>{window.__ipc=m}}; 1")
        d.raw("POST", "/api/focus")
        end = time.time() + 5
        while time.time() < end and not b.ev("window.__ipc"):
            time.sleep(0.1)
        self.assertEqual(b.ev("window.__ipc"), "focus", "palmar's own window must be told over IPC")
        self.assertEqual(b.ev("window.__focused"), 1, "and not window.focus() as well")
