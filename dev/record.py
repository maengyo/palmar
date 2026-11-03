#!/usr/bin/env python3
"""The README recording, driven rather than filmed by hand.

There was no script before. The GIF in docs/img was made by driving a live daemon on 2026-09-15 and
whatever drove it was never committed (614250a), so nobody could take the same shot twice — and the
one we had did not show what palmar does. Measured 2026-09-16: 820x418, 30 frames, 7.35 seconds of
which only 3.2 move; terminal text 7.6px; and **not one green, amber or red pixel in the whole
thing** — all three panes recorded idle grey, so the status lights, which are the point, were absent.

This drives the real daemon and the real page over the DevTools protocol, the way tests/ already
does, and keeps the frames. Run it again after the UI changes and you get the same shots back.

    python3 dev/record.py                 # writes docs/img/palmar.gif and .mp4, and two stills
    python3 dev/record.py --out /tmp/x    # somewhere else, to look before replacing anything
    python3 dev/record.py --keep-frames   # leave the PNGs behind

**It never touches the real ~/.palmar.** tests/helpers.Daemon hands it a temporary HOME, which is
the only safe way to start a daemon here: the real one would rotate the token and log you out of the
session you are in. The scratch tree it browses is a temporary directory too.

**One pane runs a real `claude`** (2026-11-02, user: "클로드 실행하고 대기하는 모습도"). It is
asked to run `ls` with Bash, which it has to ask permission for, and that permission request is
what lights the pane amber — through the hook palmar attaches, the real path. The agent's own
config is used (CLAUDE_CONFIG_DIR, and ~/.claude.json copied into the scratch HOME so there is no
onboarding), the pane's environment is the clean one every pane gets, and `check_clean` reads the
screen for the username, the hostname, the home path and any e-mail address before a frame is taken.
The other two panes run a shell, and their lights come from the hook endpoint directly. The agent is
stopped at the end, and `pgrep` says whether it really is.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tests.cdp import Browser, chrome_path          # noqa: E402
from tests.helpers import Daemon, WS                # noqa: E402

# ── the shot ─────────────────────────────────────────────────────────────────
W, H = 1280, 720          # even height: h264 refuses an odd one
FPS_CAP = 12.0            # the pace the driver aims for; real timing is written per frame

#: **The group's colour is chosen here, not in the product.** `groupHue` (app.js) hashes the group
#: id to a hue, so a group's colour is whatever its id happens to give. The user wanted green for the
#: recording (2026-09-17) but green is a status-light colour, and AGENTS.md keeps those three for the
#: lights — so the product keeps its random hue and the recording picks an id that lands on green.
#: This one is shaped exactly like newGroupId()'s output and hashes to hue 140, which measures 1.40:1
#: against the dark canvas — the best of the greens, and better than the default would give.
GREEN_GROUP = "gmfl2k8p0002c"

#: The dark theme, set before the document runs — the page reads these at load time. The palette is
#: the warm default. **The font is the product's own 13px** (2026-11-02): it was bumped to 15 for
#: legibility, and with the windows of the time that made every pane a few big lines — "the terminals
#: are tiny and the font is huge" (user). Legibility comes from the windows being a working size
#: (below) and the GIF being scaled less, not from a font nobody runs.
PRELOAD = ("try{"
           "localStorage.setItem('palmar-theme','dark');"
           "localStorage.removeItem('palmar.font');"
           "}catch(e){}")

PDF = (b"%PDF-1.4\n"
       b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 420 300]"
       b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
       b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
       b"5 0 obj<</Length 150>>stream\n"
       b"BT /F1 22 Tf 40 230 Td (palmar) Tj ET\n"
       b"BT /F1 12 Tf 40 200 Td (A file opens in a window like any other.) Tj ET\n"
       b"BT /F1 12 Tf 40 180 Td (Text, a table, a page, a PDF.) Tj ET\n"
       b"endstream endobj\n"
       b"trailer<</Root 1 0 R/Size 6>>\n")


def tree(root: str) -> None:
    """A folder with something in it, so the right rail is not empty."""
    os.makedirs(os.path.join(root, "api"), exist_ok=True)
    os.makedirs(os.path.join(root, "infra"), exist_ok=True)
    with open(os.path.join(root, "handbook.pdf"), "wb") as fh:
        fh.write(PDF)
    with open(os.path.join(root, "api", "router.py"), "w") as fh:
        fh.write("def route(path):\n    return TABLE.get(path)\n")
    with open(os.path.join(root, "api", "TASK.md"), "w") as fh:
        fh.write("# retry the router\n\n- [x] read router.py\n- [x] add the backoff\n"
                 "- [ ] run the tests\n")
    with open(os.path.join(root, "infra", "deploy.yml"), "w") as fh:
        fh.write("service: palmar\nregion: local\n")


class Take:
    """One recording. Every captured frame carries the moment it was taken, so what comes out plays
    back at the speed it happened — a fixed frame rate would have made every gesture a guess."""

    def __init__(self, br: Browser, box: str):
        self.br, self.box, self.n, self.at = br, box, 0, []

    def frame(self) -> None:
        self.n += 1
        self.br.shot(os.path.join(self.box, "f%05d.png" % self.n))
        self.at.append(time.time())

    def hold(self, seconds: float) -> None:
        """Stay put, but keep filming — a still that is not filmed is a jump."""
        end = time.time() + seconds
        while time.time() < end:
            self.frame()
            left = end - time.time()
            if left > 0:
                time.sleep(min(left, 1.0 / FPS_CAP))

    def mouse(self, kind: str, x: float, y: float, **kw) -> None:
        self.br.ws.call("Input.dispatchMouseEvent",
                        dict(type=kind, x=x, y=y, button="left", **kw))

    def wheel(self, x, y, steps, dy, pause=0.0):
        """Ctrl and the wheel, filming every notch. **The real gesture** — calling `setZoom` would
        record the scale changing and not the thing a person does to change it."""
        for _ in range(steps):
            self.br.ws.call("Input.dispatchMouseEvent",
                            dict(type="mouseWheel", x=x, y=y, deltaX=0, deltaY=dy, modifiers=2))
            self.frame()
            time.sleep(1.0 / FPS_CAP)
        if pause:
            self.hold(pause)

    def drag(self, x0, y0, x1, y1, steps=14, alt=False, pause=0.0):
        """Press, travel, let go — filming every step.

        **The path is smooth.** An earlier take jittered every step by a few pixels, borrowing the rule
        that a browser *test* must not send the same coordinates twice; that rule is about tests, and
        here it only made the windows shiver — worst of all during the hold, where the picture is meant
        to be still while the gauge fills. Easing already makes every step a different number."""
        mods = 1 if alt else 0
        self.mouse("mousePressed", x0, y0, clickCount=1, buttons=1, modifiers=mods)
        self.frame()
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)                       # ease in and out
            self.mouse("mouseMoved", x0 + (x1 - x0) * e, y0 + (y1 - y0) * e,
                       buttons=1, modifiers=mods)
            self.frame()
        if pause:
            # **Hold completely still.** The page's hold runs on its own 16ms timer and reads where the
            # window is, not where the pointer went (app.js `hold`), so the gauge fills with the hand
            # resting — which is what holding a window over another actually looks like.
            end = time.time() + pause
            while time.time() < end:
                self.frame()
                time.sleep(1.0 / FPS_CAP)
        self.mouse("mouseReleased", x1, y1, clickCount=1, buttons=0, modifiers=mods)
        self.frame()

    def concat(self) -> str:
        """ffmpeg's concat list, with the real gap between each pair of frames."""
        path = os.path.join(self.box, "list.txt")
        with open(path, "w") as fh:
            for i in range(self.n):
                d = (self.at[i + 1] - self.at[i]) if i + 1 < self.n else 0.10
                fh.write("file 'f%05d.png'\nduration %.3f\n" % (i + 1, max(0.03, min(d, 0.5))))
            fh.write("file 'f%05d.png'\n" % self.n)       # the last one needs naming twice
        return path


def check_clean(br: Browser) -> None:
    """**Refuse to film anything that names the person running this.** The shell prompt carries the
    username and the machine's name by default, and the directory rail lists the real homes; both were
    in the first take (user, 2026-09-17). This reads what is actually on screen and stops if any of it
    is still there, rather than trusting that the two guards above did their job."""
    txt = br.ev("document.body.innerText") or ""
    home = os.path.expanduser("~")
    bad = []
    for name, value in (("username", os.environ.get("USER") or ""),
                        ("hostname", socket.gethostname().split(".")[0]),
                        ("home", home),
                        ("home's last part", os.path.basename(home.rstrip("/")))):
        if value and len(value) > 2 and value in txt:
            bad.append("%s (%r)" % (name, value))
    # An address of any kind. The agent's start-up screen is the one place one could appear
    # (2026-11-02), and nothing on this canvas has a reason to show one.
    if re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", txt):
        bad.append("an e-mail address")
    if bad:
        raise SystemExit("record: this would have filmed " + ", ".join(bad) +
                         " — fix that before recording")


def centre(br: Browser, name: str) -> dict:
    """Where a window's title bar is on screen right now."""
    return br.ev("""(()=>{const t=[...window.palmar.tiles.values()].find(t=>t.s.name===%s);
      if(!t) return null; const r=t.el.querySelector('.tb').getBoundingClientRect();
      return {x:r.left+r.width/2, y:r.top+r.height/2,
              cx:r.left+r.width/2, cy:r.top+r.height/2+80};})()""" % json.dumps(name))


def run(out_dir: str, keep: bool) -> int:
    if chrome_path() is None:
        print("record: no Chrome on this machine", file=sys.stderr)
        return 1
    if not shutil.which("ffmpeg"):
        print("record: no ffmpeg on this machine", file=sys.stderr)
        return 1

    # **A short scratch root.** The viewer's title bar shows a file's whole path, and macOS's default
    # temporary directory (/var/folders/wy/qxbzy0s13rb_…/T) put a line of noise across the picture.
    os.environ["TMPDIR"] = "/tmp"
    work = tempfile.mkdtemp(prefix="palmar-record-")
    frames = os.path.join(work, "frames")
    os.makedirs(frames)

    # **Nothing of the person running this goes into the picture** (user, 2026-09-17). zsh's default
    # prompt is `%n@%m %1~ %#` — the username and the machine's name, on every line of every pane. So
    # the panes here run `sh` with a prompt that says only where it is. `check_clean` below refuses to
    # go on if anything identifying reaches the screen anyway.
    # **The agent runs with the real HOME; the daemon does not.** The daemon's HOME is the scratch
    # one (its ~/.palmar must never be the real one). But `claude` keeps its login in the real
    # home — with a scratch HOME it opened on "Select login method" (measured 2026-11-02) — so the
    # pane is handed the real path in a variable and the command typed is `HOME=$REAL_HOME claude`:
    # the path never appears on screen, and the agent's screen is cleared by its own TUI anyway.
    d = Daemon(shell="/bin/sh",
               env={"PALMAR_UPDATE_CHECK": "0", "PS1": "$ ", "PROMPT": "$ ",
                    "REAL_HOME": os.path.expanduser("~")}).start()
    # **Inside the daemon's HOME.** A pane may only open somewhere under a root, and the roots are
    # this HOME — which is the temporary one tests/helpers.py made, not the real ~.
    proj = os.path.join(d.home, "work")
    tree(proj)
    br = Browser(width=W, height=H + 90, scrollbars=False).start()
    try:
        br.open(d.url, settle=1.0, script=PRELOAD)
        # Dark has to be told twice. localStorage gets palmar's own chrome; the emulated media query
        # is what reaches **Chrome's built-in PDF viewer**, whose toolbar otherwise stays light grey
        # and puts one white band in the middle of a dark recording (measured).
        br.ws.call("Emulation.setEmulatedMedia",
                   {"features": [{"name": "prefers-color-scheme", "value": "dark"}]})
        # Pin the picture. Without this the window furniture eats ~90px and the PNG comes out at an
        # odd height, which h264 refuses.
        br.ws.call("Emulation.setDeviceMetricsOverride",
                   {"width": W, "height": H, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(3.0)

        # **These run a shell.** Naming one of them after an agent would be dressing a shell up as
        # something it is not — and this picture goes on the README. The lights below are real: they
        # come down the hook endpoint, which is the path a real agent's hooks take.
        # The first pane has no name: it runs the agent, and the row and the title read what is in
        # front — "claude ~/work/api" — the way they do for anybody.
        a = d.open_pane(cwd=os.path.join(proj, "api"))
        b = d.open_pane(cwd=os.path.join(proj, "api"), name="sh · router")
        c = d.open_pane(cwd=os.path.join(proj, "infra"), name="sh · infra")
        time.sleep(2.0)

        for sid, line in (
                # Short lines that succeed. A long one wraps in the pane and the echoed command
                # becomes the noisiest thing on screen; one that errors is one nobody meant to show.
                (b["id"], "cat router.py"),
                (c["id"], "ls -1")):
            pty = WS(d, "/pty/%s?token=%s&cols=90&rows=24" % (sid, d.token))
            pty.recv_json()
            pty.send((line + "\r").encode(), opcode=0x2)
            time.sleep(0.4)
            pty.close()

        # Where the windows sit, and **the group's colour planted on one of them**. A group of one
        # draws nothing, so nothing shows yet — and the drag below inherits the id, which is how the
        # recording gets a green group out of a product that picks its own.
        # **The one that gets carried must land on one window and one only.** With the third window
        # close underneath, the carried one straddled both and the hold kept changing its mind about
        # which it was joining ("위쪽에 그룹핑 되다가 아래쪽 되다가", 2026-09-17). The two that join sit
        # side by side at the top; the third is well below, and the minimap owns the bottom-right.
        # **Working sizes** (2026-11-02): about 58×15 and 50×15 cells at 13px, which is a terminal
        # somebody would actually type in, instead of the 53×10 of before. The canvas at this width
        # holds two of them side by side and a third below, with the minimap's corner left free.
        d.raw("PUT", "/api/layout", {"layout": {
            a["id"]: {"x": 24, "y": 24, "w": 460, "h": 330, "z": 3, "g": GREEN_GROUP, "gn": "api"},
            b["id"]: {"x": 510, "y": 24, "w": 400, "h": 330, "z": 2},
            c["id"]: {"x": 24, "y": 390, "w": 460, "h": 250, "z": 1},
        }})
        time.sleep(1.5)

        # **The directory rail lists the real homes on this machine**, because `roots()` is $HOME plus
        # every home under /Users and /home that this account owns — so it names the person. Folded for
        # the recording. Everything the user asked to see (the lights, groups moving together, the
        # viewer) is on the canvas, not in that rail.
        br.ev("(()=>{const b=document.getElementById('fold-r'); if(b) b.click(); return 1;})()")
        time.sleep(1.2)
        check_clean(br)

        # **The real thing — started before a frame is taken.** `claude` in the first pane, up to its
        # prompt. The request it must ask permission for comes in ① below, on camera; the amber light
        # that follows comes down the hook palmar attached, not from this script.
        agent = WS(d, "/pty/%s?token=%s&cols=90&rows=24" % (a["id"], d.token))
        agent.recv_json()
        screen = lambda: br.ev("""(()=>{const t=window.palmar.tiles.get(%s); if(!t) return '';
          const b=t.term.buffer.active, o=[]; for (let i=0;i<b.length;i++){const l=b.getLine(i); if(l) o.push(l.translateToString(true));}
          return o.join('\\n');})()""" % json.dumps(a["id"]))
        # `--permission-mode default`, whatever this account's own default is: the point of the beat is
        # the permission request, and an account set to auto mode would run the command without one
        # (measured 2026-11-02: "auto mode on", straight to done).
        # The shim finds its hook settings under $HOME, and with HOME swapped it finds none (measured:
        # no hook arrived, the light read "done" under a permission prompt). So the file is named
        # outright — $HOME and $PALMAR_PANE expand in the pane's shell, and nothing personal is typed.
        agent.send(b'f="$HOME/.palmar/run/$PALMAR_PANE.json"; HOME=$REAL_HOME claude --settings "$f" --permission-mode default',
                   opcode=0x2)
        time.sleep(0.3); agent.send(b"\r", opcode=0x2)
        deadline = time.time() + 40
        trusted = False
        while time.time() < deadline:
            time.sleep(1.0)
            scr = screen()
            if "trust this folder" in scr.lower() and not trusted:
                # A folder it has not seen: the dialog's default is "No, exit" (measured 2026-11-02),
                # so Down first, then Enter. **Look before the Enter.** A Down sent while the dialog
                # was still being drawn was lost, the Enter took "No, exit", and the request typed
                # next went to the shell (measured 2026-11-03, one run in three). So: Down, then read
                # the screen until the marker sits on "Yes", and only then Enter — never twice.
                # And not one spelling of Down: the TUI may have turned application cursor keys on
                # (then Down is ESC O B, not ESC [ B), and one run in two the CSI form did nothing
                # four times over (measured 2026-11-03). Each form is tried and the screen read after.
                time.sleep(1.0)
                moved = False
                for key in (b"\x1b[B", b"\x1bOB", b"j", b"\x1b[B"):
                    agent.send(key, opcode=0x2); time.sleep(0.8)
                    # The dialog's marker is ❯ (U+276F); the prompt's is › (U+203A). Reading only
                    # the second sent every key in the list and the choice wrapped back to "No, exit"
                    # (measured 2026-11-03 — that was the one run in three).
                    marked = [l for l in screen().splitlines() if "\u276f" in l or "\u203a" in l]
                    if any("yes" in l.lower() for l in marked):
                        moved = True
                        break
                if not moved:
                    print("record: the trust dialog did not move to Yes — screen was:\n" + screen(), file=sys.stderr)
                trusted = True
                agent.send(b"\r", opcode=0x2); time.sleep(3.0); continue
            if "\u203a" in scr or "> " in scr or "Try " in scr:   # its prompt is up
                break
        time.sleep(1.5)
        check_clean(br)
        # A request it cannot satisfy with a read-only tool: listing a folder it did without asking
        # (measured — Glob needs no permission). Creating a file does need one, with Bash or Write.

        take = Take(br, frames)

        # ① the lights. **All three at once**, which is the whole point of them — one working, one
        # wanting you, one finished — and they arrive one at a time so you see them change. The
        # mapping is ⑥, settled as the code had it (2026-11-02).
        take.hold(1.4)
        d.raw("POST", "/hook/claude?pane=%s" % b["id"], {"hook_event_name": "UserPromptSubmit"})
        take.hold(1.1)
        d.raw("POST", "/hook/claude?pane=%s" % c["id"], {"hook_event_name": "Stop"})
        take.hold(1.1)
        # The agent is asked for something it needs permission for. The typing is on camera; the
        # thinking is not (a cut, rather than seconds of a still screen); the asking is.
        agent.send(b"Create an empty file named notes.txt in this folder.", opcode=0x2)
        time.sleep(0.4)
        agent.send(b"\r", opcode=0x2)
        take.hold(0.8)
        deadline = time.time() + 90
        while time.time() < deadline:
            time.sleep(1.0)
            rows = d.get("/api/sessions")
            rows = rows["sessions"] if isinstance(rows, dict) else rows
            if any(r["id"] == a["id"] and r["status"] == "waiting" for r in rows):
                break
        else:
            print("record: the agent never asked — screen was:\n" + screen(), file=sys.stderr)
        time.sleep(0.8)
        check_clean(br)
        take.hold(2.2)

        # **Two stills for the README** (2026-11-02), taken here where the picture is at its
        # clearest: three windows, all three lights, nothing mid-drag. Dark is what the recording
        # runs in; light is the other half of the product, and the page is told so twice for the same
        # reason the dark one was (the media query reaches the PDF viewer; the select reaches palmar).
        still = lambda name: open(os.path.join(out_dir, name), "wb").write(
            __import__("base64").b64decode(br.ws.call("Page.captureScreenshot", {"format": "png"})["data"]))
        os.makedirs(out_dir, exist_ok=True)
        still("palmar-dark.png")
        br.ev("(()=>{const s=document.getElementById('theme'); s.value='light:paper'; s.dispatchEvent(new Event('change')); return 1;})()")
        br.ws.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": "light"}]})
        time.sleep(0.6)
        still("palmar-light.png")
        br.ev("(()=>{const s=document.getElementById('theme'); s.value='dark:earth'; s.dispatchEvent(new Event('change')); return 1;})()")
        br.ws.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": "dark"}]})
        time.sleep(0.6)

        # ② carry one window onto another and hold: the gauge fills, and letting go joins them
        # Straight across, onto the one beside it. Nothing else is anywhere near the path.
        src = centre(br, "sh · router")
        if src:
            take.drag(src["x"], src["y"], src["x"] - 340, src["y"], steps=16, pause=1.6)
        take.hold(1.2)

        # ③ a group travels together, and Alt takes one back out of it
        grp = centre(br, "sh · api")
        if grp:
            take.drag(grp["x"], grp["y"], grp["x"] + 200, grp["y"] + 40, steps=14)
        take.hold(0.9)
        one = centre(br, "sh · router")
        if one:
            take.drag(one["x"], one["y"], one["x"] + 30, one["y"] + 260, steps=14, alt=True)
        take.hold(1.4)

        # ④ a file opens in a window like any other
        br.ev("window.palmar.openViewer(%s, null, {x: 300, y: 40})"
              % json.dumps(os.path.join(proj, "handbook.pdf")))
        take.hold(2.4)

        # ⑤ **stand back.** The canvas is bigger than the screen and the README says so; the picture
        # did not. Ctrl and the wheel over the bare floor — the gesture itself, over a point with no
        # window under it, because that is the only place it takes. It draws the windows smaller and
        # tells no terminal anything, which is the half of it a still frame cannot say.
        bare = br.ev("""(()=>{const s=document.getElementById('cv-scroll');
          const b=s.getBoundingClientRect();
          // Low and to the left of the minimap: empty floor in every take so far.
          const x=b.left+90, y=b.bottom-70;
          return document.elementFromPoint(x,y)===s ? {x:x,y:y} : null;})()""")
        # **Three notches, not seven.** Seven took it to the floor of the scale (0.2) and the windows
        # became specks in a corner — a picture of empty canvas. Three lands near 0.55, which is far
        # enough back to hold all of it and close enough to still read the lights.
        #
        # **And the way home is the button, not the wheel back.** Wheeling back holds the point under
        # the pointer, and the pointer has to be on bare floor for the gesture to take at all — so it
        # came home centred on empty canvas with the windows out of frame, which is a poor last thing
        # to show anybody. The button lands on the window you were working in.
        if bare:
            take.wheel(bare["x"], bare["y"], steps=3, dy=60, pause=1.5)
            br.ev("(()=>{document.getElementById('fit').click(); return 1;})()")
            take.hold(1.6)
        else:
            take.hold(1.0)

        # ⑥ **a second canvas, and the tabs between them** (user, 2026-11-03). Made through the
        # daemon rather than the + button, which opens an inline rename nobody wants to watch. The
        # infra window moves over so both canvases have something in them, then the tabs are
        # pressed — the pill slides to the pressed one — there and back.
        cv = d.post("/api/canvases", {"name": "infra"})
        if isinstance(cv, dict) and cv.get("id"):
            d.raw("PATCH", "/api/sessions/%s" % c["id"], {"canvas": cv["id"]})
            # A window keeps its board position across canvases, and the group drag above had carried
            # this one to the right edge — on the new canvas it sat mostly off-screen (measured). Put
            # it where the eye lands.
            lay = d.get("/api/layout")
            lay = lay.get("layout", lay) if isinstance(lay, dict) else {}
            lay[c["id"]] = dict(lay.get(c["id"], {}), x=40, y=40, w=460, h=250)
            d.raw("PUT", "/api/layout", {"layout": lay})
            take.hold(1.0)
            tab = lambda cid: br.ev("(()=>{const t=document.querySelector('.tab[data-id=%s]'); if(!t) return 0; t.click(); return 1;})()" % json.dumps(cid))
            tab(cv["id"])
            take.hold(1.8)
            first = br.ev("(()=>{const t=document.querySelector('.tab'); return t ? t.dataset.id : null;})()")
            if first:
                tab(first)
            take.hold(1.4)

        # ⑦ **the left rail folds and comes back** (user, 2026-11-03): the column shrinks to the strip
        # while the list leaves, the chevron riding down to the strip's spot; the strip brings it all
        # back. The right rail stays folded (it names the person's homes).
        br.ev("(()=>{const b=document.getElementById('fold-l'); if(b) b.click(); return 1;})()")
        take.hold(1.6)
        br.ev("(()=>{const b=document.getElementById('open-l'); if(b) b.click(); return 1;})()")
        take.hold(1.8)

        # ⑧ **a terminal says something, and the bell keeps it** (2026-11-02). What a script that
        # watches for a review request does: `palmar --message …`. The card by the bell, then the
        # list behind the bell with the address already a link — the half of the product that is for
        # coming back to it.
        d.raw("PATCH", "/api/sessions/%s" % c["id"],
              {"message": "review requested  example/infra#48  https://github.com/example/infra/pull/48"})
        take.hold(2.2)
        br.ev("(()=>{document.getElementById('bell').click(); return 1;})()")
        take.hold(2.0)
        br.ev("(()=>{document.body.click(); return 1;})()")
        take.hold(0.6)

        # Stop the agent the way a person does — Ctrl-C twice — then leave its shell. A pane's shell
        # gets SIGHUP on stop; an agent in alt-screen did not always go with it (measured).
        agent.send(b"\x1b", opcode=0x2); time.sleep(0.6)        # Esc: out of the permission dialog first
        agent.send(b"\x03", opcode=0x2); time.sleep(0.6)
        agent.send(b"\x03", opcode=0x2); time.sleep(2.5)
        agent.send(b"exit\r", opcode=0x2); time.sleep(0.5)
        agent.close()

        errs = br.errors()
        if errs:
            print("record: the page complained — " + "; ".join(errs[:3]), file=sys.stderr)

        lst = take.concat()
        os.makedirs(out_dir, exist_ok=True)
        mp4 = os.path.join(out_dir, "palmar.mp4")
        gif = os.path.join(out_dir, "palmar.gif")
        run_ff = lambda *a: subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)
        run_ff("-f", "concat", "-safe", "0", "-i", lst,
               "-vf", "scale=%d:-2:flags=lanczos,fps=24" % W,
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", mp4)
        # GitHub renders a GIF in a README and will not play an .mp4 committed beside it, so the GIF
        # is the one the page shows. 1000px wide at 10fps lands around 1.3 MB; narrower made the
        # terminal text unreadable, which was the complaint about the recording before this one.
        run_ff("-f", "concat", "-safe", "0", "-i", lst,
               "-vf", "fps=10,scale=1000:-1:flags=lanczos,split[s0][s1];"
                      "[s0]palettegen=max_colors=96[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4",
               gif)
        print("frames  %d" % take.n)
        print("mp4     %s  %.1f KB" % (mp4, os.path.getsize(mp4) / 1024))
        print("gif     %s  %.1f KB" % (gif, os.path.getsize(gif) / 1024))
        if keep:
            print("frames kept in %s" % frames)
        return 0
    finally:
        br.stop()
        d.stop()
        # The agent dies with its pane's shell (SIGHUP on stop). Say so, rather than assume it.
        left = subprocess.run(["pgrep", "-f", "claude --settings " + d.home], capture_output=True, text=True).stdout.split()
        if left:
            for p in left:
                subprocess.run(["kill", "-9", p], capture_output=True)
            print("record: the agent was still running and was killed (pid %s)" % ", ".join(left), file=sys.stderr)
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    p = argparse.ArgumentParser(description="record the README shot")
    p.add_argument("--out", default=os.path.join(REPO, "docs", "img"))
    p.add_argument("--keep-frames", action="store_true")
    a = p.parse_args()
    return run(a.out, a.keep_frames)


if __name__ == "__main__":
    raise SystemExit(main())
