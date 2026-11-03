"""osyr remind — reminders and alarms: on-screen and spoken notice.

Each reminder is a systemd user timer (systemd-run) with an absolute time, and
is recorded in ~/.local/share/osyr/reminders.json. systemd-run timers do not
survive a restart: on entering the session, 'osyr remind restore' (sway)
creates them again, and those that fell due while the computer was off fire
then, marked as missed.

When (what the user or the agent writes):
  10m, 2h, 1h30m, 45s        after that much time
  "in 10 minutes", "en 10 minutos"
  18:30                      today at that time (tomorrow if it has passed)
  "tomorrow 9:00", "mañana 9:00"
  "2026-10-30 09:00"
"""
import argparse
import json
import os
import re
import secrets
import subprocess
import time
from datetime import datetime, timedelta

from . import notify, voice
from .result import Result, fail

DATA = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                    "osyr", "reminders.json")
UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "segundo": 1, "segundos": 1,
         "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60, "minuto": 60, "minutos": 60,
         "h": 3600, "hour": 3600, "hours": 3600, "hora": 3600, "horas": 3600,
         "d": 86400, "day": 86400, "days": 86400, "dia": 86400, "día": 86400, "dias": 86400, "días": 86400}
FMT = "%Y-%m-%d %H:%M:%S"


def parse_when(text, now=None):
    """Absolute date and time, or ValueError with a message for the model."""
    now = now or datetime.now().replace(microsecond=0)
    t = text.strip().lower()
    t = re.sub(r"^(in|en|dentro de)\s+", "", t)
    t = re.sub(r"^(at|a las|a la)\s+", "", t)
    # Durations: 10m, 1h30m, "10 minutes", "1 hora y 20 minutos"
    parts = re.findall(r"(\d+)\s*([a-zíá]+)", t)
    if parts and re.fullmatch(r"(\s*(y|and|,)?\s*\d+\s*[a-zíá]+)+\s*", t):
        secs = 0
        for n, unit in parts:
            if unit not in UNITS:
                break
            secs += int(n) * UNITS[unit]
        else:
            if secs <= 0:
                raise ValueError("the time must be in the future")
            return now + timedelta(seconds=secs)
    day = now.date()
    m = re.fullmatch(r"(tomorrow|mañana)\s+(\d{1,2})[:.h](\d{2})", t)
    if m:
        day, hh, mm = day + timedelta(days=1), int(m.group(2)), int(m.group(3))
        return datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
    m = re.fullmatch(r"(\d{1,2})[:.h](\d{2})", t)
    if m:
        when = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0)
        return when if when > now else when + timedelta(days=1)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            when = datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
        if when <= now:
            raise ValueError("that time has already passed")
        return when
    raise ValueError(f"'{text}' is not a time: use e.g. 10m, 2h, 18:30, 'tomorrow 9:00' "
                     "or '2026-10-30 09:00'")


def load():
    try:
        with open(DATA) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save(items):
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    tmp = DATA + ".tmp"
    with open(tmp, "w") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA)


def unit(rid):
    return f"osyr-remind-{rid}"


def schedule(r):
    """Creates the user timer; True if systemd accepted it."""
    p = subprocess.run(["systemd-run", "--user", "--quiet", f"--unit={unit(r['id'])}",
                        f"--on-calendar={r['when']}", "--timer-property=AccuracySec=1s",
                        # without this, when 'remind fire' exited systemd killed the voice that had just started
                        "--property=ExitType=cgroup",
                        "/usr/bin/osyr", "remind", "fire", r["id"]],
                       capture_output=True, text=True, timeout=15)
    return p.returncode == 0, p.stderr.strip()


def timer_active(rid):
    return subprocess.run(["systemctl", "--user", "-q", "is-active", unit(rid) + ".timer"]).returncode == 0


def pending(items):
    return [r for r in items if not r.get("done")]


def add(args):
    text = " ".join(args.text).strip()
    if not text:
        return fail("REMIND_NO_TEXT", "Run: osyr remind add <when> <text>")
    try:
        when = parse_when(args.when)
    except ValueError as e:
        return fail("REMIND_BAD_TIME", str(e), {"when": args.when})
    r = {"id": secrets.token_hex(3), "when": when.strftime(FMT), "text": text}
    ok, err = schedule(r)
    if not ok:
        return fail("REMIND_FAILED", "Run it from the graphical session.", {"error": err})
    items = load()
    items.append(r)
    save(items)
    return Result(True, "OK", r, text=f'Reminder {r["id"]} at {r["when"][:16]}: {text}')


def list_reminders(args):
    items = sorted(pending(load()), key=lambda r: r["when"])
    if not items:
        return Result(True, "OK", {"reminders": []}, text="No reminders.")
    return Result(True, "OK", {"reminders": items},
                  text="\n".join(f'{r["id"]}  {r["when"][:16]}  {r["text"]}' for r in items))


def cancel(args):
    items = load()
    r = next((r for r in pending(items) if r["id"] == args.id), None)
    if r is None:
        return fail("REMIND_UNKNOWN", "Run: osyr remind list", {"id": args.id})
    subprocess.run(["systemctl", "--user", "stop", unit(r["id"]) + ".timer"], capture_output=True)
    r["done"] = "cancelled"
    save(items)
    return Result(True, "OK", r, text=f'Cancelled: {r["text"]}')


def announce(r, missed=False):
    title = "Reminder" + (" (missed while off)" if missed else "")
    notify.send(title, r["text"], "critical", timeout_ms=0)  # stays until closed
    lang = os.environ.get("LANG", "es")[:2]
    voice.speak(("Recordatorio: " if lang == "es" else "Reminder: ") + r["text"], lang, wait=True)
    time.sleep(0.2)  # let the voice start before the process ends


def fire(args):
    items = load()
    r = next((r for r in pending(items) if r["id"] == args.id), None)
    if r is None:
        return Result(True, "OK", text="Nothing to do.")
    announce(r)
    r["done"] = "fired"
    save(items)
    return Result(True, "OK", r, text=r["text"])


def restore(args):
    """On entering the session: recreates the timers and reports the missed ones."""
    items = load()
    now = datetime.now().strftime(FMT)
    redone, missed = 0, 0
    for r in pending(items):
        if r["when"] <= now:
            announce(r, missed=True)
            r["done"] = "missed"
            missed += 1
        elif not timer_active(r["id"]):
            ok, _ = schedule(r)
            redone += ok
    # Done ones older than a week are dropped
    week = (datetime.now() - timedelta(days=7)).strftime(FMT)
    items = [r for r in items if not r.get("done") or r["when"] >= week]
    save(items)
    return Result(True, "OK", {"rescheduled": redone, "missed": missed},
                  text=f"{redone} rescheduled, {missed} missed")


def register(sub):
    p = sub.add_parser("remind", help="reminders and alarms (on screen and spoken)")
    s = p.add_subparsers(dest="action", required=True)
    a = s.add_parser("add", help="new reminder: osyr remind add 10m take the pizza out")
    a.add_argument("when", help="10m, 2h, 18:30, 'tomorrow 9:00', '2026-10-30 09:00'")
    a.add_argument("text", nargs="+")
    a.set_defaults(func=add)
    s.add_parser("list", help="pending reminders").set_defaults(func=list_reminders)
    c = s.add_parser("cancel", help="cancel a reminder by its id")
    c.add_argument("id")
    c.set_defaults(func=cancel)
    f = s.add_parser("fire", help=argparse.SUPPRESS)
    f.add_argument("id")
    f.set_defaults(func=fire)
    s.add_parser("restore", help=argparse.SUPPRESS).set_defaults(func=restore)
