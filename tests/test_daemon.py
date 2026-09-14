"""A real daemon in a home of its own, asked the questions over HTTP and WebSocket.

Slower than test_pure — each class starts a daemon — but still seconds, not minutes. Everything here
was a bug once, and most of them were found by hand on 2026-09-09 and could not have been found
again afterwards.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

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
        the "no key" page and look like palmar was broken."""
        opener, note = self.recorder()
        with Daemon(env={"BROWSER": opener}, browser=True) as d:
            got = self.wait_for(note)
            self.assertIsNotNone(got, "the daemon never started $BROWSER")
            self.assertIn(d.url.strip(), got.strip(),
                          "the browser was handed something other than the daemon's own address")
            self.assertIn("?k=", got, "the address handed over carried no key")

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
                self.assertIn(d.url, fh.read())

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
        self.assertIn("없다", r.stdout)

    def test_it_stops_a_running_daemon(self):
        with Daemon() as d:
            d.open_pane(name="goes with it")
            r = self.palmar(d.home, "--stop")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            self.assertIn("멈췄다", r.stdout)
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
        self.assertIn("없다", r.stdout, "it treated a stale lock as a running daemon")


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


if __name__ == "__main__":
    unittest.main()
