import http.server, os
class H(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        n=int(self.headers.get("content-length","0"))
        open("/tmp/mini.jsonl","a").write(self.rfile.read(n).decode()+"\n")
        self.send_response(204); self.end_headers()
    def log_message(self,*a): pass
os.chdir(os.path.dirname(os.path.abspath(__file__)))
http.server.HTTPServer(("127.0.0.1",8899),H).serve_forever()
