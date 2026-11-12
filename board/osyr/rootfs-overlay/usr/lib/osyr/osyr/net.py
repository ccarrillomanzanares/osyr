"""osyr net — wired network and wifi.

Wifi is handled by iwd: state and scans are read over D-Bus with busctl
(JSON output, no text parsing); connecting uses iwctl, which carries the
agent that hands the password to iwd. osyr is in wheel, which iwd's D-Bus
policy allows to talk to it.
"""
import getpass
import json
import os
import re
import socket
import subprocess
import sys
import time

from .result import Result, fail

IWD = "net.connman.iwd"


# --- helpers -------------------------------------------------------------------

def run(args, timeout=15):
    try:
        # No input: iwctl must not sit waiting for a typed password
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def ip_json(*args):
    rc, out, _ = run(["ip", "-j", *args])
    try:
        return json.loads(out) if rc == 0 and out.strip() else []
    except ValueError:
        return []


def unwrap(v):
    """Strips the {"type", "data"} layers from busctl's JSON output."""
    if isinstance(v, dict) and set(v) == {"type", "data"}:
        return unwrap(v["data"])
    if isinstance(v, dict):
        return {k: unwrap(x) for k, x in v.items()}
    if isinstance(v, list):
        return [unwrap(x) for x in v]
    return v


def busctl(*args, timeout=15):
    rc, out, err = run(["busctl", "--system", "--json=short", *args], timeout=timeout)
    if rc != 0:
        return None
    try:
        return unwrap(json.loads(out)) if out.strip() else {}
    except ValueError:
        return None


# --- iwd -------------------------------------------------------------------------

def iwd_objects():
    """{path: {interface: {property: value}}} or None if iwd does not respond."""
    r = busctl("call", IWD, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")
    if r is None:
        return None
    # "call" returns the list of return values; here, one: the dictionary
    return r[0] if isinstance(r, list) and r and isinstance(r[0], dict) else {}


def wifi_devices(objs):
    devs = []
    for path, ifaces in sorted(objs.items()):
        dev = ifaces.get(IWD + ".Device")
        if not dev:
            continue
        st = ifaces.get(IWD + ".Station") or {}
        net = objs.get(st.get("ConnectedNetwork", ""), {}).get(IWD + ".Network", {})
        devs.append({"path": path, "name": dev.get("Name"), "powered": dev.get("Powered"),
                     "mode": dev.get("Mode"), "state": st.get("State"),
                     "scanning": st.get("Scanning", False), "ssid": net.get("Name"),
                     "station": bool(st)})
    return devs


def pick_device(objs, name=None):
    devs = wifi_devices(objs)
    if name:
        devs = [d for d in devs if d["name"] == name]
    else:  # one in station mode before one in access point mode
        devs.sort(key=lambda d: d["mode"] != "station")
    return devs[0] if devs else None


def ensure_powered(dev):
    if dev["powered"]:
        return True
    busctl("set-property", IWD, dev["path"], IWD + ".Device", "Powered", "b", "true")
    for _ in range(20):
        objs = iwd_objects() or {}
        d = pick_device(objs, dev["name"])
        if d and d["powered"] and d["station"]:
            dev.update(d)
            return True
        time.sleep(0.25)
    return False


def no_wifi():
    if iwd_objects() is None:
        return fail("WIFI_NO_SERVICE", "Run: systemctl status iwd",
                    text="Wifi service (iwd) is not responding.")
    return fail("WIFI_NO_DEVICE", "Check the wifi card with: osyr-diag",
                text="No wifi device found.")


# --- status ------------------------------------------------------------------------

def internet_check():
    """DNS and Internet access, with short timeouts (it must not hang the bar)."""
    dns = inet = False
    try:
        socket.setdefaulttimeout(3)
        addr = socket.getaddrinfo("ccmai.org", 443, proto=socket.IPPROTO_TCP)[0][4]
        dns = True
        with socket.create_connection(addr[:2], timeout=3):
            inet = True
    except OSError:
        pass
    finally:
        socket.setdefaulttimeout(None)
    return dns, inet


def iface_list():
    out = []
    for a in ip_json("addr", "show"):
        name = a.get("ifname", "")
        if name == "lo" or not os.path.exists(f"/sys/class/net/{name}/device"):
            continue  # physical interfaces only
        kind = "wifi" if os.path.exists(f"/sys/class/net/{name}/wireless") else "ethernet"
        ipv4 = [f'{i["local"]}/{i["prefixlen"]}' for i in a.get("addr_info", [])
                if i.get("family") == "inet"]
        out.append({"name": name, "type": kind, "state": a.get("operstate", "").lower(),
                    "mac": a.get("address"), "ipv4": ipv4})
    return out


def status(check_internet=True):
    ifaces = iface_list()
    objs = iwd_objects() or {}
    for d in wifi_devices(objs):
        for i in ifaces:
            if i["name"] == d["name"]:
                i.update({"ssid": d["ssid"], "wifi_state": d["state"], "powered": d["powered"]})
    route = ip_json("route", "show", "default")
    default = {"iface": route[0].get("dev"), "gateway": route[0].get("gateway")} if route else None
    data = {"interfaces": ifaces, "default_route": default}
    if check_internet:
        data["dns"], data["internet"] = internet_check() if default else (False, False)

    if not ifaces:
        return fail("NET_NO_DEVICE", "Check the network hardware with: osyr-diag", data,
                    text="No network devices found.")
    lines = []
    for i in ifaces:
        extra = ""
        if i["type"] == "wifi":
            extra = f' "{i["ssid"]}"' if i.get("ssid") else f' ({i.get("wifi_state") or "off"})'
        lines.append(f'{i["name"]:<10} {i["type"]:<9} {i["state"]:<6} '
                     f'{", ".join(i["ipv4"]) or "-"}{extra}')
    if default is None:
        wifi = [i for i in ifaces if i["type"] == "wifi"]
        hint = "Run: osyr net wifi scan" if wifi else "Plug in a network cable."
        return Result(False, "NET_NO_LINK", data, hint, text="\n".join(lines + ["Not connected."]))
    if check_internet and not data["internet"]:
        code = "NET_NO_DNS" if not data["dns"] else "NET_NO_INTERNET"
        return Result(False, code, data, "Run: osyr-diag",
                      text="\n".join(lines + ["Connected, but no Internet access."]))
    tail = ["Internet: OK"] if check_internet else []
    return Result(True, "OK", data, text="\n".join(lines + tail))


# --- wifi ----------------------------------------------------------------------------

def networks(objs, dev):
    r = busctl("call", IWD, dev["path"], IWD + ".Station", "GetOrderedNetworks")
    nets = []
    for path, signal in (r[0] if isinstance(r, list) and r else []):
        n = objs.get(path, {}).get(IWD + ".Network")
        if not n:
            continue
        nets.append({"ssid": n.get("Name"), "security": n.get("Type"),
                     "signal_dbm": round(signal / 100), "known": "KnownNetwork" in n,
                     "connected": bool(n.get("Connected"))})
    return nets


def _ssid_key(ssid):
    return "".join(ch for ch in (ssid or "").lower() if ch.isalnum())


def match_ssid(nets, ssid):
    """The network asked for: exact or, failing that, the only one that matches ignoring
    case, spaces and punctuation (the model or voice writes "livebox 6s f406")."""
    exact = next((n for n in nets if n["ssid"] == ssid), None)
    if exact or not _ssid_key(ssid):
        return exact
    near = [n for n in nets if _ssid_key(n["ssid"]) == _ssid_key(ssid)]
    return near[0] if len(near) == 1 else None


def bars(dbm):
    return "▂▄▆█"[:max(1, min(4, (dbm + 90) // 10))]


def _wait_scan(dev, want, seconds):
    """Waits for Station.Scanning to be `want` (and for the station to exist)."""
    end = time.time() + seconds
    while time.time() < end:
        d = pick_device(iwd_objects() or {}, dev["name"])
        if d and d["station"] and d["scanning"] == want:
            return True
        time.sleep(0.25)
    return False


def scan(dev, tries=2):
    """Scans and returns the networks. On the first boot of a slow laptop
    (HP 15-ba, rtl8723be) the station takes a while to appear, iwd is busy with its
    own autoconnect scan (Scan answers Busy) and a full scan takes over 10 s: wait
    for it to start and to finish, and if nothing shows up, repeat."""
    nets = []
    for _ in range(tries):
        _wait_scan(dev, False, 15)  # station ready and no scan in progress
        started = busctl("call", IWD, dev["path"], IWD + ".Station", "Scan") is not None
        if started:
            _wait_scan(dev, True, 3)  # may be missed if it finishes very quickly
        _wait_scan(dev, False, 30)
        nets = networks(iwd_objects() or {}, dev)
        if nets:
            break
    return nets


def wifi_scan(args):
    objs = iwd_objects()
    dev = pick_device(objs or {}, args.device)
    if not dev:
        return no_wifi()
    if not ensure_powered(dev):
        return fail("WIFI_BLOCKED", "Check the wifi switch or airplane mode key.",
                    text="The wifi device cannot be turned on.")
    nets = scan(dev)
    data = {"device": dev["name"], "networks": nets}
    if not nets:
        return Result(True, "WIFI_NONE_FOUND", data, "Move closer to the router and scan again.",
                      text="No wifi networks found.")
    lines = [f'{"*" if n["connected"] else " "} {bars(n["signal_dbm"]):<4} '
             f'{n["security"]:<6} {n["ssid"]}' for n in nets]
    return Result(True, "OK", data, "Run: osyr net wifi connect <ssid>", text="\n".join(lines))


def wait_state(name, want, seconds):
    end = time.time() + seconds
    while time.time() < end:
        d = pick_device(iwd_objects() or {}, name)
        if d and d["state"] in want:
            return d
        time.sleep(0.5)
    return pick_device(iwd_objects() or {}, name)


def wait_ipv4(name, seconds):
    end = time.time() + seconds
    while time.time() < end:
        for a in ip_json("addr", "show", "dev", name):
            for i in a.get("addr_info", []):
                if i.get("family") == "inet":
                    return i["local"]
        time.sleep(0.5)
    return None


def auth_problem(reason):
    r = reason.lower()
    return "passphrase" in r or "auth" in r or "operation failed" in r


IWCTL_ERROR = re.compile(rb"(Operation [a-z ]+|Invalid [^\r\n]*|[^\r\n]*(?:failed|not found|Aborted)[^\r\n]*)")


def iwctl_asking(dev_name, ssid, password, timeout=45):
    """Connects with iwctl by answering its prompt ("Passphrase:") on a pseudo-terminal:
    with --passphrase the password would show in the process list (ps,
    /proc/<pid>/cmdline) while it connects. iwctl only asks in interactive mode
    and only accepts input typed letter by letter (all at once it answers
    "Operation aborted"; checked with iwd 3.12). Returns (rc, reason) like run()."""
    import pty
    import select
    try:
        pid, fd = pty.fork()
    except OSError as e:
        return 1, str(e)
    if pid == 0:  # child: interactive iwctl with the pseudo-terminal as its terminal
        try:
            os.execvpe("iwctl", ["iwctl"], dict(os.environ, TERM="dumb", COLUMNS="200"))
        finally:
            os._exit(127)

    def typed(text):
        for ch in text.encode():
            os.write(fd, bytes([ch]))
            time.sleep(0.02)

    out, phase, error, mark = b"", "start", None, 0
    deadline = time.monotonic() + timeout
    next_check = 0
    try:
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.3)[0]:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:  # EIO: iwctl has finished
                    break
                if not chunk:
                    break
                out += chunk
            if phase == "start" and b"[iwd]" in out:
                q = ssid.replace("\\", "\\\\").replace('"', '\\"')
                typed(f'station {dev_name} connect "{q}"\r')
                phase, mark = "connect", len(out)
            elif phase == "connect" and b"Passphrase:" in out[mark:]:
                time.sleep(0.2)
                typed(password + "\r")
                phase, mark = "sent", len(out)
            if phase in ("connect", "sent"):
                m = IWCTL_ERROR.search(re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", out[mark:]))
                if m:
                    error = m.group(1).decode(errors="replace").strip()
                    break
                if time.monotonic() >= next_check:  # no error: already connected?
                    next_check = time.monotonic() + 1
                    d = pick_device(iwd_objects() or {}, dev_name)
                    if d and d["state"] == "connected" and d["ssid"] == ssid:
                        break
        else:
            error = "timed out"
    finally:
        try:
            os.write(fd, b"quit\r")
        except OSError:
            pass
        end = time.monotonic() + 3
        done = 0
        while time.monotonic() < end:
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.1)
        if not done:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        os.close(fd)
    if phase == "start" and not error:
        error = "iwctl did not start"
    return (1, error) if error else (0, "")


def attempt(dev, ssid, password):
    """One attempt with iwctl; returns (connected device or None, reason)."""
    if password is not None and not (8 <= len(password.encode()) <= 63
                                     or re.fullmatch(r"[0-9a-fA-F]{64}", password)):
        return None, "the passphrase must have 8 to 63 characters"
    if password is None:
        cmd = ["iwctl", "--dont-ask", "station", dev["name"], "connect", ssid]  # no credentials: fail, do not wait
        rc, out, err = run(cmd, timeout=45)
    else:
        rc, err = iwctl_asking(dev["name"], ssid, password)
        out = ""
    d = wait_state(dev["name"], ("connected",), 5 if rc else 20)
    if d and d["state"] == "connected" and d["ssid"] == ssid:
        return d, ""
    # iwctl colours its output: without ANSI codes, the last line with text
    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out + err)
    msg = [m.strip() for m in plain.splitlines() if m.strip()]
    return None, msg[-1] if msg else "connection failed"


def wifi_connect(args):
    objs = iwd_objects()
    dev = pick_device(objs or {}, args.device)
    if not dev:
        return no_wifi()
    if not ensure_powered(dev):
        return fail("WIFI_BLOCKED", "Check the wifi switch or airplane mode key.",
                    text="The wifi device cannot be turned on.")
    nets = networks(iwd_objects() or {}, dev)
    if not match_ssid(nets, args.ssid):
        nets = scan(dev)  # without a recent scan, iwd does not know it yet
    target = match_ssid(nets, args.ssid)
    if target is None:
        return fail("WIFI_NOT_FOUND", "Run: osyr net wifi scan", {"ssid": args.ssid},
                    text=f'Network "{args.ssid}" not found.')
    args.ssid = target["ssid"]
    if target["connected"]:
        ip = wait_ipv4(dev["name"], 1)
        return Result(True, "OK", {"ssid": args.ssid, "device": dev["name"], "ipv4": ip},
                      text=f'Already connected to "{args.ssid}".')

    password = args.password
    if args.password_stdin:  # from another program (chat, first boot): never on the command line
        password = sys.stdin.readline().rstrip("\r\n") or None
    needs = target["security"] in ("psk", "wep") and (not target["known"] or args.ask)
    if needs and password is None:
        if args.json or not sys.stdin.isatty():
            return fail("WIFI_NEEDS_PASSWORD",
                        f"Run in a terminal: osyr net wifi connect '{args.ssid}' (it asks for the password)",
                        {"ssid": args.ssid}, text=f'"{args.ssid}" needs a password.')
        password = getpass.getpass(f'Password for "{args.ssid}": ')
    if target["security"] == "8021x":
        return fail("WIFI_UNSUPPORTED", "Enterprise (802.1X) networks are not supported yet.",
                    {"ssid": args.ssid})

    # If a password is given, it is always handed over: if the saved one no longer
    # works (the router's changed), iwd asks the agent, uses this one and saves it.
    d, reason = attempt(dev, args.ssid, password)
    if d is None:
        if needs or auth_problem(reason):
            code, hint = "WIFI_AUTH_FAILED", "Check the password and try again."
            if target["known"] and not needs:
                hint = f"Run in a terminal: osyr net wifi connect '{args.ssid}' --ask (it asks for the new password)"
        else:
            code, hint = "WIFI_CONNECT_FAILED", "Run: osyr-diag"
        return fail(code, hint, {"ssid": args.ssid, "reason": reason},
                    text=f'Could not connect to "{args.ssid}": {reason}')
    ip = wait_ipv4(dev["name"], 15)
    data = {"ssid": args.ssid, "device": dev["name"], "ipv4": ip}
    if not ip:
        return Result(False, "NET_NO_IP", data, "Run: osyr-diag",
                      text=f'Connected to "{args.ssid}" but got no IP address.')
    return Result(True, "OK", data, text=f'Connected to "{args.ssid}" ({ip}).')


def wifi_disconnect(args):
    dev = pick_device(iwd_objects() or {}, args.device)
    if not dev:
        return no_wifi()
    if dev["state"] != "connected":
        return Result(True, "OK", {"device": dev["name"]}, text="Not connected.")
    busctl("call", IWD, dev["path"], IWD + ".Station", "Disconnect")
    wait_state(dev["name"], ("disconnected",), 5)
    return Result(True, "OK", {"device": dev["name"], "ssid": dev["ssid"]},
                  text=f'Disconnected from "{dev["ssid"]}".')


def register(sub):
    p = sub.add_parser("net", help="network: status and wifi")
    s = p.add_subparsers(dest="action", required=True)
    s.add_parser("status", help="interfaces, addresses and Internet access").set_defaults(
        func=lambda a: status())
    w = s.add_parser("wifi", help="wifi networks").add_subparsers(dest="wifi_action", required=True)
    sc = w.add_parser("scan", help="list nearby networks")
    sc.add_argument("--device")
    sc.set_defaults(func=wifi_scan)
    c = w.add_parser("connect", help="connect to a network")
    c.add_argument("ssid")
    c.add_argument("--password", help="visible to other programs while it connects: better let it ask")
    c.add_argument("--password-stdin", action="store_true", help="read the password from the first line of stdin")
    c.add_argument("--ask", action="store_true", help="ask for the password even if the network is saved")
    c.add_argument("--device")
    c.set_defaults(func=wifi_connect)
    d = w.add_parser("disconnect", help="disconnect from the current network")
    d.add_argument("--device")
    d.set_defaults(func=wifi_disconnect)
