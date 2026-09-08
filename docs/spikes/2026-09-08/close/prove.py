"""#31 네 가지를 깨끗한 ~/.palmar · 진짜 8801 에서 처음부터 다시 증명한다. 앞선 통합도 같이 돈다."""
import json, os, subprocess, sys, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Chrome

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
SHOT = os.path.join(REPO, "docs/spikes/2026-09-08")
TMP = os.environ.get("TMPDIR", "/tmp")
PORT = int(os.environ.get("PALMAR_TEST_PORT", "8801"))
PY3 = os.environ.get("PALMAR_PY", "/usr/bin/python3")
OK = []
BAD = []


def chk(name, cond, extra=""):
    (OK if cond else BAD).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + ((" — " + str(extra)) if extra else ""))


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
        t = e.read().decode()
        return e.code, (json.loads(t) if t else None)


def hook(pane, ev, tok):
    return api("POST", "/hook/claude?pane=%s" % pane, {"hook_event_name": ev}, tok)


def rows(t):
    return t.js("[...document.querySelectorAll('#list .ses')].map(e=>({id:e.dataset.id,"
                "cls:e.className,pinned:e.classList.contains('pinned')}))")


def box(t, sel):
    return t.js("(()=>{const e=document.querySelector(%r); if(!e) return null;"
                "const r=e.getBoundingClientRect(); if(!r.width) return null;"
                "return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % sel)


def rowbox(t, sid, inner):
    return t.js("(()=>{const it=[...document.querySelectorAll('#list .ses')].find(e=>e.dataset.id===%r);"
                "if(!it) return null; const b=it.querySelector(%r); if(!b) return null;"
                "const r=b.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()"
                % (sid, inner))


def replay(sid, tok, secs=1.5):
    """/pty/<id>?from=0 에 붙어 링버퍼 재생을 모은다 (protocol.md 'pane 채널')."""
    from cdp import WS
    w = WS("ws://127.0.0.1:%d/pty/%s?token=%s&from=0" % (PORT, sid, tok))
    out = b""
    end = time.time() + secs
    w.s.settimeout(0.3)
    while time.time() < end:
        try:
            out += w.recv()
        except Exception:
            pass
    w.close()
    return out


d = ch = None
try:
    print("== 데몬: 깨끗한 ~/.palmar, 진짜 HOME, 포트 8801")
    log = os.path.join(TMP, "d%d.log" % PORT)
    env = {"HOME": os.path.expanduser("~"), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
           "SHELL": "/bin/bash", "TERM": "dumb", "LANG": "en_US.UTF-8"}
    f = open(log, "wb")
    d = subprocess.Popen([PY3, os.path.join(REPO, "palmar/daemon.py"), "--port", str(PORT)],
                         env=env, stdout=f, stderr=subprocess.STDOUT, cwd=REPO)
    for _ in range(200):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/sessions" % PORT, timeout=1).read()
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("daemon did not come up: " + open(log).read()[-2000:])
    tok = open(os.path.expanduser("~/.palmar/run/token")).read().strip()
    print("daemon pid", d.pid, " python:", PY3, " banner:", open(log).read().strip().splitlines()[-1])

    print("\n== 문제 2 — 남의 홈은 뿌리가 아니다")
    st, dirs = api("GET", "/api/dirs")
    names = [e["name"] for e in dirs["entries"]]
    print("  GET /api/dirs →", names)
    chk("뿌리가 홈 하나뿐이다(/Users/Shared 는 root 소유라 빠진다)", names == [os.path.expanduser("~")], names)
    chk("/Users/Shared 는 실제로 남의 것이다",
        os.stat("/Users/Shared").st_uid != os.getuid(), os.stat("/Users/Shared").st_uid)
    st, r = api("GET", "/api/dirs?path=/Users/Shared")
    chk("뿌리 밖 경로 읽기는 400", st == 400, (st, r))
    st, r = api("POST", "/api/sessions", {"cwd": "/Users/Shared"}, tok)
    chk("뿌리 밖 cwd 로 세션 만들기는 400", st == 400, (st, r))
    # 규칙이 진짜 uid 인지 — 데몬 코드를 그대로 불러 uid 를 갈아 끼워 본다
    probe = subprocess.run(["/usr/bin/python3", "-c", """
import os, sys, types
sys.path.insert(0, %r)
import importlib.util
spec = importlib.util.spec_from_file_location('pd', %r)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
real = os.getuid
for uid in (real(), 0, 999):
    os.getuid = (lambda u: (lambda: u))(uid)
    print(uid, [str(p) for p in m.roots()])
os.getuid = real
""" % (os.path.join(REPO, "palmar"), os.path.join(REPO, "palmar/daemon.py"))],
                           capture_output=True, text=True, env=dict(os.environ, PALMAR_NO_RUN="1"))
    print("  roots() by uid:\n   ", probe.stdout.strip().replace("\n", "\n    ") or probe.stderr[-500:])
    chk("uid 를 root 로 두면 /Users/Shared 가 들어온다(규칙이 소유자다)",
        "/Users/Shared" in probe.stdout.splitlines()[1] if len(probe.stdout.splitlines()) > 1 else False,
        probe.stdout[-300:])

    print("\n== 세션·캔버스 만들기")
    st, cvs = api("GET", "/api/canvases")
    c1 = cvs[0]["id"]
    st, c2 = api("POST", "/api/canvases", {"name": "notes"}, tok)
    c2 = c2["id"]
    ids = []
    for i, (cv, cwd) in enumerate([(c1, REPO), (c1, REPO + "/web"), (c1, REPO + "/docs"),
                                   (c2, REPO + "/server"), (c2, os.path.expanduser("~"))]):
        st, s = api("POST", "/api/sessions", {"cwd": cwd, "canvas": cv, "name": None}, tok)
        assert st == 201, (st, s)
        ids.append(s["id"])
    print("  sessions:", len(ids), " canvases: 2")

    ch = Chrome(port=int(os.environ.get("PALMAR_CDP", "9340")), window="1600,1000")
    A = ch.new_tab("http://127.0.0.1:%d/" % PORT)
    B = ch.new_tab("http://127.0.0.1:%d/" % PORT)
    for t in (A, B):
        t.cmd("Page.enable"); t.cmd("Runtime.enable"); t.cmd("Network.enable")
        t.js("window.__dels=[];const of=window.fetch;window.fetch=function(u,i){"
             "if(i&&i.method==='DELETE')window.__dels.push(String(u).split('?')[0]);"
             "return of.apply(this,arguments)};1")
    time.sleep(3)
    chk("두 브라우저가 같은 수를 본다", A.js("document.querySelector('#count').textContent") ==
        B.js("document.querySelector('#count').textContent") == "5",
        (A.js("document.querySelector('#count').textContent"), B.js("document.querySelector('#count').textContent")))

    print("\n== 앞선 통합 — pane 이 진짜 돈다")
    ta = box(A, '.tile[data-id=\"' + ids[0] + '\"] .xterm-screen')
    if ta: A.click(ta['x'], ta['y'])
    A.js("(()=>{const t=[...document.querySelectorAll('.tile')].find(e=>e.dataset.id===%r);"
         "const a=t.querySelector('.xterm-helper-textarea'); if(a) a.focus();})()" % ids[0])
    time.sleep(0.4)
    A.key("e", text="e"); A.key("c", text="c"); A.key("h", text="h"); A.key("o", text="o")
    A.key(" ", text=" "); A.key("P", text="P"); A.key("A", text="A"); A.key("L", text="L")
    A.key("M", text="M"); A.key("E", text="E"); A.key("R", text="R")
    A.key("Enter", code="Enter", text="\r", vk=13)
    time.sleep(1.5)
    # 브라우저가 친 키가 진짜 PTY 에 닿았는지는 **데몬 쪽 링버퍼**로 잰다 — xterm 은 캔버스에 그려
    # DOM 에 글자가 없을 수 있다. /pty 에 from=0 으로 붙으면 그 pane 이 낸 바이트가 재생돼 온다.
    got = replay(ids[0], tok)
    chk("브라우저가 친 키가 진짜 PTY 를 지나 돌아온다", b"PALMAR" in got, repr(got[-160:]))
    hook(ids[1], "PermissionRequest", tok); time.sleep(0.7)
    chk("훅 PermissionRequest → waiting 이 두 브라우저에 간다",
        all(any(r["id"] == ids[1] and "wait" in r["cls"] for r in rows(t)) for t in (A, B)))

    print("\n== 문제 3 — 목록은 캔버스로 묶이고, 기다리는 것은 그래도 안 숨는다")
    grps = A.js("[...document.querySelectorAll('#list .grp')].map(g=>({cvg:g.classList.contains('cvg'),"
                "cv:g.dataset.canvas,key:g.dataset.key,txt:g.textContent}))")
    print("  groups:", json.dumps(grps, ensure_ascii=False))
    chk("묶음이 전부 캔버스 묶음이다(상태 묶음 0)", all(g["cvg"] for g in grps) and len(grps) == 2, grps)
    chk("기다리는 세션이 있는 캔버스 묶음이 맨 위다", grps[0]["cv"] == c1, grps[0])
    g1 = A.js("(()=>{const g=[...document.querySelectorAll('#list .grp.cvg')].find(g=>g.dataset.canvas===%r);"
              "const r=g.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % c1)
    A.click(g1["x"], g1["y"]); time.sleep(0.5)
    rr = rows(A)
    print("  after collapsing canvas 1:", json.dumps(rr, ensure_ascii=False))
    kept = [r for r in rr if r["id"] == ids[1]]
    chk("접힌 묶음 안에서도 기다리는 줄이 남는다", bool(kept) and kept[0]["pinned"], rr)
    chk("접힌 묶음의 나머지는 감춰졌다(캔버스1 세션 3 중 1줄만)",
        len([r for r in rr if r["id"] in ids[:3]]) == 1, rr)
    chk("기다리는 줄이 목록 전체의 첫 줄이다", rr[0]["id"] == ids[1], rr[0])
    hdr = A.js("(()=>{const g=[...document.querySelectorAll('#list .grp.cvg')].find(g=>g.dataset.canvas===%r);"
               "return {ct:g.querySelector('.ct').textContent, dot:!!g.querySelector('.dot.wait'),"
               "car:g.querySelector('.car').textContent};})()" % c1)
    more = A.js("(()=>{const e=document.querySelector('#list .grest'); return e&&e.textContent;})()")
    chk("접힌 머리글의 수는 캔버스 전체(3)다", hdr["ct"] == "3", hdr)
    chk("접힌 머리글에 기다림 점이 있다", hdr["dot"], hdr)
    chk("감춘 개수를 한 줄로 말한다", more == "+2 more, collapsed", more)

    print("\n== 문제 1·4 — 목록 행에서 닫는다")
    victim = ids[2]
    A.click(g1["x"], g1["y"]); time.sleep(0.5)          # 다시 편다
    pane_json = os.path.expanduser("~/.palmar/run/%s.json" % victim)
    chk("pane 설정 파일이 있다", os.path.exists(pane_json), pane_json)
    cb = rowbox(A, victim, ".cl")
    chk("목록 행에 닫기 단추가 있다", cb is not None, cb)
    A.click(cb["x"], cb["y"]); time.sleep(0.3)
    chk("제자리 확인 줄이 열린다(네이티브 대화상자 아님)", A.js("!!document.querySelector('#list .cfm')"))
    esc = A.js("(()=>{const b=document.querySelector('#list .cfm .cbtn:not(.yes)');"
               "const r=b.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
    A.click(esc["x"], esc["y"]); time.sleep(0.4)
    chk("Cancel 은 DELETE 를 0건 낸다", A.js("window.__dels").count("http://127.0.0.1:8801/api/sessions/" + victim) == 0,
        A.js("window.__dels"))
    cb = rowbox(A, victim, ".cl")
    A.click(cb["x"], cb["y"]); time.sleep(0.3)
    yb = A.js("(()=>{const b=document.querySelector('#list .cfm .cbtn.yes');"
              "const r=b.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
    A.click(yb["x"], yb["y"]); time.sleep(1.5)
    dels = [u for u in A.js("window.__dels") if u.endswith(victim)]
    chk("DELETE 를 정확히 한 번 낸다", len(dels) == 1, dels)
    chk("목록 행이 사라졌다(A)", not any(r["id"] == victim for r in rows(A)))
    chk("둘째 브라우저에서도 사라졌다(B)", not any(r["id"] == victim for r in rows(B)))
    chk("데몬에서도 없다", victim not in [s["id"] for s in api("GET", "/api/sessions")[1]])
    chk("pane 설정 파일이 지워졌다", not os.path.exists(pane_json))
    chk("두 브라우저의 수가 같다(4)", A.js("document.querySelector('#count').textContent") ==
        B.js("document.querySelector('#count').textContent") == "4")

    print("\n== 문제 1·4 — 타일 제목줄에서 닫는다")
    victim2 = ids[3]
    B.js("(()=>{const tabs=[...document.querySelectorAll('#tabs .tab')];"
         "const t=tabs.find(t=>t.dataset.id===%r); if(t) t.click();})()" % c2)
    time.sleep(0.8)
    tb = B.js("(()=>{const t=[...document.querySelectorAll('.tile')].find(e=>e.dataset.id===%r);"
              "if(!t) return null; const b=t.querySelector('.tb .cl'); if(!b) return null;"
              "const r=b.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % victim2)
    chk("타일 제목줄에 닫기 단추가 있다", tb is not None, tb)
    B.click(tb["x"], tb["y"]); time.sleep(0.3)
    chk("제목줄 안에 확인 줄이 열린다", B.js("!!document.querySelector('.tb .cfm')"))
    B.key("Escape", code="Escape", vk=27); time.sleep(0.4)
    chk("Esc 로 그만둘 수 있고 DELETE 는 0건", not B.js("!!document.querySelector('.tb .cfm')") and
        len([u for u in B.js("window.__dels") if u.endswith(victim2)]) == 0)
    B.click(tb["x"], tb["y"]); time.sleep(0.3)
    # 키보드만으로 부순다 — <button> 이라 포커스가 가 있다
    chk("확인이 열리면 Close 에 포커스가 있다", B.js("document.activeElement.className") == "cbtn yes",
        B.js("document.activeElement.className"))
    B.key("Enter", code="Enter", text="\r", vk=13); time.sleep(1.5)
    chk("키보드 Enter 로 DELETE 한 번", len([u for u in B.js("window.__dels") if u.endswith(victim2)]) == 1,
        B.js("window.__dels"))
    chk("타일이 사라졌다(B)", not B.js("!![...document.querySelectorAll('.tile')].find(e=>e.dataset.id===%r)" % victim2))
    chk("A 에서도 사라졌다", not any(r["id"] == victim2 for r in rows(A)))
    chk("데몬에서도 없다", victim2 not in [s["id"] for s in api("GET", "/api/sessions")[1]])

    print("\n== 고친 것 되짚기 — 지적 1·2·3")
    # 지적 3: Cancel 에 둔 포커스가 방송에 안 밀린다
    live = [s["id"] for s in api("GET", "/api/sessions")[1]]
    A.js("(()=>{const tabs=[...document.querySelectorAll('#tabs .tab')];"
         "const t=tabs.find(t=>t.dataset.id===%r); if(t) t.click();})()" % c1)
    time.sleep(0.6)
    cb = rowbox(A, ids[0], ".cl")
    A.click(cb["x"], cb["y"]); time.sleep(0.3)
    A.js("document.querySelector('#list .cfm .cbtn:not(.yes)').focus();1")
    hook(ids[1], "Stop", tok); time.sleep(0.8)
    fc = A.js("({cls:document.activeElement.className,txt:document.activeElement.textContent})")
    chk("[지적3] 방송 뒤에도 포커스가 Cancel 에 그대로다", fc["txt"] == "Cancel", fc)
    n0 = len(A.js("window.__dels"))
    A.key("Enter", code="Enter", text="\r", vk=13); time.sleep(0.8)
    chk("[지적3] 그 Enter 는 아무것도 안 부순다", len(A.js("window.__dels")) == n0,
        A.js("window.__dels")[n0:])
    # 지적 1: 접힌 묶음에서 확인 줄이 저 혼자 되살아나지 않는다
    hook(ids[0], "PermissionRequest", tok); time.sleep(0.6)
    A.click(g1["x"], g1["y"]); time.sleep(0.5)                     # 캔버스1 접기
    cb = rowbox(A, ids[0], ".cl")
    A.click(cb["x"], cb["y"]); time.sleep(0.3)
    chk("[지적1] 접힌 묶음의 기다리는 줄에 확인을 열었다", A.js("!!document.querySelector('#list .cfm')"))
    hook(ids[0], "Stop", tok); time.sleep(0.7)
    hook(ids[1], "UserPromptSubmit", tok); time.sleep(0.5)
    hook(ids[0], "PermissionRequest", tok); time.sleep(0.9)
    chk("[지적1] 다시 waiting 이 돼도 확인 줄이 안 되살아난다",
        not A.js("!!document.querySelector('#list .cfm')"),
        A.js("(()=>{const c=document.querySelector('#list .cfm');return c&&c.textContent;})()"))
    A.click(g1["x"], g1["y"]); time.sleep(0.5)                     # 다시 펴기
    # 지적 2: 남이 먼저 닫으면 조용하다
    v3 = ids[4]
    cbb = rowbox(B, v3, ".cl")
    if not cbb:
        B.js("(()=>{const tabs=[...document.querySelectorAll('#tabs .tab')];"
             "const t=tabs.find(t=>t.dataset.id===%r); if(t) t.click();})()" % c2)
        time.sleep(0.5); cbb = rowbox(B, v3, ".cl")
    cba = rowbox(A, v3, ".cl")
    B.click(cbb["x"], cbb["y"]); time.sleep(0.25)
    A.click(cba["x"], cba["y"]); time.sleep(0.25)
    ya = A.js("(()=>{const b=document.querySelector('#list .cfm .cbtn.yes');const r=b.getBoundingClientRect();"
              "return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
    yb2 = B.js("(()=>{const b=document.querySelector('#list .cfm .cbtn.yes');const r=b.getBoundingClientRect();"
               "return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
    for t, p in ((A, ya), (B, yb2)):
        t.cmd("Input.dispatchMouseEvent", type="mousePressed", x=p["x"], y=p["y"], button="left", clickCount=1, buttons=1)
    for t, p in ((A, ya), (B, yb2)):
        t.cmd("Input.dispatchMouseEvent", type="mouseReleased", x=p["x"], y=p["y"], button="left", clickCount=1, buttons=0)
    time.sleep(1.6)
    ta = A.js("document.querySelector('#toast').textContent")
    tb2 = B.js("document.querySelector('#toast').textContent")
    print("  toasts A/B:", repr(ta), repr(tb2))
    chk("[지적2] 진 쪽에도 오류 토스트가 없다", "close terminal" not in (ta + tb2), (ta, tb2))
    chk("[지적2] 세션은 양쪽에서 사라졌다",
        not any(r["id"] == v3 for r in rows(A)) and not any(r["id"] == v3 for r in rows(B)))

    print("\n== 스크린샷 (테마 둘)")
    # 보기 좋게: 캔버스 1 로 가고, 하나를 기다림으로 두고, 캔버스 2 묶음은 접어 둔다
    left = [s["id"] for s in api("GET", "/api/sessions")[1]]
    hook(ids[0], "PermissionRequest", tok)
    hook(ids[1], "UserPromptSubmit", tok)
    time.sleep(0.8)
    A.js("(()=>{const tabs=[...document.querySelectorAll('#tabs .tab')];"
         "const t=tabs.find(t=>t.dataset.id===%r); if(t) t.click();})()" % c1)
    time.sleep(0.6)
    # 목록 행의 확인 줄을 열어 둔 채로 찍는다 — 닫기가 무엇인지 그림이 말하게
    cb = rowbox(A, ids[1], ".cl")
    if cb:
        A.click(cb["x"], cb["y"]); time.sleep(0.4)
    shots = () if os.environ.get("PALMAR_NO_SHOTS") else (("light", os.path.join(SHOT, "close-light.png")), ("dark", os.path.join(SHOT, "close-dark.png")))
    for theme, path in shots:
        A.js("try{localStorage.setItem('palmar-theme',%r)}catch(e){};"
             "document.documentElement.dataset.theme=%r;"
             "document.querySelector('#theme').title='theme: '+%r;1" % (theme, theme, theme))
        time.sleep(0.8)
        A.shot(path)
        print("  ", path, os.path.getsize(path), "bytes")

    print("\n== 마무리")
    for sid in [s["id"] for s in api("GET", "/api/sessions")[1]]:
        api("DELETE", "/api/sessions/" + sid, None, tok)
    time.sleep(1.0)
    chk("남은 세션 0", api("GET", "/api/sessions")[1] == [])
    left = [f for f in os.listdir(os.path.expanduser("~/.palmar/run")) if f.endswith(".json")]
    chk("pane 설정 파일이 하나도 안 남았다", left == [], left)
finally:
    if ch: ch.kill()
    if d:
        d.terminate()
        try: d.wait(6)
        except Exception: d.kill()
    print("\n==== %d ok, %d FAIL" % (len(OK), len(BAD)))
    if BAD:
        print("FAILED:", BAD)
