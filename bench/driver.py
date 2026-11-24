#!/usr/bin/env python3
"""Runs one benchmark variant inside its disposable session (sandbox/session).

    driver.py /run/osyr/share        reads variant.json there, writes result.json

It puts the variant's files and notes in place, photographs the machine, holds
the conversation with the real osyr-chat on a terminal (typing each turn and
answering its questions as the variant says) and photographs the machine again.
It judges nothing: bench/verify.py does, outside, on what is written here.
Only the standard library of the image's Python.
"""
import hashlib
import json
import os
import pty
import re
import select
import signal
import sys
import time

HOME = os.path.expanduser("~")
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
PROMPT = re.compile(rb"(^|\n)\s*> $")
ASK = re.compile(rb"\[y/(a/)?N\] $")
PASSWORD = re.compile(rb"\(Enter to cancel\): $")
SUDO = re.compile(rb"\[sudo\] password for \w+: $")
OWN = (".local/share/osyr/chats", ".local/share/osyr/shell", ".local/state", ".cache", ".config/osyr/ai.json",
       ".bash_history", ".python_history")   # what a conversation writes by itself; not "the machine changed"
TEXT_MAX = 65536


def size(text):
    m = re.fullmatch(r"\s*([0-9.]+)\s*([KMG]?)B?\s*", str(text), re.I)
    return int(float(m.group(1)) * {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30}[m.group(2).upper()])


def setup(variant):
    s = variant.get("setup", {})
    for f in s.get("files", []):
        path = os.path.expanduser(f["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.islink(path):
            # never through a link: /usr/sbin/crond is one to busybox, and a family's own 'crond'
            # written there replaced every command of the session (24 Nov)
            os.unlink(path)
        if f.get("folder"):
            os.makedirs(path, exist_ok=True)
        elif "text" in f:
            with open(path, "w") as out:
                out.write(f["text"] if f["text"].endswith("\n") else f["text"] + "\n")
        else:
            # A file of that size that takes no memory: the home folder lives in RAM
            with open(path, "wb") as out:
                out.truncate(size(f.get("size", "1K")))
        if "mode" in f:
            os.chmod(path, int(str(f["mode"]), 8))
        if "age_days" in f:
            t = time.time() - float(f["age_days"]) * 86400
            os.utime(path, (t, t))
    if s.get("memory"):
        path = os.path.join(HOME, ".local/share/osyr/memory.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as out:
            json.dump([{"text": t, "date": "2026-11-01"} for t in s["memory"]], out)


def tree(root, skip=()):
    """{relative path: {"size", "sha", "text"?} or {"dir": True}} under root."""
    out = {}
    for base, dirs, files in os.walk(root):
        rel_base = os.path.relpath(base, root)
        dirs[:] = [d for d in dirs
                   if not any(os.path.normpath(os.path.join(rel_base, d)) == s for s in skip)]
        for d in dirs:
            out[os.path.normpath(os.path.join(rel_base, d))] = {"dir": True}
        for name in files:
            rel = os.path.normpath(os.path.join(rel_base, name))
            if any(rel == s or rel.startswith(s + "/") for s in skip):
                continue
            path = os.path.join(base, name)
            try:
                st = os.stat(path)
                entry = {"size": st.st_size}
                if st.st_size <= TEXT_MAX:
                    with open(path, "rb") as f:
                        data = f.read()
                    entry["sha"] = hashlib.sha256(data).hexdigest()[:16]
                    try:
                        entry["text"] = data.decode()
                    except UnicodeDecodeError:
                        pass
                out[rel] = entry
            except OSError:
                pass
    return out


def machine():
    """The pretend hardware as it stands: the doubles' state, or their scenario if untouched."""
    for path in (os.environ.get("OSYR_DOUBLES_STATE"), os.environ.get("OSYR_DOUBLES_SCENARIO"),
                 "/run/osyr/doubles/laptop.json"):
        if path and os.path.exists(path):
            with open(path) as f:
                state = json.load(f)
            state.pop("sysfs", None)
            return state
    return {}


def memory():
    try:
        with open(os.path.join(HOME, ".local/share/osyr/memory.json")) as f:
            return [i.get("text", "") for i in json.load(f)]
    except (OSError, ValueError):
        return []


def one_file(path):
    """A system file the variant names by its whole path: as in tree(), or None if it is not there."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if os.path.isdir(path):
        return {"dir": True}
    entry = {"size": st.st_size}
    if st.st_size <= TEXT_MAX:
        try:
            with open(path, "rb") as f:
                entry["text"] = f.read().decode()
        except (OSError, UnicodeDecodeError):
            pass
    return entry


WATCHED = []    # whole paths named in the variant's files and expectations


def disks(m):
    """{mount: {"size", "used"}} of the pretend disks, counted as the df double counts them."""
    out = {}
    for d in m.get("system", {}).get("disks", []):
        used = size(d.get("other", 0)) + size(d.get("used", 0))
        for base, _, names in os.walk(d["track"]) if d.get("track") else ():
            for n in names:
                try:
                    used += os.lstat(os.path.join(base, n)).st_size
                except OSError:
                    pass
        out[d["mount"]] = {"size": size(d["size"]), "used": min(size(d["size"]), used)}
    return out


def photo():
    files = {p: one_file(p) for p in WATCHED}
    return {"home": tree(HOME, OWN), "files": {p: e for p, e in files.items() if e is not None}, "sys": {k: v.get("text", "").strip() for k, v in tree("/sys").items()
                                              if "text" in v},
            "machine": machine(), "memory": memory(), "disks": disks(machine())}


def converse(turns, timeout, sudo_password="osyr"):
    """Types the turns into osyr-chat. Returns (finished, terminal text, questions asked per turn)."""
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "dumb"
        os.execvp("osyr-chat", ["osyr-chat"])
    out, seen, todo = b"", 0, list(turns)
    asked, current, finished = [], None, False
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            out += data
            continue
        tail = ANSI.sub(b"", out[seen:])[-400:]
        if not tail:
            continue
        if ASK.search(tail):
            seen = len(out)
            if current is not None:
                asked[-1] += 1
            os.write(fd, (b"y" if (current or {}).get("confirm", "yes") == "yes" else b"n") + b"\r")
        elif PASSWORD.search(tail):
            seen = len(out)
            if current is not None:
                asked[-1] += 1     # asking for the password is that command's confirmation
            answer = (current or {}).get("password", "") if (current or {}).get("confirm", "yes") == "yes" else ""
            os.write(fd, answer.encode() + b"\r")
        elif SUDO.search(tail):
            # The person types their password to sudo itself, in the terminal; a "no" is an empty line
            seen = len(out)
            agrees = (current or {}).get("confirm", "yes") == "yes"
            os.write(fd, (sudo_password if agrees else "").encode() + b"\r")
        elif PROMPT.search(tail):
            seen = len(out)
            if not todo:
                os.write(fd, b"/exit\r")
                finished = True
                time.sleep(1.5)
                break
            current = todo.pop(0)
            asked.append(0)
            os.write(fd, current["user"].encode() + b"\r")
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except OSError:
        pass
    return finished, ANSI.sub(b"", out).decode(errors="replace"), asked


def conversation():
    folder = os.path.join(HOME, ".local/share/osyr/chats")
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
        with open(os.path.join(folder, names[-1])) as f:
            return json.load(f)
    except (OSError, ValueError, IndexError):
        return []


def main():
    share = sys.argv[1]
    with open(os.path.join(share, "variant.json")) as f:
        variant = json.load(f)
    setup(variant)
    named = [f["path"] for f in variant.get("setup", {}).get("files", [])]
    named += re.findall(r"(?<![\w~])/[\w./-]+", " ".join(x for x in variant.get("expect", {}).get("state", [])
                                                         if isinstance(x, str)))
    WATCHED.extend(sorted({p for p in named if p.startswith("/")}))
    password = variant.get("setup", {}).get("machine", {}).get("system", {}).get("password", "osyr")
    if os.environ.get("OSYR_SYSTEM_LOCKED") == "1":
        # A whole system to administer: its settings are closed to a normal user, as on a real
        # one; the sudo double opens them while its command runs (tests/doubles/admin.py)
        os.system("chmod -R a-w /etc 2>/dev/null")
    before = photo()
    t0 = time.time()
    finished, terminal, asked = converse(variant["turns"], float(variant.get("timeout", 600)), password)
    result = {"id": variant["id"], "finished": finished, "seconds": round(time.time() - t0, 1), "asked": asked,
              "messages": conversation(), "before": before, "after": photo(), "terminal": terminal[-6000:]}
    with open(os.path.join(share, "result.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
