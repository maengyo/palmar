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
나고, **터미널을 열 수 있는 곳이 `%USERPROFILE%` 아래뿐**이다(`roots()` 를 넓힐지는 사람이 정할 것).

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
