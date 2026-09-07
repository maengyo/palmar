# 우리 런타임 스파이크 A·C (2026-09-07)

숫자는 `docs/own-runtime.md`. 여기는 그 숫자를 만든 코드다. 파이썬은 측정 도구일 뿐, 언어 결정이 아니다.

- `daemon1.py` / `daemon2.py` — 스파이크 A. daemon1 이 `pty.fork` 로 bash 루프를 띄우고 master fd 를
  유닉스 소켓(`socket.send_fds`)으로 넘긴 뒤 정리 없이 죽는다. daemon2 가 받아서 자식이 사는지,
  `TIOCSWINSZ` 가 되는지, kqueue 로 종료를 잡는지 본다.
  실행: `python3 daemon1.py /tmp/x.sock & sleep 0.4; python3 daemon2.py /tmp/x.sock <daemon1 pid>`
- `drive.py` / `hooklog.py` / `settings.json` — 스파이크 C. `claude --settings settings.json --permission-mode
  default` 를 진짜 PTY 에서 띄우고, bash 승인 → AskUserQuestion → /exit 를 시키며 훅 발화 시각을 기록한다.
  `settings.json` 의 `<이 디렉터리>` 를 절대 경로로 바꿔야 한다. cwd 는 신뢰된 디렉터리여야 한다
  (아니면 trust 대화상자에서 멈춘다). **텍스트와 Enter 는 따로 보낸다** — `submit()` 참고.
  환경에서 `CLAUDE*`·`HERDR*` 를 뺀다. 끝나면 `pgrep -fl claude` 로 남은 프로세스를 확인한다.

전제: claude 가 설치돼 있고 로그인돼 있다. 세션 한도에 걸리면 화면에 "hit your session limit" 이 뜨고 훅은 오지 않는다.
