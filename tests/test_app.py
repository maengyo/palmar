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


if __name__ == "__main__":
    unittest.main()
