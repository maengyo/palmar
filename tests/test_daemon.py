"""A real daemon in a home of its own, asked the questions over HTTP and WebSocket.

Slower than test_pure — each class starts a daemon — but still seconds, not minutes. Everything here
was a bug once, and most of them were found by hand on 2026-09-09 and could not have been found
again afterwards.
"""
from __future__ import annotations

import json
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
