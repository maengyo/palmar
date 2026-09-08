# palmar 로고 시안 v1

심벌과 소문자 텍스트 로고를 한 세트로 만든 시안이다. 현재 UI의 인디고 포인트색과 둥근 창 모양을 생성 브리프에 반영했다. 제품에 적용하거나 최종 브랜드로 확정한 것은 아니다.

- 심벌: `palmar-symbol-v1.png`
- 텍스트 로고: `palmar-wordmark-v1.png`
- 생성 방식: 내장 이미지 생성 도구. 결과는 PNG 래스터 이미지이며 SVG 원본이 아니다.

## 심벌 생성 프롬프트

```text
Use case: logo-brand
Asset type: standalone symbol logo for palmar, a lightweight spatial canvas for coding-agent terminals.
Primary request: create a distinctive, beautifully balanced minimal symbol that combines a lowercase p silhouette with independent rounded rectangular terminal panes arranged on an open canvas. It should communicate space, calm control and lightness.
Design: a compact iconic silhouette composed of very few bold geometric elements with deliberate open gaps; slightly softened corners echo the application's rounded terminal windows. The p should feel custom and intelligent, with pane-like negative space and strong recognition when small. Quiet developer-tool identity. Not a generic four-square grid.
Style/medium: polished flat vector-style logo artwork, solid fills and extremely clean edges.
Color palette: single solid indigo #4A58B8, matching palmar's existing interface accent.
Composition/framing: one centered standalone symbol, square image, symbol occupies about 70 percent of canvas, generous clean margin, optically balanced.
Scene/backdrop: genuinely transparent background, preserve alpha; no white rectangle, no checkerboard drawn into the artwork.
Text: none.
Constraints: only the symbol. No wordmark, no slogans, no labels, no decorative border, no app-icon background container, no gradients, no shadows, no lighting, no 3D, no texture, no mockup, no watermark. Do not use green, amber, red or gray: these are reserved for terminal status in the product. Do not use palm trees, hands, robots, stars, sparkles, stacked overlapping cards, or generic terminal >_ imagery.
```

## 텍스트 로고 생성 프롬프트

```text
Use case: logo-brand
Asset type: standalone text wordmark for palmar, a lightweight spatial canvas for coding-agent terminals.
Primary request: create an elegant custom lowercase wordmark reading exactly "palmar". It should feel quietly capable, precise, friendly and light, made for a restrained developer tool with rounded terminal windows on an open canvas.
Typography: bespoke geometric semi-monospace sans serif, medium-to-semibold strokes, gently rounded outer corners and pane-like squared interior counters, open and highly legible forms, beautifully controlled optical kerning. Subtle terminal typography influence, not a pixel font. A distinctive p with a softly squared bowl and a clear stem; echo its corner geometry in a, m, e and r without sacrificing readability. Single-storey a is preferred. l must read as lowercase l.
Color palette: all six letters in single solid indigo #4A58B8, matching palmar's existing interface accent.
Composition/framing: one word only, horizontal wordmark centered in a wide landscape canvas, about 3:1 aspect ratio, generous margin and large readable lettering.
Scene/backdrop: genuinely transparent background, preserve alpha; no white rectangle, no checkerboard drawn into artwork.
Text (verbatim): "palmar" — p, a, l, m, e, r — all lowercase, spelled exactly once.
Style/medium: polished flat vector-style lettering, solid fill, clean crisp contours.
Constraints: wordmark only, no separate symbol beside it, no tagline, no punctuation, no terminal cursor or underscore, no extra letters, no frame, no app icon container, no gradients, no shadows, no 3D, no texture, no mockup, no watermark. No green, amber, red or gray.
```

