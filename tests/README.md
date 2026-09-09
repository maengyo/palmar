# tests

    python3 -m unittest discover tests          # everything
    python3 -m unittest tests.test_pure         # under a second, no daemon
    python3 -m unittest tests.test_daemon       # ~30s, starts real daemons
    python3 -m unittest tests.test_browser      # ~1min, needs Chrome; skipped without it

표준 라이브러리만 쓴다. pytest 도, 어떤 의존성도 없다 — 이 저장소가 의존성 0인 것이 성질이고
시험이 그 성질을 깨서는 안 된다.

## 세 층

| | 무엇을 묻나 | 값 |
|---|---|---|
| `test_pure` | 데몬 없이 답할 수 있는 것 — `_absorb`, 제목 판정, `cwd_of`, 복구 파일 읽기, 이름 규칙, 기동 가드 | 0.7초 |
| `test_daemon` | 진짜 데몬을 격리된 HOME 에 띄우고 HTTP·웹소켓으로 — 열쇠·토큰, 경로 탈출, pty 격리, 되살리기 한 바퀴, `--doctor` 유출 | 27초 |
| `test_browser` | 진짜 크롬으로 진짜 페이지를 — 한글 입력, 창 크기, 단축키 이름표, 테마 세 상태, 되살리기 카드와 격자 | 54초 |

평소에는 앞 둘만 돌려도 된다. `palmar/web/` 를 건드렸으면 셋 다 돌린다.

## 규칙

**진짜 `~/.palmar` 는 절대 안 건드린다.** 데몬마다 임시 HOME 을 준다. 예의 문제가 아니라 —
데몬은 뜰 때 토큰을 새로 만들고 HOME 당 배타적 잠금을 잡으므로, 진짜 것을 쓰면 **지금 쓰고 있는
세션이 로그아웃된다.**

**포트는 매번 새로 고른다.** 시험이 8801 을 쓰면 사람이 쓰는 데몬과 부딪힌다.

**시험은 자기 판을 자기가 연다.** 다른 시험이 만든 것을 빌려 쓰면 알파벳 순서에 따라 통과하거나
실패한다(`test_browser` 에서 실제로 그랬다).

**끄는 것은 `terminate` 로 한다.** 복구 파일은 종료 경로에서 쓰이므로 `SIGKILL` 로 죽이면 그
길을 통째로 건너뛴다.

## 여기서 볼 수 없는 것

크롬이 `--disable-gpu` 로 돌아 소프트웨어로 그린다. **합성(compositing)에 관한 것은 원리상 안
보인다** — #17 의 끄는 자국이 그것이다. 빠뜨린 게 아니라 이 방법으로는 못 본다.

윈도우도 여기서 못 돈다. `.github/workflows/windows-probe.yml` 이 그쪽 몫이다(#29).
