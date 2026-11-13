#!/usr/bin/env python3
"""OSYR — cases for osyr/edits.py (the write and edit tools, and undo).

Usage: tests/edits_cases.py     prints the cases that fail; exit code 1 if any.
It works in a temporary home folder; nothing outside it is written.
"""
import os
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="osyr-edits-")
os.environ.update(HOME=HOME, XDG_STATE_HOME=os.path.join(HOME, ".local/state"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "board", "osyr", "rootfs-overlay", "usr", "lib", "osyr"))
from osyr import edits  # noqa: E402

FAILED, COUNT = [], [0]


def check(name, ok, detail=""):
    COUNT[0] += 1
    if not ok:
        FAILED.append(name)
        print(f"FAIL {name} {detail}")


def text(rel):
    return open(os.path.join(HOME, rel), encoding="utf-8").read()


def main():
    os.makedirs(f"{HOME}/proj")
    os.makedirs(f"{HOME}/.ssh")
    # write: a new file, then undo deletes it
    p = edits.plan_write("proj/notes.txt", "one\ntwo")
    check("write-plan-new", p["ok"] and not p["exists"] and p["show"][0] == "new file:", str(p)[:200])
    r = edits.apply(p)
    check("write-new", r["ok"] and text("proj/notes.txt") == "one\ntwo\n" and r["data"]["created"], str(r))
    check("write-same", edits.plan_write("proj/notes.txt", "one\ntwo\n")["code"] == "NO_CHANGE")
    # edit: one piece, shown as a diff; the mode of the file stays
    os.chmod(f"{HOME}/proj/notes.txt", 0o640)
    p = edits.plan_edit("~/proj/notes.txt", "two", "2")
    check("edit-plan", p["ok"] and "-two" in p["show"] and "+2" in p["show"], str(p.get("show")))
    r = edits.apply(p)
    check("edit", r["ok"] and text("proj/notes.txt") == "one\n2\n")
    check("edit-keeps-mode", os.stat(f"{HOME}/proj/notes.txt").st_mode & 0o777 == 0o640)
    check("edit-not-found", edits.plan_edit("proj/notes.txt", "three", "3")["code"] == "EDIT_NOT_FOUND")
    open(f"{HOME}/proj/twice.txt", "w").write("a\na\n")
    check("edit-ambiguous", edits.plan_edit("proj/twice.txt", "a", "b")["code"] == "EDIT_AMBIGUOUS")
    check("edit-missing-file", edits.plan_edit("proj/none.txt", "a", "b")["code"] == "FILES_NOT_FOUND")
    # undo, newest first: the edit, then the creation
    r = edits.undo()
    check("undo-edit", r["ok"] and r["data"]["restored"] and text("proj/notes.txt") == "one\ntwo\n", str(r))
    r = edits.undo()
    check("undo-create", r["ok"] and r["data"]["deleted"] and not os.path.exists(f"{HOME}/proj/notes.txt"), str(r))
    check("undo-nothing", edits.undo()["code"] == "NOTHING_TO_UNDO")
    # replacing a whole file keeps the old one for undo
    open(f"{HOME}/proj/a.txt", "w").write("old\n")
    p = edits.plan_write("a.txt", "new\n", cwd=f"{HOME}/proj")
    check("write-replace-plan", p["ok"] and p["exists"] and p["show"][0].startswith("replaces a file of 1"))
    edits.apply(p)
    edits.undo()
    check("write-replace-undo", text("proj/a.txt") == "old\n")
    # where it may not write
    for name, path in (("secret", "~/.ssh/id_ed25519"), ("secret-folder", "~/.ssh/new"), ("system", "/usr/bin/ls"),
                       ("passwd", "/etc/passwd"), ("sudoers", "/etc/sudoers.d/x"), ("boot", "/boot/grub/grub.cfg"),
                       ("own-permissions", "~/.config/osyr/permissions.json"), ("disk", "/dev/sda"),
                       ("dotdot", "~/proj/../../../etc/shadow")):
        r = edits.plan_write(path, "x\n")
        check(f"blocked-{name}", r.get("code") == "BLOCKED", str(r)[:160])
    check("public-key-allowed", edits.plan_write("~/.ssh/config", "Host x\n").get("ok") is True)
    os.makedirs(f"{HOME}/Documents")
    r = edits.plan_write("/Documents/x.txt", "x")
    check("slash-for-home", r["code"] == "WRONG_PATH" and "~/Documents/x.txt" in r["hint"], str(r))
    check("no-folder", edits.plan_write("~/nowhere/x.txt", "x")["code"] == "FILES_NO_FOLDER")
    check("is-folder", edits.plan_write("~/proj", "x")["code"] == "FILES_IS_FOLDER")
    open(f"{HOME}/proj/bin.dat", "wb").write(b"\0\1\2")
    check("binary", edits.plan_edit("proj/bin.dat", "a", "b")["code"] == "FILES_BINARY")
    check("too-big", edits.plan_write("proj/big.txt", "x" * 100_001)["code"] == "FILES_TOO_BIG")
    # a configuration file asks for root (not written here: no sudo in the test)
    p = edits.plan_write("/etc/osyr-test-file.conf", "x\n")
    check("config-needs-root", p.get("ok") and p["root"], str(p)[:160])
    # permissions: per folder, never the home folder itself
    p = edits.plan_write("proj/b.txt", "x\n")
    check("rule-folder", edits.rule(p) == {"write": f"{HOME}/proj"})
    check("rule-not-home", edits.rule(edits.plan_write("c.txt", "x\n")) is None)
    check("rule-covers-subfolder", edits.allowed_by([{"write": HOME + "/proj"}], p)
          and not edits.allowed_by([{"write": HOME + "/other"}], p))
    total = COUNT[0]
    print(f"{total - len(FAILED)}/{total} cases pass")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
