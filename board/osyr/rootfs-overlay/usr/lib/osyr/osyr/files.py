"""osyr files — find, list and read the user's files (read only).

Works in the home folder, on mounted disks and USB sticks, and in /tmp. It
never reads where secrets are (AI provider keys, SSH, GnuPG, wifi passwords):
not even on request. It reads in parts so each fits in ~1,000 tokens; PDFs
with pdftotext, if the image includes it.

With a remote AI provider, osyr-chat asks before sending it the contents of
a file (as with the clipboard).
"""
import os
import re
import shutil
import subprocess
import time
import unicodedata

from .result import Result, fail

HOME = os.path.expanduser("~")
ALLOWED = [HOME, "/run/media", "/media", "/mnt", "/tmp", "/usr/share/osyr"]
SECRETS = [".config/osyr", ".ssh", ".gnupg", ".local/share/keyrings", ".netrc", ".password-store"]
CHUNK = 3000          # characters per read
FIND_MAX = 20
LIST_MAX = 60
SCAN_MAX = 20000      # at most this many files checked in a search
CONTENT_MAX_BYTES = 1_000_000
TEXT_EXT = {".txt", ".md", ".csv", ".json", ".conf", ".ini", ".log", ".py", ".sh", ".html", ".htm",
            ".xml", ".yaml", ".yml", ".toml", ".js", ".css", ".c", ".h", ".tex", ".srt", ".ics", ".vcf"}


def resolve(path):
    """Absolute real path (no links); relative ones start from the home folder."""
    q = os.path.expanduser(path or "~")
    return os.path.realpath(q if os.path.isabs(q) else os.path.join(HOME, q))


def check(p):
    """None if it can be read; otherwise, the error."""
    if not any(p == a or p.startswith(a.rstrip("/") + "/") for a in ALLOWED):
        return fail("FILES_OUTSIDE", "Only your folder, external disks and /tmp.", {"path": p})
    rel = os.path.relpath(p, HOME) if p.startswith(HOME + "/") else ""
    if any(rel == s or rel.startswith(s + "/") for s in SECRETS):
        return fail("FILES_PRIVATE", "That folder holds keys and passwords: OSYR never reads it.", {"path": p})
    if not os.path.exists(p):
        return fail("FILES_NOT_FOUND", "Run: osyr files find <words>", {"path": p})
    return None


def short(p):
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + "/") else p


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.0f} TB"


def walk(root):
    """Files under root, skipping hidden and secret folders, nearest first."""
    seen = 0
    for d, dirs, names in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in ("__pycache__", "node_modules"))
        for n in names:
            if n.startswith("."):
                continue
            seen += 1
            if seen > SCAN_MAX:
                return
            yield os.path.join(d, n)


def fold(text):
    """Lower case without accents, to compare names ('Música' = 'musica')."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def terms_of(words):
    """Search terms: quoted phrases are split, and words of one or two letters
    ('de', 'la', 'of') are dropped when there are longer ones."""
    terms = [t for w in words for t in re.split(r"[\s,;]+", fold(w)) if t]
    return [t for t in terms if len(t) > 2] or terms


def find(args):
    root = resolve(args.inside)
    err = check(root)
    if err:
        return err
    terms = terms_of(args.words)
    hits, partial = [], []
    for p in walk(root):
        name = fold(os.path.basename(p))
        matched = sum(t in name for t in terms)
        ok = matched == len(terms)
        if not ok and args.content and os.path.splitext(name)[1] in TEXT_EXT:
            try:
                if os.path.getsize(p) <= CONTENT_MAX_BYTES:
                    body = fold(open(p, encoding="utf-8", errors="ignore").read())
                    ok = all(t in body or t in name for t in terms)
            except OSError:
                pass
        if ok:
            hits.append(p)
            if len(hits) >= FIND_MAX:
                break
        elif matched:
            partial.append((matched, p))
    # Nothing has every word: offer the names that have most of them
    loose = not hits and bool(partial)
    if loose:
        best = max(m for m, _ in partial)
        hits = [p for m, p in partial if m == best][:FIND_MAX]
    found = []
    for p in hits:
        st = os.stat(p)
        found.append({"path": short(p), "size": human(st.st_size),
                      "modified": time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))})
    found.sort(key=lambda h: h["modified"], reverse=True)
    if not found:
        return Result(True, "FILES_NONE", {"files": []},
                      None if args.content else "Try: osyr files find <words> --content", text="Nothing found.")
    data = {"files": found, "partial": True} if loose else {"files": found}
    return Result(True, "OK", data,
                  "No name has all the words: these have some of them." if loose else None,
                  text="\n".join(f'{h["modified"]}  {h["size"]:>7}  {h["path"]}' for h in found))


def by_name(name):
    """Files in the home folder called exactly like this (accents and case aside)."""
    want = fold(name)
    out = []
    for p in walk(HOME):
        if fold(os.path.basename(p)) == want:
            out.append(p)
            if len(out) >= FIND_MAX:
                break
    return out


def list_(args):
    p = resolve(args.path)
    err = check(p)
    if err:
        return err
    if not os.path.isdir(p):
        return fail("FILES_NOT_A_FOLDER", f"Run: osyr files read {short(p)}", {"path": short(p)})
    entries = []
    for n in sorted(os.listdir(p), key=lambda n: (not os.path.isdir(os.path.join(p, n)), n.lower())):
        if n.startswith("."):
            continue
        full = os.path.join(p, n)
        entries.append(n + "/" if os.path.isdir(full) else f"{n} ({human(os.path.getsize(full))})")
    more = len(entries) > LIST_MAX
    return Result(True, "OK", {"path": short(p), "entries": entries[:LIST_MAX], "more": more},
                  text=f"{short(p)}:\n" + "\n".join("  " + e for e in entries[:LIST_MAX])
                  + ("\n  ..." if more else ""))


def text_of(p):
    ext = os.path.splitext(p)[1].lower()
    if ext == ".pdf":
        if not shutil.which("pdftotext"):
            return None, fail("FILES_PDF_UNSUPPORTED", "This OSYR image cannot read PDF files yet.")
        r = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return None, fail("FILES_UNREADABLE", None, {"error": r.stderr.strip()[-200:]})
        return r.stdout, None
    with open(p, "rb") as f:
        raw = f.read(4_000_000)
    if b"\0" in raw[:4096]:
        return None, fail("FILES_BINARY", "It is not a text file (image, program, archive...).",
                          {"path": short(p)})
    return raw.decode("utf-8", errors="replace"), None


def read(args):
    p = resolve(args.path)
    if not os.path.exists(p) and "/" not in args.path:
        # Only a name ('nota.txt') that is not in the home folder itself: look for it
        same = by_name(args.path)
        if len(same) == 1:
            p = same[0]
        elif same:
            return fail("FILES_SEVERAL", "Several files have that name: read one by its path.",
                        {"files": [short(x) for x in same]})
    err = check(p)
    if err:
        return err
    if os.path.isdir(p):
        return fail("FILES_IS_FOLDER", f"Run: osyr files list {short(p)}", {"path": short(p)})
    text, err = text_of(p)
    if err:
        return err
    start = max(0, args.start)
    part = text[start:start + CHUNK]
    nxt = start + len(part) if start + len(part) < len(text) else None
    return Result(True, "OK", {"path": short(p), "from": start, "next": nxt, "total": len(text), "text": part},
                  f"More: osyr files read '{short(p)}' --from {nxt}" if nxt else None, text=part)


def register(sub):
    p = sub.add_parser("files", help="find, list and read your files (read only)")
    s = p.add_subparsers(dest="action", required=True)
    f = s.add_parser("find", help="files whose name has these words")
    f.add_argument("words", nargs="+")
    f.add_argument("--in", dest="inside", default="~", help="folder to search (your folder by default)")
    f.add_argument("--content", action="store_true", help="also look inside text files")
    f.set_defaults(func=find)
    ls = s.add_parser("list", help="what is in a folder")
    ls.add_argument("path", nargs="?", default="~")
    ls.set_defaults(func=list_)
    r = s.add_parser("read", help="read a text or PDF file, in parts")
    r.add_argument("path")
    r.add_argument("--from", dest="start", type=int, default=0)
    r.set_defaults(func=read)
