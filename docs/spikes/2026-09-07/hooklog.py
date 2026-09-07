import sys, time, json, os
raw = sys.stdin.read()
with open(os.environ["PALMER_HOOK_LOG"], "a") as f:
    f.write(json.dumps({"t": time.time(), "arg": sys.argv[1] if len(sys.argv) > 1 else None, "payload": raw.strip()}) + "\n")
sys.exit(0)
