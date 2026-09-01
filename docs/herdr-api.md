# herdr socket API — 실측 기록

2026-09-01, herdr **0.8.2**, macOS arm64. 문서를 읽은 게 아니라 **띄워서 두드린 결과**다.

## 설치

```sh
curl -fsSL https://herdr.dev/install.sh -o install.sh   # 파이프로 바로 실행하지 않는다
sh install.sh
```

- `$HOME/.local/bin/herdr` 에 설치. **sudo 를 쓰지 않는다** — 관리자 권한 없는 기계에서
  이게 결정적이다
- 18MB 단일 바이너리, 체크섬 검증 있음
- `HERDR_INSTALL_DIR` 로 위치를 바꿀 수 있다

## 서버

```sh
herdr server        # 헤드리스
herdr server stop
herdr status
```

소켓 두 개가 생긴다:
```
~/.config/herdr/herdr.sock          ← API
~/.config/herdr/herdr-client.sock
```
둘 다 `srw-------` (0600). 남이 못 붙는다.

## 프로토콜

뉴라인 구분 JSON. **`params` 는 비어 있어도 반드시 보낸다.**

```
→ {"id":"1","method":"ping","params":{}}
← {"id":"1","result":{"type":"pong","version":"0.8.2","protocol":20,
     "capabilities":{"live_handoff":true,"detached_server_daemon":false}}}
```

오류도 구조화돼 온다:
```
← {"id":"","error":{"code":"invalid_request","message":"missing field `params` ..."}}
```

## 실제로 해 본 흐름

```
workspace.create {"path":"/tmp"}          → w1 생성, pane 1개가 따라 생긴다
session.snapshot {}                       → panes[0].pane_id = "w1:p1"
pane.send_text {"pane_id":"w1:p1","text":"echo palmer-works\n"}
pane.read {"pane_id":"w1:p1","source":"visible","format":"ansi","lines":8}
                                          → 화면 그대로, 이스케이프 포함
```

**이게 palmer 의 토대다.** 브라우저에서 터미널을 그릴 수 있다는 증거이기 때문이다.

## 걸렸던 것 (다음 사람이 같은 데서 멈추지 않도록)

- `params` 를 빼면 `invalid_request`. **비어 있어도 보낸다.**
- pane 식별자는 `pane_id` 다. `pane` 이 아니다.
- `pane.read` 는 **`source` 가 필수**다. 값은 `visible` / `recent` /
  `recent_unwrapped` / `detection` — `screen`·`scrollback` 같은 이름은 없다.
- **`strip_ansi:false` 만으로는 이스케이프가 안 온다.** `format:"ansi"` 를 함께 줘야 한다.
  (기본 `format` 이 `"text"` 라서 그렇다. 이걸 몰라 한 번 헛짚었다.)

## pane 객체

```json
{"pane_id":"w1:p1","terminal_id":…,"workspace_id":"w1","tab_id":"w1:t1",
 "focused":true,"cwd":…,"foreground_cwd":…,"agent_status":"unknown",
 "scroll":…,"revision":0}
```

`agent_status` 가 곧 신호등이다. 평범한 셸에서는 `"unknown"` 이 나온다 —
**실제 에이전트를 띄웠을 때 어떤 값이 오는지는 아직 확인하지 않았다.**

## 메서드 (91개)

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
plugin       action.invoke action.list disable enable link list log.list pane.* …
server       agent_manifests live_handoff reload_agent_manifests reload_config stop
client       window_title.clear window_title.set
notification show      popup close      ping ping      session snapshot
```

전문은 `herdr api schema --json` (255KB). **버전이 오르면 다시 뜬다 — 외우지 마라.**
