"""A/B slots of the installed disk: which root is running, grub.cfg and grubenv.

Used by osyr-install (when installing) and 'osyr update image' (when updating).
Installed disk (see osyr-install): p1 ESP, p2 root A, p3 root B, p4 /home.
GRUB reads /osyr/grub/grubenv from the ESP:
  slot=a|b   the good root (the one that always boots)
  try=a|b    boot this other one ONCE (the freshly updated one); GRUB clears it
             when used. If that boot reaches multi-user, osyr-boot-ok turns it
             into slot. If not (kernel that does not load, kernel panic with
             panic=10, emergency mode), the next boot goes back to slot.
"""
import os
import re
import subprocess

INSTALL_INFO = "/etc/osyr/install.conf"
KERNEL_ARGS = "rootwait rw console=ttyS0,115200 console=tty1"
GRUBENV_SIZE = 1024
PART = {"a": 2, "b": 3}


class SlotError(Exception):
    pass


def other(slot):
    return "b" if slot == "a" else "a"


def grub_cfg(sig, uuid_a, uuid_b):
    """grub.cfg for the ESP. sig: disk signature (int). Entry 0: the root to boot;
    1: the other one (fallback if 0 does not load); 2: recovery."""
    return f"""# OSYR installed on disk (written by osyr-install and osyr update image).
# Hold Shift at boot to see the menu. Root: slot from grubenv; try = boot the other one
# once (the freshly updated one). If it does not load, the previous one boots (fallback).
set default="0"
set fallback="1"
set timeout="0"
set timeout_style="hidden"
terminal_output console
set slot=a
set try=
load_env --file $prefix/grubenv slot try
set boot=$slot
if [ -n "$try" ]; then
    set boot=$try
    set try=
    save_env --file $prefix/grubenv try
fi
if [ "$boot" = "b" ]; then
    set osyr_uuid={uuid_b}
    set osyr_root=PARTUUID={sig:08x}-03
    set prev_uuid={uuid_a}
    set prev_root=PARTUUID={sig:08x}-02
else
    set osyr_uuid={uuid_a}
    set osyr_root=PARTUUID={sig:08x}-02
    set prev_uuid={uuid_b}
    set prev_root=PARTUUID={sig:08x}-03
fi
export osyr_uuid
export osyr_root
export prev_uuid
export prev_root

menuentry "OSYR" {{
    search --no-floppy --fs-uuid --set=root $osyr_uuid
    linux /boot/bzImage root=$osyr_root {KERNEL_ARGS} panic=10 quiet loglevel=3 vt.global_cursor_default=0
}}

menuentry "OSYR (previous version)" {{
    search --no-floppy --fs-uuid --set=root $prev_uuid
    linux /boot/bzImage root=$prev_root {KERNEL_ARGS} panic=10 quiet loglevel=3 vt.global_cursor_default=0
}}

menuentry "OSYR (recovery)" {{
    search --no-floppy --fs-uuid --set=root $osyr_uuid
    linux /boot/bzImage root=$osyr_root {KERNEL_ARGS} loglevel=7 systemd.unit=rescue.target
}}
"""


def grubenv(slot, try_=""):
    head = f"# GRUB Environment Block\nslot={slot}\ntry={try_}\n"
    return (head + "#" * (GRUBENV_SIZE - len(head))).encode()


def read_grubenv(path):
    env = {}
    try:
        for line in open(path, encoding="ascii", errors="replace"):
            if "=" in line and not line.startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                env[k] = v
    except OSError:
        pass
    return env


def write_grubenv(path, slot, try_=""):
    """In place (same blocks): GRUB writes save_env over the file's blocks."""
    data = grubenv(slot, try_)
    with open(path, "r+b" if os.path.exists(path) else "wb") as f:
        f.write(data)
        f.truncate(GRUBENV_SIZE)
        f.flush()
        os.fsync(f.fileno())


def read_kv(path):
    out = {}
    try:
        for line in open(path):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def running(cmdline_path="/proc/cmdline", info=INSTALL_INFO):
    """(signature 'xxxxxxxx', slot 'a'|'b') of the running root, or SlotError if this is
    not an OSYR installed on disk (the USB, a VM with the image)."""
    if not os.path.exists(info):
        raise SlotError("OSYR is not installed on this disk (it runs from the USB)")
    m = re.search(r"\broot=PARTUUID=([0-9a-f]{8})-0([23])\b", open(cmdline_path).read())
    if not m:
        raise SlotError("cannot tell which system slot is running (root= is not an OSYR disk)")
    disk_id = read_kv(info).get("DISK_ID", "").lower().replace("0x", "")
    if disk_id and disk_id != m.group(1):
        raise SlotError(f"running from disk {m.group(1)}, but this OSYR was installed on {disk_id}")
    return m.group(1), "a" if m.group(2) == "2" else "b"


def part(sig, n):
    """Device of partition n on the disk with that signature (by PARTUUID)."""
    path = os.path.realpath(f"/dev/disk/by-partuuid/{sig}-{n:02d}")
    if os.path.exists(path) and not path.startswith("/dev/disk/"):
        return path
    # Without udev's links (emergency mode, where 'image fallback' runs):
    # the MBR signature is the 4 bytes at offset 440 of each disk; devtmpfs already has the nodes
    for disk in sorted(os.listdir("/sys/block")):
        if disk.startswith(("loop", "ram", "zram", "sr", "dm-", "md")):
            continue
        try:
            with open(f"/dev/{disk}", "rb") as f:
                f.seek(440)
                found = int.from_bytes(f.read(4), "little")
        except OSError:
            continue
        if found == int(sig, 16):
            dev = f"/dev/{disk}{'p' if disk[-1].isdigit() else ''}{n}"
            if os.path.exists(dev):
                return dev
    raise SlotError(f"partition {sig}-{n:02d} not found")


def blkid(dev, tag):
    p = subprocess.run(["blkid", "-s", tag, "-o", "value", dev], capture_output=True, text=True)
    return p.stdout.strip()
