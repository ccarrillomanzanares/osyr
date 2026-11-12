"""osyr bt — Bluetooth: headphones, speakers, keyboards and mice.

BlueZ over D-Bus with busctl (JSON), like wifi with iwd. Connecting pairs the
device, marks it as trusted (it reconnects by itself) and connects it.
PipeWire picks up headphone sound with nothing else to do. Pairing without an
agent uses BlueZ's "Just Works" mode, the one almost all headphones and
speakers use; keyboards that ask for a PIN are not covered yet.
"""
import os
import time

from .net import busctl
from .result import Result, fail

BLUEZ = "org.bluez"


def objects():
    r = busctl("call", BLUEZ, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    if r is None:
        return None
    return r[0] if isinstance(r, list) and r and isinstance(r[0], dict) else {}


def adapter(objs):
    for path, ifaces in sorted(objs.items()):
        a = ifaces.get(BLUEZ + ".Adapter1")
        if a:
            return path, a
    return None, None


def devices(objs):
    out = []
    for path, ifaces in sorted(objs.items()):
        d = ifaces.get(BLUEZ + ".Device1")
        if not d:
            continue
        out.append({"path": path, "address": d.get("Address"),
                    "name": d.get("Alias") or d.get("Name") or d.get("Address"),
                    "type": d.get("Icon") or "", "paired": bool(d.get("Paired")),
                    "connected": bool(d.get("Connected")), "trusted": bool(d.get("Trusted")),
                    "rssi": d.get("RSSI")})
    return out


def public(d):
    return {k: v for k, v in d.items() if k != "path"}


def no_bt(objs):
    # bluetooth.service only starts if /sys/class/bluetooth exists
    if objs is None and os.path.isdir("/sys/class/bluetooth"):
        return fail("BT_NO_SERVICE", "Run: systemctl status bluetooth",
                    text="The Bluetooth service is not running.")
    return fail("BT_NO_ADAPTER", "This computer has no Bluetooth (or it is blocked).",
                text="No Bluetooth adapter found.")


def get_adapter():
    objs = objects()
    path, a = adapter(objs or {})
    return objs, path, a


def power(path, on):
    busctl("set-property", BLUEZ, path, BLUEZ + ".Adapter1", "Powered", "b", "true" if on else "false")


def find(objs, what):
    w = (what or "").lower()
    for d in devices(objs):
        if w in ((d["address"] or "").lower(), (d["name"] or "").lower()) or \
                (len(w) > 2 and w in (d["name"] or "").lower()):
            return d
    return None


def status(args):
    objs, path, a = get_adapter()
    if not path:
        return no_bt(objs)
    devs = [d for d in devices(objs) if d["paired"] or d["connected"]]
    lines = [f'Bluetooth: {"on" if a.get("Powered") else "off"} ({a.get("Alias") or a.get("Name")})']
    lines += [f'  {"*" if d["connected"] else " "} {d["name"]}  {d["address"]}  {d["type"]}' for d in devs]
    return Result(True, "OK", {"powered": bool(a.get("Powered")), "devices": [public(d) for d in devs]},
                  text="\n".join(lines))


def on_off(args):
    objs, path, a = get_adapter()
    if not path:
        return no_bt(objs)
    power(path, args.action == "on")
    return Result(True, "OK", {"powered": args.action == "on"}, text=f"Bluetooth {args.action}.")


def scan(args):
    objs, path, a = get_adapter()
    if not path:
        return no_bt(objs)
    if not a.get("Powered"):
        power(path, True)
        time.sleep(1)
    busctl("call", BLUEZ, path, BLUEZ + ".Adapter1", "StartDiscovery")
    time.sleep(args.seconds)
    busctl("call", BLUEZ, path, BLUEZ + ".Adapter1", "StopDiscovery")
    devs = sorted(devices(objects() or {}), key=lambda d: -(d["rssi"] or -999))
    if not devs:
        return Result(True, "BT_NONE_FOUND", {"devices": []},
                      "Put the device in pairing mode and scan again.", text="No devices found.")
    lines = [f'{"*" if d["connected"] else " "} {d["name"][:30]:<30} {d["address"]}  {d["type"]}'
             for d in devs]
    return Result(True, "OK", {"devices": [public(d) for d in devs]},
                  "Run: osyr bt connect <name or address>", text="\n".join(lines))


def connect(args):
    objs, path, a = get_adapter()
    if not path:
        return no_bt(objs)
    if not a.get("Powered"):
        power(path, True)
        time.sleep(1)
    d = find(objs, args.device)
    if d is None:
        return fail("BT_NOT_FOUND", "Run: osyr bt scan", {"device": args.device},
                    text=f'"{args.device}" not found. Put it in pairing mode and run: osyr bt scan')
    if not d["paired"]:
        if busctl("call", BLUEZ, d["path"], BLUEZ + ".Device1", "Pair", timeout=60) is None:
            return fail("BT_PAIR_FAILED", "Put the device in pairing mode and try again.",
                        {"device": d["name"]}, text=f'Could not pair with {d["name"]}.')
    busctl("set-property", BLUEZ, d["path"], BLUEZ + ".Device1", "Trusted", "b", "true")
    if busctl("call", BLUEZ, d["path"], BLUEZ + ".Device1", "Connect", timeout=60) is None:
        return fail("BT_CONNECT_FAILED", "Turn the device off and on, then try again.",
                    {"device": d["name"]}, text=f'Paired, but could not connect to {d["name"]}.')
    return Result(True, "OK", public(dict(d, paired=True, connected=True, trusted=True)),
                  text=f'Connected to {d["name"]}.')


def disconnect(args):
    objs, path, a = get_adapter()
    if not path:
        return no_bt(objs)
    d = find(objs, args.device)
    if d is None:
        return fail("BT_NOT_FOUND", "Run: osyr bt status", {"device": args.device})
    busctl("call", BLUEZ, d["path"], BLUEZ + ".Device1", "Disconnect")
    return Result(True, "OK", {"device": d["name"]}, text=f'Disconnected {d["name"]}.')


def remove(args):
    objs, path, a = get_adapter()
    if not path:
        return no_bt(objs)
    d = find(objs, args.device)
    if d is None:
        return fail("BT_NOT_FOUND", "Run: osyr bt status", {"device": args.device})
    busctl("call", BLUEZ, path, BLUEZ + ".Adapter1", "RemoveDevice", "o", d["path"])
    return Result(True, "OK", {"device": d["name"]}, text=f'Forgot {d["name"]}.')


def register(sub):
    p = sub.add_parser("bt", help="Bluetooth: headphones, speakers, keyboards, mice")
    s = p.add_subparsers(dest="action", required=True)
    s.add_parser("status", help="adapter and paired devices").set_defaults(func=status)
    s.add_parser("on", help="turn Bluetooth on").set_defaults(func=on_off)
    s.add_parser("off", help="turn Bluetooth off").set_defaults(func=on_off)
    sc = s.add_parser("scan", help="look for nearby devices")
    sc.add_argument("--seconds", type=int, default=8)
    sc.set_defaults(func=scan)
    for name, fn, h in (("connect", connect, "pair (if needed) and connect"),
                        ("disconnect", disconnect, "disconnect a device"),
                        ("remove", remove, "forget a paired device")):
        c = s.add_parser(name, help=h)
        c.add_argument("device", help="name or address")
        c.set_defaults(func=fn)
