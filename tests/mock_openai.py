"""Fake OpenAI-compatible server for the external provider tests.

Requires the key in X-API-Key (like the VPS server), gives the context in
/v1/models, answers by streaming and, like OpenAI, rejects what it does not
know and what exceeds the context. Like Cloudflare, it rejects the Python user agent. Usage: mock_openai.py PORT [ctx-in-models|no-ctx|train-ctx]
(train-ctx: like llama.cpp behind a proxy, only meta.n_ctx_train)
"""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
KEY, HDR, CTX = "secret123", "X-API-Key", 1500
MODE = sys.argv[2] if len(sys.argv) > 2 else "ctx-in-models"
class H(BaseHTTPRequestHandler):
    def auth(self):
        # Like Cloudflare in front of ccmai.org: Python's default user agent is kept out
        if self.headers.get("User-Agent", "").startswith("Python-urllib"):
            self.send_response(403); self.end_headers(); self.wfile.write(b'{"error":"bot"}'); return False
        if self.headers.get(HDR) != KEY:
            self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"bad key"}'); return False
        return True
    def do_GET(self):
        if not self.auth(): return
        if self.path == "/v1/models":
            m = {"id": "mock-35b", "object": "model"}
            if MODE == "ctx-in-models": m["context_length"] = CTX
            if MODE == "train-ctx": m["meta"] = {"n_ctx_train": 262144}
            body = json.dumps({"object": "list", "data": [m]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        if not self.auth(): return
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        bad = [k for k in req if k not in ("model", "messages", "stream", "stream_options", "max_tokens")]
        if bad:  # like OpenAI: unknown parameters -> 400
            self.send_response(400); self.end_headers(); self.wfile.write(json.dumps({"error": {"message": f"Unrecognized request argument supplied: {bad[0]}"}}).encode()); return
        ntok = sum(len(m["content"]) for m in req["messages"]) // 4
        if ntok > CTX:
            self.send_response(400); self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": f"This model's maximum context length is {CTX} tokens. However, your messages resulted in {ntok} tokens.", "code": "context_length_exceeded"}}).encode()); return
        n_turns = sum(1 for m in req["messages"] if m["role"] == "user")
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        for w in f"Remote answer ({req.get('model')}), turns seen: {n_turns}.".split(" "):
            self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": w + " "}}]}).encode() + b"\n\n")
        self.wfile.write(b"data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": ntok, "completion_tokens": 6}}).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
    def log_message(self, *a): pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
