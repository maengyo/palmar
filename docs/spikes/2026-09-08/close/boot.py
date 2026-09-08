"""데몬 하나 + 브라우저 하나 띄우는 공통 뼈대."""
import json, os, subprocess, sys, time, urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
# 격리된 HOME 과 로그는 **저장소 밖**에 쓴다 — 스파이크가 트리를 어지럽히면 안 된다.
SCRATCH = os.path.join(os.environ.get("TMPDIR", "/tmp"), "palmar-close")
os.makedirs(SCRATCH, exist_ok=True)


def start_daemon(port, home=None, log=None):
    home = home or os.path.join(SCRATCH, "home%d" % port)
    subprocess.run(["rm", "-rf", home])
    os.makedirs(home, 0o700)
    log = log or os.path.join(SCRATCH, "d%d.log" % port)
    env = {"HOME": home, "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "SHELL": "/bin/bash",
           "TERM": "dumb", "LANG": "en_US.UTF-8"}
    f = open(log, "wb")
    p = subprocess.Popen(["/usr/bin/python3", os.path.join(REPO, "server/palmard.py"),
                          "--port", str(port)], env=env, stdout=f, stderr=subprocess.STDOUT,
                         cwd=REPO)
    for _ in range(200):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/sessions" % port, timeout=1).read()
            break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("daemon did not come up; log=" + open(log).read()[-2000:])
    tok = open(os.path.join(home, ".palmar/run/token")).read().strip()
    return p, home, tok


def api(port, tok, method, path, body=None):
    url = "http://127.0.0.1:%d%s" % (port, path)
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


def hook(port, tok, pane, event):
    return api(port, tok, "POST", "/hook/claude?pane=%s" % pane, {"hook_event_name": event})
