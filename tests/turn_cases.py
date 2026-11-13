#!/usr/bin/env python3
"""OSYR — the tool loop of osyr-chat with a scripted model (no server).

Usage: tests/turn_cases.py     prints the cases that fail; exit code 1 if any.
The model is replaced by a list of replies; the shell tool is the real one, in a
temporary home folder. Checks how a turn ends: a normal answer, a model that
repeats the same call, and one that never stops asking for tools.
"""
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY = os.path.join(ROOT, "board", "osyr", "rootfs-overlay", "usr")
HOME = tempfile.mkdtemp(prefix="osyr-turn-")
os.environ.update(HOME=HOME, XDG_RUNTIME_DIR=HOME)
sys.path.insert(0, os.path.join(OVERLAY, "lib", "osyr"))
loader = importlib.machinery.SourceFileLoader("osyr_chat", os.path.join(OVERLAY, "bin", "osyr-chat"))
chat = importlib.util.module_from_spec(importlib.util.spec_from_loader("osyr_chat", loader))
loader.exec_module(chat)


def shell(command):
    return {"id": "", "name": "shell", "arguments": json.dumps({"command": command})}


def run(model):
    """Plays one turn: (messages, terminal text, replies used). The model is a list of
    replies (the last one repeats for ever) or a function of the reply number."""
    used = [0]

    def complete(messages, sess):
        used[0] += 1
        return model(used[0]) if callable(model) else model[min(used[0], len(model)) - 1]

    chat.complete = complete
    sess = types.SimpleNamespace(
        cwd=HOME, provider={"local": True, "name": "local"}, tools=True, speak_always=False,
        ctx=types.SimpleNamespace(trim=lambda messages, force=False: False), wait_warm=lambda: None)
    messages = [{"role": "system", "content": "test"}]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        chat.turn(messages, sess, "question")
    return messages, out.getvalue(), used[0]


def main():
    failed = []

    def check(name, ok, detail=""):
        if not ok:
            failed.append(name)
            print(f"FAIL {name}: {detail}")

    open(os.path.join(HOME, "a.txt"), "w").close()

    # A read, then the answer: two replies, no warning
    messages, out, used = run([("", [shell("ls")]), ("It has a.txt.", [])])
    check("answer-after-tool", used == 2 and "stopped" not in out and messages[-1]["content"] == "It has a.txt.", out)

    # The same read again: the second time it is told so; the third ends the turn
    messages, out, used = run([("", [shell("ls")])])
    tools = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
    check("repeat-told", len(tools) >= 2 and tools[0].get("ok") and tools[1].get("code") == "REPEATED", str(tools)[:300])
    check("repeat-stops", used == 3 and "kept repeating" in out, f"{used} replies; {out[-200:]}")

    # Told once, the model answers: the turn ends normally
    messages, out, used = run([("", [shell("ls")]), ("", [shell("ls")]), ("It has a.txt.", [])])
    check("repeat-then-answer", used == 3 and "stopped" not in out and messages[-1]["content"] == "It has a.txt.", out)

    # The same call with a different result is not a repeat (the folder changes in between)
    def changing(n):
        open(os.path.join(HOME, f"new{n}.txt"), "w").close()
        return ("", [shell("ls")]) if n < 4 else ("Done.", [])

    messages, out, used = run(changing)
    codes = [json.loads(m["content"]).get("code") for m in messages if m["role"] == "tool"]
    check("changed-result-not-a-repeat", "REPEATED" not in codes and messages[-1]["content"] == "Done.", str(codes))

    # A model that never stops, with a different read each time: cut at MAX_STEPS, with a notice
    messages, out, used = run(lambda n: ("", [shell(f"echo {n}")]))
    check("steps-limit", used == chat.MAX_STEPS and f"stopped after {chat.MAX_STEPS} steps" in out,
          f"{used} replies; {out[-200:]}")

    total = 6
    print(f"{total - len(failed)}/{total} cases pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
