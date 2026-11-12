"""Automatic power profile: on battery, power saving; plugged in, performance.

osyr-power.service applies it at boot and udev runs it again when the power
source changes (90-osyr-power.rules). It only acts if the profile changes, so
the battery's periodic events do nothing. On battery:

- CPU: energy preference (EPP) "balance_power" if the driver supports it
  (intel_pstate, amd-pstate-epp); plugged in, "balance_performance".
  Without EPP the governor is left alone: the generic ones save little or slow down a lot.
- Screen: the brightness drops to BATTERY_BRIGHTNESS if it was above it, and
  goes back to the previous level when plugged in, unless the user changed it.
- Model: fewer threads (BATTERY_LLAMA_THREADS). llama-server cannot change them
  while running: it is restarted, but only if it is not answering; if it is,
  it retries a minute later (systemd-run). While it reloads, the bar says AI LOADING.

Configuration in /etc/osyr/power.conf. State in /run/osyr/power.json.
"""
import argparse
import glob
import json
import os
import subprocess
import urllib.error
import urllib.request

from . import notify, ui
from .result import Result, fail

SYSFS = os.environ.get("OSYR_SYSFS", "/sys")
CONF = os.environ.get("OSYR_POWER_CONF", "/etc/osyr/power.conf")
RUN = os.environ.get("OSYR_POWER_RUN", "/run/osyr")
STATE = os.path.join(RUN, "power.json")
LLAMA_OVERRIDE = os.path.join(RUN, "llama-power.conf")  # read by osyr-llama
LLAMA_PORT = os.environ.get("OSYR_LLAMA_PORT", "8083")
EPP = {"battery": "balance_power", "ac": "balance_performance"}
DEFAULTS = {"ENABLED": "yes", "BATTERY_BRIGHTNESS": "60", "BATTERY_LLAMA_THREADS": "auto"}


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _write(path, value):
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except OSError:
        return False


def config():
    c = dict(DEFAULTS)
    for line in (_read(CONF) or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            c[k.strip()] = v.strip().strip('"')
    return c


def on_ac():
    """True when plugged in (or without a battery), False on battery."""
    supplies = glob.glob(os.path.join(SYSFS, "class", "power_supply", "*"))
    mains = [s for s in supplies if _read(os.path.join(s, "type")) in ("Mains", "USB")
             and _read(os.path.join(s, "online")) is not None]
    if any(_read(os.path.join(s, "online")) == "1" for s in mains):
        return True
    bats = [s for s in supplies if _read(os.path.join(s, "type")) == "Battery"]
    if not bats:
        return True  # desktop or VM
    if mains:
        return False
    # No charger visible: the battery status tells
    return not any(_read(os.path.join(b, "status")) == "Discharging" for b in bats)


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(RUN, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


# --- CPU ---------------------------------------------------------------------------------

def set_epp(profile):
    """Sets the energy preference on every policy; returns the one set, or None."""
    want, done = EPP[profile], None
    for pol in glob.glob(os.path.join(SYSFS, "devices", "system", "cpu", "cpufreq", "policy*")):
        avail = (_read(os.path.join(pol, "energy_performance_available_preferences")) or "").split()
        if want in avail and _write(os.path.join(pol, "energy_performance_preference"), want):
            done = want
    return done


# --- Screen --------------------------------------------------------------------------------

def set_brightness(profile, st, cfg):
    dev = ui.backlight()
    if dev is None:
        return None
    try:
        target = max(ui.MIN_PERCENT, min(100, int(cfg["BATTERY_BRIGHTNESS"])))
    except ValueError:
        return None
    if profile == "battery":
        if dev["percent"] <= target:
            return None
        st["brightness_before"], st["brightness_set"] = dev["percent"], target
        ui.set_value(dev, max(1, round(target * dev["max"] / 100)))
        return target
    before, mine = st.pop("brightness_before", None), st.pop("brightness_set", None)
    if before is None or dev["percent"] != mine:
        return None  # the user changed it on battery: leave it
    ui.set_value(dev, max(1, round(before * dev["max"] / 100)))
    return before


# --- Model ---------------------------------------------------------------------------------

def physical_cores():
    cores = set()
    for cpu in glob.glob(os.path.join(SYSFS, "devices", "system", "cpu", "cpu[0-9]*", "topology")):
        cores.add((_read(os.path.join(cpu, "physical_package_id")), _read(os.path.join(cpu, "core_id"))))
    return len(cores) or os.cpu_count() or 1


def llama_threads(cfg):
    v = cfg["BATTERY_LLAMA_THREADS"]
    if v in ("", "keep", "no"):
        return None
    if v == "auto":
        return max(1, physical_cores() // 2)
    try:
        return max(1, int(v))
    except ValueError:
        return None


def llama_busy():
    """True if it is answering or unknown; False if idle; None if it is not running."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{LLAMA_PORT}/slots", timeout=3) as r:
            slots = json.load(r)
    except urllib.error.HTTPError:
        return True
    except (OSError, ValueError):
        return None
    return any(s.get("is_processing") for s in slots if isinstance(s, dict))


def set_llama(profile, cfg):
    """Writes the threads for osyr-llama; returns ('restarted'|'later'|'next-start'|None, threads)."""
    n = llama_threads(cfg) if profile == "battery" else None
    old = _read(LLAMA_OVERRIDE)
    new = f"THREADS={n}\nTHREADS_BATCH={n}\n" if n else None
    if (old or None) == new:
        return None, n
    os.makedirs(RUN, exist_ok=True)
    if new:
        _write(LLAMA_OVERRIDE, new)
    else:
        try:
            os.unlink(LLAMA_OVERRIDE)
        except OSError:
            pass
    return restart_llama_when_idle(), n


def restart_llama_when_idle():
    busy = llama_busy()
    if busy is None:
        return "next-start"  # not running: it will read it when it starts
    if busy:
        # Answering: try again in a minute, without cutting the answer short
        subprocess.run(["systemd-run", "--quiet", "--on-active=60", "--unit=osyr-power-retry",
                        "/usr/bin/osyr", "sys", "power", "llama-restart"], capture_output=True)
        return "later"
    subprocess.run(["systemctl", "restart", "--no-block", "osyr-llama.service"], capture_output=True)
    return "restarted"


# --- Commands ------------------------------------------------------------------------------

def apply(args):
    if os.geteuid() != 0 and "OSYR_POWER_RUN" not in os.environ:
        return fail("NEEDS_ROOT", "Run: sudo osyr sys power apply", text="Needs administrator rights.")
    cfg = config()
    if cfg["ENABLED"] != "yes":
        return Result(True, "POWER_DISABLED", text="Automatic power profile disabled in " + CONF)
    profile = args.profile if getattr(args, "profile", None) in ("battery", "ac") else \
        ("ac" if on_ac() else "battery")
    st = load_state()
    if st.get("profile") == profile and not getattr(args, "force", False):
        return Result(True, "OK", st, text=f"Already on the {profile} profile.")
    first = "profile" not in st
    st["profile"] = profile
    done = {"profile": profile, "epp": set_epp(profile)}
    done["brightness"] = set_brightness(profile, st, cfg)
    done["llama"], done["llama_threads"] = set_llama(profile, cfg)
    save_state(st)
    if not first:  # no notice at boot: nothing has changed for the user
        what = []
        if done["epp"]:
            what.append("CPU " + ("power saving" if profile == "battery" else "full speed"))
        if done["brightness"]:
            what.append(f'brightness {done["brightness"]}%')
        if done["llama"] in ("restarted", "later"):
            what.append("AI with " + (f'{done["llama_threads"]} threads' if done["llama_threads"] else "all threads")
                        + (" (reloading)" if done["llama"] == "restarted" else " (after this answer)"))
        notify.send_to_user("On battery: saving power" if profile == "battery" else "Plugged in: full power",
                            ", ".join(what) or "")
    return Result(True, "OK", done, text=f"{profile} profile: " + json.dumps(done))


def llama_restart(args):
    """systemd-run retry: restarts the model if it is now idle."""
    r = restart_llama_when_idle()
    return Result(True, "OK", {"llama": r}, text=f"llama: {r}")


def show(args):
    st = load_state()
    data = {"on_ac": on_ac(), "profile": st.get("profile"), "config": config(),
            "llama_override": _read(LLAMA_OVERRIDE)}
    epp = _read(os.path.join(SYSFS, "devices", "system", "cpu", "cpufreq", "policy0",
                             "energy_performance_preference"))
    data["epp"] = epp
    text = (f'{"Plugged in" if data["on_ac"] else "On battery"}; profile {data["profile"] or "not applied"}'
            + (f"; CPU preference {epp}" if epp else ""))
    return Result(True, "OK", data, text=text)


def register(s):
    p = s.add_parser("power", help="automatic power profile (battery / plugged in)")
    ps = p.add_subparsers(dest="power_action")
    p.set_defaults(func=show)
    a = ps.add_parser("apply", help="apply the profile for the current power source (root)")
    a.add_argument("--profile", choices=("auto", "battery", "ac"), default="auto")
    a.add_argument("--force", action="store_true", help="apply even if it did not change")
    a.set_defaults(func=apply)
    ps.add_parser("llama-restart", help=argparse.SUPPRESS).set_defaults(func=llama_restart)
