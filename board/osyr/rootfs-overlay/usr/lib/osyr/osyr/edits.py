"""osyr edits — the agent creates and changes text files, with a way back.

Design in docs/SHELL-DESIGN.md ("The write tool"). Three steps, so that the
person sees the change before it happens:

  plan_write(path, content) / plan_edit(path, old, new)
      checks where it is (the same zones as the shell: shellguard.check_write)
      and returns what would change, or an error for the model
  apply(plan)
      keeps a copy of the previous version and writes the new one
  undo()
      puts the previous version of the last change back

Files outside the user's folders (one configuration file in /etc) are written
with sudo: osyr-chat asks for the password in the terminal first.
"""
import difflib
import json
import os
import subprocess
import tempfile
import time

from . import shellguard

UNDO = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "osyr", "undo")
KEEP = 20
MAX_CHARS = 100_000
DIFF_LINES = 40


def err(code, hint, **data):
    return {"ok": False, "code": code, "data": data, "hint": hint}


def _home():
    return os.path.expanduser("~")


def short(p):
    h = _home()
    return "~" + p[len(h):] if p == h or p.startswith(h + "/") else p


def _locate(path, cwd):
    """(absolute path, needs root, error). The rules are the classifier's."""
    home = _home()
    if not path or "\x00" in path:
        return None, False, err("USAGE", "Give the path of the file.")
    p = shellguard.resolve(path, cwd or home, home)
    # '/Documents/x.txt' for '~/Documents/x.txt': a common slip, and not an attack on the system
    top = "/" + p.strip("/").split("/")[0]
    if path.startswith("/") and not os.path.exists(top) and os.path.exists(home + top):
        return p, False, err("WRONG_PATH", f"There is no {top} folder. The user's folders are under ~: "
                             f"use ~{p}", path=p)
    if os.path.isdir(p):
        return p, False, err("FILES_IS_FOLDER", "That is a folder; give the path of a file.", path=short(p))
    z = shellguard.zone(p, home)
    blocked = shellguard.check_write(path, cwd or home, home, root=z != "user")
    if blocked is None and shellguard.SECRET_DIRS.search(p) and not shellguard.PUBLIC_IN_SECRET_DIRS.search(p):
        blocked = shellguard.block("it touches a folder that holds passwords or keys")
    if blocked:
        return p, False, err("BLOCKED", "Never allowed from the assistant. Tell the user they can do it themselves "
                             "in a terminal. Do not try another way to do the same thing.", reason=blocked["reason"])
    parent = os.path.dirname(p)
    if not os.path.isdir(parent):
        return p, False, err("FILES_NO_FOLDER", f"The folder {short(parent)} does not exist: create it first "
                             "(shell: mkdir -p).", path=short(p))
    target = p if os.path.exists(p) else parent
    return p, not os.access(target, os.W_OK), None


def _read(p):
    """(text, error) of an existing file."""
    try:
        with open(p, "rb") as f:
            raw = f.read(4 * MAX_CHARS + 1)
    except PermissionError:
        return None, err("FILES_UNREADABLE", "Only the administrator can read this file; it cannot be edited "
                         "from here.", path=short(p))
    except OSError as e:
        return None, err("FILES_UNREADABLE", None, path=short(p), error=str(e))
    if b"\0" in raw[:4096]:
        return None, err("FILES_BINARY", "It is not a text file.", path=short(p))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, err("FILES_BINARY", "It is not a UTF-8 text file.", path=short(p))
    if len(text) > MAX_CHARS:
        return None, err("FILES_TOO_BIG", f"Files over {MAX_CHARS} characters cannot be changed from here.",
                         path=short(p))
    return text, None


def _diff(old, new, name):
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "before", "after", n=1, lineterm=""))[2:]
    more = len(lines) - DIFF_LINES
    return lines[:DIFF_LINES] + ([f"... {more} more lines"] if more > 0 else [])


def plan_write(path, content, cwd=None):
    """A new file, or all of an existing one."""
    p, root, e = _locate(path, cwd)
    if e:
        return e
    if not isinstance(content, str):
        return err("USAGE", "content must be text.")
    if len(content) > MAX_CHARS:
        return err("FILES_TOO_BIG", f"At most {MAX_CHARS} characters at a time.", path=short(p))
    if content and not content.endswith("\n"):
        content += "\n"
    exists = os.path.exists(p)
    old = None
    if exists:
        old, e = _read(p)
        if e and e["code"] != "FILES_BINARY":
            return e
        if e:
            return err("FILES_BINARY", "The file there is not text; it is not replaced from here.", path=short(p))
        if old == content:
            return err("NO_CHANGE", "The file already has exactly this content.", path=short(p))
    first = content.splitlines()[:8]
    show = ([f"replaces a file of {len(old.splitlines())} lines"] + _diff(old, content, p)) if exists else \
        ["new file:"] + ["+" + line for line in first] + (["+..."] if len(content.splitlines()) > 8 else [])
    return {"ok": True, "path": p, "root": root, "exists": exists, "old": old, "new": content, "show": show,
            "summary": {"path": short(p), "created": not exists, "lines": len(content.splitlines())}}


def plan_edit(path, old, new, cwd=None):
    """One piece of text replaced by another; `old` must appear exactly once."""
    p, root, e = _locate(path, cwd)
    if e:
        return e
    if not os.path.exists(p):
        return err("FILES_NOT_FOUND", "There is no such file. To create it, use write.", path=short(p))
    if not isinstance(old, str) or not isinstance(new, str) or not old:
        return err("USAGE", "edit needs old (the text there now) and new (the text that replaces it).")
    text, e = _read(p)
    if e:
        return e
    n = text.count(old)
    if n == 0:
        return err("EDIT_NOT_FOUND", "The old text is not in the file, character for character. Read the file "
                   "again and copy the piece exactly, with its spaces.", path=short(p))
    if n > 1:
        return err("EDIT_AMBIGUOUS", f"The old text appears {n} times. Include more of the surrounding lines "
                   "so that it appears once.", path=short(p), times=n)
    changed = text.replace(old, new, 1)
    if changed == text:
        return err("NO_CHANGE", "old and new are the same.", path=short(p))
    if len(changed) > MAX_CHARS:
        return err("FILES_TOO_BIG", f"The file would pass {MAX_CHARS} characters.", path=short(p))
    return {"ok": True, "path": p, "root": root, "exists": True, "old": text, "new": changed,
            "show": _diff(text, changed, p),
            "summary": {"path": short(p), "created": False, "lines": len(changed.splitlines())}}


def _sudo(*args):
    p = subprocess.run(["sudo", "-n", *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise OSError(p.stderr.strip() or "sudo failed")


def _put(p, text, root):
    """Writes the whole file at once: a reader never sees half of it."""
    if root:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".osyr-edit", delete=False) as f:
            f.write(text)
        try:
            _sudo("cp", "--", f.name, p)  # onto an existing file: its owner and mode stay
        finally:
            os.unlink(f.name)
        return
    tmp = f"{p}.osyr-tmp-{os.getpid()}"
    try:
        mode = os.stat(p).st_mode & 0o7777
    except OSError:
        mode = None
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, p)


def _entries():
    try:
        return sorted(n[:-5] for n in os.listdir(UNDO) if n.endswith(".json"))
    except OSError:
        return []


def apply(plan):
    """Keeps the previous version and writes the new one. Returns the result for the model."""
    p = plan["path"]
    os.makedirs(UNDO, mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 10**9:09d}"
    meta = {"path": p, "existed": plan["exists"], "root": plan["root"], "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        if plan["exists"]:
            with open(os.open(os.path.join(UNDO, stamp + ".bak"), os.O_WRONLY | os.O_CREAT, 0o600), "w",
                      encoding="utf-8") as f:
                f.write(plan["old"])
        _put(p, plan["new"], plan["root"])
        with open(os.path.join(UNDO, stamp + ".json"), "w") as f:
            json.dump(meta, f)
    except OSError as e:
        return err("WRITE_FAILED", None, path=short(p), error=str(e))
    for old in _entries()[:-KEEP]:
        for ext in (".json", ".bak"):
            try:
                os.unlink(os.path.join(UNDO, old + ext))
            except OSError:
                pass
    return {"ok": True, "code": "OK", "data": plan["summary"],
            "hint": "To put the previous version back: osyr files undo"}


def last():
    """The change an undo would take back, or None."""
    names = _entries()
    if not names:
        return None
    try:
        meta = json.load(open(os.path.join(UNDO, names[-1] + ".json")))
    except (OSError, ValueError):
        return None
    meta["id"] = names[-1]
    return meta


def undo():
    """Puts back the previous version of the last file changed (or deletes it, if it was new)."""
    meta = last()
    if not meta:
        return err("NOTHING_TO_UNDO", "The assistant has not changed any file (or it was already undone).")
    p, bak = meta["path"], os.path.join(UNDO, meta["id"] + ".bak")
    try:
        if meta["existed"]:
            with open(bak, encoding="utf-8") as f:
                _put(p, f.read(), meta.get("root"))
        elif os.path.exists(p):
            _sudo("rm", "--", p) if meta.get("root") else os.unlink(p)
    except OSError as e:
        return err("UNDO_FAILED", "A file outside your folders needs the administrator password: run "
                   "'osyr files undo' in a terminal after 'sudo -v'." if meta.get("root") else None,
                   path=short(p), error=str(e))
    for ext in (".json", ".bak"):
        try:
            os.unlink(os.path.join(UNDO, meta["id"] + ext))
        except OSError:
            pass
    return {"ok": True, "code": "OK", "data": {"path": short(p), "restored": bool(meta["existed"]),
                                               "deleted": not meta["existed"], "changed_at": meta["time"]},
            "hint": None}


def rule(plan):
    """The permission a "yes, always" would save: writing in that folder. None when it
    must ask every time (outside the user's files, or the home folder itself)."""
    home = _home()
    folder = os.path.dirname(plan["path"])
    if plan["root"] or shellguard.zone(folder, home) != "user" or shellguard.under(home, folder) \
            or folder in shellguard.USER_ROOTS or shellguard.SECRET_DIRS.search(folder + "/"):
        return None
    return {"write": folder}


def allowed_by(rules, plan):
    r = rule(plan)
    return bool(r) and any(x.get("write") and shellguard.under(r["write"], x["write"]) for x in rules)
