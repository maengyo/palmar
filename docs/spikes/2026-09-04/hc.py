#!/usr/bin/env python3
"""Tiny herdr socket client for spikes.
usage: hc.py METHOD [JSON_PARAMS]      -> one request, prints result + elapsed ms
       hc.py --sub [SECONDS]            -> events.subscribe and print events as they arrive
       hc.py --raw                      -> read JSON lines from stdin, send sequentially on one connection
"""
import socket, json, sys, time, os
SOCK=os.path.expanduser("~/.config/herdr/herdr.sock")
def connect():
    s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(SOCK); return s
def send(s, i, method, params):
    s.sendall((json.dumps({"id":str(i),"method":method,"params":params})+"\n").encode())
def lines(s, timeout=None):
    s.settimeout(timeout); buf=b""
    while True:
        try: chunk=s.recv(65536)
        except socket.timeout: return
        if not chunk: return
        buf+=chunk
        while b"\n" in buf:
            line,buf=buf.split(b"\n",1)
            if line.strip(): yield json.loads(line)
def main():
    a=sys.argv[1:]
    if a and a[0]=="--sub":
        secs=float(a[1]) if len(a)>1 else 5
        params=json.loads(a[2]) if len(a)>2 else {}
        s=connect(); t0=time.monotonic(); send(s,"sub","events.subscribe",params)
        end=t0+secs
        for msg in lines(s, timeout=0.2):
            print(f"[{(time.monotonic()-t0)*1000:8.1f}ms] {json.dumps(msg,ensure_ascii=False)[:600]}", flush=True)
            if time.monotonic()>end: break
        while time.monotonic()<end:
            for msg in lines(s, timeout=0.2):
                print(f"[{(time.monotonic()-t0)*1000:8.1f}ms] {json.dumps(msg,ensure_ascii=False)[:600]}", flush=True)
        return
    if a and a[0]=="--raw":
        s=connect(); i=0
        for line in sys.stdin:
            line=line.strip()
            if not line: continue
            m,_,p=line.partition(" "); i+=1
            t0=time.monotonic(); send(s,i,m,json.loads(p or "{}"))
            for msg in lines(s, timeout=30):
                print(f"[{(time.monotonic()-t0)*1000:8.1f}ms] {m} -> {json.dumps(msg,ensure_ascii=False)[:1500]}", flush=True); break
        return
    method=a[0]; params=json.loads(a[1]) if len(a)>1 else {}
    s=connect(); t0=time.monotonic(); send(s,1,method,params)
    for msg in lines(s, timeout=float(os.environ.get("HC_TIMEOUT","30"))):
        print(f"[{(time.monotonic()-t0)*1000:8.1f}ms] {json.dumps(msg,ensure_ascii=False)}", flush=True); break
main()
