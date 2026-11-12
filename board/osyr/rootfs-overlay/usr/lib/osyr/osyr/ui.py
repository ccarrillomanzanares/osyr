"""osyr ui — interface: screen brightness and idle lock.

Brightness is changed through logind (Session.SetBrightness), which allows it
for the user of the active session without root or udev rules. OSYR_SYSFS
changes the /sys root for the tests.
"""
import argparse
import os
import subprocess

from .result import Result, fail

SYSFS = os.environ.get("OSYR_SYSFS", "/sys")
MIN_PERCENT = 5  # never 0: a black screen with no easy way back
STEP = 10
# Like systemd-backlight: firmware (ACPI) before platform and raw
PREFERENCE = {"firmware": 0, "platform": 1, "raw": 2}


def read_int(path):
    with open(path) as f:
        return int(f.read().strip())


def backlight():
    base = os.path.join(SYSFS, "class", "backlight")
    try:
        names = os.listdir(base)
    except OSError:
        return None
    devs = []
    for name in names:
        d = os.path.join(base, name)
        try:
            kind = open(os.path.join(d, "type")).read().strip()
            mx = read_int(os.path.join(d, "max_brightness"))
            cur = read_int(os.path.join(d, "brightness"))
        except (OSError, ValueError):
            continue
        if mx > 0:
            devs.append((PREFERENCE.get(kind, 3), name, cur, mx))
    if not devs:
        return None
    _, name, cur, mx = sorted(devs)[0]
    return {"device": name, "value": cur, "max": mx, "percent": round(cur * 100 / mx)}


def set_value(dev, value):
    rc = subprocess.run(
        ["busctl", "--system", "call", "org.freedesktop.login1",
         "/org/freedesktop/login1/session/auto", "org.freedesktop.login1.Session",
         "SetBrightness", "ssu", "backlight", dev["device"], str(value)],
        capture_output=True).returncode
    if rc == 0:
        return True
    try:  # no logind session (e.g. root over ssh): write directly
        with open(os.path.join(SYSFS, "class", "backlight", dev["device"], "brightness"), "w") as f:
            f.write(str(value))
        return True
    except OSError:
        return False


def brightness(args):
    dev = backlight()
    if dev is None:
        return fail("UI_NO_BACKLIGHT", "This screen has no adjustable backlight.",
                    text="No adjustable backlight found.")
    if args.level is None:
        return Result(True, "OK", dev, text=f'Brightness: {dev["percent"]}%')
    lvl = args.level.strip()
    try:
        if lvl == "up":
            pct = dev["percent"] + STEP
        elif lvl == "down":
            pct = dev["percent"] - STEP
        else:
            pct = int(lvl.rstrip("%"))
    except ValueError:
        return fail("UI_BAD_LEVEL", "Use: osyr ui brightness [up|down|0-100]", {"level": lvl})
    pct = max(MIN_PERCENT, min(100, pct))
    value = max(1, round(pct * dev["max"] / 100))
    if not set_value(dev, value):
        return fail("UI_BRIGHTNESS_DENIED", "Run it from the graphical session.", dev)
    dev.update(value=value, percent=pct)
    return Result(True, "OK", dev, text=f"Brightness: {pct}%")


def level_arg(v):
    """up, down or a percentage 0-100: anything else is a usage error, so the agent
    knows before asking for confirmation (an 800 used to be silently cut to 100)."""
    w = v.strip().lower()
    if w in ("up", "down"):
        return w
    try:
        n = int(w.rstrip("%"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{v}' is not up, down or a percentage 0-100") from None
    if not 0 <= n <= 100:
        raise argparse.ArgumentTypeError(f"{n} is not a percentage: use 0-100 (e.g. 80)")
    return str(n)


# --- idle lock (osyr-idle reads the file) ----------------------------------------

LOCK_DEFAULT = 20
LOCK_MAX = 240


def idle_conf():
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "osyr", "idle.conf")


def lock_minutes():
    try:
        for line in open(idle_conf()):
            if line.startswith("LOCK_MINUTES="):
                v = line.split("=", 1)[1].strip()
                if v.isdigit():
                    return int(v)
    except OSError:
        pass
    return LOCK_DEFAULT


def lock_arg(v):
    v = v.strip().lower()
    if v in ("off", "never", "0"):
        return 0
    v = v.removesuffix("min").removesuffix("m")
    if v.isdigit() and 1 <= int(v) <= LOCK_MAX:
        return int(v)
    raise argparse.ArgumentTypeError(f"{v}: use minutes (1-{LOCK_MAX}) or off")


def describe_lock(m):
    if m == 0:
        return "The screen never locks by itself (it still locks before the laptop sleeps)."
    return f"The screen locks after {m} min without use and turns off 5 min later."


def lock(args):
    if args.minutes is None:
        m = lock_minutes()
        return Result(True, "OK", {"lock_minutes": m}, text=describe_lock(m))
    path = idle_conf()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(f"# osyr ui lock: minutes without use before locking (0 = never)\nLOCK_MINUTES={args.minutes}\n")
    # relaunch swayidle with the new time; outside the graphical session it applies on login
    applied = bool(os.environ.get("SWAYSOCK")) and relaunch_idle()
    text = describe_lock(args.minutes) + ("" if applied else " (from the next login)")
    return Result(True, "OK", {"lock_minutes": args.minutes, "applied": applied}, text=text)


def relaunch_idle():
    """swayidle again (time or script changed). True if sway launched it."""
    uid = str(os.getuid())
    subprocess.run(["pkill", "-x", "-u", uid, "swayidle"], capture_output=True)
    # a stopped one (SIGSTOP) does not die from SIGTERM until it continues
    subprocess.run(["pkill", "-CONT", "-x", "-u", uid, "swayidle"], capture_output=True)
    return subprocess.run(["swaymsg", "exec", "osyr-idle"], capture_output=True).returncode == 0


def register(sub):
    p = sub.add_parser("ui", help="interface: brightness, screen lock and displays")
    s = p.add_subparsers(dest="action", required=True)
    b = s.add_parser("brightness", help="show or set the screen brightness")
    b.add_argument("level", nargs="?", type=level_arg, help="up, down or a percentage (5-100)")
    b.set_defaults(func=brightness)
    lk = s.add_parser("lock", help="show or set when the screen locks by itself")
    lk.add_argument("minutes", nargs="?", type=lock_arg, help=f"minutes without use (1-{LOCK_MAX}) or off")
    lk.set_defaults(func=lock)
    from . import display  # displays: osyr ui display ...
    display.register(s)
