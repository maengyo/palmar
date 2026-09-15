#!/bin/sh
# palmar installer — one line, no build, no dependency to fetch.
#
#   curl -fsSL <url>/install.sh | sh          # the quick way
#   curl -fsSL <url>/install.sh -o install.sh # the careful way: read it first, then
#   sh install.sh
#
# **This does almost nothing on purpose.** palmar is the Python standard library and a vendored
# copy of xterm.js — there is no binary to download and nothing to compile. So this script only:
#   1. checks a Python new enough is already here (macOS ships /usr/bin/python3 3.9; most Linux has one)
#   2. puts palmar where `palmar` on the command line will find it
#   3. tells you the one command to run
#
# It never runs the daemon, never touches ~/.palmar, and never needs root. If any of it is not to
# your taste, `git clone` the repo and `python3 -m palmar` does the same thing with nothing installed.

set -eu

# ── where palmar comes from ────────────────────────────────────────────────────────────────
# Run from inside a clone, this installs the checkout you are in. Run any other way — the one-line
# `curl … | sh` — it downloads the tree from GitHub and keeps it under $PREFIX/share/palmar, which is
# what the launcher then runs. PALMAR_TARBALL overrides the source: a URL, or a local .tar.gz (tests).
PALMAR_PYPI="${PALMAR_PYPI:-}"          # "palmar" once it is on PyPI — then pip is used instead
PALMAR_TARBALL="${PALMAR_TARBALL:-https://codeload.github.com/maengyo/palmar/tar.gz/refs/heads/main}"
PREFIX="${PALMAR_PREFIX:-$HOME/.local}"

say()  { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }
die()  { printf 'install: %s\n' "$*" >&2; exit 1; }

# ── 1. a Python new enough ─────────────────────────────────────────────────────────────────
# 3.9 is the floor because that is what macOS ships (spike H). Find the first python that clears it.
find_python() {
  for py in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
    if command -v "$py" >/dev/null 2>&1 && \
       "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
      command -v "$py"
      return 0
    fi
  done
  return 1
}

# ── 1b. no Python: get one ────────────────────────────────────────────────────────────────
# A machine with no python3 used to end here with "install one" (asked for 2026-09-15). The package
# manager is found and the exact command shown; it runs only on a yes — from the terminal when there
# is one (stdin is the pipe under `curl | sh`, so the question goes to /dev/tty and the answer comes
# back from it), or from PALMAR_YES=1 when there is not. sudo asks for its password on the tty too.
install_python() {
  pm=""
  for c in apt-get dnf yum zypper pacman apk; do
    if command -v "$c" >/dev/null 2>&1; then pm="$c"; break; fi
  done
  case "$pm" in
    apt-get) cmd="apt-get update && apt-get install -y python3" ;;
    dnf)     cmd="dnf install -y python3" ;;
    yum)     cmd="yum install -y python3" ;;
    zypper)  cmd="zypper --non-interactive install python3" ;;
    pacman)  cmd="pacman -S --noconfirm python" ;;
    apk)     cmd="apk add python3" ;;
    "")
      if [ "$(uname -s 2>/dev/null)" = Darwin ]; then
        die "no Python 3.9 or newer found. macOS has one at /usr/bin/python3 once the command line
  tools are in: run \`xcode-select --install\`, then this again."
      fi
      die "no Python 3.9 or newer found, and no package manager I know (apt-get, dnf, yum, zypper,
  pacman, apk). Install python3 your way, then run this again." ;;
  esac
  if [ "$(id -u)" != 0 ]; then
    case "$pm" in
      apt-get) cmd="sudo apt-get update && sudo apt-get install -y python3" ;;
      *)       cmd="sudo $cmd" ;;
    esac
  fi
  if [ -z "${PALMAR_YES:-}" ]; then
    if [ -t 2 ] && [ -r /dev/tty ]; then
      printf 'no Python 3.9 or newer here. Install one now with\n    %s\n? [Y/n] ' "$cmd" >&2
      read -r ans </dev/tty || ans=n
      case "$ans" in n*|N*) die "not installed. Run that yourself, then this again." ;; esac
    else
      die "no Python 3.9 or newer found. This would install one with
    $cmd
  Run that yourself and then this again — or run this with PALMAR_YES=1 to let it."
    fi
  fi
  say "installing python3: $cmd"
  sh -c "$cmd" || die "that did not go through. Install python3 your way, then run this again."
  PY="$(find_python || true)"
  [ -n "$PY" ] || die "python3 was installed, but no Python 3.9 or newer answers yet. Open a new shell and run this again."
}

PY="$(find_python || true)"
[ -n "$PY" ] || install_python
say "using $("$PY" --version 2>&1) at $PY"

# ── 2. get palmar onto disk ────────────────────────────────────────────────────────────────
# Three sources, in the order that needs the least from the machine. Only the first works today;
# the other two switch on the moment the repo is public or on PyPI.
SRC=""
# Only a script that is a real file has a checkout around it. Under `curl … | sh` $0 is `sh`, and
# `dirname sh` is `.` — so the one-liner run inside a repository holding a palmar/ package used to
# install *that* (review, 2026-09-15).
if [ -f "$0" ] && [ -f "$(unset CDPATH; cd -- "$(dirname -- "$0")" && pwd)/palmar/__init__.py" ]; then
  # Run from inside a checkout — install exactly this tree, no download.
  SRC="$(unset CDPATH; cd -- "$(dirname -- "$0")" && pwd)"
  say "installing from the checkout at $SRC"
elif [ -n "$PALMAR_PYPI" ]; then
  say "installing $PALMAR_PYPI from PyPI"
  "$PY" -m pip install --user --upgrade "$PALMAR_PYPI" || die "pip install failed"
  INSTALLED_VIA_PIP=1
else
  # **The tree is kept, not the temp dir.** An earlier draft extracted into mktemp and deleted it on
  # exit — the launcher then pointed at nothing. It lives under the prefix, and a reinstall replaces it.
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  if [ -f "$PALMAR_TARBALL" ]; then
    cp "$PALMAR_TARBALL" "$TMP/palmar.tar.gz"
  else
    say "downloading palmar"
    command -v curl >/dev/null 2>&1 || die "curl is needed to download palmar (or git clone it and run install.sh from the clone)"
    curl -fsSL "$PALMAR_TARBALL" -o "$TMP/palmar.tar.gz" || die "download failed — is the repository public, and is the network up?"
  fi
  mkdir -p "$TMP/x" && tar -xzf "$TMP/palmar.tar.gz" -C "$TMP/x" || die "could not unpack the archive"
  GOT="$(find "$TMP/x" -maxdepth 3 -name '__init__.py' -path '*/palmar/*' | head -1)"
  [ -n "$GOT" ] || die "the archive did not contain palmar/"
  GOT="$(dirname "$(dirname "$GOT")")"
  SRC="$PREFIX/share/palmar"
  rm -rf "$SRC" && mkdir -p "$(dirname "$SRC")" && mv "$GOT" "$SRC" || die "could not place palmar under $SRC"
  say "installed the tree at $SRC"
fi

# ── 3. make `palmar` runnable ──────────────────────────────────────────────────────────────
if [ "${INSTALLED_VIA_PIP:-}" = "1" ]; then
  BIN="$("$PY" -c 'import sysconfig,os;print(os.path.join(sysconfig.get_path("scripts",scheme="posix_user"),"palmar"))' 2>/dev/null || true)"
  say ""
  say "installed. run:  palmar"
  [ -x "$BIN" ] || warn "note: $BIN may not be on your PATH — add its directory if 'palmar' is not found"
else
  # No pip: drop a tiny shim that runs the tree in place. Nothing is copied, so a 'git pull' in the
  # checkout updates palmar with no reinstall — which is how the daemon already expects to live (⑦=b).
  mkdir -p "$PREFIX/bin"
  # **The directory that goes first on PATH has to be ours and ours alone.** A group- or
  # world-writable bin, or one owned by someone else (a shared prefix, /tmp), lets any other account
  # replace the launcher — and every command, since it is first on PATH. The daemon refuses the same
  # for ~/.palmar (#29); the installer did not for this (review, 2026-09-15).
  for d in "$PREFIX/bin" "$SRC"; do
    "$PY" - "$d" <<'OWN_EOF' || die "$d must be owned by you and writable by nobody else (chmod go-w, or pick another PALMAR_PREFIX)"
import os, sys
st = os.stat(sys.argv[1])
raise SystemExit(0 if st.st_uid == os.getuid() and not (st.st_mode & 0o022) else 1)
OWN_EOF
  done
  SHIM="$PREFIX/bin/palmar"
  # A launcher, not a copy: it runs the tree in place, so a `git pull` in the checkout updates palmar
  # with no reinstall — the daemon already expects to live that way (⑦=b). By **script path**, not
  # `python -m`: `-m` puts the caller's cwd first on sys.path, and PYTHONSAFEPATH only stops that on
  # 3.11+ — macOS ships 3.9, so `palmar` typed inside a cloned repository holding a palmar/ package
  # ran that repository (review, 2026-09-15). A script path makes sys.path[0] the tree, everywhere.
  rm -f "$SHIM"                      # never write through something somebody planted there
  cat > "$SHIM" <<SHIM_EOF
#!/bin/sh
# Generated by palmar's install.sh. Runs the tree at the path below with the Python picked then.
exec env PYTHONSAFEPATH=1 "$PY" "$SRC/launch.py" "\$@"
SHIM_EOF
  chmod +x "$SHIM"
  say ""
  say "installed a launcher at $SHIM"
  # ── 3b. the window, when there is one ───────────────────────────────────────────────────
  # `palmar` opens its own window before a browser (2026-09-15), so a built one goes beside the
  # launcher. Three sources, none required: PALMAR_APP_URL (a URL, or a local file), a build inside
  # the checkout (linked, so a rebuild is picked up), or the latest release's asset for this OS and
  # CPU. Nothing here needs Rust — the binary runs with no toolchain (app/README.md). No window is
  # not a failure: `palmar` opens a browser instead.
  APP_DEST="$PREFIX/bin/palmar-app"
  APP_FROM=""
  if [ -n "${PALMAR_APP_URL:-}" ]; then
    if [ -f "$PALMAR_APP_URL" ]; then
      cp "$PALMAR_APP_URL" "$APP_DEST" && chmod +x "$APP_DEST" && APP_FROM="$PALMAR_APP_URL"
    elif curl -fsSL "$PALMAR_APP_URL" -o "$APP_DEST.new"; then
      mv "$APP_DEST.new" "$APP_DEST" && chmod +x "$APP_DEST" && APP_FROM="$PALMAR_APP_URL"
    else
      rm -f "$APP_DEST.new"; warn "could not get the window from $PALMAR_APP_URL — palmar opens a browser instead"
    fi
  elif [ -x "$SRC/app/target/universal/palmar-app" ]; then
    ln -sf "$SRC/app/target/universal/palmar-app" "$APP_DEST" && APP_FROM="the checkout's build"
  elif [ -x "$SRC/app/target/release/palmar-app" ]; then
    ln -sf "$SRC/app/target/release/palmar-app" "$APP_DEST" && APP_FROM="the checkout's build"
  elif command -v curl >/dev/null 2>&1; then
    case "$(uname -s 2>/dev/null)/$(uname -m 2>/dev/null)" in
      Darwin/*)                  ASSET="palmar-app-macos-universal" ;;
      Linux/x86_64)              ASSET="palmar-app-linux-x86_64" ;;
      Linux/aarch64|Linux/arm64) ASSET="palmar-app-linux-aarch64" ;;
      *)                         ASSET="" ;;
    esac
    REL="https://github.com/maengyo/palmar/releases/latest/download"
    if [ -n "$ASSET" ] && curl -fsSL "$REL/$ASSET" -o "$APP_DEST.new" 2>/dev/null; then
      # The release carries SHA256SUMS; the binary has to match it before it is made executable.
      # Same origin as the binary, so this catches a torn download and a swapped asset, not a
      # compromised GitHub — that is the same trust this script itself arrived on.
      if curl -fsSL "$REL/SHA256SUMS" -o "$APP_DEST.sums" 2>/dev/null; then
        WANT="$(grep " $ASSET\$" "$APP_DEST.sums" | cut -d' ' -f1)"
        GOT="$("$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$APP_DEST.new")"
        rm -f "$APP_DEST.sums"
        if [ -z "$WANT" ] || [ "$WANT" != "$GOT" ]; then
          rm -f "$APP_DEST.new"; warn "the window's download did not match the release's SHA256SUMS — not installed; \`palmar\` opens a browser"
        fi
      fi
      if [ -f "$APP_DEST.new" ]; then
        mv "$APP_DEST.new" "$APP_DEST" && chmod +x "$APP_DEST" && APP_FROM="the latest release"
      fi
    else
      rm -f "$APP_DEST.new"
    fi
  fi
  if [ -n "$APP_FROM" ]; then
    say "installed the window at $APP_DEST (from $APP_FROM) — \`palmar\` opens it; its web button opens a browser"
  else
    say "no window build for this machine — \`palmar\` opens a browser (app/README.md says how to get one)"
  fi
  case ":$PATH:" in
    *":$PREFIX/bin:"*) say "run:  palmar" ;;
    *)
      # **Put it on the PATH, once, in the file this shell reads.** A launcher nobody can call is not
      # installed. One marked line, added only if it is not there — so running this twice adds nothing
      # twice. The running shell cannot be changed from here; the next one has it.
      RC=""
      case "$(basename -- "${SHELL:-sh}")" in
        zsh)  RC="$HOME/.zshrc" ;;
        bash) RC="$HOME/.bashrc"; [ "$(uname -s)" = "Darwin" ] && RC="$HOME/.bash_profile" ;;
        fish) RC="" ;;
        *)    RC="$HOME/.profile" ;;
      esac
      LINE="export PATH=\"$PREFIX/bin:\$PATH\"  # palmar"
      if [ -n "$RC" ] && ! grep -qs '# palmar$' "$RC"; then
        printf '\n%s\n' "$LINE" >> "$RC" && say "added $PREFIX/bin to your PATH in $RC"
      elif [ -n "$RC" ]; then
        say "$RC already puts $PREFIX/bin on your PATH"
      else
        say "add $PREFIX/bin to your PATH (fish: fish_add_path $PREFIX/bin)"
      fi
      say "open a new terminal (or run: exec \$SHELL), then:  palmar"
      say "or run it directly now:  $SHIM" ;;
  esac
fi

say ""
say "palmar prints a URL on its last line — open that in a browser. Ctrl-C stops it."
