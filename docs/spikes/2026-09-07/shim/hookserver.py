import http.server, json, sys, time
LOG=sys.argv[1]
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n=int(self.headers.get("content-length","0")); body=self.rfile.read(n)
        try: ev=json.loads(body).get("hook_event_name","?")
        except Exception: ev="unparsed"
        open(LOG,"a").write(f"HTTP-{ev}:{time.time()} hdr-pane={self.headers.get('x-palmer-pane','-')} bytes={n}\n")
        self.send_response(200); self.end_headers()
    def log_message(self,*a): pass
http.server.HTTPServer(("127.0.0.1",8899),H).serve_forever()
