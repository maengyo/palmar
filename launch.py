"""The launcher's way in: `python3 launch.py …`.

A script path, so sys.path[0] is this directory on every Python — never the caller's cwd. `python -m
palmar` puts the cwd first (PYTHONSAFEPATH turns that off only on 3.11+, and macOS ships 3.9), so
`palmar` typed inside a cloned repository that happens to hold a palmar/ package, or a json.py, ran
that repository's code as you (review, 2026-09-15). install.sh and install.ps1 point their launchers
here; `python3 -m palmar` from the checkout itself is still fine, because there the cwd is the tree.
"""
import os
import sys

# Said outright, not left to the interpreter: PYTHONSAFEPATH (which the launchers set) drops the
# implicit sys.path[0] — the script's own directory included — on 3.11+, and the tree was not found
# on a Windows with 3.13 (user, 2026-09-16). This directory is the tree; nothing else goes in.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from palmar.daemon import cli  # noqa: E402 - after the path is set

cli()
