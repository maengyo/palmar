"""F1: 접힌 캔버스 묶음에서 rowConfirm 이 저 혼자 되살아나는가 (지적 1)."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from boot import start_daemon, api, hook
from cdp import Chrome, Tab

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8832
d = ch = None
try:
    d, home, tok = start_daemon(PORT)
    print("daemon pid", d.pid, "port", PORT)
    st, cvs = api(PORT, tok, "GET", "/api/canvases")
    c1 = cvs[0]["id"]
    st, c2 = api(PORT, tok, "POST", "/api/canvases", {"name": "two"})
    c2 = c2["id"]
    ses = []
    for i, cv in enumerate([c1, c1, c2]):
        st, s = api(PORT, tok, "POST", "/api/sessions", {"cwd": home, "canvas": cv, "name": "s%d" % i})
        assert st == 201, (st, s)
        ses.append(s["id"])
    aa, bb, cc = ses
    ch = Chrome(port=9333)
    t = ch.new_tab("http://127.0.0.1:%d/" % PORT)
    t.cmd("Page.enable"); t.cmd("Runtime.enable"); t.cmd("Network.enable")
    time.sleep(2.5)
    # aa 를 waiting 으로
    hook(PORT, tok, aa, "PermissionRequest")
    time.sleep(0.6)
    # canvas 1 묶음 접기 — 머리글을 진짜 마우스로 누른다
    box = t.js("(()=>{const g=[...document.querySelectorAll('#list .grp.cvg')]"
               ".find(g=>g.dataset.canvas===%r); if(!g) return null;"
               "const r=g.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % c1)
    print("F1-a group header box:", box)
    t.click(box["x"], box["y"]); time.sleep(0.4)
    print("F1-b collapsed?", t.js("(()=>{const g=[...document.querySelectorAll('#list .grp.cvg')]"
          ".find(g=>g.dataset.canvas===%r); return g && g.classList.contains('collapsed');})()" % c1))
    print("F1-c rows in list:", t.js("[...document.querySelectorAll('#list .ses')].map(e=>e.dataset.id)"),
          "\n     aa=", aa, "bb=", bb, "cc=", cc)
    # 접힌 묶음에 남은 waiting 줄(aa)의 × 를 진짜로 누른다
    cb = t.js("(()=>{const it=[...document.querySelectorAll('#list .ses')].find(e=>e.dataset.id===%r);"
              "if(!it) return null; const b=it.querySelector('.cl'); const r=b.getBoundingClientRect();"
              "return {x:r.x+r.width/2,y:r.y+r.height/2};})()" % aa)
    print("F1-d close-x box:", cb)
    t.click(cb["x"], cb["y"]); time.sleep(0.3)
    print("F1-e confirm open?", t.js("!!document.querySelector('#list .cfm')"))
    # 그 세션의 훅 하나로 waiting 을 벗어나게 한다 → 접힌 묶음에서 줄이 빠진다
    hook(PORT, tok, aa, "Stop"); time.sleep(0.6)
    print("F1-f after Stop: rowDrawn=",
          t.js("!![...document.querySelectorAll('#list .ses')].find(e=>e.dataset.id===%r)" % aa),
          "confirmOnScreen=", t.js("!!document.querySelector('#list .cfm')"))
    # 남의 세션 훅으로 목록을 몇 번 다시 짓는다 — 그래도 안 되살아나야 한다
    for _ in range(3):
        hook(PORT, tok, cc, "UserPromptSubmit"); time.sleep(0.25)
    print("F1-g after other hooks: confirmOnScreen=", t.js("!!document.querySelector('#list .cfm')"))
    # 다시 waiting 으로 되돌린다 — 여기서 되살아나면 지적이 맞다
    hook(PORT, tok, aa, "PermissionRequest"); time.sleep(0.8)
    res = t.js("(()=>{const c=document.querySelector('#list .cfm');"
               "if(!c) return {cfm:0};"
               "const row=c.closest('.ses'); const y=c.querySelector('.cbtn.yes');"
               "const r=y.getBoundingClientRect();"
               "const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);"
               "return {cfm:1, who:row&&row.dataset.id, btn:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width)},"
               "hit: !!(hit&&hit.classList.contains('yes')), focus:document.activeElement.className||document.activeElement.tagName};})()")
    print("F1-h RESURRECTED?", json.dumps(res), " aa=", aa)
    t.shot(os.path.join(os.environ.get("TMPDIR", "/tmp"), "palmar-close-f1.png"))
    print("VERDICT finding1:", "REPRODUCED" if res.get("cfm") else "not reproduced")
finally:
    if ch: ch.kill()
    if d:
        d.terminate()
        try: d.wait(5)
        except Exception: d.kill()
