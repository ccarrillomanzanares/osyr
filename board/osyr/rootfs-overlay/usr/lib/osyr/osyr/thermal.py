"""CPU temperature and thermal throttling (osyr sys thermal).

The model runs the CPU flat out for minutes: a thin laptop heats up and lowers
the frequency (throttling), and the answer gets slower with no visible reason.
Used by the bar (osyr-status), the self-test and the agent.

Sources, best to worst: the CPU's hwmon (coretemp on Intel: "Package id 0";
k10temp/zenpower on AMD: Tctl/Tdie), the x86_pkg_temp thermal zone, and the
highest of the ACPI zones. Throttling comes from Intel's counters
(thermal_throttle/*_throttle_count, since boot); on AMD they do not exist and
only temperature and frequency are checked.
"""
import glob
import os

from .result import Result

SYSFS = os.environ.get("OSYR_SYSFS", "/sys")
HOT = 90      # °C: warn from here on
CPU_HWMON = ("coretemp", "k10temp", "zenpower")
PREFERRED_LABELS = ("package id 0", "tctl", "tdie")


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _milli(path):
    v = _read(path)
    try:
        return int(v) / 1000 if v is not None else None
    except ValueError:
        return None


def cpu_temp():
    """(°C, sensor, critical °C or None), or (None, None, None) if there is no sensor."""
    for hw in sorted(glob.glob(os.path.join(SYSFS, "class", "hwmon", "hwmon*"))):
        name = _read(os.path.join(hw, "name"))
        if name not in CPU_HWMON:
            continue
        temps = []
        for inp in sorted(glob.glob(os.path.join(hw, "temp*_input"))):
            base = inp[:-len("_input")]
            t = _milli(inp)
            if t is None:
                continue
            label = (_read(base + "_label") or "").lower()
            crit = _milli(base + "_crit") or _milli(base + "_max")
            temps.append((label, t, crit))
        if not temps:
            continue
        pick = next((x for x in temps if x[0] in PREFERRED_LABELS), None) or max(temps, key=lambda x: x[1])
        return pick[1], name, pick[2]
    zones = []
    for z in sorted(glob.glob(os.path.join(SYSFS, "class", "thermal", "thermal_zone*"))):
        kind, t = _read(os.path.join(z, "type")), _milli(os.path.join(z, "temp"))
        if t is not None and 0 < t < 150:
            zones.append((kind or "", t))
    pkg = next((t for k, t in zones if k == "x86_pkg_temp"), None)
    if pkg is not None:
        return pkg, "x86_pkg_temp", None
    if zones:
        kind, t = max(zones, key=lambda x: x[1])
        return t, kind, None
    return None, None, None


def throttle_count():
    """Times the CPU has been throttled since boot (Intel), or None."""
    total, found = 0, False
    packages = {}
    for cpu in glob.glob(os.path.join(SYSFS, "devices", "system", "cpu", "cpu[0-9]*")):
        tt = os.path.join(cpu, "thermal_throttle")
        core = _read(os.path.join(tt, "core_throttle_count"))
        if core is not None and core.isdigit():
            total += int(core)
            found = True
        pkg = _read(os.path.join(tt, "package_throttle_count"))
        pid = _read(os.path.join(cpu, "topology", "physical_package_id")) or "0"
        if pkg is not None and pkg.isdigit():
            packages[pid] = max(packages.get(pid, 0), int(pkg))  # the same on all its CPUs
            found = True
    return total + sum(packages.values()) if found else None


def freq_percent():
    """Current average frequency as % of the maximum, or None."""
    cur, mx = [], []
    for cpu in glob.glob(os.path.join(SYSFS, "devices", "system", "cpu", "cpu[0-9]*", "cpufreq")):
        c, m = _read(os.path.join(cpu, "scaling_cur_freq")), _read(os.path.join(cpu, "cpuinfo_max_freq"))
        if c and m and c.isdigit() and m.isdigit() and int(m):
            cur.append(int(c))
            mx.append(int(m))
    return round(100 * sum(cur) / sum(mx)) if mx else None


def status():
    t, sensor, crit = cpu_temp()
    return {"temp_c": round(t) if t is not None else None, "sensor": sensor,
            "crit_c": round(crit) if crit else None, "hot": t is not None and t >= HOT,
            "throttle_count": throttle_count(), "freq_percent": freq_percent()}


def thermal(args):
    s = status()
    if s["temp_c"] is None:
        return Result(True, "THERMAL_NO_SENSOR", s, text="No CPU temperature sensor (normal in VMs).")
    parts = [f'CPU {s["temp_c"]}°C ({s["sensor"]})']
    if s["freq_percent"] is not None:
        parts.append(f'speed {s["freq_percent"]}% of max')
    if s["throttle_count"]:
        parts.append(f'throttled {s["throttle_count"]} times since boot')
    hint = "Check that the air vents are not blocked; a hard, flat surface helps." if s["hot"] else None
    return Result(True, "THERMAL_HOT" if s["hot"] else "OK", s, hint, text=", ".join(parts))


def selftest_check():
    """For osyr sys selftest: (status, detail)."""
    s = status()
    if s["temp_c"] is None:
        return "ok", "no CPU temperature sensor (normal in VMs)"
    detail = f'CPU {s["temp_c"]}°C'
    if s["throttle_count"]:
        detail += f', throttled {s["throttle_count"]} times since boot (it slows the AI down)'
    if s["hot"]:
        return "warn", detail + ": too hot, check the air vents"
    return ("warn" if s["throttle_count"] else "ok"), detail
