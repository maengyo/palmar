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

| 무엇 | 곳 | 어떻게 | |
|---|---|---|---|
| ~~`self.master` 직접 사용~~ | ~~15~~ | 경계 객체로 | **됐다 (09-14)** |
| `add_reader`/`add_writer` | 5 | **제일 큰 조각** — 윈도우는 스레드, 배압은 스레드가 읽기를 멈춰서 | |
| `fcntl` (flock 등) | 8 | `msvcrt.locking` | |
| 신호 | 6 | `signal.signal` · `TerminateJobObject` | |
| `os.kill`/`waitpid` | 7 | Job + `GetExitCodeProcess` | |
| 시작 거부 | 1 | `daemon.py` 맨 위의 `sys.platform == "win32"` | |

**쪼개는 순서** — 각 단계가 **그 단계만으로 시험을 다 통과해야** 한다:
1. ~~PTY 를 경계 위로~~ **됐다 (2026-09-14)** — 아래를 봐라
2. 읽기 경로에 스레드 모델 추가 (POSIX 는 그대로)
3. 잠금·신호
4. 거부 해제 + 윈도우 CI 에서 데몬 전체

### 2026-09-14: **윈도우에서 돈다.** 터미널이 열리고 붙는다 (사용자 확인)

사용자의 진짜 윈도우에서 확인됐다 — 데몬이 뜨고, 페이지가 뜨고, **디렉터리 레일이 보이고,
터미널이 열리고 연결된다.** 아침에 이 문서는 "멈춤 — 기계가 없어서" 였다.

```powershell
$env:PALMAR_WINDOWS_ANYWAY=1
python -m palmar
```

**여전히 기본으로 켜지 않는다.** 아직 없는 것: 훅 shim 이 셸 스크립트라 **상태가 창 제목만**으로
나고, **터미널을 열 수 있는 곳이 `%USERPROFILE%` 아래뿐**이다(#31 — `roots()` 를 넓힐지는 사람이 정할 것).

#### 실제 기계가 찾은 것 — 러너로는 하나도 못 봤다

| | |
|---|---|
| 디렉터리 레일이 빔 | 루트 목록이 `/` 를 박아 놓음 → 드라이브로 |
| 브라우저가 안 열림 | `browser_argv` 가 네이티브 윈도우에서 `None` |
| `run/key 를 못 읽었다` | 첫 실행엔 없는 게 당연 — 오류처럼 보였다 |
| **제목만 뜨고 빈 창** | `ConPty` 에 `fg_varied` 가 없어 **상태 판정이 터지며 바이트를 먹었다** |
| 키 입력에 연결 끊김 | `ConPty.write` 가 `bytearray` 를 못 받음 (ctypes) |
| `--stop` 이 permission denied | **잠금 바이트에 pid 줄이 있었다** — 강제 잠금이라 못 읽는다 |
| `--stop` 이 "없다" | 잠금만 보고 판단 — **주소가 응답하는지가 직접 증거** |
| `os.O_NONBLOCK` 없음 | `git_branch` 안에 묻혀 있어 레일이 통째로 빔 |

**세 번 반복된 교훈:** 한 번에 하나씩 찾지 말고 **소스에서 이름을 전부 뽑아 플랫폼에 물어봐라**
(`dev/win-daemon-probe.py` 의 `names the source uses that this platform lacks`). 그 한 판이
스무 개를 한 번에 냈고 그중 하나(`signal.SIGKILL`)가 진짜였다.

#### 아직 없는 것: 윈도우 shim — 그리고 그게 **두 가지**를 막는다 (#30)

POSIX 에서 palmar 는 셸의 PATH 앞에 껍데기를 두어 훅을 붙인다(zsh 는 `ZDOTDIR`, bash 는 `--rcfile`).
**윈도우에는 그게 없다.** 그 하나가 빠져서 두 가지가 안 된다:

**① 신호등이 사실상 안 움직인다.** PowerShell 은 창 제목을 자기 exe 경로로 한 번 정하고 안 바꾼다
(`'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'`, 사용자 화면 2026-09-14).
제목이 안 도니 "제목이 도는 것 = 일하는 중" 이 성립하지 않는다. 출력 기반 판정은 남아 있다.

**② `cd` 를 못 따라간다.** `cwd_of` 가 PEB 에서 프로세스 작업 디렉터리를 읽는데,
**PowerShell 의 `Set-Location` 은 그걸 안 바꾼다** — PowerShell 자기 위치만 바꾸고
`SetCurrentDirectory` 를 안 부른다. 그래서 아무리 정확히 읽어도 **시작한 자리**가 나온다.
확인: `cd C:\ ; [System.IO.Directory]::GetCurrentDirectory()` 가 `C:\` 를 안 찍는다.
`cmd.exe` 나 `chdir()` 하는 프로그램에는 제대로 동작한다 — **PowerShell 에만 조용한 것이지 틀린 게 아니다.**

POSIX 의 rc 끼우기에 해당하는 것이 PowerShell 에는 `-NoExit -Command <머리말>` 이다 —
**사용자 프로필을 건너뛰지 않고** 그 뒤에 우리 것을 얹는다. PowerShell 이 프로필을 먼저 돌리고
그다음 `-Command` 를 실행하므로, POSIX 가 rc 래퍼 셋으로 하는 일이 여기서는 **인자 순서**다.
사용자 파일은 안 건드린다(원칙 3).

##### 고침: "둘 다 같은 것이 푼다" 는 절반만 맞았다 (2026-09-17)

여기 원래 "프롬프트가 경로를 창 제목에 쓰게 하면 ①과 ② 가 **둘 다** 풀린다" 고 적혀 있었다.
**②만 풀린다.** 제목에 경로만 쓰면 제목은 **폴더를 바꿀 때만** 변하고, 명령마다 돌지 않는다.
"제목이 도는 것 = 일하는 중" 은 그렇게 안 살아난다. ① 을 제대로 푸는 것은 둘 중 하나다:

- **훅**(정확함) — 머리말에서 `claude` 를 함수로 가려 `--settings` 를 끼운다. 아래 "머리말" 참고.
- **OSC 133**(프롬프트 시작·명령 시작·끝) — 셸이 직접 말한다. `_at_prompt()` 가 `tcgetpgrp` 로
  **추측**하는 것을 사실로 바꾸고, 그래서 **macOS·리눅스에도** 듣는다. 다만 프롬프트에 제어문자를
  끼우는 일이라 zsh 에서 `%{...%}` 를 빠뜨리면 줄 길이 계산이 틀어진다 — 지금 멀쩡한 것을
  망가뜨릴 수 있는 유일한 조각이라 맨 뒤에 둔다.

#### 그리고 알려진 구멍이 아니라 그냥 버그였던 것 둘 (2026-09-17)

`#30` 을 붙이기 전에, 윈도우에서 **이미 조용히 아무것도 안 하고 있던** 코드가 둘 있었다.

- **PATH 구분자가 `:` 로 박혀 있었다** (`daemon.py`, `front_of_path` 이전). 윈도우는 `;` 로 끊으니
  첫 항목이 `C:\Users\…\.palmar\bin;C:\Windows\system32` 라는 **존재하지 않는 경로 하나**가 되고,
  `.palmar\bin` 은 **PATH 에 아예 안 올라갔다.** 거기 무엇을 넣어도 찾힐 수가 없었다.
  고침은 `os.pathsep` 이고, POSIX 에서는 그 값이 원래 쓰여 있던 콜론이라 **한 글자도 안 바뀐다.**
- **shim 파일이 실행될 수 없는 모양이었다.** `~/.palmar/bin/claude` 는 확장자 없는 `#!/bin/sh`
  스크립트다. 윈도우는 shebang 을 모르고 PATHEXT 에 "" 가 없다. 이제 윈도우에서는 안 쓴다 —
  위의 구분자를 고치고 나면 **실행 못 하는 파일이 PATH 맨 앞에 앉아 있게** 되기 때문이다.

#### 머리말 — 지금 들어간 것 (2026-09-17)

`~/.palmar/pwsh/preamble.ps1`. 데몬이 뜰 때마다 다시 쓴다. 지금은 **훅 한 조각**뿐이다.

- **파일이 아니라 함수로 가린다.** PowerShell 은 PATH 보다 함수를 먼저 찾는다. `.cmd` 래퍼가 더
  가까운 대응이지만 **Ctrl-C 마다 `Terminate batch job (Y/N)?`** 가 뜬다 — 에이전트가 도는 pane 에서
  그건 치를 값이 아니다. 함수의 값은 **이 셸 안에서만** 듣는다는 것이다: 스크립트가 부른 claude,
  `cmd.exe` pane, 중첩 셸은 여전히 훅이 없다.
- **자기를 못 찾게 하는 것은 `-CommandType Application` 이다.** POSIX shim 은 PATH 에서 자기
  디렉터리를 고정 문자열로 빼서 같은 일을 하는데 한 번 틀렸다(#12: `.palmar` 의 `.` 이 정규식으로
  읽혔다). 타입을 묻는 쪽은 그렇게 틀릴 수가 없다.
- **`-File` 이 아니다.** `.ps1` 을 도는 것은 실행 정책을 거치고 클라이언트 기본값 `Restricted` 는
  그걸 거절한다. 텍스트로 만든 스크립트블록은 **스크립트 파일이 아니라서** 정책이 할 말이 없다.
  앞의 `.` 은 현재 스코프에서 돌리라는 뜻이고, 그래야 함수가 살아남는다.
- **경로에 작은따옴표**가 있을 수 있다(이름에 아포스트로피가 있으면 충분하다). 두 번 써서 escape 한다.

**시험 여덟** (`tests/test_pure.py` `TheShimOnWindows`). 윈도우 없이 맥에서 도는 것들이다 —
구분자·shim 파일·머리말 본문·인자는 전부 순수 함수로 물어볼 수 있다. **실제로 붙는지는
윈도우에서 봐야 한다.**

#### 그리고 `cd` — 윈도우 문제가 아니었다 (2026-09-17)

②를 고치려고 들여다보다 더 큰 것이 나왔다. **레일이 `cd` 를 안 따라가는 것은 세 OS 전부다.**

`to_json` 이 내주는 `cwd` 는 pane 을 **만들 때** 받은 값이고, 그것을 바꾸는 코드가 없었다. 복구
스냅샷만 `cwd_of(pid)` 로 살아 있는 값을 읽었고 — 즉 **10초마다 읽어서 버리고 있었다.**
실측(맥, 2026-09-17): 하위 폴더로 `cd` 하고 11초 뒤에도 레일은 열었던 폴더 이름을 그대로 말한다.

- **`sample_cwd()` 를 스냅샷 안에서 부른다.** pane 이 자기를 읽고, 스냅샷은 pane 이 말한 것을
  쓴다 — 호출 한 번이 파일과 레일 둘 다에 답하므로 **추가 비용이 없다.** 처음엔 10초 틱에만
  넣었다가 **깨끗한 종료를 깨뜨렸다**(실측 2026-09-17): 종료할 때도 저장하는데 `cd` 몇 초 뒤면
  틱이 아직 안 왔고, 그러면 낡은 값이 파일에 들어간다. `None` 은 답이 아니라 답이 없다는 뜻이라,
  마지막으로 알던 자리를 지우지 않는다.
- **PowerShell 은 그래도 안 읽힌다.** `Set-Location` 이 프로세스 작업 디렉터리를 안 건드리니
  `cwd_of` 가 아무리 정확해도 시작 자리를 답한다. 그래서 **셸이 직접 말한다**: 머리말이 프롬프트에
  `palmar:cwd:<경로>` 를 창 제목으로 쓰게 하고, 데몬의 `_title_cwd` 가 그것만 따로 받는다.
- **그 표식은 "제목이 돈다" 로 세지 않는다.** 폴더가 바뀔 때 한 번 변할 뿐이라 에이전트가 일하는
  것과 전혀 다르고, 그걸 심장박동으로 세면 `cd` 한 번에 불이 "working" 으로 켜진다.
- **이스케이프는 프롬프트 문자열 **밖**으로 쓴다**(`[Console]::Write`). 폭 0 짜리를 반환 문자열에
  넣는 것이 zsh 에서 `%{...%}` 를 빠뜨렸을 때 줄바꿈이 깨지는 바로 그 실수다.

시험 넷(`tests/test_pure.py` `TheFolderAPaneIsIn`)과 둘(`tests/test_daemon.py` `Restore`:
레일이 실제로 따라가는지, pane 이 스스로 말한 자리를 믿는지).

#### OSC 133 — 추측을 그만두는 층 (2026-09-17)

훅 아래의 모든 층은 **추론**이다. 제목이 도는 것은 일하는 중이기를 바라는 심장박동이고, 출력
규칙("계속 찍으면 일하는 중, 찍다 멈추면 끝")은 **조용히 오래 생각하는 것을 끝났다고 하고 로그를
뿜는 것을 바쁘다고 한다.** 셸이 직접 말하면 추측할 것이 없다.

`A` 프롬프트를 그린다 · `B` 입력을 기다린다 · `C` 명령이 시작됐다 · `D` 끝났다.

- **한 pane 이 한 번이라도 말하면 그 pane 에서는 추측이 물러난다** (`shell_marks`). 훅이 제목을
  이기는 것과 같은 규칙이다 — 정확한 쪽이 이기고 추론하는 쪽은 멈춘다. 둘이 번갈아 불을 쓰면
  어느 쪽도 못 믿는다.
- **`D` 가 "끝남"이 되려면 무언가 말했어야 한다.** `C` 다음 바로 `D` 는 빈 줄에 Enter 를 친 것이고,
  거기에 "끝났다" 불을 켜는 것은 아무도 안 시킨 불이다. `_out_tick`·`_title_tick` 이 지키는
  규율과 같다.
- **명령 도중의 `A` 는 아무 뜻도 아니다.** Ctrl-L, 창 크기 변경, 진행 표시줄이 프롬프트를 다시
  그릴 수 있다.
- **훅은 여전히 이긴다.** 이 층은 `derived` 만 쓰고, `eff_status` 는 훅이 조용할 때만 그걸 읽는다.
- **읽는 쪽은 한 정규식이다** (`OSC_SEEN`). 제목과 133 을 한 번에 훑어야 캐리가 자기와 어긋나지
  않는다 — 청크 경계에 걸친 표식도 시험에 있다.

**내보내는 쪽은 아직 윈도우뿐이다.** 머리말이 프롬프트에서 `D`·`A` 를, `PSConsoleHostReadLine`
에서 `B`·`C` 를 낸다. 둘 다 **프롬프트 문자열 밖**이라 줄 길이 계산에 안 끼어든다.
**zsh·bash 는 아직 안 냈다** — 지금 멀쩡히 도는 것을 만지는 유일한 조각이고, zsh 에서 `%{...%}`
를 빠뜨리면 줄바꿈이 깨진다. 데몬 쪽은 이미 준비되어 있으니, 낼 때 rc 만 고치면 된다.

시험 아홉(`tests/test_pure.py` `WhenTheShellSaysItOutright`)과 하나(`tests/test_daemon.py`:
진짜 pane 을 통과하는 전 경로).

##### 첫 실사용에서 나온 것 둘 (2026-09-18, 사용자)

**① 초록불만 뜨고 안 바뀐다.** `C`(명령 시작)는 오는데 `D`(끝)가 안 왔다. `D` 를 내는 것은
프롬프트 래퍼인데 — `PSConsoleHostReadLine` 쪽에만 `Get-Command` 로 존재를 확인하고 **프롬프트
쪽에는 안 걸었다.** `-Command` 가 도는 시점에 `prompt` 가 아직 없으면 `$function:prompt` 가
`$null` 이고, 래퍼가 매번 `& $null` 로 throw 해서 표식이 영영 안 나간다. 감쌀 것이 없으면
기본 프롬프트를 만들어 쓰고, 남의 프롬프트가 던져도 우리 표식은 나가게 했다.

**② 그리고 `D` 가 와도 소용없었다.** 머리말이 `D` 와 `A` 를 한 번에 쓰는데 `A` 가 idle 을 썼다 —
**끝났다는 불이 켜지는 그 숨에 지워졌다.** `A`·`B` 는 이제 상태를 안 쓴다. 프롬프트가 나타나는
것은 그 자체로 상태가 아니고, 명령이 끝나는 것이 상태이며 그것이 `D` 다.

**③ `cd` 는 따라오는데 에이전트를 돌리면 `~` 로 가서 안 돌아온다.** 내가 만든 두 경로가 서로
싸우고 있었다. 프롬프트가 제목으로 올바른 경로를 말하면 레일이 따라가고, **10초 뒤 `sample_cwd`
가 PEB 를 읽어 그걸 덮었다** — `Set-Location` 이 PEB 를 안 바꾸니 읽히는 값은 언제나 **pane 을
연 자리**다. 밖에서 보면 에이전트를 돌린 것이 레일을 집으로 보낸 것처럼 보이지만, 범인은 틱이다.
**한 pane 이 한 번이라도 자기 자리를 말하면 그 pane 은 밖에서 안 읽는다**(`cwd_told`) —
훅이 제목을 이기고 OSC 133 이 추측을 이기는 것과 같은 규칙이다.

#### 잠금 바이트를 다시 바꾸지 마라 — 바꾼다면 이행을 생각해라

새 데몬은 `1 << 30` 번 바이트를 잠근다. **옛 데몬이 도는 동안 새 `--stop` 은 그것을 못 본다** —
다른 바이트를 보니까. 사용자가 작업 관리자로 한 번 끝내야 했다. 바이트를 또 옮기면 같은 일이
또 난다. 옮겨야 한다면 **두 자리를 다 확인하는 이행 코드**를 같이 넣어라.

### (이전) 2026-09-14: 데몬이 윈도우에서 뜨고 페이지를 준다

```
starting a daemon on port 50108
it says it is at http://127.0.0.1:50108
GET / -> 200 15925 bytes · looks like the app
ok  the whole daemon serves a page
```

러너에서 잰 것이다. **써 보려면:**

```powershell
$env:PALMAR_WINDOWS_ANYWAY=1 ; palmar
```

거부 메시지가 이 줄을 알려 준다 — **기본으로 켜지는 않는다.** 아직 없는 것이 있고, 그것을 모르고
쓰는 것보다 알고 쓰는 게 낫다.

**아직 없는 것:**
- **떨어져 나오지 않는다.** 윈도우엔 `fork` 가 없다. `DETACHED_PROCESS` 로 같은 일을 해야 한다
- **훅 shim 이 셸 스크립트다.** zsh/bash 용이라 윈도우에선 안 붙는다 → 상태는 **창 제목만**으로 난다
- **브라우저 열기**를 안 재 봤다
- **`palmar --stop`** 은 된다(탐침이 그 길로 세운다)

### 2026-09-14 에 넘은 벽들 — 기계가 준 순서대로

데몬이 **윈도우에서 import 되고, `~/.palmar` 를 만들고, 잠금을 잡고, pane 을 열고, 바이트를 받는다.**
남은 것은 **읽기 경로 하나**다.

| 벽 | 무엇이었나 |
|---|---|
| `fcntl` | 유일하게 import 자체가 안 되던 것 → `palmar/locking.py` |
| `os.getuid` ×2 | 권한 모델이 다르다 → `POSIX_PERMS`. **흉내 내지 않고 건너뛴다** |
| `$SHELL` 없음 | `CreateProcessW` 실패 → 경계의 `default_shell()` 을 쓴다 |
| `os.O_NOFOLLOW` ×3 | 윈도우에 없는 플래그 → `locking.NOFOLLOW` |
| `os.fchmod` | 없다 → `POSIX_PERMS` 안에서만 |
| **콘솔 인코딩** | 윈도우 콘솔이 cp1252 라 **한국어 메시지를 찍는 순간 죽었다.** stdout/stderr 를 UTF-8 로 |
| `add_signal_handler` | Proactor 에 없다 → `signal.signal` + `call_soon_threadsafe` |
| `SIGCHLD` | **아예 없다** → pane 의 죽음은 콘솔 EOF 로 안다 |
| `die()` 의 회수 루프 | **블로킹 읽기에서 영영 안 돌아온다** — 판 하나를 6분 타임아웃으로 날렸다 |

**세 번 헤맨 자리: 잠금.** `acquire_single_instance_lock` 이 `PermissionError` 로 실패해서 세 판 동안
`msvcrt.locking` 을 의심했다. **네 가지 변형을 러너에 물어보니 전부 됐다**(빈 파일 0번 바이트 포함).
원인은 **탐침이 그걸 두 번 부른 것**이었다 — `setup_palmar_dir` 이 안에서 이미 잡고 `LOCK_FH` 가
데몬이 사는 동안 쥐고 있으니, 같은 프로세스가 다시 잡으려다 거부당하는 게 맞다.
**교훈: "안 된다" 를 세 판 의심하기 전에, 되는 것부터 물어봐라.**

### 1단계 — 한 일 (2026-09-14)

`daemon.py` 에서 **`self.master` 가 0개**가 됐다. `Pane` 은 이제 `self.pty` 하나만 쓴다.

- `pty.fork`·`os.read`·`os.write`·`os.close`·`os.tcgetpgrp`·`TIOCSWINSZ` 가 전부 `palmar/posixpty.py`
  안으로 들어갔다. `daemon.py` 에서 **`import pty` 와 `import termios` 가 사라졌고**, `set_winsize`
  헬퍼도 지웠다(경계 안에 같은 것이 있다).
- **fd 상속 차단(`os.set_inheritable`)도 경계 안으로 옮겼다.** 이건 2026-09-09 의 보안 고침이라
  — 안 옮기면 윈도우 쪽 `spawn` 에는 그 보장이 없는 채로 남는다. 주석도 통째로 같이 옮겼다.
- `fg_is_shell` 만 **번역이 필요했다.** 경계는 "모른다" 를 `None` 으로 주는데(윈도우엔 포그라운드
  프로세스 그룹이 아예 없다), 데몬의 이 호출은 예전부터 **모르는 것을 `False`** 로 읽어 왔다.
  `bool()` 한 번으로 맞췄다 — 동작을 바꾸지 않으려고.
- **동작 변화 0.** 시험 162개가 3.9 와 3.13 둘 다에서 통과한다.

**남은 것 중 제일 큰 조각은 2단계다.** `add_reader` 는 윈도우에 대응물이 없다 — 소켓이어도
`NotImplementedError` 다(잰 것, 위 표). 읽기가 스레드로 가야 하고, **배압을 그 스레드가 읽기를
멈추는 것으로** 표현해야 한다. POSIX 는 지금 모양 그대로 둔다(`blocking = False` 가 그 분기다).

## 그 기계에서 지금 할 수 있는 것 — `install.ps1` (2026-09-14)

사용자가 **"powershell 명령어와 bypass 로 설치 가능하게"** 라고 했다. 사내망 기계에서 스크립트 실행이
정책으로 막혀 있어도 `-ExecutionPolicy Bypass` 는 **그 프로세스 하나에만** 적용되고 설정을 안 바꾸며
관리자도 필요 없다. 마이크로소프트 스스로 **보안 경계가 아니라고** 말하는 물건이라, 정책이
`MachinePolicy` 로 걸려 있어도 대개 통과한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Check   # 보기만 한다. 아무것도 안 쓴다
powershell -ExecutionPolicy Bypass -File .\install.ps1          # ...그다음 묻고 쓴다
```

**기대를 정확히 맞춰 둔다. 이걸로 palmar 가 윈도우에서 돌지는 않는다.** 데몬은 여전히 `win32` 에서
나간다(#29). 설치되는 것은 **그 포트가 끝나는 날 동작할 런처**고, 오늘 값어치 있는 것은 `-Check` 가
내는 **한 화면짜리 보고**다 — 이 문서 맨 위가 "기계가 없어서 멈췄다" 인데, 그 기계에 무엇이 있는지를
바로 그 보고가 답한다.

`-Check` 가 재는 것: PowerShell 판·에디션 · **실행 정책을 범위별로 전부**(어느 범위가 막는지가 보인다) ·
WSL 과 배포판 · **파이썬 3.9 이상** · 체크아웃인지.

**파이썬 찾기는 두 번 틀렸다. 둘 다 적어 둔다.**

**① PATH 만 보면 못 찾는다** (사용자, 2026-09-14 — 첫 판이 "없다" 고 했는데 있었다).
윈도우 설치 마법사의 **"Add python.exe to PATH" 는 기본이 꺼짐**이다. 그래서 멀쩡한 파이썬이
`Get-Command` 에 안 잡히는 게 정상이다. 이제 넷을 본다:

1. PATH (`py` 런처 먼저 — 설치된 모든 판을 안다)
2. **레지스트리** — `HKCU`/`HKLM` 의 `SOFTWARE\Python\PythonCore`·`ContinuumAnalytics`.
   **설치 프로그램이 스스로 적는 자리라 이쪽이 정본**이고 PATH 체크박스와 무관하다
3. 흔한 설치 폴더 — `%LOCALAPPDATA%\Programs\Python`, `C:\`, `%USERPROFILE%\anaconda3` 등
4. `C:\Windows\py.exe` — PATH 를 비워도 여기 있다

그래도 못 찾으면 **어디를 봤는지 찍고** `-Python "C:\...\python.exe"` 로 짚어 줄 수 있다고 말한다.
"없다" 한 줄만 남기면 읽는 사람이 갈 데가 없다.

**② 길이 0으로 스토어 껍데기를 가리려 했는데 틀렸다.** `WindowsApps\python.exe` 는 **진짜로 설치된
스토어 파이썬도 0바이트**다(둘 다 앱 실행 별칭이다). 길이로는 구분이 안 된다 — 그래서 처음 판에서는
멀쩡한 스토어 파이썬까지 건너뛰었을 것이다. **실행해서 버전을 찍는지로만 판단한다.**

그리고 보고 끝에 **이 포트를 푸는 한 줄**을 찍는다:

```
    <파이썬> dev\conpty-check.py
```

**PowerShell 이 돈다면 저것도 돈다.** 그러면 #29 가 "기계가 없다" 에서 "시간이 필요하다" 로 바뀐다.

시험은 `tests/test_install.py` 의 `InstallPs1` 일곱 개다. 맥에서도 PowerShell Core 로 돈다(없으면 스킵).
**여기서 못 보는 것**: 스토어 껍데기(윈도우에만 있다)와, 진짜 그룹 정책을 bypass 가 통과하는지.
그 둘은 그 기계가 답한다.

## WSL 옆의 윈도우 (2026-09-15, 사용자 실측)

윈도우에서 palmar 가 도는 채로 WSL 우분투에서 `curl … | sh` 로 설치하고 `palmar` 를 치면 세 가지가
났다. 셋 다 고쳤고, 셋 다 이 기계 없이는 못 재는 것이라 순수 함수로 떼어 `tests/test_pure.py` 에서 잰다.

- **같은 포트, 다른 루프백.** WSL2 의 bind 는 성공하는데 윈도우 브라우저는 윈도우 palmar 를 만나
  "키가 틀리다" 페이지를 봤다. 이제 묶기 전에 윈도우의 `netstat.exe` 를 interop 으로 돌려 윈도우가
  쥔 포트를 피하고, 다음 빈 포트로 간다 — 시작 안내가 그렇게 말한다. 키가 틀린 페이지도 이제 "다른
  palmar 의 주소" 라고 말한다. (`networkingMode=mirrored` 면 bind 자체가 실패하고, 같은 길로 간다.)
- **브라우저가 안 열림.** `powershell.exe`/`cmd.exe` 를 이름으로만 찾았는데, 윈도우 PATH 를 붙이지
  않은 배포판(`appendWindowsPath=false`)에서는 이름이 안 풀린다. `/proc/mounts` 에서 C: 마운트를 찾아
  `…/Windows/System32/` 의 전체 경로로도 찾는다. `wslview`(wslu)가 있으면 그것이 먼저다.
- **윈도우 쪽 판이 WSL 데몬에 나타남.** 브라우저의 `localStorage` 가 origin 단위라 한 포트의 두
  데몬이 한 저장소를 썼고, 판 복사본이 빈 데몬에 넘어갔다. 복사본을 없앴다 (decisions ③).

## 잰 것 — 2026-09-16, 사용자 기계

- `icacls %USERPROFILE%\.palmar` → `NT AUTHORITY\SYSTEM`, `BUILTIN\Administrators`, `OWNER RIGHTS` 모두
  `(OI)(CI)(F)`, 그 외 없음. **기본 ACL 로 충분하다** — 다른 표준 계정은 키·토큰을 못 읽는다. 별도 권한 관리는
  넣지 않고, `--doctor` 가 `Everyone`/`Users`/`Authenticated Users` 를 보면 한 줄 경고한다(코덱스 제안 3).
- `--doctor`: `default chrome`, `window chrome.exe in app mode` — 레지스트리 UserChoice 읽기가 맞다. "앱 설치"
  뒤에는 `window …\palmar.lnk`. Chrome 은 `--app=` 주소를 설치된 앱 창으로 옮기며 **두 번** 받는다(일회용 →
  30초 주소로 바꾼 이유).
- 새 런처(`launch.py`)가 3.13 에서 `ModuleNotFoundError` — PYTHONSAFEPATH 가 스크립트 디렉터리도 뺀다. 고쳤다.
- **WSL 옆의 윈도우, 실측 통과.** 윈도우 palmar 를 켜 둔 채 Ubuntu 에서 새로 설치하고 `palmar`: "8801 is held on
  the Windows side" 를 보고 **8804** 로 갔고(8802·8803 도 쥔 것이 있었다), 판은 비어 있었고(윈도우 쪽 판이 안
  넘어옴), 경로는 WSL 것이었다. 09-15 의 세 버그(같은 포트·같은 localStorage·윈도우 PATH 없음)가 그 기계에서 닫혔다.
- 다른 계정에서 명령줄이 보이는가(`/once/` 경합)는 **미측정** — 그 기계에 두 번째 계정이 없다. 이론(윈도우는 다른
  계정의 명령줄을 관리자 없이는 못 읽는다)만 있다.

- **둘째 `palmar` 가 창을 앞으로, `--new` 가 하나 더 — 통과** (bcd1001). 네이티브 PowerShell 에서
  `$env:PALMAR_WINDOWS_ANYWAY=1` 을 세우고 `python -m palmar` 로 봤다. 창이 떠 있는 채로 한 번 더 치면
  새 창 없이 그 창이 앞으로 왔고, `--new` 를 붙이니 같은 데몬에 창이 하나 더 떴다. OS 단계로 창을
  올리는 길(`raise_windows_window` — 창 클래스와 제목으로 찾아 포그라운드로)은 시험이 없으니 이것이
  유일한 근거다. WSL 쪽 길(PowerShell `AppActivate`)은 **아직 미확인**이다.
- **`--stop` 은 정상이다.** `asked pid # to stop; waiting... stopped` 가 나오고 캔버스 안의 터미널이
  전부 사라졌다 — ⑦=b 가 말하는 그대로다. ce926a5 의 커밋 메시지에 남아 있던 "`--stop` 이 데몬을
  멈추지 못한 게 분명하다" 는 관찰은 **재현되지 않았다**. 그때는 앱을 막 지운 직후였다.
- **앱 모드 창이 거의 정사각으로 뜬다 — 크로미엄의 기본 크기 규칙이다.** 창 안 콘솔이 준 것:
  `outerWidth x outerHeight = 1302x893`, `availWidth x availHeight = 1536x912`, `devicePixelRatio 1.125`.
  크로미엄은 저장된 배치가 없는 창의 폭을 **1050 DIP 에서 자르고** 높이는 작업 영역에서 20 만 뺀다
  (Aura 의 `kWindowMaxDefaultWidth`·`kWindowTilePixels`; **소스를 읽은 것이고 윈도우에서 잰 것이 아니다**).
  대입하면 `min(1536-20, 1050) x (912-20) = 1050x892`, **1.18 대 1**. 위의 높이 893 이 그 892 와 맞는다 —
  폭 1302 는 사용자가 이미 옆으로 끌어 놓은 뒤라 기본값이 아니다. **폭을 절반으로 접는 분기는 안
  걸렸다**: 그것은 작업 영역 폭이 1600 DIP 를 넘어야 하는데 이 화면은 1536 이다. 창을 넓혀 놓고 닫았다
  다시 열면 **정사각으로 돌아온다** — 배치가 안 남는다. 참고로 palmar 자기 창은 1280x820 이고
  (`app/src/main.rs`), 페이지 최소 폭은 1120 이라 1050 에서는 오른쪽 레일이 잘리고 가로 스크롤이 생긴다.

**고친 것 (2026-09-16).** 둘을 같이 넣었다. ① 페이지가 처음 열릴 때 **한 번만** 스스로 폭을
넓힌다(`sizeWindowOnce`, `palmar/web/app.js`) — 1280 DIP 와 작업 영역 중 좁은 쪽까지, **절대 줄이지
않고**, 앱 창일 때만(탭은 `resizeTo` 를 무시하고도 받아들인 척 보고한다). 시작 메뉴 PWA 에는 palmar
가 인자를 못 붙이므로 두 경로에 다 닿는 것은 이것뿐이다. ② `--app=` 주소를 `http://localhost:PORT/once?n=…`
로 바꿨다(`loopback_name`, `launch_target`) — 경로가 매번 같아지고 호스트에 점이 없어서, 사람이 옆으로
끌어 놓은 크기를 크로미엄이 다음에도 기억한다. 옛 `/once/<nonce>` 도 계속 받는다. **윈도우에서는 아직
안 봤다** — 맥의 크롬 앱 창에서 `resizeTo` 가 듣는 것만 실측했다.

## 창 — exe 없이 (2026-09-15)

윈도우 창은 우리 exe 가 없어도 된다: `palmar` 는 `palmar-app` 이 없으면 **기본 브라우저를 `--app=`
모드**로 연다 — 레지스트리 UserChoice 의 ProgId 로 읽고, 그것이 Chrome 이면 Chrome, Edge 면 Edge
(Firefox 면 앱 모드가 없어 Chrome → Edge). 실측 2026-09-15: Edge 앱 모드 창이 떴다. 탭도 주소창도 없는
창이고, 회사 PC 처럼 exe 를 못 받는 곳에서 바로 되는 길이다. WSL 의 데몬도 같은 Edge 를 C: 마운트
너머로 연다. 탭이 필요하면 창 안의 web 버튼이나 `palmar --web`. 러너로는 못 잰다 — 경로 선택은
`tests/test_pure.py` 의 순수 함수로, 실제 열림은 그 기계에서 확인해야 한다.

WSL 의 데몬도 같은 순서다: 윈도우 쪽에 설치한 앱(`cmd.exe /c echo %APPDATA%` 로 찾는 시작 메뉴의
`palmar.lnk`) → 윈도우 쪽 크로미엄 앱 모드 → WSLg 의 palmar 창 → 윈도우 탭.

한 단계 더: Edge 에서 palmar 를 열고 **메뉴 → 앱 → "palmar 설치"** 를 한 번 누르면 PWA 가 된다 —
자기 아이콘, 시작 메뉴 항목, 브라우저 UI 없는 창. 데몬이 매니페스트와 아이콘을 내므로 다른 것은
필요 없고, 그 뒤 `palmar` 는 `%APPDATA%\Microsoft\Windows\Start Menu\Programs\palmar.lnk` 를 찾아
그것을 연다(Chrome 은 `Programs\Chrome Apps\`). 포트가 옮겨진 채로 설치했다면 그 주소로 굳는다.

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
