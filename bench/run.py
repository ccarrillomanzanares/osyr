#!/usr/bin/env python3
"""Runs the benchmark: every variant of every family in its own disposable
session (sandbox/session), with the real osyr-chat and the model given, and
writes the trajectories, the verdicts and the report.

    bench/run.py --model FILE.gguf [options]     starts the image's llama-server with it
    bench/run.py --server HOST:PORT [options]    uses a llama-server that is already running
    bench/run.py --provider URL [options]        the model is a provider (an OpenAI-compatible base
                                                 address), as after /pair: the chat reaches it through
                                                 a model gate and treats it as remote (what the user
                                                 asked to remember is not sent). Its key, header and
                                                 host name: --second-key-file, --second-key-header,
                                                 --second-name

    --tasks DIR      the families (default: bench/examples)
    --only TEXT      only the families whose id contains TEXT
    --split S        train | heldout | all (default all)
    --variants N     variants per family (default 10: the benchmark's own)
    --runs N         times every variant is run (default 3)
    --label NAME     name of the results folder (default: the model's file name)
    --out DIR        where results folders go (default: build/bench)
    --no-shell       without the shell tool (OSYR_SHELL=0)
    --write          with the write and edit tools (OSYR_WRITE=1)
    --threads N      llama-server threads with --model (default 4)
    --minutes N      time limit of one variant (default 12)
    --second URL     a second model for ask_server: an OpenAI-compatible base address, reached
                     from the session through a model gate (sandbox/modelgate.py)
    --second-key-file F, --second-key-header H, --second-name HOST   its key, the header the key
                     goes in, and its host name when URL is a loopback address with TLS

Results: OUT/LABEL/results.jsonl (one line per variant and run), OUT/LABEL/runs/
(the full trajectory of each) and OUT/LABEL/report.md. A run that is repeated
with the same label goes on where it stopped.

The sampling is fixed: temperature 0 and one seed per run. See
docs/BENCH-TASK-FORMAT.md for the format and the scores.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "sandbox"))
import families  # noqa: E402
import modelgate  # noqa: E402
import relay  # noqa: E402
import report  # noqa: E402
import verify  # noqa: E402

TARGET = os.path.join(ROOT, "build", "output", "target")
SCENARIO = os.path.join(ROOT, "tests", "doubles", "laptop.json")


def merge(base, patch):
    """'patch' over 'base': tables are merged, anything else is replaced."""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            out[k] = merge(base.get(k), v)
        return out
    return patch


def start_server(model, threads, log):
    """The image's llama-server on a free local port, as tests/run.py starts it."""
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    flags = open("/proc/cpuinfo").read()
    variant = "haswell" if " avx2" in flags else "sandybridge" if " avx" in flags else "x64"
    slots = os.path.join(os.path.dirname(log), "slots")
    os.makedirs(slots, exist_ok=True)
    env = dict(os.environ, GGML_BACKEND_PATH=f"{TARGET}/usr/lib/ggml/libggml-cpu-{variant}.so")
    cmd = [f"{TARGET}/lib/ld-linux-x86-64.so.2", "--library-path", f"{TARGET}/usr/lib:{TARGET}/lib",
           f"{TARGET}/usr/bin/llama-server", "-m", model, "--host", "127.0.0.1", "--port", str(port),
           "-c", "8192", "-t", str(threads), "-np", "1", "--jinja",
           # the chat saves the fixed prompt it has read and restores it in the next session
           "--slot-save-path", slots]
    proc = subprocess.Popen(cmd, env=env, stdout=open(log, "w"), stderr=subprocess.STDOUT)
    end = time.time() + 600
    while time.time() < end:
        if proc.poll() is not None:
            raise SystemExit(f"llama-server ended: see {log}")
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3).status == 200:
                return proc, port
        except OSError:
            time.sleep(1)
    proc.kill()
    raise SystemExit("llama-server did not come up in ten minutes")


def one(variant, run, host, port, opts):
    """Runs one variant in a session. Returns what the driver wrote, or a stub if it wrote nothing."""
    work = tempfile.mkdtemp(prefix="osb-", dir="/tmp")   # short: a socket's path has a length limit
    share = os.path.join(work, "share")
    os.makedirs(share)
    try:
        with open(SCENARIO) as f:
            scenario = json.load(f)
        setup = variant.get("setup", {})
        scenario = merge(scenario, setup.get("machine", {}))
        scenario["sysfs"] = merge(scenario.get("sysfs", {}), setup.get("sysfs", {}))
        with open(os.path.join(share, "scenario.json"), "w") as f:
            json.dump(scenario, f)
        with open(os.path.join(share, "variant.json"), "w") as f:
            json.dump(dict(variant, timeout=opts.minutes * 60 - 60), f, ensure_ascii=False)
        shutil.copy(os.path.join(HERE, "driver.py"), share)
        gate = srv = None
        if opts.upstream is not None:   # a provider behind its gate: the second model, or the only one
            gate = modelgate.Gate(os.path.join(work, "model.sock"), opts.upstream, 100).start()
        if port:
            srv = relay.listen_socket(os.path.join(work, "llama.sock"), host, port)
            threading.Thread(target=relay.serve_forever, args=(srv, (host, port)), daemon=True).start()
        env = ["OSYR_SANDBOX=0", "OSYR_SANDBOX_SKEL=0", "OSYR_TEMPERATURE=0", f"OSYR_SEED={run + 1}",
               "OSYR_DOUBLES_SCENARIO=/run/osyr/share/scenario.json",
               f"OSYR_SHELL={0 if opts.no_shell else 1}", f"OSYR_WRITE={1 if opts.write else 0}"]
        cmd = [os.path.join(ROOT, "sandbox", "session"), "--minutes", str(opts.minutes), "--share", share]
        if "system" in scenario:   # an administration task: a whole system to change, and its doubles
            cmd.insert(1, "--system")
        for e in env:
            cmd += ["--env", e]
        cmd += [work, "python3", "/run/osyr/share/driver.py", "/run/osyr/share"]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=opts.minutes * 60 + 60)
        if srv is not None:
            srv.close()
        if gate is not None:
            gate.close()
        try:
            with open(os.path.join(share, "result.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"id": variant["id"], "finished": False, "messages": [], "asked": [], "before": {}, "after": {},
                    "error": (p.stdout + p.stderr)[-2000:]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model")
    ap.add_argument("--server")
    ap.add_argument("--tasks", default=os.path.join(HERE, "examples"))
    ap.add_argument("--only", default="")
    ap.add_argument("--split", default="all", choices=("train", "heldout", "all"))
    ap.add_argument("--variants", type=int, default=families.BENCH_VARIANTS)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--label")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "bench"))
    ap.add_argument("--no-shell", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--minutes", type=int, default=12)
    ap.add_argument("--provider")
    ap.add_argument("--second")
    ap.add_argument("--second-key-file")
    ap.add_argument("--second-key-header", default="Authorization")
    ap.add_argument("--second-name")
    opts = ap.parse_args()
    if sum(map(bool, (opts.model, opts.server, opts.provider))) != 1:
        ap.error("give --model FILE, --server HOST:PORT or --provider URL")
    if opts.provider and opts.second:
        ap.error("--provider is the only model: no --second with it")

    opts.upstream = None
    if opts.second or opts.provider:
        key = open(opts.second_key_file).read().strip() if opts.second_key_file else None
        opts.upstream = modelgate.Upstream(opts.second or opts.provider, key, opts.second_key_header, "", 1, opts.second_name,
                                           {"chat_template_kwargs": {"enable_thinking": False}})
    fams = [f for f in families.load_all(opts.tasks)
            if opts.only in f["id"] and opts.split in ("all", f["split"])]
    have = {"shell"} if not opts.no_shell else set()
    have |= {"write"} if opts.write else set()
    have |= {"server"} if opts.second else set()
    skipped = [f["id"] for f in fams if not set(f["needs"]) <= have]
    fams = [f for f in fams if set(f["needs"]) <= have]
    todo = [(r, v) for r in range(opts.runs) for f in fams for v in families.variants(f, opts.variants)]
    label = opts.label or os.path.splitext(os.path.basename(opts.model or (opts.server or "provider").replace(":", "-")))[0]
    out = os.path.join(opts.out, label)
    os.makedirs(os.path.join(out, "runs"), exist_ok=True)
    results = os.path.join(out, "results.jsonl")
    done = set()
    if os.path.exists(results):
        with open(results) as f:
            done = {(json.loads(x)["run"], json.loads(x)["id"]) for x in f if x.strip()}
    todo = [(r, v) for r, v in todo if (r, v["id"]) not in done]
    print(f"{len(fams)} families, {opts.variants} variants, {opts.runs} runs: {len(todo)} to run"
          + (f" ({len(done)} already done)" if done else "")
          + (f"; skipped for what they need: {', '.join(skipped)}" if skipped else ""), flush=True)

    proc = None
    if opts.model:
        proc, port = start_server(os.path.abspath(opts.model), opts.threads, os.path.join(out, "llama-server.log"))
        host = "127.0.0.1"
    elif opts.server:
        host, _, port = opts.server.rpartition(":")
        host, port = host or "127.0.0.1", int(port)
    else:
        host, port = None, 0
    t0 = time.time()
    try:
        for i, (r, v) in enumerate(todo, 1):
            res = one(v, r, host, port, opts)
            verdict = verify.verify(v, res)
            name = v["id"].replace("#", "-") + f"-run{r}.json"
            with open(os.path.join(out, "runs", name), "w") as f:
                json.dump({"variant": v, "result": res, "verdict": verdict}, f, ensure_ascii=False, indent=1)
            row = {"id": v["id"], "family": v["family"], "n": v["n"], "run": r, "lang": v["lang"], "mode": v["mode"],
                   "category": v["category"], "level": v["level"], "split": v["split"],
                   **{k: verdict[k] for k in ("pass", "failed", "handover", "calls", "asked", "seconds")}}
            with open(results, "a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"[{i}/{len(todo)}] {'pass' if verdict['pass'] else 'FAIL'} {v['id']} run {r} "
                  f"({v['lang']}{' voice' if v['mode'] == 'voice' else ''}, {verdict['seconds']} s)"
                  + ("" if verdict["pass"] else f": {verdict['failed'][0]}"), flush=True)
    finally:
        if proc:
            proc.terminate()
    text = (f"# Benchmark run: {label}\n\n{time.strftime('%Y-%m-%d %H:%M')} · families from "
            f"`{os.path.relpath(opts.tasks, ROOT)}` · shell {'off' if opts.no_shell else 'on'}, "
            f"write tools {'on' if opts.write else 'off'} · {round((time.time() - t0) / 60)} minutes this time\n\n"
            + report.report(report.load(out)))
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(text)
    print("\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
