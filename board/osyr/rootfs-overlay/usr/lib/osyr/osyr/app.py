"""osyr app — open and close graphical applications.

The applications are the ones of the system (the .desktop files of
/usr/share/applications; the terminal is left out: the assistant lives in
one) and the ones installed in the box ('osyr box install', see box.py).

An application opens on a workspace of its own, the first free one from the
third on (the second is the web browser's), and the screen goes there; the
assistant stays on its workspace (Super+1 goes back).
"""
import glob
import json
import os
import re
import shlex
import subprocess
import time

from . import box
from .result import Result, fail

SYSTEM_DIR = os.environ.get("OSYR_APPLICATIONS", "/usr/share/applications")
SKIP = re.compile(r"^foot")  # the terminal: closing "foot" would close the assistant
HOME_WORKSPACE = 1
# What a program says when it only knows the old X11 display system, which OSYR does not have
NEEDS_X11 = re.compile(r"open display|cannot connect to X server|no display name|DISPLAY (is not set|environment variable)"
                       r"|could not connect to display|xcb.*could not|Gtk-WARNING.*cannot open display", re.I)
FIRST_WORKSPACE = 3  # 2 is the browser's (see /etc/sway/config)


def system_apps():
    out = []
    for path in sorted(glob.glob(os.path.join(SYSTEM_DIR, "*.desktop"))):
        app_id = os.path.basename(path)[:-len(".desktop")]
        if SKIP.match(app_id):
            continue
        try:
            entry = box.parse_desktop(open(path, encoding="utf-8", errors="replace").read())
        except OSError:
            continue
        a = box.app_entry(app_id, entry, os.environ.get("LANG", ""))
        if a:
            out.append(dict(a, where="system"))
    return out


def all_apps():
    return system_apps() + [dict(a, where="box") for a in box.apps()]


def command_of(app):
    """The program and arguments of the Exec line, without its %f-style placeholders."""
    try:
        words = shlex.split(app["exec"])
    except ValueError:
        words = app["exec"].split()
    words = [w for w in words if not re.match(r"^%[a-zA-Z]$", w)]
    while words and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0] == "env"):
        words = words[1:]
    return words


def binary(app):
    words = command_of(app)
    return os.path.basename(words[0]) if words else ""


def find(name):
    """The application the person means: by id, by name, or the only one that contains the words."""
    apps, low = all_apps(), name.strip().lower()
    for test in (lambda a: a["id"].lower() == low, lambda a: a["name"].lower() == low,
                 lambda a: binary(a).lower() == low,
                 lambda a: low in a["id"].lower() or low in a["name"].lower()):
        hit = [a for a in apps if test(a)]
        if len(hit) == 1:
            return hit[0], []
        if hit:
            return None, hit
    return None, []


def box_processes():
    """(pid as the system sees it, command name) of what runs in the box; empty if it is stopped."""
    try:
        if not box.exists() or not box.running():
            return []
        p = box.pm("top", box.NAME, "hpid", "comm", timeout=20)
    except box.BoxError:
        return []
    out = []
    for line in p.stdout.splitlines()[1:]:
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            out.append((int(parts[0]), parts[1].strip()))
    return out


def system_pids(name):
    try:
        p = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", name[:15]], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(x) for x in p.stdout.split() if x.isdigit()]


def pids(app, in_box=None):
    """The application's processes. in_box: what box_processes() gave, if already asked."""
    name = binary(app)
    if not name:
        return []
    in_box = box_processes() if in_box is None else in_box
    if app["where"] == "box":
        return [pid for pid, comm in in_box if comm == name[:15]]
    theirs = {pid for pid, _ in in_box}  # the box may hold a program with the same name
    return [pid for pid in system_pids(name) if pid not in theirs]


# --- sway ------------------------------------------------------------------------------

def sway(*args):
    if not os.environ.get("SWAYSOCK"):
        return None
    try:
        p = subprocess.run(["swaymsg", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def free_workspace():
    """Goes to the first workspace without windows (never the assistant's). Its number, or None."""
    out = sway("-t", "get_workspaces")
    try:
        used = {w.get("num") for w in json.loads(out or "")}
    except ValueError:
        return None
    n = next(i for i in range(FIRST_WORKSPACE, 100) if i not in used)
    return n if sway("workspace", "number", str(n)) is not None else None


def windows(app):
    """Ids of the sway windows of this application: those of its processes, and those that
    carry its name (a program started by a script has another process name) on its side
    of the fence. The box may hold a program the system has too, like the terminal."""
    out = sway("-t", "get_tree")
    try:
        tree = json.loads(out or "")
    except ValueError:
        return []
    in_box = box_processes()
    boxed, mine = {pid for pid, _ in in_box}, set(pids(app, in_box))
    want = {app["id"].lower(), binary(app).lower(), app["id"].lower().split(".")[-1]}
    found, stack = [], [tree]
    while stack:
        n = stack.pop()
        stack += n.get("nodes", []) + n.get("floating_nodes", [])
        pid = n.get("pid")
        if not pid:
            continue
        named = (n.get("app_id") or "").lower() in want and (pid in boxed) == (app["where"] == "box")
        if pid in mine or named:
            found.append(n["id"])
    return found


# --- commands --------------------------------------------------------------------------

def listing(args):
    procs = box_processes()
    apps = [{"id": a["id"], "name": a["name"], "where": a["where"], "running": bool(pids(a, procs))}
            for a in all_apps()]
    return Result(data={"applications": apps},
                  text="\n".join(f"{a['name']} ({a['id']}){' — running' if a['running'] else ''}" for a in apps)
                  or "No applications.",
                  hint="To open one: osyr app run <id>. To install another: osyr box install <program>")


def not_found(name, several):
    if several:
        return fail("APP_SEVERAL", "Say which one, by its id.",
                    {"matches": [{"id": a["id"], "name": a["name"]} for a in several[:8]]})
    return fail("APP_NOT_FOUND", f"It is not installed. See what there is: osyr app list. "
                f"To install it: osyr box install {name.lower().split()[0]}", {"name": name})


def run(args):
    app, several = find(args.app)
    if not app:
        return not_found(args.app, several)
    words = command_of(app)
    if not words:
        return fail("APP_BROKEN", None, {"id": app["id"]})
    files = []
    for f in args.file:
        path = f if "://" in f else os.path.abspath(os.path.expanduser(f))
        if "://" not in f and not os.path.exists(path):
            return fail("APP_NO_SUCH_FILE", "Find it first: osyr files find <words>", {"file": f})
        files.append(path)
    if not os.environ.get("WAYLAND_DISPLAY"):
        return fail("APP_NO_SESSION", "Applications open from the graphical session.")
    if app["where"] == "box":
        try:
            box.ensure()
        except box.BoxError as e:
            return fail(e.code, e.hint, e.data)
        cwd = box.home()
        cmd = [box.PODMAN, "exec", "--user", f"{os.getuid()}:{os.getgid()}", "-w", cwd, *box.env_args()]
        cmd = cmd + ["-it", box.NAME, *words, *files] if app["terminal"] else cmd + [box.NAME, *words, *files]
    else:
        cmd = [*words, *files]
    if app["terminal"]:
        cmd = ["foot", "--title", app["name"], "-e", *cmd]
    workspace = free_workspace()
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                             start_new_session=True, cwd=box.home())
    except OSError as e:
        return fail("APP_FAILED", None, {"error": str(e)})
    # A program that cannot start says so within a moment; one that opened keeps running
    try:
        p.wait(timeout=args.wait)
        err = (p.stderr.read() or b"").decode(errors="replace").strip()[-300:]
        if p.returncode != 0:
            if workspace:
                sway("workspace", "number", str(HOME_WORKSPACE))
            if NEEDS_X11.search(err):
                return fail("APP_NEEDS_X11", "This program only works with the old X11 display system, which OSYR "
                            "does not have (OSYR uses Wayland), so its window cannot be shown here. Tell the user "
                            "so, and offer to look for another program that does the same: osyr box search <words>",
                            {"id": app["id"], "error": err})
            return fail("APP_FAILED", "It closed right after starting. Tell the user what the error says.",
                        {"id": app["id"], "exit": p.returncode, "error": err})
    except subprocess.TimeoutExpired:
        pass
    data = {"id": app["id"], "name": app["name"], "where": app["where"]}
    if workspace:
        data["workspace"] = workspace
    return Result(data=data, text=f"{app['name']} is open" + (f" on workspace {workspace}." if workspace else "."),
                  hint=(f"It is on workspace {workspace}: Super+{workspace} shows it, Super+{HOME_WORKSPACE} "
                        f"comes back to the assistant." if workspace else None))


def stop(args):
    app, several = find(args.app)
    if not app:
        return not_found(args.app, several)
    cons, left = windows(app), pids(app)
    if not cons and not left:
        return fail("APP_NOT_RUNNING", None, {"id": app["id"]}, text=f"{app['name']} is not open.")
    for con in cons:  # asked to close, as with the window's own button: it can still ask to save
        sway(f"[con_id={con}]", "kill")
    if cons:
        time.sleep(1.5)
        left = pids(app)
    if left and (args.force or not cons):
        for pid in left:  # (the box's programs run as the user: they are stopped like any other)
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        time.sleep(1)
        left = pids(app)
    if left or windows(app):
        return fail("APP_STILL_OPEN", "It may be asking whether to save: look at its window. "
                    f"To close it anyway: osyr app stop {app['id']} --force", {"id": app["id"]})
    return Result(data={"id": app["id"], "closed": True}, text=f"{app['name']} closed.")


def register(sub):
    p = sub.add_parser("app", help="open and close applications")
    a = p.add_subparsers(dest="cmd", required=True)
    a.add_parser("list", help="the applications there are, and which are open").set_defaults(func=listing)
    r = a.add_parser("run", help="open an application, optionally with a file or address")
    r.add_argument("app")
    r.add_argument("file", nargs="*")
    r.add_argument("--wait", type=float, default=3, help="seconds to see whether it starts (default 3)")
    r.set_defaults(func=run)
    s = a.add_parser("stop", help="close an application")
    s.add_argument("app")
    s.add_argument("--force", action="store_true", help="close it even if it does not answer")
    s.set_defaults(func=stop)
