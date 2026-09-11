#!/bin/sh
# Answers, on a real WSLg machine, the questions the Mac palmar is written on cannot (#40).
#
# Run it inside WSL, from the top of a palmar checkout:
#
#     sh dev/wslg-probe.sh
#
# It changes nothing and installs nothing. It prints a report meant to be pasted back — paths and a
# username show up in it (that is what `--doctor` prints), but **no key and no token**: the one line
# that would carry the key is filtered here as well as there.
#
# Read it top to bottom. The first FAIL is the one to fix; everything under it is downstream.

say()  { printf '%s\n' "$*"; }
head2() { say ""; say "── $* ─────────────────────────────────────────"; }
ok()   { say "  ok    $*"; }
bad()  { say "  FAIL  $*"; FAILED=$((FAILED+1)); }
note() { say "        $*"; }
FAILED=0

say "palmar wslg-probe"
say "=================================================="
say "date   $(date '+%Y-%m-%d %H:%M' 2>/dev/null)"

# ── 1. is this WSL at all, and is WSLg there ───────────────────────────────────────────────
head2 "1. WSL and WSLg"
REL=$(cat /proc/sys/kernel/osrelease 2>/dev/null || echo "(unreadable)")
say "  /proc/sys/kernel/osrelease   $REL"
say "  \$WSL_DISTRO_NAME             ${WSL_DISTRO_NAME:-(unset)}"
say "  \$DISPLAY                     ${DISPLAY:-(unset)}"
say "  \$WAYLAND_DISPLAY             ${WAYLAND_DISPLAY:-(unset)}"
if [ -d /mnt/wslg ]; then say "  /mnt/wslg                    present"; else say "  /mnt/wslg                    ABSENT"; fi

case "$(printf '%s' "$REL" | tr 'A-Z' 'a-z')" in
  *microsoft*|*wsl*) IS_WSL=1 ;;
  *) [ -n "$WSL_DISTRO_NAME" ] && IS_WSL=1 || IS_WSL=0 ;;
esac
if [ "$IS_WSL" = 1 ]; then ok "this is WSL"; else bad "this is not WSL — run the probe inside your distro"; fi
if [ -d /mnt/wslg ] && { [ -n "$WAYLAND_DISPLAY" ] || [ -n "$DISPLAY" ]; }; then
  ok "WSLg can draw a window"
else
  bad "no WSLg session (need /mnt/wslg *and* a display)"
  note "on Windows: wsl --update, then wsl --shutdown, then open the distro again"
  note "WSLg needs Windows 11, or Windows 10 21H2+ with WSL installed from the Store"
fi

# ── 2. does a GUI program work at all, before palmar is blamed ─────────────────────────────
head2 "2. can WSLg show *any* window"
if command -v xeyes >/dev/null 2>&1 || command -v xcalc >/dev/null 2>&1; then
  ok "x11-apps is installed — run \`xeyes\` by hand; a window should appear on Windows"
else
  note "not installed. This is the test that separates 'WSLg is broken' from 'palmar is broken':"
  note "    sudo apt install -y x11-apps && xeyes"
  note "If no window appears, stop here — nothing below can work."
fi

# ── 3. the daemon side (pure Python, no build) ─────────────────────────────────────────────
head2 "3. palmar's daemon"
PY=""
for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys;raise SystemExit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -n "$PY" ]; then
  ok "$("$PY" --version 2>&1) at $(command -v "$PY")"
else
  bad "no Python 3.9+ — sudo apt install -y python3"
fi

if [ -n "$PY" ] && [ -f palmar/__init__.py ]; then
  ok "run from a palmar checkout"
  # **Run once, read twice.** --doctor ends with a ten-second watch over the running panes, and
  # calling it a second time just to grep one line pays for that watch again.
  DOC=$(mktemp 2>/dev/null || echo /tmp/palmar-doctor.$$)
  "$PY" -m palmar --doctor > "$DOC" 2>&1
  say ""
  say "  --- python3 -m palmar --doctor (key and token are never in this) ---"
  # Through the `home` line: that is the code block and the machine block, which is all that is
  # being asked here. The pane-by-pane watch below it is about panes, not about WSLg.
  sed -n '1,/^  home /p' "$DOC" | sed 's/^/  /'
  say "  --- end ---"
  WSLLINE=$(grep -E '^  WSL ' "$DOC" | awk '{print $2}')
  rm -f "$DOC"
  case "$WSLLINE" in
    wslg) ok "palmar detects: wslg   <- this is the answer we wanted" ;;
    wsl)  bad "palmar detects: wsl (no window layer). Compare with section 1." ;;
    no)   bad "palmar detects: no — the WSL detection is wrong. Paste section 1 back." ;;
    *)    bad "no WSL line in --doctor (old code? run git pull)" ;;
  esac
else
  bad "not in a palmar checkout — cd to it first (palmar/__init__.py must be here)"
fi

# ── 4. what the window needs to build ──────────────────────────────────────────────────────
head2 "4. the window (app/)"
DISTRO="(unknown)"
if [ -r /etc/os-release ]; then
  DISTRO=$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-$NAME $VERSION_ID}")
fi
say "  distribution                 $DISTRO"

if command -v cargo >/dev/null 2>&1; then
  ok "$(cargo --version 2>&1)"
elif [ -x "$HOME/.cargo/bin/cargo" ]; then
  # The most common stumble: rustup installed it but this shell's PATH predates that.
  bad "rustup is installed but cargo is not on PATH in this shell"
  note 'run:  . "$HOME/.cargo/env"      (or just open a new shell) and try again'
else
  bad "no Rust"
  note "sh app/setup-linux.sh    <- installs this and the webview headers, asking first"
  note "or by hand: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"
  note 'then:  . "$HOME/.cargo/env"     <- rustup does not change this shell by itself'
fi

# **Is it installed, and failing that, can this machine even get it?** Those are different
# answers: 4.1 does not exist on Ubuntu 20.04 or Debian 11 at all, and "apt install" there fails
# with a message about no installation candidate, which looks like a broken command.
apt_has() {
  command -v apt-cache >/dev/null 2>&1 || return 1
  apt-cache policy "$1" 2>/dev/null | grep -q 'Candidate: [^(]' 
}
if command -v pkg-config >/dev/null 2>&1; then
  # 4.1, not 4.0: this wry links webkit2gtk 2.0 / soup3, and 4.0 is the libsoup2 API.
  if pkg-config --exists webkit2gtk-4.1 2>/dev/null; then
    ok "webkit2gtk-4.1 $(pkg-config --modversion webkit2gtk-4.1 2>/dev/null)"
  else
    if pkg-config --exists webkit2gtk-4.0 2>/dev/null; then
      bad "webkit2gtk-4.0 is here but this build needs 4.1"
    else
      bad "no webkit2gtk dev package"
    fi
    if apt_has libwebkit2gtk-4.1-dev; then
      note "it is available here. Either by hand:"
      note "    sudo apt install -y libwebkit2gtk-4.1-dev build-essential pkg-config curl"
      note "or let a script do this one and Rust together (it asks first, and only that script"
      note "changes anything — this probe never does):"
      note "    sh app/setup-linux.sh"
    elif command -v apt-cache >/dev/null 2>&1; then
      note "**this distribution has no libwebkit2gtk-4.1-dev at all** ($DISTRO)."
      note "Ubuntu 22.04+ and Debian 12+ have it; 20.04 and Debian 11 do not."
      note "Either move to a newer distro (wsl --install -d Ubuntu-24.04 on Windows),"
      note "or skip the window and use the web version: python3 -m palmar"
    else
      note "not a Debian/Ubuntu apt system — install the WebKitGTK 4.1 development package"
    fi
  fi
else
  bad "no pkg-config"
  note "sudo apt install -y pkg-config build-essential"
fi

# ── 5. what the window needs beyond starting ───────────────────────────────────────────────
# Three things that all look like "palmar is broken" from the outside and are none of them palmar:
# no Hangul font, no input method, and no working GL.
head2 "5. Korean, and speed"

# **Fonts.** The terminal font is vendored (JetBrains Mono) and has no Hangul; the page falls back
# per glyph to whatever the system has. With no CJK font anywhere, Korean is tofu.
if command -v fc-list >/dev/null 2>&1; then
  HANGUL=$(fc-list :lang=ko 2>/dev/null | wc -l | tr -d ' ')
  if [ "$HANGUL" -gt 0 ]; then
    ok "$HANGUL font(s) with Korean glyphs"
  else
    bad "no font on this machine has Hangul — Korean will be empty boxes"
    note "sudo apt install -y fonts-noto-cjk"
  fi
else
  note "fontconfig is not installed, so this cannot be checked here:"
  note "    sudo apt install -y fontconfig   (then run this again)"
  note "If Korean shows as boxes, the fix is: sudo apt install -y fonts-noto-cjk"
fi

# **Input method.** Showing Hangul and typing it are different problems with different fixes.
say "  \$GTK_IM_MODULE               ${GTK_IM_MODULE:-(unset)}"
say "  \$XMODIFIERS                  ${XMODIFIERS:-(unset)}"
if command -v ibus >/dev/null 2>&1; then
  ok "ibus is installed"
elif command -v fcitx5 >/dev/null 2>&1; then
  ok "fcitx5 is installed"
else
  note "no input method installed. That is only a problem if you cannot TYPE Korean —"
  note "showing it is the font above, and the two fail separately."
fi

# **GL.** The terminal draws through WebGL when it can and falls back to a DOM renderer when it
# cannot, and that fallback is the slow one. Under WSLg this is where the Mesa noise comes from.
if [ -d /usr/lib/wsl/lib ]; then
  ok "/usr/lib/wsl/lib present (WSLg's own GPU libraries)"
else
  note "/usr/lib/wsl/lib is absent — there is no WSL GPU stack to use"
fi
if command -v glxinfo >/dev/null 2>&1; then
  REND=$(glxinfo -B 2>/dev/null | grep -i "OpenGL renderer" | cut -d: -f2- | sed 's/^ *//')
  if [ -n "$REND" ]; then ok "OpenGL renderer: $REND"; else bad "glxinfo could not get a GL context"; fi
else
  note "mesa-utils is not installed, so GL cannot be checked here:"
  note "    sudo apt install -y mesa-utils   (then: glxinfo -B)"
fi
note "In the window, the status bar (bottom right) says 'webgl N · dom N'."
note "**dom means the slow renderer** — that is what laggy typing looks like."

head2 "result"
if [ "$FAILED" -eq 0 ]; then
  say "  nothing failed. Build and run it:"
  say "      cd app && cargo build --release && ./target/release/palmar-app"
  say "  A window should appear on the Windows desktop, with palmar in it."
  say ""
  say "  If the window appears but is blank or black, that is WSLg's virtual GPU meeting"
  say "  WebKitGTK compositing — not palmar. Try, in order, and say which one works:"
  say "      WEBKIT_DISABLE_COMPOSITING_MODE=1 ./target/release/palmar-app"
  say "      WEBKIT_DISABLE_DMABUF_RENDERER=1 ./target/release/palmar-app"
else
  say "  $FAILED check(s) failed — fix the first one above, then run this again."
fi
say ""
