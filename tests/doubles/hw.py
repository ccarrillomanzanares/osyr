#!/usr/bin/env python3
"""Hardware doubles: stand-ins for the programs 'osyr net', 'osyr audio',
'osyr ui' and 'osyr bt' call, so the real osyr code runs with no hardware.

One program under several names (links in the same folder, put first in the
PATH): busctl (iwd, BlueZ and logind's brightness), iwctl, ip, wpctl, pw-dump,
pw-play, swaymsg and wl-mirror. Each answers only what OSYR uses, in the same
format as the real one.

The pretend machine is one JSON file, $OSYR_DOUBLES_STATE. It is created from
$OSYR_DOUBLES_SCENARIO (default: laptop.json next to this file); deleting it
puts the machine back in its initial state. 'hw.py --sysfs DIR' writes the
scenario's pretend /sys (backlight, battery, temperature, network cards). Wifi networks have passwords,
scans take a moment and unknown Bluetooth devices show up only after a scan,
so that the answers look like the real ones.

Standard library only: it runs with the Python of the OSYR image.
"""
import fcntl
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
STATE = os.environ.get("OSYR_DOUBLES_STATE") or os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "osyr-doubles.json")
SCENARIO = os.environ.get("OSYR_DOUBLES_SCENARIO") or os.path.join(HERE, "laptop.json")
SYSFS = os.environ.get("OSYR_SYSFS", "/sys")
IWD, BLUEZ = "net.connman.iwd", "org.bluez"
IWD_DEV = "/net/connman/iwd/0/3"
HCI = "/org/bluez/hci0"
SCAN_SECONDS = 1.5


class Machine:
    """The state file, held under a lock while one double reads and changes it."""

    def __enter__(self):
        os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
        self.lock = open(STATE + ".lock", "w")
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        try:
            with open(STATE) as f:
                self.s = json.load(f)
        except (OSError, ValueError):
            with open(SCENARIO) as f:
                self.s = json.load(f)
        self.before = json.dumps(self.s, sort_keys=True)
        return self.s

    def __exit__(self, *exc):
        if json.dumps(self.s, sort_keys=True) != self.before:
            tmp = f"{STATE}.{os.getpid()}"
            with open(tmp, "w") as f:
                json.dump(self.s, f, indent=1)
            os.replace(tmp, STATE)
        self.lock.close()


def die(text, rc=1):
    print(text, file=sys.stderr)
    sys.exit(rc)


# --- busctl ------------------------------------------------------------------------

def net_path(n):
    return f'{IWD_DEV}/{n["ssid"].encode().hex()}_{n["security"]}'


def known_path(n):
    return f'/net/connman/iwd/{n["ssid"].encode().hex()}_{n["security"]}'


def iwd_objects(s):
    w = s["wifi"]
    dev = {"Name": w["device"], "Address": w["mac"], "Powered": w["powered"], "Mode": "station",
           "Adapter": "/net/connman/iwd/0"}
    objs = {"/net/connman/iwd/0": {IWD + ".Adapter": {"Powered": True, "Name": "phy0",
                                                     "Model": w.get("model", "")}},
            IWD_DEV: {IWD + ".Device": dev}}
    if not w["powered"]:
        return objs
    station = {"State": "connected" if w["connected"] else "disconnected",
               "Scanning": time.time() < w.get("scan_until", 0)}
    objs[IWD_DEV][IWD + ".Station"] = station
    for n in w["networks"]:
        if n.get("known"):
            objs[known_path(n)] = {IWD + ".KnownNetwork": {"Name": n["ssid"], "Type": n["security"],
                                                           "AutoConnect": True}}
        if not (w.get("scanned") or n["ssid"] == w["connected"]):
            continue  # iwd only knows the networks of its last scan
        net = {"Name": n["ssid"], "Type": n["security"], "Connected": n["ssid"] == w["connected"],
               "Device": IWD_DEV}
        if n.get("known"):
            net["KnownNetwork"] = known_path(n)
        objs[net_path(n)] = {IWD + ".Network": net}
        if net["Connected"]:
            station["ConnectedNetwork"] = net_path(n)
    return objs


def bt_dev_path(d):
    return f'{HCI}/dev_{d["address"].replace(":", "_")}'


def bt_objects(s):
    b = s["bt"]
    objs = {HCI: {BLUEZ + ".Adapter1": {"Address": b["address"], "Name": b["alias"], "Alias": b["alias"],
                                        "Powered": b["powered"], "Discovering": False}}}
    for d in b["devices"]:
        if not (d.get("paired") or d.get("seen")):
            continue
        dev = {"Address": d["address"], "Name": d["name"], "Alias": d["name"], "Icon": d.get("icon", ""),
               "Paired": bool(d.get("paired")), "Connected": bool(d.get("connected")),
               "Trusted": bool(d.get("trusted")), "Adapter": HCI}
        if d.get("seen") and d.get("in_range", True):
            dev["RSSI"] = d.get("rssi", -60)
        objs[bt_dev_path(d)] = {BLUEZ + ".Device1": dev}
    return objs


def bt_find(s, path):
    return next((d for d in s["bt"]["devices"] if bt_dev_path(d) == path), None)


def busctl(argv):
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 5:
        die("busctl double: only 'call' and 'set-property' are answered")
    verb, dest, path, iface, member, rest = args[0], args[1], args[2], args[3], args[4], args[5:]

    def reply(*values):
        if any(a.startswith("--json") for a in argv):
            print(json.dumps({"type": "", "data": list(values)}))
        sys.exit(0)

    def unknown():
        die(f"Call failed: Unknown object or method '{path}' {iface}.{member}")

    with Machine() as s:
        if dest == IWD:
            w = s["wifi"]
            if verb == "call" and member == "GetManagedObjects":
                reply(iwd_objects(s))
            if verb == "set-property" and (iface, member) == (IWD + ".Device", "Powered"):
                w["powered"] = rest[-1] == "true"
                if not w["powered"]:
                    w["connected"], w["scanned"] = None, False
                reply()
            if verb == "call" and iface == IWD + ".Station" and w["powered"]:
                if member == "Scan":
                    w["scan_until"], w["scanned"] = time.time() + SCAN_SECONDS, True
                    reply()
                if member == "GetOrderedNetworks":
                    nets = [n for n in w["networks"] if w.get("scanned") or n["ssid"] == w["connected"]]
                    nets.sort(key=lambda n: (n["ssid"] != w["connected"], -n["signal"]))
                    reply([[net_path(n), n["signal"] * 100] for n in nets])
                if member == "Disconnect":
                    w["connected"] = None
                    reply()
            unknown()
        if dest == BLUEZ:
            b = s["bt"]
            if not b.get("present", True):
                die("Call failed: The name org.bluez was not provided by any .service files")
            if verb == "call" and member == "GetManagedObjects":
                reply(bt_objects(s))
            if verb == "set-property" and (iface, member) == (BLUEZ + ".Adapter1", "Powered"):
                b["powered"] = rest[-1] == "true"
                if not b["powered"]:
                    for d in b["devices"]:
                        d["connected"] = False
                reply()
            if verb == "call" and iface == BLUEZ + ".Adapter1":
                if not b["powered"]:
                    die("Call failed: Resource Not Ready")
                if member == "StartDiscovery":
                    for d in b["devices"]:
                        d["seen"] = d.get("in_range", True)
                    reply()
                if member == "StopDiscovery":
                    reply()
                if member == "RemoveDevice":
                    d = bt_find(s, rest[-1])
                    if d is None:
                        die("Call failed: Does Not Exist")
                    d.update(paired=False, connected=False, trusted=False, seen=False)
                    reply()
            d = bt_find(s, path)
            if d is not None and iface == BLUEZ + ".Device1":
                if verb == "set-property" and member == "Trusted":
                    d["trusted"] = rest[-1] == "true"
                    reply()
                usable = b["powered"] and d.get("in_range", True)
                if verb == "call" and member == "Pair":
                    if not usable or not d.get("pairable", True):
                        die("Call failed: Authentication Failed")
                    d["paired"] = True
                    reply()
                if verb == "call" and member == "Connect":
                    if not usable or not d.get("paired"):
                        die("Call failed: br-connection-page-timeout")
                    d["connected"] = True
                    reply()
                if verb == "call" and member == "Disconnect":
                    d["connected"] = False
                    reply()
            unknown()
        if dest == "org.freedesktop.login1" and member == "SetBrightness":
            kind, name, value = rest[-3:]
            try:
                with open(os.path.join(SYSFS, "class", kind, name, "brightness"), "w") as f:
                    f.write(f"{int(value)}\n")
            except (OSError, ValueError) as e:
                die(f"Call failed: {e}")
            reply()
    die(f"Call failed: The name {dest} was not provided by any .service files")


# --- iwctl ---------------------------------------------------------------------------

def wifi_join(ssid, password, ask):
    """Connects as iwd would; returns the error text, or None when connected.
    'ask' reads the password from the keyboard when the network needs one."""
    with Machine() as s:
        w = s["wifi"]
        n = next((x for x in w["networks"] if x["ssid"] == ssid), None)
        if not w["powered"] or n is None or not (w.get("scanned") or ssid == w["connected"]):
            return "Invalid network name"
        needs = n["security"] == "psk" and not (n.get("known") and n.get("saved_ok", True))
    if needs and password is None:
        password = ask(n)
        if password is None:
            return "Operation failed"
    time.sleep(1)  # association and DHCP
    with Machine() as s:
        w = s["wifi"]
        n = next(x for x in w["networks"] if x["ssid"] == ssid)
        if needs and password != n.get("password"):
            return "Operation failed"
        if n.get("broken"):
            return "Operation failed"
        n["known"], n["saved_ok"] = True, True
        w["connected"] = ssid
    return None


def iwctl(argv):
    args = [a for a in argv if a != "--dont-ask"]
    if args:  # one command, no questions
        if args[:1] == ["station"] and args[2:3] == ["connect"] and len(args) >= 4:
            err = wifi_join(args[3], None, lambda n: None)
            if err:
                print(err)
                sys.exit(1)
            sys.exit(0)
        die("iwctl double: only 'station <device> connect <ssid>' is answered")
    import termios

    def ask(n):
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ECHO
        try:
            termios.tcsetattr(fd, termios.TCSANOW, new)
            sys.stdout.write(f'Type the network passphrase for {n["ssid"]} psk.\nPassphrase: ')
            sys.stdout.flush()
            line = sys.stdin.readline()
        finally:
            termios.tcsetattr(fd, termios.TCSANOW, old)
        print()
        return line.rstrip("\r\n") if line else None

    while True:
        sys.stdout.write("[iwd]# ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line or line.strip() in ("quit", "exit"):
            sys.exit(0)
        m = re.fullmatch(r'station\s+(\S+)\s+connect\s+(?:"((?:[^"\\]|\\.)*)"|(\S+))\s*', line.strip())
        if not m:
            print("Invalid command")
            continue
        ssid = re.sub(r"\\(.)", r"\1", m.group(2)) if m.group(2) is not None else m.group(3)
        err = wifi_join(ssid, None, ask)
        if err:
            print(err)


# --- ip ------------------------------------------------------------------------------

def links(s):
    w, out = s["wifi"], []
    out.append({"ifindex": 1, "ifname": "lo", "flags": ["LOOPBACK", "UP", "LOWER_UP"], "mtu": 65536,
                "operstate": "UNKNOWN", "link_type": "loopback", "address": "00:00:00:00:00:00",
                "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8, "scope": "host"}]})
    for i, e in enumerate(s.get("wired", []), 2):
        up = bool(e.get("cable"))
        out.append({"ifindex": i, "ifname": e["name"], "mtu": 1500, "link_type": "ether",
                    "flags": ["BROADCAST", "MULTICAST", "UP"] + (["LOWER_UP"] if up else ["NO-CARRIER"]),
                    "operstate": "UP" if up else "DOWN", "address": e["mac"],
                    "addr_info": [{"family": "inet", "local": e["ipv4"], "prefixlen": 24, "scope": "global"}]
                    if up else []})
    if w.get("present", True):
        up = bool(w["connected"])
        out.append({"ifindex": len(out) + 1, "ifname": w["device"], "mtu": 1500, "link_type": "ether",
                    "flags": ["BROADCAST", "MULTICAST"] + (["UP", "LOWER_UP"] if up else
                                                           ["UP", "NO-CARRIER"] if w["powered"] else []),
                    "operstate": "UP" if up else "DOWN", "address": w["mac"],
                    "addr_info": [{"family": "inet", "local": w["ipv4"], "prefixlen": 24, "scope": "global"}]
                    if up else []})
    return out


def default_route(s):
    for e in s.get("wired", []):
        if e.get("cable"):
            return {"dst": "default", "gateway": e["gateway"], "dev": e["name"], "protocol": "dhcp",
                    "prefsrc": e["ipv4"], "metric": 1024}
    w = s["wifi"]
    if w["connected"]:
        return {"dst": "default", "gateway": w["gateway"], "dev": w["device"], "protocol": "dhcp",
                "prefsrc": w["ipv4"], "metric": 1024}
    return None


def ip(argv):
    as_json = any(a in ("-j", "-json") for a in argv)
    args = [a for a in argv if not a.startswith("-")]
    obj = args[0] if args else ""
    with Machine() as s:
        if "address".startswith(obj) and obj or "link".startswith(obj) and obj:
            show = links(s)
            if "dev" in args:
                show = [l for l in show if l["ifname"] == args[args.index("dev") + 1]]
                if not show:
                    die(f'Device "{args[args.index("dev") + 1]}" does not exist.')
            if as_json:
                print(json.dumps(show))
                return
            for l in show:
                print(f'{l["ifindex"]}: {l["ifname"]}: <{",".join(l["flags"])}> mtu {l["mtu"]} '
                      f'state {l["operstate"]}')
                print(f'    link/{l["link_type"]} {l["address"]}')
                if not obj.startswith("l"):
                    for a in l["addr_info"]:
                        print(f'    inet {a["local"]}/{a["prefixlen"]} scope {a["scope"]} {l["ifname"]}')
            return
        if "route".startswith(obj) and obj:
            r = default_route(s)
            routes = [r] if r else []
            if as_json:
                print(json.dumps(routes))
                return
            for r in routes:
                print(f'default via {r["gateway"]} dev {r["dev"]} proto dhcp src {r["prefsrc"]} metric 1024')
                net = r["prefsrc"].rsplit(".", 1)[0]
                if "default" not in args:
                    print(f'{net}.0/24 dev {r["dev"]} proto kernel scope link src {r["prefsrc"]}')
            return
    die("ip double: only 'addr', 'link' and 'route' are answered")


# --- wpctl, pw-dump, pw-play -----------------------------------------------------------

TARGETS = {"@DEFAULT_AUDIO_SINK@": "output", "@DEFAULT_SINK@": "output",
           "@DEFAULT_AUDIO_SOURCE@": "input", "@DEFAULT_SOURCE@": "input"}


def wpctl(argv):
    with Machine() as s:
        a = s["audio"]
        if not a.get("running", True):
            die("Could not connect to PipeWire")
        if argv[:1] == ["status"]:
            print("PipeWire 'pipewire-0' [1.4.7, osyr@osyr, cookie:1]\nAudio\n ├─ Sinks:")
            for d in a["outputs"]:
                print(f' │  {"*" if d["name"] == a["output"]["device"] else " "} {d["id"]}. {d["description"]}')
            print(" ├─ Sources:")
            for d in a["inputs"]:
                print(f' │  {"*" if d["name"] == a["input"]["device"] else " "} {d["id"]}. {d["description"]}')
            return
        if len(argv) >= 2 and argv[1] in TARGETS:
            t = a[TARGETS[argv[1]]]
            if not t.get("device"):
                die(f"Translate ID error: '{argv[1]}' is not a valid ID")
            if argv[0] == "get-volume":
                print(f'Volume: {t["volume"]:.2f}' + (" [MUTED]" if t["muted"] else ""))
                return
            if argv[0] == "set-volume" and len(argv) == 3:
                try:
                    t["volume"] = max(0.0, min(1.5, float(argv[2].rstrip("%")) / (100 if "%" in argv[2] else 1)))
                except ValueError:
                    die(f"Invalid volume '{argv[2]}'")
                return
            if argv[0] == "set-mute" and len(argv) == 3 and argv[2] in ("0", "1", "toggle"):
                t["muted"] = (not t["muted"]) if argv[2] == "toggle" else argv[2] == "1"
                return
    die("wpctl double: only status, get-volume, set-volume and set-mute on the default devices are answered")


def pw_dump(argv):
    with Machine() as s:
        a = s["audio"]
        if not a.get("running", True):
            die("can't connect: Host is down")
        objs = [{"id": 30, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
                 "metadata": [{"subject": 0, "key": "default.audio.sink", "type": "Spa:String:JSON",
                               "value": {"name": a["output"].get("device")}},
                              {"subject": 0, "key": "default.audio.source", "type": "Spa:String:JSON",
                               "value": {"name": a["input"].get("device")}}]}]
        for cls, devs in (("Audio/Sink", a["outputs"]), ("Audio/Source", a["inputs"])):
            for d in devs:
                objs.append({"id": d["id"], "type": "PipeWire:Interface:Node",
                             "info": {"props": {"media.class": cls, "node.name": d["name"],
                                                "node.description": d["description"]}}})
        print(json.dumps(objs))


def pw_play(argv):
    with Machine() as s:
        if not s["audio"].get("running", True):
            die("can't connect: Host is down")
    time.sleep(0.5)


# --- swaymsg, wl-mirror ----------------------------------------------------------------

def mode_text(m):
    return f'{m["width"]}x{m["height"]}@{round(m["refresh"] / 1000)}Hz'


def sway_output(o):
    cur = o["current_mode"]
    w, h = (round(cur["width"] / o["scale"]), round(cur["height"] / o["scale"])) if o["active"] else (0, 0)
    return {"name": o["name"], "make": o["make"], "model": o["model"], "serial": o["serial"],
            "active": o["active"], "dpms": o["active"], "power": o["active"], "primary": False,
            "scale": o["scale"] if o["active"] else -1.0, "transform": "normal",
            "rect": {"x": o["x"], "y": o["y"], "width": w, "height": h},
            "current_mode": dict(cur, picture_aspect_ratio="none") if o["active"] else
            {"width": 0, "height": 0, "refresh": 0},
            "modes": [dict(m, picture_aspect_ratio="none") for m in o["modes"]], "type": "output"}


def swaymsg(argv):
    args = [a for a in argv if a not in ("-r", "--raw", "-q", "--quiet")]
    with Machine() as s:
        outs = s["displays"]
        if args[:2] == ["-t", "get_outputs"]:
            print(json.dumps([sway_output(o) for o in outs]))
            return
        if args[:1] == ["--"]:
            args = args[1:]
        if args[:1] == ["output"] and len(args) >= 3:
            o = next((x for x in outs if x["name"] == args[1]), None)
            if o is None:
                die(f'Error: Unknown output {args[1]}', 2)
            what, val = args[2], args[3:]
            if what in ("enable", "disable"):
                o["active"] = what == "enable"
            elif what in ("mode", "resolution", "res") and val:
                m = re.fullmatch(r"(\d+)x(\d+)(?:@([\d.]+)Hz)?", val[0])
                fit = [x for x in o["modes"] if m and (x["width"], x["height"]) == (int(m[1]), int(m[2]))
                       and (m[3] is None or round(x["refresh"] / 1000) == round(float(m[3])))]
                if not fit:
                    die(f'Error: Mode {val[0]} not available for output {o["name"]}', 2)
                o["current_mode"] = max(fit, key=lambda x: x["refresh"])
            elif what == "scale" and val:
                o["scale"] = float(val[0])
            elif what in ("position", "pos") and len(val) >= 2:
                o["x"], o["y"] = int(val[0]), int(val[1])
            else:
                die(f"Error: Invalid output subcommand: {what}", 2)
            print(json.dumps([{"success": True}]))
            return
    die("swaymsg double: only 'get_outputs' and 'output' are answered", 2)


def wl_mirror(argv):
    time.sleep(3600)


def grim(argv):
    """A capture of the screen: a small real PNG (one grey pixel), to the file named or to '-'."""
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00\x80")) + chunk(b"IEND", b""))
    target = [a for a in argv if not a.startswith("-") or a == "-"]
    target = target[-1] if target else "-"
    if target == "-":
        sys.stdout.buffer.write(png)
    else:
        with open(target, "wb") as f:
            f.write(png)
    return 0


def proc_file(name):
    """The scenario's /proc/cpuinfo or /proc/meminfo: the pretend laptop's, not the host's."""
    with open(SCENARIO) as f:
        pc = json.load(f).get("computer", {})
    cores, mem = int(pc.get("cores", 4)), int(float(pc.get("memory_gb", 16)) * 1024 * 1024)
    if name == "cpuinfo":
        flags = ("fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse sse2 "
                 "ht syscall nx rdtscp lm pni pclmulqdq ssse3 fma cx16 sse4_1 sse4_2 movbe popcnt aes xsave avx "
                 "f16c rdrand bmi1 avx2 bmi2")
        return "".join(
            f"processor\t: {i}\nvendor_id\t: GenuineIntel\nmodel name\t: {pc.get('cpu', 'Intel(R) Core(TM) i5 CPU')}\n"
            f"cpu MHz\t\t: 1800.000\ncache size\t: 6144 KB\nphysical id\t: 0\nsiblings\t: {cores}\n"
            f"core id\t\t: {i}\ncpu cores\t: {cores}\nflags\t\t: {flags}\n\n" for i in range(cores))
    if name == "meminfo":
        used = mem // 4
        return (f"MemTotal:       {mem - 300000:>8} kB\nMemFree:        {mem - used - 2000000:>8} kB\n"
                f"MemAvailable:   {mem - used:>8} kB\nBuffers:        {120000:>8} kB\nCached:         {1700000:>8} kB\n"
                f"SwapCached:     {0:>8} kB\nSwapTotal:      {mem // 2:>8} kB\nSwapFree:       {mem // 2:>8} kB\n"
                f"Shmem:          {90000:>8} kB\n")
    die(f"hw.py --proc: cpuinfo or meminfo, not '{name}'", 2)


def make_sysfs(root):
    """Writes the scenario's pretend /sys under 'root' (a name ending in / is a folder)."""
    with open(SCENARIO) as f:
        tree = json.load(f).get("sysfs", {})
    for name, text in tree.items():
        path = os.path.join(root, name)
        if name.endswith("/"):
            os.makedirs(path, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text + "\n")


DOUBLES = {"busctl": busctl, "iwctl": iwctl, "ip": ip, "wpctl": wpctl, "pw-dump": pw_dump,
           "pw-play": pw_play, "swaymsg": swaymsg, "wl-mirror": wl_mirror, "grim": grim}


def main():
    name, argv = os.path.basename(sys.argv[0]), sys.argv[1:]
    if name not in DOUBLES and argv and argv[0] in DOUBLES:  # hw.py busctl ...
        name, argv = argv[0], argv[1:]
    if name == "hw.py" and argv[:1] == ["--names"]:
        print(" ".join(DOUBLES))
        return 0
    if name == "hw.py" and argv[:1] == ["--proc"] and len(argv) == 2:
        sys.stdout.write(proc_file(argv[1]))
        return 0
    if name == "hw.py" and argv[:1] == ["--sysfs"] and len(argv) == 2:
        make_sysfs(argv[1])
        return 0
    if name not in DOUBLES:
        die(f"hw.py: called as '{name}'; it stands in for: {', '.join(DOUBLES)}", 2)
    try:
        DOUBLES[name](argv)
    except BrokenPipeError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
