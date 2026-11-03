"""live — apply a code update without restarting the computer.

After copying the files, 'osyr update code apply|rollback' (root) looks at what
changed and:
  - restarts only the affected system services (and systemd-resolved);
  - leaves in SESSION_FILE what has to be relaunched in the user's session (sway
    and its bar, the idle lock, notifications, the chat), which the chat does on
    returning from /update (it is what runs in the session);
  - asks for a restart if something read at boot changed (disks, accounts,
    consoles, kernel modules...) and also for any file it does not know: the
    safe default is to restart (udev, iwd, network, sysctl, PAM...).
The commands and their library (usr/bin, usr/lib/osyr, usr/share, etc/osyr) need
nothing: each command loads the new code.
"""
import json
import os
import subprocess
import time

SESSION_FILE = os.environ.get("OSYR_SESSION_RELOAD", "/run/osyr/session-reload")  # (tests)
LIB = "usr/lib/osyr/osyr/"

# Read at boot: these do ask for a restart
REBOOT_PREFIXES = (
    "etc/fstab", "etc/crypttab", "etc/modprobe.d/", "etc/modules-load.d/",
    "etc/systemd/system/getty@", "etc/systemd/system/serial-getty@",
    "etc/systemd/system/emergency.service.d/", "etc/systemd/system/osyr-zram",
    "usr/bin/osyr-zram", "etc/systemd/system-preset/", "etc/systemd/logind.conf",
    "etc/systemd/journald.conf", "etc/systemd/system.conf", "etc/default/",
    "usr/bin/osyr-session", "etc/osyr/zram",
)

# Read on each use: nothing to relaunch unless they are in the tables below
PER_USE_PREFIXES = ("usr/bin/", "usr/lib/osyr/", "usr/share/", "etc/osyr/",
                    "etc/swaylock/", "etc/xdg/foot/", "etc/sudoers.d/", "etc/pam.d/")

# Services that keep running with what they loaded: file (or prefix) -> unit
SERVICE_FILES = {
    "usr/bin/osyr-llama": ["osyr-llama.service", "osyr-llama-vision.service"],
    "etc/osyr/llama.conf": ["osyr-llama.service", "osyr-llama-vision.service"],
    "usr/lib/osyr/osyr-diskwatch": ["osyr-diskwatch.service"],
    "etc/nftables.conf": ["osyr-firewall.service"],
    "etc/nftables.d/": ["osyr-firewall.service"],
    LIB + "power.py": ["osyr-power.service"],
    "etc/osyr/power.conf": ["osyr-power.service"],
}

# Session processes that load something when they start
SESSION_FILES = {
    "etc/sway/": "sway",
    "etc/osyr/theme/": "sway",
    "usr/bin/osyr-status": "sway",   # the bar: 'swaymsg reload' relaunches it
    "usr/bin/osyr-idle": "idle",   # (not etc/swaylock: swaylock reads it on every lock)
    "etc/xdg/mako/": "mako",
    "usr/bin/osyr-chat": "chat",
}


def _match(path, table):
    return [v for k, v in table.items() if path == k or (k.endswith("/") and path.startswith(k))]


def plan(touched):
    """What to do with the changed files (paths relative to /)."""
    units, session, reboot = [], [], []
    daemon_reload = resolved = False
    for p in touched:
        if p.endswith(".osyr-new"):
            continue  # copy next to a file with local changes: nobody reads it
        if any(p.startswith(r) for r in REBOOT_PREFIXES):
            reboot.append("/" + p)
            continue
        known = False
        for us in _match(p, SERVICE_FILES):
            units += us
            known = True
        hits = _match(p, SESSION_FILES)
        session += hits
        known = known or bool(hits)
        if p.startswith(LIB):
            # the bar and the chat import the library, and so does the disk watcher (notify)
            session += ["sway", "chat"]
            units.append("osyr-diskwatch.service")
        if p.startswith("etc/systemd/resolved.conf"):
            resolved = known = True
        elif p.startswith("etc/systemd/system/"):
            daemon_reload = known = True
            name = p.split("/")[3]
            unit = name[:-2] if name.endswith(".d") else name
            if unit.endswith((".service", ".socket", ".timer")) and "@" not in unit:
                units.append(unit)
        if not known and not p.startswith(PER_USE_PREFIXES):
            reboot.append("/" + p)  # unknown: the safe choice is to restart

    def dedup(xs):
        return list(dict.fromkeys(xs))
    return {"units": dedup(units), "session": dedup(session), "reboot": dedup(reboot),
            "daemon_reload": daemon_reload, "resolved": resolved}


def _unit_state(u):
    r = subprocess.run(["systemctl", "show", "-p", "Type,ActiveState,UnitFileState", u],
                       capture_output=True, text=True)
    return dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)


def apply_system(p):
    """The part that needs root. Returns (what was relaunched, whether the note for the session was left)."""
    done = []
    if p["daemon_reload"]:
        subprocess.run(["systemctl", "daemon-reload"], capture_output=True)
    for u in p["units"]:
        st = _unit_state(u)
        if st.get("ActiveState") in ("active", "activating", "reloading"):
            pass  # running: relaunch it
        elif st.get("Type") == "oneshot" and st.get("UnitFileState") == "enabled":
            pass  # oneshot without RemainAfterExit (osyr-power): it stays inactive, it has to be run again
        else:
            continue  # stopped (vision starts on demand): it will load the new code
        r = subprocess.run(["systemctl", "restart", "--no-block", u], capture_output=True)
        if r.returncode == 0:
            done.append(u.rsplit(".", 1)[0])
    if p["resolved"]:
        subprocess.run(["systemctl", "try-restart", "systemd-resolved.service"], capture_output=True)
        done.append("DNS")
    return done, write_session(p["session"])


def write_session(actions):
    """For the chat: what to relaunch in the session (read on returning from /update). False if
    it could not be written (then the session part asks for a restart)."""
    try:
        os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
        tmp = SESSION_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"id": time.time_ns(), "actions": actions}, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, SESSION_FILE)
        return True
    except OSError:
        return False


def read_session():
    try:
        with open(SESSION_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def applied_path():
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "osyr", "session-applied")


def pending_session():
    """What 'osyr update' wrote that this session has not applied yet (also if the update
    was run from a terminal): (id, actions) or (None, [])."""
    s = read_session()
    try:
        done = int(open(applied_path()).read().strip() or 0)
    except (OSError, ValueError):
        done = 0
    if s.get("id") and int(s["id"]) > done:
        return s["id"], s.get("actions") or []
    return None, []


def mark_applied(sid):
    try:
        os.makedirs(os.path.dirname(applied_path()), exist_ok=True)
        with open(applied_path(), "w") as f:
            f.write(str(sid))
    except OSError:
        pass


def apply_session(actions):
    """In the user's session (called by the chat). Returns True if the chat must relaunch itself."""
    if "sway" in actions:
        subprocess.run(["swaymsg", "reload"], capture_output=True)
    if "idle" in actions:
        from . import ui
        ui.relaunch_idle()
    if "mako" in actions:
        subprocess.run(["makoctl", "reload"], capture_output=True)
    return "chat" in actions
