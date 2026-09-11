#!/bin/sh
# Installs what building palmar's window needs on a Debian/Ubuntu machine — WSL included.
#
#     sh app/setup-linux.sh          # says what it will do, then asks
#     sh app/setup-linux.sh -y       # no question
#
# **This one changes your machine**, which is why it is not part of dev/wslg-probe.sh. That probe
# installs nothing and changes nothing, so it stays safe to run and safe to paste; a diagnostic that
# edits the system is a worse diagnostic. Run the probe first, this second.
#
# **Nothing here is needed for the web version.** `python3 -m palmar` is the standard library and a
# vendored xterm.js — no toolchain, no packages. This is only for the window.
#
# It installs two things:
#   - libwebkit2gtk-4.1-dev (+ build-essential, pkg-config, curl) via apt, with sudo
#   - rustup, from https://sh.rustup.rs, into $HOME — only if cargo is not already here
#
# It refuses rather than half-finishes: on a distribution with no WebKitGTK 4.1 it stops and says so.

set -eu

YES=0
if [ "${1:-}" = "-y" ]; then YES=1; fi

say()  { printf '%s\n' "$*"; }
die()  { printf 'setup-linux: %s\n' "$*" >&2; exit 1; }

APT_PKGS="libwebkit2gtk-4.1-dev build-essential pkg-config curl"

# ── it has to be the right kind of machine ─────────────────────────────────────────────────
[ "$(uname -s 2>/dev/null || echo '?')" = "Linux" ] || die "this is for Linux (inside WSL, that means the distro, not Windows)."
command -v apt-get >/dev/null 2>&1 || die "no apt — this script only knows Debian and Ubuntu.
  Install a WebKitGTK 4.1 development package your own way, then: cd app && cargo build --release"

DISTRO="$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-unknown}")"

# ── is 4.1 even obtainable here? ───────────────────────────────────────────────────────────
# Ubuntu 20.04 and Debian 11 have only 4.0, which is the libsoup2 API and will not build this.
# Finding that out now is the difference between one clear sentence and a failed apt run.
have_webkit() { command -v pkg-config >/dev/null 2>&1 && pkg-config --exists webkit2gtk-4.1 2>/dev/null; }
apt_candidate() { apt-cache policy "$1" 2>/dev/null | grep -q 'Candidate: [^(]'; }

NEED_APT=1
if have_webkit; then
  NEED_APT=0
elif ! apt_candidate libwebkit2gtk-4.1-dev; then
  # One refresh, in case the lists are simply old. apt-get update needs sudo too, so ask first.
  say "libwebkit2gtk-4.1-dev is not in the package lists. Refreshing them to be sure…"
  if [ "$YES" = 1 ] || { printf 'run `sudo apt-get update`? [y/N] '; read -r a; [ "$a" = y ] || [ "$a" = Y ]; }; then
    sudo apt-get update
  fi
  apt_candidate libwebkit2gtk-4.1-dev || die "this distribution has no libwebkit2gtk-4.1-dev ($DISTRO).
  Ubuntu 22.04+ and Debian 12+ have it; Ubuntu 20.04 and Debian 11 do not.
  Either move to a newer distro (on Windows: wsl --install -d Ubuntu-24.04),
  or skip the window and use the web version, which needs none of this:  python3 -m palmar"
fi

NEED_RUST=1
if command -v cargo >/dev/null 2>&1 || [ -x "$HOME/.cargo/bin/cargo" ]; then
  NEED_RUST=0
fi

# ── say it before doing it ─────────────────────────────────────────────────────────────────
say ""
say "on $DISTRO, this will:"
if [ "$NEED_APT" = 1 ]; then
  say "  sudo apt-get install -y $APT_PKGS"
else
  say "  (webkit2gtk-4.1 is already here)"
fi
if [ "$NEED_RUST" = 1 ]; then
  say "  curl https://sh.rustup.rs | sh   -> installs rustup into $HOME/.cargo"
else
  say "  (cargo is already here)"
fi
say "  and nothing else. Your palmar checkout is not touched."
say ""

if [ "$NEED_APT" = 0 ] && [ "$NEED_RUST" = 0 ]; then
  say "nothing to do."
else
  if [ "$YES" != 1 ]; then
    printf 'go ahead? [y/N] '
    read -r ans
    case "$ans" in y|Y) ;; *) die "stopped. Nothing was changed." ;; esac
  fi
  if [ "$NEED_APT" = 1 ]; then
    sudo apt-get install -y $APT_PKGS
  fi
  if [ "$NEED_RUST" = 1 ]; then
    # rustup's own -y. Without it the installer is interactive and this would look hung.
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  fi
fi

# ── what is still left for the person to do ────────────────────────────────────────────────
say ""
if ! command -v cargo >/dev/null 2>&1; then
  # rustup writes the PATH into your shell profile, which does not affect the shell you are in.
  say "cargo is installed but not on this shell's PATH yet. Run:"
  say ""
  say '    . "$HOME/.cargo/env"'
  say ""
fi
say "then build and run the window:"
say ""
say "    cd app && cargo build --release && ./target/release/palmar-app"
say ""
say "(check everything first with:  sh dev/wslg-probe.sh )"
