"""osyr ai local — the models on this computer: list, download, switch, remove.
osyr ai gpu — run the local model on a graphics card, if that is faster.

The image ships one small model in /usr/share/osyr/models. A larger one does
not fit in the system partition (8 to 16 GiB), so downloaded models live in
/home/.osyr/models, which also survives image updates. osyr-llama reads the
model named in /etc/osyr/llama.conf: a file name for the image's folder, or a
full path for a downloaded one.

Downloads are resumed if they are cut, and every file is checked against the
sha256 written here before it is used.

For the tests: OSYR_MODELS, OSYR_MODELS_EXTRA, OSYR_LLAMA_CONF and OSYR_PROC
point to stand-ins (and with OSYR_MODELS_EXTRA set, root is not asked for).
"""
import hashlib
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

from .result import Result, fail

MODELS = os.environ.get("OSYR_MODELS", "/usr/share/osyr/models")
EXTRA = os.environ.get("OSYR_MODELS_EXTRA", "/home/.osyr/models")
CONF = os.environ.get("OSYR_LLAMA_CONF", "/etc/osyr/llama.conf")
PROC = os.environ.get("OSYR_PROC", "/proc")
LLAMA_SERVER = os.environ.get("OSYR_LLAMA_SERVER", "/usr/bin/llama-server")
GIB = 1024 ** 3

# What can be downloaded. "ram_gb": memory from which it is offered (a "32 GB"
# computer reports about 31). Sizes and sha256 as published; the address is
# pinned to one revision so that the file cannot change under the checksum.
_HF = "https://huggingface.co/unsloth/Qwen3.6-35B-A3B-GGUF/resolve/a483e9e6cbd595906af30beda3187c2663a1118c/"
CATALOG = {
    "large": {
        "title": "Qwen3.6-35B-A3B Q4_K_M (the base model, not yet fine-tuned for OSYR)",
        "license": "Apache-2.0",
        "ram_gb": 30,
        "files": [
            {"role": "model", "name": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
             "url": _HF + "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf", "size": 22134528992,
             "sha256": "ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61"},
            {"role": "mmproj", "name": "Qwen3.6-35B-A3B-mmproj-F16.gguf",
             "url": _HF + "mmproj-F16.gguf", "size": 899283680,
             "sha256": "8971ee4f331ff0a4c609374f32984b3d4e6dc086c0aa35f1d637fad1829e887f"},
        ],
    },
}


def need_root(action):
    if os.geteuid() != 0 and "OSYR_MODELS_EXTRA" not in os.environ:
        return fail("NEEDS_ROOT", f"Run: sudo osyr ai local {action}",
                    text="Changing the models of this computer needs administrator rights.")
    return None


def ram_gb():
    try:
        for line in open(os.path.join(PROC, "meminfo")):
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1048576
    except OSError:
        pass
    return 0.0


def conf_values():
    """MODEL, MMPROJ and GPU as written in llama.conf ('' = not set)."""
    values = {"MODEL": "", "MMPROJ": "", "GPU": ""}
    try:
        for line in open(CONF):
            key, eq, value = line.strip().partition("=")
            if eq and key in values:
                values[key] = value.strip().strip('"\'')
    except OSError:
        pass
    return values


def write_conf(**new):
    """Sets KEY=value lines of llama.conf, keeping everything else as it is."""
    try:
        lines = open(CONF).read().splitlines()
    except OSError:
        lines = []
    seen = set()
    for i, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in new and "=" in line and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={new[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in new.items() if k not in seen]
    tmp = CONF + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, CONF)


def image_models():
    """(model, mmproj) file names in the image's folder, as osyr-llama autodetects them."""
    try:
        names = sorted(n for n in os.listdir(MODELS) if n.endswith(".gguf"))
    except OSError:
        names = []
    plain = [n for n in names if "mmproj" not in n]
    proj = [n for n in names if "mmproj" in n]
    return (plain[0] if plain else ""), (proj[0] if proj else "")


def path_of(entry, role):
    f = next((f for f in entry["files"] if f["role"] == role), None)
    return os.path.join(EXTRA, f["name"]) if f else ""


def installed(entry):
    return all(os.path.isfile(os.path.join(EXTRA, f["name"])) and
               os.path.getsize(os.path.join(EXTRA, f["name"])) == f["size"] for f in entry["files"])


def active_id():
    """'small', a catalog id, or the file name of whatever else llama.conf names."""
    model = conf_values()["MODEL"]
    for ident, entry in CATALOG.items():
        if model == path_of(entry, "model"):
            return ident
    if not model or (not model.startswith("/") and model == image_models()[0]):
        return "small"
    return os.path.basename(model)


def state():
    small, _ = image_models()
    ram, active = ram_gb(), active_id()
    items = [{"id": "small", "title": small or "(no model in the image)", "installed": bool(small),
              "active": active == "small", "size_gb": None, "fits": True, "downloadable": False}]
    for ident, e in CATALOG.items():
        items.append({"id": ident, "title": e["title"], "installed": installed(e),
                      "active": active == ident, "fits": ram >= e["ram_gb"],
                      "size_gb": round(sum(f["size"] for f in e["files"]) / 1e9, 1),
                      "needs_ram_gb": e["ram_gb"] + 2, "license": e["license"], "downloadable": True})
    return {"active": active, "ram_gb": round(ram, 1), "models": items}


def list_models(args):
    st = state()
    lines = []
    for m in st["models"]:
        if m["id"] == "small":
            what = "in the image"
        elif m["installed"]:
            what = f'downloaded, {m["size_gb"]:g} GB'
        else:
            what = f'not downloaded, {m["size_gb"]:g} GB'
            if not m["fits"]:
                what += f'; needs {m["needs_ram_gb"]} GB of memory, this computer has {st["ram_gb"]:g}'
        lines.append(f'{"*" if m["active"] else " "} {m["id"]:<6} {m["title"]}  ({what})')
    return Result(True, "OK", st, text="\n".join(lines))


def _hash(path, progress=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(4 * 1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def fetch(f, dest, show=None, attempts=8, pause=5):
    """Downloads one file to dest, resuming dest.part. Returns None or an error Result."""
    part = dest + ".part"
    error = ""
    for attempt in range(attempts):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have > f["size"]:
            os.remove(part)
            have = 0
        if have == f["size"]:
            break
        req = urllib.request.Request(f["url"], headers={"User-Agent": "osyr"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:  # the server sent the whole file again
                    have = 0
                with open(part, "ab" if have else "wb") as out:
                    while True:
                        block = r.read(1024 * 1024)
                        if not block:
                            break
                        out.write(block)
                        have += len(block)
                        if show:
                            show(f, have)
        except (urllib.error.URLError, OSError, ValueError) as e:
            error = str(getattr(e, "reason", e))
            if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 404, 416):
                break  # trying again will not help
            time.sleep(pause)
    if not os.path.exists(part) or os.path.getsize(part) != f["size"]:
        return fail("MODEL_DOWNLOAD_FAILED", "Run it again: it continues where it stopped.",
                    {"file": f["name"], "error": error},
                    text=f'The download of {f["name"]} did not finish ({error or "cut"}).')
    if show:
        show(f, f["size"], checking=True)
    if _hash(part) != f["sha256"]:
        os.remove(part)
        return fail("MODEL_BAD_CHECKSUM", "Run it again; if it repeats, report it with /report.",
                    {"file": f["name"]},
                    text=f'{f["name"]} is not the expected file (wrong checksum). It was deleted.')
    os.replace(part, dest)
    os.chmod(dest, 0o644)
    return None


def _progress():
    last = [0.0]

    def show(f, have, checking=False):
        if not sys.stderr.isatty():
            return
        now = time.time()
        if checking:
            sys.stderr.write(f'\r{f["name"]}: checking the file...        \n')
        elif now - last[0] >= 1 or have == f["size"]:
            last[0] = now
            sys.stderr.write(f'\r{f["name"]}: {have / 1e9:.1f} of {f["size"] / 1e9:.1f} GB '
                             f'({100 * have // f["size"]} %)   ')
        sys.stderr.flush()
    return show


def get(args):
    entry = CATALOG.get(args.id)
    if not entry:
        return fail("MODEL_UNKNOWN", "Run: osyr ai local list", {"id": args.id})
    r = need_root(f"get {args.id}")
    if r:
        return r
    if installed(entry):
        return Result(True, "OK", {"id": args.id, "installed": True},
                      f"Run: sudo osyr ai local use {args.id}", text="Already downloaded.")
    missing = 0
    for f in entry["files"]:
        dest = os.path.join(EXTRA, f["name"])
        if not (os.path.isfile(dest) and os.path.getsize(dest) == f["size"]):
            part = dest + ".part"
            missing += f["size"] - (os.path.getsize(part) if os.path.exists(part) else 0)
    os.makedirs(EXTRA, mode=0o755, exist_ok=True)
    st = os.statvfs(EXTRA)
    free = st.f_bavail * st.f_frsize
    data = {"id": args.id, "download_gb": round(missing / 1e9, 1), "free_gb": round(free / 1e9, 1),
            "license": entry["license"], "fits": ram_gb() >= entry["ram_gb"]}
    if free < missing + GIB:
        return fail("MODEL_NO_SPACE", "Free some space in /home and run it again.", data,
                    text=f'Not enough space: {data["download_gb"]:g} GB to download, '
                         f'{data["free_gb"]:g} GB free in {EXTRA}.')
    token = f"download-{args.id}"
    if args.confirm != token:
        msg = (f'{entry["title"]}: {data["download_gb"]:g} GB to download into {EXTRA} '
               f'(licence {entry["license"]}).')
        if not data["fits"]:
            msg += (f'\nThis computer has {ram_gb():.1f} GB of memory and the model needs '
                    f'{entry["ram_gb"] + 2}: it can be downloaded, but it will not run well here.')
        if not args.json and sys.stdin.isatty() and args.confirm is None:
            print(msg)
            if input("Download it? [y/N] ").strip().lower() not in ("y", "yes", "s", "si", "sí"):
                return Result(True, "MODEL_CANCELLED", data, text="Nothing was downloaded.")
        else:
            return Result(False, "CONFIRM_REQUIRED", data,
                          f"Run: sudo osyr ai local get {args.id} --confirm {token}", confirm=token, text=msg)
    show = None if args.json else _progress()
    for f in entry["files"]:
        dest = os.path.join(EXTRA, f["name"])
        if os.path.isfile(dest) and os.path.getsize(dest) == f["size"]:
            continue
        r = fetch(f, dest, show)
        if r:
            return r
    return Result(True, "OK", data | {"installed": True}, f"Run: sudo osyr ai local use {args.id}",
                  text=f'Downloaded. To use it: sudo osyr ai local use {args.id}')


def restart():
    subprocess.run(["systemctl", "stop", "osyr-llama-vision.service"], capture_output=True)
    p = subprocess.run(["systemctl", "restart", "osyr-llama.service"], capture_output=True, text=True)
    return p.returncode == 0, p.stderr.strip()


def use(args):
    r = need_root(f"use {args.id}")
    if r:
        return r
    if args.id == "small":
        model, mmproj = image_models()
        if not model:
            return fail("MODEL_NOT_INSTALLED", None, {"id": "small"}, text="The image has no model.")
    elif args.id in CATALOG:
        entry = CATALOG[args.id]
        if not installed(entry):
            return fail("MODEL_NOT_INSTALLED", f"Run: sudo osyr ai local get {args.id}", {"id": args.id},
                        text="That model is not downloaded yet.")
        if ram_gb() < entry["ram_gb"] and not args.force:
            return fail("MODEL_TOO_BIG", f"To try anyway: sudo osyr ai local use {args.id} --force",
                        {"id": args.id, "ram_gb": round(ram_gb(), 1), "needs_ram_gb": entry["ram_gb"] + 2},
                        text=f'This computer has {ram_gb():.1f} GB of memory; the model needs '
                             f'{entry["ram_gb"] + 2}.')
        model, mmproj = path_of(entry, "model"), path_of(entry, "mmproj")
    else:
        return fail("MODEL_UNKNOWN", "Run: osyr ai local list", {"id": args.id})
    if active_id() == args.id:
        return Result(True, "OK", {"active": args.id}, text=f'Already using "{args.id}".')
    write_conf(MODEL=model, MMPROJ=mmproj)
    ok, err = restart()
    if not ok:
        return fail("MODEL_RESTART_FAILED", "Run: systemctl status osyr-llama", {"active": args.id, "error": err},
                    text=f"The model was changed, but its service did not restart: {err}")
    return Result(True, "OK", {"active": args.id, "model": os.path.basename(model)},
                  "It takes a while to load; then: osyr ai advise --measure",
                  text=f'Using "{args.id}" ({os.path.basename(model)}). It is loading.')


def remove(args):
    entry = CATALOG.get(args.id)
    if not entry:
        return fail("MODEL_UNKNOWN", "Only downloaded models can be removed. Run: osyr ai local list",
                    {"id": args.id})
    r = need_root(f"remove {args.id}")
    if r:
        return r
    if active_id() == args.id:
        return fail("MODEL_IN_USE", "First: sudo osyr ai local use small", {"id": args.id},
                    text="That model is in use.")
    freed = 0
    for f in entry["files"]:
        for path in (os.path.join(EXTRA, f["name"]), os.path.join(EXTRA, f["name"]) + ".part"):
            if os.path.exists(path):
                freed += os.path.getsize(path)
                os.remove(path)
    return Result(True, "OK", {"removed": args.id, "freed_gb": round(freed / 1e9, 1)},
                  text=f'Removed "{args.id}" ({freed / 1e9:.1f} GB freed).')


# --- graphics card -------------------------------------------------------------------

def gpu_devices():
    """The graphics cards llama-server can run the model on (Vulkan), as it lists them."""
    try:
        p = subprocess.run([LLAMA_SERVER, "--list-devices"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in (p.stdout + "\n" + p.stderr).splitlines():
        m = re.match(r"\s*(\w+):\s+(.+?)\s+\((\d+) MiB, (\d+) MiB free\)\s*$", line)
        if m:
            found.append({"id": m.group(1), "name": m.group(2),
                          "vram_gb": round(int(m.group(3)) / 1024, 1),
                          "free_gb": round(int(m.group(4)) / 1024, 1)})
    return found


def gpu_on():
    return conf_values()["GPU"].lower() not in ("", "off", "no", "0")


def wait_ready(timeout=900):
    """True when the local server answers again after a restart (a large model takes minutes)."""
    from . import ai
    url = ai.server_root(ai.provider("local")["url"]) + "/health"
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(3)
    return False


def gpu_status(args):
    devices, on = gpu_devices(), gpu_on()
    if not devices:
        text = "No graphics card that OSYR can run the model on (AMD and Intel cards with Vulkan)."
    else:
        text = "\n".join(f'{d["id"]}: {d["name"]}, {d["vram_gb"]:g} GB' for d in devices)
        text += "\nThe model runs on " + ("the graphics card." if on else "the processor.")
    return Result(True, "OK", {"gpu": on, "devices": devices},
                  "To try the graphics card: sudo osyr ai gpu on" if devices and not on else None, text=text)


def _back_to_cpu(before):
    from . import advise
    write_conf(GPU="off")
    restart()
    wait_ready()
    if before:
        advise.save_speed(before)


def gpu_set(args):
    from . import advise
    r = need_root(f"gpu {args.state}")
    if r:
        return r
    if args.state == "off":
        if gpu_on():
            write_conf(GPU="off")
            restart()
        return Result(True, "OK", {"gpu": False}, text="The model runs on the processor.")
    devices = gpu_devices()
    if not devices:
        return fail("GPU_NONE", None, {"devices": []},
                    text="No graphics card that OSYR can run the model on (AMD and Intel cards with Vulkan).")
    if gpu_on():
        return Result(True, "OK", {"gpu": True, "devices": devices}, text="The graphics card is already in use.")
    # The processor's speed first, to compare: the saved one, or measured now
    before = advise.saved_speed() or advise.measure()[0]
    write_conf(GPU="on")
    ok, err = restart()
    if not ok or not wait_ready():
        _back_to_cpu(before)
        return fail("GPU_FAILED", "Report it with /report: it helps to support this card.",
                    {"devices": devices, "error": err},
                    text=f'The model did not start on {devices[0]["name"]}. Back on the processor.')
    after, err = advise.measure()
    data = {"gpu": True, "devices": devices, "processor": before, "card": after}
    if after is None:
        _back_to_cpu(before)
        return fail("GPU_FAILED", "Report it with /report: it helps to support this card.",
                    data | {"gpu": False, "error": err},
                    text=f'The model did not answer on {devices[0]["name"]} ({err}). Back on the processor.')
    t_card = advise.request_seconds(after)
    t_cpu = advise.request_seconds(before) if before else None
    if t_cpu is not None and t_card >= t_cpu and not args.keep:
        _back_to_cpu(before)
        return Result(False, "GPU_SLOWER", data | {"gpu": False},
                      "To use the card anyway: sudo osyr ai gpu on --keep",
                      text=f'On {devices[0]["name"]} a simple request takes about {t_card} s; on the '
                           f"processor, about {t_cpu} s. The model stays on the processor.")
    versus = f"; on the processor it was about {t_cpu} s" if t_cpu is not None else ""
    return Result(True, "OK", data,
                  text=f'The model now runs on {devices[0]["name"]}: about {t_card} s for a simple request{versus}.')


def register(s):
    g = s.add_parser("gpu", help="run the local model on a graphics card (kept only if it is faster)")
    ga = g.add_subparsers(dest="gpu_action", required=True)
    ga.add_parser("status", help="the cards OSYR can use and whether one is in use").set_defaults(func=gpu_status)
    for state in ("on", "off"):
        x = ga.add_parser(state, help="switch to the graphics card and measure it" if state == "on"
                          else "back to the processor")
        x.add_argument("--keep", action="store_true", help="keep the card even if it is slower")
        x.set_defaults(func=gpu_set, state=state)
    p = s.add_parser("local", help="the models on this computer: list, download, switch, remove")
    a = p.add_subparsers(dest="local_action", required=True)
    a.add_parser("list", help="models on this computer and those that can be downloaded").set_defaults(func=list_models)
    g = a.add_parser("get", help="download a model (it continues if it was cut)")
    g.add_argument("id")
    g.add_argument("--confirm", metavar="TOKEN")
    g.set_defaults(func=get)
    u = a.add_parser("use", help="switch the local model: 'small' (the image's) or a downloaded one")
    u.add_argument("id")
    u.add_argument("--force", action="store_true", help="even if the computer has less memory than it needs")
    u.set_defaults(func=use)
    r = a.add_parser("remove", help="delete a downloaded model")
    r.add_argument("id")
    r.set_defaults(func=remove)
