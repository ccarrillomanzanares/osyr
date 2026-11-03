"""osyr doc — the OSYR help by topic, for the agent and for the person.

The source is the same as for Super+F1 and help.html (/usr/share/osyr/help.md):
each "## ..." section is a topic. Given words, it returns the closest sections
(words in common, the title counts three times), trimmed to fit in ~1,000
tokens. Without words, the list of topics.
"""
import os
import re

from .result import Result, fail

HELP = os.environ.get("OSYR_HELP", "/usr/share/osyr/help.md")
MAX_CHARS = 3000
STOP = set("a an and are as at be by can do does for from how i in is it me my of on or the this "
           "to what when where which with you your".split())


def sections():
    try:
        text = open(HELP, encoding="utf-8").read()
    except OSError:
        return []
    out = []
    for block in re.split(r"(?m)^## ", text)[1:]:
        title, _, body = block.partition("\n")
        out.append((title.strip(), body.strip()))
    return out


def words(text):
    return {w for w in re.findall(r"[a-z0-9áéíóúñü+-]+", text.lower()) if w not in STOP and len(w) > 1}


def score(query, title, body):
    q = words(query)
    if not q:
        return 0
    return 3 * len(q & words(title)) + len(q & words(body))


def doc(args):
    secs = sections()
    if not secs:
        return fail("DOC_MISSING", None, {"file": HELP})
    if not args.words:
        topics = [t for t, _ in secs]
        return Result(True, "OK", {"topics": topics},
                      text="Topics:\n" + "\n".join(f"  {t}" for t in topics) + "\nRun: osyr doc <words>")
    query = " ".join(args.words)
    ranked = sorted(((score(query, t, b), t, b) for t, b in secs), reverse=True)
    best = [(t, b) for s, t, b in ranked if s > 0][:2]
    if not best:
        return Result(True, "DOC_NOT_FOUND", {"query": query, "topics": [t for t, _ in secs]},
                      "Nothing about that in the OSYR help: say so, do not invent steps.",
                      text=f'Nothing about "{query}" in the OSYR help.')
    out, used = [], 0
    for t, b in best:
        part = b[:MAX_CHARS - used]
        out.append({"topic": t, "text": part})
        used += len(part)
        if used >= MAX_CHARS:
            break
    return Result(True, "OK", {"query": query, "sections": out},
                  text="\n\n".join(f"## {s['topic']}\n{s['text']}" for s in out))


def register(sub):
    p = sub.add_parser("doc", help="OSYR help by topic: osyr doc <words>")
    p.add_argument("words", nargs="*")
    p.set_defaults(func=doc)
