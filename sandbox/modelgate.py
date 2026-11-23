#!/usr/bin/env python3
"""The sandbox's model gate: the only thing a sandbox session can reach
outside its container.

It listens on one Unix socket per session and speaks just enough HTTP for
osyr-chat: GET /v1/models and POST /v1/chat/completions. Nothing from the
visitor is passed on as it came: the request is rebuilt with the few fields
the chat uses, the model name and the key are put in here, and sizes, the
number of requests per session and how many run at once are capped. The
server's address and key never enter the container.

As a program, for tests (one session, until interrupted):

    modelgate.py SOCKET UPSTREAM_URL [--key-file F] [--key-header H] [--model M]

UPSTREAM_URL is an OpenAI-compatible base, e.g. http://127.0.0.1:8083/v1
"""
import argparse
import http.client
import json
import os
import socket
import ssl
import threading
import time
import urllib.parse

MAX_BODY = 512 * 1024        # bytes of one request (a long conversation with tool results)
MAX_MESSAGES = 200
MAX_REPLY_TOKENS = 2048      # as osyr-chat asks (MAX_REPLY)
MAX_TOOLS = 16
ROLES = ("system", "user", "assistant", "tool")
QUEUE_SECONDS = 180          # how long a request waits for its turn
UPSTREAM_SECONDS = 600


class Upstream:
    """The model server, and how many requests it takes at once."""

    def __init__(self, url, key=None, key_header="Authorization", model="", parallel=1, name=None,
                 extra=None):
        """'name': the server's own host name when 'url' reaches it by a loopback address
        (a server on this machine behind its own TLS front): sent as Host and SNI. Its
        certificate is then not checked: the connection never leaves the machine."""
        self.url = urllib.parse.urlsplit(url.rstrip("/"))
        self.name = name
        self.extra = extra or {}   # fields added to every request (the operator's, e.g. no reasoning)
        self.prefix = None         # (system text, tools): the start every session's conversation shares,
                                   # given by the operator, never taken from a visitor's request
        self.used = 0.0            # when the server last answered a request
        self.key, self.key_header, self.model = key, key_header, model
        self.turns = threading.BoundedSemaphore(parallel)
        self.waiting = 0
        self.lock = threading.Lock()

    def headers(self):
        h = {"Content-Type": "application/json", "User-Agent": "OSYR-sandbox/1.0", "Accept": "*/*"}
        if self.name:
            h["Host"] = self.name
        if self.key:
            h[self.key_header] = ("Bearer " + self.key) if self.key_header.lower() == "authorization" else self.key
        return h

    def connect(self):
        port = self.url.port or (443 if self.url.scheme == "https" else 80)
        if self.url.scheme == "https" and self.name:
            if self.url.hostname not in ("127.0.0.1", "::1", "localhost"):
                raise OSError("a server name is only for a loopback address")
            return LocalTLS(self.url.hostname, port, self.name)
        if self.url.scheme == "https":
            return http.client.HTTPSConnection(self.url.hostname, port, timeout=UPSTREAM_SECONDS,
                                               context=ssl.create_default_context())
        return http.client.HTTPConnection(self.url.hostname, port, timeout=UPSTREAM_SECONDS)


class LocalTLS(http.client.HTTPSConnection):
    """TLS to a server on this machine that answers to another name."""

    def __init__(self, host, port, name):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        super().__init__(host, port, timeout=UPSTREAM_SECONDS, context=ctx)
        self.tls_name, self.ctx = name, ctx

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self.ctx.wrap_socket(sock, server_hostname=self.tls_name)


def clean_message(m):
    """One message with only the fields of the chat API; None if it is not valid."""
    if not isinstance(m, dict) or m.get("role") not in ROLES:
        return None
    out = {"role": m["role"]}
    content = m.get("content")
    if isinstance(content, str) or content is None:
        out["content"] = content
    else:
        return None  # no images or other parts: the sandbox has no 'look'
    if m["role"] == "assistant" and isinstance(m.get("tool_calls"), list):
        calls = []
        for c in m["tool_calls"][:MAX_TOOLS]:
            f = c.get("function") if isinstance(c, dict) else None
            if not isinstance(f, dict) or not isinstance(f.get("name"), str):
                return None
            args = f.get("arguments")
            calls.append({"id": str(c.get("id", ""))[:80], "type": "function",
                          "function": {"name": f["name"][:80],
                                       "arguments": args if isinstance(args, str) else json.dumps(args)}})
        out["tool_calls"] = calls
    if m["role"] == "tool":
        out["tool_call_id"] = str(m.get("tool_call_id", ""))[:80]
        if isinstance(m.get("name"), str):
            out["name"] = m["name"][:80]
    return out


def clean_tool(t):
    f = t.get("function") if isinstance(t, dict) else None
    if not isinstance(f, dict) or not isinstance(f.get("name"), str):
        return None
    return {"type": "function", "function": {"name": f["name"][:80],
                                             "description": str(f.get("description", ""))[:4000],
                                             "parameters": f.get("parameters") if isinstance(f.get("parameters"), dict)
                                             else {"type": "object", "properties": {}}}}


def clean_request(body, model):
    """The request that goes to the server, or (None, reason)."""
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        return None, "no messages"
    if not 1 <= len(body["messages"]) <= MAX_MESSAGES:
        return None, "too many messages"
    messages = [clean_message(m) for m in body["messages"]]
    if None in messages:
        return None, "message not accepted"
    out = {"messages": messages, "stream": bool(body.get("stream"))}
    try:
        out["max_tokens"] = max(1, min(MAX_REPLY_TOKENS, int(body.get("max_tokens") or MAX_REPLY_TOKENS)))
    except (TypeError, ValueError):
        out["max_tokens"] = MAX_REPLY_TOKENS
    if out["stream"]:
        out["stream_options"] = {"include_usage": True}
    if isinstance(body.get("tools"), list) and body["tools"]:
        tools = [clean_tool(t) for t in body["tools"][:MAX_TOOLS]]
        if None in tools:
            return None, "tool not accepted"
        out["tools"] = tools
    if isinstance(body.get("temperature"), (int, float)):
        out["temperature"] = max(0.0, min(2.0, float(body["temperature"])))
    if model:
        out["model"] = model
    return out, None


class Gate:
    """The gate of one session. start() listens; close() ends it."""

    def __init__(self, path, upstream, max_requests=150, log=None):
        self.path, self.up, self.left, self.log = path, upstream, max_requests, log or (lambda **kw: None)
        self.requests = self.tokens_in = self.tokens_out = 0
        self.closed = False
        self.srv = socket.socket(socket.AF_UNIX)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        self.srv.bind(path)
        os.chmod(path, 0o600)
        self.srv.listen(8)

    def start(self):
        threading.Thread(target=self.loop, daemon=True).start()
        return self

    def loop(self):
        while not self.closed:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                break
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def close(self):
        self.closed = True
        try:
            self.srv.close()
            os.unlink(self.path)
        except OSError:
            pass

    # --- one connection, one request ------------------------------------------------

    def serve(self, conn):
        try:
            conn.settimeout(30)
            method, target, body = self.read_request(conn)
            conn.settimeout(UPSTREAM_SECONDS)
            path = urllib.parse.urlsplit(target).path.rstrip("/")
            if method == "GET" and path == "/v1/models":
                data = {"object": "list", "data": [{"id": self.up.model or "osyr-sandbox", "object": "model"}]}
                return self.answer(conn, 200, data)
            if method != "POST" or path != "/v1/chat/completions":
                return self.answer(conn, 404, {"error": {"message": "not available in the sandbox"}})
            try:
                req, why = clean_request(json.loads(body), self.up.model)
            except ValueError:
                req, why = None, "not JSON"
            if req is None:
                return self.answer(conn, 400, {"error": {"message": f"request not accepted: {why}"}})
            if self.left <= 0:
                return self.answer(conn, 429, {"error": {"message": "this sandbox session has used up its requests"}})
            self.left -= 1
            self.forward(conn, req)
        except (OSError, ValueError, http.client.HTTPException) as e:
            self.log(event="gate-error", error=str(e)[:200])
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def read_request(self, conn):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(65536)
            if not chunk or len(data) > 32768:
                raise ValueError("bad request head")
            data += chunk
        head, body = data.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        method, target = lines[0].split(" ")[:2]
        length = 0
        for line in lines[1:]:
            k, _, v = line.partition(":")
            if k.strip().lower() == "content-length":
                length = int(v.strip())
            if k.strip().lower() == "transfer-encoding":
                raise ValueError("chunked requests are not accepted")
        if not 0 <= length <= MAX_BODY:
            raise ValueError("request too large")
        while len(body) < length:
            chunk = conn.recv(65536)
            if not chunk:
                raise ValueError("request cut short")
            body += chunk
        return method, target, body[:length]

    def answer(self, conn, status, data):
        body = json.dumps(data).encode()
        reason = http.client.responses.get(status, "")
        conn.sendall(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)

    def forward(self, conn, req):
        up = self.up
        with up.lock:
            up.waiting += 1
        got = up.turns.acquire(timeout=QUEUE_SECONDS)
        with up.lock:
            up.waiting -= 1
        if not got:
            return self.answer(conn, 503, {"error": {"message": "the sandbox is busy; try again in a moment"}})
        t0 = time.time()
        usage = {}
        try:
            c = up.connect()
            c.request("POST", up.url.path + "/chat/completions", body=json.dumps({**req, **up.extra}).encode(),
                      headers=up.headers())
            r = c.getresponse()
            if r.status != 200:
                detail = r.read(2000).decode(errors="replace")
                self.log(event="upstream-error", status=r.status, detail=detail[:300])
                if r.status == 400:
                    # About the request itself (too long, a field the server does not take):
                    # the chat reads it to trim or to go on without tools
                    try:
                        return self.answer(conn, 400, {"error": json.loads(detail)["error"]})
                    except (ValueError, KeyError, TypeError):
                        pass
                # Any other text of the server may name it: the visitor only gets the status
                return self.answer(conn, 502 if r.status in (401, 403) else r.status,
                                   {"error": {"message": f"the model server answered {r.status}"}})
            ctype = "text/event-stream" if req["stream"] else "application/json"
            conn.sendall(f"HTTP/1.1 200 OK\r\nContent-Type: {ctype}\r\nCache-Control: no-cache\r\n"
                         "Connection: close\r\n\r\n".encode())
            tail = b""
            while True:
                chunk = r.read1(65536)
                if not chunk:
                    break
                conn.sendall(chunk)
                tail = (tail + chunk)[-4096:]
            usage = last_usage(tail)
            up.used = time.time()
        finally:
            up.turns.release()
            try:
                c.close()
            except (OSError, UnboundLocalError):
                pass
            self.requests += 1
            self.tokens_in += usage.get("prompt_tokens") or 0
            self.tokens_out += usage.get("completion_tokens") or 0
            self.log(event="request", seconds=round(time.time() - t0, 1), messages=len(req["messages"]),
                     prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))


def warm(up, log=None):
    """Has the server read the sessions' common start (the chat's fixed prompt and tools)
    so that the next visitor does not wait for it: on a CPU that is minutes. One token is
    asked for. Returns the seconds it took, or None."""
    if not up.prefix or not up.turns.acquire(timeout=1):
        return None
    t0 = time.time()
    try:
        system, tools = up.prefix
        req = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": "hello"}],
               "max_tokens": 1, "stream": False, **({"tools": tools} if tools else {}),
               **({"model": up.model} if up.model else {}), **up.extra}
        c = up.connect()
        try:
            c.request("POST", up.url.path + "/chat/completions", body=json.dumps(req).encode(),
                      headers=up.headers())
            r = c.getresponse()
            r.read()
            if r.status == 200:
                up.used = time.time()
            if log:
                log(event="warm", status=r.status, seconds=round(time.time() - t0, 1))
            return time.time() - t0 if r.status == 200 else None
        finally:
            c.close()
    except (OSError, http.client.HTTPException) as e:
        if log:
            log(event="warm", error=str(e)[:200])
        return None
    finally:
        up.turns.release()


def last_usage(tail):
    """The "usage" object at the end of an answer (stream or not), or {}."""
    text = tail.decode(errors="replace")
    i = text.rfind('"usage"')
    if i < 0:
        return {}
    start = text.find("{", i)
    depth = 0
    for j in range(start, len(text)):
        depth += text[j] == "{"
        depth -= text[j] == "}"
        if depth == 0:
            try:
                return json.loads(text[start:j + 1])
            except ValueError:
                return {}
    return {}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("socket")
    ap.add_argument("upstream")
    ap.add_argument("--key-file")
    ap.add_argument("--key-header", default="Authorization")
    ap.add_argument("--model", default="")
    ap.add_argument("--parallel", type=int, default=1)
    a = ap.parse_args()
    key = open(a.key_file).read().strip() if a.key_file else None
    gate = Gate(a.socket, Upstream(a.upstream, key, a.key_header, a.model, a.parallel),
                log=lambda **kw: print(json.dumps(kw), flush=True)).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        gate.close()


if __name__ == "__main__":
    main()
