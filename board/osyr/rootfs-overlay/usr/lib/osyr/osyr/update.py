"""osyr update code — updates OSYR's scripts and configuration from git.

It only covers what goes in the image overlay (board/osyr/rootfs-overlay):
scripts, the CLI, configuration. Packages, kernel or defconfig need a new
image; 'check' says so. Whole images are updated A/B with 'osyr update image'
(image.py).

Three-way merge per file, so local changes are not overwritten:
  base   = version from the installed commit (/etc/osyr/build-info or the last
           update), new = the repository's, current = the system's.
  - only the repository changed  -> the new one is installed
  - only the system changed      -> it is kept
  - both changed                 -> the local one is kept; the new one is left
                                    alongside as <file>.osyr-new
Before touching anything, what was there is copied to /var/lib/osyr/updates/<date>/,
and 'rollback' puts it back as it was.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

from .result import Result, fail

ROOT = os.environ.get("OSYR_UPDATE_ROOT", "/")  # system root (tests)
CONF = os.path.join(ROOT, "etc/osyr/update.conf")
SIGNERS = os.path.join(ROOT, "etc/osyr/update-signers")
BUILD_INFO = os.path.join(ROOT, "etc/osyr/build-info")
STATE = os.environ.get("OSYR_UPDATE_STATE", "/var/lib/osyr")
SRC = os.environ.get("OSYR_UPDATE_SRC") or os.path.join(STATE, "src")  # shared with image.py
CURRENT = os.path.join(STATE, "code-commit")
UPDATES = os.path.join(STATE, "updates")
OVERLAY = "board/osyr/rootfs-overlay"
# Changes outside the overlay that only arrive with a new image
IMAGE_PATHS = ("configs/", "board/osyr/kernel/", "board/osyr/post-build.sh",
               "board/osyr/post-image.sh", "board/osyr/users.txt", "board/osyr/genimage.cfg",
               "board/osyr/grub", "external.mk", "Config.in", "package/")


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


def git(*args, check=True, binary=False):
    p = subprocess.run(["git", "-C", SRC, *args], capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=300)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace").strip() or "git failed")
    return p.stdout if binary else p.stdout.decode(errors="replace")


def blob(commit, path):
    """Contents of path at commit, or None if it does not exist there."""
    if not commit:
        return None
    p = subprocess.run(["git", "-C", SRC, "show", f"{commit}:{path}"],
                       capture_output=True, stdin=subprocess.DEVNULL)
    return p.stdout if p.returncode == 0 else None


def live(path):
    try:
        with open(os.path.join(ROOT, path), "rb") as f:
            return f.read()
    except OSError:
        return None


def overlay_files(commit):
    out = git("ls-tree", "-r", "--full-tree", commit, OVERLAY)
    files = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        mode = meta.split()[0]
        if mode in ("100644", "100755"):
            files[path[len(OVERLAY) + 1:]] = mode
    return files


def installed_commit():
    try:
        c = open(CURRENT).read().strip()
        if c:
            return c
    except OSError:
        pass
    c = read_kv(BUILD_INFO).get("COMMIT", "")
    return c if c and c != "unknown" else None


def need_root(action):
    if os.geteuid() != 0 and "OSYR_UPDATE_ROOT" not in os.environ:
        return fail("NEEDS_ROOT", f"Run: sudo osyr update code {action}",
                    text="Updating OSYR code needs administrator rights.")
    return None


class NoRelease(Exception):
    """There is no version signed by a key in update-signers."""


def verified_tag():
    """The highest v* tag with a valid signature (key in update-signers)."""
    tags = git("tag", "-l", "v*", "--sort=-v:refname").split()
    for tag in tags:
        p = subprocess.run(["git", "-C", SRC, "-c", "gpg.format=ssh",
                            "-c", f"gpg.ssh.allowedSignersFile={SIGNERS}", "verify-tag", tag],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        if p.returncode == 0:
            return tag
    raise NoRelease(f"none of {len(tags)} tag(s) is signed by a key in {SIGNERS}")


def fetch():
    """Downloads and returns (commit, repo, source). Channel 'release' (default):
    the latest signed version; 'main': the tip of the branch, unsigned."""
    conf = read_kv(CONF)
    repo = conf.get("REPO", "https://github.com/ccarrillomanzanares/osyr")
    branch = conf.get("BRANCH", "main")
    channel = conf.get("CHANNEL", "release")
    if not os.path.isdir(os.path.join(SRC, ".git")):
        os.makedirs(STATE, exist_ok=True)
        p = subprocess.run(["git", "clone", "--quiet", "--branch", branch, repo, SRC],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=600)
        if p.returncode != 0:
            shutil.rmtree(SRC, ignore_errors=True)
            raise RuntimeError(p.stderr.decode(errors="replace").strip())
    git("remote", "set-url", "origin", repo)
    git("fetch", "--quiet", "--tags", "--force", "origin", branch)
    if channel == "main":
        return git("rev-parse", "FETCH_HEAD").strip(), repo, f"{branch} (unsigned)"
    tag = verified_tag()
    return git("rev-parse", f"{tag}^{{commit}}").strip(), repo, f"{tag} (signed)"


def is_older(new, base):
    """Is new older than what is installed? (the version never goes back)"""
    if not base or new == base:
        return False
    return subprocess.run(["git", "-C", SRC, "merge-base", "--is-ancestor", new, base],
                          capture_output=True).returncode == 0


def plan(base, new):
    """What to do with each overlay file when going from base to new."""
    base_ok = bool(base) and subprocess.run(
        ["git", "-C", SRC, "cat-file", "-e", f"{base}^{{commit}}"],
        capture_output=True).returncode == 0
    new_files = overlay_files(new)
    old_files = overlay_files(base) if base_ok else {}
    steps = {"update": [], "add": [], "remove": [], "conflict": [], "kept": []}
    for path, mode in sorted(new_files.items()):
        b = blob(base, f"{OVERLAY}/{path}") if base_ok else None
        n = blob(new, f"{OVERLAY}/{path}")
        cur = live(path)
        if cur == n:
            continue
        if cur is None:
            steps["add"].append({"path": path, "mode": mode})
        elif not base_ok or cur == b:  # no known base: update (with backup)
            steps["update"].append({"path": path, "mode": mode})
        elif n == b:
            continue  # only the user changed it: keep it
        else:
            steps["conflict"].append({"path": path, "mode": mode})
    for path in sorted(set(old_files) - set(new_files)):
        cur = live(path)
        if cur is None:
            continue
        if cur == blob(base, f"{OVERLAY}/{path}"):
            steps["remove"].append({"path": path})
        else:
            steps["kept"].append({"path": path})
    image = []
    if base_ok:
        changed = git("diff", "--name-only", base, new).splitlines()
        image = [p for p in changed if p.startswith(IMAGE_PATHS)]
    commits = []
    if base_ok:
        commits = git("log", "--format=%h %s", f"{base}..{new}").splitlines()
    return steps, image, commits, base_ok


def token_for(new, steps):
    raw = json.dumps([new, steps], sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:8]


def summary(new, steps, image, commits, base_ok):
    lines = [f"Latest: {new[:10]}"]  # the source (signed tag or branch) is added by the caller
    if commits:
        lines += ["Commits:"] + [f"  {c}" for c in commits[:15]]
        if len(commits) > 15:
            lines.append(f"  ... and {len(commits) - 15} more")
    if not base_ok:
        lines.append("Installed version unknown: every file that differs will be replaced (with backup).")
    labels = {"update": "update", "add": "new", "remove": "remove",
              "conflict": "local changes kept, new version saved as .osyr-new",
              "kept": "removed upstream but changed locally, kept"}
    for k, label in labels.items():
        for s in steps[k]:
            lines.append(f"  [{label}] /{s['path']}")
    if image:
        lines.append("These changes need a new image (not applied here):")
        lines += [f"  {p}" for p in image]
    return lines


def mode_for(path, git_mode):
    if path.startswith("etc/sudoers.d/"):
        return 0o440
    return 0o755 if git_mode == "100755" else 0o644


def owner_for(path):
    return (1000, 1000) if path.startswith("home/osyr/") else (0, 0)


def write_file(path, data, git_mode):
    dest = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".osyr-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode_for(path, git_mode))
    if os.geteuid() == 0:
        os.chown(tmp, *owner_for(path))
    os.replace(tmp, dest)  # atomic: a script is never left half-written


def code_check(args):
    r = need_root("check")
    if r:
        return r
    try:
        new, repo, branch = fetch()
    except NoRelease as e:
        return Result(True, "UPDATE_NO_RELEASE", {"error": str(e)},
                      "There is no signed OSYR release yet.", text=f"No signed release: {e}")
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        return fail("UPDATE_FETCH_FAILED", "Check the network with: osyr net status",
                    {"error": str(e)}, text=f"Could not download the OSYR code: {e}")
    base = installed_commit()
    if is_older(new, base):
        return Result(True, "UPDATE_NONE", {"installed": base, "latest": new, "source": branch},
                      text=f"Installed code is newer than {branch}: nothing to do.")
    steps, image, commits, base_ok = plan(base, new)
    n = sum(len(v) for k, v in steps.items() if k != "kept")
    data = {"installed": base, "latest": new, "repo": repo, "source": branch,
            "changes": steps, "needs_image": image, "commits": commits}
    if n == 0:
        return Result(True, "UPDATE_NONE", data,
                      "A new image is needed for the remaining changes." if image else None,
                      text="\n".join(["OSYR code is up to date."] +
                                     summary(new, steps, image, [], base_ok)[1:]))
    return Result(True, "UPDATE_AVAILABLE", data, "Type /update in the OSYR chat (or: sudo osyr update code apply)",
                  text="\n".join([f"Source: {branch}"] + summary(new, steps, image, commits, base_ok)))


NOTIFIED = os.path.join(STATE, "update-notified")
REBOOT_MARK = "/run/osyr/reboot-needed"  # the chat (/update) offers to restart if it exists


def mark_reboot():
    try:
        os.makedirs(os.path.dirname(REBOOT_MARK), exist_ok=True)
        open(REBOOT_MARK, "a").close()
        os.utime(REBOOT_MARK)  # the chat checks whether the date changed
    except OSError:
        pass


def has_code_backup():
    try:
        return any(not d.endswith(".undone") for d in os.listdir(UPDATES))
    except OSError:
        return False


def code_notify(args):
    """For osyr-update-check.timer: checks and, if there is something new not yet
    announced, says so on the desktop. Once per version."""
    if read_kv(CONF).get("AUTO_CHECK", "yes") != "yes":
        return Result(True, "UPDATE_CHECK_DISABLED", text="Automatic update check disabled in " + CONF)
    from . import image
    if os.path.exists(image.slots.INSTALL_INFO):
        ires = image.image_check(args)
        if ires.code == "UPDATE_AVAILABLE":
            return notify_once(ires, ires.data["commit"], "New OSYR version available",
                               f"OSYR {ires.data['version']}: type /update in the OSYR chat. Help: Super+F1")
    res = code_check(args)
    if res.code == "UPDATE_FETCH_FAILED":  # no network: normal on a laptop, try again tomorrow
        return Result(True, "UPDATE_CHECK_SKIPPED", res.data, text="No network: update check skipped")
    latest = (res.data or {}).get("latest")
    if res.code not in ("UPDATE_AVAILABLE", "UPDATE_NONE") or not latest:
        return res
    needs_image = bool((res.data or {}).get("needs_image"))
    if res.code == "UPDATE_NONE" and not needs_image:
        return res
    if res.code == "UPDATE_AVAILABLE":
        n = sum(len(v) for k, v in res.data["changes"].items() if k != "kept")
        return notify_once(res, latest, "OSYR update available",
                           f"{n} file(s) to update. Type /update in the OSYR chat. Help: Super+F1")
    from . import image
    if os.path.exists(image.slots.INSTALL_INFO):
        return res  # installed on disk: the new version arrives with update image when it is published
    return notify_once(res, latest, "New OSYR image available",
                       "Some changes need a new image: download it from ccmai.org/osyr")


def notify_once(res, key, title, body):
    """Notifies on the desktop once per version (key)."""
    try:
        with open(NOTIFIED) as f:
            if f.read().strip() == key:
                return res  # already notified about this version
    except OSError:
        pass
    from . import notify
    sent = notify.send_to_user(title, body)
    if sent:  # without a graphical session it does not count as notified: repeat next time
        os.makedirs(STATE, exist_ok=True)
        with open(NOTIFIED, "w") as f:
            f.write(key + "\n")
    res.data["notified"] = sent
    return res


def code_apply(args):
    r = need_root("apply")
    if r:
        return r
    try:
        new, _, branch = fetch()
    except NoRelease as e:
        return Result(True, "UPDATE_NO_RELEASE", {"error": str(e)},
                      "There is no signed OSYR release yet.", text=f"No signed release: {e}")
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
        return fail("UPDATE_FETCH_FAILED", "Check the network with: osyr net status",
                    {"error": str(e)}, text=f"Could not download the OSYR code: {e}")
    base = installed_commit()
    if is_older(new, base):
        return Result(True, "UPDATE_NONE", {"installed": base, "latest": new},
                      text=f"Installed code is newer than {branch}: nothing to do.")
    steps, image, commits, base_ok = plan(base, new)
    if not any(steps[k] for k in ("update", "add", "remove", "conflict")):
        return Result(True, "UPDATE_NONE", {"latest": new}, text="OSYR code is up to date.")

    # Confirmation: the contract asks for a token; in a terminal a yes is enough
    token = token_for(new, steps)
    if args.confirm != token:
        lines = summary(new, steps, image, commits, base_ok)
        if not args.json and sys.stdin.isatty() and args.confirm is None:
            print("\n".join(lines))
            if input("Apply these changes? [y/N] ").strip().lower() not in ("y", "yes", "s", "si", "sí"):
                return Result(True, "UPDATE_CANCELLED", text="Nothing changed.")
        else:
            return Result(False, "CONFIRM_REQUIRED", {"latest": new, "changes": steps},
                          f"Run: sudo osyr update code apply --confirm {token}",
                          confirm=token, text="\n".join(lines))

    # Backup of everything about to be touched
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(UPDATES, stamp)
    os.makedirs(os.path.join(bdir, "files"), exist_ok=True)
    manifest = {"from": base, "to": new, "date": stamp, "saved": [], "added": []}
    for k in ("update", "remove"):
        for s in steps[k]:
            src = os.path.join(ROOT, s["path"])
            dst = os.path.join(bdir, "files", s["path"])
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            st = os.stat(src)
            manifest["saved"].append({"path": s["path"], "mode": st.st_mode & 0o7777,
                                      "uid": st.st_uid, "gid": st.st_gid})
    manifest["added"] = [s["path"] for s in steps["add"]] + \
                        [s["path"] + ".osyr-new" for s in steps["conflict"]]
    with open(os.path.join(bdir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    for s in steps["update"] + steps["add"]:
        write_file(s["path"], blob(new, f"{OVERLAY}/{s['path']}"), s["mode"])
    for s in steps["conflict"]:
        write_file(s["path"] + ".osyr-new", blob(new, f"{OVERLAY}/{s['path']}"), s["mode"])
    for s in steps["remove"]:
        os.unlink(os.path.join(ROOT, s["path"]))
    with open(CURRENT, "w") as f:
        f.write(new + "\n")

    touched = [s["path"] for k in ("update", "add", "remove") for s in steps[k]]
    hint, live_lines = go_live(touched)
    data = {"from": base, "to": new, "changes": steps, "backup": bdir, "needs_image": image}
    lines = [f"Updated to {new[:10]}: {len(touched)} files."] + live_lines
    if steps["conflict"]:
        lines.append("Local changes kept; compare with the .osyr-new files:")
        lines += [f"  /{s['path']}" for s in steps["conflict"]]
    lines.append("Undo with: /update undo in the OSYR chat (or: sudo osyr update code rollback)")
    return Result(True, "OK", data, hint, text="\n".join(lines))


def code_rollback(args):
    r = need_root("rollback")
    if r:
        return r
    try:
        last = sorted(d for d in os.listdir(UPDATES) if not d.endswith(".undone"))[-1]
    except (OSError, IndexError):
        return fail("UPDATE_NO_BACKUP", text="There is no update to undo.")
    bdir = os.path.join(UPDATES, last)
    with open(os.path.join(bdir, "manifest.json")) as f:
        m = json.load(f)
    for path in m["added"]:
        try:
            os.unlink(os.path.join(ROOT, path))
        except FileNotFoundError:
            pass
    for s in m["saved"]:
        dest = os.path.join(ROOT, s["path"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(os.path.join(bdir, "files", s["path"]), dest)
        os.chmod(dest, s["mode"])
        if os.geteuid() == 0:
            os.chown(dest, s["uid"], s["gid"])
    if m.get("from"):
        with open(CURRENT, "w") as f:
            f.write(m["from"] + "\n")
    else:
        try:
            os.unlink(CURRENT)
        except FileNotFoundError:
            pass
    os.rename(bdir, bdir + ".undone")
    touched = [s["path"] for s in m["saved"]] + list(m["added"])
    hint, live_lines = go_live(touched)
    return Result(True, "OK", {"restored": m.get("from"), "undone": m["to"]}, hint,
                  text="\n".join([f"Restored the code from before {m['date']}."] + live_lines))


def go_live(touched):
    """Puts what changed into use without restarting (osyr.live); restart only if needed.
    Returns (hint, lines for the person)."""
    if ROOT != "/":
        return None, []  # root of another slot or of tests: nothing running to touch
    from . import live
    p = live.plan(touched)
    done, session_ok = live.apply_system(p)
    lines = []
    if done:
        lines.append("Restarted now: " + ", ".join(done) + ".")
    if any(u.startswith("osyr-llama") for u in done):
        lines.append("The AI model is reloading (about a minute).")
    if p["session"] and not session_ok:
        p["reboot"].append("the desktop session")
    if p["reboot"]:
        mark_reboot()
        return "Restart the computer to finish: " + ", ".join(p["reboot"]) + " is read at start-up.", lines
    if p["session"]:
        lines.append("The OSYR chat reloads the rest by itself (no restart needed); "
                     "if you updated from a terminal, type /update in the chat.")
    else:
        lines.append("Nothing to restart: the changes are already in use.")
    return None, lines


def register(sub):
    p = sub.add_parser("update", help="update OSYR")
    s = p.add_subparsers(dest="action", required=True)
    c = s.add_parser("code", help="scripts and configuration from the OSYR git repository")
    cs = c.add_subparsers(dest="code_action", required=True)
    cs.add_parser("check", help="download and show what would change").set_defaults(func=code_check)
    a = cs.add_parser("apply", help="apply the changes (asks first)")
    a.add_argument("--confirm", metavar="TOKEN")
    a.set_defaults(func=code_apply)
    cs.add_parser("rollback", help="undo the last update").set_defaults(func=code_rollback)
    cs.add_parser("notify", help="check and tell the desktop once per new version (daily timer)"
                  ).set_defaults(func=code_notify)
    from . import image
    image.register(s)
    a = s.add_parser("apply", help="install what is new: the whole system if there is a new version "
                     "(installed OSYR), otherwise the code (asks first)")
    a.add_argument("--confirm", metavar="TOKEN")
    a.set_defaults(func=update_apply)
    u = s.add_parser("undo", help="undo the last update (code first, then the whole system)")
    u.add_argument("--confirm", metavar="TOKEN", help="(whole system) the token it asked for")
    u.set_defaults(func=update_undo)
    s.add_parser("notify", help="same as 'code notify'").set_defaults(func=code_notify)


def update_apply(args):
    """/update: a whole new version if there is one (it brings the code too), otherwise the code."""
    from . import image
    if os.path.exists(image.slots.INSTALL_INFO):
        r = image.image_check(args)
        if r.code == "UPDATE_AVAILABLE":
            return image.image_apply(args)
        if r.code not in ("UPDATE_NONE", "UPDATE_IMAGE_NOT_INSTALLED") and not args.json:
            print(r.text)
    return code_apply(args)


def update_undo(args):
    """Undoes the last update: a code update of this root or, if there is none, the image one."""
    if has_code_backup():
        return code_rollback(args)
    from . import image
    return image.image_rollback(args)  # asks in the terminal; without one, asks for the token
