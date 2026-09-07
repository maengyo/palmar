#!/usr/bin/env python3
"""Spike 1C: cost of polling pane.read. herdr CPU via ps -p PID -o cputime (delta), client CPU via process_time."""
import socket, json, time, os, sys, subprocess
SOCK=os.path.expanduser("~/.config/herdr/herdr.sock"); PANE="w1:p1"
PID=int(subprocess.check_output(["pgrep","-f","^herdr server$"]).split()[0])  # 서버만. `pgrep -x herdr` 는 컨트롤러 자식 프로세스도 잡는다
def cputime():
    out=subprocess.check_output(["ps","-p",str(PID),"-o","cputime="]).decode().strip()  # M:SS.ss or H:MM:SS
    parts=out.split(":"); parts=[float(p) for p in parts]
    return sum(p*60**i for i,p in enumerate(reversed(parts)))
def one(m,p):
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(SOCK); s.settimeout(10)
    s.sendall((json.dumps({"id":"c","method":m,"params":p})+"\n").encode()); buf=b""
    while b"\n" not in buf:
        c=s.recv(1<<20)
        if not c: break
        buf+=c
    s.close(); return json.loads(buf.split(b"\n",1)[0])
def measure(label, hz, seconds, fmt="ansi", source="visible"):
    period=1.0/hz if hz else None; n=0; lat=[]; byts=0
    c0=cputime(); p0=time.process_time(); t0=time.time(); nxt=t0
    while time.time()-t0<seconds:
        if period:
            ts=time.time(); r=one("pane.read",{"pane_id":PANE,"source":source,"format":fmt}); lat.append((time.time()-ts)*1000); n+=1
            byts+=len(r["result"]["read"]["text"]); nxt+=period; d=nxt-time.time()
            if d>0: time.sleep(d)
        else: time.sleep(0.1)
    el=time.time()-t0; hc=(cputime()-c0)/el*100; cc=(time.process_time()-p0)/el*100
    lat_s=sorted(lat); ms=lambda q: lat_s[int(q*(len(lat_s)-1))] if lat_s else 0
    print(f"  {label:34s} herdr CPU {hc:5.1f}%  client CPU {cc:5.1f}%  reads={n:4d} ({n/el:4.1f}/s)  lat p50={ms(.5):.2f}ms p95={ms(.95):.2f}ms  avg payload={byts//n if n else 0} B", flush=True)
print(f"herdr server pid={PID}; pane={PANE}; measuring with ps -p {PID} -o cputime deltas")
one("pane.send_text",{"pane_id":PANE,"text":"clear\n"}); time.sleep(0.3)
print("\n--- idle pane ---")
measure("no polling (baseline)",0,4)
for hz in (5,10,20,30,60): measure(f"poll {hz} Hz visible/ansi",hz,4)
measure("poll 20 Hz visible/text",20,4,fmt="text")
measure("poll 20 Hz recent/ansi",20,4,source="recent")
STREAM="python3 -c 'import time,sys\nt=time.time();i=0\nwhile time.time()-t<7: i+=1; sys.stdout.write(f\"stream line {i} 0123456789abcdef 0123456789abcdef\\n\")\n'\n"
print("\n--- streaming pane (python writes lines flat-out for 7s) ---")
for label,hz in (("no polling (stream baseline)",0),("poll 10 Hz visible/ansi",10),("poll 20 Hz visible/ansi",20),("poll 30 Hz visible/ansi",30)):
    one("pane.send_text",{"pane_id":PANE,"text":STREAM}); time.sleep(0.6)
    measure(label,hz,5.5); time.sleep(2.0)
r=one("pane.read",{"pane_id":PANE,"source":"visible","format":"text"}); txt=r["result"]["read"]["text"].strip().splitlines()
print(f"\n  last stream line reached: {txt[-2] if len(txt)>1 else txt}")
