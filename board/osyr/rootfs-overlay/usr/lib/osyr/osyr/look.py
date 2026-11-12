"""look — looks at the screen: captures it with grim and asks the model (local,
with the Qwen3.5 vision projector, no network).

The capture is scaled down to MAX_WIDTH: fewer image tokens, which is what costs
on a CPU. Locally, if the agent's server does not have the vision projector
(VISION=ondemand in /etc/osyr/llama.conf), the vision server is asked instead
(port 8084): systemd starts it on the first request and it stops by itself. Used
from the chat (/look and the agent's tool) and from 'osyr ai look'.
"""
import base64
import json
import os
import subprocess
import urllib.error
import urllib.request

from . import ai as aiconf
from .result import Result, fail

MAX_WIDTH = 1024
VISION_URL = "http://127.0.0.1:{}/v1".format(os.environ.get("OSYR_VISION_PORT", "8084"))


def wayland_env():
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("WAYLAND_DISPLAY", "wayland-1")  # from a tty or the serial console
    return env


def screen_width(env):
    try:
        out = subprocess.run(["swaymsg", "-t", "get_outputs", "-r"], capture_output=True,
                             text=True, timeout=5, env=env).stdout
        widths = [o["current_mode"]["width"] for o in json.loads(out) if o.get("active")]
        return max(widths) if widths else None
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return None


def capture(path=None):
    """PNG of the screen (bytes), scaled down to MAX_WIDTH. path: use that file instead."""
    if path:
        with open(path, "rb") as f:
            return f.read()
    env = wayland_env()
    width = screen_width(env)
    scale = min(1.0, MAX_WIDTH / width) if width else 0.75
    p = subprocess.run(["grim", "-s", f"{scale:.3f}", "-"], capture_output=True,
                       timeout=20, env=env)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError(p.stderr.decode(errors="replace").strip() or "grim failed")
    return p.stdout


class EmptyAnswer(Exception):
    pass


def has_vision(p):
    """Does the agent's local server already have the projector loaded?"""
    try:
        base = p["url"].rstrip("/").removesuffix("/v1")
        with urllib.request.urlopen(base + "/props", timeout=5) as r:
            return bool(json.load(r).get("modalities", {}).get("vision"))
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return False


def describe(question, png, provider=None):
    """Asks the model about the image; returns the text of the answer."""
    p = provider or aiconf.provider()
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    body = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": question},
        {"type": "image_url", "image_url": {"url": url}}]}],
        "max_tokens": 512}
    if not p["local"]:
        body["max_tokens"] = 2048  # reasoning models spend tokens before answering
    if p["local"]:
        body["chat_template_kwargs"] = {"enable_thinking": False}
        if has_vision(p):
            body["id_slot"] = 1  # not the chat's (0): does not wipe its already processed prompt
        else:
            p = dict(p, url=VISION_URL)
    elif p.get("model"):
        body["model"] = p["model"]
    req = urllib.request.Request(p["url"].rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), headers=aiconf.headers(p))
    with urllib.request.urlopen(req, timeout=600, context=aiconf.ssl_context(p)) as r:
        msg = json.load(r)["choices"][0]["message"]
    answer = (msg.get("content") or "").strip()
    if not answer:
        if (msg.get("reasoning_content") or msg.get("reasoning") or "").strip():
            raise EmptyAnswer("the model spent its whole answer thinking and gave no reply; ask again "
                             "with a shorter question, or use another model")
        raise EmptyAnswer("the model gave an empty answer: it may not support images")
    return answer


def look(args):
    question = args.question or "Describe what is on the screen."
    try:
        png = capture(args.file)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        return fail("LOOK_NO_SCREEN", "Run it inside the graphical session.", {"error": str(e)},
                    text=f"Could not capture the screen: {e}")
    try:
        answer = describe(question, png)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        hint = "The model in use may not support images." if e.code in (400, 500) else "Run: osyr ai status"
        return fail("LOOK_MODEL_FAILED", hint, {"error": f"HTTP {e.code}: {body}"},
                    text=f"The model could not look at the screen: HTTP {e.code}")
    except EmptyAnswer as e:
        return fail("LOOK_MODEL_FAILED", "Try /look again, or another model with: osyr ai use",
                    {"error": str(e)}, text=f"The model could not look at the screen: {e}")
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        return fail("AI_UNREACHABLE", "Run: osyr ai status", {"error": str(e)})
    return Result(True, "OK", {"question": question, "answer": answer}, text=answer)
