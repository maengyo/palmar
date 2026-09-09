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
              return {search:[...k.querySelectorAll('kbd')].map(x=>x.textContent).join(''),
                      rows:[...document.querySelectorAll('.keys dt')].slice(0,4)
                        .map(d=>[...d.querySelectorAll('kbd')].map(x=>x.textContent).join('+'))};})()""")

    def test_a_mac_is_told_about_command(self):
        r = self.keys_for("MacIntel")
        self.assertEqual(r["search"], "⌘K")
        self.assertEqual(r["rows"][0], "⌘+⏎")
        # **Shift stays on new canvas.** It is what separates it from new terminal, on every
        # platform — the rule that turns Ctrl+Shift+C into ⌘C matched on shape and took it away,
        # leaving two identical rows and one of them wrong.
        self.assertEqual(r["rows"][1], "⌘+Shift+⏎")
        self.assertEqual(r["rows"][3], "⌘+C")

    def test_everywhere_else_is_told_about_ctrl(self):
        r = self.keys_for("Linux x86_64")
        self.assertEqual(r["search"], "CtrlK")
        self.assertEqual(r["rows"][:2], ["Ctrl+⏎", "Ctrl+Shift+⏎"])
        self.assertEqual(r["rows"][3], "Ctrl+Shift+C")


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


if __name__ == "__main__":
    unittest.main()
