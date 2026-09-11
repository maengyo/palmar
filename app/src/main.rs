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

/// The address to show: a daemon already up, or one we start.
fn find_or_start() -> Result<String, String> {
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

fn main() -> Result<(), Box<dyn std::error::Error>> {
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
    let window = WindowBuilder::new()
        .with_title("palmar")
        .with_inner_size(tao::dpi::LogicalSize::new(1280.0, 820.0))
        .with_min_inner_size(tao::dpi::LogicalSize::new(640.0, 420.0))
        .build(&event_loop)?;

    // The page is served from 127.0.0.1 by the daemon we just found, so there is nothing to bundle
    // and nothing to keep in sync — the window shows whatever version of the UI that daemon has.
    let _webview = WebViewBuilder::new(&window).with_url(&url).build()?;

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
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
