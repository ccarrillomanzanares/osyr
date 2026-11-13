"""osyr box — programs that are not in OSYR, installed in a box.

OSYR's own system is replaced as a whole by updates and has no package manager.
What the user asks to install goes into "the box": a container with Debian
stable, run by podman as the user (no root), with Debian's own package manager
inside. There is one box per user; it is created the first time something is
installed and lives in the user's folder (~/.local/share/containers), so it
survives system updates.

The box is there to keep the system clean, not to distrust the programs: they
see the user's folder, the screen and the sound, like programs on any Linux.
They do not see what holds keys and passwords (HIDDEN), nor the system itself.

Graphical programs installed here are opened with 'osyr app'; programs
without a window, with 'osyr box run'.

State, in ~/.local/share/osyr/box/:
  installed.json  the packages the user asked for (not their dependencies)
  apps.json       the applications found in the box, for 'osyr app'
  lists           its date is the last time the package lists were downloaded
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

from .result import Result, fail

PODMAN = os.environ.get("OSYR_PODMAN", "podman")
IMAGE = os.environ.get("OSYR_BOX_IMAGE", "docker.io/library/debian:stable")
NAME = os.environ.get("OSYR_BOX_NAME", "osyr-box")
LISTS_MAX_AGE = 24 * 3600   # the package lists are downloaded again after a day
MIN_FREE_MB = 500           # below this, nothing is installed
COVER = "tmpfs-size=64k,tmpfs-mode=0700,notmpcopyup"   # an empty folder over one the box must not see
OUT_MAX = 2800              # characters of output the model sees from 'box run'
PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,80}$")
# Folders of the user that the programs in the box do not see: keys, passwords,
# the assistant's own configuration, conversations and logs, and the box itself
HIDDEN = (".ssh", ".gnupg", ".password-store", ".config/osyr", ".local/share/osyr", ".local/state/osyr",
          ".local/share/keyrings", ".local/share/containers")
HIDDEN_RUNTIME = ("osyr", "containers", "libpod", "crun")


class BoxError(Exception):
    def __init__(self, code, hint=None, data=None):
        super().__init__(code)
        self.code, self.hint, self.data = code, hint, data or {}


def home():
    return os.path.expanduser("~")


def state_dir():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(home(), ".local", "share"), "osyr", "box")


def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"


def say(text):
    """Progress, for the person: to stderr, so that the JSON on stdout stays alone."""
    print(text, file=sys.stderr, flush=True)


def load(name, default):
    try:
        with open(os.path.join(state_dir(), name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save(name, value):
    os.makedirs(state_dir(), exist_ok=True)
    tmp = os.path.join(state_dir(), name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(value, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(state_dir(), name))


# --- podman ------------------------------------------------------------------------------

def pm(*args, timeout=120, check=False):
    """Runs podman. Raises BoxError if it is missing or (with check) if it fails."""
    try:
        p = subprocess.run([PODMAN, *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise BoxError("BOX_NO_ENGINE", "This OSYR image cannot install programs (no container engine).")
    except subprocess.TimeoutExpired:
        raise BoxError("BOX_TIMEOUT", "It took too long. Check the Internet connection and try again.",
                       {"seconds": timeout})
    if check and p.returncode != 0:
        raise BoxError("BOX_FAILED", None, {"error": (p.stderr or p.stdout).strip()[-400:]})
    return p


def exists():
    return pm("container", "exists", NAME).returncode == 0


def running():
    p = pm("inspect", "--type", "container", "--format", "{{.State.Running}}", NAME)
    return p.returncode == 0 and p.stdout.strip() == "true"


def env_args():
    """The session, for a program in the box: screen, sound, language."""
    run = runtime_dir()
    env = {"XDG_RUNTIME_DIR": run, "HOME": home(), "USER": os.environ.get("USER", "osyr"),
           "LANG": os.environ.get("LANG", "C.UTF-8"), "XDG_SESSION_TYPE": "wayland",
           "QT_QPA_PLATFORM": "wayland;xcb", "GDK_BACKEND": "wayland,x11", "MOZ_ENABLE_WAYLAND": "1",
           "SDL_VIDEODRIVER": "wayland,x11", "PULSE_SERVER": f"unix:{run}/pulse/native",
           "DBUS_SESSION_BUS_ADDRESS": f"unix:path={run}/bus", "NO_AT_BRIDGE": "1"}
    for k in ("WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP", "TERM", "LC_ALL"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return [x for k, v in env.items() for x in ("-e", f"{k}={v}")]


def free_mb():
    try:
        st = os.statvfs(home())
        return st.f_bavail * st.f_frsize // (1024 * 1024)
    except OSError:
        return None


def create():
    """Downloads Debian and makes the box. The folders in HIDDEN are covered by empty ones."""
    h, run = home(), runtime_dir()
    if (free_mb() or MIN_FREE_MB) < MIN_FREE_MB:
        raise BoxError("BOX_DISK_FULL", "There is not enough free space for programs. If OSYR runs from the USB "
                       "stick, install it on the disk first (/install).", {"free_mb": free_mb()})
    say("Downloading Debian for the box (about 50 MB, only this first time)...")
    p = pm("pull", "-q", IMAGE, timeout=1800)
    if p.returncode != 0:
        raise BoxError("BOX_DOWNLOAD_FAILED", "Check the Internet connection and try again.",
                       {"error": p.stderr.strip()[-300:]})
    say("Preparing the box...")
    # .ssh and .gnupg may not exist yet: made now, private, so that keys created later stay hidden too
    for d in (".ssh", ".gnupg"):
        os.makedirs(os.path.join(h, d), mode=0o700, exist_ok=True)
    args = ["create", "--name", NAME, "--hostname", "box", "--init", "--userns=keep-id",
            "--security-opt", "label=disable", "--workdir", h,
            "-v", f"{h}:{h}", "-v", f"{run}:{run}", "-v", "/etc/localtime:/etc/localtime:ro"]
    # notmpcopyup: by default the engine copies into the empty folder what was under it,
    # which here is the very thing being hidden (and podman's storage does not fit)
    for d in HIDDEN:
        if os.path.isdir(os.path.join(h, d)):
            args += ["--mount", f"type=tmpfs,destination={os.path.join(h, d)},{COVER}"]
    for d in HIDDEN_RUNTIME:
        args += ["--mount", f"type=tmpfs,destination={os.path.join(run, d)},{COVER}"]
    if os.path.isdir("/dev/dri"):
        args += ["-v", "/dev/dri:/dev/dri"]
    pm(*args, IMAGE, "sleep", "infinity", timeout=600, check=True)
    start()
    refresh_lists(force=True)
    # The user's language, so that programs speak it (Debian generates only what is asked for)
    lang = os.environ.get("LANG", "")
    if re.match(r"^[a-z]{2,3}_[A-Z]{2}\.UTF-8$", lang):
        say("Setting up the language...")
        root(f"apt-get install -y -q locales && sed -i 's/^# *{lang} /{lang} /' /etc/locale.gen && locale-gen",
             timeout=900)


def start():
    pm("start", NAME, timeout=120, check=True)
    os.makedirs(os.path.join(runtime_dir(), "osyr"), exist_ok=True)
    open(os.path.join(runtime_dir(), "osyr", "box-started"), "w").close()


def ensure(make=False):
    """The box, running. make: create it if it does not exist yet."""
    if not exists():
        if not make:
            raise BoxError("BOX_NOT_CREATED", "There is no box yet: it is created when the first program is "
                           "installed. Run: osyr box install <program>")
        create()
        return
    if running():
        # Started in an earlier session (before logging out and in again)? Its view of
        # the session's folder, with the screen and the sound, is stale: started again
        if os.path.exists(os.path.join(runtime_dir(), "osyr", "box-started")):
            return
        pm("stop", "-t", "3", NAME, timeout=60)
    start()


def root(script, timeout=900):
    """A shell line as the box's administrator (nothing to do with the computer's)."""
    return pm("exec", "--user", "0", "-e", "DEBIAN_FRONTEND=noninteractive", "-e", "LC_ALL=C", "-w", "/",
              NAME, "sh", "-c", script, timeout=timeout)


def refresh_lists(force=False):
    stamp = os.path.join(state_dir(), "lists")
    try:
        fresh = time.time() - os.path.getmtime(stamp) < LISTS_MAX_AGE
    except OSError:
        fresh = False
    if fresh and not force:
        return
    say("Reading the list of available programs...")
    p = root("apt-get update -q", timeout=900)
    if p.returncode != 0:
        raise BoxError("BOX_DOWNLOAD_FAILED", "Check the Internet connection and try again.",
                       {"error": (p.stderr or p.stdout).strip()[-300:]})
    os.makedirs(state_dir(), exist_ok=True)
    open(stamp, "w").close()


# --- applications found in the box (for osyr app) -------------------------------------

def parse_desktop(text):
    """The [Desktop Entry] group of a .desktop file as a dict."""
    out, inside = {}, False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            inside = line == "[Desktop Entry]"
        elif inside and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def app_entry(app_id, entry, lang=""):
    """What 'osyr app' needs from a desktop entry, or None if it is not an application to offer."""
    if entry.get("Type", "Application") != "Application" or entry.get("NoDisplay", "").lower() == "true" \
            or entry.get("Hidden", "").lower() == "true" or not entry.get("Exec"):
        return None
    short = lang.split("_")[0].split(".")[0]
    name = entry.get(f"Name[{lang.split('.')[0]}]") or entry.get(f"Name[{short}]") or entry.get("Name") or app_id
    about = entry.get(f"Comment[{short}]") or entry.get("Comment") or entry.get("GenericName") or ""
    return {"id": app_id, "name": name, "about": about[:80], "exec": entry["Exec"],
            "terminal": entry.get("Terminal", "").lower() == "true"}


def refresh_apps():
    """Reads the applications installed in the box and saves them. Returns the list."""
    p = root("for f in /usr/share/applications/*.desktop; do [ -f \"$f\" ] && echo \"@@ $f\" && cat \"$f\"; done; true",
             timeout=60)
    apps = []
    for block in re.split(r"^@@ ", p.stdout, flags=re.M)[1:]:
        path, _, text = block.partition("\n")
        app_id = os.path.basename(path.strip())[:-len(".desktop")]
        a = app_entry(app_id, parse_desktop(text), os.environ.get("LANG", ""))
        if a:
            apps.append(a)
    apps.sort(key=lambda a: a["name"].lower())
    save("apps.json", apps)
    return apps


def apps():
    return load("apps.json", [])


# --- commands --------------------------------------------------------------------------

def guarded(fn):
    def run(args):
        try:
            return fn(args)
        except BoxError as e:
            return fail(e.code, e.hint, e.data)
    return run


def check_names(names):
    bad = [n for n in names if not PACKAGE.match(n)]
    if bad:
        raise BoxError("BOX_BAD_NAME", "A program is named with lowercase letters, digits and hyphens, like "
                       "'vlc' or 'libreoffice-writer'. To find the name: osyr box search <words>", {"names": bad})


@guarded
def status(args):
    if not shutil.which(PODMAN):
        raise BoxError("BOX_NO_ENGINE", "This OSYR image cannot install programs (no container engine).")
    made = exists()
    data = {"created": made, "running": made and running(), "system": "Debian stable",
            "programs": load("installed.json", []), "applications": [a["id"] for a in apps()], "free_mb": free_mb()}
    text = ("The box has not been created yet: it is created when the first program is installed."
            if not made else
            f"Box with Debian stable, {'running' if data['running'] else 'stopped'}.\n"
            f"Programs installed: {', '.join(data['programs']) or 'none'}")
    return Result(data=data, text=text, hint=None if made else "To install a program: osyr box install <program>")


@guarded
def install(args):
    check_names(args.program)
    ensure(make=True)
    if (free_mb() or MIN_FREE_MB) < MIN_FREE_MB:
        raise BoxError("BOX_DISK_FULL", "There is not enough free space. Remove a program (osyr box remove) or "
                       "free space in the user's folder.", {"free_mb": free_mb()})
    refresh_lists()
    before = {a["id"] for a in apps()}
    say(f"Installing {' '.join(args.program)} (it can take a few minutes)...")
    p = root("apt-get install -y -q " + " ".join(args.program), timeout=3000)
    out = p.stdout + p.stderr
    if p.returncode != 0:
        missing = re.findall(r"Unable to locate package (\S+)", out)
        if missing:
            raise BoxError("BOX_NO_SUCH_PROGRAM", f"Debian has no program with that name. Look for it: "
                           f"osyr box search {missing[0]}", {"not_found": missing})
        if "No space left" in out:
            raise BoxError("BOX_DISK_FULL", "The disk filled up. Remove a program or free space.",
                           {"free_mb": free_mb()})
        raise BoxError("BOX_INSTALL_FAILED", None, {"error": out.strip()[-400:]})
    done = load("installed.json", [])
    save("installed.json", sorted(set(done) | set(args.program)))
    new = [a for a in refresh_apps() if a["id"] not in before]
    data = {"installed": args.program, "applications": [{"id": a["id"], "name": a["name"]} for a in new]}
    m = re.search(r"Need to get ([\d.,]+ [kMG]?B)", out)
    if m:
        data["downloaded"] = m.group(1)
    m = re.search(r"After this operation, ([\d.,]+ [kMG]?B) of additional disk space", out)
    if m:
        data["disk_used"] = m.group(1)
    if "is already the newest version" in out and not new and not m:
        data["already_installed"] = True
    hint = (f"Open it with: osyr app run {new[0]['id']}" if new else
            "It has no window. Run it with: osyr box run <command>")
    return Result(data=data, hint=hint, text=f"Installed: {' '.join(args.program)}")


@guarded
def remove(args):
    check_names(args.program)
    done = load("installed.json", [])
    unknown = [n for n in args.program if n not in done]
    if unknown:
        raise BoxError("BOX_NOT_INSTALLED", "See what is installed: osyr box list",
                       {"not_installed": unknown, "installed": done})
    ensure()
    say(f"Removing {' '.join(args.program)}...")
    p = root("apt-get remove -y -q --autoremove " + " ".join(args.program), timeout=900)
    if p.returncode != 0:
        raise BoxError("BOX_REMOVE_FAILED", None, {"error": (p.stdout + p.stderr).strip()[-400:]})
    save("installed.json", [n for n in done if n not in args.program])
    refresh_apps()
    return Result(data={"removed": args.program}, text=f"Removed: {' '.join(args.program)}")


@guarded
def search(args):
    words = [w for w in args.words if re.match(r"^[\w+.-]{2,40}$", w)]
    if not words:
        raise BoxError("USAGE", "Give one or more words: osyr box search video player")
    ensure()
    refresh_lists()
    p = root("apt-cache search -- " + " ".join(words), timeout=120)
    found = []
    for line in p.stdout.splitlines():
        name, sep, about = line.partition(" - ")
        if sep:
            found.append({"name": name.strip(), "about": about.strip()[:90]})
    # Names with the words first, then libraries and the rest; -dev, -doc, -dbg at the end
    def rank(r):
        n = r["name"]
        return (bool(re.search(r"-(dev|doc|dbg|dbgsym|data|common)$", n)) or n.startswith("lib"),
                not any(w.lower() in n for w in words), len(n))
    found.sort(key=rank)
    return Result(data={"results": found[:args.limit], "total": len(found)},
                  hint="To install one: osyr box install <name>" if found else "Nothing found: try other words, in English.",
                  text="\n".join(f"{r['name']}: {r['about']}" for r in found[:args.limit]) or "Nothing found.")


@guarded
def listing(args):
    done, found = load("installed.json", []), apps()
    return Result(data={"programs": done, "applications": [{"id": a["id"], "name": a["name"]} for a in found]},
                  text=("Programs: " + (", ".join(done) or "none") + "\nApplications: "
                        + (", ".join(f"{a['name']} ({a['id']})" for a in found) or "none")),
                  hint=None if done else "To install a program: osyr box install <program>")


@guarded
def run(args):
    if not args.command:
        raise BoxError("USAGE", "Give the command: osyr box run ffmpeg -version")
    ensure()
    cwd = os.getcwd()
    cwd = cwd if (cwd == home() or cwd.startswith(home() + "/")) else home()
    t0 = time.monotonic()
    p = pm("exec", "--user", f"{os.getuid()}:{os.getgid()}", "-w", cwd, *env_args(), NAME, *args.command,
           timeout=args.timeout)
    cut = len(p.stdout) > OUT_MAX
    data = {"exit": p.returncode, "stdout": p.stdout[:OUT_MAX], "stderr": p.stderr[-700:],
            "seconds": round(time.monotonic() - t0, 1)}
    if cut:
        data["truncated"] = True
    if p.returncode == 127 or (p.returncode in (126, 255) and "not found" in p.stderr):
        return fail("BOX_NO_SUCH_COMMAND", f"That command is not in the box. Install it: "
                    f"osyr box install {args.command[0]}", data)
    return Result(ok=p.returncode == 0, code="OK" if p.returncode == 0 else "EXIT_NONZERO", data=data,
                  text=(p.stdout + p.stderr).rstrip())


@guarded
def update(args):
    ensure()
    refresh_lists(force=True)
    say("Updating the programs in the box...")
    p = root("apt-get upgrade -y -q", timeout=3000)
    if p.returncode != 0:
        raise BoxError("BOX_UPDATE_FAILED", None, {"error": (p.stdout + p.stderr).strip()[-400:]})
    m = re.search(r"(\d+) upgraded", p.stdout)
    refresh_apps()
    return Result(data={"upgraded": int(m.group(1)) if m else 0},
                  text=f"{m.group(1) if m else 0} packages updated.")


@guarded
def reset(args):
    if not exists():
        return Result(data={"removed": False}, text="There is no box.")
    done = load("installed.json", [])
    token = "reset"
    if args.confirm != token:
        question = (f"This deletes the box and the {len(done)} program(s) installed in it "
                    f"({', '.join(done) or 'none'}). Your own files are not touched.")
        if not args.json and sys.stdin.isatty() and args.confirm is None:
            print(question)
            if input("Continue? [y/N] ").strip().lower() not in ("y", "yes", "s", "si", "sí"):
                return fail("CANCELLED", text="Nothing was deleted.")
        else:
            return Result(False, "CONFIRM_REQUIRED", {"programs": done}, question, confirm=token, text=question)
    pm("rm", "-f", "-t", "3", NAME, timeout=120, check=True)
    for f in ("installed.json", "apps.json", "lists"):
        try:
            os.unlink(os.path.join(state_dir(), f))
        except OSError:
            pass
    return Result(data={"removed": True, "programs": done}, text="The box was deleted.",
                  hint="A new one is created when a program is installed.")


def register(sub):
    p = sub.add_parser("box", help="programs that are not in OSYR, installed in a box (Debian)")
    b = p.add_subparsers(dest="cmd", required=True)
    b.add_parser("status", help="whether the box exists and what is in it").set_defaults(func=status)
    i = b.add_parser("install", help="install a program (creates the box the first time)")
    i.add_argument("program", nargs="+")
    i.set_defaults(func=install)
    r = b.add_parser("remove", help="remove a program")
    r.add_argument("program", nargs="+")
    r.set_defaults(func=remove)
    s = b.add_parser("search", help="look for a program by words")
    s.add_argument("words", nargs="+")
    s.add_argument("--limit", type=int, default=12)
    s.set_defaults(func=search)
    b.add_parser("list", help="the programs installed").set_defaults(func=listing)
    x = b.add_parser("run", help="run a command of a program without a window")
    x.add_argument("--timeout", type=int, default=120)
    x.add_argument("command", nargs="...")
    x.set_defaults(func=run)
    b.add_parser("update", help="update the programs in the box").set_defaults(func=update)
    z = b.add_parser("reset", help="delete the box and its programs (not your files)")
    z.add_argument("--confirm", metavar="TOKEN")
    z.set_defaults(func=reset)
