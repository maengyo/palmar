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

use std::io::{BufRead, BufReader};
use std::net::{Shutdown, SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::mpsc;
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

/// The daemon, started the way the installed `palmar` command would. `--no-browser` because the
/// window *is* the browser here — without it the daemon would also open a tab.
fn spawn_daemon() -> Result<String, String> {
    // `palmar` first: that is what install.sh puts on PATH. `python3 -m palmar` is the fallback for
    // running straight out of a checkout, and it only works if the checkout is the working directory.
    let attempts: [(&str, &[&str]); 2] = [
        ("palmar", &["--no-browser"]),
        ("python3", &["-m", "palmar", "--no-browser"]),
    ];
    let mut last = String::from("no way to start the daemon was found");
    for (program, args) in attempts {
        let child = Command::new(program)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn();
        let mut child = match child {
            Ok(c) => c,
            Err(e) => {
                last = format!("{program}: {e}");
                continue;
            }
        };
        let stdout = match child.stdout.take() {
            Some(s) => s,
            None => {
                last = format!("{program}: it gave back no stdout");
                continue;
            }
        };
        // Read on a thread so a daemon that comes up silently cannot hang the window forever.
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if line.starts_with("http://") {
                    let _ = tx.send(line);
                    return;
                }
            }
        });
        match rx.recv_timeout(START_TIMEOUT) {
            Ok(url) => return Ok(url),
            Err(_) => {
                // It never said an address. Leave nothing running behind us.
                let _ = child.kill();
                last = format!("{program}: no address within {}s", START_TIMEOUT.as_secs());
            }
        }
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
            eprintln!("  try `palmar` in a terminal first, then start this again.");
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
