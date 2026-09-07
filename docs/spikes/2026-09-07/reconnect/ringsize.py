# ⑨ 의 남은 질문: 링버퍼를 pane 당 얼마나 들 것인가.
# 셸 pane 은 바이트 재생이 곧 화면이므로, "몇 바이트면 화면 N개를 되살리나" 를 잰다.
import os, pty, select, time, sys

def run(cmd, seconds=6.0, cols=100, rows=30):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe("/bin/sh", ["/bin/sh", "-c", cmd],
                   {"TERM": "xterm-256color", "PATH": "/bin:/usr/bin:/usr/local/bin"})
    buf = bytearray(); end = time.time() + seconds
    os.set_blocking(fd, False)
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                b = os.read(fd, 65536)
                if not b: break
                buf += b
            except (BlockingIOError, OSError): break
    try: os.kill(pid, 9); os.close(fd)
    except OSError: pass
    return bytes(buf)

CASES = [
    ("git log --oneline -100",        "git -C ~/ddul/python/palmer log --oneline -100"),
    ("ls -la /usr/bin",               "ls -la /usr/bin"),
    ("색 있는 빌드 로그 흉내",          "for i in $(seq 1 200); do printf '\\033[32m ok \\033[0m module_%s compiled\\n' $i; done"),
    ("git diff (색 포함)",             "git -C ~/ddul/python/palmer diff HEAD~3 --color=always"),
    ("python 역추적 반복",             "for i in $(seq 1 40); do python3 -c 'raise ValueError(\"x\")' 2>&1; done"),
]
print(f"{'무엇':28} {'바이트':>9} {'줄':>6} {'줄당':>6} {'100줄당':>9}")
print("-"*66)
tot=[]
for label, cmd in CASES:
    out = run(cmd)
    n = len(out); lines = out.count(b"\n") or 1
    per = n/lines
    tot.append(per)
    print(f"{label:28} {n:>9,} {lines:>6,} {per:>6.0f} {per*100:>9,.0f}")
avg=sum(tot)/len(tot)
print()
print(f"평균 줄당 {avg:.0f}바이트  →  30줄 화면 하나 ≈ {avg*30:,.0f}B")
for kb in (256, 512, 1024, 4096):
    print(f"  {kb:>5}KB 링버퍼 → 약 {kb*1024/avg:>7,.0f}줄 = 화면 {kb*1024/avg/30:>5,.0f}개")
