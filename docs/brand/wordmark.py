#!/usr/bin/env python3
"""palmer 워드마크를 격자에서 계산해 SVG 로 뽑는다.

    python3 docs/brand/wordmark.py      # 저장소 뿌리에서 실행. docs/brand/*.svg 를 다시 쓴다.

손으로 찍은 좌표는 없다. 아래 상수(획 두께·기준선·반지름·곁여백)만이 정본이고,
글자는 전부 여기서 나온다. 두께를 바꾸고 싶으면 W 를 고치고 다시 돌린다.
색은 상태 점 #d99a2b 하나뿐이고, 잉크는 currentColor 라 밝은 바탕·어두운 바탕을 같이 탄다.

판은 두 벌이다.
  round  — 둥근 판. 둥근 글자가 전부 같은 원(r 18.5)으로 돈다.
  window — 창 판. 같은 자리에 원 대신 둥근 네모(창)가 들어간다. 캔버스를 글자에 녹인 것.
"""
import math, pathlib

ADV, W = 60.0, 11.0
H     = W/2
BASE  = 94.5     # 베이스라인(중심선)
XTOP  = 57.5     # x-높이 위
ASC   = 23.5     # 어센더 위
DESC  = 128.5    # 디센더 아래
R     = 18.5     # 둥근 글자 중심선 반지름 → 바깥 폭 48
MID   = (XTOP + BASE) / 2
CX    = 30.0
PAD   = 6.0
CR    = 8.0      # 창 판: 창의 모서리
SR    = 6.0      # 창 판: 어깨의 모서리
# 앰버는 신호등의 "기다린다" 색이다 — 로고가 따로 정하지 않는다.
# 앱 안에 인라인되면 --st-wait 을 따르고, 파일 하나로 볼 때만 뒤의 값으로 떨어진다.
# ⑥(신호등 색 매핑)이 정해지면 style.css 한 곳만 고치면 로고까지 따라온다.
DOTC  = "var(--st-wait, #d99a2b)"

def circle(cx, cy, r):
    return (f"M {cx-r:.2f} {cy:.2f} a {r:.2f} {r:.2f} 0 1 0 {2*r:.2f} 0 "
            f"a {r:.2f} {r:.2f} 0 1 0 {-2*r:.2f} 0")

def rrect(cx, cy, hw, hh, r):
    x1, y1, x2, y2 = cx-hw, cy-hh, cx+hw, cy+hh
    return (f"M {x1+r:.2f} {y1:.2f} H {x2-r:.2f} A {r:.2f} {r:.2f} 0 0 1 {x2:.2f} {y1+r:.2f} "
            f"V {y2-r:.2f} A {r:.2f} {r:.2f} 0 0 1 {x2-r:.2f} {y2:.2f} H {x1+r:.2f} "
            f"A {r:.2f} {r:.2f} 0 0 1 {x1:.2f} {y2-r:.2f} V {y1+r:.2f} "
            f"A {r:.2f} {r:.2f} 0 0 1 {x1+r:.2f} {y1:.2f} Z")

def bowl(X, win):
    return rrect(X+CX, MID, R, R, CR) if win else circle(X+CX, MID, R)

def g_p(X, win): return [f"M {X+CX-R:.2f} {XTOP:.2f} V {DESC:.2f}", bowl(X, win)]
def g_a(X, win): return [bowl(X, win), f"M {X+CX+R:.2f} {XTOP:.2f} V {BASE:.2f}"]

def g_l(X, win):
    r, stem = 10.5, X + 25.0
    return [f"M {stem:.2f} {ASC:.2f} V {BASE-r:.2f} A {r:.2f} {r:.2f} 0 0 0 {stem+r:.2f} {BASE:.2f}"]

def g_m(X, win):
    x1, x2, x3 = X+CX-R, X+CX, X+CX+R
    if win:
        arch = lambda a, b: (f"M {a:.2f} {XTOP+SR:.2f} A {SR:.2f} {SR:.2f} 0 0 1 {a+SR:.2f} {XTOP:.2f} "
                             f"H {b-SR:.2f} A {SR:.2f} {SR:.2f} 0 0 1 {b:.2f} {XTOP+SR:.2f} V {BASE:.2f}")
    else:
        rs = R/2
        arch = lambda a, b: (f"M {a:.2f} {XTOP+rs:.2f} A {rs:.2f} {rs:.2f} 0 0 1 {b:.2f} {XTOP+rs:.2f} "
                             f"V {BASE:.2f}")
    return [f"M {x1:.2f} {XTOP:.2f} V {BASE:.2f}", arch(x1, x2), arch(x2, x3)]

def g_e(X, win, open_deg=42.0):
    cx, cy = X+CX, MID
    if win:
        x1, x2, y1, y2 = cx-R, cx+R, XTOP, BASE
        ring = (f"M {x2:.2f} {cy:.2f} V {y1+CR:.2f} A {CR:.2f} {CR:.2f} 0 0 0 {x2-CR:.2f} {y1:.2f} "
                f"H {x1+CR:.2f} A {CR:.2f} {CR:.2f} 0 0 0 {x1:.2f} {y1+CR:.2f} V {y2-CR:.2f} "
                f"A {CR:.2f} {CR:.2f} 0 0 0 {x1+CR:.2f} {y2:.2f} H {cx+7:.2f}")
    else:
        t = math.radians(open_deg)
        ring = (f"M {cx+R:.2f} {cy:.2f} A {R:.2f} {R:.2f} 0 1 0 "
                f"{cx+R*math.cos(t):.2f} {cy+R*math.sin(t):.2f}")
    return [ring, f"M {cx-R:.2f} {cy:.2f} H {cx+R:.2f}"]

def g_r(X, win):
    r = SR if win else 10.5
    stem, arm = X + 17.5, X + 42.5
    return [f"M {stem:.2f} {XTOP:.2f} V {BASE:.2f}",
            f"M {stem:.2f} {XTOP+r:.2f} A {r:.2f} {r:.2f} 0 0 1 {stem+r:.2f} {XTOP:.2f} H {arm:.2f}"]

GLYPHS = {"p":g_p, "a":g_a, "l":g_l, "m":g_m, "e":g_e, "r":g_r}

# 칸 안에서 잉크가 차지하는 폭과, 옆면이 둥근지 평평한지.
# 둥근 옆면은 곁여백을 2 줄인다(광학 보정) — 그래야 글자 사이 색이 고르다.
INK = {"p": (6.0, 54.0, False, True),  "a": (6.0, 54.0, True,  False),
       "l": (19.5, 41.0, False, True), "m": (6.0, 54.0, False, False),
       "e": (6.0, 54.0, True,  True),  "r": (12.0, 48.0, False, False)}
SB = 8.0

def word(text, tight=True, win=False):
    """[(글자, X, [경로…])…], 잉크 왼끝, 오른끝"""
    out, pen, l_edge, r_edge = [], 0.0, None, None
    for ch in text:
        il, ir, lr, rr = INK[ch]
        if tight:
            # 창 판은 옆면이 네모라 광학 보정을 하지 않는다
            X = pen - il + (SB - (2.0 if (lr and not win) else 0.0))
            pen = X + ir + (SB - (2.0 if (rr and not win) else 0.0))
        else:
            X, pen = len(out)*ADV, len(out)*ADV + ADV
        if l_edge is None: l_edge = X + il
        r_edge = X + ir
        out.append((ch, X, GLYPHS[ch](X, win)))
    return out, l_edge, r_edge

NL = "\n"
def strokes(paths, extra_attr=""):
    body = (NL + "  ").join(f'<path d="{d}"/>' for d in paths)
    return (f'<g fill="none" stroke="currentColor" stroke-width="{W}" stroke-linecap="round" '
            f'stroke-linejoin="round"{extra_attr}>{NL}  {body}{NL}  </g>')

def wrap(inner, x0, y0, w, h, label="palmar"):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}" '
            f'width="{w:.0f}" height="{h:.0f}" role="img" aria-label="{label}">{NL}  {inner}{NL}</svg>{NL}')

def sheet(text="palmer", tight=True, win=False, extra="", tail=0.0, pre=""):
    gs, l, r = word(text, tight, win)
    paths = [p for _, _, ps in gs for p in ps]
    x0, y0 = l-PAD, 18.0-PAD
    w, h = (r+PAD+tail) - x0, (134.0+PAD) - y0
    return wrap(pre + strokes(paths) + extra, x0, y0, w, h), gs, l, r

WORD = "palmar"
HELD = 4          # 앰버가 앉는 속: 두 번째 a (눈이 낱말 끝까지 간다)

d = pathlib.Path("docs/brand"); d.mkdir(parents=True, exist_ok=True)
def put(name, svg): (d/name).write_text(svg)

def held_dots(gs, quiet=False):
    """닫힌 속(p·a·a)마다 점 하나.

    점 셋은 세션이 셋이라는 뜻이 아니다 — 낱말이 가진 속이 셋일 뿐이고,
    말하려는 것은 '여럿을 쥐고 있다'와 '그중 하나가 너를 기다린다' 둘이다.
    그래서 앰버는 **하나이거나 없다**. quiet=True 면 아무도 기다리지 않는다.
    """
    out = []
    for i, (ch, X, _) in enumerate(gs):
        if ch not in "pa":
            continue
        # p 는 세로획이 속의 왼쪽에 붙고, a 는 오른쪽에 붙는다 — 보이는 속의 가운데로 민다
        nudge = 1.0 if ch == "p" else -1.0
        hot = (i == HELD) and not quiet
        fill = f'fill="{DOTC}"' if hot else 'fill="currentColor" opacity=".34"'
        out.append(f'<circle cx="{X+CX+nudge:.2f}" cy="{MID:.2f}" r="6" {fill}/>')
    return NL + "  <g>" + "".join(out) + "</g>"

_, gs, l, r = sheet(WORD)
_, gs_w, _, _ = sheet(WORD, win=True)

# 정본은 쥔 판이다 — 속 셋, 점 셋, 하나만 앰버.
put("wordmark.svg",              sheet(WORD, extra=held_dots(gs))[0])
put("wordmark-quiet.svg",        sheet(WORD, extra=held_dots(gs, quiet=True))[0])
put("wordmark-solid.svg",        sheet(WORD)[0])
put("wordmark-window.svg",       sheet(WORD, win=True, extra=held_dots(gs_w))[0])
put("wordmark-window-solid.svg", sheet(WORD, win=True)[0])

# ── 네모 자리: 심볼 대신 낱말의 첫 글자 ────────────────────────
# 심볼은 접었다(2026-09-08, 사용자) — 왜인지는 dropped/README.md.
# p 는 속이 하나라 점도 하나다: 파비콘 한 칸으로 상태를 말할 수 있다.
P_TOP, P_BOT = XTOP - H, DESC + H          # p 는 어센더를 안 쓴다
def p_glyph(dot=True, quiet=False, box=False):
    gs1, l1, r1 = word("p")
    X = gs1[0][1]
    inner = strokes([q for _, _, ps in gs1 for q in ps])
    if dot:
        fill = 'fill="currentColor" opacity=".34"' if quiet else f'fill="{DOTC}"'
        inner += f'{NL}  <circle cx="{X+CX+1:.2f}" cy="{MID:.2f}" r="6" {fill}/>'
    x0, y0 = l1 - PAD, P_TOP - PAD
    w, h = (r1 + PAD) - x0, (P_BOT + PAD) - y0
    if box:
        side = max(w, h) + 22
        x0 -= (side - w)/2
        y0 -= (side - h)/2
        w = h = side
    return wrap(inner, x0, y0, w, h)

put("p.svg",        p_glyph())                # 기다리는 중
put("p-quiet.svg",  p_glyph(quiet=True))      # 조용할 때
put("p-plain.svg",  p_glyph(dot=False))       # 점 없이
put("p-square.svg", p_glyph(box=True))        # 아이콘 격자용 여백 판

# ── 문서용 그림 ────────────────────────────────────────────────
gs, l, r = word(WORD)
paths = [q for _, _, ps in gs for q in ps]

# 곁여백
CLEAR = 24.0                                # x-높이의 절반
bx0, by0 = l - CLEAR, 18.0 - CLEAR
bw, bh = (r - l) + 2*CLEAR, (134.0 - 18.0) + 2*CLEAR
guide = (f'<rect x="{l:.1f}" y="18" width="{r-l:.1f}" height="116" fill="none" '
         f'stroke="{DOTC}" stroke-width="1" stroke-dasharray="4 4" opacity=".7"/>{NL}'
         f'  <rect x="{bx0:.1f}" y="{by0:.1f}" width="{bw:.1f}" height="{bh:.1f}" fill="none" '
         f'stroke="var(--accent, #4a58b8)" stroke-width="1.2" stroke-dasharray="4 4" opacity=".65"/>{NL}'
         f'  <text x="{bx0+7:.1f}" y="{by0+15:.1f}" font-family="ui-monospace,monospace" '
         f'font-size="11" fill="var(--accent, #4a58b8)" opacity=".8">24 = x-height / 2</text>{NL}  ')
put("clearspace.svg", wrap(guide + strokes(paths), bx0-8, by0-8, bw+16, bh+16))

# 속 셋을 가리키는 그림
BOT = 168.0
leads = []
for i, (ch, X, _) in enumerate(gs):
    if ch not in "pa":
        continue
    cx = X + CX + (1.0 if ch == "p" else -1.0)
    leads.append(f'<path d="M {cx:.2f} {MID+9:.2f} V {BOT:.2f}" stroke="currentColor" '
                 f'stroke-width="1.2" opacity=".3" stroke-dasharray="2 3"/>')
x0, y0 = l - PAD, 18.0 - PAD
put("counters.svg", wrap(strokes(paths) + held_dots(gs) + NL + "  " + "".join(leads),
                         x0, y0, (r + PAD) - x0, BOT + 4 - y0))

# 작도
LEFT, RIGHT = l - 30.0, r + 46.0
lines = [(18.0, "ascender"), (52.0, "x-height"), (100.0, "baseline"), (134.0, "descender")]
AC = "var(--accent, #4a58b8)"
met = NL.join(
    f'  <line x1="{LEFT:.1f}" y1="{y}" x2="{RIGHT:.1f}" y2="{y}" stroke="{AC}" '
    f'stroke-width="0.9" opacity=".45"/>{NL}'
    f'  <text x="{RIGHT-2:.1f}" y="{y-4}" text-anchor="end" font-family="ui-monospace,monospace" '
    f'font-size="9" fill="{AC}" opacity=".75">{name}</text>' for y, name in lines)
flesh = (NL+"  ").join(f'<path d="{q}"/>' for q in paths)
ecx = gs[4][1] + CX
put("construction.svg", wrap(
    met + NL
    + f'  <g fill="none" stroke="currentColor" stroke-width="{W}" stroke-linecap="round" '
      f'stroke-linejoin="round" opacity=".14">{NL}  {flesh}{NL}  </g>{NL}'
    + f'  <g fill="none" stroke="{AC}" stroke-width="1.1" stroke-linecap="round">{NL}  {flesh}{NL}  </g>{NL}'
    + f'  <circle cx="{ecx:.1f}" cy="{MID}" r="{R}" fill="none" stroke="{DOTC}" stroke-width="1" '
      f'stroke-dasharray="3 3"/>{NL}'
    + f'  <line x1="{ecx:.1f}" y1="{MID}" x2="{ecx+R:.1f}" y2="{MID}" stroke="{DOTC}" stroke-width="1"/>{NL}'
    + f'  <text x="{ecx:.1f}" y="{MID-R-7:.1f}" text-anchor="middle" font-family="ui-monospace,monospace" '
      f'font-size="9" fill="{DOTC}">r 18.5</text>{NL}'
    + f'  <text x="{LEFT+2:.1f}" y="146" font-family="ui-monospace,monospace" font-size="9" '
      f'fill="{AC}" opacity=".75">stroke 11 · x-height 48 · round caps</text>',
    LEFT, 4, RIGHT-LEFT, 148, label="palmar 워드마크 작도"))
print("ok")
