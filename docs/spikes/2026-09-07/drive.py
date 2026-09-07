"""Spike C: claude 2.1.259 를 진짜 PTY 에서 띄우고, 승인 프롬프트·질문 UI 에서 어떤 훅이 언제 오는지 본다."""
import os, pty, sys, time, json, select, re, fcntl, termios, struct, signal
D=sys.argv[1]; LOG=f"{D}/hooks.log"
# claude 를 띄울 디렉터리. 두 번째 인자로 주고, 없으면 저장소 뿌리를 쓴다.
CWD=sys.argv[2] if len(sys.argv)>2 else os.path.abspath(os.path.join(os.path.dirname(__file__),"..","..",".."))
open(LOG,"w").close()
env={k:v for k,v in os.environ.items() if not (k.startswith("CLAUDE") or k.startswith("CODEX_COMPANION") or k.startswith("HERDR"))}
env.update({"TERM":"xterm-256color","PALMER_HOOK_LOG":LOG})
pid, master = pty.fork()
if pid == 0:
    os.chdir(CWD); os.execvpe("claude", ["claude","--settings",f"{D}/settings.json","--permission-mode","default"], env)
fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 32, 110, 0, 0))
ANSI=re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]|\x1b\[\?[0-9;]*[hl]|\r")
raw=b""; T0=time.time(); seen_hooks=0; timeline=[]
def now(): return time.time()-T0
def plain(b): return ANSI.sub(b"", b).decode(errors="replace")
def hooks_since(n):
    lines=open(LOG).read().splitlines()
    out=[]
    for l in lines[n:]:
        j=json.loads(l); p=json.loads(j["payload"]) if j["payload"] else {}
        out.append({"t":round(j["t"]-T0,3),"event":p.get("hook_event_name",j["arg"]),"tool":p.get("tool_name"),"ntype":p.get("notification_type"),"msg":(p.get("message") or "")[:60],"prompt":(p.get("prompt") or "")[:50],"keys":sorted(k for k in p.keys() if k not in ("session_id","transcript_path","cwd","hook_event_name"))})
    return out, len(lines)
def pump(until, seconds, quiet=False):
    """until: callable(plain_text)->bool. returns (matched, elapsed)"""
    global raw
    end=time.time()+seconds
    while time.time()<end:
        r,_,_=select.select([master],[],[],0.05)
        if r:
            try: chunk=os.read(master,65536)
            except OSError: return False, now()
            raw+=chunk
        txt=plain(raw[-20000:])
        if until(txt): return True, now()
        if "session limit" in txt.lower() and "hit your" in txt.lower(): print("!! session limit hit"); return False, now()
    return False, now()
def send(s): os.write(master, s.encode())
def submit(text):
    send(text); time.sleep(0.5); send("\r")
def tail(n=900): return plain(raw[-8000:])[-n:]
def rec(label, extra=""): timeline.append((round(now(),2), label, extra)); print(f"[{now():7.2f}s] {label} {extra}", flush=True)

ok,t=pump(lambda x: "❯" in x and "Do you trust" not in x, 45); rec("prompt box visible" if ok else "NO prompt box", f"(trust dialog: {'Do you trust' in tail(3000)})")
if not ok: print(tail(1500)); os.kill(pid, signal.SIGKILL); sys.exit(1)
h,n=hooks_since(0); rec("hooks at startup", json.dumps(h, ensure_ascii=False))
time.sleep(0.5)

# ---- 1. bash approval ----
rec("send prompt 1 (bash)"); submit("Run `ls /usr/bin | wc -l` with the Bash tool and tell me only the number.")
ok,t=pump(lambda x: "Do you want to proceed" in x or "Yes" in x and "No" in x and "proceed" in x.lower(), 90); rec("approval dialog on screen" if ok else "NO approval dialog", "")
t_dialog=now(); time.sleep(1.0); h,n=hooks_since(n); rec("hooks since prompt 1", json.dumps(h, ensure_ascii=False))
print("--- screen ---\n"+tail(700)+"\n--------------")
if ok:
    rec("send Enter (approve)"); send("\r")
    ok2,t=pump(lambda x: False, 0.1)
    # wait for Stop hook
    end=time.time()+90
    while time.time()<end:
        pump(lambda x: False, 0.3); h2,_=hooks_since(n)
        if any(e["event"]=="Stop" for e in h2): break
    h,n=hooks_since(n); rec("hooks after approve", json.dumps(h, ensure_ascii=False))
    print("--- screen ---\n"+tail(500)+"\n--------------")

# ---- 2. AskUserQuestion ----
time.sleep(1.0); rec("send prompt 2 (AskUserQuestion)"); submit("Use the AskUserQuestion tool to ask me one question: red or blue? Then reply with only the word I chose.")
ok,t=pump(lambda x: "Enter to select" in x or "to navigate" in x, 90); rec("question dialog on screen" if ok else "NO question dialog")
time.sleep(1.0); h,n=hooks_since(n); rec("hooks since prompt 2", json.dumps(h, ensure_ascii=False))
print("--- screen ---\n"+tail(700)+"\n--------------")
if ok:
    rec("send Enter (choose)"); send("\r")
    end=time.time()+90
    while time.time()<end:
        pump(lambda x: False, 0.3); h2,_=hooks_since(n)
        if any(e["event"]=="Stop" for e in h2): break
    h,n=hooks_since(n); rec("hooks after answer", json.dumps(h, ensure_ascii=False))

# ---- 3. exit ----
time.sleep(0.5); rec("send /exit"); submit("/exit"); time.sleep(0.5)
end=time.time()+15
while time.time()<end:
    pump(lambda x: False, 0.3)
    try: os.kill(pid,0)
    except ProcessLookupError: break
try: os.kill(pid,0); alive=True
except ProcessLookupError: alive=False
h,n=hooks_since(n); rec("hooks at exit", json.dumps(h, ensure_ascii=False)); rec("claude process alive", str(alive))
if alive: os.kill(pid, signal.SIGKILL)
print("\n=== FULL HOOK LOG (event, t, tool, ntype) ===")
for l in open(LOG):
    j=json.loads(l); p=json.loads(j["payload"]) if j["payload"] else {}
    print(f"  {j['t']-T0:7.2f}s  {p.get('hook_event_name',j['arg']):18s} tool={p.get('tool_name')} ntype={p.get('notification_type')} msg={(p.get('message') or '')[:50]!r}")
