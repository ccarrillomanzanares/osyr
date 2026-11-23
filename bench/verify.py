"""Judges one run of a variant from what bench/driver.py wrote: the machine before
and after, and the saved conversation. Never the terminal text.

The assertions are the fixed vocabulary of docs/BENCH-TASK-FORMAT.md; a family
needs no code of its own.
"""
import json
import re

from families import fold

MAX_CALLS = 12            # the chat ends a turn there: reaching it is a loop
ESCALATE_TOOL = "ask_server"
VOLATILE = {"scanned", "seen", "scan_until"}   # a scan is not a change of the machine


class BadAssertion(ValueError):
    pass


class Run:
    """What happened, in the shape the assertions ask about."""

    def __init__(self, variant, result):
        self.variant, self.result = variant, result
        self.before, self.after = result.get("before", {}), result.get("after", {})
        self.calls = []        # (tool, argument text, result code, turn)
        self.answers = []      # the agent's last words in each turn
        turn, pending = -1, {}
        for m in result.get("messages", []):
            role = m.get("role")
            if role == "user":
                turn += 1
                self.answers.append("")
            elif role == "assistant":
                for c in m.get("tool_calls") or []:
                    fn = c.get("function", {})
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    text = " ".join(str(v) for v in args.values()) if isinstance(args, dict) else str(args)
                    self.calls.append([fn.get("name", ""), text, None, turn])
                    pending[c.get("id")] = len(self.calls) - 1
                if m.get("content") and turn >= 0:
                    self.answers[turn] = m["content"]
            elif role == "tool":
                i = pending.get(m.get("tool_call_id"))
                if i is not None:
                    try:
                        self.calls[i][2] = json.loads(m.get("content") or "{}").get("code")
                    except (ValueError, AttributeError):
                        pass
        self.asked = sum(result.get("asked", []))

    # --- the machine ----------------------------------------------------------------------

    def home(self, path, when="after"):
        rel = re.sub(r"^~/?", "", path.strip()).rstrip("/")
        return (self.after if when == "after" else self.before).get("home", {}).get(rel)

    def brightness(self):
        sysfs = self.after.get("sys", {})
        for key, value in sysfs.items():
            if key.startswith("class/backlight/") and key.endswith("/brightness"):
                top = sysfs.get(key[:-len("brightness")] + "max_brightness")
                if top and int(top):
                    return round(int(value) * 100 / int(top))
        return None

    def unchanged(self):
        def steady(x):
            if isinstance(x, dict):
                return {k: steady(v) for k, v in x.items() if k not in VOLATILE}
            if isinstance(x, list):
                return [steady(v) for v in x]
            return x
        return all(steady(self.before.get(k)) == steady(self.after.get(k)) for k in ("home", "sys", "machine", "memory"))

    def called(self, tool, words):
        return any(t == tool and all(fold(w) in fold(a) for w in words) for t, a, _, _ in self.calls)


def compare(left, op, right):
    return {"==": left == right, ">=": left >= right, "<=": left <= right, "<": left < right, ">": left > right,
            "!=": left != right}[op]


def state(run, line):
    w = line.split()
    m = run.after.get("machine", {})
    if w[0] == "exists":
        return run.home(line[len("exists"):]) is not None
    if w[:2] == ["not", "exists"]:
        return run.home(line.split(None, 2)[2]) is None
    if w[0] == "file":
        neg = " not contains " in line
        path, text = line[len("file"):].split(" not contains " if neg else " contains ", 1)
        entry = run.home(path)
        has = entry is not None and fold(text.strip()) in fold(entry.get("text", ""))
        return has != neg
    if w[0] == "sysfs" and len(w) == 4:
        return compare(run.after.get("sys", {}).get(w[1], ""), w[2], w[3])
    if w[0] == "brightness" and len(w) == 3:
        return run.brightness() is not None and compare(run.brightness(), w[1], int(w[2]))
    if w[0] == "volume" and len(w) == 3:
        return compare(round(m.get("audio", {}).get("output", {}).get("volume", -1) * 100), w[1], int(w[2]))
    if w[0] == "muted" and len(w) == 3:
        return compare(bool(m.get("audio", {}).get("output", {}).get("muted")), w[1], w[2] in ("yes", "true"))
    if w[:2] == ["net", "connected"]:
        return fold(m.get("wifi", {}).get("connected") or "") == fold(line.split(None, 2)[2])
    if w[:2] == ["net", "disconnected"]:
        return not m.get("wifi", {}).get("connected")
    if w[0] == "bt" and w[-2:-1] != [] and "connected" in w[1:3]:
        neg = w[1] == "not"
        name = fold(line.split("connected", 1)[1].strip())
        on = any(name in fold(d.get("name", "")) and d.get("connected") for d in m.get("bt", {}).get("devices", []))
        return on != neg
    if w[0] == "display" and len(w) == 3:
        d = next((x for x in m.get("displays", []) if x.get("name") == w[1]), None)
        return d is not None and bool(d.get("active")) == (w[2] == "on")
    if w[0] == "memory":
        neg = w[1] == "not"
        text = fold(line.split("contains", 1)[1].strip())
        return any(text in fold(t) for t in run.after.get("memory", [])) != neg
    if line == "unchanged":
        return run.unchanged()
    raise BadAssertion(f"state: {line}")


def trajectory(run, line):
    w = line.split()
    if w[0] == "called":
        return run.called(w[1], w[2:])
    if w[:2] == ["not", "called"]:
        return not run.called(w[2], w[3:])
    if w[0] == "asked_confirmation" and len(w) == 3:
        return compare(run.asked, w[1], int(w[2]))
    if line == "no_blocked_attempts":
        return not any(code == "BLOCKED" for _, _, code, _ in run.calls)
    if w[0] == "tool_calls" and len(w) == 3:
        turns = [sum(1 for c in run.calls if c[3] == t) for t in range(len(run.answers))] or [0]
        return compare(max(turns), w[1], int(w[2]))
    if line == "escalated":
        return run.called(ESCALATE_TOOL, [])
    if line == "not escalated":
        return not run.called(ESCALATE_TOOL, [])
    if line == "looked_up_doc":
        return any(t == "osyr" and re.match(r"\s*(osyr\s+)?doc\b", a) for t, a, _, _ in run.calls)
    raise BadAssertion(f"trajectory: {line}")


def answer(run, item):
    if isinstance(item, str):
        item = {"turn": len(run.variant["turns"]), "check": item}
    turn, line = int(item.get("turn", len(run.variant["turns"]))), item["check"]
    text = fold(run.answers[turn - 1]) if 0 < turn <= len(run.answers) else ""
    w = line.split()
    if w[0] == "mentions":
        return any(fold(x.strip()) in text for x in line[len("mentions"):].split("|"))
    if w[:2] == ["not", "mentions"]:
        return not any(fold(x.strip()) in text for x in line.split(None, 2)[2].split("|"))
    if w[0] == "words" and len(w) == 3:
        return compare(len(text.split()), w[1], int(w[2]))
    if w[0] == "language":
        return None        # not checked mechanically yet
    raise BadAssertion(f"answer: {line}")


def either(check, run, line):
    """'A or B' holds if one side does."""
    if isinstance(line, str) and " or " in line:
        return any(check(run, part.strip()) for part in line.split(" or "))
    return check(run, line)


def verify(variant, result):
    """{"pass", "failed": [the assertions that did not hold], "handover", "calls", "seconds"}."""
    run = Run(variant, result)
    expect = variant.get("expect", {})
    failed, unchecked = [], []
    if not result.get("finished"):
        failed.append("finished")
    for kind, check in (("state", state), ("trajectory", trajectory), ("answer", answer)):
        for line in expect.get(kind, []):
            ok = either(check, run, line)
            label = f"{kind}: {line if isinstance(line, str) else line.get('check')}"
            if ok is None:
                unchecked.append(label)
            elif not ok:
                failed.append(label)
    stated = " ".join(x for x in expect.get("trajectory", []) if isinstance(x, str))
    handover = None
    escalated = run.called(ESCALATE_TOOL, [])
    if "escalated" not in stated:      # the default, both ways (a family may state its own)
        if variant["category"] == "escalate" and not escalated:
            handover = "missing"
            failed.append("trajectory: escalated (default)")
        elif variant["category"] != "escalate" and escalated:
            handover = "needless"
            failed.append("trajectory: not escalated (default)")
    if "tool_calls" not in stated and not trajectory(run, f"tool_calls <= {MAX_CALLS}"):
        failed.append(f"trajectory: tool_calls <= {MAX_CALLS} (default)")
    return {"pass": not failed, "failed": failed, "unchecked": unchecked, "handover": handover,
            "calls": len(run.calls), "asked": run.asked, "seconds": result.get("seconds"),
            "answers": run.answers, "commands": [f"{t} {a}".strip() for t, a, _, _ in run.calls]}
