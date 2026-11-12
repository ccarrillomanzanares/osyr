"""Fake llama-server whose warm-up (/completion) is slow, like a 2014 CPU.

Usage: slow_llama.py PORT SECONDS. The chat answers "Local answer".
"""
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
WARM = float(sys.argv[2])
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def j(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/health": return self.j(200, {"status": "ok"})
        if self.path == "/props": return self.j(200, {"model_path": "/m/q.gguf", "default_generation_settings": {"n_ctx": 16384}, "n_ctx": 16384})
        if self.path.startswith("/v1/models"): return self.j(200, {"data": [{"id": "local"}]})
        self.j(404, {})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        sys.stderr.write(f"{time.strftime('%X')} POST {self.path}\n"); sys.stderr.flush()
        if self.path == "/apply-template":
            return self.j(200, {"prompt": "<|im_start|>system\n" + "x" * 500 + "<|im_end|>\n<|im_start|>user\n@@Q@@"})
        if self.path.startswith("/slots/0?action=restore"): return self.j(404, {"error": "no"})
        if self.path.startswith("/slots"): return self.j(200, {})
        if self.path == "/completion":
            time.sleep(WARM); return self.j(200, {"content": ""})
        if self.path == "/v1/chat/completions":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for c in [{"choices": [{"delta": {"content": "Local answer"}}]}, {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}]:
                self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n"); return
        self.j(404, {})
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
