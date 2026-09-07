# PTY 의 포그라운드 프로세스 그룹을 찾아 SIGSTOP 을 보낸다 — 터미널이 Ctrl+Z 로 하는 그것.
import os, pty, time, select, signal, subprocess
pid, fd = pty.fork()
if pid == 0:
    os.execvpe("/bin/sh", ["/bin/sh", "-i"], {"TERM":"dumb","PATH":"/bin:/usr/bin","PS1":"$ "})
os.set_blocking(fd, False)
def drain(sec):
    n=0; end=time.time()+sec
    while time.time()<end:
        r,_,_=select.select([fd],[],[],max(0,end-time.time()))
        if r:
            try: n+=len(os.read(fd,65536))
            except (BlockingIOError,OSError): pass
    return n
drain(0.6)
os.write(fd, b'sh -c \'i=0; while :; do i=$((i+1)); echo "SEQ $i 0123456789abcdef"; done\'\n')
drain(1.0)
def cpu(p):
    try: return subprocess.check_output(["ps","-p",str(p),"-o","%cpu="]).decode().strip()
    except Exception: return "-"
shell_pgid = os.getpgid(pid)
fg = os.tcgetpgrp(fd)                       # ← 포그라운드 그룹
print(f"  셸 pid={pid} pgid={shell_pgid}  |  PTY 포그라운드 pgid={fg}  → {'같다' if fg==shell_pgid else '다르다 (잡 제어)'}")
print(f"  1. 그대로            {drain(1.5)/1048576:6.1f}MB")
os.killpg(shell_pgid, signal.SIGSTOP)
print(f"  2. 셸 그룹에 STOP    {drain(1.5)/1048576:6.1f}MB")
os.killpg(shell_pgid, signal.SIGCONT)
os.killpg(fg, signal.SIGSTOP)
print(f"  3. 포그라운드에 STOP {drain(1.5)/1048576:6.1f}MB   (자식 CPU {cpu(fg)}%)")
os.killpg(fg, signal.SIGCONT)
print(f"  4. CONT 로 재개      {drain(1.5)/1048576:6.1f}MB")
# 파이썬의 진짜 방법 — 그냥 안 읽는다
print(f"  5. 아무 신호 없이 1.5초 안 읽기 → 커널 버퍼가 차면 자식이 write 에서 막힌다")
time.sleep(1.5)
print(f"     그 뒤 자식 CPU {cpu(fg)}%  (0 이면 막힌 것)")
os.killpg(fg, signal.SIGKILL); os.kill(pid, signal.SIGKILL)
