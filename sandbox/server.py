#!/usr/bin/env python3
"""The web sandbox's service: a terminal in the browser wired to a disposable
OSYR session (sandbox/session) through a WebSocket.

It listens on 127.0.0.1 only; the web server in front (Apache) passes it
/osyr/try/ws. For each visitor it waits for a free place, opens the session's
model gate (sandbox/modelgate.py), starts the session on a pseudo-terminal
and carries the keys one way and the screen the other. It ends the session
when its time is up, when nobody types for a while or when the page closes.

    server.py CONFIG          (a JSON file; see DEFAULTS below)
    server.py CONFIG --static DIR   also serve the page from DIR (tests)

Messages: text frames are JSON, binary frames are terminal output.
  browser -> {"t": "start", "code": "...", "cols": 80, "rows": 24}
             {"t": "in", "d": "typed text"}   {"t": "size", "cols": 80, "rows": 24}
  service -> {"t": "queue", "position": 2}   {"t": "started", "minutes": 15}
             {"t": "end", "why": "time" | "idle" | "exit" | "off" | "code" | "busy" | "limit"}

No conversation is written down: the log has one line per session with its
length and counts. Standard library only.
"""
import base64
import collections
import fcntl
import hashlib
import ipaddress
import json
import os
import pty
import select
import shutil
import signal
import socket
import struct
import sys
import tempfile
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import modelgate  # noqa: E402

DEFAULTS = {
    "listen": "127.0.0.1", "port": 8095,
    "upstream": "http://127.0.0.1:8083/v1",   # OpenAI-compatible base of the model server
    "upstream_name": None,    # the server's host name, when "upstream" is a loopback address with TLS
    "key_file": None, "key_header": "Authorization", "model": "", "context": 16384,
    "extra": {},              # fields added to every model request, e.g. {"chat_template_kwargs":
                              # {"enable_thinking": false}} for a llama-server
    "prefix_file": None,      # the chat's fixed prompt and tools (sandbox/prefix.py writes it at install)
    "warm_minutes": 0,        # have the model server read that prompt ahead of the visitors, and again
                              # after this long without a request (0: never)
    "parallel": 1,            # requests the model server takes at once
    "sessions": 3,            # sessions at once; the rest wait in a queue
    "queue": 20,              # visitors waiting; beyond that, "busy"
    "minutes": 15, "idle_minutes": 5,
    "requests": 150,          # model requests per session
    "per_address_hour": 6,    # sessions one address may start in an hour
    "connections": 60,        # terminal connections open at once, whatever their state...
    "per_address_connections": 4,   # ...and from one address
    "code": None,             # access code while the sandbox is private; None: open
    "off_file": None,         # if this file exists, nobody gets in (the switch)
    "log": None,              # one JSON line per event; None: standard output
    "work": None,             # folder for the sessions' folders (default: a temporary one)
    "session": os.path.join(HERE, "session"),
    "trusted_proxies": [],    # networks of the proxies before the web server (a CDN): skipped when
                              # looking for the visitor's address
    "session_prefix": [],     # e.g. ["sudo", "-n", "-u", "osyr-sandbox"] to run it as another user
}
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 64 * 1024
PING_SECONDS = 25             # a proxy on the way may close a connection that says nothing


class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        key = None
        if cfg["key_file"]:
            with open(cfg["key_file"]) as f:
                key = f.read().strip()
        self.up = modelgate.Upstream(cfg["upstream"], key, cfg["key_header"], cfg["model"], cfg["parallel"],
                                     cfg["upstream_name"], cfg["extra"])
        self.lock = threading.Condition()
        self.running = 0
        self.waiting = collections.deque()   # tickets, in order of arrival
        self.starts = collections.defaultdict(collections.deque)  # address -> times of its sessions
        self.active = set()                  # addresses with a session or a place in the queue
        self.open = collections.Counter()    # address -> its open terminal connections
        self.work = cfg["work"] or tempfile.mkdtemp(prefix="osyr-sandbox-")
        os.makedirs(self.work, exist_ok=True)
        self.log_file = open(cfg["log"], "a") if cfg["log"] else sys.stdout
        self.proxies = [ipaddress.ip_network(n) for n in cfg["trusted_proxies"]]
        if cfg["prefix_file"] and cfg["warm_minutes"]:
            with open(cfg["prefix_file"]) as f:
                saved = json.load(f)
            self.up.prefix = (saved["system"], saved.get("tools"))
            threading.Thread(target=self.keep_warm, daemon=True).start()

    def keep_warm(self):
        """Every session's conversation starts with the same prompt (the chat's own; no
        visitor's words): on a CPU the model server takes minutes to read it."""
        while True:
            if not self.off() and time.time() - self.up.used > self.cfg["warm_minutes"] * 60:
                if modelgate.warm(self.up, self.log) is None:
                    self.up.used = time.time() - self.cfg["warm_minutes"] * 60 + 120  # again in two minutes
            time.sleep(30)

    def visitor(self, peer, forwarded):
        """The visitor's address: the peer, or, behind the web server on this machine, the
        last address of X-Forwarded-For that is not one of the trusted proxies."""
        if peer not in ("127.0.0.1", "::1") or not forwarded:
            return bucket(peer)
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        for hop in reversed(hops):
            try:
                ip = ipaddress.ip_address(hop)
            except ValueError:
                return hops[-1]
            if not any(ip in n for n in self.proxies):
                return bucket(hop)
        return hops[-1] if hops else peer

    def connect(self, address):
        """Counts a terminal connection in; False if there are too many."""
        with self.lock:
            if (sum(self.open.values()) >= self.cfg["connections"]
                    or self.open[address] >= self.cfg["per_address_connections"]):
                return False
            self.open[address] += 1
            return True

    def disconnect(self, address):
        with self.lock:
            self.open[address] -= 1
            if self.open[address] <= 0:
                del self.open[address]

    def log(self, **kw):
        kw = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), **kw}
        with self.lock:
            self.log_file.write(json.dumps(kw) + "\n")
            self.log_file.flush()

    def off(self):
        return bool(self.cfg["off_file"]) and os.path.exists(self.cfg["off_file"])

    # --- places ------------------------------------------------------------------------

    def admit(self, address):
        """A ticket for the queue, or the reason there is none."""
        now = time.time()
        with self.lock:
            mine = self.starts[address]
            while mine and mine[0] < now - 3600:
                mine.popleft()
            if address in self.active or len(mine) >= self.cfg["per_address_hour"]:
                return None, "limit"
            if len(self.waiting) >= self.cfg["queue"]:
                return None, "busy"
            ticket = object()
            self.waiting.append(ticket)
            self.active.add(address)
            return ticket, None

    def wait_turn(self, ticket, tell, alive):
        """Blocks until it is this ticket's turn; tells the position as it changes.
        False if the visitor left (alive() is False) before."""
        last = None
        with self.lock:
            while True:
                self.lock.release()   # reading the visitor's socket must not hold everybody up
                try:
                    here = alive()
                finally:
                    self.lock.acquire()
                if not here:
                    self.waiting.remove(ticket)
                    self.lock.notify_all()
                    return False
                pos = self.waiting.index(ticket)
                if pos == 0 and self.running < self.cfg["sessions"]:
                    self.waiting.popleft()
                    self.running += 1
                    self.lock.notify_all()
                    return True
                if pos != last:
                    last = pos
                    self.lock.release()
                    try:
                        tell(pos + 1)
                    finally:
                        self.lock.acquire()
                self.lock.wait(2)

    def leave(self, address, ran):
        with self.lock:
            self.active.discard(address)
            if ran:
                self.running -= 1
            self.lock.notify_all()

    # --- one session -------------------------------------------------------------------

    def session(self, ws, address, cols, rows):
        cfg = self.cfg
        d = tempfile.mkdtemp(dir=self.work)
        os.chmod(d, 0o755)
        try:
            gate = modelgate.Gate(os.path.join(d, "model.sock"), self.up, cfg["requests"],
                                  log=lambda **kw: None).start()
        except OSError as e:  # e.g. the work folder's path is too long for a socket
            shutil.rmtree(d, ignore_errors=True)
            self.log(event="error", error=f"model gate: {e}")
            return "off"
        if cfg["session_prefix"]:
            os.chmod(os.path.join(d, "model.sock"), 0o666)  # the session's user must reach it; the folder is ours
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "TERM": "xterm-256color", "HOME": d,
               "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", ""),
               "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")}
        t0, why = time.time(), "exit"
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.execvpe(cfg["session_prefix"][0] if cfg["session_prefix"] else cfg["session"],
                           [*cfg["session_prefix"], cfg["session"], "--minutes", str(cfg["minutes"]),
                            "--context", str(cfg["context"]), d], env)
            finally:
                os._exit(127)
        resize(fd, cols, rows)
        ws.send_json({"t": "started", "minutes": cfg["minutes"]})
        last_input = time.time()   # or output: waiting for a slow answer is not being away
        try:
            while True:
                now = time.time()
                if now - t0 > cfg["minutes"] * 60:
                    why = "time"
                    break
                if now - last_input > cfg["idle_minutes"] * 60:
                    why = "idle"
                    break
                if self.off():
                    why = "off"
                    break
                ws.keepalive()
                r, _, _ = select.select([fd, ws.sock], [], [], 5)
                if fd in r:
                    try:
                        data = os.read(fd, 16384)
                    except OSError:
                        data = b""
                    if not data:
                        break
                    last_input = time.time()
                    ws.send(data, binary=True)
                while ws.open and (ws.sock in r or ws.buf):
                    r = []
                    msg = ws.receive()
                    if msg is None:
                        break
                    if msg.get("t") == "in" and isinstance(msg.get("d"), str):
                        last_input = time.time()
                        os.write(fd, msg["d"].encode()[:4096])
                    elif msg.get("t") == "size":
                        resize(fd, msg.get("cols"), msg.get("rows"))
                if not ws.open:
                    why = "left"
                    break
        except (OSError, ValueError):
            why = "left"
        finally:
            stop(pid)
            os.close(fd)
            gate.close()
            shutil.rmtree(d, ignore_errors=True)
            self.log(event="session", seconds=round(time.time() - t0), why=why, requests=gate.requests,
                     tokens_in=gate.tokens_in, tokens_out=gate.tokens_out)
        return why


def bucket(address):
    """What counts as one visitor: an IPv4 address, or an IPv6 /64 (one home or one phone
    has a whole /64 to pick addresses from)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address) + "/64"
    return address


def resize(fd, cols, rows):
    try:
        cols, rows = max(20, min(300, int(cols))), max(5, min(100, int(rows)))
    except (TypeError, ValueError):
        cols, rows = 80, 24
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def stop(pid):
    """Ends the session's processes: the launcher passes the signal on, and the
    container dies with its first process."""
    for sig, wait in ((signal.SIGHUP, 2), (signal.SIGTERM, 3), (signal.SIGKILL, 2)):
        try:
            os.kill(pid, sig)
        except OSError:
            pass
        end = time.time() + wait
        while time.time() < end:
            try:
                if os.waitpid(pid, os.WNOHANG)[0]:
                    return
            except ChildProcessError:
                return
            time.sleep(0.1)


# --- WebSocket (RFC 6455, what a browser needs) ------------------------------------------

class WebSocket:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""
        self.wlock = threading.Lock()
        self.open = True
        self.pinged = time.time()

    def keepalive(self):
        if time.time() - self.pinged > PING_SECONDS:
            self.pinged = time.time()
            try:
                self.send(b"", opcode=9)
            except OSError:
                self.open = False

    def send(self, data, binary=False, opcode=None):
        op = opcode if opcode is not None else (2 if binary else 1)
        n = len(data)
        head = bytes([0x80 | op]) + (bytes([n]) if n < 126 else
                                     bytes([126]) + struct.pack(">H", n) if n < 65536 else
                                     bytes([127]) + struct.pack(">Q", n))
        with self.wlock:
            self.sock.sendall(head + data)

    def send_json(self, obj):
        try:
            self.send(json.dumps(obj).encode())
        except OSError:
            self.open = False

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def receive(self):
        """The next JSON message, or None when the browser is gone. Answers pings."""
        try:
            while True:
                b1, b2 = self._read(2)
                op, n = b1 & 0x0F, b2 & 0x7F
                if not b2 & 0x80 or not b1 & 0x80:
                    raise ValueError("unmasked or fragmented frame")
                if n == 126:
                    n = struct.unpack(">H", self._read(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", self._read(8))[0]
                if n > MAX_FRAME:
                    raise ValueError("frame too large")
                mask = self._read(4)
                data = bytes(c ^ mask[i % 4] for i, c in enumerate(self._read(n)))
                if op == 8:
                    raise OSError("close")
                if op == 9:
                    self.send(data, opcode=10)
                    continue
                if op == 10:
                    return {"t": "pong"}
                if op == 1:
                    msg = json.loads(data)
                    if isinstance(msg, dict):
                        return msg
        except (OSError, ValueError):
            self.open = False
            return None

    def pending(self):
        """True if there is something to read (used while waiting in the queue)."""
        return bool(self.buf) or bool(select.select([self.sock], [], [], 0)[0])

    def close(self):
        try:
            self.send(b"", opcode=8)
        except OSError:
            pass
        self.open = False


class Handler(BaseHTTPRequestHandler):
    service = None
    static = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def address(self):
        return self.service.visitor(self.client_address[0], self.headers.get("X-Forwarded-For", ""))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.endswith("/ws") and self.headers.get("Upgrade", "").lower() == "websocket":
            return self.websocket()
        if path.endswith("/status"):
            s = self.service
            with s.lock:
                data = {"open": not s.off(), "running": s.running, "places": s.cfg["sessions"],
                        "waiting": len(s.waiting), "code": bool(s.cfg["code"])}
            return self.reply(200, json.dumps(data).encode(), "application/json")
        if self.static:
            name = path.rsplit("/try/", 1)[-1].lstrip("/") or "index.html"
            full = os.path.realpath(os.path.join(self.static, name))
            if full.startswith(os.path.realpath(self.static) + os.sep) and os.path.isfile(full):
                kind = {"html": "text/html; charset=utf-8", "js": "text/javascript", "css": "text/css"}.get(
                    full.rsplit(".", 1)[-1], "application/octet-stream")
                with open(full, "rb") as f:
                    return self.reply(200, f.read(), kind)
        self.reply(404, b"not found\n", "text/plain")

    def reply(self, status, body, kind):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def websocket(self):
        s = self.service
        origin, host = self.headers.get("Origin", ""), self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        if origin and origin.split("://", 1)[-1] != host.split(",")[0].strip():
            return self.reply(403, b"wrong origin\n", "text/plain")  # only the sandbox's own page
        address = self.address()
        if not s.connect(address):
            return self.reply(503, b"too many connections\n", "text/plain")
        try:
            self.terminal(address)
        finally:
            s.disconnect(address)

    def terminal(self, address):
        s = self.service
        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        ws = WebSocket(self.connection)
        self.connection.settimeout(30)
        first = ws.receive()   # the 30 s stay: a visitor who stops mid-message is let go
        if not first or first.get("t") != "start":
            return ws.close()
        if s.off():
            ws.send_json({"t": "end", "why": "off"})
            return ws.close()
        if s.cfg["code"] and first.get("code") != s.cfg["code"]:
            time.sleep(1)
            ws.send_json({"t": "end", "why": "code"})
            return ws.close()
        ticket, why = s.admit(address)
        if ticket is None:
            ws.send_json({"t": "end", "why": why})
            return ws.close()
        ran = False
        try:
            def alive():
                # In the queue the browser only sends a close (or sizes, ignored)
                while ws.open and ws.pending():
                    ws.receive()
                ws.keepalive()
                return ws.open
            ran = s.wait_turn(ticket, lambda pos: ws.send_json({"t": "queue", "position": pos}), alive)
            if ran:
                with s.lock:
                    s.starts[address].append(time.time())
                why = s.session(ws, address, first.get("cols"), first.get("rows"))
                ws.send_json({"t": "end", "why": why})
        finally:
            s.leave(address, ran)
            ws.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cfg = dict(DEFAULTS)
    with open(sys.argv[1]) as f:
        given = json.load(f)
    unknown = set(given) - set(DEFAULTS)
    if unknown:
        print(f"server.py: unknown settings: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    cfg.update(given)
    Handler.service = Service(cfg)
    if "--static" in sys.argv:
        Handler.static = sys.argv[sys.argv.index("--static") + 1]
    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            if not isinstance(sys.exc_info()[1], ConnectionError):  # a visitor who left is not an error
                super().handle_error(request, client_address)

    srv = Server((cfg["listen"], cfg["port"]), Handler)
    Handler.service.log(event="start", port=cfg["port"], places=cfg["sessions"])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
