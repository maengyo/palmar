#!/usr/bin/env python3
"""Spike 1B: revision semantics, events.wait long-poll latency, wait_for_output, output_matched push subscription."""
import socket, json, time, os, sys, threading
SOCK=os.path.expanduser("~/.config/herdr/herdr.sock"); PANE=sys.argv[1] if len(sys.argv)>1 else "w1:p1"
def connect():
    s=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(SOCK); return s
def one(m,p,timeout=30):
    s=connect(); s.settimeout(timeout); t0=time.time(); s.sendall((json.dumps({"id":"c","method":m,"params":p})+"\n").encode()); buf=b""
    try:
        while b"\n" not in buf:
            c=s.recv(1<<20)
            if not c: break
            buf+=c
    except socket.timeout: s.close(); return time.time()-t0, {"__timeout__":True}
    s.close(); return time.time()-t0, json.loads(buf.split(b"\n",1)[0])
def rev(): return one("pane.get",{"pane_id":PANE})[1]["result"]["pane"]["revision"]
print("=== E1 revision semantics ===")
r0=rev(); one("pane.send_text",{"pane_id":PANE,"text":"echo rev-probe-1\n"}); time.sleep(0.3); r1=rev()
_,rd=one("pane.read",{"pane_id":PANE,"source":"visible","format":"ansi","lines":5}); rr=rd["result"]["read"]["revision"]; time.sleep(0.1); r2=rev()
one("pane.send_text",{"pane_id":PANE,"text":"seq 1 500\n"}); time.sleep(0.5); r3=rev()
one("pane.report_metadata",{"pane_id":PANE,"source":"palmer","tokens":{"palmer_x":"121"}}); time.sleep(0.1); r4=rev()
print(f"  before={r0}  after echo={r1}  pane.read.revision={rr}  after read={r2}  after seq 500={r3}  after report_metadata={r4}")
print(f"  read text tail: {rd['result']['read']['text'][-160:]!r}  truncated={rd['result']['read']['truncated']}")

print("\n=== E2 events.wait(pane_output_changed, min_revision) as long-poll ===")
res={}
def waiter(minrev,to=5000):
    dt,r=one("events.wait",{"match_event":{"event":"pane_output_changed","pane_id":PANE,"min_revision":minrev},"timeout_ms":to},timeout=10); res["t"]=time.time(); res["dt"]=dt; res["r"]=r
base=rev(); th=threading.Thread(target=waiter,args=(base+1,)); th.start(); time.sleep(0.5); tsend=time.time(); one("pane.send_text",{"pane_id":PANE,"text":"echo lp-1\n"}); th.join()
print(f"  base rev={base}; wait returned {(res['t']-tsend)*1000:.1f}ms after send_text; total wait {res['dt']*1000:.0f}ms; result={json.dumps(res['r'])[:260]}")
# immediate return if already past cursor?
dt,r=one("events.wait",{"match_event":{"event":"pane_output_changed","pane_id":PANE,"min_revision":base},"timeout_ms":1500},timeout=5)
print(f"  wait with min_revision<=current: returned in {dt*1000:.1f}ms -> {json.dumps(r)[:200]}")
dt,r=one("events.wait",{"match_event":{"event":"pane_output_changed","pane_id":PANE,"min_revision":rev()+100},"timeout_ms":800},timeout=5)
print(f"  wait with unreachable cursor, timeout 800ms: returned in {dt*1000:.0f}ms -> {json.dumps(r)[:200]}")
# repeated latency samples
lat=[]
for i in range(8):
    b=rev(); th=threading.Thread(target=waiter,args=(b+1,)); th.start(); time.sleep(0.15); ts=time.time(); one("pane.send_text",{"pane_id":PANE,"text":f"echo lp-{i}\n"}); th.join(); lat.append((res["t"]-ts)*1000)
print(f"  latency send_text -> wait returns (8 samples) ms: {[round(x,1) for x in lat]}  median={sorted(lat)[4]:.1f}")
# how many wait+read cycles during a fast stream, and are revisions skipped (coalesced)?
print("\n=== E2b wait+read loop during `seq 1 20000` stream ===")
one("pane.send_text",{"pane_id":PANE,"text":"clear\n"}); time.sleep(0.2)
cur=rev(); cycles=0; revs=[]; t0=time.time(); one("pane.send_text",{"pane_id":PANE,"text":"seq 1 20000\n"})
while time.time()-t0<4.0:
    dt,r=one("events.wait",{"match_event":{"event":"pane_output_changed","pane_id":PANE,"min_revision":cur+1},"timeout_ms":700},timeout=5)
    if "__timeout__" in r or "error" in r or r.get("result",{}).get("type")!="wait_matched": break
    cur=r["result"]["event"]["data"]["revision"]; revs.append(cur)
    _,rd=one("pane.read",{"pane_id":PANE,"source":"visible","format":"ansi"}); cycles+=1
el=time.time()-t0
print(f"  cycles={cycles} in {el:.2f}s ({cycles/el:.1f} Hz); revision {revs[0] if revs else '-'} -> {revs[-1] if revs else '-'}; last read len={len(rd['result']['read']['text'])} bytes")
print(f"  revision deltas per cycle (first 15): {[revs[i+1]-revs[i] for i in range(min(15,len(revs)-1))]}")

print("\n=== E3 pane.wait_for_output as change detector ===")
def w4o(match,to):
    dt,r=one("pane.wait_for_output",{"pane_id":PANE,"source":"visible","match":match,"timeout_ms":to},timeout=10); res["t"]=time.time(); res["dt"]=dt; res["r"]=r
th=threading.Thread(target=w4o,args=({"type":"substring","value":"needle-xyz"},5000)); th.start(); time.sleep(0.4); ts=time.time(); one("pane.send_text",{"pane_id":PANE,"text":"echo needle-xyz\n"}); th.join()
rr=res["r"].get("result",{}); print(f"  substring not yet present: returned {(res['t']-ts)*1000:.1f}ms after send; type={rr.get('type')} matched_line={rr.get('matched_line')!r} revision={rr.get('revision')} read.len={len(rr.get('read',{}).get('text',''))}")
dt,r=one("pane.wait_for_output",{"pane_id":PANE,"source":"visible","match":{"type":"regex","value":"(?s)."},"timeout_ms":3000},timeout=10)
print(f"  regex match-anything: returned in {dt*1000:.1f}ms (immediate => not a change detector) type={r.get('result',{}).get('type')}")

print("\n=== E4 subscription pane.output_matched (match-anything) as push stream ===")
evs=[]; stop=threading.Event()
def reader(s):
    buf=b""; s.settimeout(0.05)
    while not stop.is_set():
        try: c=s.recv(1<<20)
        except socket.timeout: continue
        if not c: evs.append((time.time(),{"__eof__":True})); break
        buf+=c
        while b"\n" in buf:
            line,buf=buf.split(b"\n",1)
            if line.strip(): evs.append((time.time(),json.loads(line)))
sub=connect(); threading.Thread(target=reader,args=(sub,),daemon=True).start()
sub.sendall((json.dumps({"id":"s","method":"events.subscribe","params":{"subscriptions":[{"type":"pane.output_matched","pane_id":PANE,"source":"visible","match":{"type":"regex","value":"(?s)."},"strip_ansi":False}]}})+"\n").encode())
time.sleep(0.5); print("  ack:", [json.dumps(e)[:160] for _,e in evs]); n0=len(evs)
ts=time.time(); one("pane.send_text",{"pane_id":PANE,"text":"echo push-1\n"}); time.sleep(1.0)
got=evs[n0:]; print(f"  after single echo: {len(got)} events; first at +{(got[0][0]-ts)*1000:.1f}ms" if got else "  after single echo: 0 events")
for _,e in got[:2]: print("   ", json.dumps(e,ensure_ascii=False)[:400])
n1=len(evs); ts=time.time(); one("pane.send_text",{"pane_id":PANE,"text":"seq 1 3000\n"}); time.sleep(2.5); got=evs[n1:]
print(f"  during seq 1 3000: {len(got)} events in 2.5s" + (f"; first +{(got[0][0]-ts)*1000:.1f}ms last +{(got[-1][0]-ts)*1000:.1f}ms; avg payload {sum(len(json.dumps(e)) for _,e in got)//len(got)} bytes" if got else ""))
if got:
    txt=got[-1][1].get("data",{}).get("read",{}).get("text","")
    print(f"  last payload has ESC bytes: {chr(27) in txt}; format field={got[-1][1].get('data',{}).get('read',{}).get('format')}; tail={txt[-80:]!r}")
stop.set()
