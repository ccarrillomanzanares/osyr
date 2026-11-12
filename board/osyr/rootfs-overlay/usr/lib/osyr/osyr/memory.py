"""osyr memory — what the assistant remembers about the person between conversations.

Short notes the person asks to keep ("remember that my sister is called Ana").
osyr-chat adds them to the system prompt, only with the local model: they are
not sent to a remote provider. They are stored in plain sight in
~/.local/share/osyr/memory.json: "osyr memory list" and "osyr memory forget"
show and delete them.

It never saves passwords, PINs or card numbers: it refuses.
"""
import json
import os
import re
import time

from .result import Result, fail

PATH = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                    "osyr", "memory.json")
MAX_ITEMS = 30
MAX_CHARS = 200        # per note
MAX_TOTAL = 2000       # all together, ~500 tokens of the system prompt
SENSITIVE = re.compile(r"pass(word|wd|phrase)|contrase|\bpin\b|\bcvv\b|\bcvc\b|clave|api[ _-]?key|token"
                       r"|\b(?:\d[ -]?){13,19}\b", re.I)


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            items = json.load(f)
        return [i for i in items if isinstance(i, dict) and i.get("text")]
    except (OSError, ValueError):
        return []


def save(items):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)


def prompt_block():
    """Text for the system prompt ('' if nothing is saved)."""
    items = load()
    if not items:
        return ""
    return ("\nWhat the user asked you to remember (they can see it with 'memory list' "
            "and erase it with 'memory forget'):\n" + "\n".join(f"- {i['text']}" for i in items))


def listing(items):
    return "\n".join(f"{n}. {i['text']}  ({i.get('date', '')})" for n, i in enumerate(items, 1))


def list_(args):
    items = load()
    if not items:
        return Result(True, "OK", {"items": []}, text="Nothing saved yet.")
    return Result(True, "OK", {"items": [{"n": n, **i} for n, i in enumerate(items, 1)]}, text=listing(items))


def add(args):
    text = " ".join(" ".join(args.text).split())
    if not text:
        return fail("MEMORY_EMPTY")
    if SENSITIVE.search(text):
        return fail("MEMORY_SENSITIVE", "Passwords, PINs, keys and card numbers are never saved.",
                    text="OSYR does not save passwords, PINs, keys or card numbers.")
    if len(text) > MAX_CHARS:
        return fail("MEMORY_TOO_LONG", f"Keep it under {MAX_CHARS} characters.", {"length": len(text)})
    items = load()
    if any(i["text"].lower() == text.lower() for i in items):
        return Result(True, "OK", {"text": text}, text="Already saved.")
    if len(items) >= MAX_ITEMS or sum(len(i["text"]) for i in items) + len(text) > MAX_TOTAL:
        return fail("MEMORY_FULL", "Run: osyr memory list, then osyr memory forget <n>", {"items": len(items)})
    items.append({"text": text, "date": time.strftime("%Y-%m-%d")})
    save(items)
    return Result(True, "OK", {"n": len(items), "text": text}, text=f"Saved: {text}")


def forget(args):
    items = load()
    if args.which == "all":
        save([])
        return Result(True, "OK", {"forgotten": len(items)}, text=f"Forgot {len(items)} note(s).")
    try:
        n = int(args.which)
        gone = items.pop(n - 1) if n >= 1 else None
    except (ValueError, IndexError):
        gone = None
    if not gone:
        return fail("MEMORY_NOT_FOUND", "Run: osyr memory list", {"which": args.which})
    save(items)
    return Result(True, "OK", {"text": gone["text"]}, text=f"Forgotten: {gone['text']}")


def register(sub):
    p = sub.add_parser("memory", help="what the assistant remembers about you")
    s = p.add_subparsers(dest="action", required=True)
    s.add_parser("list", help="everything saved").set_defaults(func=list_)
    a = s.add_parser("add", help="remember this (never passwords)")
    a.add_argument("text", nargs="+")
    a.set_defaults(func=add)
    f = s.add_parser("forget", help="forget note n (see list), or all")
    f.add_argument("which")
    f.set_defaults(func=forget)
