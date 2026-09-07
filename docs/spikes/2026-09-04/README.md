# 스파이크 ①·② 측정 스크립트 (2026-09-04, herdr 0.8.2)

숫자는 `docs/herdr-api.md` 에 있다. 여기는 **그 숫자 일부를 만든 코드**다 — "재는 코드부터 의심해라".

- `hc.py` — 소켓에 요청 하나 보내고 응답·소요시간을 찍는다. `hc.py ping`, `hc.py pane.read '{"pane_id":"w1:p1","source":"visible","format":"ansi"}'`
- `exp_events2.py` — `events.subscribe` 구독 27종 중 인자 없는 24종을 다 걸고 출력을 만들며 어떤 이벤트가 오는지 본다
  (`pane_id` 가 필수인 `output_matched`·`agent_status_changed`·`scroll_changed` 는 제외; `output_matched` 는 `exp_stream.py`). 답: 출력 이벤트는 없다
- `exp_stream.py` — `revision` 의미, `events.wait(pane_output_changed)`, `pane.wait_for_output`, `pane.output_matched` 구독
- `exp_poll.py` — `pane.read` 폴링 주기별 herdr/클라이언트 CPU (`ps -p <pid> -o cputime` 차분). 서버 PID 는 `pgrep -f "^herdr server$"` 로 잡는다 —
  컨트롤러 자식 프로세스도 이름이 `herdr` 라 `-x herdr` 는 틀릴 수 있다
- `agent_log.py` — pane 하나의 `pane.agent_status_changed` 구독 + `pane.get` 4Hz 폴링을 타임스탬프와 함께 기록(수동 로거)
- `stream.py` — pane 안에서 4초간 최대 속도로 줄을 찍는 부하 생성기

**보존하지 못한 것:** `herdr terminal session observe/control` 측정(3ms·47fps·4.6%·RSS), 크기 변경, 재시작 테스트,
스파이크 ② 의 드라이버(agent.start → prompt → send_keys 순서와 ≈10Hz 폴링)는 인라인 파이썬으로 돌려 스크립트가 없다.
방법은 `herdr-api.md` 본문에 적었다. **다음 측정에서는 먼저 스크립트로 옮기고 돈다.**

전제: `herdr server` 가 떠 있고 `w1:p1` 이 있다. 끝나면 `herdr server stop`.
