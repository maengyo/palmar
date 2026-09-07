# 스파이크 D — PTY → 웹소켓 → xterm.js 파이프라인 (2026-09-07)

`docs/decisions.md` ② 를 정하려고 만든 것이다. **같은 프로토콜·같은 상수**로 서버를 둘 만들어
같은 페이지로 잰다. 숫자는 `docs/own-runtime.md` 의 "스파이크 D" 에 있다.

```
py/server.py     파이썬 표준 라이브러리만 — pty + 손으로 짠 RFC 6455
bun/server.ts    Bun 내장 — Bun.Terminal + Bun.serve
web/             xterm.js 6 캔버스. 프레임 시간·입력 지연을 직접 잰다
wsprobe.py       브라우저 없이 서버 쪽만 재는 도구 (CPU·RSS·처리량)
```

## 띄우기

xterm 번들은 저장소에 없다. 먼저 받는다:

```sh
cd web
curl -fsSLO https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/lib/xterm.js
curl -fsSLO https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/css/xterm.css
curl -fsSLO https://cdn.jsdelivr.net/npm/@xterm/addon-webgl@0.19.0/lib/addon-webgl.js
curl -fsSLO https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.11.0/lib/addon-fit.js
```

```sh
python3 py/server.py --port 8801     # http://127.0.0.1:8801
bun run bun/server.ts --port=8802    # http://127.0.0.1:8802
```

서버 쪽만 재려면:

```sh
python3 wsprobe.py --port 8801 --pid $(pgrep -f 'server.py --port 8801') --name python
```

## 사람이 봐야 하는 것

숫자가 좋아도 미끄러운지는 눈으로 본다. 페이지를 열고:

- pane 8개를 띄운다
- **드래그 자동 왕복**을 켠다 → 프레임 p95·최악을 본다
- **한 pane 에 부하** 를 켠 채로 다른 창을 끌어 본다 → 입력이 막히는가
- **전부에 부하** → WebGL 개수가 유지되는가, 글자가 물결치는가
- `Ctrl+-` 로 줄여 본다 → 캔버스가 더 들어오는가, 글자가 여전히 선명한가
- 렌더러를 DOM 으로 바꿔 같은 것을 다시 본다

## 자동으로 재기 (실제로 쓴 방법)

주소에 시나리오를 넣으면 페이지가 스스로 띄우고·켜고·재고, 결과를 `/report` 로 보낸다.
결과는 `PALMER_REPORT` (기본 `/tmp/palmer-report.jsonl`) 에 한 줄씩 쌓인다.

```
?panes=8&renderer=webgl&auto=flood,drag&warm=6&measure=10
   panes     띄울 개수
   renderer  webgl | dom
   auto      flood(한 개 부하) · floodall(전부) · drag(자동 왕복), 쉼표로 여럿
   warm      이 초 뒤에 계측을 리셋한다 (붙는 동안의 값이 안 섞이게)
   measure   리셋 뒤 이 초만큼 재고 결과를 보낸다
```

**헤드리스 Chrome 으로는 못 잰다.** `--dump-dom` 은 로드 직후 빠져나가서 계측 창이 끝나기
전에 페이지가 죽는다. 창을 실제로 띄워 놓고 재야 한다. 화면이 잠겨도 안 된다.

## 상수 (둘이 같다)

`HIGH_WATER 100KB` / `LOW_WATER 10KB` — ttyd·VS Code 값(조사).
`COALESCE_MS 5` — macOS PTY 는 1KB 씩 읽히므로 모아서 보낸다(조사).
`PUMP_BUDGET 256KB` — 한 pane 이 이벤트 루프를 잡지 않게(polycanv 교훈).
