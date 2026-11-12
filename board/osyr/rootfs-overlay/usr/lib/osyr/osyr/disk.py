"""osyr disk — connected USB sticks, SD cards and external disks (osyr-automount).

They are mounted by themselves on plugging in, at /run/media/osyr/<name>. Before
removing them, "osyr disk eject <name>" finishes writing and unmounts them (with
passwordless sudo, only for that: /etc/sudoers.d/osyr-disk).
"""
import os
import shutil
import subprocess

from .result import Result, fail

BASE = os.environ.get("OSYR_MEDIA", "/run/media/osyr")
HELPER = "/usr/lib/osyr/osyr-automount"


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def mounted():
    out = []
    for line in open("/proc/mounts"):
        src, mnt, fs = line.split()[:3]
        if mnt.startswith(BASE + "/"):
            try:
                u = shutil.disk_usage(mnt)
                size, free = human(u.total), human(u.free)
            except OSError:  # pulled out without ejecting: the mount is still listed, the disk is not
                size, free = "?", "?"
            out.append({"name": os.path.basename(mnt), "path": mnt, "device": src, "fs": fs,
                        "size": size, "free": free})
    return out


def list_(args):
    ds = mounted()
    if not ds:
        return Result(True, "OK", {"disks": []}, text="No USB or external disk connected.")
    return Result(True, "OK", {"disks": ds},
                  text="\n".join(f'{d["name"]}: {d["path"]} ({d["fs"]}, {d["free"]} free of {d["size"]})'
                                 for d in ds))


def eject(args):
    if args.name not in [d["name"] for d in mounted()]:
        return fail("DISK_NOT_CONNECTED", "Run: osyr disk list", {"name": args.name})
    p = subprocess.run(["sudo", "-n", HELPER, "eject", args.name], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        return fail("DISK_BUSY" if "in use" in p.stderr else "DISK_EJECT_FAILED",
                    "Close the files and windows that use it, then try again.",
                    {"name": args.name, "error": p.stderr.strip()[-200:]})
    return Result(True, "OK", {"name": args.name}, text=f"{args.name} can be unplugged now.")


def register(sub):
    p = sub.add_parser("disk", help="USB sticks and external disks")
    s = p.add_subparsers(dest="action", required=True)
    s.add_parser("list", help="connected USB sticks and external disks").set_defaults(func=list_)
    e = s.add_parser("eject", help="finish writing and unmount, to unplug it safely")
    e.add_argument("name")
    e.set_defaults(func=eject)
