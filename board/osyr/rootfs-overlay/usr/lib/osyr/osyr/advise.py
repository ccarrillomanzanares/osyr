"""osyr ai advise — where the assistant's model should run on this computer.

It looks at the machine (memory, AVX2, cores, graphics cards), measures how
fast the local model reads and writes, and turns that into the wait for a
simple request. With it, it lists the choices (the model on this computer, a
larger one, a model on another machine) and marks the one it recommends. The
person chooses; nothing is changed here.

The wait is for "a simple request": two answers from the model, the call to a
command and the reply that uses its result, about 350 tokens read and 100
written. Measured against the candidates of 13-14 Nov 2026 it lands within a
few seconds of the median task.

OSYR_PROC and OSYR_SYSFS point to a fake /proc and /sys for the tests.
"""
import glob
import json
import os
import time
import urllib.error
import urllib.request

from . import ai
from .result import Result, fail

PROC = os.environ.get("OSYR_PROC", "/proc")
SYSFS = os.environ.get("OSYR_SYSFS", "/sys")
MODELS = os.environ.get("OSYR_MODELS", "/usr/share/osyr/models")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
                     "osyr", "ai-speed.json")

# A simple request, in tokens (see the module text)
REQUEST_READ, REQUEST_WRITE = 350, 100
# Seconds for that request: up to FAST it is comfortable; beyond SLOW the local
# model is not recommended
FAST, SLOW = 15, 30
# Memory in GB from which the large model (35B, about 20 GB) fits on this computer
LARGE_RAM_GB = 30
LARGE_VRAM_GB = 22
GPU_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def machine():
    ram_kb, flags, model, cores = 0, set(), "", set()
    for line in _read(os.path.join(PROC, "meminfo")).splitlines():
        if line.startswith("MemTotal:"):
            ram_kb = int(line.split()[1])
    threads, phys, core = 0, "0", None
    for line in _read(os.path.join(PROC, "cpuinfo")).splitlines():
        key, _, value = (x.strip() for x in line.partition(":"))
        if key == "processor":
            threads += 1
        elif key == "model name" and not model:
            model = value
        elif key == "flags" and not flags:
            flags = set(value.split())
        elif key == "physical id":
            phys = value
        elif key == "core id":
            core = value
            cores.add((phys, core))
    return {"cpu": model or "?", "threads": threads, "cores": len(cores) or threads,
            "avx2": "avx2" in flags, "ram_gb": round(ram_kb / 1048576, 1), "gpus": gpus()}


def gpus():
    """Graphics cards, with their memory when the driver tells it (amdgpu does;
    an integrated one shares the computer's memory and reports little or none)."""
    found = []
    for card in sorted(glob.glob(os.path.join(SYSFS, "class", "drm", "card[0-9]*"))):
        if "-" in os.path.basename(card):
            continue  # card0-eDP-1 and other connectors
        dev = os.path.join(card, "device")
        vendor = _read(os.path.join(dev, "vendor"))
        if not vendor:
            continue
        vram = _read(os.path.join(dev, "mem_info_vram_total"))
        driver = os.path.basename(os.path.realpath(os.path.join(dev, "driver"))) \
            if os.path.exists(os.path.join(dev, "driver")) else ""
        found.append({"vendor": GPU_VENDORS.get(vendor, vendor), "driver": driver,
                      "vram_gb": round(int(vram) / 2**30, 1) if vram.isdigit() else None})
    return found


def local_models():
    return sorted(os.path.basename(p) for p in glob.glob(os.path.join(MODELS, "*.gguf"))
                  if "mmproj" not in os.path.basename(p))


def bench_prompt():
    # About 200 tokens: a short one does not show the reading speed
    line = "An operating system manages memory, processes, files and devices for the programs that run on a computer. "
    return line * 8 + "Explain in two sentences what an operating system does."


def measure(timeout=300):
    """Reading and writing speed of the local model, in tokens per second.
    Returns (speed, error); it is saved for the next advise."""
    p = ai.provider("local")
    root = ai.server_root(p["url"])
    body = json.dumps({"prompt": bench_prompt(), "n_predict": 32, "cache_prompt": False,
                       "id_slot": 1}).encode()
    req = urllib.request.Request(root + "/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = json.load(r).get("timings", {})
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, str(getattr(e, "reason", e))
    read, write = t.get("prompt_per_second"), t.get("predicted_per_second")
    if not read or not write:
        return None, "no timings in the answer"
    speed = {"read_tps": round(read, 1), "write_tps": round(write, 1),
             "model": ai.local_model_name(p) or "", "when": int(time.time())}
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w") as f:
            json.dump(speed, f)
    except OSError:
        pass
    return speed, None


def saved_speed():
    try:
        with open(STATE) as f:
            s = json.load(f)
        return s if s.get("read_tps") and s.get("write_tps") else None
    except (OSError, ValueError):
        return None


def request_seconds(speed):
    return round(REQUEST_READ / speed["read_tps"] + REQUEST_WRITE / speed["write_tps"])


def advise(m, speed, cfg):
    """The choices and the recommended one. m = machine(), speed = measured or
    None, cfg = ai.load(). Pure: the tests call it with made-up machines."""
    wait = request_seconds(speed) if speed else None
    remote = sorted(cfg["providers"])
    active = cfg["active"]
    big_gpu = max((g["vram_gb"] or 0 for g in m["gpus"]), default=0)

    if wait is None:
        local_note = "speed not measured yet"
    elif wait <= FAST:
        local_note = f"about {wait} s for a simple request"
    elif wait <= SLOW:
        local_note = f"about {wait} s for a simple request: slow, but usable"
    else:
        local_note = f"about {wait} s for a simple request: too slow to use comfortably"
    if not m["avx2"]:
        local_note += "; this processor has no AVX2, which the model needs to run fast"

    fits_large = m["ram_gb"] >= LARGE_RAM_GB or big_gpu >= LARGE_VRAM_GB
    options = [
        {"id": "local", "title": "The model on this computer",
         "detail": "Nothing leaves the computer. Everyday requests and routine administration.",
         "available": True, "note": local_note, "how": "osyr ai use local"},
        {"id": "large", "title": "A large model on this computer",
         "detail": "Nothing leaves the computer. Also diagnoses problems of many steps.",
         "available": False,
         "note": ("this computer has the memory for it, but OSYR cannot download it yet" if fits_large
                  else f"needs {LARGE_RAM_GB + 2} GB of memory; this computer has {m['ram_gb']:g} GB"),
         "how": None},
        {"id": "server", "title": "A model on another machine",
         "detail": "Your own server, or a provider with an API key. What you write is sent there.",
         "available": True,
         "note": ("configured: " + ", ".join(remote)) if remote else "none configured yet",
         "how": (f"osyr ai use {remote[0]}" if remote else "osyr ai pair")},
    ]

    if active != "local" and active in cfg["providers"]:
        rec, why = "server", f'"{active}" is already in use.'
    elif not m["avx2"]:
        rec, why = "server", "The processor has no AVX2: the local model is very slow here."
    elif wait is not None and wait > SLOW:
        rec, why = "server", f"The local model takes about {wait} s for a simple request on this computer."
    elif wait is None:
        rec, why = "local", "Not measured yet; run: osyr ai advise --measure"
    else:
        rec, why = "local", f"The local model answers a simple request in about {wait} s."

    notes = []
    for g in m["gpus"]:
        if g["vendor"] == "NVIDIA" or (g["vram_gb"] or 0) >= 4:
            mem = f' with {g["vram_gb"]:g} GB' if g["vram_gb"] else ""
            notes.append(f'{g["vendor"]} graphics card{mem} found: OSYR runs the model on the '
                         "processor and does not use it yet.")
    return {"machine": m, "speed": speed, "request_seconds": wait, "active": active,
            "options": options, "recommended": rec, "why": why, "notes": notes}


def text_of(a):
    m = a["machine"]
    lines = [f'This computer: {m["cpu"]}, {m["cores"]} cores, {m["ram_gb"]:g} GB of memory'
             f'{"" if m["avx2"] else ", no AVX2"}.']
    if a["speed"]:
        s = a["speed"]
        lines.append(f'Local model ({s["model"] or "?"}): {s["read_tps"]:g} tokens/s reading, '
                     f'{s["write_tps"]:g} writing.')
    lines.append("")
    for i, o in enumerate(a["options"], 1):
        mark = "  <- recommended" if o["id"] == a["recommended"] else ""
        lines.append(f'{i}  {o["title"]}{"" if o["available"] else " (not available)"}{mark}')
        lines.append(f'   {o["detail"]}')
        lines.append(f'   {o["note"][0].upper() + o["note"][1:]}.')
    lines.append("")
    lines.append(a["why"])
    lines.extend(a["notes"])
    return "\n".join(lines)


def run(args):
    speed, err = saved_speed(), None
    if args.measure:
        speed, err = measure()
        if speed is None and err == "HTTP 503":
            return fail("AI_LOADING", "Wait a few seconds; the model is loading.",
                        text="The local model is still loading.")
    a = advise(machine(), speed, ai.load())
    if args.measure and speed is None:
        a["notes"].append(f"The local model could not be measured ({err}).")
    rec = next(o for o in a["options"] if o["id"] == a["recommended"])
    current = "local" if a["active"] == "local" else "server"
    hint = f'Run: {rec["how"]}' if a["recommended"] != current else None
    return Result(True, "OK", a, hint, text=text_of(a))
