# palmar

*Read this in [English](README.md).*

**터미널 여럿을 한자리에. 각각은 놓아둔 자리를 지킨다.**

![palmar: 캔버스 위의 터미널 셋. 각각 불이 하나씩 — 일하는 중, 나를 기다림, 끝남. 그중 둘을 끌어다 묶으면 같이 움직이고, ⌥ 를 누른 채 끌면 하나가 빠져나온다. 마지막으로 PDF 가 자기 창으로 열린다](docs/img/palmar.gif)

> palmar 는 모델을 돌리지 않고, API 를 부르지 않고, 당신의 작업을 어디로도 보내지 않는다.
> 셸을 열고 그것들이 어디 있는지 보여줄 뿐이다. 에이전트는 당신이 이미 쓰던 그것들이다.

## 왜

**무엇이 당신을 기다리는지 보인다.** 창마다 신호등이 있다 — 일하는 중·당신을 기다림·끝남·놀고 있음.
**설치할 것도 설정할 것도 없다.** palmar 가 에이전트의 훅을 스스로 붙인다 — macOS·리눅스에서는
셸 앞에 껍데기를 두고, 윈도우에서는 PowerShell 에 머리말을 얹는다. 훅이 없는 에이전트는 창
제목으로, 제목도 안 쓰면 셸이 실제로 찍는 것으로 읽는다.
당신의 설정 파일은 건드리지 않는다. 가장 오래 기다린 것이 목록 맨 위에 온다. 그 창이 화면 밖에
있어도 그렇다.

**창은 놓아둔 자리에 있는다.** 끌어 옮기고 크기를 바꾸면 셸의 행·열이 따라온다. 타일링이 아니고,
몰래 크기를 바꾸지도 않는다. 창끼리 겹치지 않는다 — 하나를 다른 것 위에 놓으면 겹친 만큼만 비켜난다.
잠깐 올려두고 있으면 함께 움직이는 **그룹**이 된다. 무엇을 하든 `Ctrl`/`⌘`+`Z` 한 번으로 되돌린다.

**당신 없이도 계속 돈다.** 셸은 데몬이 갖고 있다. 탭을 닫아도, 노트북을 덮어도 그대로다. 같은 주소로
돌아오면 두고 간 그대로 — 자리도 크기도 그룹도. 배치가 브라우저가 아니라 **데몬에** 있기 때문이다.

**파일이 바로 옆에 있다.** 캔버스 옆 레일이 지금 일하는 폴더다. 파일도 다른 창처럼 열린다 —
줄 번호가 붙은 텍스트, CSV 는 표, 웹 문서, PDF. 텍스트는 그 자리에서 고치고 저장한다. 그 사이에
다른 것이 그 파일을 썼다면, 이기는 대신 그렇다고 말해 준다. Finder 나 파일 탐색기에서 끌어다
놓으면 놓은 자리에 열리고, 제목줄의 버튼 하나가 그 파일을 기계가 원래 열었을 프로그램에 넘긴다.

**파이썬 말고는 설치할 것이 없다.** 표준 라이브러리와 함께 담아 둔 xterm.js 한 벌이 전부다.
빌드도, 패키지도, 실행 중 네트워크도 없다 — 옵션 목록의 **Tell me about new versions** 를 켜기
전까지 palmar 는 스스로 여는 연결이 하나도 없고, 그 스위치는 처음에 꺼져 있다.

## 설치

한 줄. 필요한 것은 파이썬뿐이고, 없으면 설치 스크립트가 구해 온다 — 리눅스는 패키지 관리자로(먼저
묻는다), 윈도우는 winget 으로. macOS 에는 `/usr/bin/python3` 가 있다.

macOS · Linux:

```
curl -fsSL https://raw.githubusercontent.com/maengyo/palmar/main/install.sh | sh
```

윈도우, PowerShell 이나 cmd 에서 — 스크립트를 지금 폴더에 받아 `-ExecutionPolicy Bypass` 로 돌린다.
정책은 바뀌지 않고, 파일은 남아 있어 나중에 읽어 볼 수 있다:

```
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/maengyo/palmar/main/install.ps1 -OutFile install.ps1; .\install.ps1"
```

먼저 읽어 보고 돌리거나 `-Check` / `-Prefix` / `-Yes` 를 주려면 둘로 나눈다: `irm … -OutFile` 까지, 그다음
`powershell -ExecutionPolicy Bypass -File .\install.ps1`.

트리를 받아 두고, `palmar` 실행기를 놓고(`~/.local/bin`, 윈도우는 `%LOCALAPPDATA%\palmar\bin`),
PATH 에 한 번 올리고, 아무것도 시작하지 않는다. root 도 빌드도 없다. 먼저 읽어 보고 싶으면
`git clone https://github.com/maengyo/palmar && sh palmar/install.sh` 가 눈앞의 체크아웃을 설치하고,
`python3 -m palmar` 는 아무것도 설치하지 않고 돌린다. 윈도우는 네이티브(ConPTY)이고, 아직 거친
부분은 [`docs/windows.md`](docs/windows.md) 에 있다.

## 실행

```
palmar
```

주소를 찍고, 창을 열고, **바로 돌아온다**. 맥에서는 palmar 의 창이 깔려 있으면 그것이고, 다른
곳에서는 크로미엄 계열 브라우저의 앱 모드(기본 브라우저가 크로미엄 계열이면 그것; 윈도우엔 Edge 가
늘 있다)이며, palmar 의 창은 크로미엄이 없는 리눅스의 몫이다. 브라우저 탭은 마지막 수단이다. exe 없이 제 프로그램으로
만들려면 Edge 나 Chrome 에서 한 번 앱으로 설치한다(메뉴 → 앱 → palmar 설치): 자기 아이콘·창·시작
메뉴 항목이 생기고, 그 뒤로 `palmar` 는 그것을 연다 — 데몬은 떨어져
나가므로 터미널을 닫아도 셸이 같이 죽지 않는다. 다시 `palmar` 를 치면 열린 창이 앞으로 온다 — 같은 데몬, 같은 주소. `palmar --new` 는 창을 하나 더 열고,
`palmar --stop` 이 끝낸다.
주소를 잃었으면 `cat ~/.palmar/run/url`. 8801 이 잡혀 있으면 — 윈도우의 palmar 옆에 WSL 의 palmar
같은 경우 — 다음 빈 포트를 잡고 그렇다고 말한다.

창은 같은 것을 브라우저 없이 보여 주는 **741 KB** 짜리 프로그램이다([`app/`](app/README.md)) — OS 가
이미 가진 웹뷰를 쓰고, Electron 이 아니다. 창이 있으면 `palmar` 가 창을 열고, 브라우저는 창 안의
web 버튼이나 `palmar --web` 이 연다. 둘을 같이 써도 된다: 데몬은 HOME 당 하나이고, 한쪽에서 연
터미널이 다른 쪽에도 바로 나타난다.

## 알아 둘 것

**터미널 안에서 도는 것은 palmar 를 조종할 수 있다.** 유닉스 권한은 *프로그램*이 아니라 *사용자*를
가른다. palmar 가 여는 셸은 당신으로 돌기 때문에, 한 판의 스크립트가 다른 판에 붙어 승인 질문에
대신 답할 수 있다. 당신으로 도는 것은 이미 당신의 SSH 키와 셸 시작 파일을 갖고 있다 — 다만 palmar 는
그것을 쉽고 구체적으로 만들고, 승인 질문은 사람이 답하라고 있는 것이다. 진짜로 막으려면 판마다 OS
경계가 필요한데, 그러면 palmar 는 다른 프로그램이 된다.

**열쇠는 주소 안에 있다.** 북마크가 재시작을 넘어 살아남는 값이다. 그 값의 전부와, 아직 안 만든
것들은 [`docs/decisions.md`](docs/decisions.md) 에 있다.

## 이름

*palmar* 는 "손바닥의" 라는 뜻이다 — 터미널 전부를 손바닥 안에. 일주일은 *palmer* 였는데, 그건 성씨이고
뜻에서 한 글자 빗나간 이름이고 PyPI 에도 이미 있었다. *palmar* 는 어디서나 비어 있었다.

## 나머지

- [`AGENTS.md`](AGENTS.md) — 작업 규약. 무엇을 적기 전에 어떻게 확인하는가.
- [`docs/protocol.md`](docs/protocol.md) — 데몬과 브라우저 사이의 계약.
- [`docs/decisions.md`](docs/decisions.md) — 정해진 것과, 일부러 안 정한 것.
- [`docs/roadmap.md`](docs/roadmap.md) — 일의 순서.
- [`docs/reports.md`](docs/reports.md) — 무엇이 보고되었고, 원인이 무엇이었고, 무엇이 아직 열려
  있는가. **재현 못 한 것도 적는다** — 그런 목록의 쓸모는 대개 거기에 있다.

살아 있는 목록은 [issues](https://github.com/maengyo/palmar/issues).

## 라이선스

MIT — [`LICENSE`](LICENSE). 이 저장소의 유일한 남의 코드는 `palmar/web/vendor/` 의 xterm.js 이고,
실행 중에 아무것도 받아오지 않으려고 같이 담아 두었다. 고지는 그 옆
[`LICENSE-xterm`](palmar/web/vendor/LICENSE-xterm) 에 있다.
