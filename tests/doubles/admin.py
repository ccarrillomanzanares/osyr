#!/usr/bin/env python3
"""Administration doubles: stand-ins for systemctl, journalctl, df, du, sudo, the
process tools (ps, pgrep, pidof, top, uptime, kill, pkill, killall) and
timedatectl, so that the tasks of a system administrator (a service that
fails, a log to read, a disk that is full, a program that hogs the processor)
can be set up and checked with no real system.
A prototype: see docs/TRAINING-READINESS.md.

One program under several names, like hw.py and in the same state file
($OSYR_DOUBLES_STATE, from the scenario). The pretend system is the
scenario's "system" table:

  "system": {
    "password": "osyr",                      what sudo accepts
    "services": {
      "cups": {"description": "CUPS Scheduler", "state": "failed", "enabled": true,
               "log": ["Listening on port 631", "err: Unknown directive Lisen on line 12"],
               "needs": [{"file": "/etc/cups/cupsd.conf", "lacks": "Lisen"}],
               "error": ["err: Unable to read /etc/cups/cupsd.conf"]}
    },
    "journal": ["kernel: usb 1-2: new device"],  lines of no service
    "disks": [{"mount": "/home", "device": "/dev/sda3", "size": "20G", "other": "12G",
               "track": "/home/osyr"}],      used = other + the files under 'track'
    "processes": [{"pid": 3120, "name": "tracker-miner", "cmd": "/usr/bin/tracker-miner --index",
                   "user": "osyr", "cpu": 97.0, "mem": 3.1}],   programs of no service
    "clock": {"timezone": "Europe/Madrid", "ntp": true, "service": "chronyd"}
  }

The processes are a list made up on every call: the ones every OSYR laptop has
(BASE), one per active service (its "process", "user" and "command") and the
scenario's own. Nothing really runs. Ending a service's process stops the
service (a service with "restart": true comes back, as with Restart=always);
ending one of the scenario's removes it; ending one of the base is written down
in "killed". A number that is in none of the three is a real process of the
session and gets the real signal. The clock is in step when "ntp" is on and its
"service", if the scenario has it, is active.

A service's own program can be a double too: 'admin.py daemon NAME -t' checks
the service's "needs" as 'nginx -t' or 'sshd -t' would check a configuration.

A service starts only if all its "needs" hold: {"file": P, "contains": T},
{"file": P, "lacks": T} (the file's commented lines do not count), {"exists": P}, {"missing": P}, {"service": NAME}
(that one is active), {"free": MOUNT, "min": "500M"}, {"any": [need, ...]} (one of
them is enough: a fault with more than one repair). Otherwise it fails and
writes its "error" lines in its log. Changing a service needs root: sudo
checks the password, remembers it for five minutes and runs the command with
OSYR_DOUBLE_ROOT=1. In a session with a whole system the files under /etc are
closed to writing (OSYR_SYSTEM_LOCKED=1, see sandbox/inside.sh) and sudo opens
them while its command runs; the rest of the system's files are the session's
and can be written without it, which a real system would refuse.

Standard library only.
"""
import getpass
import os
import re
import signal
import subprocess
import sys
import time
import zlib

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from hw import Machine, die  # noqa: E402

ROOT = os.environ.get("OSYR_DOUBLE_ROOT") == "1"
HOST = "osyr"
UNITS = {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30, "T": 1 << 40}


def size(text):
    text = str(text).strip().upper().rstrip("B")
    unit = text[-1] if text and text[-1] in UNITS else ""
    return int(float(text[:len(text) - len(unit)] or 0) * UNITS[unit])


def human(n):
    for unit in ("", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if n >= 10 or unit == "" else f"{n:.1f}{unit}"
        n /= 1024


def system(s):
    sysm = s.setdefault("system", {})
    sysm.setdefault("services", {})
    return sysm


def unit(name):
    return name[:-len(".service")] if name.endswith(".service") else name


def tree_size(root):
    total = 0
    for base, _, files in os.walk(root):
        for f in files:
            try:
                total += os.lstat(os.path.join(base, f)).st_size
            except OSError:
                pass
    return total


def disk_used(d):
    return min(size(d["size"]), size(d.get("other", 0)) + (tree_size(d["track"]) if d.get("track") else 0)
               + size(d.get("used", 0)))


def holds(need, sysm):
    """Whether one condition of a service holds."""
    if "file" in need:
        try:
            with open(need["file"]) as f:
                # a commented line is not a setting: commenting a bad one out repairs the file
                text = "".join(line for line in f if not line.lstrip().startswith(("#", ";")))
        except OSError:
            return False
        return (need.get("contains") is None or need["contains"] in text) and \
               (need.get("lacks") is None or need["lacks"] not in text)
    if "exists" in need:
        return os.path.exists(need["exists"])
    if "missing" in need:
        return not os.path.exists(need["missing"])
    if "service" in need:
        return sysm["services"].get(need["service"], {}).get("state") == "active"
    if "free" in need:
        d = next((x for x in sysm.get("disks", []) if x["mount"] == need["free"]), None)
        return d is None or size(d["size"]) - disk_used(d) >= size(need.get("min", 0))
    if "any" in need:
        return any(holds(n, sysm) for n in need["any"])
    return True


def pid_of(name, svc):
    """A service's process number: the scenario's, or one that stays the same for the name."""
    return svc.get("pid") or 1000 + zlib.crc32(name.encode()) % 1900 + 17 * svc.get("restarts", 0)


def log(svc, *lines):
    svc.setdefault("log", []).extend(lines)


def start(name, svc, sysm):
    if all(holds(n, sysm) for n in svc.get("needs", [])):
        # (a copy of its program started by hand gives way: the pretend system is kind here)
        sysm["processes"] = [p for p in sysm.get("processes", [])
                             if not (p.get("by_hand") and p["name"] == svc.get("process", name))]
        if not sysm["processes"]:
            del sysm["processes"]
        svc["state"] = "active"
        svc["since"] = time.time()
        log(svc, f"Started {svc.get('description', name)}.")
        return True
    svc["state"] = "failed"
    log(svc, *svc.get("error", ["err: start request failed"]),
        f"err: {name}.service: Main process exited, code=exited, status=1/FAILURE",
        f"err: Failed to start {svc.get('description', name)}.")
    return False


# --- systemctl ----------------------------------------------------------------------

def stamp(i, total):
    """A plausible time for log line i of total: the last one a little before now."""
    return time.strftime("%b %d %H:%M:%S", time.localtime(time.time() - 7 * (total - i)))


def lines_of(name, svc):
    out = []
    for i, text in enumerate(svc.get("log", [])):
        err = text.startswith("err: ")
        out.append((err, f"{stamp(i, len(svc.get('log', [])))} {HOST} {svc.get('process', name)}[{pid_of(name, svc)}]: "
                         f"{text[5:] if err else text}"))
    return out


def status(name, svc):
    state = svc.get("state", "inactive")
    mark = {"active": "●", "failed": "×"}.get(state, "○")
    enabled = "enabled" if svc.get("enabled", True) else "disabled"
    sub = {"active": "active (running)", "failed": "failed (Result: exit-code)"}.get(state, "inactive (dead)")
    print(f"{mark} {name}.service - {svc.get('description', name)}")
    print(f"     Loaded: loaded (/usr/lib/systemd/system/{name}.service; {enabled}; preset: enabled)")
    print(f"     Active: {sub}")
    if state == "active":
        print(f"   Main PID: {pid_of(name, svc)} ({svc.get('process', name)})")
    print()
    for _, text in lines_of(name, svc)[-10:]:
        print(text)
    return {"active": 0}.get(state, 3)


def systemctl(argv):
    args = [a for a in argv if not a.startswith("-")]
    flags = [a for a in argv if a.startswith("-")]
    verb = args[0] if args else "list-units"
    names = [unit(a) for a in args[1:]]
    quiet = "-q" in flags or "--quiet" in flags
    with Machine() as s:
        sysm = system(s)
        services = sysm["services"]
        if verb == "list-units" or (not args and flags):
            failed = "--failed" in flags or "--state=failed" in flags
            print("  UNIT                         LOAD   ACTIVE   SUB     DESCRIPTION")
            n = 0
            for name, svc in sorted(services.items()):
                state = svc.get("state", "inactive")
                if failed and state != "failed" or (state == "inactive" and "--all" not in flags and not failed):
                    continue
                n += 1
                sub = {"active": "running", "failed": "failed"}.get(state, "dead")
                print(f"{'●' if state == 'failed' else ' '} {name + '.service':<28} loaded {state:<8} {sub:<7} "
                      f"{svc.get('description', name)}")
            print(f"\n{n} loaded units listed.")
            return 0
        if verb in ("daemon-reload", "daemon-reexec", "reset-failed"):
            return 0
        if verb in ("reboot", "poweroff", "suspend", "halt"):
            if not ROOT:
                die(f"Call to {verb.capitalize()} failed: Interactive authentication required.")
            sysm["asked_to"] = verb
            return 0
        if not names:
            die(f"systemctl: '{verb}' needs a unit name" if verb in (
                "status", "start", "stop", "restart", "reload", "enable", "disable", "is-active", "is-enabled",
                "is-failed", "show", "cat") else f"Unknown command verb '{verb}'.")
        rc = 0
        for name in names:
            svc = services.get(name)
            if svc is None:
                if verb.startswith("is-"):
                    print("inactive" if verb == "is-active" else "not-found")
                    rc = 4
                    continue
                print(f"Unit {name}.service could not be found.", file=sys.stderr)
                rc = 4 if verb == "status" else 5
                continue
            state = svc.get("state", "inactive")
            if verb == "status":
                rc = max(rc, status(name, svc))
            elif verb == "is-active":
                if not quiet:
                    print(state)
                rc = max(rc, 0 if state == "active" else 3)
            elif verb == "is-failed":
                if not quiet:
                    print(state)
                rc = max(rc, 0 if state == "failed" else 1)
            elif verb == "is-enabled":
                if not quiet:
                    print("enabled" if svc.get("enabled", True) else "disabled")
                rc = max(rc, 0 if svc.get("enabled", True) else 1)
            elif verb in ("show", "cat"):
                print(f"Id={name}.service\nDescription={svc.get('description', name)}\nActiveState={state}\n"
                      f"UnitFileState={'enabled' if svc.get('enabled', True) else 'disabled'}")
            elif verb in ("start", "stop", "restart", "reload", "try-restart", "reload-or-restart", "enable", "disable"):
                if not ROOT:
                    print(f"Failed to {verb} {name}.service: Interactive authentication required.\n"
                          f"See system logs and 'systemctl status {name}.service' for details.", file=sys.stderr)
                    rc = 1
                    continue
                if verb in ("enable", "disable"):
                    svc["enabled"] = verb == "enable"
                    if "--now" not in flags:
                        continue
                    verb2 = "start" if verb == "enable" else "stop"
                else:
                    verb2 = verb
                if verb2 == "stop":
                    if state == "active":
                        log(svc, f"Stopped {svc.get('description', name)}.")
                    svc["state"] = "inactive"
                elif verb2 == "start" and state == "active":
                    pass
                elif not start(name, svc, sysm):
                    print(f"Job for {name}.service failed because the control process exited with error code.\n"
                          f"See \"systemctl status {name}.service\" and \"journalctl -xeu {name}.service\" for details.",
                          file=sys.stderr)
                    rc = 1
            else:
                die(f"Unknown command verb '{verb}'.")
    return rc


# --- journalctl ---------------------------------------------------------------------

def journalctl(argv):
    units, count, errors, reverse, i = [], None, False, False, 0
    while i < len(argv):
        a = argv[i]
        if a in ("-u", "--unit") and i + 1 < len(argv):
            units.append(unit(argv[i + 1]))
            i += 1
        elif a.startswith("--unit="):
            units.append(unit(a.split("=", 1)[1]))
        elif a.startswith("-u") and len(a) > 2 and not a.startswith("--"):
            units.append(unit(a[2:]))
        elif a in ("-n", "--lines") and i + 1 < len(argv) and argv[i + 1].isdigit():
            count = int(argv[i + 1])
            i += 1
        elif a.startswith("-n") and a[2:].isdigit():
            count = int(a[2:])
        elif a.startswith("--lines="):
            count = int(a.split("=", 1)[1])
        elif a in ("-p", "--priority") and i + 1 < len(argv):
            errors = argv[i + 1] in ("err", "error", "3", "crit", "2", "alert", "1", "emerg", "0")
            i += 1
        elif a.startswith("-p") and len(a) > 2:
            errors = a[2:] in ("err", "error", "3", "crit", "2")
        elif a in ("-r", "--reverse"):
            reverse = True
        elif a in ("-f", "--follow"):
            die("journalctl: following the log is not possible here; use -n <lines>")
        elif a == "--disk-usage":
            # the journal is small here: a full disk is never its doing
            print("Archived and active journals take up 16.0M in the file system.")
            return 0
        elif a.startswith("--vacuum-") or a in ("--rotate", "--flush"):
            if not ROOT:
                die("journalctl: Failed to vacuum: Permission denied" if a.startswith("--vacuum")
                    else "journalctl: Failed to send rotate request: Permission denied")
            if a.startswith("--vacuum-"):
                print("Vacuuming done, freed 0B of archived journals from /var/log/journal.")
            return 0
        elif a.startswith("-") and not a.startswith("--") and "e" in a and "u" in a and i + 1 < len(argv):
            units.append(unit(argv[i + 1]))     # -xeu NAME
            i += 1
        i += 1
    with Machine() as s:
        sysm = system(s)
        out = []
        if units:
            for name in units:
                svc = sysm["services"].get(name)
                if svc is not None:
                    out += lines_of(name, svc)
        else:
            for name, svc in sysm["services"].items():
                out += lines_of(name, svc)
            extra = sysm.get("journal", [])
            out += [(t.startswith("err: "), f"{stamp(k, len(extra))} {HOST} {t[5:] if t.startswith('err: ') else t}")
                    for k, t in enumerate(extra)]
            out.sort(key=lambda x: x[1][:15])
    if errors:
        out = [x for x in out if x[0]]
    text = [t for _, t in out]
    if count is not None:
        text = text[-count:] if count else []
    if reverse:
        text.reverse()
    print("\n".join(text) if text else "-- No entries --")
    return 0


# --- df and du ----------------------------------------------------------------------

def df(argv):
    with Machine() as s:
        disks = system(s).get("disks")
    if not disks:
        os.execv("/usr/bin/df", ["df", *argv])
    flags = "".join(a.lstrip("-") for a in argv if a.startswith("-") and not a.startswith("--"))
    paths = [os.path.realpath(a) for a in argv if not a.startswith("-")]
    hum = "h" in flags or "H" in flags or "--human-readable" in argv
    rows = sorted(disks, key=lambda d: d["mount"])
    if paths:
        rows = []
        for p in paths:
            best = max((d for d in disks if p == d["mount"] or p.startswith(d["mount"].rstrip("/") + "/")),
                       key=lambda d: len(d["mount"]), default=None)
            if best and best not in rows:
                rows.append(best)
    show = (lambda n: human(n)) if hum else (lambda n: str(n // 1024))
    print(f"{'Filesystem':<15} {'Size' if hum else '1K-blocks':>9} {'Used':>9} {'Avail' if hum else 'Available':>9} Use% Mounted on")
    for d in rows:
        total, used = size(d["size"]), disk_used(d)
        print(f"{d.get('device', '/dev/sda2'):<15} {show(total):>9} {show(used):>9} {show(total - used):>9} "
              f"{round(100 * used / total) if total else 0:>3}% {d['mount']}")
    return 0


def du(argv):
    # The big files of a task are holes: their size counts, not the memory they take
    os.execv("/usr/bin/du", ["du", "--apparent-size", *argv])


# --- sudo ---------------------------------------------------------------------------

def sudo(argv):
    opts, i = [], 0
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] == "--":
            i += 1
            break
        opts.append(argv[i])
        if argv[i] in ("-u", "-g", "-p", "-C", "-h") and i + 1 < len(argv):
            i += 1
        i += 1
    command = argv[i:]
    flat = "".join(o.lstrip("-") for o in opts if not o.startswith("--"))
    with Machine() as s:
        sysm = system(s)
        if "k" in flat or "K" in flat:
            sysm["sudo_until"] = 0
            if not command:
                return 0
        fresh = time.time() < sysm.get("sudo_until", 0)
        password = sysm.get("password", "osyr")
    if not fresh:
        if "n" in flat:
            die("sudo: a password is required")
        for attempt in range(3):
            try:
                typed = getpass.getpass("[sudo] password for osyr: ")
            except (EOFError, KeyboardInterrupt, OSError):
                die("\nsudo: a password is required")
            if typed == password:
                break
            print("Sorry, try again.", file=sys.stderr)
        else:
            die("sudo: 3 incorrect password attempts")
        with Machine() as s:
            system(s)["sudo_until"] = time.time() + 300
    if not command:
        return 0
    os.environ["OSYR_DOUBLE_ROOT"] = "1"
    # In a session with a whole system (--system) the settings in /etc are closed to writing,
    # as they are to a normal user; they open while a command runs as root
    locked = os.environ.get("OSYR_SYSTEM_LOCKED") == "1"
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if locked:
        subprocess.call(["chmod", "-R", "u+w", "/etc"], **quiet)
    try:
        return subprocess.call(command)
    except OSError:
        die(f"sudo: {command[0]}: command not found")
    finally:
        if locked:
            subprocess.call(["chmod", "-R", "a-w", "/etc"], **quiet)


# --- processes ----------------------------------------------------------------------

# What runs on every OSYR laptop: (pid, parent, user, name, command line)
BASE = [
    (1, 0, "root", "systemd", "/usr/lib/systemd/systemd"),
    (2, 0, "root", "kthreadd", "[kthreadd]"),
    (14, 2, "root", "ksoftirqd/0", "[ksoftirqd/0]"),
    (61, 2, "root", "kswapd0", "[kswapd0]"),
    (187, 1, "root", "systemd-journal", "/usr/lib/systemd/systemd-journald"),
    (214, 1, "root", "systemd-udevd", "/usr/lib/systemd/systemd-udevd"),
    (398, 1, "dbus", "dbus-daemon", "/usr/bin/dbus-daemon --system --address=systemd: --nofork --nopidfile"),
    (412, 1, "root", "iwd", "/usr/libexec/iwd"),
    (431, 1, "root", "systemd-logind", "/usr/lib/systemd/systemd-logind"),
    (440, 1, "root", "bluetoothd", "/usr/libexec/bluetooth/bluetoothd"),
    (596, 1, "root", "login", "login -- osyr"),
    (602, 1, "osyr", "systemd", "/usr/lib/systemd/systemd --user"),
    (617, 602, "osyr", "dbus-daemon", "/usr/bin/dbus-daemon --session --address=systemd: --nofork --nopidfile"),
    (618, 602, "osyr", "pipewire", "/usr/bin/pipewire"),
    (619, 602, "osyr", "wireplumber", "/usr/bin/wireplumber"),
    (640, 596, "osyr", "sway", "sway"),
    (671, 640, "osyr", "foot", "foot osyr-chat"),
    (674, 671, "osyr", "osyr-chat", "python3 /usr/bin/osyr-chat"),
]
SHELL_PID = 680     # the terminal the listing is asked from
SIGNALS = {"HUP": 1, "INT": 2, "QUIT": 3, "KILL": 9, "USR1": 10, "USR2": 12, "TERM": 15, "CONT": 18, "STOP": 19}


def extra_pid(p, i):
    return int(p.get("pid") or 3000 + 7 * i)


def table(sysm):
    """Every pretend process, as dictionaries, in the order of their numbers."""
    gone = set(sysm.get("killed", []))
    rows = [{"pid": pid, "ppid": parent, "user": user, "name": name, "cmd": cmd, "cpu": 0.0, "mem": 0.1,
             "kind": "base"} for pid, parent, user, name, cmd in BASE if pid not in gone]
    for name, svc in sysm["services"].items():
        if svc.get("state") == "active":
            proc = svc.get("process", name)
            rows.append({"pid": pid_of(name, svc), "ppid": 1, "user": svc.get("user", "root"), "name": proc,
                         "cmd": svc.get("command", f"/usr/sbin/{proc}"), "cpu": float(svc.get("cpu", 0.0)),
                         "mem": float(svc.get("mem", 0.4)), "kind": "service", "service": name})
    for i, p in enumerate(sysm.get("processes", [])):
        rows.append({"pid": extra_pid(p, i), "ppid": int(p.get("ppid", 671 if p.get("user", "osyr") == "osyr" else 1)),
                     "user": p.get("user", "osyr"), "name": p["name"], "cmd": p.get("cmd", p["name"]),
                     "cpu": float(p.get("cpu", 0.0)), "mem": float(p.get("mem", 0.2)), "kind": "extra", "index": i})
    for r in rows:
        if r["name"] == "sway":
            r["mem"] = 0.9
        elif r["name"] == "osyr-chat":
            r["mem"] = 0.5
        elif r["cmd"].startswith("["):
            r["mem"] = 0.0       # the kernel's own
    return sorted(rows, key=lambda r: r["pid"])


MEM_KB = 16 * 1024 * 1024


def minutes_up(sysm):
    return int(sysm.get("up_minutes", 187))


def cpu_time(r, sysm):
    """Processor time used so far: a hog has been at it for a while."""
    sec = int(r["cpu"] / 100 * 60 * min(minutes_up(sysm), 42)) + (r["pid"] % 3)
    return sec


def clock(sec, long=False):
    if long:
        return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
    return f"{sec // 60}:{sec % 60:02d}"


def short_user(r):
    return r["user"] if len(r["user"]) <= 8 else r["user"][:7] + "+"


COLUMNS = {
    "pid": ("PID", lambda r, s: str(r["pid"]), ">", 7), "ppid": ("PPID", lambda r, s: str(r["ppid"]), ">", 7),
    "user": ("USER", lambda r, s: short_user(r), "<", 8), "uid": ("UID", lambda r, s: short_user(r), "<", 8),
    "comm": ("COMMAND", lambda r, s: r["name"][:15], "<", 15), "cmd": ("CMD", lambda r, s: r["cmd"], "<", 0),
    "%cpu": ("%CPU", lambda r, s: f"{r['cpu']:.1f}", ">", 4), "%mem": ("%MEM", lambda r, s: f"{r['mem']:.1f}", ">", 4),
    "rss": ("RSS", lambda r, s: str(int(r["mem"] / 100 * MEM_KB)), ">", 7),
    "vsz": ("VSZ", lambda r, s: str(int(r["mem"] / 100 * MEM_KB * 3) + (4096 if r["mem"] else 0)), ">", 8),
    "stat": ("STAT", lambda r, s: ("R" if r["cpu"] >= 50 else "S") + ("s" if r["ppid"] == 1 else ""), "<", 4),
    "tty": ("TT", lambda r, s: "pts/0" if r["pid"] >= SHELL_PID and r["kind"] != "service" and r["user"] == "osyr" else "?", "<", 8),
    "time": ("TIME", lambda r, s: clock(cpu_time(r, s), True), ">", 8),
    "etime": ("ELAPSED", lambda r, s: clock(minutes_up(s) * 60 - r["pid"] % 50, True), ">", 11),
    "start": ("START", lambda r, s: time.strftime("%H:%M", time.localtime(time.time() - minutes_up(s) * 60 + r["pid"] // 20)), "<", 5),
    "ni": ("NI", lambda r, s: "0", ">", 3), "pri": ("PRI", lambda r, s: "20", ">", 3),
}
ALIASES = {"args": "cmd", "command": "cmd", "pcpu": "%cpu", "c": "%cpu", "pmem": "%mem", "ucmd": "comm", "ucomm": "comm",
           "euser": "user", "uname": "user", "ruser": "user", "s": "stat", "state": "stat", "tname": "tty", "tt": "tty",
           "cputime": "time", "stime": "start", "lstart": "start", "start_time": "start", "bsdstart": "start",
           "rssize": "rss", "rsz": "rss", "vsize": "vsz", "nice": "ni", "priority": "pri", "tgid": "pid", "exe": "cmd"}


def shell_rows(sysm, cmd):
    """The terminal and the listing itself, as a real listing would show them."""
    return [{"pid": SHELL_PID, "ppid": 671, "user": "osyr", "name": "bash", "cmd": "-bash", "cpu": 0.0, "mem": 0.0, "kind": "self"},
            {"pid": 4000 + os.getpid() % 900, "ppid": SHELL_PID, "user": "osyr", "name": cmd.split()[0], "cmd": cmd,
             "cpu": 0.0, "mem": 0.0, "kind": "self"}]


def ps(argv):
    with Machine() as s:
        sysm = system(s)
        rows = table(sysm)
    every, style, fmt, header, sort, i = False, "short", None, True, None, 0
    pids, names, users = None, None, None

    def more(value):
        return [x for x in re.split(r"[,\s]+", value) if x]
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if a.startswith("--"):
            key, _, val = a[2:].partition("=")
            if key in ("no-headers", "no-heading", "noheader", "noheaders"):
                header = False
            elif key in ("sort", "pid", "ppid", "user", "User", "format"):
                if not val and nxt is not None:
                    val, i = nxt, i + 1
                if key == "sort":
                    sort = val
                elif key == "pid":
                    pids = (pids or []) + more(val)
                elif key in ("user", "User"):
                    users = (users or []) + more(val)
                elif key == "format":
                    fmt = (fmt or []) + more(val)
            elif key == "forest":
                pass
            elif key in ("help", "version"):
                print("ps from procps-ng 4.0.4")
                return 0
        elif a.startswith("-") and len(a) > 1:
            letters, j = a[1:], 0
            while j < len(letters):
                ch = letters[j]
                rest = letters[j + 1:]
                if ch in "opCuUGgt" and ch != "g":
                    val = rest
                    if not val and nxt is not None:
                        val, i = nxt, i + 1
                    if ch == "o":
                        fmt = (fmt or []) + more(val)
                    elif ch == "p":
                        pids = (pids or []) + more(val)
                    elif ch == "C":
                        names = (names or []) + more(val)
                    elif ch in "uU":
                        users = (users or []) + more(val)
                    break
                if ch in "eA":
                    every = True
                elif ch == "f":
                    style = "full"
                elif ch in "lF":
                    style = "full"
                j += 1
        elif a.isdigit() or re.fullmatch(r"\d+(,\d+)*", a):
            pids = (pids or []) + more(a)
        else:       # BSD letters: aux, axo ..., ef
            j = 0
            while j < len(a):
                ch = a[j]
                if ch in "ax":
                    every = True
                elif ch == "u":
                    style = "user"
                elif ch == "e" and "f" in a and "a" not in a:
                    every = True
                elif ch == "f" and "e" in a and "a" not in a:
                    style = "full"
                elif ch == "o":
                    val = a[j + 1:]
                    if not val and nxt is not None:
                        val, i = nxt, i + 1
                    fmt = (fmt or []) + more(val)
                    break
                elif ch == "h":
                    header = False
                elif ch in "kO" and (a[j + 1:] or nxt):
                    sort = a[j + 1:] or nxt
                    if not a[j + 1:]:
                        i += 1
                    break
                j += 1
        i += 1
    me = shell_rows(sysm, "ps " + " ".join(argv) if argv else "ps")
    if pids is not None or names is not None or users is not None:
        rows = [r for r in rows + me if (pids is not None and str(r["pid"]) in pids)
                or (names is not None and r["name"] in names) or (users is not None and r["user"] in users)]
    elif every:
        rows = rows + me
    else:
        rows = me
    if sort:
        for key in reversed(more(sort)):
            desc = key.startswith("-")
            key = ALIASES.get(key.lstrip("+-").lower(), key.lstrip("+-").lower())
            if key in ("%cpu", "%mem", "pid", "rss", "vsz", "ppid"):
                field = {"%cpu": "cpu", "%mem": "mem", "rss": "mem", "vsz": "mem"}.get(key, key)
                rows.sort(key=lambda r: r[field], reverse=desc)
            elif key in ("comm", "cmd", "user"):
                rows.sort(key=lambda r: r[{"comm": "name"}.get(key, key)], reverse=desc)
    if fmt is None:
        fmt = {"short": ["pid", "tty", "time", "cmd"], "user": ["user", "pid", "%cpu", "%mem", "vsz", "rss", "tty", "stat",
                                                               "start", "time", "cmd"],
               "full": ["uid", "pid", "ppid", "c", "stime", "tty", "time", "cmd"]}[style]
        titles = {"short": {"tty": "TTY", "time": "TIME"}, "user": {"tty": "TTY", "cmd": "COMMAND"},
                  "full": {"c": "C", "stime": "STIME", "tty": "TTY"}}[style]
        short_time = style == "user"
    else:
        titles, short_time = {}, False
    cols = []
    for f in fmt:
        key, _, title = f.partition("=")
        base = ALIASES.get(key.lower(), key.lower())
        if base not in COLUMNS:
            die(f"error: unknown user-defined format specifier \"{key}\"")
        t, fn, align, width = COLUMNS[base]
        if "=" in f:
            t = title
        else:
            t = titles.get(key.lower(), t if base != "cmd" or key.lower() == "cmd" else "COMMAND")
        if base == "time" and short_time:
            fn = lambda r, s: clock(cpu_time(r, s))      # noqa: E731
            width = 6
        if key.lower() == "c":
            fn = lambda r, s: str(int(r["cpu"]))          # noqa: E731
            width = 2
        cols.append((t, fn, align, width))
    if style == "short" and fmt == ["pid", "tty", "time", "cmd"]:
        for r in rows:
            r["cmd"] = r["name"] if r["kind"] != "self" else r["cmd"].split()[0].lstrip("-")
    if header and any(t for t, *_ in cols):
        print(" ".join(f"{t:{al}{w}}" if w and k < len(cols) - 1 else t for k, (t, _, al, w) in enumerate(cols)).rstrip())
    for r in rows:
        print(" ".join(f"{fn(r, sysm):{al}{w}}" if w and k < len(cols) - 1 else fn(r, sysm)
                       for k, (_, fn, al, w) in enumerate(cols)).rstrip())
    return 0 if rows else 1


def pgrep(argv, kill_with=None):
    """pgrep; with kill_with (a signal), pkill."""
    full = exact = names = lines = count = newest = oldest = invert = False
    users, pattern, sig, i = None, None, kill_with, 0
    while i < len(argv):
        a = argv[i]
        if a in ("-u", "-U", "--euid", "--uid") and i + 1 < len(argv):
            users = argv[i + 1].split(",")
            i += 1
        elif a in ("--signal",) and i + 1 < len(argv):
            sig = signal_number(argv[i + 1])
            i += 1
        elif a.startswith("--"):
            full |= a == "--full"
            exact |= a == "--exact"
            names |= a == "--list-name"
            lines |= a == "--list-full"
            count |= a == "--count"
            newest |= a == "--newest"
            oldest |= a == "--oldest"
            invert |= a == "--inverse"
        elif a.startswith("-") and len(a) > 1:
            if kill_with is not None and signal_number(a[1:]) is not None:
                sig = signal_number(a[1:])
            else:
                full |= "f" in a
                exact |= "x" in a
                names |= "l" in a
                lines |= "a" in a
                count |= "c" in a
                newest |= "n" in a
                oldest |= "o" in a
                invert |= "v" in a
        elif pattern is None:
            pattern = a
        i += 1
    if pattern is None and users is None:
        die(f"{'pkill' if kill_with is not None else 'pgrep'}: no matching criteria specified\n"
            f"Try `{'pkill' if kill_with is not None else 'pgrep'} --help' for more information.", 2)
    try:
        rx = re.compile(pattern or "")
    except re.error:
        die("pgrep: bad regular expression", 2)
    with Machine() as s:
        sysm = system(s)
        found = []
        for r in table(sysm):
            text = r["cmd"] if full else r["name"][:15]
            hit = (rx.fullmatch(text) if exact else rx.search(text)) is not None
            if users is not None and r["user"] not in users:
                hit = False
            if hit != invert:
                found.append(r)
        if newest:
            found = found[-1:]
        if oldest:
            found = found[:1]
        if kill_with is not None:
            rc = 0 if found else 1
            for r in found:
                rc = max(rc, end(r, sig, sysm, "pkill"))
            return rc
    if count:
        print(len(found))
    else:
        for r in found:
            print(f"{r['pid']} {r['cmd']}" if lines else f"{r['pid']} {r['name']}" if names else r["pid"])
    return 0 if found else 1


def pidof(argv):
    names = [a for a in argv if not a.startswith("-")]
    with Machine() as s:
        rows = table(system(s))
    found = [str(r["pid"]) for r in reversed(rows) if r["name"] in names or os.path.basename(r["cmd"].split()[0]) in names]
    if "-s" in argv:
        found = found[:1]
    if found:
        print(" ".join(found))
    return 0 if found else 1


def load(sysm):
    return sum(r["cpu"] for r in table(sysm)) / 100


def uptime(argv):
    with Machine() as s:
        sysm = system(s)
        up, ld = minutes_up(sysm), load(sysm)
    if "-p" in argv or "--pretty" in argv:
        print(f"up {up // 60} hours, {up % 60} minutes")
    else:
        print(f" {time.strftime('%H:%M:%S')} up {up // 60:2d}:{up % 60:02d},  1 user,  "
              f"load average: {ld + 0.08:.2f}, {ld * 0.9 + 0.11:.2f}, {ld * 0.6 + 0.09:.2f}")
    return 0


def top(argv):
    """One screen of top and out, whatever was asked: there is no keyboard to leave it with."""
    count, by_mem, i = None, False, 0
    while i < len(argv):
        a = argv[i]
        if a == "-o" and i + 1 < len(argv):
            by_mem = "MEM" in argv[i + 1].upper() or "RES" in argv[i + 1].upper()
            i += 1
        elif a.startswith("-o"):
            by_mem = "MEM" in a.upper() or "RES" in a.upper()
        i += 1
    with Machine() as s:
        sysm = system(s)
        rows = table(sysm) + shell_rows(sysm, "top")
        up, ld = minutes_up(sysm), load(sysm)
    rows.sort(key=lambda r: (-(r["mem"] if by_mem else r["cpu"]), r["pid"]))
    busy = min(100.0, sum(r["cpu"] for r in rows) / 4)       # four processors
    used = sum(r["mem"] for r in rows) / 100 * MEM_KB / 1024 + 900
    total = MEM_KB / 1024
    running = sum(1 for r in rows if r["cpu"] >= 50) or 1
    print(f"top - {time.strftime('%H:%M:%S')} up {up // 60:2d}:{up % 60:02d},  1 user,  "
          f"load average: {ld + 0.08:.2f}, {ld * 0.9 + 0.11:.2f}, {ld * 0.6 + 0.09:.2f}")
    print(f"Tasks: {len(rows):3d} total, {running:3d} running, {len(rows) - running:3d} sleeping,   0 stopped,   0 zombie")
    print(f"%Cpu(s): {busy * 0.93:4.1f} us, {busy * 0.07:4.1f} sy,  0.0 ni, {100 - busy:4.1f} id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st")
    print(f"MiB Mem : {total:8.1f} total, {total - used - 2100:8.1f} free, {used:8.1f} used, {2100.0:8.1f} buff/cache")
    print(f"MiB Swap: {4096.0:8.1f} total, {4096.0:8.1f} free, {0.0:8.1f} used. {total - used:8.1f} avail Mem")
    print()
    print("    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND")
    for r in rows:
        res = int(r["mem"] / 100 * MEM_KB)
        sec = cpu_time(r, sysm)
        print(f"{r['pid']:7d} {short_user(r):<8.8s}  20   0 {res * 3 + (4096 if res else 0):7d} {res:6d} {res // 3:6d} "
              f"{'R' if r['cpu'] >= 50 else 'S'} {r['cpu']:5.1f} {r['mem']:5.1f} {sec // 60:4d}:{sec % 60:02d}.{r['pid'] % 100:02d} {r['name'][:15]}")
    return 0


def signal_number(text):
    text = str(text).upper()
    if text.isdigit():
        return int(text)
    return SIGNALS.get(text[3:] if text.startswith("SIG") else text)


def end(r, sig, sysm, prog):
    """Sends one signal to one pretend process; what a real one would do with it."""
    if r["user"] != "osyr" and not ROOT:
        print(f"{prog}: ({r['pid']}): Operation not permitted" if prog == "kill" else
              f"{prog}: killing pid {r['pid']} failed: Operation not permitted", file=sys.stderr)
        return 1
    if sig in (0, 10, 12, 18, 19):
        return 0
    if r["kind"] == "service":
        name = r["service"]
        svc = sysm["services"][name]
        if sig == 1:
            log(svc, f"Reloaded {svc.get('description', name)}.")
            return 0
        if sig == 9:
            log(svc, f"err: {name}.service: Main process exited, code=killed, status=9/KILL",
                f"err: {name}.service: Failed with result 'signal'.")
            svc["state"] = "failed"
        else:
            log(svc, f"{name}.service: Deactivated successfully.")
            svc["state"] = "inactive"
        if svc.get("restart"):
            svc["restarts"] = svc.get("restarts", 0) + 1
            log(svc, f"{name}.service: Scheduled restart job, restart counter is at {svc['restarts']}.")
            start(name, svc, sysm)
    elif r["kind"] == "extra":
        procs = sysm.get("processes", [])
        victim = next((p for i, p in enumerate(procs) if extra_pid(p, i) == r["pid"]), None)
        if victim is not None:
            if victim.get("ignores_term") and sig != 9:
                return 0          # a hung program: only -9 ends it
            for i, p in enumerate(procs):       # the others keep their numbers
                p["pid"] = extra_pid(p, i)
            procs.remove(victim)
            sysm.setdefault("ended", []).append(victim["name"])
    elif r["pid"] != 1:
        sysm.setdefault("killed", []).append(r["pid"])
    return 0


def parent_of(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def real_job(pid, near=False):
    """Whether the number is a real process that may be signalled: one started by the shell that
    asks (near), or any of this terminal session that is not what the asking shell hangs from
    (the chat, the session itself). The real numbers of a session are small and can be the
    same as a pretend one: only a job of the asking shell wins over the pretend process."""
    if pid <= 1 or parent_of(pid) is None:
        return False
    if parent_of(pid) == os.getppid():
        return True
    if near:
        return False
    above, p = set(), os.getpid()
    while p and p not in above:
        above.add(p)
        p = parent_of(p)
    try:
        return pid not in above and os.getsid(pid) == os.getsid(0)
    except OSError:
        return False


def kill(argv):
    if argv and argv[0] in ("-l", "-L", "--list", "--table"):
        print(" ".join(f"{n}) SIG{name}" for name, n in sorted(SIGNALS.items(), key=lambda x: x[1])))
        return 0
    sig, targets, i = 15, [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("-s", "--signal", "-n") and i + 1 < len(argv):
            sig = signal_number(argv[i + 1])
            i += 1
        elif a == "--":
            targets += argv[i + 1:]
            break
        elif a.startswith("-") and not targets and signal_number(a[1:]) is not None and len(argv) > i + 1:
            sig = signal_number(a[1:])
        else:
            targets.append(a)
        i += 1
    if sig is None:
        die("kill: unknown signal", 1)
    if not targets:
        die("kill: usage: kill [-s sigspec | -n signum | -sigspec] pid | jobspec ... or kill -l [sigspec]", 2)
    rc = 0
    with Machine() as s:
        sysm = system(s)
        for t in targets:
            if not re.fullmatch(r"-?\d+", t):
                print(f"kill: failed to parse argument: '{t}'", file=sys.stderr)
                rc = 1
                continue
            r = next((x for x in table(sysm) if x["pid"] == int(t)), None)
            if r is not None and not real_job(int(t), near=True):
                rc = max(rc, end(r, sig, sysm, "kill"))
                continue
            try:            # a real process of the session: a job a shell of the agent started
                if not real_job(int(t)):
                    raise ProcessLookupError
                os.kill(int(t), sig)
            except (ProcessLookupError, PermissionError, OverflowError):
                print(f"kill: ({t}): No such process", file=sys.stderr)
                rc = 1
    return rc


def pkill(argv):
    return pgrep(argv, kill_with=15)


def killall(argv):
    sig, names = 15, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-s", "--signal") and i + 1 < len(argv):
            sig = signal_number(argv[i + 1])
            i += 1
        elif a.startswith("-") and signal_number(a[1:]) is not None:
            sig = signal_number(a[1:])
        elif not a.startswith("-"):
            names.append(a)
        i += 1
    if not names:
        die("Usage: killall [-SIGNAL] name...", 1)
    rc = 0
    with Machine() as s:
        sysm = system(s)
        for name in names:
            found = [r for r in table(sysm) if r["name"] == name]
            if not found:
                print(f"{name}: no process found", file=sys.stderr)
                rc = 1
            for r in found:
                rc = max(rc, end(r, sig, sysm, "killall"))
    return rc


# --- the clock ----------------------------------------------------------------------

ZONES = "/usr/share/zoneinfo"


def clock_of(sysm):
    c = sysm.setdefault("clock", {})
    c.setdefault("timezone", "Europe/Madrid")
    c.setdefault("ntp", True)
    if "service" not in c:
        c["service"] = next((n for n in ("systemd-timesyncd", "chronyd", "chrony", "ntpd", "ntp") if n in sysm["services"]),
                            "systemd-timesyncd")
    return c


def ntp_active(c, sysm):
    svc = sysm["services"].get(c["service"])
    return bool(c["ntp"]) and (svc is None or svc.get("state") == "active")


def timedatectl(argv):
    args = [a for a in argv if not a.startswith("-")]
    verb = args[0] if args else "status"
    with Machine() as s:
        sysm = system(s)
        c = clock_of(sysm)
        active = ntp_active(c, sysm)
        synced = active and not c.get("unsynchronized")
        now = time.time() + c.get("offset", 0)
        os.environ["TZ"] = c["timezone"]
        time.tzset()
        local = time.localtime(now)
        if verb == "status":
            print(f"               Local time: {time.strftime('%a %Y-%m-%d %H:%M:%S %Z', local)}")
            print(f"           Universal time: {time.strftime('%a %Y-%m-%d %H:%M:%S UTC', time.gmtime(now))}")
            print(f"                 RTC time: {time.strftime('%a %Y-%m-%d %H:%M:%S', time.gmtime(now))}")
            print(f"                Time zone: {c['timezone']} ({time.strftime('%Z, %z', local)})")
            print(f"System clock synchronized: {'yes' if synced else 'no'}")
            print(f"              NTP service: {'active' if active else 'inactive'}")
            print("          RTC in local TZ: no")
            return 0
        if verb == "show":
            props = {"Timezone": c["timezone"], "LocalRTC": "no", "CanNTP": "yes", "NTP": "yes" if c["ntp"] else "no",
                     "NTPSynchronized": "yes" if synced else "no",
                     "TimeUSec": time.strftime("%a %Y-%m-%d %H:%M:%S %Z", local),
                     "RTCTimeUSec": time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(now))}
            want, value, i = [], "--value" in argv, 0
            while i < len(argv):
                if argv[i] in ("-p", "--property") and i + 1 < len(argv):
                    want += argv[i + 1].split(",")
                    i += 1
                elif argv[i].startswith("--property="):
                    want += argv[i].split("=", 1)[1].split(",")
                elif argv[i].startswith("-p") and len(argv[i]) > 2:
                    want += argv[i][2:].split(",")
                i += 1
            for k, v in props.items():
                if not want or k in want:
                    print(v if value else f"{k}={v}")
            return 0
        if verb == "timesync-status":
            print(f"       Server: {'162.159.200.1 (time.cloudflare.com)' if synced else 'n/a'}")
            print("Poll interval: 34min 8s (min: 32s; max 34min 8s)")
            print(f"  Packet count: {7 if synced else 0}")
            return 0
        if verb == "list-timezones":
            zones = []
            for base, _, files in os.walk(ZONES):
                for f in files:
                    rel = os.path.relpath(os.path.join(base, f), ZONES)
                    if rel[0].isupper() and "/" in rel and not rel.startswith(("posix", "right", "Etc")):
                        zones.append(rel)
            print("\n".join(sorted(zones) or ["Europe/Berlin", "Europe/London", "Europe/Madrid", "UTC"]))
            return 0
        if verb in ("set-ntp", "set-timezone", "set-time", "set-local-rtc"):
            if len(args) < 2:
                die("Too few arguments.")
            if not ROOT:
                die(f"Failed to {verb.replace('set-', 'set ').replace('ntp', 'NTP')}: Interactive authentication required.")
            value = args[1]
            if verb == "set-ntp":
                if value.lower() not in ("true", "false", "yes", "no", "1", "0", "on", "off"):
                    die(f"Failed to parse NTP setting: {value}")
                on = value.lower() in ("true", "yes", "1", "on")
                c["ntp"] = on
                svc = sysm["services"].get(c["service"])
                if svc is not None:
                    svc["enabled"] = on
                    if not on:
                        svc["state"] = "inactive"
                    elif svc.get("state") != "active" and not start(c["service"], svc, sysm):
                        die(f"Failed to set ntp: Unit {c['service']}.service failed to start.")
            elif verb == "set-timezone":
                if os.path.isdir(ZONES):
                    ok = os.path.isfile(os.path.join(ZONES, value)) and ".." not in value
                else:
                    ok = re.fullmatch(r"UTC|[A-Z][A-Za-z_]+(/[A-Za-z_+-]+){1,2}", value) is not None
                if not ok:
                    die(f"Failed to set time zone: Invalid or not installed time zone '{value}'")
                c["timezone"] = value
                try:        # so that 'date' agrees (a whole system: /etc is open while sudo runs)
                    if os.path.isfile(os.path.join(ZONES, value)):
                        tmp = "/etc/localtime.new"
                        os.symlink(os.path.join(ZONES, value), tmp)
                        os.replace(tmp, "/etc/localtime")
                except OSError:
                    pass
            elif verb == "set-time":
                if c["ntp"]:
                    die("Failed to set time: Automatic time synchronization is enabled")
                text = " ".join(args[1:])
                for form in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%H:%M:%S", "%H:%M"):
                    try:
                        t = time.strptime(text, form)
                    except ValueError:
                        continue
                    if form.startswith("%H"):
                        t = time.struct_time(local[:3] + t[3:6] + local[6:])
                    c["offset"] = round(time.mktime(t) - time.time())
                    break
                else:
                    die(f"Failed to parse time specification '{text}': Invalid argument")
            return 0
        die(f"Unknown command verb '{verb}'.")


# --- a service's own program ----------------------------------------------------------

def daemon(argv):
    """'admin.py daemon NAME [-t ...]': the service's program asked to check its configuration
    (-t, -T, --test, configtest), or started by hand: then, if the configuration is right, one
    more pretend process of that name is left running, outside its service (which stays as it
    was, as it would: nobody told systemd)."""
    if not argv:
        die("admin.py daemon: which service?", 2)
    name = unit(argv[0])
    with Machine() as s:
        sysm = system(s)
        svc = sysm["services"].get(name) or next(
            (v for v in sysm["services"].values() if v.get("process") == name), None)
        if svc is None:
            die(f"admin.py daemon: no service '{name}' in the scenario", 2)
        ok = all(holds(n, sysm) for n in svc.get("needs", []))
        proc = svc.get("process", name)
        test = any(a in ("-t", "-T", "-tt", "--test", "configtest", "-Q") for a in argv[1:])
        if not ok:
            for line in svc.get("error", ["err: configuration error"]):
                print(f"{proc}: {line[5:] if line.startswith('err: ') else line}", file=sys.stderr)
            return 1
        if test:
            print(f"{proc}: the configuration file syntax is ok", file=sys.stderr)
            return 0
        if any(r["name"] == proc for r in table(sysm)):
            die(f"{proc}: could not start: Address already in use (is {proc} already running?)")
        if not ROOT:
            die(f"{proc}: could not start: Permission denied (are you root?)")
        procs = sysm.setdefault("processes", [])
        for i, p in enumerate(procs):
            p["pid"] = extra_pid(p, i)
        procs.append({"pid": 5000 + 13 * len(procs), "name": proc, "cmd": " ".join([f"/usr/sbin/{proc}", *argv[1:]]),
                      "user": "root", "ppid": 1, "by_hand": True})
    return 0


DOUBLES = {"systemctl": systemctl, "journalctl": journalctl, "df": df, "du": du, "sudo": sudo,
           "ps": ps, "pgrep": pgrep, "pidof": pidof, "top": top, "uptime": uptime, "kill": kill, "pkill": pkill,
           "killall": killall, "timedatectl": timedatectl, "daemon": daemon}


def main():
    name, argv = os.path.basename(sys.argv[0]), sys.argv[1:]
    if name not in DOUBLES and argv and argv[0] in DOUBLES:
        name, argv = argv[0], argv[1:]
    if name not in DOUBLES:
        die(f"admin.py: called as '{name}'; it stands in for: {', '.join(DOUBLES)}", 2)
    try:
        return DOUBLES[name](argv) or 0
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
