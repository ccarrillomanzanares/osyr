"""osyr ui display — displays: list, on/off, resolution, scale, extend and
mirror.

The state comes from sway (swaymsg -t get_outputs, JSON). Each change is
applied at once and saved per monitor model (make, model and serial number)
in ~/.config/sway/config.d/50-osyr-displays.conf: when it is plugged in again,
sway sets it up as it was. Sway does not mirror displays: wl-mirror does, a
full-screen window on the target display that copies the source one.
"""
import json
import os
import re
import signal
import subprocess

from .result import Result, fail

CONF = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                    "sway", "config.d", "50-osyr-displays.conf")


def env():
    e = dict(os.environ)
    e.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if "SWAYSOCK" not in e:  # from a tty or the serial console
        d = e["XDG_RUNTIME_DIR"]
        socks = [f for f in os.listdir(d) if f.startswith("sway-ipc.")] if os.path.isdir(d) else []
        if socks:
            e["SWAYSOCK"] = os.path.join(d, socks[0])
    return e


def swaymsg(*args):
    p = subprocess.run(["swaymsg", *args], capture_output=True, text=True, timeout=10, env=env())
    return p.returncode == 0, p.stdout.strip() or p.stderr.strip()


def outputs():
    ok, out = swaymsg("-t", "get_outputs", "-r")
    if not ok:
        return None
    res = []
    for o in json.loads(out):
        mode = o.get("current_mode") or {}
        res.append({
            "name": o["name"], "id": f'{o.get("make", "")} {o.get("model", "")} {o.get("serial", "")}'.strip(),
            "description": f'{o.get("make", "")} {o.get("model", "")}'.strip(),
            "enabled": o.get("active", False), "internal": o["name"].startswith(("eDP", "LVDS", "DSI")),
            "mode": f'{mode.get("width")}x{mode.get("height")}@{round(mode.get("refresh", 0) / 1000)}Hz'
            if mode else None,
            "scale": o.get("scale"), "position": [o.get("rect", {}).get("x"), o.get("rect", {}).get("y")],
            "width": (o.get("rect") or {}).get("width"), "height": (o.get("rect") or {}).get("height"),
            "modes": sorted({f'{m["width"]}x{m["height"]}@{round(m["refresh"] / 1000)}Hz'
                             for m in o.get("modes", [])},
                            key=lambda s: [-int(x) for x in re.findall(r"\d+", s)]),
        })
    return res


def find(outs, name):
    name = (name or "").lower()
    for o in outs:
        if o["name"].lower() == name or (name and name in o["description"].lower()):
            return o
    return None


def remember(o, setting):
    """Saves 'setting' (e.g. 'mode 1920x1080@60Hz') for that monitor."""
    os.makedirs(os.path.dirname(CONF), exist_ok=True)
    try:
        lines = open(CONF).read().splitlines()
    except OSError:
        lines = ["# OSYR — displays (written by 'osyr ui display'). It can be edited."]
    key = setting.split()[0]
    ident = f'output "{o["id"]}"'
    lines = [l for l in lines if not (l.startswith(ident) and l[len(ident):].split()[:1] == [key])]
    lines.append(f"{ident} {setting}")
    with open(CONF, "w") as f:
        f.write("\n".join(lines) + "\n")


def apply(o, setting):
    ok, msg = swaymsg("output", o["name"], *setting.split())
    if ok:
        remember(o, setting)
    return ok, msg


def no_sway():
    return fail("UI_NO_SESSION", "Run it inside the graphical session.",
                text="The graphical session (sway) is not running.")


def show(outs):
    lines = []
    for o in outs:
        state = f'{o["mode"]}  scale {o["scale"]}  at {o["position"][0]},{o["position"][1]}' \
            if o["enabled"] else "off"
        lines.append(f'{o["name"]:<10} {o["description"][:28]:<28} {state}')
    return "\n".join(lines)


def display(args):
    outs = outputs()
    if outs is None:
        return no_sway()
    act = args.action or "list"
    if act == "list":
        return Result(True, "OK", {"displays": outs}, text=show(outs))

    o = find(outs, args.name) if args.name else None
    if act in ("on", "off", "mode", "scale") and o is None:  # extend/mirror: optional name
        return fail("UI_NO_DISPLAY", "Run: osyr ui display list", {"name": args.name},
                    text=f'No display called "{args.name}".')

    if act == "on":
        ok, msg = apply(o, "enable")
    elif act == "off":
        if sum(1 for x in outs if x["enabled"]) <= 1 and o["enabled"]:
            return fail("UI_LAST_DISPLAY", "Turn another display on first.",
                        text="That is the only display on; turning it off would leave you without screen.")
        ok, msg = apply(o, "disable")
    elif act == "mode":
        if not args.value:
            return Result(True, "OK", {"name": o["name"], "modes": o["modes"]},
                          text=f'{o["name"]} modes: ' + ", ".join(o["modes"]))
        want = args.value if "@" in args.value else next(
            (m for m in o["modes"] if m.startswith(args.value + "@")), args.value)
        ok, msg = apply(o, f"mode {want}")
    elif act == "scale":
        try:
            s = float(args.value)
            assert 0.5 <= s <= 3
        except (TypeError, ValueError, AssertionError):
            return fail("UI_BAD_SCALE", "Use a scale between 0.5 and 3, e.g. 1.25")
        ok, msg = apply(o, f"scale {s:g}")
    elif act == "extend":
        return extend(outs, args.value or args.name or "right")
    elif act == "mirror":
        return mirror(outs, args.name)
    else:
        return fail("UI_BAD_ACTION", "Run: osyr ui display --help")
    if not ok:
        return fail("UI_DISPLAY_FAILED", "Run: osyr ui display list", {"error": msg}, text=msg)
    return Result(True, "OK", {"displays": outputs()}, text=show(outputs()))


def extend(outs, where):
    """The external display next to the built-in one (left/right/above/below), both on."""
    stop_mirror()
    main = next((o for o in outs if o["internal"]), outs[0])
    others = [o for o in outs if o is not main]
    if not others:
        return fail("UI_ONE_DISPLAY", "Plug in another display first.", text="There is only one display.")
    for o in [main] + others:
        if not o["enabled"]:
            apply(o, "enable")
    outs = outputs()
    main = next(o for o in outs if o["name"] == main["name"])
    for o in (x for x in outs if x["name"] != main["name"]):
        w, h = o["width"] or 1920, o["height"] or 1080
        pos = {"right": (main["width"], 0), "left": (-w, 0),
               "above": (0, -h), "below": (0, main["height"])}.get(where, (main["width"], 0))
        apply(o, f"position {pos[0]} {pos[1]}")
    apply(main, "position 0 0")
    return Result(True, "OK", {"displays": outputs()}, text=show(outputs()))


def mirror_pidfile():
    return os.path.join(env()["XDG_RUNTIME_DIR"], "osyr-mirror.pid")


def stop_mirror():
    try:
        os.kill(int(open(mirror_pidfile()).read()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    try:
        os.unlink(mirror_pidfile())
    except FileNotFoundError:
        pass


def mirror(outs, target_name):
    """Mirrors the built-in display on the external one (or on the one given)."""
    stop_mirror()
    src = next((o for o in outs if o["internal"]), outs[0])
    dst = find(outs, target_name) if target_name else next((o for o in outs if o is not src), None)
    if dst is None or dst is src:
        return fail("UI_ONE_DISPLAY", "Plug in another display first.", text="There is no display to mirror to.")
    if not dst["enabled"]:
        apply(dst, "enable")
    p = subprocess.Popen(["wl-mirror", "--fullscreen-output", dst["name"], src["name"]], env=env(),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    with open(mirror_pidfile(), "w") as f:
        f.write(str(p.pid))
    return Result(True, "OK", {"source": src["name"], "target": dst["name"]},
                  "Stop it with: osyr ui display extend",
                  text=f'Mirroring {src["name"]} on {dst["name"]}.')


def register(sub):
    d = sub.add_parser("display", help="displays: list, on/off, resolution, scale, extend, mirror")
    d.add_argument("action", nargs="?",
                   choices=["list", "on", "off", "mode", "scale", "extend", "mirror"])
    d.add_argument("name", nargs="?", help="display name (e.g. HDMI-A-1) or part of its model")
    d.add_argument("value", nargs="?", help="mode (1920x1080[@60Hz]), scale (1.25) or side for extend")
    d.set_defaults(func=display)
