"""The parts that can be asked directly, with no daemon and no browser.

These run in well under a second, so there is no reason not to run them. Everything here is a
regression somebody already paid for once; the point of writing it down is that nobody pays twice.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from palmar import daemon as D


class AltScreenScan(unittest.TestCase):
    """`_absorb` finds the alt-screen markers and keeps the ring right.

    It used to search for **both** markers on every pass of the loop, so when one of them was absent
    from the rest of the buffer each find walked to the end — 256 KB of unpaired ESC[?1049h took
    **5.8 seconds** on the event loop, and for those seconds no pane moved a byte (2026-09-09)."""

    def fake(self):
        class F:
            def __init__(self):
                self.ring = D.Ring(D.RING)
                self.alt = False
                self.carry = b""
                self.produced = 0
            _absorb = D.Session._absorb
        return F()

    def test_unpaired_markers_are_not_quadratic(self):
        f = self.fake()
        t = time.perf_counter()
        f._absorb(D.ALT_ON * 32768)
        ms = (time.perf_counter() - t) * 1000
        self.assertLess(ms, 800, "256 KB of unpaired ESC[?1049h took %.0f ms — the scan is "
                                 "quadratic again (it was 5,830 ms before the fix)" % ms)
        self.assertTrue(f.alt)

    def test_ordinary_output_is_fast(self):
        f = self.fake()
        t = time.perf_counter()
        f._absorb(b"hello world line\r\n" * 14563)
        self.assertLess((time.perf_counter() - t) * 1000, 50)
        self.assertFalse(f.alt)

    def test_split_across_chunks_is_the_same_as_whole(self):
        """The marker can land across a chunk boundary — `carry` exists for that. Whether it did
        must make no difference to the ring, the offset or the alt state."""
        import random
        random.seed(7)
        pieces = [D.ALT_ON, D.ALT_OFF, b"abc", b"\x1b[?1049", b"h", b"l", b"\r\n"]

        def run(chunks):
            f = self.fake()
            for c in chunks:
                f._absorb(c)
            return f.alt, f.produced, f.carry, f.ring.since(0)

        for _ in range(120):
            blob = b"".join(random.choice(pieces) for _ in range(random.randint(1, 14)))
            cuts = sorted(random.sample(range(len(blob) + 1), min(4, len(blob) + 1)))
            chunks = [blob[a:b] for a, b in zip([0] + cuts, cuts + [len(blob)]) if blob[a:b]]
            self.assertEqual(run(chunks), run([blob]), "chunking changed the result for %r" % blob)


class TitleSpin(unittest.TestCase):
    """A spinner is sustained change, not two changes.

    Shells that retitle per command (oh-my-zsh, p10k, plain WSL bash) change the title two or three
    times **inside nine milliseconds**, and counting alone read that as a spinner: an untouched pane
    sat at `done` — the light that means "wants you" — for as long as you left it (2026-09-09)."""

    def busy_with(self, gaps):
        """Feed title-change timestamps `gaps` seconds apart and ask whether that reads as busy."""
        class F:
            title_hits = []
        f = F()
        now = time.monotonic()
        f.title_hits = [now - g for g in gaps]
        return D.Session._title_busy(f)

    def test_a_burst_inside_one_command_is_not_a_spinner(self):
        # preexec then precmd: three changes, nine milliseconds apart
        self.assertFalse(self.busy_with([0.009, 0.005, 0.0]))

    def test_a_slow_spinner_counts(self):
        # Claude Code, about once a second
        self.assertTrue(self.busy_with([2.0, 1.0, 0.0]))

    def test_a_fast_spinner_counts(self):
        # codex, about twelve times a second, filling the window
        # **Oldest first.** _title_busy measures the span as hits[-1] - hits[0], so a list built
        # newest-first comes out negative and reads as "not spinning" — which is what this test
        # said the first time it ran.
        self.assertTrue(self.busy_with(sorted((i * 0.08 for i in range(30)), reverse=True)))

    def test_one_change_is_never_a_spinner(self):
        self.assertFalse(self.busy_with([0.0]))


class HasContent(unittest.TestCase):
    """Cursor housekeeping is not output. One TUI printed the same 32 bytes ten times a second
    while sitting still, and every one of them looked like work."""

    def test_a_cursor_move_is_not_content(self):
        self.assertFalse(D.Session._has_content(b"\x1b[2;5H"))

    def test_text_is_content(self):
        self.assertTrue(D.Session._has_content(b"hello"))

    def test_a_newline_is_content(self):
        self.assertTrue(D.Session._has_content(b"\r\n"))

    def test_anything_large_is_content_without_looking(self):
        self.assertTrue(D.Session._has_content(b"\x1b[2;5H" * 4000))


class ChildAndCwd(unittest.TestCase):
    """Two questions the daemon asks the kernel about a pane. Both may answer None on a platform
    nobody has taught them — that is the honest answer and must not be mistaken for a real one."""

    def test_there_is_no_has_child_yet(self):
        """A direct "does this shell have a child" test was written and **reverted** (#27,
        2026-09-09): it was measured at 2 µs and changed no observable behaviour, because the layer
        above it had already stopped misfiring. It belongs with the Windows port (#29 step 5), where
        there is no tcgetpgrp at all and the question has to be answered somehow. This test is here
        so that reappearing is a deliberate act with a measurement, not a quiet return."""
        self.assertFalse(hasattr(D, "has_child"),
                         "has_child is back — give it a test that shows what it changes")

    def test_cwd_of_reads_where_a_process_actually_is(self):
        """Not where it started — where it moved to. That is the whole reason it exists."""
        got = D.cwd_of(os.getpid())
        if got is None:
            self.skipTest("cwd_of is not implemented on %s" % sys.platform)
        self.assertEqual(os.path.realpath(got), os.path.realpath(os.getcwd()))
        d = os.path.realpath(tempfile.mkdtemp(prefix="palmar-cwd-"))
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(4)"], cwd=d)
        try:
            time.sleep(0.4)
            self.assertEqual(os.path.realpath(D.cwd_of(p.pid)), d)
        finally:
            p.kill()
            p.wait()

    def test_a_dead_pid_is_none_not_a_guess(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        time.sleep(0.2)
        self.assertIsNone(D.cwd_of(p.pid))


class RestoreFile(unittest.TestCase):
    """A recovery file that cannot be trusted is worth less than no recovery file."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="palmar-restore-")
        self._real = D.RESTORE_FILE
        D.RESTORE_FILE = __import__("pathlib").Path(self.dir) / "restore.json"

    def tearDown(self):
        D.RESTORE_FILE = self._real

    def write(self, text):
        D.RESTORE_FILE.write_text(text)

    def test_missing_is_none(self):
        self.assertIsNone(D.read_restore())

    def test_not_json_is_none(self):
        self.write("not json at all {{{")
        self.assertIsNone(D.read_restore())

    def test_a_version_we_do_not_know_is_none(self):
        self.write('{"v": 99, "canvases": [], "sessions": []}')
        self.assertIsNone(D.read_restore())

    def test_wrong_shapes_are_none(self):
        self.write('{"v": 1, "canvases": "nope", "sessions": []}')
        self.assertIsNone(D.read_restore())

    def test_a_good_one_comes_back(self):
        self.write('{"v": 1, "canvases": [{"id": "a", "name": "infra"}], '
                   '"sessions": [{"name": "x", "cwd": "/tmp", "canvas": "a"}]}')
        d = D.read_restore()
        self.assertEqual(d["canvases"][0]["name"], "infra")
        self.assertEqual(d["sessions"][0]["cwd"], "/tmp")


class Names(unittest.TestCase):
    """protocol.md "이름 규칙": 1–64 characters, trimmed, no control characters."""

    def test_trimmed(self):
        self.assertEqual(D.clean_name("  hi  "), "hi")

    def test_blank_is_no_name(self):
        self.assertIsNone(D.clean_name("   "))
        self.assertIsNone(D.clean_name(None))

    def test_too_long_is_refused(self):
        with self.assertRaises(ValueError):
            D.clean_name("x" * 65)

    def test_control_characters_are_refused(self):
        with self.assertRaises(ValueError):
            D.clean_name("a\x07b")


class Startup(unittest.TestCase):
    """The guards at the top of the module — both of them used to sit *below* `import fcntl`, where
    neither could ever run on a platform that could not import it."""

    def test_windows_gets_a_sentence_not_a_stack(self):
        code = ("import sys; sys.platform = 'win32'; sys.path.insert(0, %r); import palmar.daemon"
                % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        out = r.stdout + r.stderr
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", out)
        self.assertIn("does not run natively on Windows", out)
        self.assertIn("issues/29", out)

    def test_the_package_itself_imports_anywhere(self):
        """`import palmar` must work even where the daemon cannot — it is what lets dev-stub run
        on Windows today."""
        code = ("import sys; sys.platform = 'win32'; sys.path.insert(0, %r); "
                "import palmar; print(palmar.PROTOCOL)"
                % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), str(D.PROTOCOL))


class TheShimOnWindows(unittest.TestCase):
    """What palmar puts in front of `claude` so the hooks come back — and why none of it reached
    Windows (#30). The lights there do not move, and this is the half of the reason that is not a
    missing feature but two plain mistakes."""

    def test_the_path_separator_is_the_platforms(self):
        """It was a colon, written in. On Windows that made the first entry
        `C:\\Users\\…\\.palmar\\bin;C:\\Windows\\system32` — one path, and not one that exists — so
        `.palmar\\bin` was **never on PATH** and nothing in it could be found."""
        got = D.front_of_path(r"C:\Users\x\.palmar\bin", r"C:\Windows\system32;C:\Windows", sep=";")
        self.assertEqual(got.split(";")[0], r"C:\Users\x\.palmar\bin", "the shim is not first: " + got)
        self.assertEqual(got.split(";")[1:], [r"C:\Windows\system32", r"C:\Windows"],
                         "it did not leave the rest of PATH alone: " + got)

    def test_posix_is_left_exactly_as_it_was(self):
        """The fix is `os.pathsep`, which on POSIX is the colon that was written in — so the one
        platform where this all works today must come out character for character the same."""
        self.assertEqual(D.front_of_path("/h/.palmar/bin", "/usr/local/bin:/usr/bin"),
                         "/h/.palmar/bin:/usr/local/bin:/usr/bin")
        self.assertEqual(D.front_of_path("/h/.palmar/bin", None),
                         "/h/.palmar/bin:" + D.DEFAULT_PATH[0])

    def test_the_sh_shim_is_not_written_where_it_cannot_run(self):
        """`~/.palmar/bin/claude` is a `#!/bin/sh` script under an extension-less name. Windows has no
        shebang and PATHEXT does not list "", so it is a file that can never be run — and once the
        separator above is fixed it would be sitting first on PATH."""
        self.assertTrue(D.SHIM.startswith("#!/bin/sh"), "the shim stopped being a shell script")
        src = pathlib.Path(D.__file__).read_text(encoding="utf-8")
        self.assertIn('if POSIX_PERMS:\n        write_private(BIN_DIR / "claude"', src,
                      "the sh shim is written on every platform again")

    def test_the_preamble_cannot_find_itself(self):
        """The POSIX shim keeps out of its own way by taking its directory out of PATH as a fixed
        string, which went wrong once — the `.` in `.palmar` read as a regex (#12) — and exec'd itself
        forever. The PowerShell side asks for a **type** instead, which cannot go wrong that way."""
        body = D.PWSH_PREAMBLE
        self.assertIn("-CommandType Application", body, "the function would find itself")
        self.assertIn("--settings", body, "it does not attach the hooks, which is its whole job")
        self.assertIn("@args", body, "it drops the arguments the person typed")
        self.assertIn("$env:PALMAR_PANE", body, "it does not know which pane it is in")
        self.assertNotIn("#!/bin/sh", body)

    def test_the_preamble_knows_where_the_pane_files_are(self):
        """The run directory is written in when the file is made, rather than rebuilt from $HOME in
        PowerShell — one place decides where palmar keeps things, and it is the daemon."""
        rendered = D.PWSH_PREAMBLE.replace("{run}", "/tmp/h/.palmar/run")
        self.assertIn("Join-Path '/tmp/h/.palmar/run'", rendered)
        self.assertNotIn("{run}", rendered)

    def test_the_prompt_says_where_the_pane_is(self):
        """The two halves have to agree: the preamble writes a marker and the daemon reads one. If
        anybody edits either spelling alone, this is what notices."""
        body = D.PWSH_PREAMBLE
        self.assertIn("palmar:cwd:", body, "the prompt does not say where the pane is")
        self.assertIn(D.TITLE_CWD, body, "the preamble and the daemon disagree on the marker")
        self.assertIn("$PWD.ProviderPath", body,
                      "a PSDrive path would be sent where a folder is meant")
        self.assertIn("$global:PalmarUserPrompt", body, "it replaced the user's prompt instead of wrapping it")
        # The user's prompt is called first, so if it sets a title of its own ours is the one that lands.
        self.assertLess(body.index("& $global:PalmarUserPrompt"), body.index("palmar:cwd:"),
                        "palmar's title is written before the user's prompt has had its turn")
        # Beside the prompt, not inside it: a zero-width sequence in the returned string is the
        # mistake that breaks line wrapping in zsh when %{...%} is forgotten.
        self.assertIn("[Console]::Write(", body, "the escape is going through the prompt string")

    def test_the_profile_runs_and_the_policy_does_not_refuse(self):
        """Three things the arguments have to get right at once. **The user's profile still runs** —
        no `-NoProfile` — and because PowerShell loads it before `-Command`, palmar's part comes
        after it, which is the whole rc-wrapping dance POSIX needs. **The window stays** (`-NoExit`).
        And it is not `-File`: running a `.ps1` goes through the execution policy, `Restricted` by
        default on a client, which would refuse it — text made into a script block is not a script
        file, so the policy has nothing to say."""
        argv = D.pwsh_argv(["C:/pwsh.exe", "-NoLogo"], "C:/Users/x/.palmar/pwsh/preamble.ps1")
        self.assertEqual(argv[:2], ["C:/pwsh.exe", "-NoLogo"], "it lost the shell's own arguments")
        self.assertIn("-NoExit", argv, "the pane would close as soon as the preamble finished")
        self.assertIn("-Command", argv)
        self.assertNotIn("-File", argv, "a .ps1 file is what the execution policy refuses")
        self.assertNotIn("-NoProfile", argv, "it would be skipping the user's own profile")
        self.assertIn("scriptblock]::Create", argv[-1], "it is dot-sourcing the path after all")
        self.assertTrue(argv[-1].startswith(". "), "not dot-sourced — the function would not survive")

    def test_an_apostrophe_in_the_path_does_not_end_the_string(self):
        """A person's name is enough to put one there. In a single-quoted PowerShell string it is
        escaped by doubling, and both places that write a path into one have to do it."""
        argv = D.pwsh_argv(["pwsh"], "C:/Users/O'Brien/.palmar/pwsh/preamble.ps1")
        self.assertIn("C:/Users/O''Brien/", argv[-1], "the quote was left to close the string: " + argv[-1])
        self.assertEqual(argv[-1].count("'") % 2, 0, "unbalanced quotes: " + argv[-1])


class TheFolderAPaneIsIn(unittest.TestCase):
    """`to_json` handed out the folder a pane was **created** with, and nothing ever changed it — so
    the left rail named the opening folder for the life of the session. On every platform: measured
    2026-09-17 on a Mac, a `cd` into a subfolder and eleven seconds later the rail still said the
    folder it started in. The live value was being read every ten seconds for the restore snapshot
    and thrown away."""

    def pane(self, cwd="/start"):
        class F:
            pass
        f = F()
        f.cwd = cwd
        f.cwd_told = False
        f.pid = os.getpid()
        f.title = ""
        f.title_hits = []
        f.osc_carry = b""
        # _scan_title reaches for it through self, and this stand-in is not a Session.
        f._title_cwd = lambda t: D.Session._title_cwd(f, t)
        f._title_tick = lambda: None
        f._arm_settle = lambda: None
        return f

    def test_the_marker_moves_the_pane(self):
        """The Windows route. PowerShell's `Set-Location` never touches the process working
        directory, so `cwd_of` answers with where the shell started however correctly it reads — only
        a shell that says so can be followed, and palmar's preamble makes the prompt say so."""
        f = self.pane()
        with mock.patch.object(D.registry, "changed"):
            took = D.Session._title_cwd(f, D.TITLE_CWD + tempfile.gettempdir())
        self.assertTrue(took, "the marker was not recognised as palmar's own")
        self.assertEqual(f.cwd, tempfile.gettempdir())

    def test_a_folder_that_is_not_there_is_not_believed(self):
        f = self.pane()
        with mock.patch.object(D.registry, "changed"):
            took = D.Session._title_cwd(f, D.TITLE_CWD + "/no/such/place/at/all")
        self.assertTrue(took, "it is still palmar's marker, and still not a title spin")
        self.assertEqual(f.cwd, "/start", "it believed a folder that does not exist")

    def test_once_a_pane_has_said_where_it_is_we_stop_reading_it(self):
        """**The two were fighting.** The prompt writes the right folder into the title, and ten
        seconds later the tick read the process working directory and put the folder the pane
        *opened* in back — because PowerShell's `Set-Location` never touches it. From the outside it
        looked like running an agent sent the rail home and kept it there (user, 2026-09-18). It was
        the tick, not the agent. The one who knows wins; the one who reads from outside stops."""
        f = self.pane()
        with mock.patch.object(D.registry, "changed"):
            D.Session._title_cwd(f, D.TITLE_CWD + tempfile.gettempdir())
            self.assertTrue(f.cwd_told)
            D.Session.sample_cwd(f)           # the ten-second tick, reading this very process
        self.assertEqual(f.cwd, tempfile.gettempdir(),
                         "the tick put the folder the pane opened in back")

    def test_a_pane_that_has_never_said_is_still_read(self):
        """The other three quarters of the world: a shell that says nothing, where reading the
        process is the only way to follow a cd — and it works there."""
        f = self.pane()
        with mock.patch.object(D.registry, "changed"):
            D.Session.sample_cwd(f)
        self.assertEqual(os.path.realpath(f.cwd), os.path.realpath(os.getcwd()),
                         "a silent pane stopped being read")

    def test_an_ordinary_title_is_left_alone(self):
        f = self.pane()
        with mock.patch.object(D.registry, "changed"):
            self.assertFalse(D.Session._title_cwd(f, "~/work — claude"))
        self.assertEqual(f.cwd, "/start")

    def test_the_marker_is_not_a_heartbeat(self):
        """It changes when the folder does, once, which is nothing like an agent working. Counting it
        as a spin would put "working" in the lights for a `cd`."""
        f = self.pane()
        with mock.patch.object(D.registry, "changed"):
            D.Session._scan_title(f, b"\x1b]2;" + (D.TITLE_CWD + tempfile.gettempdir()).encode() + b"\x07")
        self.assertEqual(f.title_hits, [], "palmar's own marker was counted as the agent working")
        self.assertEqual(f.cwd, tempfile.gettempdir())


class WhenTheShellSaysItOutright(unittest.TestCase):
    """OSC 133. Every other layer under the hooks is inference: the title spinning is a heartbeat we
    hope means work, and the output rule — printing on and on is working, printing then stopping is
    done — calls a long quiet think finished and a chatty log busy. A shell that emits these marks is
    telling us, and there is nothing left to guess."""

    def pane(self):
        class F:
            pass
        f = F()
        f.derived, f.status = "idle", "unknown"
        f.shell_marks, f.cmd_start = False, None
        f.last_out = 0.0
        f.title, f.title_hits, f.title_spun, f.osc_carry = "", [], False, b""
        f.cwd, f.pid = "/start", os.getpid()
        f._title_cwd = lambda t: D.Session._title_cwd(f, t)
        f._shell_mark = lambda m: D.Session._shell_mark(f, m)
        f._title_tick = lambda: D.Session._title_tick(f)
        f._title_busy = lambda: D.Session._title_busy(f)
        f._arm_settle = lambda: None
        return f

    def mark(self, f, *marks):
        with mock.patch.object(D.registry, "changed"):
            for m in marks:
                D.Session._shell_mark(f, m)
        return f.derived

    def test_a_command_starting_is_working(self):
        self.assertEqual(self.mark(self.pane(), "C"), "working")

    def test_a_command_that_said_something_is_done(self):
        f = self.pane()
        with mock.patch.object(D.registry, "changed"):
            D.Session._shell_mark(f, "C")
            f.last_out = time.monotonic()      # it printed
            D.Session._shell_mark(f, "D")
        self.assertEqual(f.derived, "done")

    def test_an_empty_line_and_an_enter_is_not_finished_work(self):
        """`C` then `D` with nothing printed between is a person pressing Enter on an empty line.
        Lighting "finished" for that is a light nobody asked for."""
        self.assertEqual(self.mark(self.pane(), "C", "D"), "idle")

    def test_the_prompt_appearing_does_not_erase_what_just_finished(self):
        """**The bug the user saw first.** The preamble writes `D` and `A` in one go, and `A` used to
        set idle — so "finished" was erased in the same breath it was set and the done light could
        never be seen at all (2026-09-18). A prompt appearing is not a state of its own; the command
        ending is, and `D` is that."""
        f = self.pane()
        with mock.patch.object(D.registry, "changed"):
            D.Session._shell_mark(f, "C")
            f.last_out = time.monotonic()
            D.Session._shell_mark(f, "D")
            self.assertEqual(f.derived, "done")
            D.Session._shell_mark(f, "A")
            D.Session._shell_mark(f, "B")
        self.assertEqual(f.derived, "done", "the prompt wrote over the light the command had earned")

    def test_a_command_that_said_nothing_ends_at_idle(self):
        f = self.pane()
        f.derived = "working"
        self.assertEqual(self.mark(f, "D"), "idle")

    def test_a_prompt_redrawn_mid_command_means_nothing(self):
        """Ctrl-L, a window resize, a progress line that repaints — the prompt can be written again
        while a command is still running, and that is not the command ending."""
        f = self.pane()
        self.assertEqual(self.mark(f, "C", "A"), "working")
        self.assertIsNotNone(f.cmd_start, "the mid-command mark forgot a command was running")

    def test_at_a_prompt_the_shell_has_the_last_word(self):
        """Nothing may paint green over a `D`. The shell has said there is no command running, and a
        title still twitching afterwards is a leftover, not work."""
        f = self.pane()
        self.mark(f, "C", "D")
        f.title_hits = [time.monotonic() - g for g in (2.0, 1.0, 0.0)]   # a spinner, on any other day
        f.title_spun = True
        with mock.patch.object(D.registry, "changed"):
            D.Session._title_tick(f)
        self.assertEqual(f.derived, "idle", "the title layer painted over what the shell said")

    def test_while_a_command_runs_the_layers_that_watch_the_agent_still_speak(self):
        """**The regression that mattered.** Standing them down for good the moment a shell spoke was
        wrong in exactly the case palmar is for: the shell knows *a command is running*, and an agent
        is one long command. Running aelix, the light went green and stayed green for the whole
        session, because the one layer that watches **the agent** rather than the shell had been
        switched off (user, 2026-09-18). Between C and D they are all there is."""
        f = self.pane()
        self.mark(f, "C")
        f.title_spun = True
        f.title_hits = [time.monotonic() - g for g in (2.0, 1.0, 0.0)]   # the agent, spinning
        with mock.patch.object(D.registry, "changed"):
            D.Session._title_tick(f)
        self.assertEqual(f.derived, "working")
        f.title_hits = []                                                # and it stopped — it wants you
        with mock.patch.object(D.registry, "changed"):
            D.Session._title_tick(f)
        self.assertEqual(f.derived, "done",
                         "the agent stopped and nothing could say so while its command was running")

    def test_hooks_still_win(self):
        """This only ever writes `derived`. A pane the hooks speak for reads its status from them."""
        f = self.pane()
        f.status = "waiting"
        self.mark(f, "C")
        self.assertEqual(D.Session.eff_status(f), "waiting")

    def test_the_marks_are_found_in_the_stream_and_are_not_titles(self):
        f = self.pane()
        with mock.patch.object(D.registry, "changed"):
            D.Session._scan_title(f, b"hello\x1b]133;C\x07world\x1b]0;vim\x07")
        self.assertEqual(f.derived, "working", "the mark was not seen in the stream")
        self.assertEqual(f.title, "vim", "the title beside it was lost")
        self.assertEqual(len(f.title_hits), 1, "the mark was counted as a title change")

    def test_a_mark_split_across_two_chunks_is_still_one_mark(self):
        f = self.pane()
        with mock.patch.object(D.registry, "changed"):
            D.Session._scan_title(f, b"\x1b]133")
            self.assertEqual(f.derived, "idle", "half a sequence was acted on")
            D.Session._scan_title(f, b";C\x07")
        self.assertEqual(f.derived, "working", "the carry lost the mark at the chunk boundary")


class WhereADroppedFileIsLookedFor(unittest.TestCase):
    """A page is never told where a dropped file is, so palmar goes and finds it by the name, size and
    time that *did* come over. Where it looks is the whole question: too narrow and the same file
    opens from the rail and not from a drop, too wide and a search never ends."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="palmar-tops-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, *parts, text="x\n"):
        p = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def test_a_mounted_volume_is_a_place_to_look(self):
        """A home on C: and the file on D: is the ordinary shape of a Windows machine — and the rail
        already walks there and opens it, so a drop that could not was the same inconsistency one
        level out (user, 2026-09-18)."""
        os.makedirs(os.path.join(self.tmp, "Elements", "docs"))
        os.makedirs(os.path.join(self.tmp, ".hidden"))
        with open(os.path.join(self.tmp, "a file"), "w") as fh:
            fh.write("not a volume")
        got = D.volume_tops(platform="darwin", bases=[self.tmp])
        self.assertEqual(got, [os.path.join(self.tmp, "Elements")],
                         "volumes came out as %r" % (got,))

    def test_a_network_drive_is_not_swept(self):
        """The kind is asked of the system, not guessed from the letter: a mapped network drive looks
        exactly like a local one, and sweeping one is how a search stops being a search. Nothing to
        ask means nothing to sweep, which is why a machine that is not Windows gets an empty list from
        the Windows branch rather than a guess."""
        self.assertEqual(D.volume_tops(platform="win32"), [],
                         "it invented drive letters on a machine that has none")

    def test_it_finds_the_file_on_the_other_drive(self):
        import asyncio
        want = self.make("Elements", "papers", "on the other drive.pdf")
        with mock.patch.object(D, "roots", lambda: [pathlib.Path(self.tmp, "home")]), \
             mock.patch.object(D, "volume_tops", lambda: [os.path.join(self.tmp, "Elements")]):
            os.makedirs(os.path.join(self.tmp, "home"), exist_ok=True)
            got = asyncio.run(D.find_files("on the other drive.pdf"))
        self.assertEqual([h["path"] for h in got], [want], "it did not reach the other drive: %r" % (got,))

    def test_a_wide_home_does_not_starve_the_drive_the_file_is_on(self):
        """Each top gets its own budget of entries. With one shared between them, a home big enough to
        spend it is a home that hides every other drive."""
        for i in range(40):
            self.make("home", "deep%d" % i, "filler.txt")
        want = self.make("Elements", "the one.pdf")
        with mock.patch.object(D, "roots", lambda: [pathlib.Path(self.tmp, "home")]), \
             mock.patch.object(D, "volume_tops", lambda: [os.path.join(self.tmp, "Elements")]), \
             mock.patch.object(D, "FIND_FILE_NODES", 8):     # a budget the home alone would eat
            import asyncio
            got = asyncio.run(D.find_files("the one.pdf"))
        self.assertEqual([h["path"] for h in got], [want],
                         "the home ate the whole search: %r" % (got,))


class DrawingTheSameThingAgain(unittest.TestCase):
    """An agent with no hooks and no window title holds its approval menu open by repainting it, and
    to the only question the output layer can ask — did bytes come out? — that is identical to
    working. So the light stayed green for as long as it waited (user, aelix, 2026-09-18).

    The rule chosen (2026-09-19) is the same shape as the one already there for cursor-management
    bytes: **it never looks at what was written, only at whether it is the same as last time.**"""

    def pane(self, alt=True):
        class F:
            pass
        f = F()
        f.alt = alt
        f.last_paint = None
        f.last_out, f.out_start, f.out_break = 0.0, 0.0, False
        f.title_spun = False
        f.ticks = 0
        f._has_content = lambda d: D.Session._has_content(d)
        f._out_tick = lambda: setattr(f, "ticks", f.ticks + 1)
        f._arm_out = lambda: None
        return f

    def feed(self, f, *chunks):
        for c in chunks:
            D.Session._out_scan(f, c)
        return f.last_out

    def test_the_same_frame_twice_is_not_work(self):
        f = self.pane()
        menu = b"\x1b[2J\x1b[H  Allow this edit?  [y] yes  [n] no"
        self.feed(f, menu)
        first = f.last_out
        self.assertGreater(first, 0, "the first paint did not count as output at all")
        time.sleep(0.02)
        self.feed(f, menu, menu, menu)
        self.assertEqual(f.last_out, first,
                         "repainting the same menu kept the pane looking busy")

    def test_a_spinner_is_work_because_its_frames_differ(self):
        """The whole point of a spinner is that it changes. This must not quiet one."""
        f = self.pane()
        for frame in "⠋⠙⠹⠸⠼":
            time.sleep(0.01)
            was = f.last_out
            self.feed(f, ("\x1b[H thinking " + frame).encode())
            self.assertGreater(f.last_out, was, "frame %s was taken for a repeat" % frame)

    def test_a_shell_loop_printing_one_line_is_still_work(self):
        """`while true; do echo x; done` prints the same bytes for ever and **is** working. A shell
        is not in the alt screen and a full-screen agent is, which is the whole of the distinction."""
        f = self.pane(alt=False)
        self.feed(f, b"x\n")
        first = f.last_out
        time.sleep(0.02)
        self.feed(f, b"x\n", b"x\n")
        self.assertGreater(f.last_out, first, "a shell loop was quieted")

    def test_a_repaint_too_big_to_be_one_is_left_alone(self):
        """A build log is not what this is for, and stripping escapes out of every large chunk to
        compare it is a cost paid on exactly the panes that produce the most."""
        f = self.pane()
        big = b"\x1b[H" + b"a line of build output\n" * 400
        self.assertGreater(len(big), D.REPEAT_MAX)
        self.feed(f, big)
        first = f.last_out
        time.sleep(0.02)
        self.feed(f, big)
        self.assertGreater(f.last_out, first, "a large repeated chunk was quieted")

    def test_leaving_the_alt_screen_forgets_the_last_frame(self):
        """Otherwise a frame from before could silence the first identical line after it."""
        f = self.pane()
        self.feed(f, b"\x1b[H menu")
        f.alt = False
        self.feed(f, b"plain")
        self.assertIsNone(f.last_paint)

    def test_cursor_management_alone_still_counts_for_nothing(self):
        """The rule this one extends, unchanged: output that leaves nothing on screen is not work."""
        f = self.pane()
        self.feed(f, b"\x1b[?25l\x1b[?7l\x1b[?7h\x1b[0m\x1b[?12l\x1b[?25h")
        self.assertEqual(f.last_out, 0.0, "a cursor tick was counted as output")


URL = "http://127.0.0.1:8801/?k=abc123"


def having(*names):
    """A stand-in for shutil.which: these programs are on this imaginary machine and nothing else."""
    return lambda n: ("/usr/bin/" + n) if n in names else None


class WhereItOpens(unittest.TestCase):
    """Which program gets handed the address.

    **Every fact is injected**, because the machine these run on is a Mac and the case that matters
    is WSL. The rule from AGENTS.md holds either way: what cannot be measured here is still written
    down as what the code will do, so the day someone runs it on WSL the claim is already on record.
    """

    # ── which world are we in ───────────────────────────
    def test_a_mac_is_not_wsl(self):
        self.assertEqual(D.wsl_kind(env={}, osrelease="24.6.0"), "")

    def test_wsl_is_seen_in_the_kernel_release(self):
        """Both WSL1 and WSL2 carry 'microsoft' there."""
        self.assertEqual(D.wsl_kind(env={}, osrelease="5.15.90.1-microsoft-standard-WSL2",
                                    wslg=False), "wsl")
        self.assertEqual(D.wsl_kind(env={}, osrelease="4.4.0-19041-Microsoft", wslg=False), "wsl")

    def test_the_distro_name_alone_is_enough(self):
        """A stripped-down distro can leave that /proc entry unreadable — WSL still sets this."""
        self.assertEqual(D.wsl_kind(env={"WSL_DISTRO_NAME": "Ubuntu"}, osrelease="", wslg=False),
                         "wsl")

    def test_wslg_needs_a_screen_as_well_as_the_mount(self):
        """**The mount is not the point — a display is.** ssh into a WSL distro and /mnt/wslg is
        still mounted while there is nothing to draw a window on."""
        rel = "5.15.90.1-microsoft-standard-WSL2"
        self.assertEqual(D.wsl_kind(env={"WAYLAND_DISPLAY": "wayland-0"}, osrelease=rel,
                                    wslg=True), "wslg")
        self.assertEqual(D.wsl_kind(env={"DISPLAY": ":0"}, osrelease=rel, wslg=True), "wslg")
        self.assertEqual(D.wsl_kind(env={}, osrelease=rel, wslg=True), "wsl")
        self.assertEqual(D.wsl_kind(env={"DISPLAY": ":0"}, osrelease=rel, wslg=False), "wsl")

    # ── what it hands the address to ────────────────────
    def test_browser_env_wins_everywhere(self):
        """The Unix convention, and how someone on WSLg asks for the Linux Firefox over Edge."""
        for platform, kind in (("darwin", ""), ("linux", "wslg"), ("linux", "wsl"), ("linux", "")):
            self.assertEqual(
                D.browser_argv(URL, platform=platform, kind=kind,
                               env={"BROWSER": "my-browser"}, which=having()),
                ["my-browser", URL], "%s/%s ignored $BROWSER" % (platform, kind))

    def test_a_mac_uses_open(self):
        self.assertEqual(D.browser_argv(URL, platform="darwin", bundle="com.apple.Safari", env={}, which=having()),
                         ["open", "-b", "com.apple.Safari", URL])

    def test_wsl_without_a_screen_hands_the_address_to_windows(self):
        """No WSLg, so there is nothing inside Linux to show it on. wslu first — it is the package
        that exists for this — then powershell, then cmd."""
        argv = D.browser_argv(URL, platform="linux", kind="wsl", env={},
                              which=having("wslview", "firefox"))
        self.assertEqual(argv, ["wslview", URL], "a Linux browser was used with no screen for it")
        argv = D.browser_argv(URL, platform="linux", kind="wsl", env={},
                              which=having("powershell.exe"))
        self.assertEqual(argv[0], "powershell.exe")
        self.assertEqual(argv[-1], URL)
        argv = D.browser_argv(URL, platform="linux", kind="wsl", env={}, which=having("cmd.exe"))
        # **The empty string is `start`'s title argument.** Without it the URL becomes the title and
        # nothing opens — the classic `start "http://…"` bug.
        self.assertEqual(argv, ["cmd.exe", "/c", "start", "", URL])

    def test_wslg_prefers_the_browser_next_to_the_daemon(self):
        """With a screen, a Linux browser is the better answer: same side as the daemon, so its
        127.0.0.1 is this machine and nothing has to be forwarded."""
        argv = D.browser_argv(URL, platform="linux", kind="wslg", env={},
                              which=having("firefox", "wslview"))
        self.assertEqual(argv, ["firefox", URL])

    def test_wslg_with_no_linux_browser_still_reaches_windows(self):
        """Most distributions install no browser at all. That must not become 'palmar cannot open'."""
        argv = D.browser_argv(URL, platform="linux", kind="wslg", env={}, which=having("wslview"))
        self.assertEqual(argv, ["wslview", URL])

    def test_plain_linux_uses_a_browser_then_xdg_open(self):
        self.assertEqual(D.browser_argv(URL, platform="linux", kind="", env={},
                                        which=having("chromium", "xdg-open")),
                         ["chromium", URL])
        self.assertEqual(D.browser_argv(URL, platform="linux", kind="", env={},
                                        which=having("xdg-open")),
                         ["xdg-open", URL])

    def test_a_machine_with_nothing_says_so(self):
        """None, not a guess. The caller prints 'open it yourself' — a command that does not exist
        would fail silently into DEVNULL."""
        self.assertIsNone(D.browser_argv(URL, platform="linux", kind="", env={}, which=having()))
        self.assertIsNone(D.browser_argv(URL, platform="linux", kind="wsl", env={}, which=having()))


class GlyphsTheMachineMayNotHave(unittest.TestCase):
    """Characters the chrome draws itself, checked against what a bare Linux box can render.

    palmar already refuses to set the close button as `✕` and draws it in CSS instead, because
    "every font draws it differently" (AGENTS.md). The same reasoning rules out characters a machine
    may not have **at all**: the add-canvas button was a fullwidth `＋` (U+FF0B), which lives in the
    CJK Halfwidth/Fullwidth Forms block, and on a WSL install with no CJK font it drew as an empty
    box (user report 2026-09-11).

    **Hangul is deliberately not on this list.** The IME diagnostic has to print Korean — asking you
    to type 안녕하십니까 in English would not test anything — and `docs/` section titles are quoted in
    Korean on purpose, as grep keys (AGENTS.md). What is banned is the fullwidth and ideographic
    punctuation that gets reached for as an *icon*, which is the mistake that was actually made."""

    #: Blocks a plain Latin font is not expected to cover, and that no piece of chrome needs. Anything
    #: drawn from here wants CSS or an inline SVG instead of a glyph.
    RISKY = (
        (0x2E80, 0x2EFF, "CJK Radicals"),
        (0x3000, 0x303F, "CJK Symbols and Punctuation"),
        (0x31C0, 0x31EF, "CJK Strokes"),
        (0x3200, 0x33FF, "Enclosed CJK / CJK Compatibility"),
        (0x4E00, 0x9FFF, "CJK Unified Ideographs"),
        (0xF900, 0xFAFF, "CJK Compatibility Ideographs"),
        (0xFE30, 0xFE4F, "CJK Compatibility Forms"),
        (0xFF00, 0xFFEF, "Halfwidth and Fullwidth Forms"),
    )

    def test_no_chrome_glyph_needs_a_cjk_font(self):
        web = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "palmar", "web")
        bad = []
        for name in ("index.html", "app.js", "style.css"):
            path = os.path.join(web, name)
            if not os.path.exists(path):
                continue
            html = name.endswith(".html")
            in_block = False
            with open(path, encoding="utf-8") as fh:
                lines = list(enumerate(fh, 1))
            for n, line in lines:
                # Comments explain the ban and quote the banned character while doing it; the fix for
                # this very bug does. Line numbers stay the file's own, so a failure is findable.
                stripped = line.strip()
                if html:
                    if "<!--" in line:
                        in_block = "-->" not in line[line.index("<!--"):]
                        continue
                    if in_block:
                        in_block = "-->" not in line
                        continue
                else:
                    if in_block:
                        in_block = "*/" not in line
                        continue
                    if stripped.startswith("/*"):
                        in_block = "*/" not in stripped[2:]
                        continue
                    if stripped.startswith("//") or stripped.startswith("*"):
                        continue
                    if "//" in line and '"' not in line.split("//")[0] and "'" not in line.split("//")[0]:
                        line = line.split("//")[0]
                for ch in line:
                    for lo, hi, block in self.RISKY:
                        if lo <= ord(ch) <= hi:
                            bad.append("%s:%d  %r (U+%04X, %s)" % (name, n, ch, ord(ch), block))
        self.assertEqual(bad, [], "chrome that needs a CJK font to draw:\n  " + "\n  ".join(bad))


class TheFontStackOrder(unittest.TestCase):
    """`--mono` is the whole UI's font, not just the terminal's — body, tabs, buttons, the lot.

    **On Linux none of the Latin faces named before the generic exist**, so whichever family is
    named next becomes the face for *everything*, Latin included. Naming the Korean families there
    meant that installing D2Coding silently replaced the UI typeface and moved the layout with it
    (regression, 2026-09-11; measured with CDP's getPlatformFontsForNode: Latin resolved to the
    Korean face). The generic has to come first so Latin keeps coming from the system monospace,
    and Hangul — which that face does not have — carries on down the list per character.

    A string test rather than a browser one: the rule is about the order of names, it holds on every
    machine, and a machine that happens to have Menlo cannot see the difference at all."""

    #: Families that exist to supply CJK glyphs. Every one of them belongs after the generic.
    CJK_FAMILIES = ("D2Coding", "Nanum", "Noto Sans Mono CJK", "Noto Sans CJK", "Noto Sans KR",
                    "Malgun", "Apple SD Gothic")

    def mono(self):
        css = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "palmar", "web", "style.css")
        with open(css, encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"--mono:\s*(.*?);", text, re.S)
        self.assertIsNotNone(m, "--mono is not in style.css any more")
        return " ".join(m.group(1).split())

    def test_the_generic_comes_before_the_cjk_families(self):
        stack = self.mono()
        # Split on commas and compare whole family names, so the bare generic `monospace` is not
        # confused with the quoted "Noto Sans Mono CJK KR" that contains the same letters.
        families = [f.strip() for f in stack.split(",")]
        try:
            generic = families.index("monospace")
        except ValueError:
            self.fail("`monospace` is not a family of its own in --mono: " + stack)
        for i, fam in enumerate(families):
            if any(k.lower() in fam.lower() for k in self.CJK_FAMILIES):
                self.assertGreater(
                    i, generic,
                    "%s is named before the generic, so on Linux it becomes the font for Latin too "
                    "and the whole UI changes shape. Stack: %s" % (fam, stack))


class TopOfTheMachine(unittest.TestCase):
    """`tops()` — where the directory rail lets you browse down from.

    **The rail came up empty on Windows** (user, 2026-09-14). The root list opened with a hard-coded
    "/", which on Windows names the root of whichever drive happens to be current — wrong, and not
    even stable. With nothing to pick, *Open terminal here* stayed disabled, so the whole rail was
    dead.

    A top is not a root. `roots()` is still the floor `under_roots` checks, so appearing here does
    not make a place one a terminal can be opened in."""

    def test_posix_has_one_top_and_it_is_slash(self):
        self.assertEqual(D.tops(), ["/"])

    def test_windows_gets_drives_instead(self):
        """Probed rather than listed: os.listdrives is 3.12 and the floor here is 3.9."""
        seen = []

        def isdir(p):
            seen.append(p)
            return p in ("C:\\", "D:\\")

        with mock.patch.object(D.sys, "platform", "win32"), \
             mock.patch.object(D.os.path, "isdir", isdir):
            self.assertEqual(D.tops(), ["C:\\", "D:\\"])
        self.assertIn("C:\\", seen)
        self.assertTrue(all(p.endswith(":\\") for p in seen), seen)

    def test_windows_with_no_drive_still_says_something(self):
        """An empty list would put the rail back where it started."""
        with mock.patch.object(D.sys, "platform", "win32"), \
             mock.patch.object(D.os.path, "isdir", lambda p: False):
            self.assertEqual(D.tops(), ["C:\\"])


class OpeningABrowser(unittest.TestCase):
    """`browser_argv` on native Windows. It returned None there, so the daemon said it could not
    find a way to open one and the person had to copy the address by hand (user, 2026-09-14)."""

    def test_windows_goes_through_cmd_start(self):
        argv = D.browser_argv("http://127.0.0.1:8801/?k=abc", platform="win32")
        self.assertEqual(argv, ["cmd", "/c", "start", "", "http://127.0.0.1:8801/?k=abc"])

    def test_the_empty_title_is_not_decoration(self):
        """`start "http://…"` treats a quoted first argument as the window **title** and opens
        nothing. The empty string takes that slot. The WSL branch has carried this note for days;
        the native branch needed it too."""
        argv = D.browser_argv("http://x/?k=1", platform="win32")
        self.assertEqual(argv[3], "", "the title slot is not empty, so the URL would become a title")

    def test_an_explicit_BROWSER_still_wins_there(self):
        argv = D.browser_argv("http://x/?k=1", platform="win32", env={"BROWSER": "firefox"})
        self.assertEqual(argv, ["firefox", "http://x/?k=1"])


class FirstStartIsNotAFault(unittest.TestCase):
    """The very first start on a HOME printed `run/key 를 못 읽었다 (...)` before making one — which
    reads like something went wrong, and the first thing a person sees on Windows was that line
    (user, 2026-09-14). A missing key on a fresh HOME is the expected case."""

    def test_a_missing_key_file_says_nothing(self):
        home = tempfile.mkdtemp(prefix="palmar-key-")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        said = []
        run = pathlib.Path(home) / ".palmar" / "run"
        run.mkdir(parents=True)
        with mock.patch.object(D, "KEY_FILE", run / "key"), \
             mock.patch.object(D, "log", lambda *a: said.append(" ".join(str(x) for x in a))):
            key = D.load_or_make_key()
        self.assertTrue(key)
        self.assertEqual([m for m in said if "run/key" in m], [],
                         "a fresh HOME was told something had gone wrong: %r" % said)


class TheHomeLock(unittest.TestCase):
    """One daemon per HOME, and the file it locks stays readable.

    **The lock and the content cannot share a byte on Windows.** `msvcrt.locking` is a *mandatory*
    byte-range lock where POSIX `flock` is advisory on the whole file, so locking byte 0 -- where the
    pid line is written -- also locked that line against being read. `palmar --stop` reads the pid to
    know whom to signal, and was refused by the daemon it was trying to stop (user, 2026-09-14).

    Four variants were measured on a runner before this was written, and the one that works was
    among them. It was written the other way anyway, which is the part worth remembering."""

    def lockfile(self):
        d = tempfile.mkdtemp(prefix="palmar-lockbyte-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, "lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        self.addCleanup(lambda: os.close(fd) if fd else None)
        return path, fd

    def test_the_pid_line_is_still_readable_while_locked(self):
        """What --stop and --doctor both need."""
        from palmar import locking
        path, fd = self.lockfile()
        locking.take(fd)
        os.write(fd, b"pid 4242 http://127.0.0.1:8801\n")
        os.lseek(fd, 0, os.SEEK_SET)
        self.assertIn(b"pid 4242", os.read(fd, 256))
        with open(path, "rb") as other:          # and from a second descriptor, which is the real case
            self.assertIn(b"pid 4242", other.read(256))
        locking.release(fd)

    def test_it_still_excludes_a_second_holder(self):
        """Moving the byte must not have made it stop being a lock."""
        from palmar import locking
        path, fd = self.lockfile()
        locking.take(fd)
        fd2 = os.open(path, os.O_RDWR)
        try:
            with self.assertRaises(OSError):
                locking.take(fd2)
        finally:
            os.close(fd2)
        locking.release(fd)

    def test_releasing_twice_is_not_an_error(self):
        from palmar import locking
        _, fd = self.lockfile()
        locking.take(fd)
        locking.release(fd)
        locking.release(fd)


class ReadingTheLockLine(unittest.TestCase):
    """`_read_lock_line` — getting the pid out of run/lock even when its first byte is locked.

    A daemon from before 2026-09-14 locks byte 0 on Windows, where that lock is *mandatory*: the one
    byte the pid line starts with cannot be read while it runs, so `--stop` was refused by the daemon
    it was trying to stop (user, 2026-09-14). Its lock covers exactly one byte, so everything after it
    still reads and the leading "p" can be put back.

    Tolerance for one old build, not a format. The first read simply works for anything newer."""

    def lock_line(self, text):
        d = tempfile.mkdtemp(prefix="palmar-lockline-")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, "lock")
        with open(path, "wb") as fh:
            fh.write(text)
        fd = os.open(path, os.O_RDWR)
        self.addCleanup(os.close, fd)
        return fd

    def test_the_ordinary_read(self):
        fd = self.lock_line(b"pid 4242 http://127.0.0.1:8801\n")
        got, why = D._read_lock_line(fd)
        self.assertIsNone(why)
        self.assertEqual(got.split()[:2], ["pid", "4242"])

    def test_it_reads_around_a_locked_first_byte(self):
        """Simulated by making the first read fail the way a mandatory lock does."""
        fd = self.lock_line(b"pid 4242 http://127.0.0.1:8801\n")
        real = os.read
        state = {"first": True}

        def refuse_once(f, n):
            if state["first"]:
                state["first"] = False
                raise PermissionError(13, "Permission denied")
            return real(f, n)

        with mock.patch.object(D.os, "read", refuse_once):
            got, why = D._read_lock_line(fd)
        self.assertIsNone(why, "it gave up instead of reading past the locked byte")
        self.assertEqual(got.split()[:2], ["pid", "4242"])

    def test_it_gives_up_rather_than_invent_a_pid(self):
        """If what follows is not a pid line, saying so beats signalling a number we guessed."""
        fd = self.lock_line(b"something else entirely\n")

        def always_refuse(f, n):
            raise PermissionError(13, "Permission denied")

        with mock.patch.object(D.os, "read", always_refuse):
            got, why = D._read_lock_line(fd)
        self.assertIsNone(got)
        self.assertIsInstance(why, OSError)


class WindowsNextDoor(unittest.TestCase):
    """A palmar in WSL beside one on Windows. WSL2's loopback is its own, so both bind 8801 and the
    Windows browser reaches the Windows one — which told the WSL one's key it was wrong, and a
    distro without the Windows PATH opened no browser at all (user, 2026-09-15). None of it can be
    run on the Mac this is written on, so every piece is pure and measured here."""

    NETSTAT = ("\nActive Connections\n\n  Proto  Local Address          Foreign Address        State\n"
               "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING\n"
               "  TCP    127.0.0.1:8801         0.0.0.0:0              LISTENING\n"
               "  TCP    127.0.0.1:8801         127.0.0.1:52011        ESTABLISHED\n"
               "  TCP    [::1]:8803             [::]:0                 \uc218\uc2e0 \ub300\uae30\n"
               "  UDP    0.0.0.0:8802           *:*\n")

    def test_netstat_names_the_ports_windows_holds(self):
        self.assertTrue(D.netstat_names_port(self.NETSTAT, 8801))
        self.assertTrue(D.netstat_names_port(self.NETSTAT, 135))
        self.assertTrue(D.netstat_names_port(self.NETSTAT, 8803), "the state word is localised; the address is not")
        self.assertFalse(D.netstat_names_port(self.NETSTAT, 8802), "UDP is not a listener a browser can reach")
        self.assertFalse(D.netstat_names_port(self.NETSTAT, 880), "a prefix of a port is not the port")
        self.assertFalse(D.netstat_names_port("", 8801))

    def test_off_wsl_windows_holds_nothing(self):
        with mock.patch.object(D, "wsl_kind", return_value=""):
            self.assertFalse(D.windows_holds_port(8801))

    S32 = "/mnt/c/Windows/System32"

    def test_wsl_finds_windows_programs_by_their_full_path(self):
        by_path = lambda n: n if n == self.S32 + "/cmd.exe" else None   # noqa: E731 - a which that knows one file
        argv = D.browser_argv(URL, platform="linux", kind="wsl", env={}, which=by_path, sys32=self.S32)
        self.assertEqual(argv, [self.S32 + "/cmd.exe", "/c", "start", "", URL])
        both = lambda n: n if n.startswith(self.S32) else None   # noqa: E731
        argv = D.browser_argv(URL, platform="linux", kind="wsl", env={}, which=both, sys32=self.S32)
        self.assertEqual(argv[0], self.S32 + "/WindowsPowerShell/v1.0/powershell.exe", "powershell before cmd, as by name")
        self.assertIsNone(D.browser_argv(URL, platform="linux", kind="wsl", env={}, which=lambda n: None, sys32=""))

    def test_the_lines_after_the_address(self):
        notes = D.start_notes("opening a browser with wslview", "port 8801 was taken (8801 is held on the Windows side) — this one is on 8802")
        self.assertEqual(len(notes), 4)
        self.assertTrue(notes[0].startswith("port 8801 was taken"), "the moved port comes first — it is the surprise")
        self.assertIn("wslview", notes[1])
        self.assertIn("palmar --stop", notes[2])
        self.assertIn("run/url", notes[3])
        plain = D.start_notes()
        self.assertEqual(len(plain), 2, "nothing to say about the port or the browser: two lines")
        self.assertTrue(D.start_notes(running=True)[0].startswith("already running"))


class TheWindowFirst(unittest.TestCase):
    """`palmar` opens its own window before a browser — "that is what the web button is for"
    (user, 2026-09-15). Where the window is looked for, in order, and when it is not looked for at
    all because there is no screen to put it on."""

    def test_the_order_it_is_looked_for_in(self):
        on_path = lambda n: "/home/me/.local/bin/palmar-app" if n == "palmar-app" else None   # noqa: E731
        self.assertEqual(D.find_app(env={"DISPLAY": ":0"}, which=on_path, exists=lambda p: False, platform="linux"),
                         "/home/me/.local/bin/palmar-app")
        got = D.find_app(env={}, which=lambda n: None, exists=lambda p: p.endswith("app/target/release/palmar-app"),
                         platform="darwin")
        self.assertTrue(got.endswith("app/target/release/palmar-app"), "the checkout's build comes after PATH")
        self.assertEqual(D.find_app(env={}, which=lambda n: None, exists=lambda p: False, platform="darwin"), "")

    def test_palmar_app_names_it_or_turns_it_off(self):
        self.assertEqual(D.find_app(env={"PALMAR_APP": "/opt/w/palmar-app"}, which=lambda n: "/x/palmar-app",
                                    exists=lambda p: p == "/opt/w/palmar-app", platform="darwin"), "/opt/w/palmar-app")
        for off in ("0", "", "no"):
            self.assertEqual(D.find_app(env={"PALMAR_APP": off}, which=lambda n: "/x/palmar-app",
                                        exists=lambda p: True, platform="darwin"), "", off)
        self.assertEqual(D.find_app(env={"PALMAR_APP": "/gone"}, which=lambda n: "/x/palmar-app",
                                    exists=lambda p: p != "/gone", platform="darwin"),
                         "/x/palmar-app", "a PALMAR_APP that points at nothing falls through, and is logged")

    def test_no_screen_no_window(self):
        anywhere = lambda n: "/x/palmar-app"   # noqa: E731
        self.assertEqual(D.find_app(env={}, which=anywhere, exists=lambda p: True, kind="wsl", platform="linux"),
                         "", "WSL without WSLg has no screen inside Linux")
        self.assertEqual(D.find_app(env={"DISPLAY": ":0"}, which=anywhere, exists=lambda p: True, kind="wslg",
                                    platform="linux"), "/x/palmar-app")
        self.assertEqual(D.find_app(env={}, which=anywhere, exists=lambda p: True, kind="", platform="linux"),
                         "", "a Linux with no DISPLAY is an ssh session or a container")
        self.assertEqual(D.find_app(env={"WAYLAND_DISPLAY": "wayland-0"}, which=anywhere, exists=lambda p: True,
                                    kind="", platform="linux"), "/x/palmar-app")
        self.assertEqual(D.find_app(env={}, which=lambda n: "C:/u/palmar-app.exe" if n.endswith(".exe") else None,
                                    exists=lambda p: True, platform="win32"), "C:/u/palmar-app.exe")


class AWindowWithoutAnExe(unittest.TestCase):
    """"Does Windows really need an exe?" (user, 2026-09-15). No: a Chromium-family browser's
    `--app=` is a window with no tabs and no address bar, and every Windows has an Edge. Where it is
    looked for, per platform, and that the URL rides on --app=."""

    ENV = {"ProgramFiles": r"C:\Program Files", "ProgramFiles(x86)": r"C:\Program Files (x86)", "LOCALAPPDATA": r"C:\Users\me\AppData\Local"}
    EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

    def test_windows_takes_the_default_browser_first(self):
        """Edge opened on a machine whose browser is Chrome (user, 2026-09-15)."""
        both = lambda p: p in (self.EDGE, self.CHROME)   # noqa: E731
        self.assertEqual(D.app_mode_argv(URL, platform="win32", env=self.ENV, which=lambda n: None, exists=both, prefer="edge"),
                         [self.EDGE, "--app=" + URL])
        self.assertEqual(D.app_mode_argv(URL, platform="win32", env=self.ENV, which=lambda n: None, exists=both, prefer="chrome"),
                         [self.CHROME, "--app=" + URL])
        self.assertEqual(D.app_mode_argv(URL, platform="win32", env=self.ENV, which=lambda n: None, exists=both, prefer="firefox")[0],
                         self.CHROME, "a default that has no app mode: Chrome before Edge, since installing it was a choice")
        self.assertEqual(D.app_mode_argv(URL, platform="win32", env=self.ENV, which=lambda n: None, exists=both, prefer="")[0],
                         self.CHROME, "and the same when the default cannot be read")
        self.assertEqual(D.app_mode_argv(URL, platform="win32", env=self.ENV, which=lambda n: None,
                                         exists=lambda p: p == self.EDGE, prefer="chrome"), [self.EDGE, "--app=" + URL],
                         "a default that is not installed here is not an option")
        self.assertIsNone(D.app_mode_argv(URL, platform="win32", env=self.ENV, which=lambda n: "/x", exists=lambda p: False, prefer=""),
                          "on Windows a PATH name is not tried — the .exe paths are the whole search")

    def test_the_default_browser_is_read_by_its_name_on_each_platform(self):
        self.assertEqual(D.browser_from_progid("ChromeHTML"), "chrome")
        self.assertEqual(D.browser_from_progid("MSEdgeHTM"), "edge")
        self.assertEqual(D.browser_from_progid("FirefoxURL-308046B0AF4A39CB"), "firefox")
        self.assertEqual(D.browser_from_progid("AppXq0fevzme2pys62n3e0fbqa7peapykr8v"), "", "the old Edge's AppX id is not a browser this knows")
        reg = ("\r\nHKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\Shell\\Associations\\UrlAssociations\\http\\UserChoice\r\n"
               "    ProgId    REG_SZ    ChromeHTML\r\n    Hash    REG_SZ    abc=\r\n\r\n")
        self.assertEqual(D.progid_from_reg_output(reg), "ChromeHTML")
        self.assertEqual(D.progid_from_reg_output("ERROR: The system was unable to find the specified registry key or value."), "")
        self.assertEqual(D.browser_from_bundle("com.google.Chrome"), "chrome")
        self.assertEqual(D.browser_from_bundle("com.apple.Safari"), "safari")
        self.assertEqual(D.browser_from_desktop("google-chrome.desktop"), "chrome")
        self.assertEqual(D.browser_from_desktop("firefox_firefox.desktop"), "firefox")
        self.assertEqual(D.chromium_names("edge")[0], "microsoft-edge")
        self.assertEqual(D.chromium_names("")[0], "google-chrome")

    def test_wsl_reaches_the_windows_edge_through_the_c_mount(self):
        edge = "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
        argv = D.app_mode_argv(URL, platform="linux", kind="wsl", env={}, which=lambda n: None,
                               exists=lambda p: p == edge, cdrive="/mnt/c", prefer="")
        self.assertEqual(argv, [edge, "--app=" + URL])
        argv = D.app_mode_argv(URL, platform="linux", kind="wslg", env={}, which=lambda n: None,
                               exists=lambda p: p == edge, cdrive="/mnt/c", prefer="")
        self.assertEqual(argv[0], edge, "under WSLg too — a Windows window is the one the person sees")
        chrome = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
        argv = D.app_mode_argv(URL, platform="linux", kind="wsl", env={}, which=lambda n: None,
                               exists=lambda p: p in (edge, chrome), cdrive="/mnt/c", prefer="chrome")
        self.assertEqual(argv[0], chrome, "the Windows default browser, read with reg.exe, goes first from WSL too")

    def test_a_mac_and_a_linux(self):
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        self.assertEqual(D.app_mode_argv(URL, platform="darwin", env={}, which=lambda n: None, exists=lambda p: p == chrome, prefer=""),
                         [chrome, "--app=" + URL])
        edge = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        self.assertEqual(D.app_mode_argv(URL, platform="darwin", env={}, which=lambda n: None, exists=lambda p: p in (chrome, edge),
                                         prefer="edge")[0], edge)
        self.assertEqual(D.app_mode_argv(URL, platform="linux", kind="", env={}, which=having("chromium"), exists=lambda p: False, prefer=""),
                         ["chromium", "--app=" + URL])
        self.assertEqual(D.app_mode_argv(URL, platform="linux", kind="", env={}, which=having("chromium", "microsoft-edge"),
                                         exists=lambda p: False, prefer="edge"), ["microsoft-edge", "--app=" + URL])
        self.assertIsNone(D.app_mode_argv(URL, platform="linux", kind="", env={}, which=having("firefox"), exists=lambda p: False, prefer=""),
                          "Firefox has no app mode")

    def test_palmar_chromium_names_it_or_turns_it_off(self):
        self.assertEqual(D.app_mode_argv(URL, platform="darwin", env={"PALMAR_CHROMIUM": "/opt/b/chrome"}, which=lambda n: None,
                                         exists=lambda p: p == "/opt/b/chrome", prefer=""), ["/opt/b/chrome", "--app=" + URL])
        self.assertIsNone(D.app_mode_argv(URL, platform="darwin", env={"PALMAR_CHROMIUM": "0"}, which=having("chromium"),
                                          exists=lambda p: True, prefer=""))


class TheInstalledApp(unittest.TestCase):
    """Once the page is installed as an app, `palmar` opens that — the shortcut Edge or Chrome made on
    Windows, the .app they made on a Mac. Nothing of this can run on the machine the test runs on, so
    the lookup is pure and measured here."""

    def test_a_shortcut_anywhere_under_the_start_menu_is_found(self):
        """Chrome puts it under "Chrome Apps", Edge under Programs, either sometimes in a folder of
        its own — the fixed two paths missed on a real Windows (user, 2026-09-16)."""
        root = tempfile.mkdtemp(prefix="palmar-startmenu-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        deep = os.path.join(root, "Some Browser", "Apps")
        os.makedirs(deep)
        with open(os.path.join(deep, "Palmar.lnk"), "wb") as fh:
            fh.write(b"lnk")
        self.assertEqual(D.shortcut_under(root), os.path.join(deep, "Palmar.lnk"))
        self.assertEqual(D.shortcut_under(os.path.join(root, "nowhere")), "")
        too_deep = os.path.join(root, "a", "b", "c")
        os.makedirs(too_deep)
        with open(os.path.join(too_deep, "palmar.lnk"), "wb") as fh:
            fh.write(b"lnk")
        os.remove(os.path.join(deep, "Palmar.lnk"))
        self.assertEqual(D.shortcut_under(root), "", "three levels down is not the Start Menu any more")

    def test_windows_finds_the_start_menu_shortcut(self):
        env = {"APPDATA": r"C:\Users\me\AppData\Roaming"}
        edge = r"C:\Users\me\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\palmar.lnk"
        chrome = r"C:\Users\me\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Chrome Apps\palmar.lnk"
        self.assertEqual(D.installed_pwa(env=env, exists=lambda p: p == edge, platform="win32"), edge)
        self.assertEqual(D.installed_pwa(env=env, exists=lambda p: p == chrome, platform="win32"), chrome)
        self.assertEqual(D.installed_pwa(env=env, exists=lambda p: False, platform="win32"), "")
        self.assertEqual(D.pwa_argv(edge, platform="win32"), ["cmd", "/c", "start", "", edge])

    def test_a_mac_finds_the_app_bundle(self):
        env = {"HOME": "/Users/me"}
        app = "/Users/me/Applications/Chrome Apps.localized/palmar.app"
        self.assertEqual(D.installed_pwa(env=env, exists=lambda p: p == app, platform="darwin"), app)
        self.assertEqual(D.pwa_argv(app, platform="darwin"), ["open", app])
        self.assertEqual(D.installed_pwa(env={"HOME": "/Users/me"}, exists=lambda p: False, platform="darwin"), "")
        self.assertEqual(D.installed_pwa(env={}, exists=lambda p: True, platform="linux"), "", "not looked for on Linux yet")


class TheOrderPerPlatform(unittest.TestCase):
    """Decided 2026-09-15 (user): the Mac opens palmar's own window first; everywhere else the
    installed app, then a Chromium in app mode, and palmar's own window only for a machine without
    one. A tab is always last."""

    def test_the_order(self):
        self.assertEqual(D.window_order("darwin"), ("app", "pwa", "mode", "tab"))
        self.assertEqual(D.window_order("linux"), ("pwa", "mode", "app", "tab"))
        self.assertEqual(D.window_order("win32"), ("pwa", "mode", "app", "tab"))

    def test_from_wsl_the_windows_shortcut_counts(self):
        appdata = r"C:\Users\me\AppData\Roaming"
        lnk = appdata + r"\Microsoft\Windows\Start Menu\Programs\palmar.lnk"
        self.assertEqual(D.windows_path_on_wsl(lnk, "/mnt/c"),
                         "/mnt/c/Users/me/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/palmar.lnk")
        self.assertEqual(D.windows_path_on_wsl(r"D:\x\y", "/mnt/c"), "/mnt/d/x/y")
        self.assertEqual(D.windows_path_on_wsl("not a windows path", "/mnt/c"), "")
        seen = lambda p: p == "/mnt/c/Users/me/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/palmar.lnk"   # noqa: E731
        self.assertEqual(D.installed_pwa(env={}, exists=seen, platform="linux", kind="wsl", appdata=appdata, cdrive="/mnt/c"),
                         lnk, "the Windows path comes back — that is what cmd.exe's start needs")
        self.assertEqual(D.installed_pwa(env={}, exists=seen, platform="linux", kind="", appdata=appdata, cdrive="/mnt/c"), "",
                         "off WSL a Linux has no Windows shortcut")
        self.assertEqual(D.installed_pwa(env={}, exists=lambda p: True, platform="linux", kind="wsl", appdata="", cdrive="/mnt/c"), "",
                         "no %APPDATA% (no interop) means no shortcut to look for")
        self.assertEqual(D.pwa_argv(lnk, platform="linux", kind="wsl", cmd_exe="/mnt/c/Windows/System32/cmd.exe"),
                         ["/mnt/c/Windows/System32/cmd.exe", "/c", "start", "", lnk])


class TheOsSaysIt(unittest.TestCase):
    """palmar's own window on a Mac (WKWebView) has no Notification API, so the daemon asks the OS.
    What it runs, per platform, and that the words survive AppleScript's quoting."""

    def test_a_mac_uses_osascript_and_quotes_the_words(self):
        argv = D.notifier_argv('agent "one" wants you', "/home/me", platform="darwin", env={}, which=having("osascript"))
        self.assertEqual(argv[:2], ["osascript", "-e"])
        self.assertEqual(argv[2], 'display notification "/home/me" with title "agent \\"one\\" wants you"')

    def test_a_linux_uses_notify_send(self):
        self.assertEqual(D.notifier_argv("t", "b", platform="linux", env={}, which=having("notify-send")),
                         ["notify-send", "--app-name=palmar", "--", "t", "b"])   # `--`: a title starting with - is not an option
        self.assertIsNone(D.notifier_argv("t", "b", platform="linux", env={}, which=having()))
        self.assertIsNone(D.notifier_argv("t", "b", platform="win32", env={}, which=lambda n: "/x"),
                          "Windows shows the page in a browser that has the API — nothing needed yet")

    def test_palmar_notifier_names_it_or_turns_it_off(self):
        self.assertEqual(D.notifier_argv("t", "b", platform="darwin", env={"PALMAR_NOTIFIER": "/opt/say"}, which=having("osascript")),
                         ["/opt/say", "t", "b"])
        self.assertIsNone(D.notifier_argv("t", "b", platform="darwin", env={"PALMAR_NOTIFIER": "0"}, which=having("osascript")))


class NothingRuns(unittest.TestCase):
    """The system opener executes some things instead of showing them. What /api/open refuses."""

    def test_posix_refuses_an_execute_bit_and_the_launcher_types(self):
        self.assertTrue(D.would_run(pathlib.Path("/r/x.sh"), platform="darwin", mode=0o755))
        self.assertFalse(D.would_run(pathlib.Path("/r/x.sh"), platform="darwin", mode=0o644), "a script with no execute bit opens in an editor")
        for ext in (".command", ".app", ".desktop", ".pkg", ".jar"):
            self.assertTrue(D.would_run(pathlib.Path("/r/x" + ext), platform="linux", mode=0o644), ext)
        self.assertFalse(D.would_run(pathlib.Path("/r/x.pdf"), platform="linux", mode=0o644))

    def test_windows_refuses_what_the_shell_executes(self):
        for ext in (".bat", ".cmd", ".exe", ".vbs", ".js", ".lnk", ".ps1", ".hta", ".url"):
            self.assertTrue(D.would_run(pathlib.Path(r"C:\\r\\x" + ext), platform="win32"), ext)
        self.assertFalse(D.would_run(pathlib.Path(r"C:\\r\\x.xlsx"), platform="win32"))


class TheKeyStaysOffTheCommandLine(unittest.TestCase):
    """What a browser is handed instead of the keyed address: a 0600 file on a Mac or a Linux, a
    one-time address where a file cannot cross (Windows, WSL)."""

    def test_windows_and_wsl_get_a_one_time_address(self):
        D.PORT[0] = 8801
        D.SERVING[0] = True                          # the daemon itself mints; see the test below for the other case
        self.addCleanup(lambda: D.SERVING.__setitem__(0, False))
        t = D.launch_target("http://127.0.0.1:8801/?k=SECRET", platform="win32", host="localhost")
        self.assertTrue(t.startswith("http://localhost:8801/once?n="), t)
        self.assertNotIn("SECRET", t)
        n = t.split("n=", 1)[1]
        self.assertTrue(D.take_once(n), "the nonce did not open")
        self.assertTrue(D.take_once(n), "a second fetch inside the seconds must work — Chrome fetches --app= twice when the app is installed")
        D.ONCE[n] = 0                                # the seconds ran out
        self.assertFalse(D.take_once(n), "an expired nonce opened")
        t = D.launch_target("http://127.0.0.1:8801/?k=SECRET", platform="linux", kind="wsl", host="localhost")
        self.assertTrue(t.startswith("http://localhost:8801/once?n="))

    def test_a_process_that_is_not_the_daemon_does_not_mint(self):
        """A second `palmar` minting in its own memory gave the browser an address the daemon had
        never seen — "expired" (user, 2026-09-16). It asks the daemon; with none to ask (here), the
        keyed address itself goes, which on these platforms is the boundary it had anyway."""
        D.SERVING[0] = False
        with mock.patch.object(D, "once_from_daemon", return_value=""):
            self.assertEqual(D.launch_target("http://127.0.0.1:8801/?k=SECRET", platform="win32"),
                             "http://127.0.0.1:8801/?k=SECRET")
        with mock.patch.object(D, "once_from_daemon", return_value="http://127.0.0.1:8801/once/abc"):
            self.assertEqual(D.launch_target("http://127.0.0.1:8801/?k=SECRET", platform="linux", kind="wsl"),
                             "http://127.0.0.1:8801/once/abc")

    def test_a_spent_or_unknown_nonce_is_nothing(self):
        self.assertFalse(D.take_once("never-minted"))
        n = D.mint_once()
        D.ONCE[n] = 0                                # expired
        self.assertFalse(D.take_once(n))
        self.assertNotIn(n, D.ONCE, "an expired nonce is forgotten, not kept")


class TheReleaseCheckIsOffUntilAsked(unittest.TestCase):
    """palmar opens no connection of its own. The release check is the one exception, and the user
    chose that it stay **off until somebody turns it on** (2026-09-16) — "no network at runtime" is a
    promise both READMEs make, and it has to keep being true for anybody who never touches it."""

    def test_off_by_default(self):
        self.assertFalse(D.update_check_on(env={}, settings={}))

    def test_only_a_real_true_in_the_file_turns_it_on(self):
        self.assertTrue(D.update_check_on(env={}, settings={"update_check": True}))
        self.assertFalse(D.update_check_on(env={}, settings={"update_check": "yes"}))
        self.assertFalse(D.update_check_on(env={}, settings={"update_check": 1}))

    def test_the_environment_overrules_the_file_either_way(self):
        """A locked-down machine needs a way that does not go through a screen, and a scripted one
        needs a way that does not need a click."""
        on = {"update_check": True}
        for off in ("0", "no", "off", "", "  "):
            self.assertFalse(D.update_check_on(env={"PALMAR_UPDATE_CHECK": off}, settings=on), repr(off))
        self.assertTrue(D.update_check_on(env={"PALMAR_UPDATE_CHECK": "1"}, settings={}))


class WhichVersionIsNewer(unittest.TestCase):
    def test_a_plain_release_reads(self):
        self.assertEqual(D.version_tuple("v0.1.2"), (0, 1, 2))
        self.assertEqual(D.version_tuple(" 0.1.2 "), (0, 1, 2))

    def test_anything_else_is_nothing(self):
        for bad in ("", "v1.2", "1.2.3.4", "v0.1.2-rc1", "latest", None):
            self.assertIsNone(D.version_tuple(bad), repr(bad))

    def test_newer_only_when_both_read_and_it_really_is(self):
        self.assertTrue(D.is_newer("0.1.2", "0.1.1"))
        self.assertTrue(D.is_newer("0.2.0", "0.1.9"))
        self.assertTrue(D.is_newer("1.0.0", "0.9.9"))
        self.assertFalse(D.is_newer("0.1.1", "0.1.1"))
        self.assertFalse(D.is_newer("0.1.0", "0.1.1"))
        self.assertFalse(D.is_newer("nightly", "0.1.1"), "a tag we cannot read must not read as newer")
        self.assertFalse(D.is_newer("0.1.2", "what"))

    def test_ten_comes_after_nine(self):
        self.assertTrue(D.is_newer("0.10.0", "0.9.0"), "compared as numbers, not as text")


class TheReleaseCheckAsksOnce(unittest.TestCase):
    """One HEAD, and the version is read off the address the redirect landed on — no page comes down
    and nothing but the tag is learned."""

    class Answer:
        def __init__(self, url):
            self.url = url

        def geturl(self):
            return self.url

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener_for(self, landed):
        seen = []

        def opener(req, timeout=None):
            seen.append((req.get_method(), req.full_url))
            return self.Answer(landed)
        return opener, seen

    def test_the_tag_comes_from_where_the_redirect_landed(self):
        opener, seen = self.opener_for("https://github.com/maengyo/palmar/releases/tag/v9.9.9")
        self.assertEqual(D.fetch_latest(url="https://example.invalid/x", opener=opener), "9.9.9")
        self.assertEqual(seen[0][0], "HEAD", "it must not pull the page down")

    def test_an_address_that_is_not_a_release_tag_says_nothing(self):
        for landed in ("https://github.com/login?return_to=x",
                       "https://github.com/maengyo/palmar/releases",
                       "https://github.com/maengyo/palmar/releases/tag/",
                       "https://elsewhere.example/a/b/releases/tag/v9.9.9/and/more",
                       "not a url", ""):
            opener, _ = self.opener_for(landed)
            self.assertEqual(D.fetch_latest(url="https://example.invalid/x", opener=opener), "", landed)

    def test_no_network_is_quiet(self):
        def opener(req, timeout=None):
            raise OSError("nothing out there")
        self.assertEqual(D.fetch_latest(url="https://example.invalid/x", opener=opener), "")


class WhatTheDaemonHandsBackIsChecked(unittest.TestCase):
    """`/api/once`'s answer goes onto a command line and into a browser. The check used to be the
    substring `/once/`, which moving the nonce into the query broke — and which a URL pointing
    somewhere else entirely satisfied anyway (found while moving it, 2026-09-16)."""

    BASE = "http://127.0.0.1:8801"

    def test_both_spellings_of_our_own_address_pass(self):
        self.assertTrue(D.is_once_url("http://127.0.0.1:8801/once?n=abc", self.BASE))
        self.assertTrue(D.is_once_url("http://localhost:8801/once?n=abc", self.BASE))
        self.assertTrue(D.is_once_url("http://127.0.0.1:8801/once/abc", self.BASE), "the old spelling still opens")

    def test_anywhere_else_is_refused(self):
        for bad in ("http://elsewhere.example/x/once/y",      # the substring check let this through
                    "https://127.0.0.1:8801/once?n=abc",      # not the scheme we serve
                    "http://127.0.0.1:9999/once?n=abc",       # another port — another daemon
                    "http://127.0.0.1:8801/?k=SECRET",        # the keyed address is what we are avoiding
                    "http://127.0.0.1:8801/oncearoo?n=abc",
                    "javascript:alert(1)", "", "not a url"):
            self.assertFalse(D.is_once_url(bad, self.BASE), bad)


class TheAppWindowKeepsItsSize(unittest.TestCase):
    """What the browser is handed decides whether the window comes back the size it was left.

    Chromium saves an `--app` window's bounds under a key made of the host and the path. A nonce in
    the **path** made a new key on every launch, and a dot in the **host** made a key Chromium writes
    by dotted path and reads back flat. Both had to go. On the user's Windows machine the window came
    up 1050x892 — 1.18:1 — every time, however wide it had been dragged (2026-09-16)."""

    def setUp(self):
        D.SERVING[0] = True                          # the daemon itself mints
        self.addCleanup(lambda: D.SERVING.__setitem__(0, False))

    def test_only_the_query_changes_between_launches(self):
        a = D.launch_target("http://127.0.0.1:8801/?k=SECRET", platform="win32", host="localhost")
        b = D.launch_target("http://127.0.0.1:8801/?k=SECRET", platform="win32", host="localhost")
        pa, pb = urllib.parse.urlsplit(a), urllib.parse.urlsplit(b)
        self.assertEqual((pa.scheme, pa.netloc, pa.path), (pb.scheme, pb.netloc, pb.path),
                         "anything but the query differing is a new window to Chromium")
        self.assertEqual(pa.path, "/once")
        self.assertNotEqual(pa.query, pb.query, "the nonce has to be fresh each launch")

    def test_the_host_has_no_dot_when_localhost_reaches_us(self):
        class Sock:
            def close(self):
                pass
        self.assertEqual(D.loopback_name(8801, connect=lambda addr, t: Sock()), "localhost")

    def test_it_falls_back_to_the_numeric_host_when_localhost_does_not_reach_us(self):
        """The daemon binds 127.0.0.1 alone. A machine whose localhost is ::1 and nothing else would
        be handed an address that goes nowhere, and a window with no size is better than no window."""
        def refuse(addr, t):
            raise OSError("localhost is ::1 here")
        self.assertEqual(D.loopback_name(8801, connect=refuse), "127.0.0.1")


class WhoElseCanRead(unittest.TestCase):
    """What --doctor says about ~/.palmar's ACL on Windows, from icacls text measured on the real
    machine (2026-09-16): the default is the person, SYSTEM and Administrators, nothing else."""

    GOOD = ("C:\\Users\\me\\.palmar NT AUTHORITY\\SYSTEM:(OI)(CI)(F)\n"
            "                    BUILTIN\\Administrators:(OI)(CI)(F)\n"
            "                    OWNER RIGHTS:(OI)(CI)(F)\n\nSuccessfully processed 1 files; Failed processing 0 files\n")

    def test_the_measured_default_is_clean(self):
        self.assertEqual(D.acl_strangers(self.GOOD), [])

    def test_everyone_and_users_are_named(self):
        text = self.GOOD.replace("OWNER RIGHTS:(OI)(CI)(F)", "OWNER RIGHTS:(OI)(CI)(F)\n                    Everyone:(OI)(CI)(RX)\n                    BUILTIN\\Users:(RX)")
        self.assertEqual(D.acl_strangers(text), ["BUILTIN\\Users", "Everyone"])
        self.assertEqual(D.acl_strangers(""), [])


class TheWindowInFront(unittest.TestCase):
    """`palmar` while palmar is open brings that window forward. On Windows the window is found by
    class and title — the class every Chromium window has, and the page's title with or without the
    badge; the terminal that ran `palmar` is titled palmar too, and is not it."""

    def test_which_window_is_palmars(self):
        self.assertTrue(D.is_palmar_window("palmar", "Chrome_WidgetWin_1"))
        self.assertTrue(D.is_palmar_window("(2) palmar", "Chrome_WidgetWin_1"))
        self.assertFalse(D.is_palmar_window("palmar", "CASCADIA_HOSTING_WINDOW_CLASS"), "Windows Terminal running `palmar`")
        self.assertFalse(D.is_palmar_window("GitHub - Google Chrome", "Chrome_WidgetWin_1"))
        self.assertFalse(D.is_palmar_window("", "Chrome_WidgetWin_1"))


if __name__ == "__main__":
    unittest.main()
