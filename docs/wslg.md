# WSLg 에서 palmar 창 띄우기

윈도우에서 **palmar 를 하나의 프로그램으로** 쓰는 길이다. WSLg 가 리눅스 GUI 프로그램을 윈도우
화면에 그려 주므로, Obsidian 같은 다른 앱과 똑같이 창 하나로 뜬다.

웹 버전(`python3 -m palmar` + 브라우저)은 이 문서의 것이 **하나도 필요 없다.** 툴체인도 패키지도
안 쓴다. 창이 필요할 때만 읽어라.

> **아직 안 잰 것이 있다.** 여기 적힌 건 2026-09-11 에 사용자 한 명의 WSLg 기계에서 실제로 돌려
> 본 것이다(표본 1). 배포판이나 WSL 판이 다르면 다를 수 있다.

---

## 0. 먼저 점검한다

체크아웃 맨 위에서, WSL 안에서:

```sh
sh dev/wslg-probe.sh
```

**아무것도 설치하거나 바꾸지 않는다.** 다섯 구간을 보고 처음 막힌 곳을 짚어 준다. 아래 단계 중
어디쯤 있는지 이게 말해 주므로, 막힐 때마다 다시 돌려라. 키도 토큰도 찍히지 않으니 출력을 그대로
남에게 보여도 된다.

## 1. WSLg 가 있나

윈도우 11, 또는 윈도우 10 21H2 이상에서 스토어 판 WSL 이 필요하다. PowerShell 에서:

```powershell
wsl --update
wsl --shutdown
```

그리고 배포판을 다시 연다. 확인:

```sh
ls -d /mnt/wslg && echo "$WAYLAND_DISPLAY / $DISPLAY"
```

**창이 뜨는지부터 확인해라.** palmar 를 탓하기 전에:

```sh
sudo apt install -y x11-apps && xeyes
```

여기서 아무 창도 안 뜨면 그 아래는 전부 무의미하다. WSLg 부터 고쳐야 한다.

## 2. 데몬 쪽 (빌드 없음)

`python3 -m palmar` 는 표준 라이브러리와 벤더링된 xterm.js 뿐이다. 파이썬 3.9 이상만 있으면 된다.

```sh
python3 -m palmar --doctor
```

`WSL wslg` 라고 나오면 감지가 맞은 것이다. `wsl` 이나 `no` 면 1번으로 돌아가라.

> **`/mnt/c` 밑에 클론하지 마라.** 윈도우 파일시스템에서 빌드가 몇 배 느리다. WSL 홈에 두어라.

## 3. 창 빌드

```sh
sh app/setup-linux.sh          # 무엇을 할지 보여주고 물어본 뒤 설치한다
sh app/setup-linux.sh --contained   # Rust 를 홈이 아니라 app/.rust 안에만 둔다
```

직접 하려면:

```sh
sudo apt install -y libwebkit2gtk-4.1-dev build-essential pkg-config curl
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
. "$HOME/.cargo/env"           # rustup 은 지금 이 셸을 안 바꾼다
```

**4.1 이지 4.0 이 아니다.** Ubuntu 22.04+ · Debian 12+ 에만 있다. 20.04 · Debian 11 에는 없고,
그 경우 창은 포기하고 웹 버전을 쓰면 된다.

그리고:

```sh
cd app && cargo build --release
./target/release/palmar-app
```

실행파일 하나, 약 700KB. **Rust 는 빌드에만 필요하다** — 다 만들고 나면 지워도 창은 돈다
(`rm -rf app/.rust app/target`). 빌드조차 하기 싫으면 CI 가 지어 둔 것을 받으면 된다 —
`app/README.md` 의 "Getting one without building it".

## 4. 한글

**표시와 입력은 다른 문제다.** 따로 고쳐야 한다.

### 표시 — 폰트

Ubuntu WSL 기본 이미지에는 **한글 폰트가 하나도 없다**. 한글이 네모박스로 보이면 이것이다.

```sh
fc-list :lang=ko          # 비어 있으면 없는 것
sudo apt install -y fonts-naver-d2coding     # 터미널용 고정폭, 약 8MB
```

일본어·중국어까지 필요하면 `fonts-noto-cjk`(약 89MB). 설치 뒤 **창을 껐다 켜라** — fontconfig 는
프로세스마다 캐시해서, 돌고 있는 창은 새 폰트를 못 본다.

### 입력 — IME

**윈도우 한글 IME 는 WSLg 에서 쓸 수 없다.** 설정을 잘못해서가 아니라, WSLg 의 컴포지터가 RDP
유니코드 키보드 이벤트를 버리기 때문이다 — 조합된 글자가 리눅스로 넘어올 통로 자체가 없다
(microsoft/wslg#9, 2021 년부터 열려 있다). 윈도우 쪽 설정을 뒤지지 마라. 리눅스 IME 를 깐다.

```sh
sudo apt install -y ibus ibus-hangul ibus-gtk3 dbus-x11
```

WSL 세션마다 한 번, **창을 띄우기 전에**:

```sh
eval "$(dbus-launch --sh-syntax)"    # WSLg 는 세션 버스를 안 준다
ibus-daemon -drx
ibus engine hangul
```

`~/.bashrc` 에 넣어 두면 매번 안 쳐도 된다. **`GTK_IM_MODULE` 은 넣지 마라** — 시작 메뉴로 창을
띄우면 셸 파일을 안 읽으므로, palmar 가 프로그램 안에서 직접 건다.

한/영 전환은 **Shift+Space**.

- `ibus engine hangul` 이 `execute setxkbmap failed` 라고 해도 무해하다. 거슬리면
  `sudo apt install -y x11-xkb-utils`.
- 버스 없이 창을 띄우면 palmar 가 그렇다고 말해 준다.

## 5. 그래서 안 되면

창에서 **`?` → "Record typing (15s)"** → 터미널을 클릭하고 한글을 쳐라. 15 초 뒤 맨 첫 줄에
`VERDICT:` 한 줄이 나온다. **개발자 도구가 필요 없고, 그 한 줄만 읽어 주면 된다** — 사내망이라
아무것도 복사해 나갈 수 없는 기계에서도 쓸 수 있게 그렇게 만들었다.

| 증상 | 어디를 보나 |
|---|---|
| 한글이 네모박스 | 폰트 (4번) |
| 한글이 아예 안 쳐짐 | IME (4번). 먼저 `GTK_IM_MODULE=ibus gedit` 같은 다른 GTK 앱에서 되는지 봐라 |
| 창은 뜨는데 까맣다 | 알려 달라. `WEBKIT_DMABUF_RENDERER_FORCE_SHM=1` 은 palmar 가 이미 건다 |
| 타자가 느리다 | 상태바 오른쪽 아래가 `dom` 이면 WebGL 이 안 잡힌 것이다 |
| 창이 아예 안 뜸 | 1번의 `xeyes` 부터 |

**`LIBGL_ALWAYS_SOFTWARE=1` 이나 `GALLIUM_DRIVER=llvmpipe` 는 쓰지 마라.** 시작할 때 나오는 Mesa
경고의 해법처럼 보이지만, WSLg 자체 d3d12 드라이버를 목록에서 빼 버린다 — 조용함과 GPU 를
맞바꾸는 셈이다. 그 경고들은 EGL 초기화가 재시도 사다리를 걷는 소리이고 마지막 시도는 조용히
성공한다. palmar 가 알아서 입을 막아 둔다.

## 6. 끄기

**창을 닫아도 데몬은 안 죽는다.** 데몬이 살아 있는 셸을 쥐고 있고, 그래서 창을 닫아도 작업이
안 죽는다. 정말로 멈추려면:

```sh
palmar --stop        # 또는 체크아웃에서  python3 -m palmar --stop
```
