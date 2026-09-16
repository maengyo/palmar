"""install.sh — the one-line install. It puts a launcher where the shell finds it and nothing else.

Run against /bin/sh, not the interactive shell, because that is what `curl … | sh` uses and it is
stricter (no bashisms). Skipped where /bin/sh is missing, which on a dev machine it is not.
"""
from __future__ import annotations

import json
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

    def test_the_launcher_ignores_a_palmar_in_the_current_directory(self):
        """`python -m` puts the current directory first on sys.path, so `palmar` typed inside any other
        checkout ran the palmar of *that* checkout — an old clone kept for testing answered
        `unrecognized arguments: --stop` while the launcher pointed at the right tree all along
        (user, 2026-09-15). PYTHONSAFEPATH turned that off on Python 3.11+ only — macOS ships 3.9 —
        so the launcher runs the tree by script path now, which puts the tree first on every Python
        (review, 2026-09-15). Whatever python run_install's clean PATH finds, this has to hold."""
        self.run_install()
        shim = os.path.join(self.prefix, "bin", "palmar")
        cwd = tempfile.mkdtemp(prefix="palmar-shadow-")
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        os.makedirs(os.path.join(cwd, "palmar"))
        with open(os.path.join(cwd, "palmar", "__init__.py"), "w") as fh:
            fh.write("")
        with open(os.path.join(cwd, "palmar", "__main__.py"), "w") as fh:
            fh.write("print('SHADOW'); raise SystemExit(3)\n")
        r = subprocess.run([shim, "--version"], cwd=cwd, capture_output=True, text=True, timeout=60)
        self.assertNotIn("SHADOW", r.stdout + r.stderr, "the launcher ran the palmar in the cwd")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("palmar ", r.stdout, "the real palmar did not answer --version")
        # And a repository that shadows a standard module — json.py — must not reach the daemon either.
        with open(os.path.join(cwd, "json.py"), "w") as fh:
            fh.write("raise SystemExit('SHADOW-STDLIB')\n")
        r = subprocess.run([shim, "--version"], cwd=cwd, capture_output=True, text=True, timeout=60)
        self.assertNotIn("SHADOW", r.stdout + r.stderr, "a json.py in the cwd was imported over the standard library")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_launch_py_finds_the_tree_under_safe_path_on_this_python(self):
        """The launchers set PYTHONSAFEPATH=1 and run launch.py by path. On 3.11+ safe path drops the
        script's own directory from sys.path too, and `palmar` on a Windows with 3.13 died with
        ModuleNotFoundError (user, 2026-09-16); the Mac's 3.9 ignores the variable, which is why
        every other test here passed. Run with *this* interpreter, from somewhere else."""
        elsewhere = tempfile.mkdtemp(prefix="palmar-elsewhere-")
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        r = subprocess.run([sys.executable, os.path.join(REPO, "launch.py"), "--version"], cwd=elsewhere,
                           env=dict(os.environ, PYTHONSAFEPATH="1"), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("palmar ", r.stdout)

    def test_piped_in_it_does_not_take_the_cwd_for_a_checkout(self):
        """Under `curl … | sh` $0 is `sh`, and `dirname sh` is `.`: the one-liner run inside a
        repository holding a palmar/ package used to install that repository (review, 2026-09-15)."""
        cwd = tempfile.mkdtemp(prefix="palmar-cwdpkg-")
        self.addCleanup(shutil.rmtree, cwd, ignore_errors=True)
        os.makedirs(os.path.join(cwd, "palmar"))
        with open(os.path.join(cwd, "palmar", "__init__.py"), "w") as fh:
            fh.write("")
        env = dict(os.environ, PALMAR_PREFIX=self.prefix, PALMAR_TARBALL="/nonexistent/palmar.tar.gz")
        env["PATH"] = "/usr/bin:/bin"
        with open(INSTALL, "rb") as script:
            r = subprocess.run(["/bin/sh"], stdin=script, cwd=cwd, capture_output=True, text=True, timeout=60, env=env)
        self.assertNotIn("installing from the checkout", r.stdout + r.stderr, "the cwd was taken for a checkout")
        self.assertNotEqual(r.returncode, 0, "with no tarball to fetch it should have stopped")
        self.assertFalse(os.path.exists(os.path.join(self.prefix, "bin", "palmar")))

    def test_a_prefix_others_can_write_is_refused(self):
        os.makedirs(os.path.join(self.prefix, "bin"), exist_ok=True)
        os.chmod(os.path.join(self.prefix, "bin"), 0o777)
        r = self.run_install()
        self.assertNotEqual(r.returncode, 0, "it put a world-writable directory first on PATH")
        self.assertIn("writable by nobody else", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.prefix, "bin", "palmar")))

    def test_outside_a_checkout_it_fetches_the_tree_and_keeps_it(self):
        """`curl … | sh` runs with no checkout around it: the tree is downloaded and kept under the
        prefix, and the launcher runs that. An earlier draft extracted into mktemp and deleted it on
        exit, so the launcher pointed at nothing. Here the download is a local tarball of this repo."""
        import tarfile
        elsewhere = tempfile.mkdtemp(prefix="palmar-sh-out-")
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        tgz = os.path.join(elsewhere, "palmar-main.tar.gz")
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(os.path.join(REPO, "palmar"), arcname="palmar-main/palmar")
            tf.add(os.path.join(REPO, "launch.py"), arcname="palmar-main/launch.py")
        copy = os.path.join(elsewhere, "install.sh")
        shutil.copy(INSTALL, copy)
        home = tempfile.mkdtemp(prefix="palmar-sh-home-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        env = dict(os.environ, PALMAR_PREFIX=self.prefix, PALMAR_TARBALL=tgz, HOME=home, SHELL="/bin/zsh",
                   PATH="/usr/bin:/bin")
        r = subprocess.run(["/bin/sh", copy], capture_output=True, text=True, timeout=120, env=env, cwd=elsewhere)
        self.assertEqual(r.returncode, 0, r.stderr)
        tree = os.path.join(self.prefix, "share", "palmar")
        self.assertTrue(os.path.exists(os.path.join(tree, "palmar", "__init__.py")), "the tree was not kept")
        shim = os.path.join(self.prefix, "bin", "palmar")
        v = subprocess.run([shim, "--version"], capture_output=True, text=True, timeout=60, cwd="/tmp")
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertIn("palmar ", v.stdout, "the launcher does not run the kept tree")
        # PATH: one marked line in the shell's file, and not a second one on a second run.
        rc = os.path.join(home, ".zshrc")
        self.assertTrue(os.path.exists(rc), "no PATH line was written for zsh")
        with open(rc) as fh:
            first = fh.read()
        self.assertIn(self.prefix + "/bin", first)
        subprocess.run(["/bin/sh", copy], capture_output=True, text=True, timeout=120, env=env, cwd=elsewhere)
        with open(rc) as fh:
            second = fh.read()
        self.assertEqual(first, second, "a second install wrote the PATH line again")

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
        # **Exiting 0 is now success.** The daemon detaches, so the launcher prints the address and
        # comes back — that is the whole point of it (2026-09-14). What is asserted is that a daemon
        # is there afterwards, not that this process is.
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
                if proc.poll() is not None and proc.returncode != 0:
                    self.fail("the launcher exited: " + (proc.stderr.read() or b"").decode()[-300:])
                time.sleep(0.2)
            self.assertIsNotNone(url, "the launched daemon never wrote its url")
            req = urllib.request.Request(url, headers={"Origin": url.split("/?")[0]})
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            # The launcher may already be gone; the daemon it detached is what has to be stopped.
            subprocess.run([shim, "--stop"], env=dict(os.environ, HOME=home),
                           capture_output=True, timeout=30)
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
        # **The fake directory and nothing else.** /bin was on this PATH, which on a Mac holds no
        # python at all and on Linux is a symlink to /usr/bin — so install.sh's walk through
        # python3.13, python3.12 … found a real one and the test passed for the wrong reason (CI,
        # 2026-09-11). install.sh needs no external program before it gives up, so this is enough.
        r = self.run_install(extra_env={"PATH": fake})
        self.assertNotEqual(r.returncode, 0, "it accepted Python 3.8")
        self.assertIn("3.9", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.prefix, "bin", "palmar")),
                         "a launcher was written despite no usable Python")

    def shims_without_python(self, install_works=True):
        """A PATH head on which no python answers, `sudo` just runs its argument, and `apt-get
        install` puts a real python3 in place — the shape of a fresh Ubuntu, in a directory."""
        fake = tempfile.mkdtemp(prefix="palmar-nopy-")
        self.addCleanup(shutil.rmtree, fake, ignore_errors=True)
        for n in ("python3", "python3.13", "python3.12", "python3.11", "python3.10", "python3.9", "python"):
            with open(os.path.join(fake, n), "w") as fh:
                fh.write("#!/bin/sh\nexit 127\n")
            os.chmod(os.path.join(fake, n), 0o755)
        with open(os.path.join(fake, "sudo"), "w") as fh:
            fh.write('#!/bin/sh\nexec "$@"\n')
        with open(os.path.join(fake, "apt-get"), "w") as fh:
            fh.write('#!/bin/sh\necho "apt-get $*"\ncase "$1" in install) %s;; esac\nexit 0\n'
                     % ('ln -sf "%s" "%s/python3"' % (sys.executable, fake) if install_works else "exit 100"))
        for n in ("sudo", "apt-get"):
            os.chmod(os.path.join(fake, n), 0o755)
        return fake

    def test_without_a_python_it_installs_one_when_told_yes(self):
        """A machine with no python3 used to end at "install one from your package manager"; the
        person asked for it to just happen (2026-09-15). PALMAR_YES stands in for the terminal's yes."""
        fake = self.shims_without_python()
        r = self.run_install(extra_env={"PATH": fake + ":/usr/bin:/bin", "PALMAR_YES": "1"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("apt-get install -y python3", r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.prefix, "bin", "palmar")), "no launcher after the install")

    def test_without_a_python_and_without_a_yes_it_names_the_command(self):
        """No terminal to ask on and no PALMAR_YES: nothing is installed, and the exact command is in
        the sentence, so the person can run it or hand over the yes."""
        fake = self.shims_without_python()
        r = self.run_install(extra_env={"PATH": fake + ":/usr/bin:/bin"})
        self.assertNotEqual(r.returncode, 0, "it installed a package without a yes")
        self.assertIn("sudo apt-get update && sudo apt-get install -y python3", r.stdout + r.stderr)
        self.assertIn("PALMAR_YES=1", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.prefix, "bin", "palmar")))

    def test_when_the_package_manager_fails_it_says_so(self):
        fake = self.shims_without_python(install_works=False)
        r = self.run_install(extra_env={"PATH": fake + ":/usr/bin:/bin", "PALMAR_YES": "1"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not go through", r.stdout + r.stderr)

    def test_a_window_named_by_url_goes_beside_the_launcher(self):
        """`palmar` opens its own window before a browser (2026-09-15), so a built one is put next to
        the launcher. PALMAR_APP_URL with a local file stands in for the release asset here."""
        fake = tempfile.mkdtemp(prefix="palmar-appbin-")
        self.addCleanup(shutil.rmtree, fake, ignore_errors=True)
        src = os.path.join(fake, "palmar-app-macos-universal")
        with open(src, "w") as fh:
            fh.write("#!/bin/sh\necho window\n")
        r = self.run_install(extra_env={"PALMAR_APP_URL": src})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dest = os.path.join(self.prefix, "bin", "palmar-app")
        self.assertTrue(os.access(dest, os.X_OK), "no executable window beside the launcher")
        self.assertIn("installed the window", r.stdout)

    def test_a_build_in_the_checkout_is_linked_not_copied(self):
        """A rebuild in the checkout is then picked up, the same way the launcher runs the tree in place."""
        built = os.path.join(REPO, "app", "target", "release", "palmar-app")
        if not os.access(built, os.X_OK):
            self.skipTest("no palmar-app built in this checkout")
        r = self.run_install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        dest = os.path.join(self.prefix, "bin", "palmar-app")
        self.assertTrue(os.path.islink(dest), "the checkout's build was copied, not linked")
        self.assertEqual(os.path.realpath(dest), os.path.realpath(built))

    def test_no_window_is_not_a_failure(self):
        r = self.run_install(extra_env={"PALMAR_APP_URL": "/nonexistent/palmar-app"})
        self.assertEqual(r.returncode, 0, "a missing window took the install down")
        self.assertIn("browser", r.stdout + r.stderr)


def pwsh_path():
    """PowerShell, or None. Windows has it built in; elsewhere it is `pwsh` if someone installed it."""
    return shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipUnless(pwsh_path(), "no PowerShell on this machine")
class InstallPs1(unittest.TestCase):
    """install.ps1 — the Windows half of the one-line install.

    Run through PowerShell Core, which is what a Windows machine has (5.1 built in, 7 if installed)
    and what a Mac can have, so this is testable off Windows. What it cannot check here is the part
    that is Windows-only: the Microsoft Store's zero-length python.exe stub, and whether a corporate
    execution policy really lets `-ExecutionPolicy Bypass` through. Those need that machine.

    **The daemon does not run on Windows yet (#29)**, so none of this ends in a working palmar. What
    it does end in is a launcher that will work when the port lands, and a report of what the machine
    has — which is the thing blocking the port."""

    SCRIPT = os.path.join(REPO, "install.ps1")

    def run_ps(self, *args, **kw):
        cmd = [pwsh_path(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.SCRIPT] + list(args)
        # **Close stdin.** Anything that reaches a Read-Host with an inherited stdin waits for the test
        # runner's terminal, which is a hang rather than a failure.
        return subprocess.run(cmd, input=kw.get("input", ""), capture_output=True, text=True,
                              timeout=180, cwd=kw.get("cwd", REPO))

    def test_it_parses(self):
        """A syntax error in a file people run with the policy bypassed is a bad first impression."""
        # **The path goes in the string.** With -Command, a trailing argument is appended to the command
        # rather than bound to $args, so `$args[0]` was empty and pwsh sat waiting on stdin until the
        # timeout — a hang, not a failure, which is the worse kind.
        probe = (
            "$t=$null; $e=$null;"
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "  %s, [ref]$t, [ref]$e) | Out-Null;"
            "if ($e) { $e | ForEach-Object { $_.Message }; exit 1 }"
        ) % json.dumps(self.SCRIPT).replace("\\", "\\\\")
        r = subprocess.run([pwsh_path(), "-NonInteractive", "-NoProfile", "-Command", probe],
                           input="", capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_check_writes_nothing(self):
        """`-Check` is the diagnostic, and a diagnostic that edits the machine is a worse diagnostic
        (the same reason dev/wslg-probe.sh is separate from app/setup-linux.sh)."""
        prefix = tempfile.mkdtemp(prefix="palmar-ps-")
        self.addCleanup(shutil.rmtree, prefix, ignore_errors=True)
        shutil.rmtree(prefix)                      # it must not even create the directory
        r = self.run_ps("-Check", "-Prefix", prefix)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(prefix), "-Check created something")
        self.assertIn("Nothing was written", r.stdout)

    def test_the_report_says_what_it_found(self):
        """One screen a person can read back. That report is what this script is actually for today."""
        out = self.run_ps("-Check").stdout
        for want in ("PowerShell", "policy", "python", "checkout"):
            self.assertIn(want, out, "the report does not mention " + want)

    def test_it_says_the_daemon_does_not_run_here_yet(self):
        """**The thing a person must not be left to discover by running it.** Until #29 the launcher
        prints a refusal, and a script that installs it without saying so is setting up a surprise."""
        out = self.run_ps("-Check").stdout
        self.assertIn("runs natively on Windows", out, "the report still says the port is not there")
        self.assertNotIn("does not run natively", out)
        self.assertIn("windows.md", out, "it does not say where the rough edges are written")

    def test_it_installs_a_launcher_that_points_at_the_checkout(self):
        prefix = tempfile.mkdtemp(prefix="palmar-ps-")
        self.addCleanup(shutil.rmtree, prefix, ignore_errors=True)
        r = self.run_ps("-Yes", "-Prefix", prefix)
        self.assertEqual(r.returncode, 0, r.stderr)
        cmd = os.path.join(prefix, "bin", "palmar.cmd")
        self.assertTrue(os.path.exists(cmd), "no launcher was written")
        with open(cmd, encoding="ascii") as fh:
            body = fh.read()
        # A launcher, not a copy — so `git pull` updates palmar with no reinstall, as on POSIX.
        self.assertIn(REPO, body, "the launcher does not point at this checkout")
        self.assertIn("launch.py", body, "the launcher must run the tree by script path, not -m (review, 2026-09-15)")
        self.assertNotIn('set "PYTHONPATH', body, "an empty PYTHONPATH element is the cwd")
        self.assertIn("PYTHONSAFEPATH=1", body, "a palmar in the current directory would shadow the checkout")

    def test_it_asks_before_writing(self):
        """Without -Yes it must stop at the question rather than write. Answering nothing is a no."""
        prefix = tempfile.mkdtemp(prefix="palmar-ps-")
        self.addCleanup(shutil.rmtree, prefix, ignore_errors=True)
        shutil.rmtree(prefix)
        cmd = [pwsh_path(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self.SCRIPT,
               "-Prefix", prefix]
        r = subprocess.run(cmd, input="\n", capture_output=True, text=True, timeout=180, cwd=REPO)
        self.assertIn("Nothing was written", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(prefix, "bin", "palmar.cmd")),
                         "it wrote a launcher without being told to")

    def test_it_looks_past_PATH(self):
        """**The first real run found nothing** (user, 2026-09-14) on a machine that has Python.
        Windows' installer leaves "Add python.exe to PATH" unticked by default, so Get-Command is
        exactly the wrong place to stop. The registry is where the installer records itself and is
        the authority; the usual install folders and C:\\Windows\\py.exe come after it.

        What is checked here is that the search *names where it looked*, because a bare "none" is a
        dead end for the person reading it. The registry branch itself only exists on Windows."""
        env = dict(os.environ, PATH="/nonexistent")
        r = subprocess.run([pwsh_path(), "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", self.SCRIPT, "-Check"],
                           input="", capture_output=True, text=True, timeout=180, cwd=REPO, env=env)
        self.assertIn("looked in", r.stdout, "it gave up without saying where it looked")
        self.assertIn("-Python", r.stdout, "it does not offer the way out for a Python it missed")

    def test_it_is_pure_ascii(self):
        """**Windows PowerShell 5.1 reads a BOM-less file in the system code page, not UTF-8.**

        One em dash inside a string became two mojibake characters, the string ended early, and 5.1
        gave up at line 54 with "Unexpected token 'line'" -- taking the whole script with it. The
        same file parsed and ran clean under PowerShell 7 on a Mac, which is why a Mac could not see
        it (2026-09-14). A BOM would fix it too; staying ASCII means never having to remember one.

        Cheap and total: every byte, not a spot check."""
        with open(self.SCRIPT, "rb") as fh:
            raw = fh.read()
        bad = sorted({b for b in raw if b > 0x7F})
        self.assertEqual(bad, [], "non-ASCII bytes in install.ps1: %r -- PowerShell 5.1 will mangle them"
                                  % [hex(b) for b in bad[:8]])
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "a BOM crept in; pure ASCII needs none")

    def test_one_bad_step_does_not_take_the_report_down(self):
        """**`$ErrorActionPreference = 'Stop'` was the bug.** On Windows PowerShell 5.1 a *native*
        command writing one line to stderr becomes a terminating NativeCommandError under it, so
        `wsl.exe -l -q` on a machine with no distribution killed the whole report — on a diagnostic,
        which is the one kind of script that must never stop at its first surprise.

        Checked at the source rather than by simulating a failure, because the failure needs Windows:
        the preference must not be Stop, and the calls that can throw must sit inside Step."""
        with open(self.SCRIPT, encoding="utf-8") as fh:
            body = fh.read()
        self.assertNotIn("$ErrorActionPreference = 'Stop'", body,
                         "a diagnostic must not stop at its first surprise")
        self.assertIn("function Step", body, "no per-step guard")
        for risky in ("asking wsl what it has", "looking for Python", "reading the execution policy"):
            self.assertIn("Step '%s'" % risky, body, "%s is not guarded" % risky)
        # And a step that fails has to name itself and its line, because this report gets read aloud
        # off a machine nothing can be copied from.
        self.assertIn("ScriptLineNumber", body, "a failed step does not say where it failed")

    def test_a_python_can_be_named_outright(self):
        """The escape hatch, for the machine that knows better than the search."""
        r = self.run_ps("-Check", "-Python", sys.executable)
        self.assertIn("via -Python", r.stdout)
        self.assertIn(os.path.basename(sys.executable), r.stdout)

    def test_outside_a_checkout_it_fetches_the_tree_and_keeps_it(self):
        """The one-liner: `irm … | iex` has no checkout around it, so the tree is downloaded into the
        prefix and the launcher runs that (2026-09-15). Here the download is a local zip of this repo,
        so the test needs no network and no public repository."""
        import zipfile
        elsewhere = tempfile.mkdtemp(prefix="palmar-ps-out-")
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        z = os.path.join(elsewhere, "palmar-main.zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(os.path.join(REPO, "palmar")):
                for f in files:
                    full = os.path.join(root, f)
                    zf.write(full, "palmar-main/" + os.path.relpath(full, REPO))
        copy = os.path.join(elsewhere, "install.ps1")
        shutil.copy(self.SCRIPT, copy)
        prefix = os.path.join(elsewhere, "prefix")
        env = dict(os.environ, PALMAR_ZIP=z)
        r = subprocess.run([pwsh_path(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", copy, "-Yes",
                            "-Prefix", prefix, "-Python", sys.executable],
                           capture_output=True, text=True, timeout=180, cwd=elsewhere, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(os.path.join(prefix, "src", "palmar", "__init__.py")), "the tree was not kept under the prefix")
        with open(os.path.join(prefix, "bin", "palmar.cmd"), encoding="ascii") as fh:
            body = fh.read()
        self.assertIn(os.path.join(prefix, "src"), body, "the launcher does not point at the kept tree")


if __name__ == "__main__":
    unittest.main()
