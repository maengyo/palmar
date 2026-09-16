"""The app icons: docs/brand/p-square.svg on a rounded dark square, as PNG at 192 and 512.

    python3 docs/brand/icons.py          # writes palmar/web/icon-192.png and icon-512.png

Where they go: the web app manifest (Edge/Chrome install palmar as an app from it — Start Menu,
Dock, taskbar) and the page's favicon before the status badge takes over. Rendered by the same
headless Chrome the browser tests drive (tests/cdp.py), because nothing else here rasterises SVG and
the marks are strokes with round caps, which a hand-written rasteriser would get wrong. The glyph
is the brand's `p` (currentColor → paper), the dot the brand's amber: "one is waiting for you".
Colours match the manifest's theme_color / background_color in palmar/daemon.py.
"""
import base64
import os
import sys
import urllib.parse

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
from tests.cdp import Browser  # noqa: E402

INK, PAPER, AMBER = "#1f2329", "#f6f3ec", "#d99a2b"


def svg(size: int) -> str:
    # The viewBox is p-square.svg's; the rounded square fills it, the p sits where the brand puts it.
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-26 35 116 116" width="{size}" height="{size}">'
            f'<rect x="-26" y="35" width="116" height="116" rx="26" fill="{INK}"/>'
            f'<g fill="none" stroke="{PAPER}" stroke-width="11" stroke-linecap="round" stroke-linejoin="round">'
            '<path d="M 13.50 57.50 V 128.50"/>'
            '<path d="M 13.50 76.00 a 18.50 18.50 0 1 0 37.00 0 a 18.50 18.50 0 1 0 -37.00 0"/></g>'
            f'<circle cx="33" cy="76" r="6" fill="{AMBER}"/></svg>')


def main() -> None:
    b = Browser(width=600, height=600).start()
    try:
        for size in (192, 512):
            page = f'<!doctype html><meta charset="utf-8"><style>html,body{{margin:0;background:transparent}}</style>{svg(size)}'
            b.open("data:text/html;charset=utf-8," + urllib.parse.quote(page), settle=0.6)
            b.ws.call("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
            r = b.ws.call("Page.captureScreenshot", {"format": "png",
                                                    "clip": {"x": 0, "y": 0, "width": size, "height": size, "scale": 1}})
            out = os.path.join(REPO, "palmar", "web", f"icon-{size}.png")
            with open(out, "wb") as fh:
                fh.write(base64.b64decode(r["data"]))
            print(out, os.path.getsize(out), "bytes")
    finally:
        b.stop()


if __name__ == "__main__":
    main()
