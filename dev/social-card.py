#!/usr/bin/env python3
"""The picture GitHub shows when somebody pastes a link to the repository.

There was none, so a shared link came back as GitHub's default card — the account's avatar and the
repository's name — and palmar's own mark was nowhere in it (user, 2026-09-21). The image itself is
`dev/social-card.html`, and this takes the shot: open it in Chrome, 1280x640, which is the size
GitHub asks for.

    python3 dev/social-card.py                  # writes docs/img/social.png
    python3 dev/social-card.py --out /tmp/x.png # somewhere else, to look before replacing anything

**Committing the image is not the same as setting it.** GitHub has no API for the social preview —
it is an upload in Settings -> General -> Social preview. The file in the repository is what gets
uploaded, and what lets the next person take the same shot again rather than guess at the one that
is already up there.

The card takes its colours and its mark from the app rather than keeping copies: the icon is
`palmar/web/icon-512.png`, the same file the web app and the Dock tile use, so there is one image
to keep in step.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

CARD = os.path.join(REPO, "dev", "social-card.html")
OUT = os.path.join(REPO, "docs", "img", "social.png")
W, H = 1280, 640


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    # The same Chrome the browser tests drive, for the same reason: it is already here and it is
    # already the thing that renders palmar.
    from tests.cdp import Browser, chrome_path

    if chrome_path() is None:
        print("no Chrome on this machine — that is what draws the card", file=sys.stderr)
        return 1
    if not os.path.exists(CARD):
        print("the card is missing: " + CARD, file=sys.stderr)
        return 1

    b = Browser().start()
    try:
        b.open("file://" + CARD)
        # The mark is a file next to the page; give it a moment to be decoded before the shot.
        time.sleep(1.5)
        shot = b.ws.call("Page.captureScreenshot", {
            "clip": {"x": 0, "y": 0, "width": W, "height": H, "scale": 1},
            "captureBeyondViewport": True,
        })
    finally:
        b.stop()

    data = base64.b64decode(shot["data"])
    with open(args.out, "wb") as fh:
        fh.write(data)
    print("%s — %dx%d, %.1f KB" % (args.out, W, H, len(data) / 1024.0))
    print("Settings -> General -> Social preview -> Upload an image, and pick that file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
