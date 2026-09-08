# herdr socket API — 실측 기록

> **2026-09-07: herdr 를 쓰지 않기로 했다** (`docs/decisions.md`). 이 문서는 (1) 우리 런타임이 제공해야 할 것의
> 목록, (2) 가벼움 비교 기준(서버 RSS 35MB, 컨트롤러 6MB/pane, 최대 출력 시 코어 하나), (3) 화면 정규식 상태 감지가
> 실제로 틀린 기록(codex 업데이트 대화상자 → idle)으로 남긴다. 아래 본문은 2026-09-04 그대로다.

herdr **0.8.2**, macOS arm64. 대부분 **띄워서 두드린 결과**고, 아닌 것(공식 문서·소스·릴리스 노트)은
출처를 옆에 적었다. 1차 2026-09-01(설치·프로토콜·읽기), 2차 2026-09-04(스파이크 ①·②, 좌표, 크기).
소켓·폴링·상태 로거 스크립트는 `docs/spikes/2026-09-04/` 에 있다. 컨트롤러 스트림·크기·재시작·
스파이크 ② 드라이버는 인라인 파이썬으로 돌려 **보존하지 못했다** — 방법을 숫자 옆에 적었고, 다음엔
스크립트로 남긴다.

## 설치

```sh
curl -fsSL https://herdr.dev/install.sh -o install.sh   # 파이프로 바로 실행하지 않는다
sh install.sh
```

- `$HOME/.local/bin/herdr` 에 설치. **sudo 를 쓰지 않는다** — 관리자 권한 없는 기계에서
  이게 결정적이다
- 단일 바이너리, 체크섬 검증 있음. 크기는 플랫폼별(릴리스 자산 기준): macOS arm64 18.1MiB,
  linux x86_64 21.7MiB
- `HERDR_INSTALL_DIR` 로 위치를 바꿀 수 있다

## 서버

```sh
herdr server        # 헤드리스
herdr server stop
herdr status
```

소켓 두 개가 생긴다:
```
~/.config/herdr/herdr.sock          ← API (뉴라인 JSON). herdr pane/agent/… CLI 도 이걸 쓴다
~/.config/herdr/herdr-client.sock   ← TUI attach · terminal session observe/control 용 (바이너리)
```
둘 다 `srw-------` (0600, `ls -l` 로 봤다). 남이 못 붙는다. 클라이언트 소켓은 u32 LE 길이 접두 —
JSON 을 보냈을 때 서버 경고의 `claimed=1684611707` 이 `{"id` 의 LE 값이다.

**pane 안 프로세스는 서버의 환경변수를 물려받는다.** Claude Code 세션 안에서 `herdr server` 를
띄웠더니 pane 의 claude 상태줄에 "inherited CLAUDE_CODE_CHILD_SESSION marker" 와 "auto mode on" 이
떴다. 서버는 깨끗한 셸에서 띄워라.

## 프로토콜

뉴라인 구분 JSON. **`params` 는 비어 있어도 반드시 보낸다.**

```
→ {"id":"1","method":"ping","params":{}}
← {"id":"1","result":{"type":"pong","version":"0.8.2","protocol":20,
     "capabilities":{"live_handoff":true,"detached_server_daemon":false}}}
```

오류도 구조화돼 온다. 파싱 단계 오류면 `id` 가 빈 문자열로 온다:
```
← {"id":"","error":{"code":"invalid_request","message":"invalid request: missing field `params` ..."}}
```

**연결 하나에 요청 하나다.** 응답을 보내면 서버가 연결을 닫는다(같은 연결로 두 번째를 보내면
`BrokenPipe`). 예외는 `events.subscribe` — 이건 연결을 열어 두고 이벤트를 밀어 준다.
새 연결 포함 왕복은 p50 0.2~0.7ms, p95 1.7ms 이하(유닉스 소켓, `pane.read` 기준).

프로토콜 번호는 자주 오른다: 0.7.0=14 → 0.8.0=19 → 0.8.2=20 (`herdr.dev/latest.json`),
master 는 22 (`src/protocol/wire.rs`, 2026-09-03 커밋 기준). 소켓을 직접 말하는 클라이언트는
이걸 따라가야 한다. 아래 "스파이크 ①" 의 CLI 를 사이에 두면 **CLI 와 서버가 같은 빌드인 한** 피할
수 있다 — 단 `herdr update` 뒤 옛 서버가 살아 있으면 CLI 가 프로토콜 불일치(protocol mismatch)로
stderr 에 "서버를 재시작하라" 를 찍고 종료 코드 1 로 죽는다(소스 `cli/protocol_guard.rs`,
`client/handshake.rs`, 실측 아님). 다리는 그 stderr 를 그대로 보여 주면 된다.

## 실제로 해 본 흐름

```
workspace.create {"cwd":"/tmp"}           → w1 생성, pane 1개가 따라 생긴다
session.snapshot {}                       → panes[0].pane_id = "w1:p1"
pane.send_text {"pane_id":"w1:p1","text":"echo palmar-works\n"}
pane.read {"pane_id":"w1:p1","source":"visible","format":"ansi","lines":8}
                                          → 화면 그대로, 이스케이프 포함
```
(1차 실행은 `path` 를 줘서 `~` 에 떴다. `cwd` 로 /tmp 에 뜨는지는 다음 실행에서 확인 — 2차의
`cwd:"<저장소>"` 는 그 디렉터리에 떴다.)

## 걸렸던 것 (다음 사람이 같은 데서 멈추지 않도록)

- `params` 를 빼면 `invalid_request`. **비어 있어도 보낸다.**
- pane 식별자는 `pane_id` 다. `pane` 이 아니다.
- **`workspace.create` 의 디렉터리 필드는 `cwd` 다.** `path` 를 주면 오류 없이 무시되고 서버의
  cwd 에 pane 이 뜬다. 모르는 필드는 조용히 버려진다.
- `pane.read` 는 **`source` 가 필수**다. 값은 `visible` / `recent` /
  `recent_unwrapped` / `detection` — `screen`·`scrollback` 같은 이름은 없다.
- **`strip_ansi:false` 만으로는 이스케이프가 안 온다.** `format:"ansi"` 를 함께 줘야 한다.
- `events.subscribe` 는 `{"subscriptions":[{"type":"pane.updated"}, ...]}` 형태다. 빈 `{}` 는 거절.
  `pane.agent_status_changed`·`pane.output_matched`·`pane.scroll_changed` 는 **`pane_id` 가 필수**다
  — pane 마다 항목을 넣고, pane 이 생기면 다시 구독한다.
- 워크스페이스를 만든 1초 뒤에도 `agent.start` 가 `agent_pane_busy` 였다("is not an available
  shell"). 얼마나 기다려야 하는지는 안 쟀다 — 소스상 CLI `herdr agent start` 는 100ms 간격으로 최대
  2초 재시도한다(`src/cli/agent.rs`). 다리도 같은 규칙으로.
- `agent.start` 는 `launch_pending:true` 로 **즉시** 돌아온다. 준비된 건 `agent_status` 가 `idle` 이
  되는 걸로 본다 — 그런데 그 idle 은 에이전트와 무관하게 약 3초 뒤에 온다(아래 스파이크 ②).
- **스키마에 있다고 구현된 게 아니다.** `pane_output_changed` 가 그 예다(아래). 반대로
  **스키마에 없어도 있는 것**도 있다 — `pane.graphics.stream` 은 공식 문서에 있지만 `herdr api
  schema --json` 의 요청 목록 91개에는 없다.

## pane 객체

```json
{"pane_id":"w1:p1","terminal_id":…,"workspace_id":"w1","tab_id":"w1:t1",
 "focused":true,"cwd":…,"foreground_cwd":…,"agent_status":"unknown",
 "tokens":{…},"scroll":{"offset_from_bottom":0,"max_offset_from_bottom":0,"viewport_rows":39},
 "revision":0}
```

`agent_status` 가 곧 신호등이다. 값은 `idle` / `working` / `blocked` / `done` / `unknown`.
평범한 셸에서는 `unknown`. 실제 에이전트에서의 전이는 아래 스파이크 ②.

**`revision` 은 출력 카운터가 아니다.** 0 일 때 2,500줄, 1 일 때 500줄을 찍어도 안 변했고,
`pane.report_metadata` 마다 1 씩 올랐다(0→1→2). 에이전트 pane 은 상태·타이틀이 바뀔 때도
오른다(pane_updated rev 1→5; 소스상 토큰 변경·만료와 스피너를 뺀 타이틀 변경이 올린다).
출력 커서로도, 메타데이터 카운터로도 못 쓴다.

## 메서드 (스키마 91개)

```
agent        explain focus get list prompt read rename send_keys start view.clear view.set wait
pane         clear_agent_authority close current edges focus focus_direction get graphics.clear
             graphics.info graphics.set input.set layout list move neighbor process_info read
             release_agent rename report_agent report_agent_session report_metadata resize
             send_input send_keys send_text split swap wait_for_output zoom
workspace    close create focus get list move move_block rename report_metadata
tab          close create focus get list move rename
worktree     create list open remove
layout       apply export set_split_ratio
events       subscribe wait
integration  install uninstall
plugin       action.invoke action.list disable enable link list log.list unlink
             pane.open pane.focus pane.close
server       agent_manifests live_handoff reload_agent_manifests reload_config stop
client       window_title.clear window_title.set
notification show      popup close      ping ping      session snapshot
```

전문은 `herdr api schema --json` (255KB). **버전이 오르면 다시 뜬다 — 외우지 마라.**
스키마 밖: `pane.graphics.stream`(전용 스트림 소켓, socket-api.mdx).

`events.subscribe` 가 받는 구독 27종: `workspace.*`(8), `worktree.*`(3), `tab.*`(5),
`pane.created/closed/updated/focused/moved/exited/agent_detected`,
`pane.output_matched {pane_id 필수, source, match}`, `pane.agent_status_changed {pane_id 필수}`,
`pane.scroll_changed {pane_id 필수}`, `layout.updated`.

---

## 스파이크 ① — 출력을 스트리밍할 수 있는가 (이슈 #1)

**답: 된다. 단, 소켓 API 가 아니라 CLI 로.**

### 소켓 API 에는 출력 변경 알림이 없다

- 구독 27종에 `pane.output_changed` 는 **없다.** 출력이 나도 `pane.updated` 는 안 온다(실측 0건).
- `pane_output_changed` 는 스키마(`EventKind`, `EventMatch{pane_id, min_revision}`)에는 있지만
  `events.wait` 에 넣으면 `unsupported_event_wait_match: events.wait currently supports pane
  agent status matches` 로 거절된다. 업스트림 이슈 #2116/#2905(같은 제목, 같은 사람이 재제출) —
  둘 다 "계획 없음(not planned)" 으로 닫혔고 #2905 에는 봇 답변으로 "의도된 동작, events.wait 는
  agent status 전이 전용" 이 달렸다. **고칠 계획이 없으니 기다리지 마라.**
- `pane.output_matched` 구독에 아무거나 매치(`regex "(?s)."`)를 걸면 **구독 시점에 한 번**
  발화하고 그 뒤 출력이 바뀌어도 안 온다. 소스(`src/api/subscriptions.rs` 의 `currently_matching`)를
  보면 에지 트리거다 — 매치가 없다가 생길 때만 발화. 구현은 서버가 100ms 마다 `pane.read` 를 도는
  것이라 구독 하나가 곧 10Hz 내부 폴링이고, 내용은 `format:"text"` 로 고정.
- `pane.wait_for_output` 은 서버가 **100ms 마다** `pane.read` 를 돌리는 폴링이다(소스
  `CONNECTION_POLL_INTERVAL=100ms`). 특정 패턴 대기에는 쓸 만하지만 반환 지연은 0~100ms 사이다
  (1회 표본 17ms). 반환값(실측): `{type:"output_matched", matched_line:"…", revision:0,
  read:{…pane.read 와 같은 스냅샷, 287B}}` — 매치된 줄과 그 시점 화면을 함께 준다.
  아무거나 매치는 이미 있는 화면에 즉시 걸려 변경 감지기가 못 된다.
- `revision` 은 위에서 말했듯 출력과 무관.

### 폴링하면 얼마나 드는가 (참고용 — 아래 스트림이 있으니 안 써도 된다)

`pane.read {source:visible, format:ansi}` 를 요청마다 새 연결로. herdr CPU 는
`ps -p <서버pid> -o cputime` 차분(**`-p` 를 빼먹지 마라**), 클라이언트는 `process_time`.

| 상태 | 주기 | herdr CPU | 클라이언트 CPU | 지연 p50 | read.text 길이 |
|---|---|---|---|---|---|
| 유휴 | 5 Hz | 0.5% | 0.2% | 0.6ms | 22B (프롬프트 한 줄) |
| 유휴 | 20 Hz | 0.7% | 0.5% | 0.4ms | 22B |
| 유휴 | 60 Hz | 3.0% | 1.6% | 0.5ms | 22B |
| 최대 출력 | 0 (폴링 없음) | 98.8% | — | — | — |
| 최대 출력 | 30 Hz | 99.9% | 0.3% | 0.2ms | 2,085B |

- 유휴 행의 herdr CPU 는 4초 창에서 `cputime` 10ms 틱 2~3개(±0.25%p) — 5Hz 와 20Hz 의 차이는
  틱 하나다. 60Hz 의 3.0% 만 유의미.
- 최대 출력(파이썬이 7초에 730만 줄)에서는 폴링 유무와 무관하게 herdr 가 코어 하나를 다 쓴다.
  **비용은 폴링이 아니라 herdr 의 PTY 파싱이다.** 에이전트 수준(초당 수십 줄, 폴링·컨트롤러 없음)
  에서는 herdr 0.5% 미만(측정 하한).
- 2,085B 는 39줄 × 약 53자, 이스케이프 없음. 94칸을 다 채운 화면이면 3.7KB 이상, 색이 있으면 더.

### 답: `herdr terminal session observe` / `control` (0.7.2 부터, 공식 문서 있음)

```sh
herdr terminal session observe w1:p1 --cols 93 --rows 39             # 읽기 전용 — PTY 크기(stty size)와 같게
herdr terminal session control w1:p1 --takeover --cols 80 --rows 24  # 입력·크기까지 — 이 크기로 PTY 가 바뀐다
```

stdout 에 뉴라인 JSON 프레임이 온다:
```json
{"type":"terminal.frame","seq":1,"full":true,"encoding":"ansi","width":80,"height":24,"bytes":"<base64 ANSI>"}
{"type":"terminal.frame","seq":2,"full":false,…}      ← full:false 는 차분(커서 이동 + 바뀐 셀). full 은 프레임마다 보고 처리한다
{"type":"terminal.closed","reason":"detached"}         ← 끝
```

`control` 은 stdin 으로 뉴라인 JSON 명령을 받는다. input·resize 는 실측, scroll 은 필드 누락·음수
오류만 보고 값은 소스(`src/client/terminal_sessions.rs`)로 확인:
```json
{"type":"terminal.input","text":"echo hi\n"}          또는  {"type":"terminal.input","bytes":"<base64>"}
{"type":"terminal.resize","cols":70,"rows":18}        ← 그 pane 의 PTY 크기가 진짜 바뀐다 (stty size → 18 70). cols/rows > 0
{"type":"terminal.scroll","direction":"up","lines":5} ← direction 은 up|down, lines 는 u16 ≥1, 선택 source: "wheel"|"page_key"
{"type":"terminal.release"}                            ← 컨트롤러 종료, terminal.closed
```

- 쓰기 컨트롤러는 pane 당 하나. `--takeover` 없이 두 번째를 붙이면 즉시 종료(exit 0).
  `--takeover` 로 교체되는 쪽이 무엇을 받는지는 **미측정.** observe 는 컨트롤러와 나란히 붙는다
  (실측 1개; 여러 observe 동시 부착은 문서상 가능, 미측정).
- **컨트롤러는 붙는 순간 PTY 를 자기 뷰포트 크기로 바꾼다.** `--cols 60 --rows 20` 으로 붙이면
  stty `20 60`. `--cols/--rows` 없이 붙이면 기본 80x24 가 된다(서버 로그 `~/.config/herdr/
  herdr-server.log`: `terminal attach client connected cols=80 rows=24`; 이 경우 stty 는 안 쟀다).
  컨트롤러가 빠져도 크기는 남는다. **palmar 는 pane 마다 반드시 `--cols/--rows` 를 캔버스 창 크기로
  주고 띄워야 한다.**
- `observe --cols/--rows` 는 PTY 크기를 **안** 바꾼다(100x30 으로 붙여도 stty `39 93` 그대로).
  대신 크기가 다르면 **화면이 잘린다** — 39행 PTY 를 30행으로 보니 상단 30행만 왔고, 마지막 프레임은
  최종 화면(…2000, 프롬프트)이 아니었다. 읽기 전용 뷰도 PTY 크기로 붙여야 한다.
- 소스상 주의(`terminal_sessions.rs`): CLI 는 그래픽(Kitty) 프레임을 버리고, **stdin 이 EOF 이면
  스스로 release 한다** — 다리는 자식의 stdin 을 열어 둬야 한다.
- 이 CLI 는 `herdr-client.sock`(바이너리 프로토콜)를 쓴다. 거기에 JSON 을 보내면 서버 로그에
  `oversized handshake` 경고가 남는다. **직접 말하지 마라 — CLI 를 자식 프로세스로 띄우고
  stdin/stdout 만 잇는다.**
- 문서: `docs/persistence-remote.mdx` "terminal session observer", `cli-reference.mdx`.
  0.7.2 릴리스 노트: "for bridge processes" — 우리 같은 다리를 위해 만든 것이다.

측정 (80x24 컨트롤러 하나, pane 안에서 출력 생성, 4.6초 창):

| 출력 | herdr CPU (컨트롤러 없음 → 있음) | 컨트롤러 CPU | 프레임 | ANSI 총량 | 프레임 간격 |
|---|---|---|---|---|---|
| 최대 (4초 버스트) | 86.6% → 87.9% | 0.0% | 216 (47 fps) | 96KB | 중앙값 19ms, 최소 16ms |
| 초당 50줄 목표 (실측 ≈30줄/s) | 0.4% → 4.6% | 0.2% | 137 (30 fps) | 32KB | 중앙값 34ms |
| 유휴 3초 (1회, 오염 의심) | — → 2.0% | 0.0% | 32 | — | — |

- 입력 → 첫 프레임: **3ms** (stdin 에 쓴 시각 → 리더 스레드가 프레임 받은 시각, 해상도 ≈1ms), 8ms
  (소켓 `pane.send_text` 기준, 5ms 파일 폴링 오차).
- 프레임 상한은 60Hz 근처(최소 간격 16ms). 차분이라 4초 최대 출력도 96KB 다.
- 창(4.6초)이 버스트(4초)보다 길어 CPU 평균이 낮게 나온다. 버스트 중엔 ≈100% — 폴링 표의 98.8% 와
  같은 부하다.
- 50줄/s 생성기는 창 안에서 다 못 돌았다(138줄, 줄당 1프레임). 다음엔 창 전체를 채우도록 돌린다.
- **유휴 값은 아직 깨끗한 게 없다.** 유휴 3초 1회에서 32프레임(≈10fps)·2.0% 가 나왔지만 그 직전
  생성기가 창 안에서 끝나지 않아 꼬리가 섞였을 가능성이 크다. observe 캡처는 유휴 1초에 0프레임,
  control 캡처도 명령 에코 외엔 없었다(소스 `pane/terminal.rs` 에 커서 블링크 타이머 없음).
  깨끗한 pane 에서 다시 재서 ⑤ 에 넣는다.
- RSS: 컨트롤러 6MB, observe 5MB, herdr 서버 35MB (컨트롤러 프로토콜 탐색 중 1회 측정; pane 수·가동
  시간 미기록).

**결론: 다리는 pane 마다 `herdr terminal session control` 자식 프로세스 하나와 웹소켓 하나를
잇는 것이다.** 폴링도, 상태 사본도 필요 없다. 미측정으로 남는 것 — `--takeover` 교체 시 옛
컨트롤러가 받는 것, 컨트롤러가 붙은 채 소켓 `pane.send_text`/`agent.prompt` 가 통하는지, TUI 와
동시 접속, 깨끗한 유휴 프레임. 이슈 #1 은 이 넷을 적어 두고 닫는다.

---

## 스파이크 ② — agent_status 가 실제 에이전트에서 믿을 만한가 (이슈 #3)

**답: 전이는 구독으로 0.1초 안에 오고, 질문 UI(AskUserQuestion)에서는 `blocked` 가 뜬다. 이슈의
핵심인 bash 승인 프롬프트는 두 번 다 실물 확인에 실패해 아직 열려 있다. 모르는 대화상자는 `idle` 로
떨어진다 — codex 에서 실제로 봤다.**

### herdr 가 어떻게 감지하는가

- claude·codex 는 **어떤 에이전트인지는 포그라운드 프로세스로 식별하고, 상태(idle/working/blocked)는
  훅이 아니라 화면 매니페스트로 판정**한다. 공식 문서(agents.mdx)의 표에서 Claude Code / Codex 의
  상태 권한은 "screen manifest". 훅 통합(`herdr integration install claude`)은 세션 ID 복원용이지
  상태 권한이 아니다. (Pi·OMP·Kimi·MastraCode 는 훅, OpenCode·Kilo 는 플러그인이 설치돼 있으면 그쪽이 권한.)
- 규칙은 `~/.local/state/herdr/agent-detection/remote/<agent>.toml` 에 있고 herdr.dev 에서
  자동 갱신된다(claude.toml 2026.08.31.1). 화면 하단 버퍼(`source:"detection"`)와 OSC
  타이틀·진행률에 정규식을 건다. 예(claude.toml, 단순화):
  - 타이틀 앞 스피너 `⠋`/`◐` → `working` (우선순위 1100)
  - 프롬프트 박스 `❯` → `idle` (950)
  - "esc to cancel" + ("enter to confirm" 또는 "enter to select"+"…to navigate") → `blocked` (980)
  - "do you want to proceed?" + ("bash command"/"bash("/"tab to amend"/"ctrl+e to explain" 중 하나) + yes/no 줄 → `blocked` (850)
- `blocked` 는 **일부러 엄격**하다(agents.mdx). 아는 화면만 blocked, 모르면 `idle`. 문서는 그때
  `agent.explain` 에 `default_known_agent_idle_fallback` 이 찍힌다고 한다 — 우리 스크립트는 explain 의
  `matched_rule` 만 찍어서(None) 그 라벨은 확인 못 했다. 다음엔 explain 전체를 저장한다.
- `pane.report_agent {pane_id, source, agent, state}` 로 바깥에서 상태를 보고할 수 있다. **그러면 그
  source 가 그 pane 의 상태 권한을 통째로 가져간다** — 이후 herdr 는 그 pane 에 화면 매니페스트를
  돌리지 않는다(agents.mdx: "does not also run screen manifest fallback"). 놓친 화면 하나를
  보태는 문이 아니라 전체를 떠맡는 문이다. 값도 idle/working/blocked/unknown 뿐(`done` 없음).
  되돌리려면 `pane.release_agent`/`pane.clear_agent_authority`. 미측정 — 문서 기준.
- 로컬 오버라이드 `~/.config/herdr/agent-detection/<agent>.toml` 이 항상 이긴다(agents.mdx:
  "Local overrides always win"). 놓치는 화면은 이쪽으로 규칙을 보탤 수 있다.
- 대안 — 훅. polycanv 실측(`docs/research/cli-status-hooks.md`)에 따르면 claude·codex·qwen 은
  승인 프롬프트에서 `PermissionRequest` 훅을, 끝나면 `Stop` 을 쏜다. 이를 `pane.report_agent` 로
  herdr 에 넣으면 blocked 가 화면 모양과 무관해진다 — 단 그 순간 그 pane 의 화면 매니페스트는
  꺼진다(권한 통째로). **매니페스트 오버라이드 vs 훅 보고는 정하지 않았다 — 사람이 정한다.**

### 실측 타임라인 — claude 2.1.259

`pane.agent_status_changed` 구독 + `pane.get` 4Hz 폴링(로거 `agent_log.py`) + 드라이버 쪽 ≈10Hz
폴링(보존 안 됨). `agent.start` 응답은 `launch_pending:true`, status unknown. 시간은 직전 동작 기준.

| 동작 | 전이 | 걸린 시간 |
|---|---|---|
| `agent.start` | `pane_agent_detected` 이벤트 (agent=claude) | +0.3s |
| | → `idle` | +3.6s |
| `agent.prompt` (AskUserQuestion 시키기) | → `working` (타이틀 ◐◑ 스피너) | +0.5s |
| | → `blocked` (`live_blocked_form`) | +8.5s |
| `send_keys enter` (답 선택) | → `working` | +0.3s |
| | → `done` | +5.6s |
| `/exit` | → `working` → `done` → 에이전트 해제 → `unknown` | +0.6s / +1.1s / +1.6s / +2.0s |

- blocked 까지 8.5s 중 8.4s 는 claude 가 질문을 만드는 시간. 스피너가 멈추고(질문 UI 렌더) 0.1s 뒤 blocked.
- 화면 변화 → 상태 이벤트는 working 105ms, blocked 106ms(각 1회). 소스상 working → (보이는 신호
  없는) idle 은 100ms 재확인 3회, 최대 700ms 늦춘다(`pane/agent_detection.rs`) — 미측정.
- +3.6s(claude)·+3.5s(codex) 는 herdr 의 `agent.start` 정착 지연 3초(`AGENT_START_SETTLE_DELAY`)와
  감지 유예 3초(`AGENT_STARTUP_GRACE_WINDOW`)가 만든 숫자다 — 에이전트 기동 시간이 아니다(소스 기준).
- 4Hz 폴링은 이벤트보다 0~200ms 늦었다(폴 주기 250ms 안에서 고르게). 한 번(blocked)은 폴이
  이벤트보다 62ms 먼저 봤다 — 이벤트도 수십 ms 는 늦을 수 있다. 그래도 **구독이 폴링보다 빠르고 싸다.**
- `done` 은 "끝났는데 아직 안 본 것"이다(문서 기준, done→idle 전이는 미측정). 탭 포커스 또는 소켓의
  `pane.focus`/`agent.focus` 가 "봤다"로 치고 `idle` 로 바꾼다 — `pane.read` 는 안 바꾼다.
  palmar 가 창을 앞으로 가져올 때 `pane.focus` 를 쏴야 done 이 걷힌다. 신호등에는 둘 다 "입력 가능".
- 종료도 working→done 을 한 번 거친다 — 신호등이 done 색으로 한 번 바뀐 뒤 꺼질 것이다.
- **미확인:** bash 승인 프롬프트(`bash_permission_prompt` 규칙). 첫 시도는 내 스크립트 탓이다 —
  대기 루프가 "이미 done" 을 종료 조건으로 잡아 2ms 에 빠져나왔고, 화면을 읽은 뒤 바로 `/exit` 를
  보냈다. 로그상 프롬프트는 제출돼 working(01:24:23.3)→done(01:24:24.2) 을 거쳤지만 승인 프롬프트가
  뜰 시간을 주지 않았다. 두 번째(`--permission-mode default`, 한 줄 프롬프트)는 계정 세션 한도에
  걸려 claude 가 답을 못 했다. 규칙은 매니페스트에 있다. 다시 잰다.

### 실측 — codex 0.147.0

- `agent.start` → 감지 +0.1s → `idle` +3.5s.
- **그런데 그때 화면은 "✨ Update available! 1. Update now / 2. Skip / Press enter to continue"
  대화상자였다.** 사람을 기다리는 화면이 `idle` 로 분류됐다 — codex.toml 의 blocked 규칙 어디에도
  없는 모양이고, 타이틀만 있으면 idle 로 보는 `osc_title_idle`(우선순위 100) 이 잡았을 가능성이
  크다(그 시점 explain 은 안 찍음).
- 그 상태에서 `agent.prompt` 를 보내자 Enter 가 "1. Update now" 를 눌러 `npm install -g
  @openai/codex` 가 돌기 시작했다(중단함, 바이너리 무사). npm 이 포그라운드가 되자 herdr 는
  에이전트를 해제했다(로그 01:24:39 `released:true final_status idle`, 이후 agent=None) — 신호등은
  그 순간 "에이전트 없음"이 된다. **신호등의 거짓 "입력 가능"이 실제로 나는 사례이고, 그 위에서 자동
  입력을 하면 사고가 난다.**
- codex 의 `working`/`blocked` 전이는 **미측정** — 여기서 실험을 중단했다.
- opencode: 미측정.

### 시사점

- `blocked` 가 **뜨면** 믿는다(일부러 엄격한 규칙이라 오탐이 드물다). `working` 도 믿는다. 단
  blocked 는 AskUserQuestion 한 사례뿐이고 bash 승인은 미확인.
- **`idle`/`done` 은 "확실히 안 기다림" 이 아니다** — 매니페스트가 모르는 대화상자일 수 있다.
  UI 에서 idle 을 "입력 가능" 색으로 칠하더라도, 화면 자체를 보여 주는 게 안전장치다.
- 상태만 보고 자동으로 키를 보내지 마라.
- **이슈 #3 은 닫지 않는다** — bash 승인 프롬프트 재측정(`--permission-mode default`, 한 줄 프롬프트,
  대기 루프는 blocked 만 기다리기), codex working/blocked 전이, opencode 가 남았다. 셋을 이슈에
  체크리스트로 남긴다.

---

## 자유 배치 좌표를 herdr 에 둘 수 있는가 (decisions ③)

**답: 못 둔다. palmar 가 보관한다.**

- `layout.export` 는 split 트리를 돌려준다:
  ```json
  {"workspace_id":"w1","tab_id":"w1:t1","zoomed":false,"focused_pane_id":"w1:p1",
   "root":{"type":"split","direction":"right","ratio":0.8,"first":{"type":"pane",…},"second":{…}}}
  ```
  `layout.apply` 는 스키마상 `root`(필수)와 workspace_id/tab_id/tab_label/focus 를 받는다 — 호출은
  안 했다(스키마 기준). 겹침·절대 좌표는 표현할 수 없다.
- `pane.report_metadata {pane_id, source, tokens:{…}}` / `workspace.report_metadata` 로
  문자열 토큰(socket-api.mdx 기준: 한 번의 report 당 키 ≤16개, pane/workspace 당 보관 ≤32개, 키
  `[A-Za-z0-9_-]{1,32}`, 값 ≤80자, `null` 이면 삭제)을 실을 수 있고 `pane.get`/`workspace.get` 에
  `tokens` 로 돌아온다. 즉시 반영, 1ms.
- **그러나 서버 재시작 후 사라진다.** 실측: 재시작 전 `{"palmar_x":"121","palmar_y":"80",…}`
  → 후 `None`. `session.json` 에도 안 들어간다. 공식 문서: "Token metadata is not restored
  after a server restart." `ttl_ms`(최대 24h) 도 있다 — 단명 메타데이터용이다.
- `pane_id` 는 재시작 후에도 그대로였다(실측은 pane 하나뿐 — w2·w3·w4 를 닫고 재시작했다). 근거는
  `session.json` 이 워크스페이스마다 `public_pane_numbers`·`public_tab_numbers`·layout 트리를
  저장한다는 것. 키로 쓸 수 있되, 여러 pane/탭에서도 유지되는지는 다음에 확인한다.

---

## pane 크기는 누가 정하는가

- 헤드리스 가상 화면은 120x40(`config.toml [server] headless_cols/rows`), 사이드바 기본 26
  (`[ui] sidebar_width`, 워크스페이스 이름에 따라 18~36 으로 자동 조정) → 탭 영역 rect 94x39.
  워크스페이스/탭마다 이 rect 를 split 비율로 나눈다(right 0.3 → rect 28+66). rect 와 PTY 는
  같지 않다: 전체 영역 pane 의 PTY 는 stty `39 93`(rect 94) 이었고, split pane 의 PTY 는 안 쟀다.
  cols/rows 를 직접 주는 소켓 메서드는 없다(`pane.resize` 는 방향+양, `pane.split` 은 ratio).
- `terminal session control --cols/--rows` 와 `terminal.resize` 가 **그 pane 의 PTY 를 요청한
  크기로 바꾼다.** 실측: `--cols 60 --rows 20` → stty `20 60`; `terminal.resize {cols:70,rows:18}`
  → stty `18 70`. 컨트롤러가 빠져도 남는다. 레이아웃 스냅샷(`pane.layout` 의 rect)은 94x39 인 채로 어긋난다.
- 실측된 것은 **헤드리스·TUI 없음·레이아웃 변경 없음** 조건에서다. 공식 문서는 이를 "컨트롤러
  viewport" 라 부르므로 남는 건 보장이 아니다. 재시작 직후 서버 로그의 spawn 크기는 80x24 다
  (`rows=24 cols=80`) — 첫 기동 때도 같았고 그 pane 은 뒤에 stty `39 93` 이었으니 레이아웃 적용 전
  값으로 보인다. `terminal.resize` 로 바꾼 크기가 재시작 뒤 남는지는 **미측정**(재시작 테스트 pane 은
  리사이즈한 적이 없다). 레이아웃 이벤트(`layout.updated`) 뒤에 유지되는지도 미측정.
- TUI 클라이언트가 붙으면 가상 화면(120x40)이 그 터미널 크기로 바뀌고 pane 은 그 안에서 split 몫을
  다시 받을 것이다 — **미측정.** 근거는 `herdr --default-config` 주석 "Size of the virtual terminal
  used when no client is attached. Attached clients always use their own terminal size." 와
  session-state 문서 "After a client attaches and provides terminal size…". palmar 와 TUI 를
  동시에 쓰면 서로 크기를 뺏을 것이다 — 실물로 봐야 한다.

**결론: 크기를 바꾸는 유일한 손잡이는 `terminal.resize`(또는 control `--cols/--rows`) 다.**
헤드리스·TUI 없음 조건에서는 자유 크기 조절이 성립했다. TUI 동시 접속·`layout.updated`·재시작 뒤는
미측정 — 어느 쪽이든 palmar 는 (1) pane 마다 control 을 띄울 때 항상 `--cols/--rows` 를 주고,
(2) 재시작·재접속·`layout.updated` 뒤에 자기 좌표에서 크기를 다시 보낸다.
