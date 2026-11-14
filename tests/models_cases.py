#!/usr/bin/env python3
"""OSYR — osyr ai local: download, switch and remove local models (no network)

Usage: tests/models_cases.py     prints the cases that fail; exit code 1 if any.
A small HTTP server stands in for the download site: it honours Range, and can
be told to cut a transfer halfway or to serve a wrong file. A two-file catalog
of a few hundred KiB stands in for the 35B.
"""
import hashlib
import http.server
import os
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="osyr-models-")
os.environ.update(OSYR_MODELS=f"{TMP}/image", OSYR_MODELS_EXTRA=f"{TMP}/home/.osyr/models",
                  OSYR_LLAMA_CONF=f"{TMP}/llama.conf", OSYR_PROC=f"{TMP}/proc",
                  XDG_STATE_HOME=f"{TMP}/state", XDG_CONFIG_HOME=f"{TMP}/config")
sys.path.insert(0, os.path.join(ROOT, "board", "osyr", "rootfs-overlay", "usr", "lib", "osyr"))
from osyr import advise, models  # noqa: E402

BIG = os.urandom(300 * 1024)
PROJ = os.urandom(40 * 1024)
FILES = {"/big.gguf": BIG, "/proj.gguf": PROJ}
CUT = {"after": None}     # cut the next transfer after this many bytes
SEEN = []                 # Range headers received


class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = FILES.get(self.path)
        if body is None:
            self.send_error(404)
            return
        rng = self.headers.get("Range")
        SEEN.append(rng)
        start = int(rng.split("=")[1].rstrip("-")) if rng else 0
        self.send_response(206 if rng else 200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        chunk = body[start:]
        if CUT["after"] is not None:
            chunk, CUT["after"] = chunk[:CUT["after"]], None
        self.wfile.write(chunk)


class Args:
    json = True
    confirm = None
    force = False

    def __init__(self, ident, **kw):
        self.id = ident
        self.__dict__.update(kw)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def main():
    bad = []

    def check(name, cond, got=""):
        if not cond:
            bad.append(f"{name}: {got}")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    models.CATALOG = {"large": {"title": "Test 35B", "license": "Apache-2.0", "ram_gb": 30, "files": [
        {"role": "model", "name": "big.gguf", "url": base + "/big.gguf", "size": len(BIG),
         "sha256": hashlib.sha256(BIG).hexdigest()},
        {"role": "mmproj", "name": "big-mmproj.gguf", "url": base + "/proj.gguf", "size": len(PROJ),
         "sha256": hashlib.sha256(PROJ).hexdigest()}]}}
    restarts = []
    models.restart = lambda: (restarts.append(1), (True, ""))[1]
    extra = os.environ["OSYR_MODELS_EXTRA"]
    write(f"{TMP}/image/Qwen3.5-4B-Q4_K_M.gguf", "")
    write(f"{TMP}/image/Qwen3.5-4B-mmproj-F16.gguf", "")
    write(f"{TMP}/llama.conf", "# comment\nMODEL=Qwen3.5-4B-Q4_K_M.gguf\nMMPROJ=Qwen3.5-4B-mmproj-F16.gguf\nCTX=auto\n")
    write(f"{TMP}/proc/meminfo", "MemTotal:       16114528 kB\n")

    # Before anything is downloaded
    st = models.state()
    check("list-small-active", st["active"] == "small" and st["models"][0]["active"], st)
    check("list-large", st["models"][1]["installed"] is False and st["models"][1]["fits"] is False, st["models"][1])
    check("use-not-installed", models.use(Args("large")).code == "MODEL_NOT_INSTALLED")
    check("unknown", models.get(Args("huge")).code == "MODEL_UNKNOWN")

    # Download: asks first; a cut transfer is resumed where it stopped
    r = models.get(Args("large"))
    check("get-confirm", r.code == "CONFIRM_REQUIRED" and r.confirm == "download-large" and
          r.data["fits"] is False, r.as_dict())
    check("get-nothing-yet", not os.path.exists(f"{extra}/big.gguf.part"))
    CUT["after"] = 100 * 1024
    r = models.fetch(models.CATALOG["large"]["files"][0], f"{extra}/big.gguf", attempts=1, pause=0)
    check("cut-fails", r is not None and r.code == "MODEL_DOWNLOAD_FAILED", r and r.as_dict())
    check("cut-keeps-part", os.path.getsize(f"{extra}/big.gguf.part") == 100 * 1024)
    del SEEN[:]
    r = models.get(Args("large", confirm="download-large"))
    check("get-ok", r.ok and r.data.get("installed"), r.as_dict())
    check("resumed", SEEN[0] == f"bytes={100 * 1024}-", SEEN)
    check("files", open(f"{extra}/big.gguf", "rb").read() == BIG and
          open(f"{extra}/big-mmproj.gguf", "rb").read() == PROJ and not os.path.exists(f"{extra}/big.gguf.part"))
    check("get-again", models.get(Args("large")).text == "Already downloaded.")

    # A wrong file is deleted, not installed
    os.remove(f"{extra}/big-mmproj.gguf")
    FILES["/proj.gguf"] = bytes(len(PROJ))
    r = models.get(Args("large", confirm="download-large"))
    check("bad-checksum", r.code == "MODEL_BAD_CHECKSUM" and not os.path.exists(f"{extra}/big-mmproj.gguf")
          and not os.path.exists(f"{extra}/big-mmproj.gguf.part"), r.as_dict())
    FILES["/proj.gguf"] = PROJ

    # No space: nothing is started
    real = os.statvfs
    models.os.statvfs = lambda p: type("S", (), {"f_bavail": 10, "f_frsize": 4096})()
    check("no-space", models.get(Args("large", confirm="download-large")).code == "MODEL_NO_SPACE")
    models.os.statvfs = real
    check("get-rest", models.get(Args("large", confirm="download-large")).ok)

    # Switching: not with 16 GB unless forced; llama.conf keeps its other lines
    check("too-big", models.use(Args("large")).code == "MODEL_TOO_BIG" and not restarts)
    write(f"{TMP}/proc/meminfo", "MemTotal:       32780000 kB\n")
    r = models.use(Args("large"))
    conf = open(f"{TMP}/llama.conf").read()
    check("use-large", r.ok and restarts == [1] and f"MODEL={extra}/big.gguf\n" in conf and
          f"MMPROJ={extra}/big-mmproj.gguf\n" in conf and "CTX=auto" in conf and "# comment" in conf, conf)
    check("active-large", models.state()["active"] == "large")
    check("use-again", models.use(Args("large")).ok and restarts == [1])
    check("remove-in-use", models.remove(Args("large")).code == "MODEL_IN_USE")

    # The menu follows: the large one in use takes the measured speed
    m = {"cpu": "t", "threads": 16, "cores": 8, "avx2": True, "ram_gb": 31.3, "gpus": []}
    a = advise.advise(m, {"read_tps": 60, "write_tps": 12, "model": "big", "when": 0},
                      {"active": "local", "providers": {}}, models.state())
    opts = {o["id"]: o for o in a["options"]}
    check("advise-large-used", a["recommended"] == "large" and "about 14 s" in opts["large"]["note"] and
          opts["local"]["note"] == "not in use", a)

    r = models.use(Args("small"))
    conf = open(f"{TMP}/llama.conf").read()
    check("use-small", r.ok and restarts == [1, 1] and "MODEL=Qwen3.5-4B-Q4_K_M.gguf\n" in conf and
          "MMPROJ=Qwen3.5-4B-mmproj-F16.gguf\n" in conf, conf)
    a = advise.advise(m, None, {"active": "local", "providers": {}}, models.state())
    check("advise-large-downloaded", {o["id"]: o for o in a["options"]}["large"]["how"] ==
          "sudo osyr ai local use large", a["options"])
    r = models.remove(Args("large"))
    check("remove", r.ok and os.listdir(extra) == [], (r.as_dict(), os.listdir(extra)))
    a = advise.advise(m, None, {"active": "local", "providers": {}}, models.state())
    big = {o["id"]: o for o in a["options"]}["large"]
    check("advise-large-offered", big["available"] and big["how"] == "sudo osyr ai local get large", big)

    # --- Graphics card: what llama-server lists, and "on" kept only if it is faster
    fake = f"{TMP}/llama-server"
    write(fake, "#!/bin/sh\necho 'Available devices:'\n"
                "echo '  Vulkan0: AMD Radeon RX 7900 XT (RADV NAVI31) (20464 MiB, 20000 MiB free)'\n"
                "echo '  Vulkan1: Intel(R) UHD Graphics (ICL GT1) (7802 MiB, 7000 MiB free)'\n")
    os.chmod(fake, 0o755)
    models.LLAMA_SERVER = fake
    devs = models.gpu_devices()
    check("gpu-devices", [d["id"] for d in devs] == ["Vulkan0", "Vulkan1"] and devs[0]["vram_gb"] == 20.0 and
          devs[0]["name"] == "AMD Radeon RX 7900 XT (RADV NAVI31)", devs)
    models.LLAMA_SERVER = f"{TMP}/missing"
    check("gpu-none", models.gpu_devices() == [] and models.gpu_set(Args("", state="on", keep=False)).code == "GPU_NONE")
    models.LLAMA_SERVER = fake

    cpu = {"read_tps": 26.0, "write_tps": 5.3, "model": "m", "when": 0}
    ready, speeds, saved = [True], [], []
    models.wait_ready = lambda timeout=900: ready[0]
    advise.saved_speed = lambda: cpu
    advise.measure = lambda timeout=300: speeds.pop(0)
    advise.save_speed = saved.append

    def gpu_line():
        return [x for x in open(f"{TMP}/llama.conf").read().splitlines() if x.startswith("GPU=")]

    del restarts[:]
    speeds.append(({"read_tps": 400.0, "write_tps": 40.0, "model": "m", "when": 0}, None))
    r = models.gpu_set(Args("", state="on", keep=False))
    check("gpu-faster-kept", r.ok and gpu_line() == ["GPU=on"] and restarts == [1] and "about 3 s" in r.text and
          "about 32 s" in r.text, (r.as_dict(), r.text))
    check("gpu-status-on", models.gpu_status(Args("")).data["gpu"] is True)
    check("gpu-already", models.gpu_set(Args("", state="on", keep=False)).ok and restarts == [1])
    check("gpu-off", models.gpu_set(Args("", state="off", keep=False)).ok and gpu_line() == ["GPU=off"] and
          restarts == [1, 1])

    del restarts[:]
    speeds.append(({"read_tps": 20.0, "write_tps": 4.0, "model": "m", "when": 0}, None))
    r = models.gpu_set(Args("", state="on", keep=False))
    check("gpu-slower-reverted", r.code == "GPU_SLOWER" and gpu_line() == ["GPU=off"] and restarts == [1, 1] and
          saved[-1] == cpu and "stays on the processor" in r.text, (r.as_dict(), r.text))
    speeds.append(({"read_tps": 20.0, "write_tps": 4.0, "model": "m", "when": 0}, None))
    r = models.gpu_set(Args("", state="on", keep=True))
    check("gpu-slower-kept", r.ok and gpu_line() == ["GPU=on"], r.as_dict())
    models.gpu_set(Args("", state="off", keep=False))

    ready[0] = False  # the server never comes back with the card
    r = models.gpu_set(Args("", state="on", keep=False))
    check("gpu-no-start-reverted", r.code == "GPU_FAILED" and gpu_line() == ["GPU=off"], r.as_dict())
    ready[0] = True
    speeds.append((None, "timed out"))  # it starts, but does not answer
    r = models.gpu_set(Args("", state="on", keep=False))
    check("gpu-no-answer-reverted", r.code == "GPU_FAILED" and gpu_line() == ["GPU=off"], r.as_dict())
    conf = open(f"{TMP}/llama.conf").read()
    check("conf-intact", "CTX=auto" in conf and "# comment" in conf and conf.count("MODEL=") == 1, conf)

    server.shutdown()
    server.server_close()
    for b in bad:
        print("FAIL", b)
    print(f"models cases: {'%d failed' % len(bad) if bad else 'all passed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
