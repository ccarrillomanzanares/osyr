#!/usr/bin/env python3
"""Cases for the benchmark's machinery (bench/): drawing variants, judging a run and
the report's intervals. No model and no session: the conversations are made up.

Usage: tests/bench_cases.py     prints the cases that fail; exit code 1 if any.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bench"))
import families  # noqa: E402
import report  # noqa: E402
import verify  # noqa: E402

bad = []


def check(name, ok, detail=""):
    if not ok:
        bad.append(f"{name}: {detail}")


FAMILY = '''
id = "t-delete"
category = "files"
level = 1
split = "heldout"
voice = true
[vars]
gone = ["a.txt", "b.txt", "c.txt", "d.txt"]
disk = [{ size = "20G", used = "19G" }, { size = "8G", used = "7G" }]
[setup]
files = [{ path = "~/Documents/{gone}", size = "{disk.used}" }]
[[turns]]
en = ["Delete {gone}.", "remove {gone} please", "Get rid of {gone}!"]
es = ["Borra {gone}.", "quita {gone}", "Elimina el archivo {gone}, que ocupa {disk.used}."]
confirm = "yes"
[expect]
state = ["not exists ~/Documents/{gone}"]
trajectory = ["asked_confirmation >= 1"]
answer = [{ turn = 1, check = "mentions {gone}|deleted" }]
'''


def call(name, **args):
    return {"id": f"c{len(args)}{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def result(messages, before=None, after=None, asked=(1,), finished=True):
    return {"finished": finished, "seconds": 10, "asked": list(asked), "messages": messages,
            "before": before or {}, "after": after or {}}


def drawing(tmp):
    path = os.path.join(tmp, "t-delete.toml")
    with open(path, "w") as f:
        f.write(FAMILY)
    fam = families.load(path)
    vs = families.variants(fam)
    check("langs-in-turn", [v["lang"] for v in vs[:4]] == ["en", "es", "en", "es"], [v["lang"] for v in vs])
    check("one-in-four-spoken", [v["mode"] for v in vs[:8]].count("voice") == 2, [v["mode"] for v in vs])
    check("same-draw-every-time", families.variants(families.load(path)) == vs)
    check("variant-n-alone", families.variant(fam, 7) == vs[7] and families.variants(fam, 3, 10)[0]["n"] == 10)
    check("no-two-alike", len({families.same(v) for v in families.variants(fam, 16)}) == 16)
    v = vs[0]
    gone = v["vars"]["gone"]
    check("slots-filled", gone in v["turns"][0]["user"] and v["setup"]["files"][0]["path"] == f"~/Documents/{gone}"
          and v["setup"]["files"][0]["size"] == v["vars"]["disk"]["used"]
          and v["expect"]["state"] == [f"not exists ~/Documents/{gone}"], v)
    spoken = next(x for x in vs if x["mode"] == "voice")["turns"][0]["user"]
    check("spoken-form", spoken[0].islower() and not spoken.endswith((".", "!")), spoken)
    check("percent-as-words", families.spoken("Pon el brillo al 80%.", "es") == "pon el brillo al 80 por ciento")
    try:
        families.variants(fam, 60)
        check("runs-out", False, "60 different variants from 48 combinations")
    except families.FamilyError:
        pass
    with open(path, "w") as f:
        f.write(FAMILY.replace("{gone}.\"", "{missing}.\"", 1))
    try:
        families.variants(families.load(path))
        check("unknown-slot", False)
    except families.FamilyError:
        pass
    return fam, vs


def judging(vs):
    v = vs[0]
    gone = v["vars"]["gone"]
    home = {"Documents": {"dir": True}, f"Documents/{gone}": {"size": 5}}
    done = [{"role": "user", "content": "x"},
            {"role": "assistant", "content": "", "tool_calls": [call("shell", command=f"rm ~/Documents/{gone}")]},
            {"role": "tool", "tool_call_id": "c1shell", "content": json.dumps({"ok": True, "code": "OK"})},
            {"role": "assistant", "content": f"I deleted {gone}."}]
    ok = verify.verify(v, result(done, {"home": home}, {"home": {"Documents": {"dir": True}}}))
    check("pass", ok["pass"] and ok["calls"] == 1 and ok["commands"] == [f"shell rm ~/Documents/{gone}"], ok)
    still = verify.verify(v, result(done, {"home": home}, {"home": home}))
    check("state-fails", still["failed"] == [f"state: not exists ~/Documents/{gone}"], still)
    silent = verify.verify(v, result(done, {"home": home}, {"home": {}}, asked=(0,)))
    check("did-not-ask", silent["failed"] == ["trajectory: asked_confirmation >= 1"], silent)
    cut = verify.verify(v, result(done, {"home": home}, {"home": {}}, finished=False))
    check("not-finished", cut["failed"][0] == "finished", cut)
    # The hand-over is expected not to happen outside 'escalate', and to happen inside
    handed = done[:1] + [{"role": "assistant", "content": "", "tool_calls": [call("ask_server", task="delete it")]},
                         {"role": "tool", "tool_call_id": "c1ask_server", "content": json.dumps({"ok": True, "code": "HANDED_OVER"})}] + done[1:]
    r = verify.verify(v, result(handed, {"home": home}, {"home": {}}))
    check("needless-handover", r["handover"] == "needless" and not r["pass"], r)
    hard = dict(v, category="escalate")
    check("handover-expected", verify.verify(hard, result(handed, {"home": home}, {"home": {}}))["pass"]
          and verify.verify(hard, result(done, {"home": home}, {"home": {}}))["handover"] == "missing")
    either = dict(v, expect=dict(v["expect"], trajectory=["escalated or not escalated"]))
    check("family-states-its-own", verify.verify(either, result(handed, {"home": home}, {"home": {}}))["pass"])
    # A loop: more calls in one turn than the chat allows
    loop = done[:1] + [m for i in range(13) for m in (
        {"role": "assistant", "content": "", "tool_calls": [dict(call("shell", command=f"ls {i}"), id=f"k{i}")]},
        {"role": "tool", "tool_call_id": f"k{i}", "content": json.dumps({"ok": True, "code": "OK"})})] + done[-1:]
    r = verify.verify(v, result(loop, {"home": home}, {"home": {}}))
    check("loop", r["failed"] == ["trajectory: tool_calls <= 12 (default)"], r["failed"])
    # The pretend machine, and 'unchanged'
    machine = {"audio": {"output": {"volume": 0.7, "muted": False}}, "wifi": {"connected": "Casa-Lopez", "scanned": True},
               "bt": {"devices": [{"name": "Sony WH-1000XM4", "connected": True}]},
               "displays": [{"name": "eDP-1", "active": False}],
               "system": {"sudo_until": 5, "services": {"cups": {"state": "active", "process": "cupsd"},
                                                        "sshd": {"state": "failed"}},
                          "processes": [{"name": "htop"}], "killed": [640], "clock": {"timezone": "Asia/Tokyo"}}}
    photo = {"home": {"Documents/Apuntes.tar.gz": {"size": 9}, "Documents/Apuntes": {"dir": True}}, "machine": machine, "memory": ["The user's dog is called Rufus"],
             "files": {"/etc/cups/cupsd.conf": {"size": 9, "text": "Port 631\n"}},
             "sys": {"class/backlight/x/brightness": "300", "class/backlight/x/max_brightness": "1000"}}
    run = verify.Run(v, result(done, photo, photo))
    for line in ("volume == 70", "muted == no", "net connected casa-lopez", "bt connected sony", "bt not connected jbl",
                 "display eDP-1 off", "memory contains rufus", "memory not contains password", "brightness == 30",
                 "sysfs class/backlight/x/brightness == 300", "unchanged", "service cups is active",
                 "service sshd is failed", "exists /etc/cups/cupsd.conf", "not exists /etc/cups/other.conf",
                 "exists ~/Documents/Apuntes.*", "not exists ~/Documents/Otra.*", "process cupsd running", "process sshd gone", "process htop running", "process ffmpeg gone",
                 "process osyr-chat running", "process sway gone", "timezone Asia/Tokyo",
                 "file /etc/cups/cupsd.conf contains Port 631", "file /etc/cups/cupsd.conf not contains Lisen"):
        check(f"state '{line}'", verify.state(run, line) is True)
    scanned = dict(photo, machine=dict(machine, wifi={"connected": "Casa-Lopez", "scanned": False}))
    check("a-scan-is-not-a-change", verify.Run(v, result(done, scanned, photo)).unchanged())
    louder = dict(photo, machine=dict(machine, audio={"output": {"volume": 0.4, "muted": False}}))
    check("a-change-is", not verify.Run(v, result(done, louder, photo)).unchanged())
    blocked = [dict(m) for m in done]
    blocked[2] = dict(blocked[2], content=json.dumps({"ok": False, "code": "BLOCKED"}))
    check("blocked-attempt", not verify.trajectory(verify.Run(v, result(blocked)), "no_blocked_attempts")
          and verify.trajectory(run, "no_blocked_attempts"))
    check("called", verify.trajectory(run, "called shell rm") and verify.trajectory(run, "not called osyr")
          and not verify.trajectory(run, "looked_up_doc"))
    check("answers", verify.answer(run, "mentions nothing|deleted") and verify.answer(run, "not mentions sorry")
          and verify.answer(run, "words <= 5") and verify.answer(run, "language == en") is None)
    # What it said before a command counts as said: the name is often there, not in the last words
    said = [done[0], {"role": "assistant", "content": "It is ffmpeg. I will stop it.",
                      "tool_calls": [dict(call("shell", command="kill 7"), id="s1")]},
            {"role": "tool", "tool_call_id": "s1", "content": json.dumps({"ok": True, "code": "OK"})},
            {"role": "assistant", "content": "Stopped."}]
    check("answer-whole-turn", verify.answer(verify.Run(v, result(said)), "mentions ffmpeg"))
    try:
        verify.state(run, "the moon is full")
        check("unknown-assertion", False)
    except verify.BadAssertion:
        pass


def scoring():
    rows = []
    for fam, passes in (("a", [1] * 10), ("b", [1] * 5 + [0] * 5), ("c", [0] * 10), ("d", [1] * 9 + [0])):
        for n, p in enumerate(passes):
            rows.append({"family": fam, "n": n, "run": 0, "pass": bool(p), "failed": [] if p else ["state: x"],
                         "split": "heldout" if fam == "d" else "train", "category": "files", "level": 1,
                         "lang": "en" if n % 2 else "es", "mode": "text", "handover": None, "seconds": 30, "calls": 1})
    fams = report.by_family(rows)
    check("family-scores", fams["b"] == {n: (1.0 if n < 5 else 0.0) for n in range(10)})
    check("mean-of-families", abs(report.score(fams) - (1 + 0.5 + 0 + 0.9) / 4) < 1e-9)
    lo, hi = report.interval(fams)
    check("interval-around", lo < 0.6 < hi and 0.1 < lo and hi < 1.0 and hi - lo > 0.3, (lo, hi))
    check("interval-repeatable", report.interval(fams) == (lo, hi))
    check("all-pass-no-width", report.interval({"a": fams["a"]}) == (1.0, 1.0))
    # Run twice, a variant counts by its share of passes
    twice = rows + [dict(r, run=1, **{"pass": True}) for r in rows if r["family"] == "c"]
    check("share-of-runs", report.by_family(twice)["c"][0] == 0.5)
    # Against another run, variant by variant
    better = [dict(r, **{"pass": True}) if r["family"] in ("b", "c") else r for r in rows]
    d = report.difference(report.by_family(better), fams)
    check("difference", abs(report.score(d) - (0 + 0.5 + 1 + 0) / 4) < 1e-9, report.score(d))
    text = report.report(better, rows)
    check("report-text", "| heldout |" in text and "Difference: +37.5 points" in text and "| d | 9/10 |" in text, text)
    check("report-zero-in", "includes zero" in report.report(rows, rows))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        fam, vs = drawing(tmp)
        judging(vs)
    scoring()
    examples = families.load_all(os.path.join(ROOT, "bench", "examples")) + \
        families.load_all(os.path.join(ROOT, "bench", "tasks"))
    for f in examples:
        try:
            families.variants(f, 20)       # the benchmark's ten and ten more for training data
        except families.FamilyError as e:
            check(f"example {f['id']}", False, e)
    check("examples", len(examples) >= 8)
    for b in bad:
        print("FAIL", b)
    print(f"bench cases: {'%d failed' % len(bad) if bad else 'all passed'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
