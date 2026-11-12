"""Desktop notifications (mako) over standard D-Bus, without libnotify."""
import os
import pwd
import subprocess

URGENCY = {"low": "0", "normal": "1", "critical": "2"}


def send(title, body="", urgency="normal", timeout_ms=8000):
    env = dict(os.environ)
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    p = subprocess.run(
        ["busctl", "--user", "call", "org.freedesktop.Notifications",
         "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify",
         "susssasa{sv}i", "OSYR", "0", "", title, body, "0",
         "1", "urgency", "y", URGENCY.get(urgency, "1"), str(timeout_ms)],
        capture_output=True, env=env, timeout=10)
    return p.returncode == 0


def send_to_user(title, body="", urgency="normal", user="osyr", timeout_ms=10000):
    """From a system service (root): to the graphical session of 'user'.
    If there is no session (no user bus), it does nothing."""
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return False
    uid = pw.pw_uid
    bus = f"/run/user/{uid}/bus"
    if not os.path.exists(bus):
        return False
    p = subprocess.run(
        ["busctl", "--user", "call", "org.freedesktop.Notifications",
         "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify",
         "susssasa{sv}i", "OSYR", "0", "", title, body, "0",
         "1", "urgency", "y", URGENCY.get(urgency, "1"), str(timeout_ms)],
        capture_output=True, timeout=15, user=uid, group=pw.pw_gid, extra_groups=[],
        env={"DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus}", "XDG_RUNTIME_DIR": f"/run/user/{uid}",
             "HOME": pw.pw_dir, "PATH": "/usr/bin:/bin"})
    return p.returncode == 0
