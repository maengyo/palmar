"""docs/spikes/2026-09-08/close-{light,dark}.png — 한 장에 #31 네 가지가 다 보이게."""
import json, os, subprocess, sys, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Chrome

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
SHOT = os.path.join(REPO, "docs/spikes/2026-09-08")
PORT = 8801


def api(method, path, body=None, tok=None):
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    if method != "GET":
        url += ("&" if "?" in path else "?") + "token=" + tok
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("content-type", "application/json")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        t = r.read().decode()
        return r.status, (json.loads(t) if t else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


d = ch = None
try:
    log = os.path.join(os.environ.get("TMPDIR", "/tmp"), "palmar-close-shots.log")
    env = {"HOME": os.path.expanduser("~"), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
           "SHELL": "/bin/bash", "TERM": "dumb", "LANG": "en_US.UTF-8"}
    d = subprocess.Popen(["/usr/bin/python3", os.path.join(REPO, "palmar/daemon.py"), "--port", str(PORT)],
                         env=env, stdout=open(log, "wb"), stderr=subprocess.STDOUT, cwd=REPO)
    for _ in range(200):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/sessions" % PORT, timeout=1).read(); break
        except Exception:
            time.sleep(0.1)
    tok = open(os.path.expanduser("~/.palmar/run/token")).read().strip()
    st, cvs = api("GET", "/api/canvases"); c1 = cvs[0]["id"]
    api("PATCH", "/api/canvases/" + c1, {"name": "palmar"}, tok)
    st, c2 = api("POST", "/api/canvases", {"name": "notes"}, tok); c2 = c2["id"]
    mk = lambda cv, cwd, nm: api("POST", "/api/sessions", {"cwd": cwd, "canvas": cv, "name": nm}, tok)[1]["id"]
    a = mk(c1, REPO, None)
    b = mk(c1, REPO + "/web", None)
    c = mk(c1, REPO + "/server", None)
    e = mk(c2, REPO + "/docs", "notes · draft")
    f = mk(c2, os.path.expanduser("~"), None)
    g = mk(c2, REPO + "/docs/spikes", None)
    ch = Chrome(port=9341, window="1600,1000")
    t = ch.new_tab("http://127.0.0.1:%d/" % PORT)
    t.cmd("Page.enable"); t.cmd("Runtime.enable")
    time.sleep(3)
    for pane, ev in ((a, "PermissionRequest"), (b, "UserPromptSubmit"), (c, "Stop"),
                     (e, "PermissionRequest"), (f, "UserPromptSubmit"), (g, "SessionStart")):
        api("POST", "/hook/claude?pane=%s" % pane, {"hook_event_name": ev}, tok)
        time.sleep(0.15)
    time.sleep(1.0)
    # notes 묶음을 접는다 — 기다리는 줄 하나만 남는 것이 그림의 요점
    gb = t.js("(()=>{const g=[...document.querySelectorAll('#list .grp.cvg')].find(g=>g.dataset.canvas===%r);"
              "const r=g.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % c2)
    t.click(gb["x"], gb["y"]); time.sleep(0.6)
    # 목록 행의 확인 줄을 열어 둔다 — 닫기가 무엇인지 그림이 말하게
    cb = t.js("(()=>{const it=[...document.querySelectorAll('#list .ses')].find(e=>e.dataset.id===%r);"
              "if(!it) return null; const b=it.querySelector('.cl');const r=b.getBoundingClientRect();"
              "return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % b)
    t.click(cb["x"], cb["y"]); time.sleep(0.5)
    print("list:", json.dumps(t.js("[...document.querySelectorAll('#list .grp,#list .ses,#list .grest')]"
                                   ".map(e=>e.className+' | '+e.textContent)"), ensure_ascii=False, indent=1))
    for want, path in (("light", os.path.join(SHOT, "close-light.png")),
                       ("dark", os.path.join(SHOT, "close-dark.png"))):
        for _ in range(4):
            if t.js("document.querySelector('#theme').dataset.mode") == want:
                break
            t.js("document.querySelector('#theme').click();1"); time.sleep(0.4)
        time.sleep(0.9)
        t.shot(path)
        print(" ", want, t.js("document.querySelector('#theme').dataset.mode"), path,
              os.path.getsize(path), "bytes")
    for sid in [s["id"] for s in api("GET", "/api/sessions")[1]]:
        api("DELETE", "/api/sessions/" + sid, None, tok)
    time.sleep(1)
    print("sessions left:", len(api("GET", "/api/sessions")[1]))
finally:
    if ch: ch.kill()
    if d:
        d.terminate()
        try: d.wait(6)
        except Exception: d.kill()
