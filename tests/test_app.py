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
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "app")


@unittest.skipUnless(shutil.which("cargo"), "no Rust toolchain")
@unittest.skipUnless(os.path.isdir(APP), "no app/ in this tree")
class Builds(unittest.TestCase):
    def test_it_compiles(self):
        """`cargo check`, not `cargo build` — it catches the same errors and, warm, takes seconds.
        Cold on a machine that has never fetched the crates it is minutes, which is why this is
        skipped rather than required anywhere that has no toolchain."""
        r = subprocess.run(["cargo", "check", "--release"], cwd=APP,
                           capture_output=True, text=True, timeout=900)
        self.assertEqual(r.returncode, 0, (r.stderr or "")[-2000:])


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


if __name__ == "__main__":
    unittest.main()
