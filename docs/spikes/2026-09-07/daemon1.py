"""daemon1: PTY 자식을 띄우고, 유닉스 소켓으로 master fd 를 넘긴 뒤 죽는다."""
import os, pty, socket, sys, json, time, fcntl, termios, struct
sock_path = sys.argv[1]
pid, master = pty.fork()
if pid == 0:
    os.execvp("bash", ["bash", "--noprofile", "--norc", "-c",
        "trap 'echo GOT-HUP; exit 129' HUP; i=0; while true; do echo tick $i; i=$((i+1)); sleep 0.5; done"])
fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
srv = socket.socket(socket.AF_UNIX)
try: os.unlink(sock_path)
except FileNotFoundError: pass
srv.bind(sock_path); srv.listen(1)
print(json.dumps({"daemon1_pid": os.getpid(), "child_pid": pid}), flush=True)
conn, _ = srv.accept()
socket.send_fds(conn, [json.dumps({"child_pid": pid}).encode()], [master])
conn.close(); srv.close()
# 넘긴 뒤 잠깐 있다가 아무 정리 없이 죽는다 (crash 흉내)
time.sleep(0.3)
os._exit(0)
