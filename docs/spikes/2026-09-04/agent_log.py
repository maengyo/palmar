#!/usr/bin/env python3
"""Spike 2 logger: subscribe to agent status events for one pane + poll pane.get 4x/s; write timestamped log."""
import socket,json,time,os,sys,threading
SOCK=os.path.expanduser("~/.config/herdr/herdr.sock"); PANE=sys.argv[1]; LOG=sys.argv[2]
def connect():
    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(SOCK); return s
def one(m,p):
    s=connect(); s.settimeout(5); s.sendall((json.dumps({"id":"c","method":m,"params":p})+"\n").encode()); buf=b""
    while b"\n" not in buf:
        c=s.recv(1<<20)
        if not c: break
        buf+=c
    s.close(); return json.loads(buf.split(b"\n",1)[0])
out=open(LOG,"a",buffering=1)
def log(kind,msg): out.write(f"{time.time():.3f} {time.strftime('%H:%M:%S')} {kind:6s} {msg}\n")
def sub_thread():
    s=connect(); s.sendall((json.dumps({"id":"s","method":"events.subscribe","params":{"subscriptions":[{"type":"pane.agent_status_changed","pane_id":PANE},{"type":"pane.agent_detected"},{"type":"pane.updated"},{"type":"pane.exited"},{"type":"pane.closed"}]}})+"\n").encode())
    buf=b""; s.settimeout(0.2)
    while True:
        try: c=s.recv(1<<20)
        except socket.timeout: continue
        if not c: log("EVENT","<EOF>"); return
        buf+=c
        while b"\n" in buf:
            line,buf=buf.split(b"\n",1)
            if not line.strip(): continue
            e=json.loads(line); d=e.get("data",{})
            if d.get("pane_id",PANE)!=PANE and d.get("pane",{}).get("pane_id",PANE)!=PANE: continue
            if e.get("event")=="pane_updated" or d.get("type")=="pane_updated":
                p=d.get("pane",{}); log("EVENT",f"pane_updated status={p.get('agent_status')} agent={p.get('agent')} title={p.get('title')!r} labels={p.get('state_labels')} rev={p.get('revision')}")
            else: log("EVENT",json.dumps(e,ensure_ascii=False)[:400])
threading.Thread(target=sub_thread,daemon=True).start()
last=None
while True:
    try:
        r=one("pane.get",{"pane_id":PANE}); p=r.get("result",{}).get("pane",{})
        cur=(p.get("agent_status"),p.get("agent"),json.dumps(p.get("state_labels"),sort_keys=True))
        if cur!=last: log("POLL",f"agent_status={cur[0]} agent={cur[1]} labels={cur[2]} title={p.get('title')!r} tokens={p.get('tokens')}"); last=cur
    except Exception as ex: log("POLL",f"error {ex}"); break
    time.sleep(0.25)
