# alt-screen TUI 는 같은 화면을 계속 다시 그린다. 링버퍼에 담으면 재생 재료가 아니라 쓰레기다.
# 얼마나 빨리 채우는지 잰다 (API 안 쓰는 로컬 TUI 로).
import os, pty, select, time
ALT_ON, ALT_OFF = b"\x1b[?1049h", b"\x1b[?1049l"

def run(cmd, seconds):
    pid, fd = pty.fork()
    if pid == 0:
        os.execvpe("/bin/sh", ["/bin/sh","-c",cmd], {"TERM":"xterm-256color","PATH":"/bin:/usr/bin"})
    os.set_blocking(fd, False)
    buf=bytearray(); alt_bytes=0; in_alt=False; end=time.time()+seconds
    while time.time()<end:
        r,_,_=select.select([fd],[],[],0.05)
        if r:
            try:
                b=os.read(fd,65536)
                if not b: break
            except (BlockingIOError,OSError): break
            if ALT_ON in b: in_alt=True
            if ALT_OFF in b: in_alt=False
            if in_alt: alt_bytes+=len(b)
            buf+=b
    try: os.kill(pid,9); os.close(fd)
    except OSError: pass
    return bytes(buf), alt_bytes

for label, cmd, secs in [
    ("top (진짜 alt-screen)",  "top -s 1", 6),
    ("top -l (다시그리기만)",   "top -l 6 -s 1", 7),
    ("셸 + 보통 명령",         "for i in $(seq 1 60); do echo \"line $i output here\"; sleep 0.05; done", 5),
]:
    out, alt = run(cmd, secs)
    rate = len(out)/secs
    print(f"{label:24} 전체 {len(out):>8,}B  alt-screen 안 {alt:>8,}B ({alt/max(1,len(out))*100:>3.0f}%)  초당 {rate:>7,.0f}B")
    if rate>0:
        for kb in (256,1024):
            print(f"{'':24}   → {kb}KB 버퍼가 {kb*1024/rate:>6.1f}초면 찬다")
