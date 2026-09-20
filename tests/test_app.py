"""palmar's window (app/) — the parts that can be checked without a screen.

The window itself cannot be asserted from here: opening one needs a display, and what it shows is
the same UI tests/test_browser.py already drives against the daemon. What this file protects is the
thing that actually breaks — **the source stops compiling** — plus the two promises the app makes
about how it finds a daemon.

Skipped where there is no Rust toolchain, which is most machines that only run the Python side.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "app")


def can_build_app():
    """Whether this machine could compile the window, not merely whether it has cargo.

    **A GitHub runner has cargo and no WebKitGTK**, so "cargo is here" let the test try and fail on
    a pkg-config error that says nothing about palmar (CI, 2026-09-11). On Linux the build needs
    webkit2gtk-4.1's development files; on macOS the webview is in the OS."""
    if not shutil.which("cargo"):
        return False
    if sys.platform.startswith("linux"):
        if not shutil.which("pkg-config"):
            return False
        return subprocess.run(["pkg-config", "--exists", "webkit2gtk-4.1"],
                              capture_output=True).returncode == 0
    return True


@unittest.skipUnless(can_build_app(), "nothing here can build the window")
@unittest.skipUnless(os.path.isdir(APP), "no app/ in this tree")
class Builds(unittest.TestCase):
    def test_it_compiles(self):
        """`cargo check`, not `cargo build` — it catches the same errors and, warm, takes seconds.
        Cold on a machine that has never fetched the crates it is minutes, which is why this is
        skipped rather than required anywhere that has no toolchain."""
        r = subprocess.run(["cargo", "check", "--release"], cwd=APP,
                           capture_output=True, text=True, timeout=900)
        self.assertEqual(r.returncode, 0, (r.stderr or "")[-2000:])


@unittest.skipUnless(sys.platform == "darwin", "the menu bar is a macOS thing")
@unittest.skipUnless(can_build_app(), "nothing here can build the window")
class TheMenuBar(unittest.TestCase):
    """**On macOS a menu is not decoration — it is where the keyboard lives.** This binary had none,
    and three things followed. A window the green button sent to full screen could not come back:
    `⌃⌘F` is a menu item's shortcut, and the title bar that slides down at the top of the screen
    slides down with the menu bar, which was not there either (user, 2026-09-21). `⌘Q` did nothing.
    And `⌘C`/`⌘V` are `copy:` and `paste:` sent down the responder chain by the Edit menu — with no
    Edit menu there is nothing to send them.

    The window needs a display, so what it does cannot be asserted here. What the bar **holds** can:
    the app prints it on `--print-menu`, which is the only proof available without a hand."""

    @classmethod
    def setUpClass(cls):
        r = subprocess.run(["cargo", "build", "--release"], cwd=APP,
                           capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise unittest.SkipTest((r.stderr or "")[-800:])
        cls.bin = os.path.join(APP, "target", "release", "palmar-app")

    def menu(self):
        # It needs a daemon to point at, and one that is not this machine's own.
        from tests.helpers import Daemon
        d = Daemon().start()
        try:
            p = subprocess.Popen([self.bin, d.url, "--print-menu"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            time.sleep(5)
            p.terminate()
            try:
                out = p.communicate(timeout=8)[0]
            except Exception:
                p.kill()
                out = p.communicate()[0]
        finally:
            d.stop()
        return dict(
            (line.split(":", 1)[0], line.split(":", 1)[1])
            for line in (out or "").strip().splitlines() if ":" in line)

    def test_the_four_menus_and_what_they_carry(self):
        m = self.menu()
        self.assertTrue(m, "the app printed no menu bar at all")
        self.assertIn("palmar", m)
        self.assertIn("Quit palmar [q]", m["palmar"], "there is no way to quit from the keyboard")
        # The one that carries the clipboard. Without it ⌘C and ⌘V reach nothing.
        self.assertIn("Edit", m)
        for want in ("Copy [c]", "Paste [v]", "Cut [x]", "Select All [a]"):
            self.assertIn(want, m["Edit"], "the Edit menu is missing %s: %r" % (want, m["Edit"]))
        # The way out of full screen, which is the whole reason this exists.
        self.assertIn("View", m)
        self.assertIn("Enter Full Screen [^f]", m["View"],
                      "full screen has no way back on the keyboard: %r" % (m["View"],))
        # Zoom is what Windows calls maximize, and unlike full screen it toggles back.
        self.assertIn("Window", m)
        self.assertIn("Zoom", m["Window"])
        self.assertIn("Minimize [m]", m["Window"])

    def test_the_edit_menu_is_not_padded_out_by_the_system(self):
        """AppKit adds items to any menu called "Edit" by itself — dictation and the character
        palette — and here it added them three times over (measured by reading the bar back). Two
        defaults are the switch for them; a terminal has no use for either."""
        edit = self.menu().get("Edit", "")
        for unwanted in ("Dictation", "Emoji"):
            self.assertNotIn(unwanted, edit, "the system padded the Edit menu out: %r" % (edit,))


class Promises(unittest.TestCase):
    """Two claims in app/src/main.rs that the Python side has to keep true, read out of the source
    so that deleting one of them here breaks a test rather than the app.

    **This is a contract test, not a style check.** `--no-browser` is how the app stops the daemon
    from also opening a tab, and reading the address off stdout only works while stdout's last line
    is the address (docs/protocol.md)."""

    def source(self):
        with open(os.path.join(APP, "src", "main.rs")) as fh:
            return fh.read()

    @unittest.skipUnless(os.path.isdir(APP), "no app/ in this tree")
    def test_it_starts_the_daemon_with_no_browser(self):
        self.assertIn('"--no-browser"', self.source(),
                      "the app would start a daemon that also opens a browser tab")

    @unittest.skipUnless(os.path.isdir(APP), "no app/ in this tree")
    def test_the_flag_it_passes_still_exists(self):
        """If --no-browser were ever renamed, the app would silently open two things."""
        r = subprocess.run(["python3", "-m", "palmar", "--help"], cwd=REPO,
                           capture_output=True, text=True, timeout=30)
        self.assertIn("--no-browser", r.stdout)


class Scripts(unittest.TestCase):
    """The two shell scripts around the window. `/bin/sh`, not the interactive shell — that is what
    the instructions tell people to run them with, and it is stricter."""

    PROBE = os.path.join(REPO, "dev", "wslg-probe.sh")
    SETUP = os.path.join(REPO, "app", "setup-linux.sh")

    @unittest.skipUnless(os.path.exists("/bin/sh"), "no /bin/sh")
    def test_both_are_valid_sh(self):
        for path in (self.PROBE, self.SETUP):
            if not os.path.exists(path):
                continue
            r = subprocess.run(["/bin/sh", "-n", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, path + ":\n" + r.stderr)

    @unittest.skipUnless(os.path.exists("/bin/sh"), "no /bin/sh")
    @unittest.skipUnless(os.path.exists(os.path.join(REPO, "dev", "wslg-probe.sh")), "no probe")
    def test_the_probe_changes_nothing(self):
        """**Its whole value is that it is safe to run and safe to paste.** If it ever grows a
        `sudo`, an `apt install` or an installer pipe, that is gone — and app/setup-linux.sh next
        door is where those belong.

        Comments and message lines are stripped first: the probe *prints* `sudo apt install …` as
        advice, which is the point of it, and only what it would **run** is checked."""
        printers = ("say", "note", "ok", "bad", "head2", "die")
        lines = []
        with open(self.PROBE) as fh:
            for line in fh:
                bare = line.lstrip()
                if bare.startswith("#"):
                    continue
                if bare.split(" ", 1)[0].rstrip("\n") in printers:
                    continue      # a message, not a command
                lines.append(line)
        body = "".join(lines)
        for danger in ("sudo", "apt-get install", "apt install", "apt-get update",
                       "rm -r", "rustup.rs", "curl", "wget", "| sh"):
            self.assertNotIn(danger, body, "the probe would change the machine: " + danger)

    @unittest.skipUnless(os.path.exists("/bin/sh"), "no /bin/sh")
    @unittest.skipUnless(os.path.exists(os.path.join(REPO, "app", "setup-linux.sh")), "no setup")
    def test_setup_refuses_off_linux(self):
        """It installs packages. On the wrong kind of machine it has to stop, not improvise —
        this is the one branch of it a Mac can actually run."""
        r = subprocess.run(["/bin/sh", self.SETUP], capture_output=True, text=True, timeout=60)
        if sys.platform == "darwin":
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Linux", r.stdout + r.stderr)


BINARY = os.path.join(APP, "target", "release", "palmar-app")


@unittest.skipUnless(os.path.exists(BINARY), "the window is not built (cargo build --release in app/)")
class FindingTheDaemon(unittest.TestCase):
    """What the window does when it cannot reach a daemon.

    **Both tests run the real binary and neither can open a window**: PATH is emptied, so no daemon
    can be started and there is nothing to show. What is asserted is the sentence it comes back
    with, because a wrong sentence here cost a real user twenty seconds of silence (2026-09-11):
    `python3 -m palmar` was being run from `app/`, where the package is not importable, and the
    reason went to /dev/null."""

    def run_it(self, exe, cwd):
        home = tempfile.mkdtemp(prefix="palmar-appfind-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        # PATH=/nonexistent: neither `palmar` nor `python3` can be spawned, so this returns at once
        # and no daemon and no window can result. HOME is empty, so there is no run/url either.
        return subprocess.run([exe], cwd=cwd, capture_output=True, text=True, timeout=60,
                              env={"HOME": home, "PATH": "/nonexistent"})

    def test_it_finds_the_checkout_from_inside_app(self):
        """`app/` is where the build leaves you, so it is where people run it from. The python
        fallback only imports the package with the checkout as its working directory."""
        r = self.run_it(BINARY, APP)
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("run in " + REPO, out,
                      "it did not find the checkout — the python fallback would fail to import")

    def test_a_binary_outside_a_checkout_says_so(self):
        """Copied somewhere else, there is no package to fall back to. It must say that, rather
        than start something and wait out the timeout."""
        box = tempfile.mkdtemp(prefix="palmar-appcopy-")
        self.addCleanup(shutil.rmtree, box, ignore_errors=True)
        exe = os.path.join(box, "palmar-app")
        shutil.copy2(BINARY, exe)
        r = self.run_it(exe, box)
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not inside a palmar checkout", out)

    def test_it_does_not_wait_out_the_timeout_to_say_nothing(self):
        """START_TIMEOUT is 20s and is for a daemon that hangs. A daemon that cannot start at all
        has already answered, and making someone wait for that is the bug this replaced."""
        start = time.time()
        self.run_it(BINARY, APP)
        self.assertLess(time.time() - start, 10.0, "it sat on a failure that was immediate")


if __name__ == "__main__":
    unittest.main()
