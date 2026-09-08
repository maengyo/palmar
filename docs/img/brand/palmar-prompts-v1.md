# palmar 로고 시안 v1

사용자 요청: “손바닥안에 터미널이 있다는 의미로 palmar 로 다시 생각해줘”.

이 시안은 손바닥 실루엣 가운데 터미널 창과 `>_`를 넣었다. 텍스트는 사용자 요청 철자대로 소문자 `palmar`다. 손 안에서 다루는 터미널이라는 뜻을 직접 표현하는 방향이다.

- 심벌: `palmar-symbol-v1.png`
- 텍스트: `palmar-wordmark-v1.png`
- 생성 방식: 내장 이미지 생성 도구. 두 결과는 PNG 래스터 이미지다.
- 이 작업은 로고 시안 제작이다. 기존 제품 코드나 문서의 프로젝트 이름을 변경하는 작업은 하지 않았다.

## 심벌 생성 프롬프트

새 이미지 생성. 입력 이미지 없음.

```text
Use case: logo-brand
Asset: standalone symbol for a new brand direction named palmar, meaning a terminal in the palm of your hand. It is a light spatial workspace for coding-agent terminals.
Design a memorable youthful logo: a simple recognizable open human palm with a little terminal window INSIDE THE PALM. Front-facing relaxed hand, four softly rounded fingers held fairly close together and one asymmetric thumb; the thumb and fingers must make this unmistakably a hand. Friendly compact silhouette, natural enough proportions but highly simplified. Palm faces the viewer. No drawing of fingernails, joints or palm lines.
Graphic construction: single bright indigo hand silhouette, with ONE generous softly rounded rectangular negative-space terminal window cut out of the central palm. Inside that window, a crisp simple indigo >_ command prompt. The terminal is contained within the palm, not placed above the fingertips. The prompt must look like >_ rather than an eye or a face. Keep the wrist short and the composition compact. Carefully balance the terminal window size and finger shapes for an iconic readable silhouette.
Style: contemporary independent software brand, human, playful, fresh, clean flat vector-style artwork, confident curves with just enough crisp geometry. Sophisticated simplicity. Suitable for an app symbol and small web header.
Color: solid vivid periwinkle-indigo #6B64F5 only.
Background: transparent PNG asset, alpha=0 surrounding the symbol and in the negative-space window.
Composition: single symbol centered in square canvas with generous margins.
Text: only the tiny >_ prompt, no brand name or other lettering.
Avoid: gradients, shading, texture, 3D, gloss, realism, outlines around a solid background tile, badges, labels, watermarks, corporate handshake or charity icon imagery, a clenched fist, robot faces, palm trees, hidden letter p/F shapes. No green, amber, red or gray because those are reserved for product status.
```

## 텍스트 로고 생성 프롬프트

참고 이미지: `palmer-wordmark-v2.png`.

```text
Use case: text-localization / logo-brand
Input image: supporting reference for the current youthful wordmark style and vivid indigo color.
Create a matching standalone wordmark for the new name "palmar".
Exact lettering: "palmar" — p, a, l, m, a, r — SIX lowercase letters, including TWO a letters. Replace the reference's e with a matching second a. There is NO e in the new name.
Preserve the youthful, clean, naturally proportioned lowercase geometric sans-serif character of the reference, the single-storey a, open counters, straight l and small diagonal p descender cut. Make the two a glyphs exactly the same design. Fine-tune optical kerning for the new name.
Brand meaning: a terminal in the palm of your hand; the lettering should feel human, friendly and nimble.
Solid periwinkle-indigo #6B64F5. Clean uniform fill and crisp edges.
Only the wordmark "palmar", no hand symbol beside it, no slogan, no extra text or punctuation.
Centered horizontal wide canvas with generous margins. Transparent PNG asset with alpha=0 outside every letter and inside its counters. Preserve transparency.
No gradient, texture, shadows, 3D, mockup, frame, watermark, green, amber, red or gray.
```

