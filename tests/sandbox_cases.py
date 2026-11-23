#!/usr/bin/env python3
"""Cases for the sandbox (sandbox/) and the hardware doubles (tests/doubles):
the real osyr commands inside a disposable session, its isolation, and the
model gate against the fake provider. Needs the built image root
(build/output/target) and bwrap; the memory, CPU and process limits are not
tried here (they are systemd's)."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sandbox"))
import modelgate  # noqa: E402

bad = []


def check(name, ok, detail=""):
    if not ok:
        bad.append(f"{name}: {detail}")


def session(tmp, script, timeout=180):
    """Runs a shell script inside a new session; returns its output."""
    d = tempfile.mkdtemp(dir=tmp)
    p = subprocess.run([f"{ROOT}/sandbox/session", d, "sh", "-c", script], capture_output=True, text=True,
                       timeout=timeout, env=dict(os.environ, OSYR_SANDBOX_LIMITS="0"))
    return p.stdout + p.stderr


def osyr(out, marker):
    """The JSON result printed after '== marker' by the script."""
    try:
        return json.loads(out.split(f"== {marker}\n", 1)[1].split("\n", 1)[0])
    except (IndexError, ValueError):
        return {}


def doubles(tmp):
    steps = [("status0", "net status"), ("scan", "net wifi scan"), ("status1", "net status"),
             ("vol", "audio volume 70"), ("mute", "audio mute on"), ("audio", "audio status"),
             ("bright", "ui brightness 80"), ("bright2", "ui brightness"),
             ("mode", "ui display mode HDMI-A-1 1280x720"), ("off", "ui display off eDP-1"),
             ("last", "ui display off HDMI-A-1"), ("btscan", "bt scan --seconds 1"),
             ("btconn", "bt connect sony"), ("btpin", "bt connect keychron"), ("bt", "bt status"),
             ("thermal", "sys thermal")]
    script = "\n".join(f'echo "== {m}"; osyr --json {c}' for m, c in steps)
    script = script.replace('echo "== status1"', 'echo "== wrong"; echo wrongpass1 | osyr --json net wifi connect '
                            'casa-lopez --password-stdin\necho "== right"; echo girasol2026 | osyr --json net wifi '
                            'connect casa-lopez --password-stdin\necho "== open"; osyr --json net wifi connect '
                            '"Cafe Central (guests)"\necho "== status1"')
    out = session(tmp, script)
    r = lambda m: osyr(out, m)  # noqa: E731
    check("net-no-link", r("status0").get("code") == "NET_NO_LINK", out[-600:])
    nets = r("scan").get("data", {}).get("networks", [])
    check("wifi-scan", [n["ssid"] for n in nets][:2] == ["Casa-Lopez", "MOVISTAR_F406"], nets)
    check("wifi-wrong-password", r("wrong").get("code") == "WIFI_AUTH_FAILED", r("wrong"))
    check("wifi-connect", r("right").get("ok") and r("right")["data"]["ipv4"] == "192.168.1.37", r("right"))
    check("wifi-open", r("open").get("ok") and r("open")["data"]["ssid"] == "Cafe Central (guests)", r("open"))
    check("net-status", r("status1").get("ok") and r("status1")["data"]["internet"] is True, r("status1"))
    check("volume", r("vol").get("data") == {"volume": 70}, r("vol"))
    a = r("audio").get("data", {}).get("output", {})
    check("audio-status", a.get("volume") == 70 and a.get("muted") is True, a)
    check("brightness", r("bright2").get("data", {}).get("percent") == 80, r("bright2"))
    d = {o["name"]: o for o in r("off").get("data", {}).get("displays", [])}
    check("display-mode-off", d.get("HDMI-A-1", {}).get("mode") == "1280x720@60Hz"
          and d.get("eDP-1", {}).get("enabled") is False, d)
    check("display-last", r("last").get("code") == "UI_LAST_DISPLAY", r("last"))
    check("bt-scan", len(r("btscan").get("data", {}).get("devices", [])) == 4, r("btscan"))
    check("bt-connect", r("btconn").get("ok") and r("btconn")["data"]["name"] == "Sony WH-1000XM4", r("btconn"))
    check("bt-pair-failed", r("btpin").get("code") == "BT_PAIR_FAILED", r("btpin"))
    check("bt-status", [x["name"] for x in r("bt").get("data", {}).get("devices", []) if x["connected"]]
          == ["Sony WH-1000XM4"], r("bt"))
    check("thermal", r("thermal").get("data", {}).get("temp_c") == 47, r("thermal"))


def isolation(tmp):
    out = session(tmp, """
        id -un; ls /home; cat /proc/1/comm
        python3 -c 'import socket; socket.create_connection(("1.1.1.1", 443), 3)' 2>&1 | tail -1
        touch /usr/bin/x 2>&1; cat /etc/osyr/diag.token 2>/dev/null | wc -c
        sudo -n true 2>&1; ls ~/Documents | wc -l
        dd if=/dev/zero of=$HOME/big bs=1M count=300 2>&1 | tail -1
    """)
    lines = out.strip().splitlines()
    check("user", lines[:3] == ["osyr", "osyr", "bwrap"], lines[:3])
    check("no-network", "Network is unreachable" in out, out)
    check("read-only-system", "Read-only file system" in out, out)
    check("no-upload-token", "\n0\n" in out, out)
    check("no-sudo", "no new privileges" in out, out)
    check("sample-files", "\n4\n" in out, out)
    check("home-capped", "256 MiB" in out, out)
    # Each session starts from scratch
    again = session(tmp, "ls $HOME/big 2>&1; osyr --json ui brightness")
    check("starts-clean", "No such file" in again and '"percent": 50' in again, again)


def http(path, method="GET", body=None, sock=None):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(20)
    s.connect(sock)
    data = json.dumps(body).encode() if body is not None else b""
    s.sendall(f"{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(data)}\r\n\r\n".encode() + data)
    out = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        out += chunk
    s.close()
    head, _, rest = out.partition(b"\r\n\r\n")
    return int(head.split()[1]), rest.decode(errors="replace")


def gate(tmp):
    req, why = modelgate.clean_request(
        {"messages": [{"role": "user", "content": "hi", "extra": 1}], "max_tokens": 99999, "id_slot": 3,
         "model": "other", "n_probs": 5, "stream": True}, "m")
    check("clean-fields", req == {"messages": [{"role": "user", "content": "hi"}], "stream": True,
                                  "max_tokens": modelgate.MAX_REPLY_TOKENS,
                                  "stream_options": {"include_usage": True}, "model": "m"}, req)
    check("clean-image", modelgate.clean_request({"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "http://internal/"}}]}]}, "")[0] is None)
    check("clean-role", modelgate.clean_request({"messages": [{"role": "root", "content": "x"}]}, "")[0] is None)

    port = 18000 + os.getpid() % 1000
    mock = subprocess.Popen([sys.executable, f"{ROOT}/tests/mock_openai.py", str(port)])
    sock = os.path.join(tmp, "gate.sock")
    up = modelgate.Upstream(f"http://127.0.0.1:{port}/v1", "secret123", "X-API-Key", "mock-35b")
    g = modelgate.Gate(sock, up, max_requests=2).start()
    try:
        time.sleep(1)
        msg = {"messages": [{"role": "user", "content": "hello"}], "stream": True, "id_slot": 0,
               "chat_template_kwargs": {"enable_thinking": False}}
        st, body = http("/v1/chat/completions", "POST", msg, sock)
        # The fake provider wants the key, rejects Python's user agent and unknown fields
        check("gate-forwards", st == 200 and "Remote answer (mock-35b)" in body.replace('"}}]}\n\ndata: {"choices": '
              '[{"delta": {"content": "', ""), (st, body[:300]))
        check("gate-counts", g.requests == 1 and g.tokens_out == 6, (g.requests, g.tokens_out))
        check("gate-models", http("/v1/models", sock=sock)[0] == 200)
        check("gate-other-path", http("/props", sock=sock)[0] == 404 and http("/v1/embeddings", "POST", {}, sock)[0] == 404)
        check("gate-bad-body", http("/v1/chat/completions", "POST", {"messages": "x"}, sock)[0] == 400)
        http("/v1/chat/completions", "POST", msg, sock)
        check("gate-session-cap", http("/v1/chat/completions", "POST", msg, sock)[0] == 429)
        up.key = "wrong"
        g.left = 5
        st, body = http("/v1/chat/completions", "POST", msg, sock)
        check("gate-hides-upstream", st == 502 and "bad key" not in body, (st, body))
    finally:
        g.close()
        mock.kill()


class Browser:
    """What the page does: a WebSocket to the service."""

    def __init__(self, port, headers=""):
        import base64
        self.s = socket.create_connection(("127.0.0.1", port))
        self.buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /osyr/try/ws HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                        f"{headers}\r\n").encode())
        self.s.settimeout(30)
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.s.recv(4096)
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        self.status = int(head.split()[1])

    def send(self, obj):
        d, m = json.dumps(obj).encode(), os.urandom(4)
        self.s.sendall(bytes([0x81, 0x80 | len(d)]) + m + bytes(c ^ m[i % 4] for i, c in enumerate(d)))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(65536)
            if not chunk:
                raise OSError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def until(self, kind, seconds=60):
        """Reads until a JSON message of that kind; returns (message, screen output so far)."""
        import struct
        out, end = b"", time.time() + seconds
        try:
            while time.time() < end:
                b1, b2 = self._read(2)
                n = b2 & 0x7F
                if n == 126:
                    n = struct.unpack(">H", self._read(2))[0]
                data = self._read(n)
                if b1 & 0x0F == 2:
                    out += data
                elif b1 & 0x0F == 1 and json.loads(data).get("t") == kind:
                    return json.loads(data), out
                elif b1 & 0x0F == 8:
                    break
        except OSError:
            pass
        return None, out


def service(tmp):
    port, mport = 19000 + os.getpid() % 1000, 20000 + os.getpid() % 1000
    work = tempfile.mkdtemp(prefix="osb-", dir="/tmp")  # short: a socket's path has a length limit
    conf = os.path.join(tmp, "sandbox.json")
    keyf = os.path.join(tmp, "key")
    with open(keyf, "w") as f:
        f.write("secret123\n")
    with open(conf, "w") as f:
        json.dump({"port": port, "upstream": f"http://127.0.0.1:{mport}/v1", "key_file": keyf,
                   "key_header": "X-API-Key", "model": "mock-35b", "context": 1500, "sessions": 1, "code": "letmein",
                   "off_file": os.path.join(tmp, "off"), "log": os.path.join(tmp, "log"), "work": work}, f)
    mock = subprocess.Popen([sys.executable, f"{ROOT}/tests/mock_openai.py", str(mport)])
    srv = subprocess.Popen([sys.executable, f"{ROOT}/sandbox/server.py", conf],
                           env=dict(os.environ, OSYR_SANDBOX_LIMITS="0"))
    try:
        time.sleep(1.5)
        w = Browser(port)
        w.send({"t": "start", "code": "nope"})
        check("service-wrong-code", (w.until("end", 10)[0] or {}).get("why") == "code")
        check("service-foreign-origin", Browser(port, "Origin: https://elsewhere.example\r\n").status == 403)
        a = Browser(port)
        a.send({"t": "start", "code": "letmein", "cols": 100, "rows": 30})
        check("service-started", a.until("started", 20)[0] is not None)
        b = Browser(port, "X-Forwarded-For: 203.0.113.9\r\n")
        b.send({"t": "start", "code": "letmein"})
        check("service-queue", (b.until("queue", 10)[0] or {}).get("position") == 1)
        c = Browser(port)
        c.send({"t": "start", "code": "letmein"})
        check("service-one-per-address", (c.until("end", 10)[0] or {}).get("why") == "limit")
        time.sleep(6)  # the chat's greeting
        a.send({"t": "in", "d": "hello there\r"})
        time.sleep(6)
        a.send({"t": "in", "d": "/exit\r"})
        end, out = a.until("end", 60)
        # The chat inside reached the fake provider through the relay and the gate, which put the key
        # in (two requests: the provider refuses tools, and the chat goes on without them)
        check("service-chat", b"Remote answer (mock-35b)" in out and b"END OF LINE" in out, out[-500:])
        check("service-exit", (end or {}).get("why") == "exit", end)
        check("service-next-in-queue", b.until("started", 30)[0] is not None)
        open(os.path.join(tmp, "off"), "w").close()
        check("service-switch-ends", (b.until("end", 20)[0] or {}).get("why") == "off")
        d = Browser(port, "X-Forwarded-For: 203.0.113.10\r\n")
        d.send({"t": "start", "code": "letmein"})
        check("service-switch-refuses", (d.until("end", 10)[0] or {}).get("why") == "off")
        time.sleep(3)
        check("service-cleans-up", os.listdir(work) == [], os.listdir(work))
        log = open(os.path.join(tmp, "log")).read()
        check("service-log", '"why": "exit", "requests": 2' in log and "hello there" not in log, log)
    finally:
        srv.kill()
        mock.kill()
        import shutil
        shutil.rmtree(work, ignore_errors=True)


def main():
    with tempfile.TemporaryDirectory(prefix="osyr-sandbox-") as tmp:
        gate(tmp)
        if os.path.exists(f"{ROOT}/build/output/target/usr/bin/python3"):
            doubles(tmp)
            isolation(tmp)
            service(tmp)
        else:
            print("sandbox cases: no built image root, session cases skipped")
    for b in bad:
        print("FAIL", b)
    print(f"sandbox cases: {'%d failed' % len(bad) if bad else 'all passed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
