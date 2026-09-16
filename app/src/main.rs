//! palmar in its own window.
//!
//! The daemon is the program that owns the terminals; this is the program you look at it through.
//! It does three things and stops:
//!
//!   1. find a running daemon (`~/.palmar/run/url`, then check the port actually answers),
//!   2. start one if there is none — `palmar --no-browser`, reading the address off stdout, which
//!      is a contract: "stdout's last line is always the address" (docs/protocol.md),
//!   3. show that address in a webview window.
//!
//! **Closing the window does not stop the daemon.** The daemon holds live shells; a window is a
//! view of them, exactly as a browser tab was. That is the same rule as ⑦=b — there is no handoff,
//! and what survives a closed window is the workspace, not the processes' owner.
//!
//! The address carries the key (#14). It is passed to the webview and is never logged.

use std::io::{BufRead, BufReader, Read};
use std::net::{Shutdown, SocketAddr, TcpStream};
#[cfg(target_os = "linux")]
use std::path::Path;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

use tao::{
    event::{Event, WindowEvent},
    event_loop::{ControlFlow, EventLoop},
    window::WindowBuilder,
};
use wry::WebViewBuilder;

/// How long to wait for a daemon we started to print its address. A cold start is well under a
/// second; this is the "something is wrong" deadline, not the expected wait.
const START_TIMEOUT: Duration = Duration::from_secs(20);

fn url_file() -> Option<PathBuf> {
    std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".palmar/run/url"))
}

/// `http://127.0.0.1:8801/?k=…` -> `127.0.0.1:8801`. Deliberately not a URL parser: the daemon
/// writes this line itself and it is always this shape.
fn addr_of(url: &str) -> Option<SocketAddr> {
    let rest = url.strip_prefix("http://")?;
    let host_port = rest.split('/').next()?;
    host_port.parse().ok()
}

/// Is anything listening there? A stale `run/url` outlives the daemon that wrote it — a kill -9
/// leaves the file behind — so the file alone is not evidence.
fn answers(url: &str) -> bool {
    match addr_of(url) {
        Some(a) => match TcpStream::connect_timeout(&a, Duration::from_millis(400)) {
            Ok(s) => {
                let _ = s.shutdown(Shutdown::Both);
                true
            }
            Err(_) => false,
        },
        None => false,
    }
}

/// The palmar checkout this binary was built inside, if it is still there.
///
/// **`python3 -m palmar` only finds the package if it is run from the checkout**, and the obvious
/// place to run this binary from is `app/`, where it is not. That cost a real user twenty seconds
/// of nothing followed by "no address" (2026-09-11). Rather than count directories up from
/// `<checkout>/app/target/release/palmar-app`, walk up looking for the package itself — then a
/// binary that has been moved somewhere else simply finds nothing, instead of finding the wrong
/// thing.
fn checkout_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    for dir in exe.ancestors() {
        if dir.join("palmar").join("__init__.py").is_file() {
            return Some(dir.to_path_buf());
        }
    }
    None
}

/// One way of starting the daemon: what to run, and where from.
struct Attempt {
    program: String,
    args: Vec<String>,
    cwd: Option<PathBuf>,
}

/// Read a child's whole stderr on a thread, into somewhere the parent can look afterwards.
///
/// **This used to go to /dev/null.** When the daemon refused to start, the reason it printed —
/// "No module named palmar", "Python 3.9 or newer is required", a port already taken — was thrown
/// away, and all that came out here was that no address had arrived. The reason is the whole
/// message; without it this program can only say that something did not happen.
fn collect(stream: impl Read + Send + 'static) -> Arc<Mutex<String>> {
    let buf = Arc::new(Mutex::new(String::new()));
    let handle = Arc::clone(&buf);
    std::thread::spawn(move || {
        let mut text = String::new();
        let mut stream = stream;
        let _ = stream.read_to_string(&mut text);
        if let Ok(mut slot) = handle.lock() {
            *slot = text;
        }
    });
    buf
}

/// The last few lines of whatever the child complained about, trimmed for one terminal line each.
fn tail(text: &str, lines: usize) -> String {
    let kept: Vec<&str> = text
        .lines()
        .map(str::trim_end)
        .filter(|l| !l.is_empty())
        .collect();
    let start = kept.len().saturating_sub(lines);
    kept[start..].join("; ")
}

/// What the stdout reader found.
enum Said {
    /// The address, off the last line of stdout (docs/protocol.md).
    Url(String),
    /// stdout ended without one — the daemon exited, or refused to start.
    Nothing,
}

/// The daemon, started the way the installed `palmar` command would. `--no-browser` because the
/// window *is* the browser here — without it the daemon would also open a tab.
fn spawn_daemon() -> Result<String, String> {
    // `palmar` first: that is what install.sh puts on PATH, and it works from any directory.
    let mut attempts = vec![Attempt {
        program: "palmar".into(),
        args: vec!["--no-browser".into()],
        cwd: None,
    }];
    // `python3 -m palmar` second, and **only with the checkout as its working directory**.
    match checkout_dir() {
        Some(dir) => attempts.push(Attempt {
            program: "python3".into(),
            args: vec!["-m".into(), "palmar".into(), "--no-browser".into()],
            cwd: Some(dir),
        }),
        None => {
            // Saying this now is better than a "no module named palmar" from a child later.
            return Err(String::from(
                "`palmar` is not on PATH, and this binary is not inside a palmar checkout,\n  so there is no python package to fall back to",
            ));
        }
    }

    let mut last = String::from("no way to start the daemon was found");
    for a in &attempts {
        let where_ = match &a.cwd {
            Some(d) => format!(" (run in {})", d.display()),
            None => String::new(),
        };
        let mut cmd = Command::new(&a.program);
        cmd.args(&a.args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(dir) = &a.cwd {
            cmd.current_dir(dir);
        }
        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                // Not installed is the ordinary case for `palmar`; keep it short and move on.
                last = format!("{}{}: {}", a.program, where_, e);
                continue;
            }
        };
        let errors = child.stderr.take().map(collect);
        let stdout = match child.stdout.take() {
            Some(s) => s,
            None => {
                last = format!("{}{}: it gave back no stdout", a.program, where_);
                continue;
            }
        };
        // Read on a thread, so a daemon that comes up silently cannot hang the window forever —
        // and so that stdout simply ending is noticed at once rather than at the timeout.
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if line.starts_with("http://") {
                    let _ = tx.send(Said::Url(line));
                    return;
                }
            }
            let _ = tx.send(Said::Nothing);
        });

        let why = match rx.recv_timeout(START_TIMEOUT) {
            Ok(Said::Url(url)) => return Ok(url),
            Ok(Said::Nothing) => "it stopped without printing an address".to_string(),
            Err(_) => format!("no address within {}s", START_TIMEOUT.as_secs()),
        };
        let _ = child.kill();
        let _ = child.wait();
        // Give the stderr reader the moment it needs to finish after the pipe closes.
        std::thread::sleep(Duration::from_millis(120));
        let said = errors
            .and_then(|b| b.lock().ok().map(|s| s.clone()))
            .unwrap_or_default();
        let said = tail(&said, 3);
        last = if said.is_empty() {
            format!("{}{}: {}", a.program, where_, why)
        } else {
            format!("{}{}: {} — it said: {}", a.program, where_, why, said)
        };
    }
    Err(last)
}

/// Is a daemon holding this HOME's lock? `Some(false)` when the lock is free — nobody alive, whatever
/// run/url says and whatever answers on its port. `None` when there is no lock file to ask.
///
/// **The file is not evidence, and neither is a listener.** run/url outlives a daemon that was
/// killed -9 or rebooted away, and "something answers on that port" is what any local account can
/// arrange by binding it — that listener would then be handed the persistent key in the first request
/// (review, 2026-09-15). The daemon holds an exclusive flock on run/lock for exactly as long as it
/// lives (daemon.py acquire_single_instance_lock), and that is the one thing that cannot be faked
/// from another account: the lock file is 0600 in a 0700 directory.
#[cfg(unix)]
fn daemon_alive() -> Option<bool> {
    use std::os::unix::io::AsRawFd;
    let lock = std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".palmar/run/lock"))?;
    // No lock file: no daemon has ever run here, or run/ was cleared — either way nobody holds it,
    // and a run/url without it is not one a daemon of ours is behind.
    let f = match std::fs::OpenOptions::new().read(true).write(true).open(&lock) {
        Ok(f) => f,
        Err(_) => return Some(false),
    };
    // SAFETY: flock on a descriptor we own; LOCK_NB makes it return at once.
    let got = unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0;
    if got {
        unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_UN); }
        Some(false)
    } else {
        Some(true)
    }
}

#[cfg(not(unix))]
fn daemon_alive() -> Option<bool> {
    None
}

/// The address to show: a daemon already up, or one we start.
fn find_or_start() -> Result<String, String> {
    if daemon_alive() != Some(true) {
        // The lock is free, or cannot be asked: whatever run/url says, no daemon of ours is known to
        // be behind it. Start one — it removes the stale file on its way up and writes its own once
        // it is bound, and a daemon that is up answers the start with its own address.
        return spawn_daemon();
    }
    if let Some(path) = url_file() {
        if let Ok(text) = std::fs::read_to_string(&path) {
            let url = text.trim().to_string();
            if !url.is_empty() && answers(&url) {
                return Ok(url);
            }
        }
    }
    spawn_daemon()
}

/// Set a variable only if the person has not already set one. Returns whether it did.
///
/// The rule everything below follows: **never override a choice already made.** Setting the
/// variable yourself, to anything at all including an empty value, wins.
#[cfg(target_os = "linux")]
fn set_if_unset(key: &str, value: &str) -> bool {
    if std::env::var_os(key).is_some() {
        return false;
    }
    std::env::set_var(key, value);
    true
}

/// Defaults that only make sense inside WSL. Must run **before the event loop**, because GTK reads
/// the input-method variable when it builds its first context and Mesa reads the rest at EGL init.
///
/// What each one is for:
///
/// - `WEBKIT_DMABUF_RENDERER_FORCE_SHM` — WSLg has no real DRM device, so WebKit's dmabuf transport
///   has nothing to hand buffers through and the window can come up blank. This keeps the renderer
///   but moves the buffers to shared memory. **It replaces `WEBKIT_DISABLE_DMABUF_RENDERER=1`,
///   which was set here first and was a mistake**: that one turns off hardware acceleration and
///   accelerated compositing wholesale, so every frame reaches the screen by readback and CPU
///   compositing — it made typing lag, and the lag was ours, not WSLg's.
/// - `EGL_LOG_LEVEL` / `MESA_LOG` — Mesa prints four lines on start-up ("failed to get driver name
///   for fd -1", "ZINK: failed to choose pdev", …). They are one EGL initialisation walking its
///   retry ladder — hardware, then Zink, then software — and the software attempt then succeeds
///   silently. Nothing is wrong, so nothing should be printed. **Not `LIBGL_ALWAYS_SOFTWARE`**,
///   which looks like the fix for those lines and actually removes WSLg's own d3d12 driver from
///   the list, trading the GPU away for quiet.
/// - `GTK_IM_MODULE` / `XMODIFIERS` — Korean, Japanese and Chinese cannot be typed under WSLg with
///   the Windows IME at all: WSLg's compositor discards RDP unicode keyboard events, so composed
///   text never crosses into Linux (microsoft/wslg#9, open since 2021). An IME has to run on the
///   Linux side, and GTK only looks for one if it is told to. Set **only when ibus is actually
///   installed** — pointing GTK at a module that is not there is worse than leaving it alone.
///   This belongs here rather than in a shell file because the Start Menu launch path does not
///   read one.
#[cfg(target_os = "linux")]
fn tune_for_wslg() {
    let wsl = std::env::var_os("WSL_DISTRO_NAME").is_some() || Path::new("/mnt/wslg").is_dir();
    if !wsl {
        return;
    }
    set_if_unset("WEBKIT_DMABUF_RENDERER_FORCE_SHM", "1");
    set_if_unset("EGL_LOG_LEVEL", "fatal");
    set_if_unset("MESA_LOG", "null");
    for dir in [
        "/usr/lib/x86_64-linux-gnu/gtk-3.0/3.0.0/immodules",
        "/usr/lib/aarch64-linux-gnu/gtk-3.0/3.0.0/immodules",
        "/usr/lib/gtk-3.0/3.0.0/immodules",
    ] {
        if Path::new(dir).join("im-ibus.so").is_file() {
            set_if_unset("GTK_IM_MODULE", "ibus");
            set_if_unset("XMODIFIERS", "@im=ibus");
            // ibus talks over the session bus, and **WSLg does not start one**. Pointing GTK at
            // ibus with no bus behind it fails silently — you get a window that simply will not
            // take Korean, with nothing said. So say it.
            if std::env::var_os("DBUS_SESSION_BUS_ADDRESS").is_none() {
                eprintln!("palmar-app: ibus is installed but there is no session bus, so it cannot run.");
                eprintln!("  Before starting this, once per WSL session:");
                eprintln!("      eval \"$(dbus-launch --sh-syntax)\" && ibus-daemon -drx && ibus engine hangul");
            }
            break;
        }
    }
}

/// `--no-titlebar`: draw no system title bar and let palmar's own top bar be it.
///
/// Under WSLg the title bar is drawn by Windows and cannot be restyled from here, and it sits on top
/// of a 44px bar palmar draws anyway — two bars' worth of height off the canvas, on a screen that is
/// usually a laptop's. Turning it off gives that back.
///
/// **Opt-in, on purpose.** A window with no title bar cannot be moved unless the page asks for it,
/// and a person who ends up in that state with no way out has been given a worse problem than the
/// one they started with. Leaving the flag off is the way back.
fn wants_bare_window() -> bool {
    std::env::args().any(|a| a == "--no-titlebar")
}

/// **The Dock tile.** This binary is not a `.app` bundle — it is one Mach-O file — so macOS has no
/// `Info.plist` to read an icon out of and shows the generic executable instead ("mac에서는 palmar app
/// 로고가 제대로 적용이 안되어있고", 2026-09-16). tao cannot help: its `set_window_icon` is an empty
/// function here, its own comment being "macOS doesn't have window icons". So the tile goes straight
/// to AppKit, with the same `p` the installed web app uses, compiled in — one file to keep in step.
///
/// This buys the icon and nothing else: the process is still called `palmar-app` in the Dock and
/// still cannot be double-clicked in Finder. Both need a real bundle, which is the rest of #12.
#[cfg(target_os = "macos")]
fn set_dock_icon() {
    use cocoa::base::{id, nil};
    use objc::{class, msg_send, sel, sel_impl};
    const ICON: &[u8] = include_bytes!("../../palmar/web/icon-512.png");
    unsafe {
        let data: id = msg_send![class!(NSData),
            dataWithBytes: ICON.as_ptr() as *const std::ffi::c_void
            length: ICON.len() as u64];
        let img: id = msg_send![class!(NSImage), alloc];
        let img: id = msg_send![img, initWithData: data];
        if img != nil {
            let app: id = msg_send![class!(NSApplication), sharedApplication];
            let _: () = msg_send![app, setApplicationIconImage: img];
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(target_os = "linux")]
    tune_for_wslg();

    let url = match find_or_start() {
        Ok(u) => u,
        Err(e) => {
            // The address is the one thing this program cannot invent. Say which step failed and
            // stop — a window showing an error page would be a worse version of this sentence.
            eprintln!("palmar-app: could not reach a daemon — {e}");
            eprintln!();
            eprintln!("  Start one yourself and run this again:");
            eprintln!("      python3 -m palmar          (from the palmar checkout)");
            eprintln!("  or put the launcher on PATH so it works from anywhere:");
            eprintln!("      sh install.sh");
            std::process::exit(1);
        }
    };

    let event_loop = EventLoop::new();
    let bare = wants_bare_window();
    // Rc because the IPC handler outlives this scope and has to reach the window to move it.
    let window = std::rc::Rc::new(WindowBuilder::new()
        .with_decorations(!bare)
        .with_title("palmar")
        .with_inner_size(tao::dpi::LogicalSize::new(1280.0, 820.0))
        .with_min_inner_size(tao::dpi::LogicalSize::new(640.0, 420.0))
        .build(&event_loop)?);

    // The page is served from 127.0.0.1 by the daemon we just found, so there is nothing to bundle
    // and nothing to keep in sync — the window shows whatever version of the UI that daemon has.
    //
    // **The way in differs by platform, and getting it wrong is silent until it is not.**
    // `WebViewBuilder::new(&window)` takes a raw window handle; on Linux wry then goes down its X11
    // path and accepts nothing but `RawWindowHandle::Xlib`. Under WSLg the session is Wayland, so
    // that is `UnsupportedWindowHandle` and no window ever appears (reported 2026-09-11). The GTK
    // widget is the portable answer there — it works under X11 and Wayland alike.
    // **The page has to be able to reach the window, on every platform.** These two — the flag that
    // tells the page what kind of window it is in, and the channel it answers on — used to be built
    // only on the Linux path, because that is where the missing title bar first needed them. So on a
    // Mac `window.PALMAR_NATIVE` was never defined and there was no `window.ipc` at all: `palmar`
    // typed a second time told the page to come forward, the page fell back to `window.focus()`, and
    // WKWebView ignores that — the window stayed where it was and another one opened (user,
    // 2026-09-16; the cause found 2026-09-17, after a rebuild did not help).
    let init = if bare {
        "window.PALMAR_NATIVE={titlebar:false};"
    } else {
        "window.PALMAR_NATIVE={titlebar:true};"
    };
    // Only the daemon's own page gets to move, close or raise the window: the request carries the
    // sender's URL, and anything else that ends up in this webview (a navigation away, a page on a
    // squatted port) is not it (review, 2026-09-15).
    let ours = addr_of(&url).map(|a| a.to_string());
    let ipc = {
        let w = std::rc::Rc::clone(&window);
        move |req: wry::http::Request<String>| {
            let from = req.uri().authority().map(|a| a.as_str().to_string());
            if from.is_none() || from != ours {
                return;
            }
            match req.body().as_str() {
            // `palmar` typed while this window is open: the daemon told the page, the page tells
            // us. Whatever the title bar — this one is not about the bar.
            "focus" => { w.set_minimized(false); w.set_focus(); }
            // The rest exist only without the system title bar, which is what they replace.
            // Dragging has to be handed to the window manager at the moment the button goes down;
            // there is no way to do it from the page alone.
            "drag" if bare => { let _ = w.drag_window(); }
            "maximize" if bare => w.set_maximized(!w.is_maximized()),
            "minimize" if bare => w.set_minimized(true),
            // Exiting here rather than routing a user event back through the event loop: there is
            // nothing to unwind. The daemon is a separate process and is meant to outlive this one —
            // the same as pressing the title bar's X, which is what this replaces.
            "close" if bare => std::process::exit(0),
            _ => {}
            }
        }
    };

    #[cfg(not(target_os = "linux"))]
    let _webview = WebViewBuilder::new(&window)
        .with_url(&url)
        .with_initialization_script(init)
        .with_ipc_handler(ipc)
        .build()?;

    #[cfg(target_os = "linux")]
    let webview = {
        use tao::platform::unix::WindowExtUnix;
        use wry::WebViewBuilderExtUnix;
        // tao puts a vertical gtk::Box in the window as its only child; wry packs the webview into
        // it with `pack_start(.., true, true, 0)`, so it fills whatever the window is given.
        let vbox = window
            .default_vbox()
            .ok_or("this window has no GTK container to put a webview in")?;
        // The page cannot see whether it has a title bar, and without one it has to provide the
        // things a title bar does. It is told, and given a way to ask.
        WebViewBuilder::new_gtk(vbox)
            .with_url(&url)
            .with_initialization_script(init)
            .with_ipc_handler(ipc)
            .build()?
    };

    // **Put the composing syllable back on screen.** wry turns the IME preedit off for every
    // webview it builds — added so fcitx's editor could anchor at the cursor — and for ibus that
    // means the half-formed syllable never reaches the page at all: ibus sends it to its own panel,
    // and you see nothing until the character is finished. (It is also what made Korean repeat
    // itself: with no preedit there is no compositionstart, and WebKit still fires compositionend
    // for the composition it never started — WebKit bug 84394. That half is handled in the page.)
    //
    // Only for ibus. fcitx is the case wry disabled it for, and this has no business overruling
    // that for someone who is not affected.
    #[cfg(target_os = "linux")]
    {
        let im = std::env::var("GTK_IM_MODULE").unwrap_or_default();
        if im.contains("ibus") {
            // Both traits, by name: this crate has no prelude, and the methods live on the
            // extension traits rather than on the types (checked against webkit2gtk 2.0.1, and by
            // a CI build on Linux — a Mac cannot compile this branch at all).
            use webkit2gtk::{InputMethodContextExt, WebViewExt};
            use wry::WebViewExtUnix;
            if let Some(ctx) = webview.webview().input_method_context() {
                ctx.set_enable_preedit(true);
            }
        }
    }

    #[cfg(target_os = "macos")]
    let mut dock_icon_set = false;

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        // **Once, and only after the loop is running.** Setting it before `run` did nothing: the Dock
        // tile is made when the application is first activated, and anything set earlier is replaced
        // by the default for a binary with no bundle (measured 2026-09-17 — the Dock still read
        // `exec`).
        #[cfg(target_os = "macos")]
        if !dock_icon_set {
            dock_icon_set = true;
            set_dock_icon();
        }
        if let Event::WindowEvent {
            event: WindowEvent::CloseRequested,
            ..
        } = event
        {
            // Only the window goes. The daemon keeps the shells running (see the module note).
            *control_flow = ControlFlow::Exit;
        }
    });
}
