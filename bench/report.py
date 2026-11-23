#!/usr/bin/env python3
"""Scores of a benchmark run, each with its interval over the families.

    report.py RUN_DIR                the scores of one run
    report.py RUN_DIR BASE_DIR       the same, and the difference with another run
                                     (same variants, compared one by one)

A family's score is the share of its variants that pass (a variant run several
times counts by its share of passes). A score is the mean of its families'
scores. The interval is 95 %, by resampling the families and, inside each, its
variants: the uncertainty comes from them, not from repeating the run.
"""
import collections
import json
import os
import random
import sys

RESAMPLES = 2000


def load(folder):
    rows = []
    with open(os.path.join(folder, "results.jsonl")) as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def by_family(rows):
    """{family: {variant n: share of its runs that passed}}"""
    runs = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        runs[r["family"]][r["n"]].append(1.0 if r["pass"] else 0.0)
    return {fam: {n: sum(v) / len(v) for n, v in vs.items()} for fam, vs in runs.items()}


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def score(fams):
    return mean(mean(v.values()) for v in fams.values())


def interval(fams, seed=1):
    """(low, high) of the mean of family scores, resampling families and their variants."""
    names = sorted(fams)
    if not names:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    values = {f: [fams[f][n] for n in sorted(fams[f])] for f in names}
    out = []
    for _ in range(RESAMPLES):
        total = 0.0
        for _ in names:
            v = values[rng.choice(names)]
            total += sum(rng.choice(v) for _ in v) / len(v)
        out.append(total / len(names))
    out.sort()
    return out[int(0.025 * RESAMPLES)], out[int(0.975 * RESAMPLES) - 1]


def difference(a, b):
    """Families of 'a' minus the same variants in 'b': {family: {n: difference}}."""
    out = {}
    for fam in a:
        both = {n: a[fam][n] - b[fam][n] for n in a[fam] if fam in b and n in b[fam]}
        if both:
            out[fam] = both
    return out


def pct(x):
    return "  n/a" if x != x else f"{100 * x:5.1f}"


def line(label, fams):
    lo, hi = interval(fams)
    variants = sum(len(v) for v in fams.values())
    return f"| {label} | {pct(score(fams))} | {pct(lo)} to {pct(hi)} | {len(fams)} | {variants} |"


def groups(rows, key):
    out = collections.defaultdict(list)
    for r in rows:
        out[r[key]].append(r)
    return dict(sorted(out.items(), key=lambda kv: str(kv[0])))


def table(title, rows, key):
    text = [f"### {title}", "", f"| {key} | score % | 95 % interval | families | variants |", "| --- | --- | --- | --- | --- |"]
    for value, part in groups(rows, key).items():
        text.append(line(value, by_family(part)))
    return "\n".join(text) + "\n"


def report(rows, base=None):
    text = []
    runs = sorted({r["run"] for r in rows})
    text.append(f"Runs: {len(runs)} · variants per run: {len({(r['family'], r['n']) for r in rows})} · "
                f"families: {len({r['family'] for r in rows})}\n")
    text.append("### Overall\n\n| split | score % | 95 % interval | families | variants |\n| --- | --- | --- | --- | --- |")
    for split, part in groups(rows, "split").items():
        text.append(line(split, by_family(part)))
    text.append(line("all", by_family(rows)) + "\n")
    for title, key in (("By level", "level"), ("By category", "category"), ("By language", "lang"), ("By mode", "mode")):
        text.append(table(title, rows, key))
    if len(runs) > 1:
        per_run = [score(by_family([r for r in rows if r["run"] == k])) for k in runs]
        text.append(f"Run by run (expected to be close at temperature 0): {', '.join(pct(s).strip() for s in per_run)}\n")
    needless = sum(1 for r in rows if r.get("handover") == "needless")
    missing = sum(1 for r in rows if r.get("handover") == "missing")
    text.append(f"Hand-over errors: handed over without need {needless}, did not hand over {missing} (of {len(rows)} runs)\n")
    secs = sorted(r["seconds"] for r in rows if r.get("seconds") is not None)
    if secs:
        text.append(f"Seconds per variant: median {secs[len(secs) // 2]:.0f}, longest {secs[-1]:.0f} · "
                    f"tool calls per variant: {mean(r.get('calls', 0) for r in rows):.1f}\n")
    fams = by_family(rows)
    partial = {f: v for f, v in fams.items() if mean(v.values()) < 1}
    if partial:
        text.append("### Families that do not pass every variant\n")
        text.append("| family | passed | first failed assertion (how many variants) | failing variants |\n| --- | --- | --- | --- |")
        for fam in sorted(partial, key=lambda f: mean(partial[f].values())):
            mine = [r for r in rows if r["family"] == fam]
            reasons = collections.Counter(r["failed"][0] for r in mine if r["failed"])
            bad = sorted({f"#{r['n']} {r['lang']}{' voice' if r['mode'] == 'voice' else ''}" for r in mine if not r["pass"]})
            why = "; ".join(f"{k} ({n})" for k, n in reasons.most_common(3))
            text.append(f"| {fam} | {sum(1 for v in partial[fam].values() if v == 1)}/{len(partial[fam])} | {why} | {', '.join(bad)} |")
        text.append("")
    if base is not None:
        a, b = by_family(rows), by_family(base)
        d = difference(a, b)
        lo, hi = interval(d)
        text.append("### Against the other run (same variants)\n")
        text.append(f"Difference: {100 * score(d):+.1f} points, 95 % interval {100 * lo:+.1f} to {100 * hi:+.1f} "
                    f"({len(d)} families, {sum(len(v) for v in d.values())} variants in common). "
                    + ("The interval leaves zero out." if lo > 0 or hi < 0 else "The interval includes zero."))
        text.append("")
    return "\n".join(text)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    rows = load(sys.argv[1])
    base = load(sys.argv[2]) if len(sys.argv) > 2 else None
    print(report(rows, base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
