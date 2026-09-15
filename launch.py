"""The launcher's way in: `python3 launch.py …`.

A script path, so sys.path[0] is this directory on every Python — never the caller's cwd. `python -m
palmar` puts the cwd first (PYTHONSAFEPATH turns that off only on 3.11+, and macOS ships 3.9), so
`palmar` typed inside a cloned repository that happens to hold a palmar/ package, or a json.py, ran
that repository's code as you (review, 2026-09-15). install.sh and install.ps1 point their launchers
here; `python3 -m palmar` from the checkout itself is still fine, because there the cwd is the tree.
"""
from palmar.daemon import cli

cli()
