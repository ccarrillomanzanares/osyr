"""osyr update image — update the whole system without reinstalling (A/B slots).

Only on an OSYR installed on disk (osyr-install leaves root B free for this).
  check     downloads the signed manifest (osyr.img.xz.manifest + .sig, next to the
            published image) and says whether there is a newer version
  apply     downloads osyr.img.xz to /home (resumes if cut off), checks its sha256,
            writes its root to the free slot, carries over the system state
            (password, wifi, Bluetooth, language, time, /home, changed settings),
            brings it up to the latest code and sets it to boot ONCE (try)
  confirm   osyr-boot-ok.service: the trial boot reached multi-user, it stays
  fallback  emergency.service: if the trial boot fails, restart into the previous one
  rollback  go back to the version in the other slot (with the current state)
Trust: the manifest is signed by the release key (ssh-keygen, namespace
osyr-image, /etc/osyr/update-signers) and carries the sha256 of the image and of
the root. A root is never written unverified, and the version never goes back
(git ancestry).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

from . import slots
from . import update as code
from .result import Result, fail

DEFAULT_URL = "https://ccmai.org/osyr/releases/osyr.img.xz"
NAMESPACE = "osyr-image"
WORK = "/run/osyr-update"
DOWNLOADS = os.environ.get("OSYR_UPDATE_DOWNLOADS", "/home/.osyr-update")
USER_AGENT = "OSYR/1.0"  # Cloudflare (ccmai.org) rejects Python's user agent
BLOCK = 4 * 1024 * 1024

# System state that lives in the root and has to move to the new one
COPY_FILES = ["etc/locale.conf", "etc/vconsole.conf", "etc/hostname", "etc/machine-id",
              "etc/localtime", "etc/crypttab", "etc/osyr/install.conf"]
COPY_GLOBS = [("etc/ssh", "ssh_host_")]
COPY_DIRS = ["var/lib/iwd", "var/lib/bluetooth", "var/lib/systemd/timesync",
             "var/lib/systemd/backlight", "var/lib/systemd/linger"]
ONLY_IF_MISSING_DIRS = ["etc/nftables.d", "etc/osyr"]  # local rules and data (diag token...)


class ImageError(Exception):
    pass


def need_root(action):
    if os.geteuid() != 0:
        return fail("NEEDS_ROOT", f"Run: sudo osyr update image {action}",
                    text="Updating the OSYR system needs administrator rights.")
    return None


def run(args, check=True, **kw):
    p = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL, **kw)
    if check and p.returncode != 0:
        raise ImageError(f"{args[0]}: {(p.stderr or p.stdout).strip()[-300:]}")
    return p


def mark_reboot():
    code.mark_reboot()


# --- signed manifest -------------------------------------------------------------------

def image_url():
    return code.read_kv(code.CONF).get("IMAGE_URL", DEFAULT_URL)


def get(url, timeout=30):
    if url.startswith("file://"):
        return open(url[7:], "rb").read()
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def principals():
    """Signers in update-signers that are valid for images."""
    out = []
    try:
        for line in open(code.SIGNERS):
            f = line.split()
            if not f or f[0].startswith("#"):
                continue
            ns = [o for o in f[1:] if o.startswith("namespaces=")]
            if not ns or NAMESPACE in ns[0].split("=", 1)[1].strip('"').split(","):
                out.append(f[0])
    except OSError:
        pass
    return out


def verify(manifest, sig):
    """Checks the manifest's signature; returns the dictionary."""
    names = principals()
    if not names:
        raise ImageError(f"no key in {code.SIGNERS} may sign OSYR images: run 'sudo osyr update code apply' first")
    os.makedirs(WORK, exist_ok=True)
    sigfile = os.path.join(WORK, "manifest.sig")
    open(sigfile, "wb").write(sig)
    for name in names:
        p = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", code.SIGNERS, "-I", name,
                            "-n", NAMESPACE, "-s", sigfile], input=manifest, capture_output=True)
        if p.returncode == 0:
            m = {}
            for line in manifest.decode().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    m[k.strip()] = v.strip()
            for k in ("VERSION", "COMMIT", "IMAGE_SHA256", "IMAGE_SIZE", "ROOT_OFFSET", "ROOT_SIZE",
                      "ROOT_SHA256"):
                if not m.get(k):
                    raise ImageError(f"the signed manifest has no {k}")
            return m
    raise ImageError("the image manifest is not signed by an OSYR release key")


def latest():
    url = image_url()
    try:
        manifest, sig = get(url + ".manifest"), get(url + ".manifest.sig")
    except OSError as e:
        raise ImageError(f"cannot download {url}.manifest: {getattr(e, 'reason', e)}")
    m = verify(manifest, sig)
    base = url.rsplit("/", 1)[0]
    m["URL"] = base + "/" + m.get("IMAGE", url.rsplit("/", 1)[1])
    return m


def models(m):
    """[(path, sha256, size)] of the models the small image does not include."""
    out = []
    for item in filter(None, m.get("MODELS", "").split(",")):
        path, digest, size = item.rsplit(":", 2)
        if path.startswith("/") or ".." in path.split("/"):
            raise ImageError(f"bad model path in the manifest: {path}")
        out.append((path, digest, int(size)))
    return out


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(BLOCK), b""):
            h.update(b)
    return h.hexdigest()


def plan(m, check_hash=True):
    """The download to use: the root without models (osyr-update.img.xz, ~400 MB) if this
    computer's models are the same as the new version's; otherwise, the whole image.
    check_hash=False only compares sizes (for 'check', which must be quick)."""
    need = ("UPDATE_IMAGE", "UPDATE_SHA256", "UPDATE_SIZE", "UPDATE_ROOT_SHA256", "MODELS")
    if all(m.get(k) for k in need):
        ok = True
        for path, digest, size in models(m):
            here = os.path.join(code.ROOT, path)
            if not os.path.isfile(here) or os.path.getsize(here) != size:
                ok = False
                break
            if check_hash:
                print(f"Checking {os.path.basename(path)} on this computer...", flush=True)
                if file_sha256(here) != digest:
                    ok = False
                    break
        if ok:
            return dict(m, URL=m["URL"].rsplit("/", 1)[0] + "/" + m["UPDATE_IMAGE"], KIND="update",
                        IMAGE_SHA256=m["UPDATE_SHA256"], IMAGE_SIZE=m["UPDATE_SIZE"],
                        ROOT_OFFSET="0", ROOT_SHA256=m["UPDATE_ROOT_SHA256"])
    return dict(m, KIND="full")


def copy_models(m, new):
    """This computer's models into the new root (their files are already there, zeroed)."""
    for path, digest, size in models(m):
        src, dst = os.path.join(code.ROOT, path), os.path.join(new, path)
        print(f"Copying {os.path.basename(path)} ({size / 1e9:.1f} GB)...", flush=True)
        h = hashlib.sha256()
        with open(src, "rb") as fi, open(dst, "r+b" if os.path.exists(dst) else "wb") as fo:
            for b in iter(lambda: fi.read(BLOCK), b""):
                fo.write(b)
                h.update(b)
            fo.truncate(size)
            fo.flush()
            os.fsync(fo.fileno())
        if h.hexdigest() != digest:
            raise ImageError(f"{path} changed while copying it; run the update again")


def installed_image():
    """Commit of the running image (code updates do not change it)."""
    c = code.read_kv(code.BUILD_INFO).get("COMMIT", "")
    return c if c and c != "unknown" else None


def is_newer(new, base):
    """new comes after base in the signed history (the version never goes back)."""
    code.fetch()  # up-to-date clone in /var/lib/osyr/src (shared with update code)
    if not base:
        return True
    if new == base:
        return False
    for c in (new, base):
        if subprocess.run(["git", "-C", code.SRC, "cat-file", "-e", f"{c}^{{commit}}"],
                          capture_output=True).returncode != 0:
            raise ImageError(f"commit {c[:10]} is not in the OSYR repository")
    return subprocess.run(["git", "-C", code.SRC, "merge-base", "--is-ancestor", base, new],
                          capture_output=True).returncode == 0


# --- check -----------------------------------------------------------------------------

def state():
    sig, cur = slots.running()
    return sig, cur, slots.other(cur)


def image_check(args):
    r = need_root("check")
    if r:
        return r
    try:
        sig, cur, target = state()
    except slots.SlotError as e:
        return fail("UPDATE_IMAGE_NOT_INSTALLED", "To get a new version on the USB, download the image "
                    "from ccmai.org/osyr.", text=str(e))
    try:
        m = latest()
        base = installed_image()
        newer = is_newer(m["COMMIT"], base)
    except (ImageError, code.NoRelease, RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        return fail("UPDATE_IMAGE_CHECK_FAILED", "Check the network with: osyr net status",
                    {"error": str(e)}, text=f"Could not check for a new OSYR version: {e}")
    data = {"version": m["VERSION"], "commit": m["COMMIT"], "installed": base, "slot": cur,
            "target": target, "download": int(plan(m, check_hash=False)["IMAGE_SIZE"])}
    if not newer:
        return Result(True, "UPDATE_NONE", data, text=f"OSYR {m['VERSION']} is the latest version "
                      f"and this computer has it (or a newer one).")
    gb = data["download"] / 1e9
    return Result(True, "UPDATE_AVAILABLE", data, "Type /update in the OSYR chat (or: sudo osyr update image apply)",
                  text=f"New OSYR version: {m['VERSION']} ({gb:.1f} GB to download).")


# --- apply -----------------------------------------------------------------------------

def download(m):
    """osyr.img.xz to /home (resumable); downloads of other versions are deleted."""
    os.makedirs(DOWNLOADS, mode=0o700, exist_ok=True)
    name = f"osyr-{m['COMMIT'][:12]}-{m.get('KIND', 'full')}.img.xz"
    path = os.path.join(DOWNLOADS, name)
    for f in os.listdir(DOWNLOADS):
        if f != name:
            os.unlink(os.path.join(DOWNLOADS, f))
    size = int(m["IMAGE_SIZE"])
    have = os.path.getsize(path) if os.path.exists(path) else 0
    free = shutil.disk_usage(DOWNLOADS).free
    if size - have + 256 * 1024 ** 2 > free:
        raise ImageError(f"not enough free space in /home: {(size - have) / 1e9:.1f} GB needed, "
                         f"{free / 1e9:.1f} GB free")
    if have < size:
        print(f"Downloading OSYR {m['VERSION']} ({size / 1e9:.1f} GB"
              + (f", resuming at {have / 1e9:.1f} GB" if have else "") + ")...", flush=True)
        if m["URL"].startswith("file://"):
            shutil.copyfile(m["URL"][7:], path)
        else:
            p = subprocess.run(["curl", "-fL", "-C", "-", "--retry", "5", "--retry-delay", "5",
                                "-A", USER_AGENT, "-o", path, m["URL"]]
                               + (["-#"] if sys.stdout.isatty() else ["-sS"]))
            if p.returncode != 0:
                raise ImageError("the download stopped; run the update again to resume it")
    if os.path.getsize(path) != size:
        os.unlink(path)
        raise ImageError("the downloaded file has the wrong size; run the update again")
    print("Checking the download...", flush=True)
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(BLOCK), b""):
            h.update(b)
    if h.hexdigest() != m["IMAGE_SHA256"]:
        os.unlink(path)
        raise ImageError("the downloaded file is damaged (sha256 does not match); run the update again")
    return path


def write_root(path, m, dev):
    """The image's root (ROOT_OFFSET/ROOT_SIZE inside osyr.img) to partition dev."""
    off, size = int(m["ROOT_OFFSET"]), int(m["ROOT_SIZE"])
    with open(dev, "rb") as f:
        cap = f.seek(0, 2)
    if size > cap:
        raise ImageError(f"the new system ({size / 2**30:.1f} GiB) does not fit in its partition "
                         f"({cap / 2**30:.1f} GiB)")
    x = subprocess.Popen(["xzcat", path], stdout=subprocess.PIPE)
    h = hashlib.sha256()
    done, shown = 0, -1
    try:
        skip = off
        while skip:
            b = x.stdout.read(min(BLOCK, skip))
            if not b:
                raise ImageError("the image ends before the system partition")
            skip -= len(b)
        fd = os.open(dev, os.O_WRONLY)
        try:
            while done < size:
                b = x.stdout.read(min(BLOCK, size - done))
                if not b:
                    raise ImageError("the image ends inside the system partition")
                _write_all(fd, b)
                h.update(b)
                done += len(b)
                pct = done * 100 // size
                if pct // 10 != shown:
                    shown = pct // 10
                    print(f"  writing the new system: {pct}%", flush=True)
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        x.kill()
        x.wait()
    if h.hexdigest() != m["ROOT_SHA256"]:
        raise ImageError("the written system does not match the signed manifest")


def _write_all(fd, b):
    v = memoryview(b)
    while v:
        n = os.write(fd, v)
        v = v[n:]


def merge_accounts(src, dst):
    """Accounts from the new root + passwords from the current one; added users (uid >= 1000)
    and group membership are kept. New system users stay."""
    def table(path):
        rows = {}
        try:
            for line in open(path):
                line = line.rstrip("\n")
                if line and not line.startswith("#"):
                    rows[line.split(":", 1)[0]] = line
        except OSError:
            pass
        return rows

    old = {n: table(f"{src}/etc/{n}") for n in ("passwd", "shadow", "group", "gshadow")}
    new = {n: table(f"{dst}/etc/{n}") for n in ("passwd", "shadow", "group", "gshadow")}
    added = [u for u, l in old["passwd"].items()
             if u not in new["passwd"] and 1000 <= int(l.split(":")[2]) < 60000]
    for u in added:
        new["passwd"][u] = old["passwd"][u]
    for u in list(new["shadow"]) + added:  # passwords: the current ones
        if u in old["shadow"]:
            new["shadow"][u] = old["shadow"][u]
    for g, line in old["group"].items():
        members = set(filter(None, line.split(":")[3].split(",")))
        if g in new["group"]:
            f = new["group"][g].split(":")
            keep = members & set(new["passwd"])
            f[3] = ",".join(sorted(set(filter(None, f[3].split(","))) | keep))
            new["group"][g] = ":".join(f)
        elif any(int(old["passwd"][u].split(":")[3]) == int(line.split(":")[2])
                 for u in added if u in old["passwd"]):
            new["group"][g] = line  # an added user's own group
            if g in old["gshadow"]:
                new["gshadow"][g] = old["gshadow"][g]
    for n, mode in (("passwd", 0o644), ("group", 0o644), ("shadow", 0o600), ("gshadow", 0o600)):
        if not new[n]:
            continue
        p = f"{dst}/etc/{n}"
        st = os.stat(p) if os.path.exists(p) else None
        with open(p + ".osyr-tmp", "w") as f:
            f.write("\n".join(new[n].values()) + "\n")
        os.chmod(p + ".osyr-tmp", st.st_mode & 0o7777 if st else mode)
        os.replace(p + ".osyr-tmp", p)


def copy_path(src, dst):
    if os.path.islink(src):
        if os.path.lexists(dst):
            os.unlink(dst)
        os.symlink(os.readlink(src), dst)
    elif os.path.isdir(src):
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)


def merge_osyr_conf(src, dst, base):
    """/etc/osyr/*: what the person changed from their OSYR version is kept
    (if the new version changed it too, the new one is left alongside as .osyr-new)."""
    kept = []
    try:
        files = code.overlay_files(base) if base else {}
    except RuntimeError:
        # no git clone (e.g. going back from a slot that did not have it)
        try:
            code.fetch()
            files = code.overlay_files(base)
        except (RuntimeError, code.NoRelease, OSError, subprocess.SubprocessError):
            # no network: the other slot keeps its /etc/osyr (the one from when it was in use)
            return kept
    for path, _ in sorted(files.items()):
        if not path.startswith("etc/osyr/") or path == "etc/osyr/build-info":
            continue
        try:
            cur = open(f"{src}/{path}", "rb").read()
        except OSError:
            continue
        b = code.blob(base, f"{code.OVERLAY}/{path}")
        if cur == b:
            continue  # untouched: the new version's one applies
        new_path = f"{dst}/{path}"
        if os.path.exists(new_path) and open(new_path, "rb").read() not in (b, cur):
            shutil.copy2(new_path, new_path + ".osyr-new")
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        shutil.copy2(f"{src}/{path}", new_path)
        kept.append("/" + path)
    return kept


def migrate(src, dst, target_slot, base, fresh=True):
    """System state from src (the current root) to dst (the new one, mounted)."""
    merge_accounts(src, dst)
    # update code's git clone (a few MB): the other slot needs it to compare
    # /etc/osyr when going back, and to avoid downloading it all again
    if os.path.isdir(f"{code.SRC}/.git"):
        os.makedirs(f"{dst}/var/lib/osyr", exist_ok=True)
        copy_path(code.SRC, f"{dst}/var/lib/osyr/src")
    for rel in COPY_FILES:
        if os.path.lexists(f"{src}/{rel}"):
            os.makedirs(os.path.dirname(f"{dst}/{rel}"), exist_ok=True)
            copy_path(f"{src}/{rel}", f"{dst}/{rel}")
    for d, prefix in COPY_GLOBS:
        if os.path.isdir(f"{src}/{d}"):
            os.makedirs(f"{dst}/{d}", exist_ok=True)
            for f in os.listdir(f"{src}/{d}"):
                if f.startswith(prefix):
                    copy_path(f"{src}/{d}/{f}", f"{dst}/{d}/{f}")
    for rel in COPY_DIRS:
        if os.path.isdir(f"{src}/{rel}"):
            os.makedirs(os.path.dirname(f"{dst}/{rel}"), exist_ok=True)
            copy_path(f"{src}/{rel}", f"{dst}/{rel}")
    kept = merge_osyr_conf(src, dst, base)
    for rel in ONLY_IF_MISSING_DIRS:
        if os.path.isdir(f"{src}/{rel}"):
            for f in os.listdir(f"{src}/{rel}"):
                s, d = f"{src}/{rel}/{f}", f"{dst}/{rel}/{f}"
                if os.path.isfile(s) and not os.path.lexists(d):
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                    copy_path(s, d)
    # fstab: the new version's + this installation's /home mount
    home = [l for l in open(f"{src}/etc/fstab") if l.split()[1:2] == ["/home"]]
    with open(f"{dst}/etc/fstab") as f:
        lines = [l for l in f if l.split()[1:2] != ["/home"]]
    with open(f"{dst}/etc/fstab", "w") as f:
        f.writelines(lines + home)
    info = f"{dst}/{slots.INSTALL_INFO.lstrip('/')}"
    if os.path.exists(info):
        rows = [l for l in open(info) if not l.startswith("SLOT=")]
        with open(info, "w") as f:
            f.writelines(rows + [f"SLOT={target_slot}\n"])
    # the journal and update code's backups belong to the other root. When going back
    # to a slot (fresh=False) its backups and code-commit are still its own: deleting
    # them would make 'update code' take its already updated code as conflicts.
    if fresh:
        shutil.rmtree(f"{dst}/var/lib/osyr/updates", ignore_errors=True)
    for f in (("code-commit", "update-notified") if fresh else ("update-notified",)):
        try:
            os.unlink(f"{dst}/var/lib/osyr/{f}")
        except FileNotFoundError:
            pass
    return kept


def sync_code(dst):
    """Brings the new root up to the latest signed code (if newer than its image):
    done by 'update code apply' with the root and the state pointing to dst."""
    env = dict(os.environ, OSYR_UPDATE_ROOT=dst, OSYR_UPDATE_STATE=f"{dst}/var/lib/osyr",
               OSYR_UPDATE_SRC=code.SRC)
    osyr = [sys.executable, "-B", "-c", "import sys; sys.path.insert(0, '/usr/lib/osyr'); "
            "from osyr.cli import main; sys.exit(main())"]
    p = subprocess.run(osyr + ["update", "code", "apply", "--json"], env=env, capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, timeout=600)
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ImageError(f"code update of the new system failed: {(p.stderr or p.stdout)[-300:]}")
    if res.get("code") == "CONFIRM_REQUIRED":
        p = subprocess.run(osyr + ["update", "code", "apply", "--json", "--confirm", res["confirm"]],
                           env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
        res = json.loads(p.stdout.strip().splitlines()[-1])
    if not res.get("ok"):
        raise ImageError(f"code update of the new system failed: {res.get('code')} {res.get('data')}")
    # the backups update code keeps are from this operation, useless in the new root
    shutil.rmtree(f"{dst}/var/lib/osyr/updates", ignore_errors=True)
    return (res.get("data") or {}).get("to")


def enable_units(dst):
    """A/B services enabled in the new root (update code does not create links)."""
    for unit, target in (("osyr-boot-ok.service", "multi-user.target"),):
        if os.path.exists(f"{dst}/etc/systemd/system/{unit}"):
            wants = f"{dst}/etc/systemd/system/{target}.wants"
            os.makedirs(wants, exist_ok=True)
            link = f"{wants}/{unit}"
            if not os.path.lexists(link):
                os.symlink(f"/etc/systemd/system/{unit}", link)


class Mounts:
    def __init__(self):
        self.done = []

    def mount(self, dev, where, *opts):
        os.makedirs(where, exist_ok=True)
        run(["mount", *opts, dev, where])
        self.done.append(where)
        return where

    def close(self):
        for where in reversed(self.done):
            subprocess.run(["umount", where], capture_output=True)
        self.done = []


def esp_dir(sig, mounts):
    """The mounted ESP (this disk's)."""
    return mounts.mount(slots.part(sig, 1), f"{WORK}/esp")


def set_boot(sig, esp, slot, try_=""):
    """grub.cfg (with the previous version's entry) and grubenv."""
    ua = slots.blkid(slots.part(sig, 2), "UUID")
    ub = slots.blkid(slots.part(sig, 3), "UUID")
    cfg = f"{esp}/osyr/grub/grub.cfg"
    with open(cfg + ".osyr-tmp", "w") as f:
        f.write(slots.grub_cfg(int(sig, 16), ua, ub))
    os.replace(cfg + ".osyr-tmp", cfg)
    slots.write_grubenv(f"{esp}/osyr/grub/grubenv", slot, try_)
    run(["sync"])


def token_for(m, target):
    return hashlib.sha256(f"{m['COMMIT']}:{target}".encode()).hexdigest()[:8]


def image_apply(args):
    r = need_root("apply")
    if r:
        return r
    res = image_check(args)
    if res.code != "UPDATE_AVAILABLE":
        return res
    try:
        sig, cur, target = state()
        m = latest()  # (another download: the wifi may fail right now)
    except (slots.SlotError, ImageError, OSError) as e:
        return fail("UPDATE_IMAGE_CHECK_FAILED", text=f"Could not check the new version: {e}")
    token = token_for(m, target)
    msg = [res.text,
           "It is downloaded to /home, written next to the current system and started on the next restart.",
           "Your files, password, wifi and settings stay. If the new version does not start, the computer",
           "goes back to this one by itself."]
    if args.confirm != token:
        if not args.json and sys.stdin.isatty() and args.confirm is None:
            print("\n".join(msg))
            if input("Update now? [y/N] ").strip().lower() not in ("y", "yes", "s", "si", "sí"):
                return Result(True, "UPDATE_CANCELLED", text="Nothing changed.")
        else:
            return Result(False, "CONFIRM_REQUIRED", res.data, f"Run: sudo osyr update image apply --confirm {token}",
                          confirm=token, text="\n".join(msg))
    dev = slots.part(sig, slots.PART[target])
    if run(["findmnt", "-n", "-S", dev], check=False).stdout.strip():
        return fail("UPDATE_IMAGE_BUSY", text=f"{dev} is mounted: restart and try again.")
    mounts = Mounts()
    idle = "/run/osyr/no-idle"
    try:
        os.makedirs(os.path.dirname(idle), exist_ok=True)
        open(idle, "w").close()
        d = plan(m)
        path = download(d)
        print(f"Writing the new system to {dev} (several minutes)...", flush=True)
        write_root(path, d, dev)
        run(["e2fsck", "-fy", dev], check=False)  # 0 or 1: clean or fixed
        # its own UUID: the USB has the same one and GRUB could boot the other
        run(["tune2fs", "-U", "random", "-L", f"osyr-{target}", dev])
        if shutil.which("resize2fs"):
            run(["resize2fs", dev], check=False)
        new = mounts.mount(dev, f"{WORK}/new")
        if d["KIND"] == "update":
            copy_models(d, new)
        print("Moving your settings to the new system...", flush=True)
        kept = migrate("/", new, target, code.installed_commit())
        print("Bringing the new system up to the latest OSYR code...", flush=True)
        synced = sync_code(new)
        enable_units(new)
        version = code.read_kv(f"{new}/etc/osyr/build-info").get("COMMIT", "")[:10]
        mounts.close()
        esp = esp_dir(sig, mounts)
        set_boot(sig, esp, cur, target)
        mounts.close()
        os.unlink(path)
    except (ImageError, slots.SlotError, OSError, subprocess.SubprocessError, ValueError, RuntimeError) as e:
        mounts.close()
        return fail("UPDATE_IMAGE_FAILED", "Run the update again; this system was not changed.",
                    {"error": str(e)}, text=f"The update FAILED: {e}")
    finally:
        try:
            os.unlink(idle)
        except OSError:
            pass
    mark_reboot()
    lines = [f"OSYR {m['VERSION']} is ready (system {target.upper()}, image {version}"
             + (f", code {synced[:10]}" if synced else "") + ").",
             "Restart the computer to start it. The current version stays as the previous one."]
    if kept:
        lines.append("Your changed settings were kept: " + ", ".join(kept))
    return Result(True, "OK", {"version": m["VERSION"], "slot": target, "previous": cur, "kept": kept},
                  "Restart the computer to finish.", text="\n".join(lines))


# --- boot: confirm, fall back, roll back -----------------------------------------------

HEALTH_WAIT = 300  # seconds: the first boot after writing the root is slow


def healthy(wait):
    """None if the trial version works: the graphical session (sway) running and the
    local model not failing. Otherwise, what fails. Reaching multi-user is not enough:
    with sway or mesa broken it is reached all the same, at a text console."""
    end = time.time() + wait
    while True:
        sway = subprocess.run(["pgrep", "-x", "sway"], capture_output=True).returncode == 0
        llama = subprocess.run(["systemctl", "is-failed", "--quiet", "osyr-llama.service"],
                               capture_output=True).returncode != 0  # 0 = it failed
        if sway and llama:
            return None
        if time.time() >= end:
            return "the desktop did not start" if not sway else "the AI model failed to start"
        time.sleep(5)


def image_confirm(args):
    """osyr-boot-ok.service: if the trial slot booted, it stays as the good one."""
    r = need_root("confirm")
    if r:
        return r
    try:
        sig, cur, _ = state()
    except slots.SlotError as e:
        return Result(True, "UPDATE_IMAGE_NOT_INSTALLED", text=str(e))
    mounts = Mounts()
    try:
        esp = esp_dir(sig, mounts)
        env = slots.read_grubenv(f"{esp}/osyr/grub/grubenv")
        if env.get("slot") == cur and not env.get("try"):
            return Result(True, "OK", {"slot": cur}, text=f"System {cur.upper()} is the active one.")
        mounts.close()
        bad = healthy(getattr(args, "wait", HEALTH_WAIT))
        if bad:
            # not confirmed: GRUB already used the attempt, the next boot goes back to the good one
            return fail("UPDATE_IMAGE_UNHEALTHY", "Restart the computer to go back to the previous version.",
                        {"slot": cur, "problem": bad}, text=f"System {cur.upper()} is not kept: {bad}.")
        esp = esp_dir(sig, mounts)
        slots.write_grubenv(f"{esp}/osyr/grub/grubenv", cur)
    except (slots.SlotError, ImageError, OSError) as e:
        return fail("UPDATE_IMAGE_CONFIRM_FAILED", text=str(e))
    finally:
        mounts.close()
    rows = [l for l in open(slots.INSTALL_INFO) if not l.startswith("SLOT=")]
    with open(slots.INSTALL_INFO, "w") as f:
        f.writelines(rows + [f"SLOT={cur}\n"])
    return Result(True, "OK", {"slot": cur}, text=f"System {cur.upper()} started fine: it stays.")


def image_fallback(args):
    """emergency.service: a trial boot that does not get to work goes back to the good one."""
    try:
        sig, cur, _ = state()
        mounts = Mounts()
        try:
            env = slots.read_grubenv(f"{esp_dir(sig, mounts)}/osyr/grub/grubenv")
        finally:
            mounts.close()
    except (slots.SlotError, ImageError, OSError) as e:
        return Result(True, "OK", text=f"no fallback: {e}")
    if env.get("slot") in ("a", "b") and env["slot"] != cur:
        print(f"OSYR: the new version (system {cur.upper()}) did not start properly. "
              f"Going back to the previous one in 10 seconds...", flush=True)
        time.sleep(10)
        subprocess.run(["systemctl", "reboot"])
        return Result(True, "UPDATE_IMAGE_FALLBACK", {"slot": env["slot"]}, text="Restarting.")
    return Result(True, "OK", text="not a trial boot: emergency mode stays")


def image_rollback(args):
    """The other slot becomes the good one again (with the current password, wifi...)."""
    r = need_root("rollback")
    if r:
        return r
    try:
        sig, cur, prev = state()
    except slots.SlotError as e:
        return fail("UPDATE_IMAGE_NOT_INSTALLED", text=str(e))
    mounts = Mounts()
    try:
        dev = slots.part(sig, slots.PART[prev])
        old = mounts.mount(dev, f"{WORK}/prev")
        info = code.read_kv(f"{old}/etc/osyr/build-info")
        if not os.path.exists(f"{old}/boot/bzImage") or not info.get("COMMIT"):
            return fail("UPDATE_IMAGE_NO_PREVIOUS", text="There is no previous version to go back to.")
        token = hashlib.sha256(f"rollback:{info['COMMIT']}:{prev}".encode()).hexdigest()[:8]
        question = f"Go back to the previous OSYR (system {prev.upper()}, image {info['COMMIT'][:10]})?"
        if getattr(args, "confirm", None) != token:
            if sys.stdin.isatty() and not args.json and getattr(args, "confirm", None) is None:
                print(question)
                if input("Your files and password stay. Continue? [y/N] ").strip().lower() not in \
                        ("y", "yes", "s", "si", "sí"):
                    return Result(True, "UPDATE_CANCELLED", text="Nothing changed.")
            else:
                return Result(False, "CONFIRM_REQUIRED", {"slot": prev, "image": info["COMMIT"]},
                              f"Run: sudo osyr update image rollback --confirm {token}",
                              confirm=token, text=question)
        migrate("/", old, prev, code.installed_commit(), fresh=False)
        mounts.close()
        set_boot(sig, esp_dir(sig, mounts), prev)
    except (slots.SlotError, ImageError, OSError, subprocess.SubprocessError, RuntimeError) as e:
        return fail("UPDATE_IMAGE_FAILED", text=f"Could not go back: {e}")
    finally:
        mounts.close()
    mark_reboot()
    return Result(True, "OK", {"slot": prev}, "Restart the computer to finish.",
                  text=f"The previous OSYR (system {prev.upper()}) starts on the next restart.")


def image_status(args):
    try:
        sig, cur, other = state()
    except slots.SlotError as e:
        return Result(True, "UPDATE_IMAGE_NOT_INSTALLED", text=str(e))
    data = {"slot": cur, "image": installed_image()}
    return Result(True, "OK", data, text=f"Running system {cur.upper()}, image {str(data['image'])[:10]}.")


def register(s):
    i = s.add_parser("image", help="the whole system, without reinstalling (installed OSYR only)")
    a = i.add_subparsers(dest="image_action", required=True)
    a.add_parser("check", help="is there a newer OSYR version?").set_defaults(func=image_check)
    p = a.add_parser("apply", help="download it and start it on the next restart (asks first)")
    p.add_argument("--confirm", metavar="TOKEN")
    p.set_defaults(func=image_apply)
    p = a.add_parser("rollback", help="go back to the previous version (asks first)")
    p.add_argument("--confirm", metavar="TOKEN")
    p.set_defaults(func=image_rollback)
    a.add_parser("status", help="which system slot is running").set_defaults(func=image_status)
    a.add_parser("confirm", help="(at boot) keep the version that just started").set_defaults(func=image_confirm)
    a.add_parser("fallback", help="(emergency mode) go back if a new version failed").set_defaults(
        func=image_fallback)
