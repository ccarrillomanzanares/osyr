#!/usr/bin/env python3
"""OSYR — is the published image newer than the one running? (no network)

Usage: tests/image_cases.py     prints the cases that fail; exit code 1 if any.
A temporary git repository with two unrelated histories stands in for the OSYR
clone: the one before the history was started again (12 Nov 2026) and the one
after. image.is_newer() must offer the new image to a machine built from the old
history, with or without the old commit in its clone, and never go back.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="osyr-image-")
os.environ.update(OSYR_UPDATE_ROOT=TMP, OSYR_UPDATE_STATE=TMP, OSYR_UPDATE_SRC=os.path.join(TMP, "src"))
sys.path.insert(0, os.path.join(ROOT, "board", "osyr", "rootfs-overlay", "usr", "lib", "osyr"))
from osyr import image  # noqa: E402

SRC = os.environ["OSYR_UPDATE_SRC"]
image.code.fetch = lambda: None


def git(*args, date=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", "-C", SRC, *args], env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(message, date):
    git("commit", "-q", "--allow-empty", "-m", message, date=date)
    return git("rev-parse", "HEAD")


def built(date):
    os.makedirs(os.path.join(TMP, "etc", "osyr"), exist_ok=True)
    with open(image.code.BUILD_INFO, "w") as f:
        f.write(f"COMMIT=x\nDIRTY=no\n{'DATE=' + date if date else ''}\n")


def main():
    names, failed = [], []

    def check(name, ok, detail=""):
        names.append(name)
        if not ok:
            failed.append(name)
            print(f"FAIL {name} {detail}")

    os.makedirs(SRC)
    git("init", "-q", "-b", "old")
    old1 = commit("v0.30.0", "2026-11-04T07:00:00Z")
    old2 = commit("v0.31.0", "2026-11-10T07:00:00Z")
    git("checkout", "-q", "--orphan", "main")
    new1 = commit("base tree", "2026-11-12T07:00:00Z")
    new2 = commit("v0.32.0", "2026-11-13T17:00:00Z")
    missing = "1" * 40

    built("2026-11-04T07:40:21Z")
    check("same-history-newer", image.is_newer(new2, new1))
    check("same-history-not-back", not image.is_newer(new1, new2))
    check("same-commit", not image.is_newer(new2, new2))
    check("nothing-installed", image.is_newer(new2, None))
    check("old-history-newer", image.is_newer(new2, old1))
    check("old-history-newer-in-old", image.is_newer(old2, old1))
    check("old-commit-not-in-clone", image.is_newer(new2, missing))
    # An old signed manifest replayed to a machine that is already on the new history
    built("2026-11-13T17:31:53Z")
    check("old-history-not-back", not image.is_newer(old2, new2))
    check("unknown-base-not-back", not image.is_newer(new1, missing))
    built(None)
    try:
        image.is_newer(new2, missing)
        check("no-date-refused", False, "no error")
    except image.ImageError:
        check("no-date-refused", True)
    try:
        image.is_newer(missing, new1)
        check("new-commit-must-exist", False, "no error")
    except image.ImageError:
        check("new-commit-must-exist", True)

    print(f"{len(names) - len(failed)}/{len(names)} cases pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
