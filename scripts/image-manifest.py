#!/usr/bin/env python3
"""OSYR — signed manifest of an image, for 'osyr update image'.

Usage: scripts/image-manifest.py IMG XZ VERSION COMMIT OUTPUT [--key KEY] [--update UPD]
  IMG     uncompressed osyr.img (the root's position and sha256 come from it)
  XZ      osyr.img.xz as published (its sha256 and size)
  --keep P       (with --update) files under P go whole into UPD
  --update UPD   also writes UPD (osyr-update.img.xz): only the root, with the contents
          of the models (usr/share/osyr/models) zeroed. Machines that already have those
          models (same sha256) download this (~400 MB) and copy their own; otherwise the
          whole image. The manifest carries UPDATE_* and MODELS.
Writes OUTPUT (osyr.img.xz.manifest) and OUTPUT.sig, signed with the release
key (~/.ssh/osyr-release, or --key) in the osyr-image namespace. Machines
verify it with /etc/osyr/update-signers.
"""
import argparse
import hashlib
import os
import re
import struct
import subprocess
import sys

BLOCK = 4 * 1024 * 1024
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = "usr/share/osyr/models"


def sha256(path, offset=0, size=None):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(offset)
        left = size if size is not None else os.path.getsize(path) - offset
        while left:
            b = f.read(min(BLOCK, left))
            if not b:
                raise SystemExit(f"{path}: ends before {offset + size} bytes")
            h.update(b)
            left -= len(b)
    return h.hexdigest()


def root_partition(img):
    """(offset, size) of partition 2 (root) in the MBR table."""
    mbr = open(img, "rb").read(512)
    if mbr[510:512] != b"\x55\xaa":
        raise SystemExit(f"{img}: no MBR")
    e = mbr[446 + 16:446 + 32]
    if e[4] != 0x83:
        raise SystemExit(f"{img}: partition 2 is not Linux (type {e[4]:#x})")
    lba, n = struct.unpack("<II", e[8:16])
    return lba * 512, n * 512


def tool(name):
    """From e2fsprogs: Buildroot's (host) or the system's; /usr/sbin is not in the user's PATH."""
    for d in (os.path.join(ROOT, "build/output/host/sbin", name), f"/usr/sbin/{name}", f"/sbin/{name}"):
        if os.path.exists(d):
            return d
    raise SystemExit(f"{name} not found (e2fsprogs)")


def dfs(raw, cmd):
    p = subprocess.run([tool("debugfs"), "-R", cmd, raw], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"debugfs {cmd}: {p.stderr.strip()}")
    return p.stdout


MIN_SIZE = 1024 * 1024  # small ones (espeak-ng dictionaries...) stay in the image


KEEP = []  # prefixes (--keep) that go whole into the small image


def model_files(raw):
    """[(relative path, size)] of the large regular files under MODELS, recursively."""
    out = []

    def walk(d):
        for line in dfs(raw, f"ls -p /{d}").splitlines():
            # /inode/mode/uid/gid/name/size/
            f = line.strip().split("/")
            if len(f) < 7 or f[5] in (".", ".."):
                continue
            mode = int(f[2], 8)
            if mode & 0o170000 == 0o040000:
                walk(f"{d}/{f[5]}")
            elif mode & 0o170000 == 0o100000 and int(f[6] or 0) >= MIN_SIZE \
                    and not any(f"{d}/{f[5]}".startswith(k.strip("/")) for k in KEEP):
                out.append((f"{d}/{f[5]}", int(f[6])))
    walk(MODELS)
    return sorted(out)


def extents(raw, path):
    """[(logical, physical, length)] in blocks: only the leaves (data), never the tree."""
    rows = []
    for line in dfs(raw, f"dump_extents -l /{path}").splitlines():
        m = re.match(r"\s*\d+/\s*\d+\s+\d+/\s*\d+\s+(\d+)\s*-\s*(\d+)\s+(\d+)\s*-\s*(\d+)\s+(\d+)", line)
        if m:
            rows.append((int(m[1]), int(m[3]), int(m[5])))
    return sorted(rows)


def blank_models(raw):
    """Zeroes the models' data in the raw root; returns [(path, sha256, size)]."""
    bs = int(re.search(r"Block size:\s+(\d+)", dfs(raw, "stats"))[1])
    models = []
    with open(raw, "r+b") as f:
        for path, size in model_files(raw):
            h, pos = hashlib.sha256(), 0
            ext = extents(raw, path)
            for logical, phys, n in ext:
                if logical * bs > pos:  # hole: zeros
                    gap = min(logical * bs, size) - pos
                    h.update(b"\0" * gap)
                    pos += gap
                left = min(n * bs, max(0, size - pos))
                f.seek(phys * bs)
                while left:
                    b = f.read(min(BLOCK, left))
                    h.update(b)
                    pos += len(b)
                    left -= len(b)
            if pos < size:
                h.update(b"\0" * (size - pos))
            for logical, phys, n in ext:
                f.seek(phys * bs)
                left = n * bs
                while left:
                    k = min(BLOCK, left)
                    f.write(b"\0" * k)
                    left -= k
            models.append((path, h.hexdigest(), size))
    return models


def update_image(img, off, size, out):
    """UPD: the root without the models' data, compressed. Returns the manifest fields."""
    raw = out + ".root"
    with open(img, "rb") as src, open(raw, "wb") as dst:
        src.seek(off)
        left = size
        while left:
            b = src.read(min(BLOCK, left))
            if b.count(0) == len(b):
                dst.seek(len(b), 1)  # sparse: does not take 7 GB in build/
            else:
                dst.write(b)
            left -= len(b)
        dst.truncate(size)
    try:
        models = blank_models(raw)
        # the sha256s are those of the real file (the one in build/output/target, if present)
        for path, digest, n in models:
            t = os.path.join(ROOT, "build/output/target", path)
            if os.path.exists(t) and os.path.getsize(t) == n and sha256(t) != digest:
                raise SystemExit(f"{path}: the bytes read from the image do not match the file")
        p = subprocess.run([tool("e2fsck"), "-fn", raw], capture_output=True, text=True)
        if p.returncode != 0:
            raise SystemExit(f"e2fsck on the update root: {p.stdout[-500:]}")
        root_sha = sha256(raw)
        subprocess.run(f"xz -T0 -6 -c '{raw}' > '{out}.tmp' && mv '{out}.tmp' '{out}'", shell=True, check=True)
    finally:
        os.unlink(raw)
    # the name it is published under (next to osyr.img.xz), whatever the local one
    return (f"UPDATE_IMAGE=osyr-update.img.xz\nUPDATE_SHA256={sha256(out)}\n"
            f"UPDATE_SIZE={os.path.getsize(out)}\nUPDATE_ROOT_SHA256={root_sha}\n"
            f"MODELS={','.join(f'{p}:{d}:{n}' for p, d, n in models)}\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("img")
    p.add_argument("xz")
    p.add_argument("version")
    p.add_argument("commit")
    p.add_argument("out")
    p.add_argument("--key", default=os.environ.get("OSYR_RELEASE_KEY",
                                                   os.path.expanduser("~/.ssh/osyr-release")))
    p.add_argument("--update")
    # Models new in this version: they go inside the small image, because machines
    # do not have them yet and their updater is all-or-nothing
    p.add_argument("--keep", action="append", default=[], metavar="PREFIX")
    a = p.parse_args()
    KEEP.extend(a.keep)
    off, size = root_partition(a.img)
    text = (f"# OSYR — image manifest (verified by 'osyr update image')\n"
            f"VERSION={a.version}\nCOMMIT={a.commit}\nIMAGE=osyr.img.xz\n"
            f"IMAGE_SHA256={sha256(a.xz)}\nIMAGE_SIZE={os.path.getsize(a.xz)}\n"
            f"ROOT_OFFSET={off}\nROOT_SIZE={size}\nROOT_SHA256={sha256(a.img, off, size)}\n")
    if a.update:
        text += update_image(a.img, off, size, a.update)
    open(a.out, "w").write(text)
    if os.path.exists(a.out + ".sig"):
        os.unlink(a.out + ".sig")
    r = subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", a.key, "-n", "osyr-image", a.out])
    if r.returncode != 0 or not os.path.exists(a.out + ".sig"):
        raise SystemExit("could not sign the manifest")
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
