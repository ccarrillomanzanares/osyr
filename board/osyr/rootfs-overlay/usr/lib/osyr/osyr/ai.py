"""osyr ai — AI model: the local one (llama-server) and external providers.

Providers are servers with an OpenAI-compatible API. They are stored in
~/.config/osyr/ai.json (0600: it holds the keys). Each one's context size is
worked out when it is added:
  1. llama.cpp:   /props -> n_ctx (the server's real one)
  2. /v1/models:  context_length / max_model_len / meta.n_ctx, if given
  3. Ollama (also Ollama Cloud): /api/show -> model_info.<arch>.context_length
  4. table of known models
  5. the user is asked (or --context N)
The chat trims the conversation to that context and, if the server still
rejects it as too long, trims and retries.
"""
import getpass
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

from .result import Result, fail

CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                      "osyr", "ai.json")
LOCAL = {"url": "http://127.0.0.1:{}/v1".format(os.environ.get("OSYR_LLAMA_PORT", "8083")),
         "model": "local", "context": "auto", "local": True}
# Known contexts (tokens), by model name prefix
KNOWN_CONTEXT = [
    ("gpt-4.1", 1047576), ("gpt-4o", 128000), ("gpt-5", 400000), ("o3", 200000), ("o4", 200000),
    ("claude", 200000), ("gemini-2", 1048576), ("gemini-1.5", 1048576),
    ("deepseek", 128000), ("mistral-large", 128000), ("qwen3", 32768), ("llama-3", 128000),
]


def load():
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("active", "local")
    cfg.setdefault("providers", {})
    return cfg


def save(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(tmp, CONFIG)


def provider(name=None):
    """The active provider (or the one asked for), with 'name'; local if it does not exist."""
    cfg = load()
    name = name or cfg["active"]
    p = cfg["providers"].get(name)
    if name == "local" or p is None:
        return dict(LOCAL, name="local")
    return dict(p, name=name, local=False)


# Cloudflare (in front of ccmai.org and many providers) answers 403 to
# Python's default user agent ("Python-urllib"): identify as OSYR
USER_AGENT = "OSYR/1.0"


def headers(p):
    h = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if p.get("api_key"):
        hdr = p.get("key_header") or "Authorization"
        h[hdr] = ("Bearer " + p["api_key"]) if hdr.lower() == "authorization" else p["api_key"]
    return h


def server_root(url):
    """http://host:port/v1 -> http://host:port (where llama.cpp serves /props)."""
    u = url.rstrip("/")
    return u[:-3] if u.endswith("/v1") else u


def ssl_context(p):
    if not p.get("insecure"):
        return None
    ctx = ssl.create_default_context()  # self-signed certificate (e.g. a home server)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def get_json(url, p, timeout=10):
    req = urllib.request.Request(url, headers=headers(p))
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context(p)) as r:
        return json.load(r)


# llama.cpp only gives the training context in /v1/models (meta.n_ctx_train), not the
# server's real one (-c). Behind a proxy that only exposes /v1 (/props unreachable) it is
# all there is, and it is usually much larger: it is not trusted, the user is asked.
TRAINING = "server /models (training context)"


def detect_context(p):
    """(context, where it came from) or (None, reason). If it is TRAINING, it is only the model's maximum."""
    try:
        props = get_json(server_root(p["url"]) + "/props", p)
        n = (props.get("default_generation_settings") or {}).get("n_ctx") or props.get("n_ctx")
        if n:
            return int(n), "server /props"
    except (urllib.error.URLError, OSError, ValueError):
        pass
    try:
        models = get_json(p["url"].rstrip("/") + "/models", p).get("data", [])
        for m in models:
            if p.get("model") in (None, "", m.get("id")) or len(models) == 1:
                for k in ("context_length", "max_model_len", "context_window"):
                    if m.get(k):
                        return int(m[k]), "server /models"
                meta = m.get("meta") or {}
                if meta.get("n_ctx_train"):
                    return int(meta["n_ctx_train"]), TRAINING
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        pass
    if p.get("model"):
        try:
            req = urllib.request.Request(server_root(p["url"]) + "/api/show", headers=headers(p),
                                         data=json.dumps({"model": p["model"]}).encode())
            with urllib.request.urlopen(req, timeout=10, context=ssl_context(p)) as r:
                info = json.load(r).get("model_info") or {}
            n = next((v for k, v in info.items() if k.endswith(".context_length")), None)
            if n:
                return int(n), "server /api/show (Ollama)"
        except (urllib.error.URLError, OSError, ValueError, AttributeError):
            pass
    model = (p.get("model") or "").lower()
    for prefix, n in KNOWN_CONTEXT:
        if model.startswith(prefix):
            return n, "known model table"
    return None, "unknown"


def context_of(p):
    """The provider's context in tokens (for the local one, the server is asked)."""
    if p.get("context") not in (None, "auto"):
        return int(p["context"])
    n, _ = detect_context(p)
    return n or 16384


def reachable(p):
    try:
        get_json(p["url"].rstrip("/") + "/models", p, timeout=5)
        return True, None
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, OSError, ValueError) as e:
        return False, str(getattr(e, "reason", e))


def model_ids(p):
    """Models offered by the server (empty list if it does not say)."""
    try:
        data = get_json(p["url"].rstrip("/") + "/models", p).get("data", [])
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return []
    return sorted({m.get("id") for m in data if isinstance(m, dict) and m.get("id")})


# --- commands ------------------------------------------------------------------------

def public(p):
    return {k: v for k, v in p.items() if k != "api_key"} | {"api_key": bool(p.get("api_key"))}


def status(args):
    p = provider()
    ok, err = reachable(p)
    data = public(p) | {"reachable": ok, "context": context_of(p) if ok else p.get("context")}
    where = "on this computer" if p["local"] else f'remote: {p["url"]}'
    text = f'AI: {p["name"]} ({where}), model {p.get("model")}, context {data["context"]} tokens'
    if not ok and p["local"] and err == "HTTP 503":
        return Result(False, "AI_LOADING", data, "Wait a few seconds; the model is loading.",
                      text=text + "\nThe model is still loading.")
    if not ok:
        return Result(False, "AI_UNREACHABLE", data | {"error": err},
                      "Run: osyr ai use local" if not p["local"] else "Run: systemctl status osyr-llama",
                      text=text + f"\nNot reachable: {err}")
    return Result(True, "OK", data, text=text)


def list_providers(args):
    cfg = load()
    items = [public(provider("local"))] + [public(provider(n)) for n in sorted(cfg["providers"])]
    lines = [f'{"*" if i["name"] == cfg["active"] else " "} {i["name"]:<12} {i["url"]}  '
             f'model={i.get("model")} context={i.get("context")}' for i in items]
    return Result(True, "OK", {"active": cfg["active"], "providers": items}, text="\n".join(lines))


def add(args):
    if args.name == "local":
        return fail("AI_RESERVED_NAME", "Choose another name.", text='"local" is the built-in model.')
    p = {"url": args.url.rstrip("/"), "model": args.model or "", "key_header": args.key_header}
    if args.insecure:
        p["insecure"] = True
    key = args.api_key
    if getattr(args, "api_key_stdin", False):  # never on the command line
        key = sys.stdin.readline().strip() or None
    if key is None and not args.no_key:
        if args.json or not sys.stdin.isatty():
            return fail("AI_NEEDS_KEY", f"Run it in a terminal (it asks for the key), or use: osyr ai pair "
                        f"(or --no-key if {args.url} has none).",
                        text="The provider needs an API key.")
        key = getpass.getpass("API key (empty for none): ").strip()
    if key:
        p["api_key"] = key

    ok, err = reachable(p)
    if not ok:
        return fail("AI_UNREACHABLE", "Check the URL and the API key.", {"url": p["url"], "error": err},
                    text=f'Cannot reach {p["url"]}: {err}')
    if not p["model"]:
        try:  # only one model on the server: use it
            models = get_json(p["url"] + "/models", p).get("data", [])
            if len(models) == 1:
                p["model"] = models[0].get("id", "")
        except (urllib.error.URLError, OSError, ValueError):
            pass

    source = "given"
    if args.context:
        p["context"] = args.context
    else:
        n, source = detect_context(p)
        if n is None or source == TRAINING:
            why = ("The server only reports the model's maximum context "
                   f"({n} tokens); its real limit is often lower."
                   if n else "The server does not report its context size.")
            if args.json or not sys.stdin.isatty():
                return fail("AI_NEEDS_CONTEXT", why + " Add --context <tokens>.",
                            {"url": p["url"], "model": p["model"], "model_max": n},
                            text="Unknown context size for this provider.")
            print(why + " Ask whoever runs the server (llama-server: the -c value).")
            while True:
                ans = input("Context size of this model in tokens (e.g. 32768): ").strip()
                if ans.isdigit() and int(ans) >= 1024:
                    n, source = int(ans), "asked"
                    break
        p["context"] = n
    cfg = load()
    cfg["providers"][args.name] = p
    save(cfg)
    return Result(True, "OK", public(dict(p, name=args.name)) | {"context_source": source},
                  f"Run: osyr ai use {args.name}",
                  text=f'Added "{args.name}": {p["url"]}, model {p["model"] or "?"}, '
                       f'context {p["context"]} tokens ({source}).')


def models(args):
    p = provider(args.name)
    if args.name and args.name != "local" and args.name not in load()["providers"]:
        return fail("AI_UNKNOWN_PROVIDER", "Run: osyr ai list", {"name": args.name})
    ids = model_ids(p)
    data = {"name": p["name"], "model": p.get("model"), "models": ids}
    if not ids:
        return Result(False, "AI_NO_MODEL_LIST", data, "Set one with: osyr ai use <provider> --model <id>",
                      text=f'{p["name"]} does not list its models.')
    lines = [f'{"*" if i == p.get("model") else " "} {i}' for i in ids]
    return Result(True, "OK", data, f'Run: osyr ai use {p["name"]} --model <id>', text="\n".join(lines))


def use(args):
    cfg = load()
    if args.name != "local" and args.name not in cfg["providers"]:
        return fail("AI_UNKNOWN_PROVIDER", "Run: osyr ai list", {"name": args.name})
    if getattr(args, "model", None):
        if args.name == "local":
            return fail("AI_LOCAL_MODEL", "Edit MODEL in /etc/osyr/llama.conf.",
                        text="The local model is chosen in /etc/osyr/llama.conf.")
        q = cfg["providers"][args.name]
        if q.get("model") != args.model:
            ids = model_ids(dict(q, name=args.name))
            if ids and args.model not in ids:
                return fail("AI_UNKNOWN_MODEL", f"Run: osyr ai models {args.name}",
                            {"model": args.model, "models": ids[:50]},
                            text=f'{args.name} has no model "{args.model}".')
            q["model"] = args.model
            n, source = detect_context(q)  # another model, maybe another context
            if n and source != TRAINING:
                q["context"] = n
            save(cfg)
    p = provider(args.name)
    if not p["local"] and cfg["active"] != args.name:
        # Data leaves the computer: explicit confirmation (contract: token)
        token = "send-" + args.name
        if args.confirm != token:
            msg = (f'Your conversations will be sent to {p["url"]} '
                   f'({p.get("model") or "remote model"}).')
            if not args.json and sys.stdin.isatty() and args.confirm is None:
                print(msg)
                if input("Continue? [y/N] ").strip().lower() not in ("y", "yes", "s", "si", "sí"):
                    return Result(True, "AI_CANCELLED", text="Still using " + cfg["active"] + ".")
            else:
                return Result(False, "CONFIRM_REQUIRED", {"name": args.name, "url": p["url"]},
                              f"Run: osyr ai use {args.name} --confirm {token}", confirm=token,
                              text=msg)
    cfg["active"] = args.name
    save(cfg)
    return Result(True, "OK", public(p), "The OSYR chat switches to it by itself.",
                  text=f'Using "{args.name}".')


def remove(args):
    cfg = load()
    if args.name not in cfg["providers"]:
        return fail("AI_UNKNOWN_PROVIDER", "Run: osyr ai list", {"name": args.name})
    del cfg["providers"][args.name]
    if cfg["active"] == args.name:
        cfg["active"] = "local"
    save(cfg)
    return Result(True, "OK", {"removed": args.name, "active": cfg["active"]},
                  text=f'Removed "{args.name}" (and its API key). Using "{cfg["active"]}".')


def _pair(args):
    from . import pair  # pair imports this module
    return pair.pair(args)


def _look(args):
    from . import look  # look imports this module
    return look.look(args)


def register(sub):
    p = sub.add_parser("ai", help="AI model: local or external providers")
    s = p.add_subparsers(dest="action", required=True)
    s.add_parser("status", help="which model is in use and whether it answers").set_defaults(func=status)
    s.add_parser("list", help="configured providers").set_defaults(func=list_providers)
    a = s.add_parser("add", help="add an OpenAI-compatible provider")
    a.add_argument("name")
    a.add_argument("--url", required=True, help="base URL, e.g. https://api.example.com/v1")
    a.add_argument("--model")
    a.add_argument("--api-key", help="visible to other programs while it runs: better let it ask, or osyr ai pair")
    a.add_argument("--api-key-stdin", action="store_true", help="read the key from the first line of stdin")
    a.add_argument("--no-key", action="store_true", help="the server needs no key")
    a.add_argument("--key-header", default="Authorization",
                   help="header for the key (default: Authorization: Bearer)")
    a.add_argument("--context", type=int, help="context size in tokens, if the server does not say")
    a.add_argument("--insecure", action="store_true", help="accept a self-signed certificate")
    a.set_defaults(func=add)
    u = s.add_parser("use", help="switch to a provider (or 'local')")
    u.add_argument("name")
    u.add_argument("--model", help="model id on that provider (see: osyr ai models)")
    u.add_argument("--confirm", metavar="TOKEN")
    u.set_defaults(func=use)
    m = s.add_parser("models", help="models offered by a provider (default: the active one)")
    m.add_argument("name", nargs="?")
    m.set_defaults(func=models)
    pr = s.add_parser("pair", help="add a provider typing its API key on a phone or another computer")
    pr.add_argument("--server", help="pairing server (default: the OSYR one)")
    pr.set_defaults(func=_pair)
    lk = s.add_parser("look", help="ask the model about what is on the screen")
    lk.add_argument("question", nargs="?")
    lk.add_argument("--file", help="an image instead of a screen capture")
    lk.set_defaults(func=_look)
    r = s.add_parser("remove", help="delete a provider and its API key")
    r.add_argument("name")
    r.set_defaults(func=remove)
