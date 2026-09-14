"""ConPTY, called directly — palmar's pseudo-terminal on Windows.

**Why not pywinpty.** It was chosen on 2026-09-09 to avoid writing this file, and measured on
2026-09-11 to be unable to carry palmar's bytes: its `read()` returns `str`, so a NUL in a pane's
output disappears and a character straddling its 32 KB read becomes U+FFFD — 8 of them in 210 KB of
Korean, one per read boundary. palmar is a byte-exact pipe from the PTY to xterm.js, so that is not
a detail. The other half of the argument went too: a pane needs a job object or its agent outlives
it (measured), and `CreateJobObjectW` is ctypes either way. See docs/decisions.md.

**What this is.** One pseudo-console, the process inside it, and a job object holding that process
and everything it starts. Bytes in, bytes out, nothing decoded. It knows nothing about palmar — it
is a PTY, and `daemon.py` is what makes it a pane.

**What it is not.** Reading here is blocking: `ReadFile` on the ConPTY's output pipe returns when
there is something, and Windows offers no way to select on it. asyncio's Proactor loop has no
`add_reader` at all (measured — it raises NotImplementedError even for a socket), so the caller runs
`read()` on a thread. That is the shape the daemon has to take on Windows and it is decided here.

Everything is `ctypes` against `kernel32`. No dependency, on any platform.
"""
from __future__ import annotations

import os
import sys

if sys.platform != "win32":                      # pragma: no cover - the module is Windows-only
    raise ImportError("palmar.conpty is for Windows; POSIX uses pty.fork in daemon.py")

import ctypes
from ctypes import byref, sizeof, wintypes

# ── the Win32 surface ──────────────────────────────────────────────────────────────────────
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
STILL_ACTIVE = 259
ERROR_BROKEN_PIPE = 109
ERROR_HANDLE_EOF = 38

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
#: The attribute that hands a child its pseudo-console. The number is from ProcThreadAttributeList
#: in the SDK headers and there is no name for it in Python.
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
#: Say the child's standard handles are given, then give none — see the note in spawn().
STARTF_USESTDHANDLES = 0x00000100

JobObjectExtendedLimitInformation = 9
#: **The one that matters.** When the last handle to the job closes — including because palmar was
#: killed — every process still in it dies. Without it a closed pane leaves its agent running
#: (measured 2026-09-11: killing a shell left its child alive).
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.POINTER(ctypes.c_ulong)), ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


# ── prototypes ─────────────────────────────────────────────────────────────────────────────
# **Declared, not guessed.** Without argtypes, ctypes passes a Python int as a C int, so a 64-bit
# pointer overflows — InitializeProcThreadAttributeList died with "int too long to convert" on the
# first real run (2026-09-11). Every call below is spelled out once, here.
_P = ctypes.POINTER
LPVOID = ctypes.c_void_p
SIZE_T = ctypes.c_size_t

kernel32.CreatePipe.argtypes = [_P(wintypes.HANDLE), _P(wintypes.HANDLE),
                                _P(SECURITY_ATTRIBUTES), wintypes.DWORD]
kernel32.CreatePipe.restype = wintypes.BOOL

kernel32.InitializeProcThreadAttributeList.argtypes = [LPVOID, wintypes.DWORD, wintypes.DWORD,
                                                       _P(SIZE_T)]
kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL

kernel32.UpdateProcThreadAttribute.argtypes = [LPVOID, wintypes.DWORD, SIZE_T, LPVOID, SIZE_T,
                                               LPVOID, _P(SIZE_T)]
kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL

kernel32.DeleteProcThreadAttributeList.argtypes = [LPVOID]
kernel32.DeleteProcThreadAttributeList.restype = None

kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, _P(SECURITY_ATTRIBUTES),
                                    _P(SECURITY_ATTRIBUTES), wintypes.BOOL, wintypes.DWORD,
                                    LPVOID, wintypes.LPCWSTR, _P(STARTUPINFOEXW),
                                    _P(PROCESS_INFORMATION)]
kernel32.CreateProcessW.restype = wintypes.BOOL

kernel32.CreateJobObjectW.argtypes = [_P(SECURITY_ATTRIBUTES), wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, LPVOID, wintypes.DWORD]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateJobObject.restype = wintypes.BOOL

kernel32.ReadFile.argtypes = [wintypes.HANDLE, LPVOID, wintypes.DWORD, _P(wintypes.DWORD), LPVOID]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [wintypes.HANDLE, LPVOID, wintypes.DWORD, _P(wintypes.DWORD), LPVOID]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, _P(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, LPVOID]
kernel32.CancelIoEx.restype = wintypes.BOOL
kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
kernel32.GetStdHandle.restype = wintypes.HANDLE
kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
kernel32.SetHandleInformation.restype = wintypes.BOOL

# Absent before Windows 10 1809, so `available()` below can still answer instead of crashing here.
if hasattr(kernel32, "CreatePseudoConsole"):
    kernel32.CreatePseudoConsole.argtypes = [COORD, wintypes.HANDLE, wintypes.HANDLE,
                                             wintypes.DWORD, _P(wintypes.HANDLE)]
    kernel32.CreatePseudoConsole.restype = ctypes.HRESULT
    kernel32.ResizePseudoConsole.argtypes = [wintypes.HANDLE, COORD]
    kernel32.ResizePseudoConsole.restype = ctypes.HRESULT
    kernel32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
    kernel32.ClosePseudoConsole.restype = None


def _err(name):
    raise OSError(ctypes.get_last_error(), "%s failed" % name)


def available() -> bool:
    """Is ConPTY here at all? Windows 10 1809 and later. Older Windows gets a sentence, not a
    stack — the same rule the POSIX side follows for a missing feature."""
    return hasattr(kernel32, "CreatePseudoConsole")


class ConPty:
    """A pseudo-console, the process in it, and the job that owns its whole tree."""

    #: **Nothing here can be waited on.** asyncio's Proactor loop has no add_reader at all — it
    #: raises NotImplementedError even for a socket (measured) — so the daemon reads on a thread.
    #: This is the one thing it branches on.
    blocking = True

    def __init__(self):
        self._hpc = None            # HPCON
        self._in_w = None           # our end: what we write to the child
        self._out_r = None          # our end: what the child prints
        self._job = None
        self._proc = None           # process HANDLE
        self.pid = None
        self.closed = False

    # ── starting ───────────────────────────────────────────────────────
    def spawn(self, argv, env=None, cwd=None, rows: int = 24, cols: int = 80, out_pipe=None) -> None:
        """Start `argv` inside a new pseudo-console.

        **Takes a list, like the POSIX side**, so the daemon never has to know which platform it is
        on. CreateProcessW wants one string and the quoting rules are Windows', so the joining
        happens here — `subprocess.list2cmdline` is those rules, in the standard library.
        **The caller chooses the command** — the daemon hard-codes it (#14), which is a security
        rule, not a convenience. A bare string is still accepted for the probes."""
        import subprocess
        cmdline = argv if isinstance(argv, str) else subprocess.list2cmdline(list(argv))
        if not available():
            raise OSError("this Windows has no ConPTY (needs 10 1809 or newer)")

        sa = SECURITY_ATTRIBUTES(sizeof(SECURITY_ATTRIBUTES), None, True)
        in_r = wintypes.HANDLE(); in_w = wintypes.HANDLE()
        if not kernel32.CreatePipe(byref(in_r), byref(in_w), byref(sa), 0):
            _err("CreatePipe(in)")
        # **Where the output pipe comes from is a seam.** `CreatePipe` makes an anonymous pipe, which
        # cannot be opened for overlapped I/O — so reading it means blocking, which means a thread.
        # A named pipe created with FILE_FLAG_OVERLAPPED can go to asyncio's Proactor loop instead,
        # and whether ConPTY accepts one is a question for a machine, not for reasoning. `out_pipe`
        # lets a probe answer it without a second copy of everything below (#29 step 2).
        if out_pipe is not None:
            out_r, out_w = out_pipe()
        else:
            out_r = wintypes.HANDLE(); out_w = wintypes.HANDLE()
            if not kernel32.CreatePipe(byref(out_r), byref(out_w), byref(sa), 0):
                _err("CreatePipe(out)")

        # **The console gets the far ends.** It reads what we write into in_w and writes what we
        # read out of out_r; we must let go of the two it owns or the pipes never see EOF.
        size = COORD(max(1, int(cols)), max(1, int(rows)))
        hpc = wintypes.HANDLE()
        kernel32.CreatePseudoConsole(size, in_r, out_w, 0, byref(hpc))
        self._hpc = hpc

        # The attribute list is the only way to hand a child its console.
        need = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, byref(need))
        buf = (ctypes.c_ubyte * need.value)()
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = sizeof(STARTUPINFOEXW)
        si.lpAttributeList = ctypes.cast(buf, ctypes.c_void_p)
        if not kernel32.InitializeProcThreadAttributeList(si.lpAttributeList, 1, 0, byref(need)):
            _err("InitializeProcThreadAttributeList")
        if not kernel32.UpdateProcThreadAttribute(
                si.lpAttributeList, 0, ctypes.c_size_t(PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE),
                hpc, ctypes.c_size_t(sizeof(wintypes.HANDLE)), None, None):
            _err("UpdateProcThreadAttribute")

        # **Three NULL standard handles, and this is not a detail.** The attribute above puts the
        # child in our console; it does not decide where the child's stdout goes. Left alone,
        # Windows hands the child the parent's standard handles, and palmar's are a pipe whenever it
        # was not started from a terminal — so a pane's output went to the daemon's stdout instead of
        # into the pane. Measured on a runner: the child was provably in our console (CONOUT$ came
        # through) while everything it printed normally escaped to the job log.
        #
        # With STARTF_USESTDHANDLES set and nothing to hand over, the runtime opens CONIN$/CONOUT$
        # itself — which is the console the attribute gave it. The parent cannot open the child's
        # console to pass it in, so this is the way to say "use your own".
        si.StartupInfo.dwFlags |= STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput = None
        si.StartupInfo.hStdOutput = None
        si.StartupInfo.hStdError = None

        # **The job is made before the process and kept for its life.** Closing the last handle to
        # it kills everything inside, which is what takes an agent down with its pane.
        self._job = kernel32.CreateJobObjectW(None, None)
        if not self._job:
            _err("CreateJobObjectW")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
                self._job, JobObjectExtendedLimitInformation, byref(info), sizeof(info)):
            _err("SetInformationJobObject")

        pi = PROCESS_INFORMATION()
        ok = kernel32.CreateProcessW(
            None, ctypes.create_unicode_buffer(cmdline), None, None, False,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT,
            _env_block(env), cwd, byref(si), byref(pi))
        if not ok:
            _err("CreateProcessW")
        self._proc, self.pid = pi.hProcess, pi.dwProcessId
        kernel32.CloseHandle(pi.hThread)
        if not kernel32.AssignProcessToJobObject(self._job, self._proc):
            _err("AssignProcessToJobObject")

        kernel32.DeleteProcThreadAttributeList(si.lpAttributeList)
        # Our copies of the console's ends. **Not closing these is the classic ConPTY hang** — the
        # output pipe never reports EOF because we are still holding a writer.
        kernel32.CloseHandle(in_r)
        kernel32.CloseHandle(out_w)
        self._in_w, self._out_r = in_w, out_r

    # ── bytes ──────────────────────────────────────────────────────────
    def read(self, n: int = 65536) -> bytes:
        """Block until there is output, and return it **as bytes**. b"" means the child is gone.

        There is no way to wait on this handle alongside anything else, so the caller runs it on a
        thread — that is what the whole Windows read path is shaped around."""
        if self._out_r is None:
            return b""
        buf = (ctypes.c_char * n)()
        got = wintypes.DWORD(0)
        if not kernel32.ReadFile(self._out_r, buf, n, byref(got), None):
            code = ctypes.get_last_error()
            if code in (ERROR_BROKEN_PIPE, ERROR_HANDLE_EOF):
                return b""            # the console closed — the pane has ended, not an error
            raise OSError(code, "ReadFile failed")
        return bytes(buf[:got.value])

    def write(self, data: bytes) -> int:
        """Bytes to the child. Returns how many went, like os.write."""
        if self._in_w is None or not data:
            return 0
        put = wintypes.DWORD(0)
        if not kernel32.WriteFile(self._in_w, data, len(data), byref(put), None):
            code = ctypes.get_last_error()
            if code == ERROR_BROKEN_PIPE:
                return 0
            raise OSError(code, "WriteFile failed")
        return put.value

    def resize(self, rows: int, cols: int) -> None:
        if self._hpc is None:
            return
        kernel32.ResizePseudoConsole(self._hpc, COORD(max(1, int(cols)), max(1, int(rows))))

    def fileno(self):
        """**None, and that is the point.** There is no handle here the event loop can wait on; see
        `blocking` above. The POSIX side returns its master fd."""
        return None

    def foreground_is_shell(self):
        """Unknowable on Windows. ConPTY has no foreground process group, and GetConsoleProcessList
        needs the caller attached to that console — so the status lights fall back to what the title
        and the output say, which is how they work for an unknown agent anyway."""
        return None

    def hangup(self) -> None:
        """There is no SIGHUP. Terminating the job is the closest thing, and it is also the only
        thing that takes the pane's children with it — measured: killing the shell alone does not."""
        self.kill()

    # ── life and death ─────────────────────────────────────────────────
    def alive(self) -> bool:
        if self._proc is None:
            return False
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(self._proc, byref(code)):
            return False
        return code.value == STILL_ACTIVE

    def exit_status(self):
        """The child's exit code, or None while it is still running."""
        if self._proc is None:
            return None
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(self._proc, byref(code)):
            return None
        return None if code.value == STILL_ACTIVE else code.value

    def kill(self) -> None:
        """**The job, not the process.** Killing the shell alone leaves whatever it started running
        — measured on Windows, and the reason the job exists."""
        if self._job:
            kernel32.TerminateJobObject(self._job, 1)

    def close(self) -> None:
        """Let go of everything, in the order that does not hang.

        ClosePseudoConsole waits for the console's own thread to drain, so the process goes first
        and our pipe ends go before the console does."""
        if self.closed:
            return
        self.closed = True
        self.kill()
        # **Unblock the reader first.** A thread sitting in ReadFile on the output pipe keeps the
        # console busy, and ClosePseudoConsole waits for the console to drain — so closing in the
        # obvious order deadlocks. Measured: a check that should take seconds took thirteen minutes
        # and was killed by its timeout (2026-09-11).
        if self._out_r:
            kernel32.CancelIoEx(self._out_r, None)
        for h in ("_in_w", "_out_r"):
            v = getattr(self, h)
            if v:
                kernel32.CloseHandle(v)
                setattr(self, h, None)
        if self._hpc is not None:
            kernel32.ClosePseudoConsole(self._hpc)
            self._hpc = None
        for h in ("_proc", "_job"):
            v = getattr(self, h)
            if v:
                kernel32.CloseHandle(v)
                setattr(self, h, None)


def _env_block(env):
    """A CreateProcessW environment block, or None to inherit ours.

    Windows wants one NUL-separated wide string ending in two NULs, and **sorted** — the docs say
    the block should be sorted and some programs believe it."""
    if env is None:
        return None
    items = sorted(("%s=%s" % (k, v) for k, v in env.items()), key=lambda s: s.upper())
    blob = "\0".join(items) + "\0\0"
    return ctypes.cast(ctypes.create_unicode_buffer(blob), ctypes.c_void_p)


def default_shell(env=None) -> list:
    """What palmar opens when nobody said otherwise.

    There is no $SHELL on Windows. COMSPEC is the closest the OS offers and it is cmd.exe; PowerShell
    is what people actually use. **The client never chooses this** (#14) — this is the daemon's own
    default and the only place it is decided.

    **Returns a list, like the POSIX side.** It used to return a quoted command line, which is a
    different type for the same call on the same seam — the daemon builds argv as a list, so that
    asymmetry was a bug waiting for the first Windows pane. `spawn` joins with Windows' own quoting
    rules anyway (`subprocess.list2cmdline`), so the list is the right shape to hand it."""
    env = os.environ if env is None else env
    for name in ("pwsh.exe", "powershell.exe"):
        found = _which(name, env)
        if found:
            return [found, "-NoLogo"]
    return [env.get("COMSPEC") or "cmd.exe"]


def _which(name, env):
    for d in (env.get("PATH") or "").split(os.pathsep):
        if not d:
            continue
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None
