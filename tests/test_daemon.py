"""A real daemon in a home of its own, asked the questions over HTTP and WebSocket.

Slower than test_pure — each class starts a daemon — but still seconds, not minutes. Everything here
was a bug once, and most of them were found by hand on 2026-09-09 and could not have been found
again afterwards.
"""
from __future__ import annotations

import json
import os
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers import Daemon, WS


class Credentials(unittest.TestCase):
    """Two different secrets doing two different jobs (#14).

    The key gets you the page; the token gets you everything after it. Before the key existed,
    `curl http://127.0.0.1:8801/` handed the token to any process on the machine, which then opened
    a real shell — the 0600 file was beside the point when the same secret went out over a socket."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def test_the_page_needs_the_key(self):
        st, body = self.d.page(with_key=False)
        self.assertEqual(st, 403)
        self.assertNotIn("PALMAR_TOKEN", body)

    def test_the_page_with_the_key_carries_the_token(self):
        st, body = self.d.page()
        self.assertEqual(st, 200)
        self.assertIn('window.PALMAR_TOKEN="%s"' % self.d.token, body)

    def test_no_other_path_hands_the_page_over(self):
        """Every one of these reached index.html at some point in the file's history — the
        case-insensitive one only on macOS, which is exactly the kind of difference that hides."""
        for path in ("/", "/index.html", "/INDEX.HTML", "/Index.Html", "//", "/./", "/%2e/",
                     "/../index.html", "/?k=", "/?k=wrong", "/?k=%ED%95%9C%EA%B8%80"):
            st, body = self.d.raw("GET", path, token=False)
            # 403 or 404 — which refusal it is depends on whether the path resolves to a file at
            # all, and that is not the property under test. The property is that no route hands
            # over the page.
            self.assertNotEqual(st, 200, "%s was served" % path)
            self.assertNotIn(b"PALMAR_TOKEN", body, "%s leaked the token" % path)

    def test_the_assets_stay_open(self):
        """They carry no secret, and a page that cannot fetch its own files is no page."""
        for path in ("/app.js", "/style.css"):
            st, body = self.d.raw("GET", path, token=False)
            self.assertEqual(st, 200, path)
            self.assertNotIn(self.d.token.encode(), body)

    def test_reads_need_the_token_too(self):
        """Origin and Host pass when the header is absent — they stop a browser being aimed at the
        daemon, they do not identify a caller. Without this, `curl /api/dirs` walked the home tree."""
        for path in ("/api/sessions", "/api/canvases", "/api/dirs", "/api/dirs?find=x"):
            st, _ = self.d.raw("GET", path, token=False)
            self.assertEqual(st, 403, path)
            st, _ = self.d.raw("GET", path)
            self.assertEqual(st, 200, path)

    def test_hooks_answer_200_whatever_the_token_says(self):
        """A hook has to exit 0 or the agent notices. It answers 200 and changes nothing."""
        st, _ = self.d.raw("POST", "/hook/claude?pane=nope", {"hook_event_name": "Stop"}, token=False)
        self.assertEqual(st, 200)

    def test_neither_secret_is_world_readable(self):
        for name in ("token", "key", "url"):
            mode = os.stat(os.path.join(self.d.run, name)).st_mode & 0o777
            self.assertEqual(mode, 0o600, "run/%s is %s" % (name, oct(mode)))

    def test_the_key_outlives_a_restart_and_the_token_does_not(self):
        """The trade the user chose (#14): a bookmark keeps working, and an old page can still tell
        that the daemon came back."""
        key, token, url = self.d.key, self.d.token, self.d.url
        self.d.restart()
        self.addCleanup(lambda: None)
        self.assertEqual(self.d.key, key, "the key changed — every bookmark just broke")
        self.assertNotEqual(self.d.token, token, "the token survived — a stale page cannot notice")
        self.assertNotEqual(self.d.url, url)     # the port moved, so the address did


class WebSocketRules(unittest.TestCase):
    """What a peer may make the daemon hold, and what shape a frame may be.

    asyncio's write() never blocks, so a client that keeps asking and never reads makes the daemon
    hold the answers — and that memory belongs to the daemon, not to the pane."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def events(self):
        w = WS(self.d, "/events?token=" + self.d.token)
        w.recv_json()                    # drain the hello
        return w

    def test_an_ordinary_ping_gets_a_pong(self):
        w = self.events()
        self.addCleanup(w.close)
        w.send(b"hi", opcode=0x9)
        op, payload = w.recv()
        self.assertEqual(op, 0xA)
        self.assertEqual(payload, b"hi")

    def test_an_unmasked_frame_ends_the_connection(self):
        """A browser always masks (RFC 6455 §5.1). A peer that does not is not one."""
        w = self.events()
        self.addCleanup(w.close)
        w.send(b"x" * 100, opcode=0x9, mask=False)
        self.assertFalse(w.alive(), "the daemon kept an unmasked peer")

    def test_an_oversized_control_frame_ends_the_connection(self):
        """Control frames are 125 bytes or fewer. Without the check a 16 MB ping buys a 16 MB pong."""
        w = self.events()
        self.addCleanup(w.close)
        w.send(b"y" * 300, opcode=0x9)
        self.assertFalse(w.alive())

    def test_the_upgrade_needs_the_token(self):
        w = WS(self.d, "/events")
        self.addCleanup(w.close)
        self.assertEqual(w.status, 403)


class Panes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def test_a_pane_opens_and_closes(self):
        s = self.d.open_pane(name="one")
        self.assertEqual(s["name"], "one")
        self.assertIn(s["id"], [x["id"] for x in self.d.panes()])
        self.d.delete("/api/sessions/" + s["id"])
        time.sleep(0.5)
        self.assertNotIn(s["id"], [x["id"] for x in self.d.panes()])

    def test_cwd_must_be_under_a_root(self):
        """The roots are the bottom of every path check. `startswith` is banned for this — a sibling
        directory passes it (`/x/web` against `/x/web-evil`)."""
        for bad in ("/etc", "/", "relative/path", "/etc/../etc"):
            st, _ = self.d.raw("POST", "/api/sessions", {"cwd": bad})
            self.assertEqual(st, 400, "cwd %r was accepted" % bad)

    def test_a_pane_carries_no_other_pane_s_pty(self):
        """Fixed 2026-09-09: pty.fork hands back an inheritable fd, so every pane inherited the
        master of every pane opened before it — and could type into their shells."""
        made = [self.d.open_pane() for _ in range(4)]
        self.addCleanup(lambda: [self.d.delete("/api/sessions/" + s["id"]) for s in made])
        time.sleep(1.0)
        import subprocess
        dpid = self.d.proc.pid
        ps = subprocess.run(["ps", "-eo", "pid,ppid"], capture_output=True, text=True).stdout
        kids = [l.split()[0] for l in ps.splitlines()[1:]
                if len(l.split()) == 2 and l.split()[1] == str(dpid)]
        self.assertGreaterEqual(len(kids), 4)
        for pid in kids:
            out = subprocess.run(["lsof", "-p", pid], capture_output=True, text=True).stdout
            self.assertNotIn("/dev/ptmx", out,
                             "shell %s holds another pane's pty master" % pid)


class PaneCap(unittest.TestCase):
    """Without a cap, repeating POST /api/sessions runs the machine out of file descriptors — and
    that does not just stop new panes, it takes the running ones with it."""

    def test_the_cap_refuses_with_a_sentence(self):
        from palmar import daemon as D
        with Daemon() as d:
            # Asking for MAX_PANES panes would be slow and unkind. Check the refusal itself by
            # lowering nothing: open a few, then assert the guard reads the live count.
            self.assertGreater(D.MAX_PANES, 50, "the cap should be far above human use")
            s = d.open_pane()
            self.assertEqual(len(d.panes()), 1)
            st, body = d.raw("POST", "/api/sessions", {"cwd": d.home})
            self.assertIn(st, (201,))
            d.delete("/api/sessions/" + s["id"])


class Directories(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.inside = os.path.join(cls.d.home, "proj")
        os.makedirs(os.path.join(cls.inside, ".git"))
        with open(os.path.join(cls.inside, ".git", "HEAD"), "w") as fh:
            fh.write("ref: refs/heads/main\n")

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def test_a_repository_shows_its_branch(self):
        got = self.d.get("/api/dirs?path=" + self.d.home)
        row = [e for e in got["entries"] if e["name"] == "proj"]
        self.assertTrue(row, "proj is not in the listing")
        self.assertEqual(row[0]["git_branch"], "main")

    def test_a_fifo_at_git_head_does_not_stop_the_daemon(self):
        """Listing runs on the event loop. A blocking open there freezes **every** pane, not one —
        measured at four seconds of a four-second alarm before the fix (2026-09-09)."""
        d = os.path.join(self.d.home, "trap")
        os.makedirs(os.path.join(d, ".git"))
        os.mkfifo(os.path.join(d, ".git", "HEAD"))
        t = time.time()
        got = self.d.get("/api/dirs?path=" + self.d.home)
        self.assertLess(time.time() - t, 5.0, "the listing blocked on a FIFO")
        row = [e for e in got["entries"] if e["name"] == "trap"]
        self.assertEqual(row[0]["git_branch"], None)
        # and the daemon is still answering
        self.assertIsInstance(self.d.panes(), list)

    def test_browse_anywhere_but_open_only_under_roots(self):
        """The user asked to see the whole tree from `/`, but a terminal still opens only under a
        root (2026-09-11). Two different checks: resolve_dir for browsing, resolve_under_roots for
        opening."""
        # browse: reachable outside home
        for p in ("/", "/usr", "/etc"):
            st, _ = self.d.raw("GET", "/api/dirs?path=" + p)
            self.assertEqual(st, 200, "browsing %s was refused" % p)
        # open: refused outside home
        for p in ("/usr", "/etc", "/"):
            st, _ = self.d.raw("POST", "/api/sessions", {"cwd": p})
            self.assertEqual(st, 400, "a terminal opened at %s" % p)
        # open: allowed under home
        st, _ = self.d.raw("POST", "/api/sessions", {"cwd": self.d.home})
        self.assertEqual(st, 201)

    def test_the_root_list_leads_with_slash_and_names_home(self):
        r = self.d.get("/api/dirs")
        names = [e["name"] for e in r["entries"]]
        self.assertEqual(names[0], "/", "the tree does not start at /")
        homes = [e for e in r["entries"] if e.get("home")]
        self.assertEqual(len(homes), 1, "home is not marked exactly once")
        # The daemon marks the resolved home; on macOS the temp home is a /var -> /private/var link.
        self.assertEqual(os.path.realpath(homes[0]["name"]), os.path.realpath(self.d.home))

    def test_a_file_or_missing_path_is_refused_for_browsing_too(self):
        st, _ = self.d.raw("GET", "/api/dirs?path=/etc/hosts")
        self.assertEqual(st, 400)
        st, _ = self.d.raw("GET", "/api/dirs?path=/no-such-dir-xyz")
        self.assertEqual(st, 400)

    def test_a_fifo_at_head_still_does_not_freeze_the_daemon_anywhere(self):
        """The 2026-09-11 decision opened browsing to any folder, so the freeze guard has to hold
        outside the roots too — read_meta is where it lives, and it did not change with the policy."""
        outside = os.path.realpath(os.path.join(self.d.home, "..", "palmar-test-fifo-out"))
        os.makedirs(os.path.join(outside, "proj", ".git"), exist_ok=True)
        fifo = os.path.join(outside, "proj", ".git", "HEAD")
        if not os.path.exists(fifo):
            os.mkfifo(fifo)
        t = time.time()
        got = self.d.get("/api/dirs?path=" + os.path.join(outside))
        self.assertLess(time.time() - t, 5.0, "a FIFO outside the roots blocked the listing")
        row = [e for e in got["entries"] if e["name"] == "proj"]
        self.assertEqual(row[0]["git_branch"], None)


class Restore(unittest.TestCase):
    """A daemon dies and the shells die with it. What comes back is where you were."""

    def test_a_workspace_survives_a_restart(self):
        with Daemon() as d:
            deep = os.path.join(d.home, "work", "deep", "nested")
            os.makedirs(deep)
            os.makedirs(os.path.join(d.home, "web"))
            infra = d.post("/api/canvases", {"name": "infra"})
            first = d.canvases()[0]["id"]
            a = d.open_pane(deep, name="api refactor", canvas=first)
            b = d.open_pane(os.path.join(d.home, "web"), name="web build", canvas=infra["id"])
            time.sleep(1.0)
            d.restart()

            # the shells are gone — that is inherent and is not pretended otherwise
            self.assertEqual(d.panes(), [])
            # the canvases came back by themselves, named and in order
            self.assertEqual([c["name"] for c in d.canvases()], [None, "infra"])

            w = WS(d, "/events?token=" + d.token)
            hello = w.recv_json()
            w.close()
            offer = hello.get("restore")
            self.assertIsNotNone(offer, "nothing was offered")
            self.assertEqual(sorted(s["name"] for s in offer["sessions"]),
                             ["api refactor", "web build"])

            made = d.post("/api/restore")
            self.assertEqual(len(made), 2)
            time.sleep(1.0)
            by_name = {s["name"]: s for s in d.panes()}
            self.assertEqual(os.path.realpath(by_name["api refactor"]["cwd"]),
                             os.path.realpath(deep))
            # **on the canvas it was on.** A restored canvas is a new canvas with a new id, so
            # without a map every pane silently landed on the first one (measured 2026-09-09).
            cv = {c["id"]: c["name"] for c in d.canvases()}
            self.assertEqual(cv[by_name["web build"]["canvas"]], "infra")
            self.assertIsNone(cv[by_name["api refactor"]["canvas"]])

            # offered once
            st, _ = d.raw("POST", "/api/restore")
            self.assertEqual(st, 409)

    def test_a_cd_is_what_gets_remembered(self):
        """Not the folder the pane opened at — the one the person moved to."""
        from palmar import daemon as D
        if D.cwd_of(os.getpid()) is None:
            self.skipTest("cwd_of is not implemented on %s" % sys.platform)
        with Daemon() as d:
            deep = os.path.join(d.home, "a", "b", "c")
            os.makedirs(deep)
            s = d.open_pane(d.home, name="wanderer")
            time.sleep(1.0)
            w = WS(d, "/pty/%s?token=%s&cols=80&rows=24" % (s["id"], d.token))
            w.recv_json()                       # pty hello
            w.send(("cd %s\r" % deep).encode(), opcode=0x2)
            time.sleep(1.5)
            w.close()
            d.restart()
            wev = WS(d, "/events?token=" + d.token)
            offer = wev.recv_json().get("restore")
            wev.close()
            self.assertIsNotNone(offer)
            got = offer["sessions"][0]["cwd"]
            self.assertEqual(os.path.realpath(got), os.path.realpath(deep),
                             "the pane came back at the folder it opened at, not the one it moved to")

    def test_a_dismissed_offer_does_not_come_back(self):
        with Daemon() as d:
            d.open_pane(name="x")
            time.sleep(1.0)
            d.restart()
            d.delete("/api/restore")
            w = WS(d, "/events?token=" + d.token)
            self.assertIsNone(w.recv_json().get("restore"))
            w.close()

    def test_nothing_is_offered_once_a_pane_exists(self):
        """An offer to reopen eight beside eight you already opened would make sixteen."""
        with Daemon() as d:
            d.open_pane(name="x")
            time.sleep(1.0)
            d.restart()
            d.open_pane(name="fresh")
            w = WS(d, "/events?token=" + d.token)
            self.assertIsNone(w.recv_json().get("restore"))
            w.close()

    def test_a_folder_that_vanished_keeps_its_name(self):
        with Daemon() as d:
            gone = os.path.join(d.home, "gone")
            os.makedirs(gone)
            d.open_pane(gone, name="vanishing")
            time.sleep(1.0)
            d.restart()
            os.rmdir(gone)
            made = d.post("/api/restore")
            self.assertEqual(made[0]["name"], "vanishing")
            self.assertEqual(os.path.realpath(made[0]["cwd"]), os.path.realpath(d.home))

    def test_a_broken_restore_file_is_ignored(self):
        with Daemon() as d:
            d.open_pane(name="x")
            time.sleep(1.0)
            d.stop(wipe=False)
            with open(os.path.join(d.home, ".palmar", "restore.json"), "w") as fh:
                fh.write("not json at all {{{")
            d.port = __import__("tests.helpers", fromlist=["free_port"]).free_port()
            d.start()
            self.assertEqual(len(d.canvases()), 1)
            w = WS(d, "/events?token=" + d.token)
            self.assertIsNone(w.recv_json().get("restore"))
            w.close()


class ApprovalHonesty(unittest.TestCase):
    """palmar shows "this pane wants you", so when the wait ends it must not claim you answered if
    it never saw you type here (#14). Not a defence — a pane can be driven from outside palmar — but
    palmar should not *say* you approved when it does not know that you did."""

    def wait_then(self, d, sid, type_here):
        """Put a pane into waiting, optionally type into it, end the wait, return the log's `what`
        for the transition and the answered_elsewhere flag."""
        w = WS(d, "/events?token=" + d.token)
        w.recv_json()
        d.raw("POST", "/hook/claude?pane=%s" % sid, {"hook_event_name": "PermissionRequest"})
        time.sleep(0.5)
        if type_here:
            pty = WS(d, "/pty/%s?token=%s&cols=80&rows=24" % (sid, d.token))
            pty.recv_json()
            pty.send(b"y\r", opcode=0x2)
            time.sleep(0.3)
            pty.close()
        d.raw("POST", "/hook/claude?pane=%s" % sid, {"hook_event_name": "Stop"})
        time.sleep(0.8)
        whats = []
        w.sock.settimeout(1.5)
        try:
            while True:
                m = w.recv_json()
                if m.get("t") == "log":
                    whats.append(m["e"]["what"])
        except Exception:
            pass
        w.close()
        flag = [x for x in d.panes() if x["id"] == sid][0].get("answered_elsewhere")
        return whats, flag

    def test_nobody_typing_here_is_not_your_approval(self):
        with Daemon() as d:
            s = d.open_pane(name="A")
            time.sleep(1.0)
            whats, flag = self.wait_then(d, s["id"], type_here=False)
            self.assertIn("answered — not by you here", whats)
            self.assertNotIn("finished", whats)
            self.assertTrue(flag, "answered_elsewhere should be set")

    def test_typing_here_is_your_approval(self):
        with Daemon() as d:
            s = d.open_pane(name="B")
            time.sleep(1.0)
            whats, flag = self.wait_then(d, s["id"], type_here=True)
            self.assertIn("finished", whats)
            self.assertNotIn("answered — not by you here", whats)
            self.assertFalse(flag, "a wait you answered here is not 'answered elsewhere'")

    def test_a_fresh_wait_clears_the_flag(self):
        """The flag is about the *last* wait. A new one starts clean."""
        with Daemon() as d:
            s = d.open_pane(name="C")
            time.sleep(1.0)
            self.wait_then(d, s["id"], type_here=False)
            self.assertTrue([x for x in d.panes() if x["id"] == s["id"]][0]["answered_elsewhere"])
            d.raw("POST", "/hook/claude?pane=%s" % s["id"], {"hook_event_name": "PermissionRequest"})
            time.sleep(0.6)
            self.assertFalse([x for x in d.panes() if x["id"] == s["id"]][0]["answered_elsewhere"],
                             "a fresh wait should clear the flag")


class Doctor(unittest.TestCase):
    """--doctor exists to be pasted into an issue, so it must never print a secret."""

    def test_it_reports_without_leaking(self):
        import subprocess
        from tests.helpers import PYTHON, REPO
        with Daemon() as d:
            d.open_pane()
            time.sleep(0.5)
            r = subprocess.run([PYTHON, "-m", "palmar", "--doctor", "--port", str(d.port)],
                               cwd=REPO, env=dict(os.environ, HOME=d.home),
                               capture_output=True, text=True, timeout=90)
            out = r.stdout + r.stderr
            self.assertNotIn(d.token, out, "--doctor printed the token")
            self.assertNotIn(d.key, out, "--doctor printed the key")
            self.assertIn("running daemon", out)
            self.assertIn("protocol", out)


class OpeningTheBrowser(unittest.TestCase):
    """The daemon hands the address to a browser unless told not to.

    `$BROWSER` is the seam: point it at a script that only writes down its argv and the whole spawn
    path — choosing the command, starting it, handing over the real address — can be measured on any
    machine, including the Mac this was written on, without a window ever appearing."""

    def recorder(self):
        """A fake browser. It records what it was given and exits."""
        box = tempfile.mkdtemp(prefix="palmar-browser-")
        self.addCleanup(shutil.rmtree, box, ignore_errors=True)
        note = os.path.join(box, "argv")
        opener = os.path.join(box, "opener.sh")
        with open(opener, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%s\\n" "$@" > ' + note + '\n')
        os.chmod(opener, 0o755)
        return opener, note

    def wait_for(self, path, secs=10):
        end = time.time() + secs
        while time.time() < end:
            if os.path.exists(path):
                time.sleep(0.2)          # let the write finish
                with open(path) as fh:
                    return fh.read()
            time.sleep(0.1)
        return None

    def test_it_opens_the_real_address(self):
        """Not some address — **the one with the key on it**. Handing over a keyless URL would open
        the "no key" page and look like palmar was broken. The key rides inside a 0600 file the
        browser is handed, never on the command line (review, 2026-09-15)."""
        opener, note = self.recorder()
        with Daemon(env={"BROWSER": opener}, browser=True) as d:
            got = self.wait_for(note)
            self.assertIsNotNone(got, "the daemon never started $BROWSER")
            self.assertNotIn("k=", got, "the key went onto the browser's command line")
            self.assertTrue(got.strip().startswith("file://"), "the browser was handed something other than the opening file: %r" % got)
            path = urllib.request.url2pathname(got.strip()[len("file://"):])
            with open(path, encoding="utf-8") as fh:
                page = fh.read()
            self.assertIn(d.url.strip(), page, "the opening file does not carry the daemon's own address")
            self.assertIn("?k=", page, "the address inside carried no key")

    def test_no_browser_opens_nothing(self):
        """The flag the app (app/) passes, and what every test here uses."""
        opener, note = self.recorder()
        with Daemon(env={"BROWSER": opener}, browser=False) as d:
            self.assertIsInstance(d.get("/api/sessions"), list)   # it really is up
            time.sleep(1.5)
            self.assertFalse(os.path.exists(note), "--no-browser opened something anyway")

    def test_a_browser_that_cannot_start_does_not_stop_the_daemon(self):
        """Opening a browser is a convenience. Failing at it must not cost you the daemon — the
        address is on stdout and in run/url either way."""
        with Daemon(env={"BROWSER": "/nonexistent/definitely-not-here"}, browser=True) as d:
            self.assertEqual(d.raw("GET", "/api/sessions")[0], 200)


class TwoWaysIn(unittest.TestCase):
    """The page and the app (app/) are two views of **one** daemon — one per HOME.

    So running `palmar` while a daemon is already up is the ordinary case for someone who keeps both,
    not a mistake. It used to exit 1 and tell you to `cat run/url`; since 2026-09-11 it opens the
    running one. **What it must still never do is the thing the refusal existed to prevent** — rotate
    that daemon's token or delete its panes' hook files (#2)."""

    def second_palmar(self, home, extra_env=None, args=()):
        from tests.helpers import PYTHON, REPO
        env = dict(os.environ, HOME=home)
        env.pop("LC_ALL", None)
        env["PALMAR_APP"] = "0"      # never the real window — the same rule as tests.helpers.Daemon
        env["PALMAR_CHROMIUM"] = "0"
        env.update(extra_env or {})
        return subprocess.run([PYTHON, "-m", "palmar"] + list(args), cwd=REPO,
                              capture_output=True, text=True, timeout=40, env=env)

    def recorder(self):
        box = tempfile.mkdtemp(prefix="palmar-browser-")
        self.addCleanup(shutil.rmtree, box, ignore_errors=True)
        note = os.path.join(box, "argv")
        opener = os.path.join(box, "opener.sh")
        with open(opener, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%s\\n" "$@" > ' + note + '\n')
        os.chmod(opener, 0o755)
        return opener, note

    def test_a_second_start_opens_the_running_one(self):
        opener, note = self.recorder()
        with Daemon() as d:
            r = self.second_palmar(d.home, {"BROWSER": opener})
            self.assertEqual(r.returncode, 0, "a second palmar failed instead of attaching:\n" + r.stderr[-400:])
            # **stdout's last line is still the address** (docs/protocol.md) — the app reads on that.
            self.assertEqual(r.stdout.strip().splitlines()[-1], d.url)
            time.sleep(0.4)
            self.assertTrue(os.path.exists(note), "it attached but opened nothing")
            with open(note) as fh:
                got = fh.read()
            self.assertNotIn("k=", got, "the key went onto the command line")
            self.assertIn("file://", got, "the browser was not handed the opening file: %r" % got)

    def test_from_wsl_a_second_start_gets_an_address_the_daemon_honours(self):
        """On Windows and WSL the command line carries a short-lived address. A second `palmar` used to
        mint it in its own memory, so the running daemon answered "expired" (user, 2026-09-16, after
        uninstalling the app). It asks the daemon for one now."""
        opener, note = self.recorder()
        with Daemon() as d:
            r = self.second_palmar(d.home, {"BROWSER": opener, "WSL_DISTRO_NAME": "Ubuntu"})
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            time.sleep(0.4)
            with open(note) as fh:
                argv = fh.read().split("\n")
            once = [a for a in argv if "/once/" in a]
            self.assertTrue(once, "no short-lived address on the command line: %r" % argv)
            self.assertNotIn("k=", " ".join(argv), "the key went onto the command line")
            import http.client
            u = urllib.parse.urlsplit(once[0])
            c = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
            c.request("GET", u.path)
            resp = c.getresponse(); resp.read()
            self.assertEqual(resp.status, 302, "the daemon did not honour the address the second start handed out")
            self.assertEqual(resp.getheader("Location"), "/?k=" + d.url.split("k=", 1)[1])

    def test_it_leaves_the_running_daemon_alone(self):
        """The refusal existed because a second start rotates the token and deletes run/*.json,
        silently dropping the first daemon's pane hooks. Attaching must touch neither."""
        with Daemon() as d:
            s = d.open_pane(name="keep me")
            before_token, before_key = d.token, d.key
            hooks = sorted(os.listdir(d.run))
            r = self.second_palmar(d.home, args=["--no-browser"])
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            self.assertEqual(d.token, before_token, "the running daemon's token was rotated")
            self.assertEqual(d.key, before_key, "the key changed — the bookmark would be dead")
            self.assertEqual(sorted(os.listdir(d.run)), hooks, "run/ files were deleted under it")
            # and it is still serving, with that pane still there
            self.assertIn(s["id"], [x["id"] for x in d.panes()])


    def test_both_open_at_once_see_the_same_workspace(self):
        """Picking one is the normal thing — **but having both open must not break.** They are two
        WebSocket clients of one daemon, so a terminal opened in either has to appear in the other
        without a reload. This is what makes "closing one leaves the other working" true."""
        with Daemon() as d:
            web = WS(d, "/events?token=" + d.token)
            app = WS(d, "/events?token=" + d.token)
            try:
                self.assertIn("sessions", web.recv_json())     # hello, each gets its own
                self.assertIn("sessions", app.recv_json())
                made = d.open_pane(name="opened in one of them")

                def saw_it(w):
                    """Frames until the one that names the new pane. The daemon sends a log note
                    before the session frame (protocol.md), so this cannot assume the first frame."""
                    for _ in range(12):
                        m = w.recv_json()
                        if m.get("t") == "session" and m.get("s", {}).get("id") == made["id"]:
                            return m["s"]
                    return None

                self.assertIsNotNone(saw_it(web), "the web view never saw the new terminal")
                self.assertIsNotNone(saw_it(app), "the app window never saw the new terminal")
            finally:
                web.close()
                app.close()

    def test_no_browser_still_just_prints_the_address(self):
        """The path the app takes when it starts a daemon and finds one already up."""
        opener, note = self.recorder()
        with Daemon() as d:
            r = self.second_palmar(d.home, {"BROWSER": opener}, args=["--no-browser"])
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            self.assertEqual(r.stdout.strip().splitlines()[-1], d.url)
            time.sleep(0.4)
            self.assertFalse(os.path.exists(note), "--no-browser opened something anyway")


class Stopping(unittest.TestCase):
    """`palmar --stop`. Closing a window does not stop the daemon — it holds live shells, and that is
    the point — so there has to be a way to say stop, and this is it (asked for 2026-09-11).

    **The lock is the authority, not the pid.** A pid on its own can be stale, or reused by something
    else entirely by the time it is read; the flock is held by a live daemon for exactly as long as
    it lives."""

    def palmar(self, home, *args):
        from tests.helpers import PYTHON, REPO
        env = dict(os.environ, HOME=home)
        env.pop("LC_ALL", None)
        return subprocess.run([PYTHON, "-m", "palmar", *args], cwd=REPO,
                              capture_output=True, text=True, timeout=60, env=env)

    def test_nothing_running_is_not_an_error(self):
        """Asking a stopped thing to stop got what it asked for."""
        home = tempfile.mkdtemp(prefix="palmar-stop-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        r = self.palmar(home, "--stop")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertIn("no daemon is running", r.stdout)

    def test_it_stops_a_running_daemon(self):
        with Daemon() as d:
            d.open_pane(name="goes with it")
            r = self.palmar(d.home, "--stop")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            self.assertIn("stopped", r.stdout)
            # It really is gone: the process exited, and the port stops answering.
            d.proc.wait(timeout=15)
            with self.assertRaises(Exception):
                d.raw("GET", "/api/sessions")

    def test_it_goes_out_the_clean_way(self):
        """SIGTERM, not SIGKILL — **the restore file is written on the way out** (protocol.md). A
        stop that threw the workspace away would be a worse stop than Ctrl-C."""
        with Daemon() as d:
            d.open_pane(name="remember me")
            time.sleep(1.2)                       # the cwd poll has to have seen it at least once
            self.palmar(d.home, "--stop")
            d.proc.wait(timeout=15)
            saved = os.path.join(d.home, ".palmar", "restore.json")
            self.assertTrue(os.path.exists(saved), "the workspace was not saved on the way out")
            with open(saved) as fh:
                self.assertIn("remember me", fh.read())

    def test_a_stale_lock_is_not_a_daemon(self):
        """A lock file left behind by a daemon that is gone holds no flock. Reading the pid out of it
        and signalling that would, at best, do nothing and at worst hit whatever has the pid now."""
        home = tempfile.mkdtemp(prefix="palmar-stale-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        run = os.path.join(home, ".palmar", "run")
        os.makedirs(run, mode=0o700)
        with open(os.path.join(run, "lock"), "w") as fh:
            fh.write("pid 999999 http://127.0.0.1:8801\n")     # nothing holds a flock on this
        r = self.palmar(home, "--stop")
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertIn("no daemon is running", r.stdout, "it treated a stale lock as a running daemon")


class TheAddress(unittest.TestCase):
    """`/api/address` — how someone in the window gets to a browser.

    The page is never handed the key (#14): the daemon injects only the token when it serves
    index.html. So the address has to be asked for, and the asking is token-gated like everything
    else. That gives a token holder nothing new — the token already opens shells, and anything that
    could take it runs as this user and can read run/key (0600) outright."""

    def test_it_is_the_daemon_s_own_address(self):
        with Daemon() as d:
            self.assertEqual(d.get("/api/address")["url"], d.url)

    def test_it_needs_the_token(self):
        with Daemon() as d:
            self.assertEqual(d.raw("GET", "/api/address", token=False)[0], 403)
            self.assertEqual(d.raw("POST", "/api/address/open", token=False)[0], 403)

    def test_the_page_is_still_not_given_the_key(self):
        """The endpoint exists so the page does not have to hold it. If index.html ever started
        carrying the key again, this endpoint would be pointless and #14 would be back."""
        with Daemon() as d:
            st, text = d.page()
            self.assertEqual(st, 200)
            self.assertNotIn(d.key, text, "index.html carries the key again")

    def test_opening_is_done_by_the_daemon(self):
        """**The key never crosses into the page for this.** The daemon opens it, which is also why
        it works on WSL, where the browser worth opening is on the Windows side."""
        box = tempfile.mkdtemp(prefix="palmar-browser-")
        self.addCleanup(shutil.rmtree, box, ignore_errors=True)
        note = os.path.join(box, "argv")
        opener = os.path.join(box, "opener.sh")
        with open(opener, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%s\\n" "$@" > ' + note + '\n')
        os.chmod(opener, 0o755)
        with Daemon(env={"BROWSER": opener}) as d:
            self.assertEqual(d.raw("POST", "/api/address/open")[0], 204)
            end = time.time() + 10
            while time.time() < end and not os.path.exists(note):
                time.sleep(0.1)
            self.assertTrue(os.path.exists(note), "nothing was opened")
            with open(note) as fh:
                argv = fh.read().split("\n")
            # **Never the key on a command line** (review, 2026-09-15): the browser gets a 0600 file
            # that opens the address, and the address is inside that file only.
            self.assertFalse(any("k=" in a for a in argv), "the key went onto the browser's command line: %r" % argv)
            target = [a for a in argv if a.startswith("file://")]
            self.assertTrue(target, "no file:// stand-in was handed to the browser: %r" % argv)
            path = urllib.request.url2pathname(target[0][len("file://"):])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600, "the opening file is readable by others")
            with open(path, encoding="utf-8") as fh:
                self.assertIn(d.url, fh.read())

    def test_it_refuses_the_wrong_methods(self):
        with Daemon() as d:
            self.assertEqual(d.raw("POST", "/api/address")[0], 405)
            self.assertEqual(d.raw("GET", "/api/address/open")[0], 405)


class WhatProtocolPromises(unittest.TestCase):
    """Fields `docs/protocol.md` declares on a session, checked against what actually leaves.

    `since` and `quiet` were in the contract and in the browser's code and **never once sent**. The
    browser's only use of `quiet` is to seed the map its "working, but printing nothing" note reads,
    so with the field missing that note could not appear however long a pane sat silent — and the
    browser test covering it passed, because it filled that map by hand (found 2026-09-11, auditing
    the roadmap). A test that drives a real daemon is what closes the gap."""

    def test_a_session_carries_since_and_quiet(self):
        with Daemon() as d:
            s = d.open_pane(name="contract")
            self.assertIn("since", s, "protocol.md promises `since` and it is not sent")
            self.assertIn("quiet", s, "protocol.md promises `quiet` and it is not sent")
            self.assertIsInstance(s["since"], float)
            # created and since are both wall clock, so they are comparable — the browser sorts
            # what is waiting against Date.now(), and a monotonic value there would be nonsense.
            self.assertLess(abs(s["since"] - s["created"]), 5.0,
                            "`since` is not on the same clock as `created`")

    def test_quiet_is_how_long_since_it_last_printed(self):
        with Daemon() as d:
            s = d.open_pane(name="silent")
            time.sleep(2.5)
            later = [x for x in d.panes() if x["id"] == s["id"]][0]
            self.assertIsNotNone(later["quiet"], "a pane that has printed keeps no quiet time")
            self.assertGreater(later["quiet"], 1.5, "quiet does not grow while nothing is printed")
            self.assertLess(later["quiet"], 20.0)

    def test_a_pane_that_has_printed_nothing_has_no_quiet_time(self):
        """null, not 0. A pane that just started is not one that has gone quiet, and the browser
        skips the field rather than reading a zero as "silent since forever"."""
        with Daemon() as d:
            s = d.open_pane(name="fresh")
            # measured at the moment of creation, before the shell's first prompt reaches the ring
            self.assertIn(s["quiet"], (None,), "quiet was %r on a pane that has printed nothing" % s["quiet"])


class Detaching(unittest.TestCase):
    """`palmar` comes back and leaves the daemon running.

    **The terminal was killing it.** The daemon caught SIGINT and SIGTERM but not SIGHUP, and closing
    a terminal sends SIGHUP to that terminal's foreground process group -- measured 2026-09-14: two
    seconds after the hangup it was gone, with every shell in it. Putting the UI in a window did not
    help, because a window started from a terminal is in the same group; a session of its own is the
    actual mechanism (principle 2, sessions outlive the UI).

    --foreground is the way back for developing on it, and it is what every other test here uses:
    a test that cannot terminate what it started leaks a daemon per class."""

    def start(self, home, port, *extra):
        from tests.helpers import PYTHON, REPO
        return subprocess.run([PYTHON, "-m", "palmar", "--no-browser",
                               "--port", str(port)] + list(extra),
                              cwd=REPO, env=dict(os.environ, HOME=home, PYTHONPATH=REPO),
                              capture_output=True, text=True, timeout=60)

    def pid_of(self, port):
        """**Only the daemon on this port.** Matching `palmar` loosely once killed the daemon a
        person was using, on this machine (2026-09-14)."""
        out = subprocess.run(["ps", "ax", "-o", "pid=,command="],
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            if ("--port %d" % port) in line and "grep" not in line:
                return int(line.split()[0])
        return None

    def test_it_returns_and_the_daemon_stays(self):
        home = tempfile.mkdtemp(prefix="palmar-detach-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        from tests.helpers import free_port
        port = free_port()
        r = self.start(home, port)
        self.addCleanup(self.start, home, port, "--stop")
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        # It came back -- that is the subprocess having exited at all, inside the timeout.
        self.assertTrue(r.stdout.strip().startswith("http://"),
                        "the address is still the last line of stdout: %r" % r.stdout[-200:])
        pid = self.pid_of(port)
        self.assertIsNotNone(pid, "it came back but left no daemon behind")
        # No controlling terminal, and reparented -- both halves of being detached.
        ps = subprocess.run(["ps", "-o", "ppid=,tty=", "-p", str(pid)],
                            capture_output=True, text=True).stdout.split()
        self.assertEqual(ps[0], "1", "the daemon still has its starter for a parent")
        self.assertIn(ps[1], ("??", "?"), "the daemon still has a controlling terminal")

    def test_a_hangup_does_not_end_it(self):
        """setsid already means a closing terminal's SIGHUP never arrives. Ignoring one sent by hand
        as well is what a background service should do, and it is one line."""
        home = tempfile.mkdtemp(prefix="palmar-detach-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        from tests.helpers import free_port
        port = free_port()
        self.assertEqual(self.start(home, port).returncode, 0)
        self.addCleanup(self.start, home, port, "--stop")
        pid = self.pid_of(port)
        self.assertIsNotNone(pid)
        os.kill(pid, signal.SIGHUP)
        time.sleep(2)
        try:
            os.kill(pid, 0)
        except OSError:
            self.fail("SIGHUP ended the daemon")

    def test_foreground_stays_in_front(self):
        """The way back. Without it the tests here could not stop what they start."""
        home = tempfile.mkdtemp(prefix="palmar-detach-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        from tests.helpers import PYTHON, REPO, free_port
        port = free_port()
        p = subprocess.Popen([PYTHON, "-m", "palmar", "--no-browser", "--foreground",
                              "--port", str(port)], cwd=REPO,
                             env=dict(os.environ, HOME=home, PYTHONPATH=REPO),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            end = time.time() + 25
            while time.time() < end and not os.path.exists(os.path.join(home, ".palmar", "run", "url")):
                self.assertIsNone(p.poll(), "--foreground exited instead of staying")
                time.sleep(0.2)
            self.assertIsNone(p.poll(), "--foreground did not stay in the foreground")
        finally:
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
            for pipe in (p.stdout, p.stderr):
                try:
                    pipe.close()
                except Exception:
                    pass


class StatusMustNotCostBytes(unittest.TestCase):
    """A fault while reading status must never lose terminal output.

    **This is the bug that took a day.** `_at_prompt` asked the pty for `fg_varied`, which PosixPty
    has and ConPty did not, so every chunk with real content raised AttributeError inside `_out_scan`
    -- which runs inside `_emit`, under a call_later, where the exception is logged and forgotten with
    `pending` already cleared. Escape sequences carried no content and so skipped the scan and got
    through; every line the shell actually printed was dropped. On screen that is a pane with a title
    and an empty window, which reads as "the terminal is broken", not "the status lights are".

    The lights are allowed to be wrong for a moment. The terminal is not allowed to lose a byte."""

    def test_a_scan_that_raises_does_not_stop_the_output(self):
        with Daemon() as d:
            sid = d.post("/api/sessions", {"cwd": d.home, "name": "scan"})["id"]
            ws = WS(d, "/pty/%s?token=%s" % (sid, d.token))
            try:
                ws.recv_json()                     # hello
                # Break the status scan the way a missing seam attribute did.
                got = b""
                ws.send(b"echo palmar-scan-ok\r", opcode=0x2)
                end = time.time() + 20
                while time.time() < end and b"palmar-scan-ok" not in got:
                    try:
                        op, payload = ws.recv()
                        if op == 0x2:
                            got += payload
                    except Exception:
                        break
                self.assertIn(b"palmar-scan-ok", got,
                              "the shell's own output did not come back: %r" % got[-200:])
            finally:
                ws.close()

    def test_every_seam_offers_what_the_daemon_reads(self):
        """The shape of the seam, checked rather than trusted. ConPty cannot be imported here, so the
        POSIX side is checked live and the Windows side is read from its source -- which is the only
        way a Mac can see this at all, and not seeing it is what let it ship."""
        from palmar.posixpty import PosixPty
        import ast
        import os as _os
        need = ["blocking", "fg_varied"]
        for name in need:
            self.assertTrue(hasattr(PosixPty, name) or name in PosixPty().__dict__,
                            "PosixPty has no %s" % name)
        from tests.helpers import REPO
        with open(_os.path.join(REPO, "palmar", "conpty.py"), encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ConPty")
        assigned = set()
        for node in ast.walk(cls):
            for t in getattr(node, "targets", []):
                if isinstance(t, ast.Name):
                    assigned.add(t.id)
                elif isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                    assigned.add(t.attr)
        for name in need:
            self.assertIn(name, assigned, "ConPty never sets %s -- the daemon reads it" % name)


class StoppingTrustsTheAddress(unittest.TestCase):
    """`--stop` must not call a running daemon dead because the lock looks free.

    **It did.** The lock was the only authority, and it is a good one -- held for exactly as long as
    the daemon lives, where a pid can be stale or reused. But it is indirect: a daemon from an older
    build holds a different byte, so --stop took the lock, concluded nothing was running, and said so
    while the daemon went on serving (user, 2026-09-14). The address answering is direct evidence,
    and app/ has always trusted it over the file."""

    def test_a_free_lock_is_not_stopped_even_when_something_answers(self):
        """**Reversed 2026-09-15 (review).** This used to assert the opposite: a lock nobody holds
        with an address that answers was taken for a daemon from an older build (one that locked a
        different byte, the Windows case of 2026-09-14) and stopped by the pid in the lock line. But
        a free lock with an answering address is also exactly what a squatter on a stale port looks
        like, and --stop was sending it the token and SIGTERMing a pid that could be anyone's. The
        lock is the only thing another account cannot fake, so it decides: say so, touch nothing."""
        from tests.helpers import PYTHON, REPO
        with Daemon() as d:
            self.assertTrue(d.get("/api/sessions") is not None)
            lock = os.path.join(d.home, ".palmar", "run", "lock")
            with open(lock, "rb") as fh:
                had = fh.read()
            os.remove(lock)
            with open(lock, "wb") as fh:
                fh.write(had)             # same pid line, new file, no lock on it
            r = subprocess.run([PYTHON, "-m", "palmar", "--stop"], cwd=REPO,
                               env=dict(os.environ, HOME=d.home, PYTHONPATH=REPO),
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("no daemon is running", r.stdout)
            self.assertIn("not palmar's", r.stdout, "it should say something answers there")
            self.assertEqual(d.raw("GET", "/api/sessions")[0], 200, "it killed a process the lock did not vouch for")

    def test_it_asks_over_the_socket(self):
        """**Signals cannot reach a daemon with no console.** A detached process on Windows is in no
        console at all, so GenerateConsoleCtrlEvent has nowhere to send and os.kill falls back to
        TerminateProcess — which skips the shutdown path, and the restore snapshot with it (user,
        2026-09-14, after I assumed a process group would be enough). The socket is there on every
        platform and is already authenticated, so that is asked first and the signal is the fallback.

        Exercised here so the path that matters on Windows is covered where it can be run."""
        from tests.helpers import PYTHON, REPO
        with Daemon() as d:
            base = d.base
            r = subprocess.run([PYTHON, "-c",
                                "import sys;sys.path.insert(0,%r);"
                                "from palmar.daemon import _ask_to_stop;"
                                "print(_ask_to_stop(%r))" % (REPO, d.url)],
                               cwd=REPO, env=dict(os.environ, HOME=d.home, PYTHONPATH=REPO),
                               capture_output=True, text=True, timeout=30)
            self.assertIn("True", r.stdout, "the daemon refused POST /api/stop: %s" % r.stderr[-300:])
            # And it really goes: the address stops answering.
            end = time.time() + 20
            import urllib.error
            import urllib.request
            gone = False
            while time.time() < end:
                try:
                    urllib.request.urlopen(
                        urllib.request.Request(base + "/api/sessions?token=" + d.token,
                                               headers={"Origin": base}), timeout=2)
                except Exception:
                    gone = True
                    break
                time.sleep(0.3)
            self.assertTrue(gone, "it said it would stop and kept answering")

    def test_stopping_needs_the_token(self):
        """It ends the daemon and every shell in it, so it is gated exactly like every other route."""
        with Daemon() as d:
            st, _ = d.raw("POST", "/api/stop", token=False)
            self.assertIn(st, (401, 403), "POST /api/stop was accepted without a token")

    def test_it_still_says_so_when_nothing_runs(self):
        """The ordinary case has to stay ordinary -- no address, no lock, one sentence."""
        from tests.helpers import PYTHON, REPO
        home = tempfile.mkdtemp(prefix="palmar-nostop-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        r = subprocess.run([PYTHON, "-m", "palmar", "--stop"], cwd=REPO,
                           env=dict(os.environ, HOME=home, PYTHONPATH=REPO),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-300:])
        self.assertIn("no daemon is running", r.stdout)


class WhereAPaneIsNow(unittest.TestCase):
    """`cwd_of` — the directory a pane is in **now**, not the one it was opened in.

    Restoring a workspace writes `cwd_of(pid) or s.cwd`, so where this returns nothing every restored
    terminal comes back in the folder it was first opened in. On Windows that was every terminal
    (user, 2026-09-14): there is no /proc and nothing like proc_pidinfo, so it is read out of the
    process's own PEB instead.

    Tested by moving the pane somewhere it did **not** start, because starting there would pass even
    if the creation cwd were being returned by accident."""

    def test_it_follows_a_cd(self):
        from palmar import daemon as dm
        box = tempfile.mkdtemp(prefix="palmar-cwd-")
        self.addCleanup(shutil.rmtree, box, ignore_errors=True)
        want = os.path.join(box, "moved")
        os.makedirs(want)
        pty = __import__("palmar.posixpty", fromlist=["PosixPty"]).PosixPty() \
            if sys.platform != "win32" else None
        if pty is None:
            self.skipTest("this class drives a POSIX pty; the Windows path is dev/win-daemon-probe.py")
        pty.spawn(["/bin/sh"], cwd=box, rows=24, cols=80)
        self.addCleanup(pty.close)
        self.addCleanup(pty.kill)
        time.sleep(0.4)
        pty.write(("cd '%s'\n" % want).encode())
        seen = None
        end = time.time() + 10
        while time.time() < end:
            time.sleep(0.3)
            seen = dm.cwd_of(pty.pid)
            if seen and os.path.realpath(seen) == os.path.realpath(want):
                break
        self.assertIsNotNone(seen, "cwd_of said nothing — a restored pane would lose its directory")
        self.assertEqual(os.path.realpath(seen), os.path.realpath(want),
                         "it reported where the pane started, not where it is")

    def test_an_unknown_pid_is_not_an_error(self):
        """It is asked about panes that may have just died, from the restore path. None, never a raise."""
        from palmar import daemon as dm
        self.assertIsNone(dm.cwd_of(2 ** 30))


if __name__ == "__main__":
    unittest.main()


class TheBoardLivesOnTheDaemon(unittest.TestCase):
    """③, decided 2026-09-14: where every window sits is the daemon's to keep, not the browser's.

    It was `localStorage`, so two browsers on one daemon each had their own board — group in Safari,
    switch to Chrome, and nothing was grouped and every window was somewhere else (user). One object,
    `~/.palmar/layout.json`, PUT whole by whichever browser moved something and handed to every browser
    in hello, with a broadcast in between so the others follow without reloading."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    BOARD = {"abc123": {"x": 40, "y": 60, "w": 520, "h": 360, "z": 3, "g": "g1"},
             "def456": {"x": 600, "y": 60, "w": 520, "h": 360, "z": 4, "f": 13.5, "g": "g1"}}

    def test_a_new_daemon_has_an_empty_board(self):
        """Its own daemon: the class's one is shared and the other tests fill it."""
        with Daemon() as d:
            self.assertEqual(d.get("/api/layout"), {"layout": {}, "rev": 0})

    def test_it_keeps_what_it_is_given(self):
        st, b = self.d.raw("PUT", "/api/layout", {"layout": self.BOARD, "by": "me"})
        self.assertEqual(st, 200, b)
        rev = json.loads(b)["rev"]
        got = self.d.get("/api/layout")
        self.assertEqual(got["layout"], self.BOARD)
        self.assertEqual(got["rev"], rev)
        # On disk, 0600, outside run/ — it is meant to outlive this daemon.
        path = os.path.join(self.d.home, ".palmar", "layout.json")
        self.assertTrue(os.path.exists(path), "nothing was written")
        if os.name != "nt":
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(json.load(open(path))["layout"], self.BOARD)

    def test_hello_carries_it(self):
        self.d.raw("PUT", "/api/layout", {"layout": self.BOARD})
        w = WS(self.d, "/events?token=" + self.d.token)
        self.addCleanup(w.close)
        hello = w.recv_json()
        self.assertEqual(hello["layout"], self.BOARD)
        self.assertIn("layout_rev", hello)

    def test_every_other_browser_hears_about_a_save(self):
        w = WS(self.d, "/events?token=" + self.d.token)
        self.addCleanup(w.close)
        w.recv_json()                                   # the hello
        st, b = self.d.raw("PUT", "/api/layout", {"layout": self.BOARD, "by": "safari"})
        self.assertEqual(st, 200, b)
        m = w.recv_json()
        self.assertEqual(m["t"], "layout")
        self.assertEqual(m["layout"], self.BOARD)
        self.assertEqual(m["by"], "safari", "a browser cannot tell its own save from another's")
        self.assertEqual(m["rev"], json.loads(b)["rev"])

    def test_a_daemon_that_starts_over_a_file_serves_it(self):
        """The file is meant to outlive a daemon — restore.json's reason, and this one's. The helper
        makes its HOME before it starts the daemon, so the file a previous daemon would have left
        can simply be put there first."""
        d = Daemon()
        path = os.path.join(d.home, ".palmar")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "layout.json"), "w") as fh:
            json.dump({"v": 1, "layout": self.BOARD}, fh)
        d.start()
        try:
            self.assertEqual(d.get("/api/layout")["layout"], self.BOARD)
        finally:
            d.stop()

    def test_a_save_needs_the_token(self):
        st, _ = self.d.raw("PUT", "/api/layout", {"layout": {}}, token=False)
        self.assertEqual(st, 403)

    def test_it_refuses_what_is_not_a_board(self):
        """What goes in is served back to every browser, so the shape is checked on the way in."""
        bad = [
            {"layout": []},                                         # not an object
            {"layout": {"abc": {"x": "40"}}},                       # a number as a string
            {"layout": {"abc": {"x": True}}},                       # bool is an int in Python — refused
            {"layout": {"abc": {"g": 5}}},                          # a group id that is not a string
            {"layout": {"../etc": {"x": 1}}},                       # not an id
            {"nope": {}},                                           # no board at all
        ]
        for body in bad:
            st, _ = self.d.raw("PUT", "/api/layout", body)
            self.assertEqual(st, 400, "accepted %r" % (body,))
        # A field the page does not write is dropped, not refused: the page may grow one.
        st, _ = self.d.raw("PUT", "/api/layout", {"layout": {"abc": {"x": 1, "y": 2, "colour": "red"}}})
        self.assertEqual(st, 200)
        self.assertEqual(self.d.get("/api/layout")["layout"], {"abc": {"x": 1, "y": 2}})


class WhatRunsInAPane(unittest.TestCase):
    """The daemon names the foreground command — whatever it is, not a list (user, 2026-09-15:
    "claude 를 실행시켰을 때는 claude, aelix 를 실행했을 때는 aelix … 하드코딩 말고"). The foreground
    process group was already read for the status lights; its leader's name is one call further."""

    def test_the_foreground_command_is_reported_and_cleared(self):
        if sys.platform == "win32":
            self.skipTest("Windows has no foreground process group — the title is the way in (#30)")
        with Daemon() as d:
            s = d.open_pane(d.home, name=None)
            time.sleep(1.0)
            w = WS(d, "/pty/%s?token=%s&cols=80&rows=24" % (s["id"], d.token))
            self.addCleanup(w.close)
            w.recv_json()
            w.send(b"sleep 30\r", opcode=0x2)
            fg = None
            for _ in range(60):                       # a silent command is seen by the 10s tick at the latest
                time.sleep(0.25)
                fg = [x for x in d.panes() if x["id"] == s["id"]][0].get("fg")
                if fg:
                    break
            self.assertEqual(fg, "sleep", "the daemon did not name what is running: %r" % fg)
            w.send(b"\x03", opcode=0x2)              # Ctrl-C: back at the prompt
            for _ in range(60):
                time.sleep(0.25)
                fg = [x for x in d.panes() if x["id"] == s["id"]][0].get("fg")
                if fg is None:
                    break
            self.assertIsNone(fg, "it still names a command at a prompt: %r" % fg)


class LookingAtADocument(unittest.TestCase):
    """The rail stopped being a launcher and became the way to look at a document (user, 2026-09-15).
    The listing carries files, and GET /api/file serves one back as it is — text as text, images as
    images, nothing else — within the boundary browsing already has."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.dir = os.path.join(cls.d.home, "docs")
        os.makedirs(os.path.join(cls.dir, "sub"))
        with open(os.path.join(cls.dir, "notes.md"), "w", encoding="utf-8") as fh:
            fh.write("# 메모\n\nhello, 팔마\n")
        with open(os.path.join(cls.dir, "blob.bin"), "wb") as fh:
            fh.write(b"\x00\x01\x02 not text")
        with open(os.path.join(cls.dir, "dot.png"), "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
        with open(os.path.join(cls.dir, "huge.txt"), "wb") as fh:
            fh.write(b"x" * (2 * 1024 * 1024 + 1))
        with open(os.path.join(cls.dir, ".hidden"), "w") as fh:
            fh.write("no")

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def test_the_listing_has_folders_then_files(self):
        d = self.d.get("/api/dirs?path=" + self.dir)
        kinds = [(e["name"], e["kind"]) for e in d["entries"]]
        self.assertEqual(kinds[0], ("sub", "dir"), "folders do not come first: %r" % kinds)
        self.assertIn(("notes.md", "file"), kinds)
        self.assertNotIn(".hidden", [n for n, _ in kinds], "a dot file was listed")
        notes = [e for e in d["entries"] if e["name"] == "notes.md"][0]
        self.assertEqual(notes["size"], os.path.getsize(os.path.join(self.dir, "notes.md")))

    def test_text_comes_back_as_text(self):
        st, b = self.d.raw("GET", "/api/file?path=" + os.path.join(self.dir, "notes.md"))
        self.assertEqual(st, 200, b)
        self.assertEqual(b.decode("utf-8"), "# 메모\n\nhello, 팔마\n")

    def test_an_image_comes_back_as_an_image(self):
        st, b = self.d.raw("GET", "/api/file?path=" + os.path.join(self.dir, "dot.png"))
        self.assertEqual(st, 200)
        self.assertTrue(b.startswith(b"\x89PNG"))

    def test_what_is_neither_is_refused(self):
        st, b = self.d.raw("GET", "/api/file?path=" + os.path.join(self.dir, "blob.bin"))
        self.assertEqual(st, 415, b)
        # An image too big to draw is refused; text that size is shown from the top instead (below).
        big = os.path.join(self.dir, "big.png")
        with open(big, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n"); fh.truncate(65 * 1024 * 1024)
        st, b = self.d.raw("GET", "/api/file?path=" + big)
        self.assertEqual(st, 413, b)

    def test_a_big_text_file_is_shown_from_the_top_and_is_read_only(self):
        """Most real files were over the old limit and the viewer just said no (user, 2026-09-15). The
        first TEXT_HEAD bytes go out, cut at a line end, with the whole size named — and no offer to
        edit, because saving a head over a whole would destroy the rest."""
        import urllib.request
        p = os.path.join(self.dir, "long.log")
        with open(p, "wb") as fh:
            for i in range(300000):
                fh.write(b"line %07d of a log that is bigger than the head\n" % i)
        total = os.path.getsize(p)
        req = urllib.request.Request(self.d.base + "/api/file?path=" + p + "&token=" + self.d.token)
        req.add_header("Origin", self.d.base)
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
            self.assertEqual(r.headers.get("X-Palmar-Truncated"), str(total))
            self.assertEqual(r.headers.get("X-Palmar-Editable"), "0", "a head must not be editable")
        self.assertLess(len(body), total)
        self.assertGreater(len(body), 3 * 1024 * 1024)
        self.assertTrue(body.endswith(b"\n"), "the head was cut in the middle of a line")
        st, b = self.d.raw("GET", "/api/file?path=" + self.dir)
        self.assertEqual(st, 400, "a folder was served as a file")
        st, b = self.d.raw("GET", "/api/file?path=" + os.path.join(self.dir, "notes.md"), token=False)
        self.assertEqual(st, 403, "a file went out without the token")

    def test_the_board_keeps_a_viewer(self):
        st, b = self.d.raw("PUT", "/api/layout", {"layout": {"v:abc": {"x": 1, "y": 2, "w": 300, "h": 200, "kind": "file",
                                                                        "path": "/tmp/x.md", "canvas": "c1"}}})
        self.assertEqual(st, 200, b)
        self.assertEqual(self.d.get("/api/layout")["layout"]["v:abc"]["path"], "/tmp/x.md")


class EditingAFile(unittest.TestCase):
    """Text and Markdown can be changed from the viewer (user, 2026-09-15); everything else is read-only,
    and a save that would land on top of somebody else's write is refused rather than won."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()
        cls.dir = os.path.join(cls.d.home, "edit")
        os.makedirs(cls.dir)

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def write(self, name, text="one\n"):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def put(self, path, body, mtime=None):
        q = "/api/file?path=" + path + ("&mtime=" + str(mtime) if mtime is not None else "")
        return self.d.raw("PUT", q, body)

    def test_a_text_file_saves_and_says_its_new_stamp(self):
        p = self.write("notes.md")
        st, b = self.d.raw("GET", "/api/file?path=" + p)
        self.assertEqual(st, 200)
        st, b = self.put(p, "두 번째\n".encode())
        self.assertEqual(st, 200, b)
        self.assertIn("mtime", json.loads(b))
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "두 번째\n")

    def test_it_keeps_the_mode_the_file_had(self):
        if os.name == "nt":
            self.skipTest("no POSIX modes here")
        p = self.write("script.sh")
        os.chmod(p, 0o750)
        self.assertEqual(self.put(p, b"echo hi\n")[0], 200)
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o750, "saving changed the file's mode")

    def test_a_save_that_would_land_on_somebody_else_is_refused(self):
        p = self.write("shared.txt")
        stamp = float(os.stat(p).st_mtime)
        time.sleep(0.02)
        with open(p, "w", encoding="utf-8") as fh:      # the agent in the terminal beside it
            fh.write("theirs\n")
        st, b = self.put(p, b"mine\n", mtime=stamp)
        self.assertEqual(st, 409, b)
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "theirs\n", "it was overwritten anyway")
        # ...and it can be forced, which is what the page's "Overwrite" does.
        self.assertEqual(self.put(p, b"mine\n")[0], 200)

    def test_what_may_not_be_written(self):
        png = os.path.join(self.dir, "x.png")
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.put(png, b"nope")[0], 415, "an image was writable")
        p = self.write("ok.txt")
        self.assertEqual(self.put(p, b"a\x00b")[0], 415, "a NUL went through")
        self.assertEqual(self.d.raw("PUT", "/api/file?path=" + p, b"x", token=False)[0], 403)
        self.assertEqual(self.put("/etc/hosts", b"x")[0], 400, "a file outside home was writable")

    def test_the_get_says_whether_it_can_be_edited(self):
        import urllib.request
        for name, want in (("a.md", "1"), ("b.png", "0")):
            p = os.path.join(self.dir, name)
            with open(p, "wb") as fh:
                fh.write(b"x")
            req = urllib.request.Request(self.d.base + "/api/file?path=" + p + "&token=" + self.d.token)
            req.add_header("Origin", self.d.base)
            with urllib.request.urlopen(req, timeout=10) as r:
                self.assertEqual(r.headers.get("X-Palmar-Editable"), want, name)
                self.assertTrue(r.headers.get("X-Palmar-Mtime"), "no stamp on " + name)

    def test_open_with_needs_a_file_under_home(self):
        self.assertEqual(self.d.raw("POST", "/api/open", {"path": "/etc/hosts"})[0], 400)
        self.assertEqual(self.d.raw("POST", "/api/open", {"path": self.dir})[0], 400, "a folder was accepted")
        self.assertEqual(self.d.raw("POST", "/api/open", {"path": self.write("z.txt")}, token=False)[0], 403)


def stderr_so_far(d, want, seconds=5.0):
    """What a foreground daemon has said on stderr so far — polled, since the pipe never closes
    while it runs. Stops as soon as `want` is in it."""
    got = b""
    end = time.time() + seconds
    fd = d.proc.stderr.fileno()
    while time.time() < end and want.encode() not in got:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            got += os.read(fd, 65536)
    return got.decode("utf-8", "replace")


class WhenThePortIsTaken(unittest.TestCase):
    """8801 belongs to something else — a palmar on Windows beside this one in WSL, another user's,
    anything. It used to be the end (`could not bind`); now the next free port is taken and the
    printout says so, because the person asked for palmar, not for a number (user, 2026-09-15)."""

    def test_it_takes_the_next_free_port_and_says_so(self):
        d = Daemon()
        asked = d.port
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", asked))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        d.start()
        self.addCleanup(d.stop)
        self.assertNotEqual(d.port, asked)
        self.assertTrue(asked < d.port <= asked + 20, "it went further than the twenty it is allowed")
        with open(d.url_file, encoding="utf-8") as fh:
            self.assertIn(":%d/" % d.port, fh.read(), "run/url does not name the port it is really on")
        with open(os.path.join(os.path.dirname(d.url_file), "lock"), encoding="utf-8") as fh:
            self.assertIn(":%d" % d.port, fh.read(), "the lock line still names the port it asked for — --doctor reads that")
        said = stderr_so_far(d, "--stop")
        self.assertIn("port %d was taken" % asked, said)
        self.assertIn("on %d" % d.port, said)
        # --stop finds it there: it goes by run/url, not by the number that was asked for.
        from tests.helpers import PYTHON, REPO
        r = subprocess.run([PYTHON, "-m", "palmar", "--stop"], cwd=REPO, capture_output=True, text=True,
                           timeout=30, env=dict(os.environ, HOME=d.home, PYTHONPATH=REPO))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        d.proc.wait(timeout=10)

    def test_the_printout_says_how_to_stop_and_where_the_address_is(self):
        """The address alone was the whole printout, and the first question after it was how to stop
        the thing (user, 2026-09-15)."""
        d = Daemon().start()
        self.addCleanup(d.stop)
        said = stderr_so_far(d, "run/url")
        self.assertIn("`palmar --stop` ends it", said)
        self.assertIn("`palmar` again shows this address", said)
        self.assertNotIn("port %d was taken" % d.port, said, "nothing to say about a port that was free")
        out = d.proc.stdout   # stdout holds the address and only the address (docs/protocol.md)
        r, _, _ = select.select([out.fileno()], [], [], 2.0)
        self.assertTrue(r, "no address on stdout")
        line = os.read(out.fileno(), 4096).decode("utf-8", "replace")
        self.assertTrue(line.startswith("http://127.0.0.1:%d/?k=" % d.port), line)
        self.assertEqual(len(line.strip().splitlines()), 1, "more than the address went to stdout")


class TheKeyPage(unittest.TestCase):
    """Two answers for a wrong address, because they are two situations. No key: the owner lost the
    address. The wrong key: **another palmar's** address on this port — a palmar in WSL beside one on
    Windows landed on the Windows one's port and was told to `cat run/url`, which named the wrong
    daemon's file (user, 2026-09-15)."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def page(self, path):
        base = self.d.url.split("/?", 1)[0]
        try:
            with urllib.request.urlopen(base + path, timeout=5) as f:
                return f.status, f.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def test_no_key_is_told_where_the_address_is(self):
        code, body = self.page("/")
        self.assertEqual(code, 403)
        self.assertIn("needs the key", body)
        self.assertNotIn("different palmar", body)

    def test_a_wrong_key_is_told_it_is_another_daemon(self):
        code, body = self.page("/?k=notthisone")
        self.assertEqual(code, 403)
        self.assertIn("different palmar", body)
        self.assertIn("WSL beside one on Windows", body)
        self.assertNotIn(self.d.url.split("k=", 1)[1], body, "the real key went out on the wrong-key page")


class TheWindowOpensFirst(unittest.TestCase):
    """`palmar` opened a browser tab while the window sat right there; the web button was made so
    the browser is the thing you *ask* for (user, 2026-09-15). The window first, when there is one;
    `--web` for a browser; the web button always a browser; a window that dies at once gives way
    to the browser and says so."""

    def fakes(self):
        box = tempfile.mkdtemp(prefix="palmar-window-")
        self.addCleanup(shutil.rmtree, box, ignore_errors=True)
        app_ran, browser_ran = os.path.join(box, "app-ran"), os.path.join(box, "browser-argv")
        app = os.path.join(box, "palmar-app")
        with open(app, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%s\\n" "$@" > ' + app_ran + '\n')
        browser = os.path.join(box, "browser.sh")
        with open(browser, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%s\\n" "$@" > ' + browser_ran + '\n')
        for f in (app, browser):
            os.chmod(f, 0o755)
        return app, browser, app_ran, browser_ran

    def wait_for(self, path, secs=6.0):
        end = time.time() + secs
        while time.time() < end and not os.path.exists(path):
            time.sleep(0.1)
        return os.path.exists(path)

    def test_the_window_when_there_is_one_and_the_web_button_still_means_a_browser(self):
        app, browser, app_ran, browser_ran = self.fakes()
        with Daemon(env={"PALMAR_APP": app, "BROWSER": browser}, browser=True) as d:
            self.assertTrue(self.wait_for(app_ran), "the window was not opened")
            with open(app_ran) as fh:
                self.assertEqual(fh.read().strip(), "", "the window was handed arguments — it finds the daemon by run/url itself")
            time.sleep(1.0)
            self.assertFalse(os.path.exists(browser_ran), "a browser opened as well as the window")
            said = stderr_so_far(d, "opening the window")
            self.assertIn("opening the window", said)
            self.assertEqual(d.raw("POST", "/api/address/open")[0], 204)
            self.assertTrue(self.wait_for(browser_ran), "the web button opened nothing")
            with open(browser_ran) as fh:
                arg = fh.read().strip()
            self.assertTrue(arg.startswith("file://"), "the web button must hand the browser the file stand-in: " + arg)
            self.assertNotIn("k=", arg, "the key went onto the command line")

    def test_web_asks_for_a_browser_outright(self):
        app, browser, app_ran, browser_ran = self.fakes()
        with Daemon(env={"PALMAR_APP": app, "PALMAR_CHROMIUM": app, "BROWSER": browser}, browser=True, web=True) as d:
            self.assertTrue(self.wait_for(browser_ran), "--web opened no browser")
            time.sleep(0.5)
            self.assertFalse(os.path.exists(app_ran), "--web opened a window too")

    def test_without_a_window_of_our_own_a_chromium_opens_in_app_mode(self):
        """"Does Windows really need an exe?" — no. The same fake stands in for Edge here: it gets
        the address on --app=, the browser fake gets nothing until the web button asks for a tab."""
        app, browser, app_ran, browser_ran = self.fakes()
        with Daemon(env={"PALMAR_APP": "0", "PALMAR_CHROMIUM": app, "BROWSER": browser}, browser=True) as d:
            self.assertTrue(self.wait_for(app_ran), "no window in app mode")
            with open(app_ran) as fh:
                arg = fh.read().strip()
            self.assertTrue(arg.startswith("--app=file://"), "app mode must get the file stand-in, not the keyed address: " + arg)
            self.assertNotIn("k=", arg)
            said = stderr_so_far(d, "app mode")
            self.assertIn("opening a window with palmar-app (app mode", said)
            time.sleep(0.5)
            self.assertFalse(os.path.exists(browser_ran), "a tab opened as well as the window")
            self.assertEqual(d.raw("POST", "/api/address/open")[0], 204)
            self.assertTrue(self.wait_for(browser_ran), "the web button opened no tab")
            with open(browser_ran) as fh:
                arg = fh.read().strip()
            self.assertTrue(arg.startswith("file://") and "--app=" not in arg, "the web button must open a plain tab, not app mode: " + arg)

    def test_a_window_that_dies_gives_way_to_the_browser(self):
        app, browser, app_ran, browser_ran = self.fakes()
        with open(app, "w") as fh:
            fh.write('#!/bin/sh\necho "palmar-app: no webkit here" >&2\nexit 3\n')
        with Daemon(env={"PALMAR_APP": app, "BROWSER": browser}, browser=True) as d:
            self.assertTrue(self.wait_for(browser_ran), "no browser after the window died")
            said = stderr_so_far(d, "did not start")
            self.assertIn("the window did not start (palmar-app: no webkit here)", said)

    def test_the_web_button_says_when_no_browser_can_be_opened(self):
        with Daemon(env={"BROWSER": "/nonexistent/definitely-not-here"}) as d:
            self.assertEqual(d.raw("POST", "/api/address/open")[0], 500, "the 500 the protocol promises")


class InstalledAsAnApp(unittest.TestCase):
    """"Isn't app mode still a browser?" (user, 2026-09-15). The next step up needs no binary either:
    a web app manifest lets Edge and Chrome install the page as an app of its own. The manifest holds
    the key in start_url, so it goes out only with the key, like index.html (#14)."""

    @classmethod
    def setUpClass(cls):
        cls.d = Daemon().start()

    @classmethod
    def tearDownClass(cls):
        cls.d.stop()

    def get(self, path):
        base = self.d.url.split("/?", 1)[0]
        try:
            with urllib.request.urlopen(base + path, timeout=5) as f:
                return f.status, f.headers.get("content-type", ""), f.read()
        except urllib.error.HTTPError as e:
            return e.code, "", e.read()

    def test_the_manifest_needs_the_key_and_carries_it(self):
        key = self.d.url.split("k=", 1)[1]
        code, ctype, body = self.get("/manifest.webmanifest?k=" + key)
        self.assertEqual(code, 200)
        self.assertIn("manifest+json", ctype)
        m = json.loads(body)
        self.assertEqual(m["start_url"], "/?k=" + key)
        self.assertEqual(m["display"], "standalone")
        self.assertEqual(m["name"], "palmar")
        self.assertTrue(any(i["sizes"] == "512x512" for i in m["icons"]))
        self.assertEqual(self.get("/manifest.webmanifest")[0], 403, "the manifest went out without the key — it holds it")
        self.assertEqual(self.get("/manifest.webmanifest?k=nope")[0], 403)

    def test_the_icons_are_plain_files_and_the_page_still_holds_no_key(self):
        """The link to the manifest is the page's to build from its own address (app.js) — index.html
        carrying the key is exactly what #14 closed, and TheAddress guards that too."""
        key = self.d.url.split("k=", 1)[1]
        code, ctype, body = self.get("/?k=" + key)
        self.assertEqual(code, 200)
        self.assertNotIn(key.encode(), body)
        self.assertNotIn(b"manifest", body)
        self.assertIn(b'<link rel="icon" href="icon-192.png">', body)
        for icon in ("/icon-192.png", "/icon-512.png"):
            code, ctype, body = self.get(icon)
            self.assertEqual(code, 200, icon)
            self.assertEqual(ctype, "image/png")
            self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n", icon + " is not a PNG")


class TheDaemonNotifies(unittest.TestCase):
    """POST /api/notify: the OS says "a terminal wants you" for a window that has no Notification API
    of its own. 501 when this machine has no way, so the page can say so."""

    def test_the_words_reach_the_notifier(self):
        box = tempfile.mkdtemp(prefix="palmar-notify-")
        self.addCleanup(shutil.rmtree, box, ignore_errors=True)
        note = os.path.join(box, "said")
        say = os.path.join(box, "say.sh")
        with open(say, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%s\\n" "$@" > ' + note + '\n')
        os.chmod(say, 0o755)
        with Daemon(env={"PALMAR_NOTIFIER": say}) as d:
            code, _ = d.raw("POST", "/api/notify", json.dumps({"title": "agent-1 wants you", "body": "/home/me"}).encode())
            self.assertEqual(code, 204)
            end = time.time() + 5
            while time.time() < end and not os.path.exists(note):
                time.sleep(0.1)
            with open(note) as fh:
                self.assertEqual(fh.read().split("\n")[:2], ["agent-1 wants you", "/home/me"])

    def test_no_way_here_is_a_501(self):
        with Daemon(env={"PALMAR_NOTIFIER": "0"}) as d:
            self.assertEqual(d.raw("POST", "/api/notify", b'{"title": "x"}')[0], 501)
            self.assertEqual(d.raw("GET", "/api/notify")[0], 405)


class TheAddressDiesWithTheDaemon(unittest.TestCase):
    """run/url used to outlive the daemon, and everything that found it trusted a bare TCP connect to
    say the address was still palmar's — so any local account could bind the old port and be handed
    the key (review, 2026-09-15). Now the file is removed the moment a daemon takes the lock and
    again when it stops: on disk means alive."""

    def test_a_clean_stop_removes_it(self):
        d = Daemon().start()
        self.assertTrue(os.path.exists(d.url_file))
        d.stop(wipe=False)
        self.addCleanup(shutil.rmtree, d.home, ignore_errors=True)
        self.assertFalse(os.path.exists(d.url_file), "run/url survived the daemon")

    def test_a_start_removes_a_stale_one_before_it_binds(self):
        d = Daemon()
        run = os.path.dirname(d.url_file)
        os.makedirs(run, mode=0o700, exist_ok=True)
        with open(d.url_file, "w") as fh:
            fh.write("http://127.0.0.1:1/?k=stale\n")           # a dead daemon's, or a squatter's
        d.start()
        self.addCleanup(d.stop)
        with open(d.url_file) as fh:
            self.assertNotIn("stale", fh.read())


class OnWslTheAddressOpensOnce(unittest.TestCase):
    """From WSL the browser is on the Windows side and a Linux file cannot cross, so the command
    line carries a short-lived address: /once/<nonce> turns into the keyed address for thirty
    seconds — any number of times inside them, since Chrome fetches --app= twice when the app is
    installed (user, 2026-09-16), and never after."""

    def test_the_nonce_opens_once(self):
        box = tempfile.mkdtemp(prefix="palmar-once-")
        self.addCleanup(shutil.rmtree, box, ignore_errors=True)
        note = os.path.join(box, "argv")
        opener = os.path.join(box, "opener.sh")
        with open(opener, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%s\\n" "$@" > ' + note + '\n')
        os.chmod(opener, 0o755)
        # WSL_DISTRO_NAME alone makes wsl_kind() say "wsl" — the seam the WSL tests already use.
        with Daemon(env={"BROWSER": opener, "WSL_DISTRO_NAME": "Ubuntu"}) as d:
            self.assertEqual(d.raw("POST", "/api/address/open")[0], 204)
            end = time.time() + 10
            while time.time() < end and not os.path.exists(note):
                time.sleep(0.1)
            with open(note) as fh:
                argv = fh.read().split("\n")
            once = [a for a in argv if "/once/" in a]
            self.assertTrue(once, "no one-time address on the command line: %r" % argv)
            self.assertFalse(any("k=" in a for a in argv), "the key went onto the command line: %r" % argv)
            base = d.url.split("/?", 1)[0]
            path = "/once/" + once[0].rsplit("/once/", 1)[1]
            code, headers = self.head(base + path)
            self.assertEqual(code, 302)
            self.assertEqual(headers.get("Location"), "/?k=" + d.url.split("k=", 1)[1])
            code, _ = self.head(base + path)
            self.assertEqual(code, 302, "a second fetch inside the seconds must work — Chrome fetches --app= twice when the app is installed")
            self.assertEqual(self.head(base + "/once/never-minted")[0], 404)

    def head(self, url):
        import http.client
        u = urllib.parse.urlsplit(url)
        c = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
        c.request("GET", u.path)
        r = c.getresponse()
        r.read()
        return r.status, dict(r.getheaders())


class NothingRunsFromTheViewer(unittest.TestCase):
    def test_an_executable_is_refused_by_open(self):
        with Daemon() as d:
            f = os.path.join(d.home, "run.sh")
            with open(f, "w") as fh:
                fh.write("#!/bin/sh\necho hi\n")
            os.chmod(f, 0o755)
            code, body = d.raw("POST", "/api/open", json.dumps({"path": f}).encode())
            self.assertEqual(code, 415, body)
            self.assertIn(b"would run", body)


class LinksAreNotWrittenThrough(unittest.TestCase):
    """A cloned repository decides where its links point — `notes.txt -> ~/.zshrc` — and a save to
    what looks like a repo file used to land on the target (review, 2026-09-15)."""

    def test_the_listing_says_link_and_put_refuses(self):
        with Daemon() as d:
            repo = os.path.join(d.home, "repo"); os.makedirs(repo)
            secret = os.path.join(d.home, ".zshrc")
            with open(secret, "w") as fh:
                fh.write("export SECRET=1\n")
            os.symlink(secret, os.path.join(repo, "notes.txt"))
            rows = d.get("/api/dirs?path=" + urllib.parse.quote(repo))
            rows = rows.get("entries", rows) if isinstance(rows, dict) else rows
            row = [r for r in (rows if isinstance(rows, list) else []) if r.get("name") == "notes.txt"]
            self.assertTrue(row and row[0].get("link"), "the listing does not say it is a link: %r" % rows)
            code, body = d.raw("PUT", "/api/file?path=" + urllib.parse.quote(os.path.join(repo, "notes.txt")), b"owned\n")
            self.assertEqual(code, 400, body)
            with open(secret) as fh:
                self.assertEqual(fh.read(), "export SECRET=1\n", "the save went through the link")


class ATempNameNobodyCouldPlant(unittest.TestCase):
    """The temp file beside a saved file used to have a fixed name, and the folder is a cloned
    repository's to fill: a FIFO by that name blocked the daemon, a hard link by that name was
    truncated in place with the file it pointed at (Codex review, 2026-09-15)."""

    def test_a_fifo_by_the_old_name_does_not_block_the_save(self):
        with Daemon() as d:
            f = os.path.join(d.home, "notes.txt")
            with open(f, "w") as fh:
                fh.write("one\n")
            os.mkfifo(os.path.join(d.home, "notes.txt.palmar-tmp"))
            code, body = d.raw("PUT", "/api/file?path=" + urllib.parse.quote(f), b"two\n")
            self.assertEqual(code, 200, body)
            with open(f) as fh:
                self.assertEqual(fh.read(), "two\n")

    def test_a_hard_link_by_the_old_name_does_not_truncate_its_target(self):
        with Daemon() as d:
            f = os.path.join(d.home, "notes.txt")
            victim = os.path.join(d.home, "victim.txt")
            for p, text in ((f, "one\n"), (victim, "keep me\n")):
                with open(p, "w") as fh:
                    fh.write(text)
            os.link(victim, os.path.join(d.home, "notes.txt.palmar-tmp"))
            code, body = d.raw("PUT", "/api/file?path=" + urllib.parse.quote(f), b"two\n")
            self.assertEqual(code, 200, body)
            with open(victim) as fh:
                self.assertEqual(fh.read(), "keep me\n", "the hard link's target was truncated")


class StopTrustsTheLock(unittest.TestCase):
    """A free lock means no daemon, whatever answers on the old address: --stop used to send that
    listener the token and SIGTERM the pid a stale lock line named (review, 2026-09-15)."""

    def test_a_squatter_on_a_stale_address_is_not_a_daemon(self):
        home = tempfile.mkdtemp(prefix="palmar-stopsq-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        run = os.path.join(home, ".palmar", "run"); os.makedirs(run, mode=0o700)
        sq = socket.socket(); sq.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); sq.bind(("127.0.0.1", 0)); sq.listen(1)
        self.addCleanup(sq.close)
        port = sq.getsockname()[1]
        with open(os.path.join(run, "url"), "w") as fh:
            fh.write("http://127.0.0.1:%d/?k=stale\n" % port)
        with open(os.path.join(run, "lock"), "w") as fh:
            fh.write("pid %d http://127.0.0.1:%d\n" % (os.getpid(), port))     # our own pid: a SIGTERM would be felt
        from tests.helpers import PYTHON, REPO
        r = subprocess.run([PYTHON, "-m", "palmar", "--stop"], cwd=REPO, capture_output=True, text=True, timeout=30,
                           env=dict(os.environ, HOME=home, PYTHONPATH=REPO))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no daemon is running", r.stdout)
        self.assertIn("not palmar's", r.stdout)


class ThePageIsFenced(unittest.TestCase):
    def test_index_html_carries_a_script_src_policy_with_a_nonce(self):
        with Daemon() as d:
            import http.client
            u = urllib.parse.urlsplit(d.url)
            c = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
            c.request("GET", "/?" + u.query)
            r = c.getresponse(); body = r.read().decode("utf-8", "replace")
            csp = [v for k, v in r.getheaders() if k.lower() == "content-security-policy"]
            self.assertTrue(any("script-src 'self' 'nonce-" in v for v in csp), csp)
            self.assertTrue(any("frame-ancestors 'none'" in v for v in csp), csp)
            nonce = [v for v in csp if "nonce-" in v][0].split("nonce-", 1)[1].split("'", 1)[0]
            self.assertIn('<script nonce="%s">window.PALMAR_TOKEN=' % nonce, body)
            self.assertEqual(dict(r.getheaders()).get("X-Content-Type-Options"), "nosniff")
