"""osyr shell — runs a command for the agent, and keeps the saved permissions.

The design is in docs/SHELL-DESIGN.md. What may run is decided before, by
shellguard and by the person's answer in osyr-chat; this module only runs it:
as the user, with bash, without a keyboard, with a time limit, with the same
English output whatever the system language, and with the output trimmed for
the model and saved whole to a file.
"""
import json
import os
import signal
import subprocess
import time

STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "osyr")
OUTPUTS = os.path.join(STATE, "shell")          # whole outputs, the newest KEEP of them
AUDIT = os.path.join(STATE, "shell.log")        # every request: command, class, answer, exit code
PERMISSIONS = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                           "osyr", "permissions.json")
KEEP = 20
AUDIT_MAX = 1024 * 1024
TIMEOUT, TIMEOUT_MAX = 60, 600
OUT_MAX, ERR_MAX = 2800, 700   # characters of stdout and stderr the model sees
ENV = {"LC_ALL": "C", "LANG": "C", "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200", "LINES": "50",
       "PAGER": "cat", "GIT_PAGER": "cat", "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0", "GIT_TERMINAL_PROMPT": "0",
       "DEBIAN_FRONTEND": "noninteractive"}


def trim(text, limit):
    """The beginning and the end, with a line saying how much is missing."""
    if len(text) <= limit:
        return text, False
    head, tail = limit * 2 // 3, limit // 3
    cut = len(text) - head - tail
    return f"{text[:head]}\n[... {cut} characters left out ...]\n{text[-tail:]}", True


def _child():
    # Its own process group (to stop it whole on a timeout) inside the chat's
    # terminal session (so sudo finds the password the user typed a moment ago).
    # A program that tries to read the keyboard gets an error instead of stopping.
    os.setpgid(0, 0)
    signal.signal(signal.SIGTTIN, signal.SIG_IGN)
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)


def run(command, cwd, timeout=None):
    """Runs it and returns (result for the model, new current folder)."""
    try:
        limit = max(1, min(int(timeout or TIMEOUT), TIMEOUT_MAX))
    except (TypeError, ValueError):
        limit = TIMEOUT
    os.makedirs(OUTPUTS, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 10**9:09d}"
    out_path, err_path, cwd_path = (os.path.join(OUTPUTS, f"{stamp}.{x}") for x in ("out", "err", "cwd"))
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")
    # The folder a 'cd' leaves is written to a file, for the next command
    script = f"{command}\n__osyr_rc=$?\npwd -P > \"$OSYR_CWD_FILE\" 2>/dev/null\nexit $__osyr_rc\n"
    t0 = time.monotonic()
    timed_out = False
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        try:
            p = subprocess.Popen(["bash", "-c", script, "osyr-shell"], cwd=cwd,
                                 env={**os.environ, **ENV, "OSYR_CWD_FILE": cwd_path},
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=err, preexec_fn=_child)
        except OSError as e:
            return {"ok": False, "code": "SHELL_FAILED", "data": {"error": str(e)}}, cwd
        try:
            rc = p.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            timed_out = True
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(p.pid, sig)
                except OSError:
                    break
                try:
                    p.wait(timeout=3)
                    break
                except subprocess.TimeoutExpired:
                    continue
            rc = p.returncode if p.returncode is not None else -9
        except KeyboardInterrupt:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()
            raise
    seconds = round(time.monotonic() - t0, 1)

    def text_of(path):
        try:
            with open(path, "rb") as f:
                return f.read(4_000_000).decode("utf-8", errors="replace")
        except OSError:
            return ""
    stdout, stderr = text_of(out_path), text_of(err_path)
    new_cwd = text_of(cwd_path).strip()
    if not (new_cwd and os.path.isdir(new_cwd)):
        new_cwd = cwd
    shown_out, cut_out = trim(stdout, OUT_MAX)
    shown_err, cut_err = trim(stderr, ERR_MAX)
    data = {"exit": rc, "stdout": shown_out, "stderr": shown_err, "seconds": seconds}
    if new_cwd != cwd:
        data["cwd"] = new_cwd
    hint = None
    if cut_out or cut_err:
        data["truncated"] = True
        data["full_output"] = out_path if cut_out else err_path
        hint = f"The output was trimmed. To see more: osyr files read '{data['full_output']}' --from <n>"
    for path in (cwd_path, *((err_path,) if not stderr else ()), *((out_path,) if not stdout else ())):
        try:
            os.unlink(path)
        except OSError:
            pass
    _prune()
    if timed_out:
        return {"ok": False, "code": "TIMEOUT", "data": data,
                "hint": f"Stopped after {limit} s. If it really needs longer, call again with a larger "
                        f"timeout (up to {TIMEOUT_MAX}); programs that wait for the keyboard cannot run here."}, new_cwd
    return {"ok": rc == 0, "code": "OK" if rc == 0 else "EXIT_NONZERO", "data": data, "hint": hint}, new_cwd


def _prune():
    try:
        names = sorted(n for n in os.listdir(OUTPUTS))
    except OSError:
        return
    stamps = sorted({n.rsplit(".", 1)[0] for n in names})
    for old in stamps[:-KEEP]:
        for n in names:
            if n.rsplit(".", 1)[0] == old:
                try:
                    os.unlink(os.path.join(OUTPUTS, n))
                except OSError:
                    pass


def audit(**kw):
    """One line per request. It stays on this computer: osyr-diag does not collect it."""
    try:
        os.makedirs(STATE, exist_ok=True)
        if os.path.exists(AUDIT) and os.path.getsize(AUDIT) > AUDIT_MAX:
            os.replace(AUDIT, AUDIT + ".old")
        with open(os.open(AUDIT, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), **kw}, ensure_ascii=False) + "\n")
    except OSError:
        pass


# --- Saved permissions ("yes, always") ----------------------------------------------------

def load():
    try:
        rules = json.load(open(PERMISSIONS))
        return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []
    except (OSError, ValueError):
        return []


def save(rules):
    os.makedirs(os.path.dirname(PERMISSIONS), exist_ok=True)
    tmp = PERMISSIONS + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(rules, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PERMISSIONS)


def add(rule):
    rules = load()
    if rule not in rules:
        rules.append(rule)
        save(rules)


def describe(rule):
    """One line for /permissions and for the question."""
    home = os.path.expanduser("~")
    short = lambda d: "~" + d[len(home):] if d == home or d.startswith(home + "/") else d  # noqa: E731
    if rule.get("osyr"):
        return f"'osyr {rule['osyr']}'"
    if rule.get("send"):
        return f"sending files in {short(rule['send'])} to {rule.get('provider')}"
    if rule.get("program"):
        return f"'{(rule['program'] + ' ' + rule.get('sub', '')).strip()}' in {short(rule.get('dir', '?'))}"
    return json.dumps(rule)
