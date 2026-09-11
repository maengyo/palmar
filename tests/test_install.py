"""install.sh — the one-line install. It puts a launcher where the shell finds it and nothing else.

Run against /bin/sh, not the interactive shell, because that is what `curl … | sh` uses and it is
stricter (no bashisms). Skipped where /bin/sh is missing, which on a dev machine it is not.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(REPO, "install.sh")


@unittest.skipUnless(os.path.exists("/bin/sh"), "no /bin/sh")
class Install(unittest.TestCase):
    def setUp(self):
        self.prefix = tempfile.mkdtemp(prefix="palmar-inst-")
        self.addCleanup(shutil.rmtree, self.prefix, ignore_errors=True)

    def run_install(self, path=None, extra_env=None):
        env = dict(os.environ, PALMAR_PREFIX=self.prefix)
        # A clean PATH so the test picks the system python, not whatever the dev shell puts first.
        env["PATH"] = extra_env.pop("PATH", "/usr/bin:/bin") if extra_env else "/usr/bin:/bin"
        if extra_env:
            env.update(extra_env)
        return subprocess.run(["/bin/sh", INSTALL], capture_output=True, text=True, timeout=60, env=env)

    def test_it_is_valid_sh(self):
        r = subprocess.run(["/bin/sh", "-n", INSTALL], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_it_installs_a_working_launcher(self):
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        shim = os.path.join(self.prefix, "bin", "palmar")
        self.assertTrue(os.access(shim, os.X_OK), "no executable launcher was written")
        # It should say which python it chose and how to run the result.
        self.assertIn("using Python", r.stdout)

    def test_the_launcher_actually_starts_the_daemon(self):
        """The whole point: `palmar` from outside the repo brings the daemon up. Run it from a temp
        cwd so nothing but the launcher's own PYTHONPATH can find the tree."""
        self.run_install()
        shim = os.path.join(self.prefix, "bin", "palmar")
        home = tempfile.mkdtemp(prefix="palmar-inst-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        # a port unlikely to collide
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        proc = subprocess.Popen([shim, "--port", str(port), "--no-browser"], cwd="/tmp",
                                env=dict(os.environ, HOME=home),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            import time
            import urllib.request
            url_file = os.path.join(home, ".palmar", "run", "url")
            end = time.time() + 20
            url = None
            while time.time() < end:
                if os.path.exists(url_file):
                    url = open(url_file).read().strip()
                    break
                if proc.poll() is not None:
                    self.fail("the launcher exited: " + (proc.stderr.read() or b"").decode()[-300:])
                time.sleep(0.2)
            self.assertIsNotNone(url, "the launched daemon never wrote its url")
            req = urllib.request.Request(url, headers={"Origin": url.split("/?")[0]})
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            for pipe in (proc.stdout, proc.stderr):
                try:
                    pipe.close()
                except Exception:
                    pass

    def test_it_refuses_when_python_is_too_old(self):
        """A machine with only Python 3.8 should get a sentence, not a broken launcher."""
        fake = tempfile.mkdtemp(prefix="palmar-oldpy-")
        self.addCleanup(shutil.rmtree, fake, ignore_errors=True)
        py = os.path.join(fake, "python3")
        with open(py, "w") as fh:
            fh.write('#!/bin/sh\ncase "$1" in\n  --version) echo "Python 3.8.0";;\n  *) exit 1;;\nesac\n')
        os.chmod(py, 0o755)
        r = self.run_install(extra_env={"PATH": fake + ":/bin"})
        self.assertNotEqual(r.returncode, 0, "it accepted Python 3.8")
        self.assertIn("3.9", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.prefix, "bin", "palmar")),
                         "a launcher was written despite no usable Python")


if __name__ == "__main__":
    unittest.main()
