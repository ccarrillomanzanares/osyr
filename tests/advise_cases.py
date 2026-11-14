#!/usr/bin/env python3
"""OSYR — osyr ai advise: the choices and the recommended one (no model, no network)

Usage: tests/advise_cases.py     prints the cases that fail; exit code 1 if any.
Made-up machines stand in for the real ones, with the speeds measured on them
(13-14 Nov 2026): the HP 15s, the 2014 laptop without AVX2 and the VPS. A fake
/proc and /sys check what is read from the machine, and a small HTTP server
stands in for llama-server in the speed measurement.
"""
import http.server
import json
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="osyr-advise-")
SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), None)
os.environ.update(OSYR_PROC=os.path.join(TMP, "proc"), OSYR_SYSFS=os.path.join(TMP, "sys"),
                  OSYR_MODELS=os.path.join(TMP, "models"), XDG_STATE_HOME=os.path.join(TMP, "state"),
                  XDG_CONFIG_HOME=os.path.join(TMP, "config"), OSYR_LLAMA_PORT=str(SERVER.server_address[1]))
sys.path.insert(0, os.path.join(ROOT, "board", "osyr", "rootfs-overlay", "usr", "lib", "osyr"))
from osyr import advise  # noqa: E402

TIMINGS = {"prompt_per_second": 25.94, "predicted_per_second": 5.35}


class Llama(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply({"data": [{"id": "Qwen3.5-4B-Q4_K_M.gguf"}]})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.reply({"content": "x", "timings": TIMINGS})


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def fake_machine():
    cpu = ""
    for n in range(8):  # 4 cores, 8 threads
        cpu += (f"processor\t: {n}\nmodel name\t: Intel(R) Core(TM) i5-1035G1\nphysical id\t: 0\n"
                f"core id\t\t: {n % 4}\nflags\t\t: fpu sse2 avx avx2 fma\n\n")
    write(os.path.join(TMP, "proc", "cpuinfo"), cpu)
    write(os.path.join(TMP, "proc", "meminfo"), "MemTotal:       16114528 kB\nMemFree: 1 kB\n")
    card = os.path.join(TMP, "sys", "class", "drm", "card0", "device")
    write(os.path.join(card, "vendor"), "0x1002\n")
    write(os.path.join(card, "mem_info_vram_total"), str(8 * 2**30) + "\n")
    os.makedirs(os.path.join(TMP, "sys", "class", "drm", "card0-eDP-1"))
    write(os.path.join(TMP, "models", "Qwen3.5-4B-Q4_K_M.gguf"), "")
    write(os.path.join(TMP, "models", "Qwen3.5-4B-mmproj-F16.gguf"), "")


def m(ram=16, avx2=True, gpus=()):
    return {"cpu": "test", "threads": 8, "cores": 4, "avx2": avx2, "ram_gb": ram, "gpus": list(gpus)}


def sp(read, write_):
    return {"read_tps": read, "write_tps": write_, "model": "Qwen3.5-4B-Q4_K_M", "when": 0}


LOCAL = {"active": "local", "providers": {}}
REMOTE = {"active": "home", "providers": {"home": {"url": "https://x/v1"}}}
ADDED = {"active": "local", "providers": {"home": {"url": "https://x/v1"}}}


def main():
    bad = []

    def check(name, cond, got=""):
        if not cond:
            bad.append(f"{name}: {got}")

    def opt(a, ident):
        return next(o for o in a["options"] if o["id"] == ident)

    # The three real machines, with what was measured on them
    a = advise.advise(m(), sp(25.9, 5.35), LOCAL)  # HP 15s, cold
    check("hp15s-wait", a["request_seconds"] == 32, a["request_seconds"])
    check("hp15s-server", a["recommended"] == "server", a["recommended"])
    check("hp15s-pair", opt(a, "server")["how"] == "osyr ai pair", opt(a, "server"))
    a = advise.advise(m(avx2=False, ram=7.2), sp(3.0, 1.2), LOCAL)  # the 2014 laptop
    check("2014-server", a["recommended"] == "server" and "AVX2" in a["why"], a["why"])
    check("2014-wait", a["request_seconds"] == 200, a["request_seconds"])
    a = advise.advise(m(ram=62.8), sp(27.9, 7.5), LOCAL)  # the VPS
    check("vps-local", a["recommended"] == "local" and a["request_seconds"] == 26, a)
    check("vps-slow-note", "slow, but usable" in opt(a, "local")["note"], opt(a, "local")["note"])
    check("vps-large-fits", "has the memory" in opt(a, "large")["note"], opt(a, "large")["note"])

    # Fast, unmeasured, and a provider already there
    a = advise.advise(m(ram=32), sp(120, 30), LOCAL)
    check("fast-local", a["recommended"] == "local" and a["request_seconds"] == 6, a["request_seconds"])
    a = advise.advise(m(), None, LOCAL)
    check("unmeasured-local", a["recommended"] == "local" and "--measure" in a["why"], a["why"])
    check("unmeasured-no-avx2", advise.advise(m(avx2=False), None, LOCAL)["recommended"] == "server")
    a = advise.advise(m(), sp(120, 30), REMOTE)
    check("remote-kept", a["recommended"] == "server" and "home" in a["why"], a["why"])
    a = advise.advise(m(), sp(25.9, 5.35), ADDED)
    check("added-used", opt(a, "server")["how"] == "osyr ai use home", opt(a, "server"))

    # The large model: by memory or by the graphics card; never offered as available yet
    a = advise.advise(m(ram=16), None, LOCAL)
    check("large-needs-memory", "needs 32 GB" in opt(a, "large")["note"], opt(a, "large")["note"])
    check("large-unavailable", opt(a, "large")["available"] is False)
    a = advise.advise(m(ram=16, gpus=[{"vendor": "NVIDIA", "driver": "", "vram_gb": 24.0}]), None, LOCAL)
    check("large-by-gpu", "has the memory" in opt(a, "large")["note"], opt(a, "large")["note"])
    check("nvidia-note", any("no driver" in n for n in a["notes"]), a["notes"])
    card = [{"id": "Vulkan0", "name": "AMD Radeon RX 7900 XT (RADV NAVI31)", "vram_gb": 20.0, "free_gb": 19.5}]
    a = advise.advise(m(), None, LOCAL, None, card)
    check("card-offered", len(a["notes"]) == 1 and "/gpu on" in a["notes"][0] and "20 GB" in a["notes"][0], a["notes"])
    a = advise.advise(m(), None, LOCAL, None, card, True)
    check("card-in-use", "/gpu off" in a["notes"][0], a["notes"])
    a = advise.advise(m(gpus=[{"vendor": "Intel", "driver": "i915", "vram_gb": None}]), None, LOCAL)
    check("integrated-no-note", a["notes"] == [], a["notes"])

    # What is read from the machine
    fake_machine()
    got = advise.machine()
    check("machine-cpu", got["cores"] == 4 and got["threads"] == 8 and got["avx2"], got)
    check("machine-ram", got["ram_gb"] == 15.4, got["ram_gb"])
    check("machine-gpu", got["gpus"] == [{"vendor": "AMD", "driver": "", "vram_gb": 8.0}], got["gpus"])
    check("models", advise.local_models() == ["Qwen3.5-4B-Q4_K_M.gguf"], advise.local_models())

    # The measurement, against a stand-in for llama-server, and the whole command
    SERVER.RequestHandlerClass = Llama
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    check("no-speed-yet", advise.saved_speed() is None)
    speed, err = advise.measure(timeout=10)
    check("measure", speed and speed["read_tps"] == 25.9 and speed["write_tps"] == 5.3, (speed, err))
    check("measure-model", speed and speed["model"] == "Qwen3.5-4B-Q4_K_M", speed)
    check("speed-saved", advise.saved_speed() == speed, advise.saved_speed())

    class Args:
        measure = False
    r = advise.run(Args)
    check("run-server", r.ok and r.data["recommended"] == "server" and r.hint == "Run: osyr ai pair", r.hint)
    check("run-text", "<- recommended" in r.text and "15.4 GB" in r.text, r.text)
    SERVER.shutdown()
    SERVER.server_close()
    Args.measure = True
    r = advise.run(Args)  # the server is gone: it still advises, and says so
    check("run-unreachable", r.ok and any("could not be measured" in n for n in r.data["notes"]), r.data["notes"])

    for b in bad:
        print("FAIL", b)
    print(f"advise cases: {'%d failed' % len(bad) if bad else 'all passed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
