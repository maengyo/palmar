#!/usr/bin/env python3
"""Spike 1A (fixed): events.subscribe with typed subscriptions; latency + coalescing of pane_output_changed."""
import socket, json, time, os, sys, threading
SOCK=os.path.expanduser("~/.config/herdr/herdr.sock")
S=os.path.dirname(os.path.abspath(__file__))
PANE=sys.argv[1] if len(sys.argv)>1 else "w1:p1"
import subprocess
schema=json.loads(subprocess.check_output(["herdr","api","schema","--json"]))["schemas"]  # 실행 중인 herdr 의 스키마를 그때그때 읽는다
subs=[]
print("=== Subscription types (events.subscribe) ===")
for alt in schema["request"]["$defs"]["Subscription"]["oneOf"]:
    t=alt["properties"]["type"]["const"]; req=[r for r in alt.get("required",[]) if r!="type"]; props=[p for p in alt["properties"] if p!="type"]
    print(f"  {t:32s} required={req} optional={[p for p in props if p not in req]}")
    if not req: subs.append({"type":t})
print("\n=== EventMatch for pane_* (events.wait) ===")
for alt in schema["request"]["$defs"]["EventMatch"]["oneOf"]:
    t=alt["properties"]["event"]["const"]
    if t.startswith("pane"): print(f"  {t:32s} required={[r for r in alt.get('required',[]) if r!='event']} props={[p for p in alt['properties'] if p!='event']}")
def connect():
    s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(SOCK); return s
def one(m,p):
    s=connect(); s.sendall((json.dumps({"id":"c","method":m,"params":p})+"\n").encode()); buf=b""
    while b"\n" not in buf:
        c=s.recv(65536)
        if not c: break
        buf+=c
    s.close(); return json.loads(buf.split(b"\n",1)[0])
def reader(s,out,stop):
    buf=b""; s.settimeout(0.05)
    while not stop.is_set():
        try: c=s.recv(65536)
        except socket.timeout: continue
        if not c: out.append((time.time(),{"__eof__":True})); break
        buf+=c
        while b"\n" in buf:
            line,buf=buf.split(b"\n",1)
            if line.strip(): out.append((time.time(),json.loads(line)))
sub=connect(); events=[]; stop=threading.Event()
threading.Thread(target=reader,args=(sub,events,stop),daemon=True).start()
sub.sendall((json.dumps({"id":"sub","method":"events.subscribe","params":{"subscriptions":subs}})+"\n").encode())
time.sleep(0.4); print("\nsubscribe ack:", [json.dumps(e[1])[:200] for e in events]); events.clear()
def kind(e): return e.get("event") or e.get("data",{}).get("type") or ("EOF" if "__eof__" in e else str(list(e)))
def run(label,text,settle):
    events.clear(); t0=time.time(); r=one("pane.send_text",{"pane_id":PANE,"text":text}); tack=time.time(); time.sleep(settle)
    evs=list(events); kinds={}
    for _,e in evs: kinds[kind(e)]=kinds.get(kind(e),0)+1
    print(f"\n### {label}\n  send_text roundtrip(new conn): {(tack-t0)*1000:.1f}ms  events in {settle}s: {len(evs)}  kinds={kinds}")
    oc=[(t,e) for t,e in evs if kind(e)=="pane_output_changed"]
    if oc:
        print(f"  pane_output_changed: first +{(oc[0][0]-t0)*1000:.1f}ms  last +{(oc[-1][0]-t0)*1000:.1f}ms  count={len(oc)}")
        revs=[e["data"].get("revision") for _,e in oc]; print(f"  revisions: {revs[:15]}{' ...' if len(revs)>15 else ''}  (delta {revs[-1]-revs[0] if len(revs)>1 else 0})")
        gaps=[(oc[i+1][0]-oc[i][0])*1000 for i in range(len(oc)-1)]
        if gaps: print(f"  gaps ms: min={min(gaps):.1f} med={sorted(gaps)[len(gaps)//2]:.1f} max={max(gaps):.1f}")
    for t,e in evs[:5]: print(f"   +{(t-t0)*1000:7.1f}ms {json.dumps(e,ensure_ascii=False)[:230]}")
    if len(evs)>5: print(f"   ... {len(evs)-5} more")
    return evs
run("single echo","echo palmar-ev-1\n",1.5)
run("burst: 300 lines, no sleep","for i in $(seq 1 300); do echo line-$i; done\n",2.5)
run("trickle: 50 lines @20ms","for i in $(seq 1 50); do echo t-$i; sleep 0.02; done\n",3.0)
run("stream: 2000 lines via seq (fast)","seq 1 2000\n",2.5)
run("idle baseline","",1.0)
# does the subscription connection stay open?
time.sleep(0.5); print("\nsubscription connection still open:", not any("__eof__" in e for _,e in events))
stop.set()
