"""Task families of the benchmark: loading a family file and drawing its variants.

The format is described in docs/BENCH-TASK-FORMAT.md. A family is one situation;
variant n of a family is always the same draw (a generator seeded with the
family's id and n), so two models are compared on the same conversations.
"""
import copy
import glob
import hashlib
import os
import random
import re
import tomllib
import unicodedata

BENCH_VARIANTS = 10       # variants 0..9 are the benchmark's; training data is drawn from 10 on
LANGS = ("en", "es", "fr", "de", "it", "pt", "ca")
CATEGORIES = {
    "read": 1, "change": 1, "files": 1, "apps": 1, "write": 1, "web": 1, "memory": 1,
    "admin-services": 2, "admin-disk": 2, "admin-net": 2, "admin-users": 2, "admin-procs": 2,
    "diagnose": 3, "safety-decline": 0, "safety-ambiguous": 0, "safety-dangerous": 0,
    "escalate": 0, "unseen-command": 0, "known-failures": 0, "multi-step": 0,
}
SLOT = re.compile(r"\{([a-z_][a-z0-9_]*)(?:\.([a-z_][a-z0-9_]*))?\}")
PERCENT = {"en": "percent", "es": "por ciento", "fr": "pour cent", "de": "Prozent", "it": "per cento",
           "pt": "por cento", "ca": "per cent"}


class FamilyError(ValueError):
    pass


def load(path):
    with open(path, "rb") as f:
        fam = tomllib.load(f)
    fam["path"] = path
    for key in ("id", "category", "level", "split", "turns", "expect"):
        if key not in fam:
            raise FamilyError(f"{path}: no '{key}'")
    if fam["category"] not in CATEGORIES:
        raise FamilyError(f"{path}: unknown category {fam['category']!r}")
    if fam["split"] not in ("train", "heldout"):
        raise FamilyError(f"{path}: split must be train or heldout")
    if fam["level"] not in (1, 2, 3):
        raise FamilyError(f"{path}: level must be 1, 2 or 3")
    fam.setdefault("needs", [])
    fam.setdefault("vars", {})
    fam.setdefault("setup", {})
    fam.setdefault("voice", False)
    langs = [k for k in fam["turns"][0] if k in LANGS]
    fam.setdefault("langs", langs)
    for i, turn in enumerate(fam["turns"]):
        for lang in fam["langs"]:
            if not turn.get(lang):
                raise FamilyError(f"{path}: turn {i + 1} has no wording in '{lang}'")
    return fam


def load_all(folder):
    fams = [load(p) for p in sorted(glob.glob(os.path.join(folder, "**", "*.toml"), recursive=True))]
    seen = {}
    for f in fams:
        if f["id"] in seen:
            raise FamilyError(f"{f['path']}: id {f['id']!r} is also in {seen[f['id']]}")
        seen[f["id"]] = f["path"]
    return fams


def fill(value, slots):
    """'value' with every {slot} and {slot.key} replaced, at any depth."""
    if isinstance(value, str):
        def one(m):
            v = slots.get(m.group(1))
            if v is None:
                raise FamilyError(f"unknown slot {{{m.group(1)}}}")
            if m.group(2):
                if not isinstance(v, dict) or m.group(2) not in v:
                    raise FamilyError(f"slot {m.group(1)} has no '{m.group(2)}'")
                v = v[m.group(2)]
            elif isinstance(v, dict):
                raise FamilyError(f"slot {m.group(1)} holds several values: write {{{m.group(1)}.key}}")
            return str(v)
        return SLOT.sub(one, value)
    if isinstance(value, list):
        return [fill(v, slots) for v in value]
    if isinstance(value, dict):
        return {fill(k, slots): fill(v, slots) for k, v in value.items()}
    return value


def spoken(text, lang):
    """The request as a speech recogniser hands it over: no capitals at the start, little
    punctuation, signs as words."""
    text = text.replace("%", " " + PERCENT.get(lang, "percent"))
    text = re.sub(r"[¿¡\"«»()]", "", text)
    text = re.sub(r"[.!?;:]+(\s|$)", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:1].lower() + text[1:]


def draw(fam, n, attempt=0):
    """One draw for variant n: {"id", "family", "n", "lang", "mode", "vars", "setup", "turns", "expect"}."""
    seed = int.from_bytes(hashlib.sha256(f"{fam['id']}#{n}#{attempt}".encode()).digest()[:8], "big")
    rng = random.Random(seed)
    langs = fam["langs"]
    lang = langs[n % len(langs)]
    # one variant in four is spoken; languages go round in another step, so each gets its share
    mode = "voice" if fam["voice"] and n % 4 == 3 else "text"
    slots = {name: rng.choice(values) for name, values in sorted(fam["vars"].items())}
    turns = []
    for turn in fam["turns"]:
        text = fill(rng.choice(turn[lang]), slots)
        t = {"user": spoken(text, lang) if mode == "voice" else text, "confirm": turn.get("confirm", "yes")}
        for extra in ("password",):
            if extra in turn:
                t[extra] = fill(turn[extra], slots)
        turns.append(t)
    return {"id": f"{fam['id']}#{n}", "family": fam["id"], "n": n, "lang": lang, "mode": mode,
            "category": fam["category"], "level": fam["level"], "split": fam["split"],
            "vars": slots, "setup": fill(copy.deepcopy(fam["setup"]), slots), "turns": turns,
            "expect": fill(copy.deepcopy(fam["expect"]), slots)}


def same(v):
    return (v["lang"], v["mode"], repr(v["turns"]), repr(sorted(v["vars"].items(), key=str)))


def variants(fam, count=BENCH_VARIANTS, first=0):
    """Variants first..first+count-1. Variant n is the first draw for n that is not the same
    conversation as an earlier variant, so it is always the same one, on any machine."""
    out, seen = [], set()
    for n in range(first + count):
        for attempt in range(200):
            v = draw(fam, n, attempt)
            if same(v) not in seen:
                break
        else:
            raise FamilyError(f"{fam['id']}: no new conversation left for variant {n}: "
                              "the family needs more values or wordings")
        seen.add(same(v))
        out.append(v)
    return out[first:]


def variant(fam, n):
    return variants(fam, 1, n)[0]


def fold(text):
    """Lower case, no accents: how names and answers are compared."""
    text = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()
