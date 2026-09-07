"""daemon2: master fd 를 받아 daemon1 이 죽은 뒤에도 자식이 살아 있고 대화가 되는지 본다."""
import os, socket, sys, json, time, select, fcntl, termios, struct, signal
sock_path, d1_pid = sys.argv[1], int(sys.argv[2])
c = socket.socket(socket.AF_UNIX)
for _ in range(50):
    try: c.connect(sock_path); break
    except (FileNotFoundError, ConnectionRefusedError): time.sleep(0.05)
msg, fds, _, _ = socket.recv_fds(c, 1024, 4)
child = json.loads(msg)["child_pid"]; master = fds[0]
print(json.dumps({"daemon2_pid": os.getpid(), "received_master_fd": master, "child_pid": child}), flush=True)
def alive(p):
    try: os.kill(p, 0); return True
    except ProcessLookupError: return False
t0=time.time()
while alive(d1_pid) and time.time()-t0 < 5: time.sleep(0.02)
print(f"daemon1 dead after {(time.time()-t0)*1000:.0f}ms: {not alive(d1_pid)}", flush=True)
def drain(seconds):
    out=b""; end=time.time()+seconds
    while time.time()<end:
        r,_,_=select.select([master],[],[],0.05)
        if r:
            try: out+=os.read(master,65536)
            except OSError as e: out+=f"<read error {e}>".encode(); break
    return out
out=drain(1.6).decode(errors="replace")
ticks=[l for l in out.splitlines() if l.startswith("tick")]
print(f"output after daemon1 death (1.6s): {len(ticks)} ticks, GOT-HUP present: {'GOT-HUP' in out}, sample: {ticks[:2]}...{ticks[-1:]}", flush=True)
os.write(master, b"echo HANDOFF-OK-$((1+1))\n")   # bash 는 while 루프 중이라 stdin 을 안 읽는다 → 이 줄은 루프가 끝나야 실행됨. 대신 시그널로 확인
time.sleep(0.3)
# 크기 변경이 되는가
fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
rows, cols, _, _ = struct.unpack("HHHH", fcntl.ioctl(master, termios.TIOCGWINSZ, b"\0"*8))
print(f"TIOCSWINSZ via inherited fd: now {rows}x{cols}", flush=True)
# 자식 종료 감지: 부모가 아니라서 waitpid 불가 → kqueue NOTE_EXIT (macOS) / pidfd (Linux)
kq=None
try:
    kq=select.kqueue(); kq.control([select.kevent(child, select.KQ_FILTER_PROC, select.KQ_EV_ADD|select.KQ_EV_ONESHOT, select.KQ_NOTE_EXIT)], 0)
    print("kqueue NOTE_EXIT registered for orphaned child", flush=True)
except Exception as e: print("kqueue not available:", e, flush=True)
print(f"child alive before kill: {alive(child)}; child parent pid now: ", end="", flush=True)
os.system(f"ps -o ppid= -p {child}")
t1=time.time(); os.kill(child, signal.SIGTERM)
if kq:
    ev=kq.control(None, 1, 3.0); print(f"kqueue exit event after {(time.time()-t1)*1000:.0f}ms: {[(e.ident, hex(e.fflags)) for e in ev]}", flush=True)
time.sleep(0.2); print(f"child alive after SIGTERM: {alive(child)}", flush=True)
# master 마지막 읽기: 자식이 죽으면 EIO
r,_,_=select.select([master],[],[],0.5)
try: print("read after child exit:", os.read(master, 4096)[-60:])
except OSError as e: print("read after child exit -> OSError:", e)
