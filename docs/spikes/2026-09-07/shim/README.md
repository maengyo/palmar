# 스파이크 E — `claude --settings` 는 병합인가, 대체인가 (2026-09-07 저녁)

**왜 잰다.** 훅을 PATH shim 으로 붙이기로 하면(`decisions.md` "훅은 palmer 가 붙인다"), 껍데기가
`claude --settings /tmp/palmer/<pane>.json` 을 exec 한다. 이때 `--settings` 가 사용자의
`~/.claude/settings.json` 훅을 **지워 버리면** shim 은 쓸 수 없다. 이게 shim 전체가 걸려 있는 전제다.

**어떻게 잰다.** 사용자 설정은 건드리지 않는다. 대신 같은 병합 코드를 타는 두 층을 쓴다 —
프로젝트 훅(`proj/.claude/settings.json`)과 `--settings` 훅(`extra.json`). 둘이 다 발화하면 병합이다.
`--settings` 쪽에는 `http` 타입도 같이 넣어 그것도 확인한다(`hookserver.py` 가 127.0.0.1:8899 에서 받는다).

```sh
python3 hookserver.py /tmp/log.txt &
cd proj && claude -p "Reply with exactly the word: ok" --settings ../extra.json </dev/null
```
(Claude Code 안에서 돌릴 때는 `CLAUDE*` 환경변수를 지우고 돌린다.)

**결과 (Claude Code 2.1.263, 1회, 5초).** `result.log` 그대로:

```
PROJ-SessionStart        EXTRA-SessionStart                          ← 둘 다
HTTP-UserPromptSubmit    EXTRA-UserPromptSubmit   PROJ-UserPromptSubmit
HTTP-Stop                PROJ-Stop                EXTRA-Stop
```

- **병합된다.** 세 이벤트 모두에서 프로젝트 훅과 `--settings` 훅이 둘 다 발화했다. `--settings` 는
  도움말 그대로 "additional settings" 다.
- **`http` 타입은 실제로 받는다.** `UserPromptSubmit`·`Stop` 은 POST 가 왔다(541·608 바이트,
  `hook_event_name` 포함).
- **`http` 는 `SessionStart` 에서 안 왔다.** 같은 이벤트의 command 훅은 왔다. 1회 관측 — 타입이
  그 이벤트를 안 지원하는지, 타이밍인지 모른다. 상태에 쓰는 세 이벤트(`UserPromptSubmit`·
  `PermissionRequest`·`Stop`)에는 걸리지 않지만 **재확인 대상**이다.
- 헤더에 환경변수는 안 실어 봤다. shim 은 pane 을 URL(`?pane=<id>`)에 박으므로 필요가 없어졌다.

**안 잰 것.** `PermissionRequest` 는 `-p` 모드에서 안 나므로 이 스파이크에 없다 — 스파이크 C 가
command 타입으로 잰 것이 있다. 사용자 층(`~/.claude/settings.json`) 과의 병합은 직접 안 봤고,
프로젝트 층으로 대신했다. 껍데기가 진짜 `claude` 를 찾는 방법과 codex 쪽은 #20 에 남는다.
