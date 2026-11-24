#!/usr/bin/env python3
"""Administration doubles: stand-ins for systemctl, journalctl, df, du and sudo,
so that the tasks of a system administrator (a service that fails, a log to
read, a disk that is full) can be set up and checked with no real system.
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
               "track": "/home/osyr"}]       used = other + the files under 'track'
  }

A service starts only if all its "needs" hold: {"file": P, "contains": T},
{"file": P, "lacks": T}, {"exists": P}, {"missing": P}, {"service": NAME}
(that one is active), {"free": MOUNT, "min": "500M"}. Otherwise it fails and
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
import subprocess
import sys
import time

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
                text = f.read()
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
    return True


def log(svc, *lines):
    svc.setdefault("log", []).extend(lines)


def start(name, svc, sysm):
    if all(holds(n, sysm) for n in svc.get("needs", [])):
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
        out.append((err, f"{stamp(i, len(svc.get('log', [])))} {HOST} {svc.get('process', name)}[{svc.get('pid', 800 + len(name))}]: "
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
        print(f"   Main PID: {svc.get('pid', 800 + len(name))} ({svc.get('process', name)})")
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


DOUBLES = {"systemctl": systemctl, "journalctl": journalctl, "df": df, "du": du, "sudo": sudo}


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
