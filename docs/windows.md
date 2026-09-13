# 네이티브 윈도우 (#29) — 어디까지 왔나

WSL 안에서는 이미 된다. 이 문서는 **WSL 없이 윈도우에서 직접** 도는 데몬 이야기다.

**2026-09-12 에 멈춰 둔다.** 사용자의 윈도우는 사내망이고 설치가 막혀 있어서, 매 질문을 GitHub
윈도우 러너로 보내야 한다. 계층 하나 만드는 데 CI 라운드 열 번이 들었고 그중 절반은 이쪽 버그였다.
**윈도우 데스크탑을 쓸 수 있을 때 재개한다.** 그때 다시 캐내지 않아도 되도록, 잰 것을 여기 남긴다.

---

## 된 것

| | |
|---|---|
| `palmar/conpty.py` (374줄) | ctypes 로 ConPTY + Job Object. **윈도우에서 전부 통과** |
| `palmar/posixpty.py` (146줄) | 같은 모양의 POSIX 구현 |
| 경계 | 연산 11개, 시그니처까지 동일(AST 로 확인) |
| `dev/conpty-check.py` | 윈도우에서 그걸 검증한다. **29초** |
| `.github/workflows/windows-conpty.yml` | 전용 워크플로. `sections` 로 질문을 고른다 |

마지막 통과 기록: 자식이 우리 pseudo-console 안에 · 출력·입력 · **U+FFFD 0개** · 판을 닫으면
트리가 같이 죽음(`1 → 3 → 1`) · resize 중에도 스트림 유지.

## 잰 것 — 다시 재지 마라

전부 실제 윈도우 러너에서 나온 값이다.

| 물음 | 답 | 그래서 |
|---|---|---|
| `add_reader` | **`NotImplementedError`** (소켓·파이프 다) | **판마다 읽기 스레드.** 콜백 모델 불가 |
| `add_signal_handler` | `NotImplementedError` | Ctrl-C 는 `signal.signal` |
| 셸을 죽이면 자식도? | **안 죽는다** | **판마다 Job Object** (`KILL_ON_JOB_CLOSE`) |
| `CreateJobObjectW` | ctypes 만으로 된다 | 의존성 0 유지 |
| `msvcrt.locking` | 배타적 · 둘째 거부 · **죽여도 풀림** | `fcntl.flock` 을 대체 |
| `chmod 0600` | `st_mode` 가 `0o666` (무효) | 모드 비트로는 아무것도 못 지킨다 |
| `%USERPROFILE%` ACL | SYSTEM · Administrators · 본인 | **위협 모델이 이미 성립** — ACL 작업 불필요 |
| 셸 | cmd · powershell · **pwsh 7** · Git bash · wsl | `$SHELL` 대응은 `COMSPEC`(=cmd.exe) |
| `pywinpty` | `read()` 가 `str`. NUL 사라지고 210KB 한글에서 **8글자 파괴** | **쓰지 않는다** (`decisions.md` 참조) |

### 두 번 헤맨 자리 — 다시 헤매지 마라

- **`PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` 은 처음부터 잘 먹었다.** 문제는 그게 *어느 콘솔에
  붙을지*만 정하고 *stdout 이 어디로 갈지*는 안 정한다는 것. 그냥 두면 윈도우가 부모의 표준 핸들을
  넘겨서, 데몬이 터미널에서 시작되지 않았으면 **판 출력이 데몬 stdout 으로 샌다.**
  → `STARTF_USESTDHANDLES` + **세 핸들 NULL**. 넘겨받을 게 없으니 런타임이 `CONIN$`/`CONOUT$` 를
  스스로 연다. 표준 핸들을 파이프 끝으로 주는 변형은 **안 된다**(콘솔 렌더링까지 잃는다).
- **`argtypes` 를 반드시 선언한다.** 안 하면 64비트 포인터가 C `int` 로 넘어가 넘친다.
- **`close()` 는 `CancelIoEx` 먼저.** 읽기 스레드가 막혀 있으면 `ClosePseudoConsole` 이 교착한다.
- **ConPTY 는 파이프가 아니라 터미널이다.** NUL·잘못된 UTF-8·210KB 홍수는 *어떤* ConPTY 에서도
  그대로 안 나온다. 볼 숫자는 **U+FFFD 개수**(우리 0, pywinpty 8).

## 남은 것

`daemon.py` 3,097줄 중 플랫폼에 묶인 **41곳**. 위험한 쪽(PTY·판)은 **판 테스트 29개**가 지킨다.

| 무엇 | 곳 | 어떻게 |
|---|---|---|
| `self.master` 직접 사용 | 15 | 경계 객체로 |
| `add_reader`/`add_writer` | 5 | **제일 큰 조각** — 윈도우는 스레드, 배압은 스레드가 읽기를 멈춰서 |
| `fcntl` (flock 등) | 8 | `msvcrt.locking` |
| 신호 | 6 | `signal.signal` · `TerminateJobObject` |
| `os.kill`/`waitpid` | 7 | Job + `GetExitCodeProcess` |
| 시작 거부 | 1 | `daemon.py` 맨 위의 `sys.platform == "win32"` |

**쪼개는 순서** — 각 단계가 **그 단계만으로 시험을 다 통과해야** 한다:
1. PTY 를 경계 위로 (POSIX 만, 동작 변화 0, 테스트 29개가 지킴)
2. 읽기 경로에 스레드 모델 추가 (POSIX 는 그대로)
3. 잠금·신호
4. 거부 해제 + 윈도우 CI 에서 데몬 전체

## 어떻게 시험하나

```sh
gh workflow run windows-conpty.yml -f sections="steps attached"   # 질문을 고른다
gh run watch
```

`palmar/conpty.py` 나 `dev/conpty-check.py` 를 밀면 자동으로도 돈다. 검사에는 워치독이 있어서
막히면 2분 안에 한 줄 남기고 나간다 — 예전엔 러너 타임아웃까지 15분을 태웠다.

**윈도우 데스크탑이 생기면**: `python dev\conpty-check.py` 가 몇 초다. 그게 이 이식의 비용
대부분(답을 듣는 시간)을 없앤다.

### 이식이 끝나면 따라오는 것 — 시험 매트릭스

**지금 윈도우에서 도는 시험은 0개다.** `tests.yml` 이 매 push 마다 리눅스(3.9·3.13)와 맥(3.13)에서
146개를 돌리는데 윈도우가 빠져 있는 것은 잊어서가 아니라 **못 돌려서**다 — `daemon.py` 31행이
`win32` 에서 자기 import 보다 먼저 `SystemExit` 이라, `test_pure` 조차 모듈을 못 든다.

그래서 **#29 가 사는 것은 데몬만이 아니라 시험 매트릭스 한 줄**이다. 포트가 시험을 다 통과하는 날 같이 할 일:

1. `tests.yml` 의 `matrix.include` 에 `{ os: windows-latest, python: "3.13", skips: ? }` 를 더한다.
   `skips` 는 첫 판을 보고 정한다 — 그 단계가 스킵된 시험 **이름을 다 찍는다.**
2. 셸 단계(`set -eux`, 히어독)가 `bash` 로 도는지 본다. 윈도우 러너의 기본은 `pwsh` 다.
3. `tests/README.md` 의 "윈도우도 여기서 못 돈다" 를 고친다.

**그때도 안 덮이는 것**: 시험은 크롬(Blink)을 몬다. 윈도우 앱이 쓸 WebView2 도 Blink 라 그건
겹치는데, 맥의 **WKWebView** 와 리눅스의 **WebKitGTK** 는 끝까지 안 덮인다(`AGENTS.md` "시험").
