#!/usr/bin/env python3
"""A summary of the web sandbox's log, for its periodic review.

    sudo sandbox/report.py [LOG] [--days N]      (default: /var/log/osyr-sandbox/sessions.log, 7 days)

Per day: sessions, how they ended, how long they lasted, the model requests
and tokens they used, and the errors. The log holds no conversation.
"""
import collections
import json
import sys
import time


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else "/var/log/osyr-sandbox/sessions.log"
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 7
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    per_day = collections.defaultdict(lambda: {"sessions": [], "errors": 0, "warm": [], "starts": 0})
    with open(path) as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            day = e.get("time", "")[:10]
            if day < since:
                continue
            d = per_day[day]
            if e.get("event") == "session":
                d["sessions"].append(e)
            elif e.get("event") == "error" or "error" in e:
                d["errors"] += 1
            elif e.get("event") == "warm":
                d["warm"].append(e.get("seconds", 0))
            elif e.get("event") == "start":
                d["starts"] += 1
    if not per_day:
        print(f"Nothing in {path} since {since}.")
        return 0
    print(f"{'day':<11} {'sessions':>8} {'talked':>6} {'median min':>10} {'requests':>8} {'tokens in':>10} {'out':>8} "
          f"{'errors':>6} {'restarts':>8}  how they ended")
    for day in sorted(per_day):
        d = per_day[day]
        s = d["sessions"]
        used = [x for x in s if x.get("requests")]
        mins = sorted(x.get("seconds", 0) / 60 for x in s)
        why = collections.Counter(x.get("why") for x in s)
        print(f"{day:<11} {len(s):>8} {len(used):>6} {(mins[len(mins) // 2] if mins else 0):>10.1f} "
              f"{sum(x.get('requests', 0) for x in s):>8} {sum(x.get('tokens_in', 0) for x in s):>10} "
              f"{sum(x.get('tokens_out', 0) for x in s):>8} {d['errors']:>6} {d['starts']:>8}  "
              + ", ".join(f"{k} {n}" for k, n in why.most_common()))
    slow = [w for d in per_day.values() for w in d["warm"] if w > 30]
    if slow:
        print(f"\nThe model server had lost the fixed prompt {len(slow)} times (it took {max(slow):.0f} s at most to read again).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
