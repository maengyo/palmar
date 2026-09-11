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
        nothing for STUCK_S — moved onto the pane's row in the left list (msgText)."""
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


if __name__ == "__main__":
    unittest.main()
