"""osyr backup — copy of the home folder to a USB stick or external disk.

  osyr backup run <disk>        copies ~ to <disk>/OSYR-backup/<user> (rsync)
  osyr backup restore <disk>    brings back what was copied, deleting nothing in ~
  osyr backup status            whether a backup is running and when the last one was

The copy runs in the background and shows a notice when it finishes: the agent
and the terminal do not wait. The second time it only copies what changed. The
disk holds ordinary files that can be opened from any computer; that is why
folders with keys (the same ones osyr files does not read) are never copied,
and there is a warning if the home folder is encrypted and the copy will not be.
"""
import json
import os
import subprocess
import sys
import time

from . import disk, files
from .result import Result, fail

STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
                     "osyr", "backup.json")
HOME = os.path.expanduser("~")
EXCLUDE = [".cache/", ".local/share/Trash/", ".local/state/osyr/backup.json"] + [s + "/" for s in files.SECRETS]
FAT = ("vfat", "exfat", "ntfs3", "fuseblk")


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def store(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE + ".tmp", "w") as f:
        json.dump(state, f, indent=1)
    os.replace(STATE + ".tmp", STATE)


def running(state):
    pid = state.get("running", {}).get("pid")
    if not pid:
        return None
    try:
        os.kill(pid, 0)
        return state["running"]
    except OSError:
        return None


def encrypted_home():
    try:
        return "ENCRYPTED=yes" in open("/etc/osyr/install.conf").read()
    except OSError:
        return False


def target(name):
    d = next((d for d in disk.mounted() if d["name"] == name), None)
    if not d:
        return None, fail("DISK_NOT_CONNECTED", "Run: osyr disk list", {"name": name})
    return d, None


def rsync_cmd(src, dst, fs, restore=False):
    cmd = ["rsync", "-rt" if fs in FAT else "-a", "--partial"]
    if fs in FAT:
        cmd.append("--modify-window=2")  # FAT stores times with 2 s precision
    if restore:
        cmd.append("--update")           # does not overwrite anything newer in ~
    else:
        cmd += ["--delete", "--delete-excluded"] + [f"--exclude=/{e}" for e in EXCLUDE]
    return cmd + [src.rstrip("/") + "/", dst.rstrip("/") + "/"]


def start(args, restore):
    state = load()
    if running(state):
        return fail("BACKUP_RUNNING", "Run: osyr backup status", state["running"])
    d, err = target(args.disk)
    if err:
        return err
    dst = os.path.join(d["path"], "OSYR-backup", os.path.basename(HOME))
    if restore and not os.path.isdir(dst):
        return fail("BACKUP_NOT_FOUND", f"There is no OSYR backup on {args.disk}.", {"path": dst})
    src, to = (dst, HOME) if restore else (HOME, dst)
    lib = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    job = {"disk": args.disk, "fs": d["fs"], "src": src, "dst": to, "restore": restore,
           "started": time.strftime("%Y-%m-%d %H:%M")}
    p = subprocess.Popen([sys.executable, "-I", "-c",
                          f"import sys; sys.path.insert(0, {lib!r}); from osyr import backup; backup.worker()",
                          json.dumps(job)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    state["running"] = dict(job, pid=p.pid)
    store(state)
    what = "Restoring your files from" if restore else "Copying your folder to"
    warn = ""
    if not restore and encrypted_home():
        warn = " Your folder is encrypted on this computer, but the copy on the disk is not."
    if not restore and d["fs"] in FAT:
        warn += f" {args.disk} is {d['fs']}: files over 4 GB may not fit."
    return Result(True, "OK", {"disk": args.disk, "path": dst, "restore": restore},
                  "It runs in the background and shows a notice when it finishes. Progress: osyr backup status",
                  text=f"{what} {args.disk}.{warn}")


def worker():
    """The background process: rsync, then record and announce the result."""
    from . import notify
    job = json.loads(sys.argv[1])
    os.makedirs(job["dst"], exist_ok=True)
    p = subprocess.run(rsync_cmd(job["src"], job["dst"], job["fs"], job["restore"]),
                       capture_output=True, text=True)
    err = p.stderr.strip().splitlines()
    ok = p.returncode in (0, 24)  # 24: files that vanished during the copy
    if p.returncode == 23:
        msg = "Some files could not be copied."
    elif "No space left" in p.stderr:
        msg = f"{job['disk']} is full."
    else:
        msg = "" if ok else (err[-1] if err else f"rsync exit {p.returncode}")
    state = load()
    state.pop("running", None)
    key = "last_restore" if job["restore"] else "last"
    state.setdefault(key, {})[job["disk"]] = {"date": time.strftime("%Y-%m-%d %H:%M"), "ok": ok,
                                              "error": msg[:200]}
    store(state)
    title = ("Restore" if job["restore"] else "Backup") + (" finished" if ok else " failed")
    body = msg or (f"Your files are back from {job['disk']}." if job["restore"]
                   else f"Your folder is copied to {job['disk']}. Eject it before unplugging: osyr disk eject {job['disk']}")
    notify.send(title, body, urgency="normal" if ok else "critical", timeout_ms=0 if not ok else 15000)


def run(args):
    return start(args, restore=False)


def restore(args):
    return start(args, restore=True)


def status(args):
    state = load()
    job = running(state)
    lines = []
    if job:
        what = "Restoring from" if job.get("restore") else "Copying to"
        lines.append(f"{what} {job['disk']} since {job['started']}.")
    for name, r in sorted(state.get("last", {}).items()):
        lines.append(f"Last backup to {name}: {r['date']}" + ("" if r["ok"] else f" (failed: {r['error']})"))
    for name, r in sorted(state.get("last_restore", {}).items()):
        lines.append(f"Last restore from {name}: {r['date']}" + ("" if r["ok"] else f" (failed: {r['error']})"))
    if not lines:
        lines.append("No backup yet. Plug in a USB disk and run: osyr backup run <disk> (see osyr disk list)")
    return Result(True, "OK", {"running": job, "last": state.get("last", {}),
                               "last_restore": state.get("last_restore", {})}, text="\n".join(lines))


def register(sub):
    p = sub.add_parser("backup", help="copy your folder to a USB or external disk")
    s = p.add_subparsers(dest="action", required=True)
    r = s.add_parser("run", help="copy your folder to this disk (see osyr disk list)")
    r.add_argument("disk")
    r.set_defaults(func=run)
    b = s.add_parser("restore", help="bring your files back from this disk (deletes nothing)")
    b.add_argument("disk")
    b.set_defaults(func=restore)
    s.add_parser("status", help="backup in progress and the last ones").set_defaults(func=status)
