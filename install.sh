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
if [ -f "$(unset CDPATH; cd -- "$(dirname -- "$0")" && pwd)/palmar/__init__.py" ]; then
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
  SHIM="$PREFIX/bin/palmar"
  # A launcher, not a copy. It points PYTHONPATH at the tree in place, so a `git pull` in the
  # checkout updates palmar with no reinstall — the daemon already expects to live that way (⑦=b).
  cat > "$SHIM" <<SHIM_EOF
#!/bin/sh
# Generated by palmar's install.sh. Runs the tree at the path below with the Python picked then.
# PYTHONSAFEPATH: \`python -m\` puts the current directory first on sys.path, so \`palmar\` typed inside
# any other checkout ran the palmar of *that* checkout — an old one said \`unrecognized arguments: --stop\`
# (2026-09-15). Python 3.11+ honours it; older ones ignore it and keep the trap.
exec env PYTHONSAFEPATH=1 PYTHONPATH="$SRC\${PYTHONPATH:+:\$PYTHONPATH}" "$PY" -m palmar "\$@"
SHIM_EOF
  chmod +x "$SHIM"
  say ""
  say "installed a launcher at $SHIM"
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
