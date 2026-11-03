#!/usr/bin/env python3
"""OSYR diagnostics receiver (runs on the VPS, behind Apache).

osyr-diag uploads a .tar.gz with POST to https://ccmai.org/osyr-diag/upload;
Apache passes it to 127.0.0.1:8091. Standard library only.

It also pairs AI providers (osyr ai pair, see pair.py in the image): the
laptop requests a code with its RSA public key; on the /osyr-diag/pair page
the browser encrypts the API key with it and only the ciphertext is kept
here, in memory, until the laptop collects it (or it expires).

Usage: diag-server.py [--port 8091] [--dir ~/osyr-diag] [--token-file ~/.config/osyr-diag/token]
"""
import argparse
import hmac
import json
import threading
import os
import re
import secrets
import shutil
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_UPLOAD = 64 * 1024 * 1024    # per upload
MAX_TOTAL = 2 * 1024 ** 3        # on disk; above this, uploads are rejected
MIN_INTERVAL = 10                # seconds between uploads from the same IP

args = None
token = b""
last_by_ip = {}

# --- Pairing -------------------------------------------------------------------------
PAIR_TTL = 600                    # seconds a code is valid
PAIR_MAX = 500                    # pending codes at once
CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
pairs = {}                        # code -> {pubkey, poll, expires, payload}
pair_lock = threading.Lock()
hits = {}                         # (ip, kind) -> [timestamps] to rate-limit attempts
LIMITS = {"new": (20, 3600), "guess": (40, 600)}


def limited(ip, kind):
    n, window = LIMITS[kind]
    now = time.monotonic()
    with pair_lock:
        h = [t for t in hits.get((ip, kind), []) if now - t < window]
        h.append(now)
        hits[(ip, kind)] = h
        if len(hits) > 1000:  # drop IPs whose attempts have expired (otherwise it grows forever)
            for k in [k for k, v in hits.items() if not v or now - v[-1] >= LIMITS[k[1]][1]]:
                del hits[k]
        return len(h) > n


def forget_old_uploaders(now):
    """last_by_ip only matters for MIN_INTERVAL: do not keep every IP forever."""
    if len(last_by_ip) > 1000:
        for ip in [ip for ip, t in last_by_ip.items() if now - t >= MIN_INTERVAL]:
            del last_by_ip[ip]


def clean_code(c):
    return "".join(ch for ch in (c or "").upper() if ch.isalnum())[:12]


def expire_pairs():
    now = time.monotonic()
    for c in [c for c, v in pairs.items() if v["expires"] < now]:
        del pairs[c]


def text_field(d, key, maxlen):
    v = d.get(key, "")
    return v.strip()[:maxlen] if isinstance(v, str) else ""


PAIR_PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pair.html"), encoding="utf-8").read()


def dir_size(path):
    return sum(e.stat().st_size for e in os.scandir(path) if e.is_file())


class Handler(BaseHTTPRequestHandler):
    server_version = "osyr-diag"

    def reply(self, code, text):
        body = (text + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def client_ip(self):
        # Apache appends the real IP at the end; the ones before it can be forged by the client
        return self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[-1].strip()

    def read_json(self, maxlen=8192):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if n <= 0 or n > maxlen:
            return None
        try:
            d = json.loads(self.rfile.read(n))
        except ValueError:
            return None
        return d if isinstance(d, dict) else None

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path.rstrip("/") == "/osyr-diag":
            self.reply(200, "osyr-diag receiver OK")
        elif path.rstrip("/") == "/osyr-diag/pair":
            body = PAIR_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; form-action 'none'; frame-ancestors 'none'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/osyr-diag/pair/key":
            if limited(self.client_ip(), "guess"):
                return self.send_json(429, {"error": "too many attempts, wait a few minutes"})
            code = clean_code(dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("c"))
            with pair_lock:
                expire_pairs()
                v = pairs.get(code)
                if not v or v["payload"]:
                    return self.send_json(404, {"error": "unknown or expired code"})
                return self.send_json(200, {"pubkey": v["pubkey"]})
        elif path == "/osyr-diag/pair/poll":
            poll = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            with pair_lock:
                expire_pairs()
                code = next((c for c, v in pairs.items() if hmac.compare_digest(v["poll"], poll)), None)
                if not code:
                    return self.send_json(404, {"error": "unknown or expired"})
                if not pairs[code]["payload"]:
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload = pairs.pop(code)["payload"]   # delivered once, then deleted
            print(f"paired {code}", flush=True)
            return self.send_json(200, payload)
        else:
            self.reply(404, "not found")

    def pair_new(self):
        if limited(self.client_ip(), "new"):
            return self.send_json(429, {"error": "too many pairing codes, wait a while"})
        d = self.read_json()
        pub = text_field(d or {}, "pubkey", 1200)
        if not pub.startswith("-----BEGIN PUBLIC KEY-----"):
            return self.send_json(400, {"error": "pubkey required"})
        with pair_lock:
            expire_pairs()
            if len(pairs) >= PAIR_MAX:
                return self.send_json(503, {"error": "busy, try again later"})
            code = "".join(secrets.choice(CODE_CHARS) for _ in range(6))
            while code in pairs:
                code = "".join(secrets.choice(CODE_CHARS) for _ in range(6))
            poll = secrets.token_urlsafe(32)
            pairs[code] = {"pubkey": pub, "poll": poll, "payload": None,
                           "expires": time.monotonic() + PAIR_TTL}
        url = f"{args.public_url.rstrip('/')}/osyr-diag/pair?c={code}"
        return self.send_json(200, {"code": f"{code[:3]}-{code[3:]}", "poll": poll, "url": url,
                                    "expires": PAIR_TTL})

    def pair_submit(self):
        if limited(self.client_ip(), "guess"):
            return self.send_json(429, {"error": "too many attempts, wait a few minutes"})
        d = self.read_json()
        if d is None:
            return self.send_json(400, {"error": "bad request"})
        code = clean_code(d.get("code"))
        prov = d.get("provider") if isinstance(d.get("provider"), dict) else {}
        provider = {k: text_field(prov, k, n) for k, n in
                    (("name", 32), ("label", 80), ("url", 300), ("key_header", 40), ("model", 120))}
        ctx = prov.get("context")
        if isinstance(ctx, int) and not isinstance(ctx, bool) and 1024 <= ctx <= 10_000_000:
            provider["context"] = ctx  # server tokens, if the page knows them
        key_enc = text_field(d, "key_enc", 1100)
        if not provider["url"].startswith(("https://", "http://")):
            return self.send_json(400, {"error": "provider URL required"})
        with pair_lock:
            expire_pairs()
            v = pairs.get(code)
            if not v or v["payload"]:
                return self.send_json(404, {"error": "unknown or expired code"})
            v["payload"] = {"provider": provider, "key_enc": key_enc}
        return self.send_json(200, {"ok": True})

    def do_POST(self):
        if self.path == "/osyr-diag/pair/new":
            return self.pair_new()
        if self.path == "/osyr-diag/pair/submit":
            return self.pair_submit()
        if self.path != "/osyr-diag/upload":
            return self.reply(404, "not found")
        auth = self.headers.get("Authorization", "").encode()
        if not hmac.compare_digest(auth, b"Bearer " + token):
            return self.reply(403, "bad token")
        ip = self.client_ip()
        now = time.monotonic()
        if now - last_by_ip.get(ip, -MIN_INTERVAL) < MIN_INTERVAL:
            return self.reply(429, "too many uploads, wait a few seconds")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return self.reply(411, "Content-Length required")
        if length > MAX_UPLOAD:
            return self.reply(413, f"too large (max {MAX_UPLOAD // 2**20} MB)")
        if dir_size(args.dir) + length > MAX_TOTAL:
            return self.reply(507, "receiver full")
        forget_old_uploaders(now)
        last_by_ip[ip] = now

        # Name: date + host the client declares (sanitized) + random suffix
        host = re.sub(r"[^A-Za-z0-9_-]", "", self.headers.get("X-OSYR-Host", ""))[:32] or "unknown"
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{host}-{secrets.token_hex(3)}.tar.gz"
        tmp = os.path.join(args.dir, "." + name + ".part")
        try:
            with open(tmp, "wb") as f:
                left = length
                while left:
                    chunk = self.rfile.read(min(left, 1 << 16))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left:
                os.unlink(tmp)
                return self.reply(400, "upload truncated")
            os.rename(tmp, os.path.join(args.dir, name))
        except OSError as e:  # client hung up, disk full...: no half-written .part left behind
            try:
                os.unlink(tmp)
            except OSError:
                pass
            print(f"upload from {ip} failed: {e}", flush=True)
            return self.reply(500, "upload failed")
        print(f"received {name} ({length} bytes) from {ip}", flush=True)
        self.reply(200, name)

    def log_message(self, fmt, *a):
        pass   # uploads are already logged above; no noise for every request


def main():
    global args, token
    p = argparse.ArgumentParser(description="OSYR diagnostics receiver")
    p.add_argument("--port", type=int, default=8091)
    p.add_argument("--dir", default=os.path.expanduser("~/osyr-diag"))
    p.add_argument("--token-file", default=os.path.expanduser("~/.config/osyr-diag/token"))
    p.add_argument("--public-url", default="https://ccmai.org", help="where Apache serves /osyr-diag/")
    args = p.parse_args()
    try:
        token = open(args.token_file, "rb").read().strip()
    except OSError as e:
        sys.exit(f"diag-server: cannot read token: {e}")
    if len(token) < 16:
        sys.exit("diag-server: token too short (min 16 chars)")
    os.makedirs(args.dir, exist_ok=True)
    for e in os.scandir(args.dir):  # half-finished uploads from a previous run
        if e.name.startswith(".") and e.name.endswith(".part"):
            os.unlink(e.path)
    print(f"diag-server on 127.0.0.1:{args.port}, saving to {args.dir}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
