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


def main():
    with tempfile.TemporaryDirectory(prefix="osyr-sandbox-") as tmp:
        gate(tmp)
        if os.path.exists(f"{ROOT}/build/output/target/usr/bin/python3"):
            doubles(tmp)
            isolation(tmp)
        else:
            print("sandbox cases: no built image root, session cases skipped")
    for b in bad:
        print("FAIL", b)
    print(f"sandbox cases: {'%d failed' % len(bad) if bad else 'all passed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
