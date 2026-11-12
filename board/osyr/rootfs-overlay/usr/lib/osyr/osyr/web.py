"""osyr web — the browser as an extension of the agent.

The browser is cog (WPE WebKit), driven through WebDriver (W3C): WPEWebDriver
listens on 127.0.0.1 and launches cog in automation mode. The agent opens
pages, reads their text, follows links and fills in fields; the person sees
the same window and can use it with the mouse and keyboard.

Risk (see osyr-chat): reading and browsing do not ask; pressing buttons and
filling in fields do. The agent never types into password or card fields:
the person does.

The session is stored in $XDG_RUNTIME_DIR/osyr/web.json; if the window has
been closed, the next command opens another.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from .result import Result, fail

PORT = int(os.environ.get("OSYR_WEBDRIVER_PORT", "4445"))
BASE = f"http://127.0.0.1:{PORT}"
DRIVER = os.environ.get("OSYR_WEBDRIVER", "WPEWebDriver")
BROWSER = os.environ.get("OSYR_BROWSER", "cog")
SEARCH = "https://duckduckgo.com/?q="
READ_MAX = 2200      # text characters per read: fits with the list in ~1,000 tokens
ITEMS_MAX = 30       # numbered links and fields per read


def state_path():
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "osyr", "web.json")


class WebError(Exception):
    def __init__(self, code, hint=None, data=None):
        super().__init__(code)
        self.code, self.hint, self.data = code, hint, data or {}


# --- WebDriver ---------------------------------------------------------------------

def wd(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data, {"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r).get("value")
    except urllib.error.HTTPError as e:
        try:
            v = json.load(e).get("value", {})
        except ValueError:
            v = {}
        raise WebError("WEB_DRIVER_ERROR", None, {"error": v.get("error"), "message": (v.get("message") or "")[:200]})
    except (OSError, ValueError) as e:
        raise WebError("WEB_NOT_RUNNING", "Run it from the graphical session.", {"error": str(e)})


def driver_up():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=1):
            return True
    except OSError:
        return False


def start_driver():
    if driver_up():
        return
    if not shutil.which(DRIVER):
        raise WebError("WEB_NOT_INSTALLED", "This OSYR image has no browser.")
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise WebError("WEB_NO_SESSION", "Run it from the graphical session.")
    subprocess.Popen([DRIVER, f"--port={PORT}", "--host=127.0.0.1"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        if driver_up():
            return
        time.sleep(0.2)
    raise WebError("WEB_NOT_RUNNING", "The browser driver did not start.")


def load_state():
    try:
        return json.load(open(state_path()))
    except (OSError, ValueError):
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(state_path()), exist_ok=True)
    with open(state_path(), "w") as f:
        json.dump(st, f)


def session(create=True):
    """Id of the live session; if the window was closed, opens another (if create)."""
    sid = load_state().get("session")
    if sid and driver_up():
        try:
            wd("GET", f"/session/{sid}/url", timeout=10)
            return sid
        except WebError:
            pass
    if not create:
        raise WebError("WEB_NOT_OPEN", "Run: osyr web open <address or search>")
    start_driver()
    caps = {"capabilities": {"alwaysMatch": {
        "wpe:browserOptions": {"binary": BROWSER, "args": ["--automation"]}}}}
    v = wd("POST", "/session", caps, timeout=60)
    sid = v["sessionId"]
    save_state({"session": sid, "items": []})
    return sid


def script(sid, js, args=None):
    return wd("POST", f"/session/{sid}/execute/sync", {"script": js, "args": args or []})


# --- reading the page: text and numbered elements ------------------------------------

# Numbers visible links, buttons and fields (data-osyr-n) and returns the text and the list.
READ_JS = r"""
const max = arguments[0], itemsMax = arguments[1];
const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 &&
  getComputedStyle(e).visibility !== 'hidden'; };
document.querySelectorAll('[data-osyr-n]').forEach(e => e.removeAttribute('data-osyr-n'));
const items = []; let n = 0;
for (const e of document.querySelectorAll('a[href], button, input, textarea, select, [role=button]')) {
  if (items.length >= itemsMax) break;
  if (!vis(e) || e.type === 'hidden') continue;
  n += 1; e.setAttribute('data-osyr-n', n);
  const tag = e.tagName.toLowerCase();
  const kind = tag === 'a' ? 'link' : (tag === 'button' || e.getAttribute('role') === 'button' ||
               ['submit', 'button'].includes(e.type)) ? 'button' : 'field';
  const label = (e.innerText || e.value || e.getAttribute('aria-label') || e.placeholder ||
                 e.name || e.title || '').trim().replace(/\s+/g, ' ').slice(0, 60);
  const it = {n, kind, label};
  if (kind === 'link') it.href = e.href.slice(0, 120);
  if (kind === 'field') it.type = e.type || tag;
  items.push(it);
}
const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').trim();
return {title: document.title, url: location.href, text: text.slice(0, max),
        truncated: text.length > max, items};
"""

SENSITIVE_JS = r"""
const e = document.querySelector('[data-osyr-n="' + arguments[0] + '"]');
if (!e) return 'missing';
const ac = (e.getAttribute('autocomplete') || '').toLowerCase();
if (e.type === 'password' || ac.startsWith('cc-') || ac.includes('password') ||
    /card|cvv|cvc|iban|tarjeta/i.test(e.name + ' ' + e.id + ' ' + (e.placeholder || ''))) return 'sensitive';
return 'ok';
"""


WEB_SCHEMES = ("http://", "https://")


def web_only(url):
    """Only http(s): file://, data: and the rest would give the model local files
    (API keys included) without going through osyr files' questions."""
    if not url.lower().startswith(WEB_SCHEMES):
        raise WebError("WEB_BAD_URL", "Only http:// and https:// addresses.", {"url": url[:200]})
    return url


def to_url(text):
    t = text.strip()
    if " " not in t and re.match(r"^[\w-]+(\.[\w-]+)*(:\d+)?(/.*)?$", t) and ("." in t.split("/")[0] or ":" in t):
        return "https://" + t  # example.org, localhost:8080
    if re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I):
        return web_only(t)
    return SEARCH + urllib.parse.quote_plus(t)


def page(sid, max_chars=READ_MAX):
    time.sleep(0.3)  # let it finish drawing what the page's script adds
    v = script(sid, READ_JS, [max_chars, ITEMS_MAX])
    if v.get("url") != "about:blank":
        web_only(v.get("url") or "")  # a page may have jumped to file:// by itself
    st = load_state()
    st["items"] = v["items"]
    save_state(st)
    return v


def element(sid, n):
    found = wd("POST", f"/session/{sid}/element",
               {"using": "css selector", "value": f'[data-osyr-n="{n}"]'})
    return next(iter(found.values()))  # the key is the element's W3C identifier


def item_line(it):
    """'4 field/password: pw': one short line per element (what the model sees)."""
    kind = f'{it["kind"]}/{it.get("type")}' if it["kind"] == "field" else it["kind"]
    return f'{it["n"]} {kind}: {it["label"]}'


def result(v, code="OK"):
    data = dict(v, items=[item_line(it) for it in v["items"]])
    lines = [f'{v["title"]} — {v["url"]}', "", v["text"]]
    if v.get("truncated"):
        lines.append("[... more text: osyr web read --more]")
    if v["items"]:
        lines += [""] + [f"[{l}]" for l in data["items"]]
    return Result(True, code, data, text="\n".join(lines))


# --- commands ------------------------------------------------------------------------

def guarded(fn):
    def run(args):
        try:
            return fn(args)
        except WebError as e:
            return fail(e.code, e.hint, e.data)
    return run


@guarded
def open_(args):
    sid = session()
    url = to_url(" ".join(args.target))
    wd("POST", f"/session/{sid}/url", {"url": url}, timeout=90)
    return result(page(sid))


@guarded
def read(args):
    sid = session(create=False)
    return result(page(sid, READ_MAX * 3 if args.more else READ_MAX))


@guarded
def go(args):
    """Follows a numbered link: browsing, no question asked."""
    sid = session(create=False)
    it = next((i for i in load_state().get("items", []) if i["n"] == args.n), None)
    if not it:
        return fail("WEB_NO_SUCH_ITEM", "Run: osyr web read", {"n": args.n})
    if it["kind"] != "link":
        return fail("WEB_NOT_A_LINK", f"[{args.n}] is a {it['kind']}: use osyr web click {args.n}", {"n": args.n})
    wd("POST", f"/session/{sid}/url", {"url": web_only(it["href"])}, timeout=90)
    return result(page(sid))


@guarded
def click(args):
    sid = session(create=False)
    wd("POST", f"/session/{sid}/element/{element(sid, args.n)}/click", {})
    time.sleep(1)
    return result(page(sid))


@guarded
def fill(args):
    sid = session(create=False)
    verdict = script(sid, SENSITIVE_JS, [args.n])
    if verdict == "missing":
        return fail("WEB_NO_SUCH_ITEM", "Run: osyr web read", {"n": args.n})
    if verdict == "sensitive":
        return fail("WEB_SENSITIVE_FIELD", "Passwords and card details: the user types them in the browser.",
                    {"n": args.n})
    el = element(sid, args.n)
    wd("POST", f"/session/{sid}/element/{el}/clear", {})
    wd("POST", f"/session/{sid}/element/{el}/value", {"text": " ".join(args.text)})
    return Result(True, "OK", {"n": args.n}, text=f"Filled [{args.n}].")


@guarded
def back(args):
    sid = session(create=False)
    wd("POST", f"/session/{sid}/back", {})
    return result(page(sid))


@guarded
def status(args):
    try:
        sid = session(create=False)
    except WebError:
        return Result(True, "OK", {"open": False}, text="The browser is not open.")
    url = wd("GET", f"/session/{sid}/url")
    title = wd("GET", f"/session/{sid}/title")
    return Result(True, "OK", {"open": True, "url": url, "title": title}, text=f"{title} — {url}")


@guarded
def close(args):
    st = load_state()
    if st.get("session") and driver_up():
        try:
            wd("DELETE", f"/session/{st['session']}", timeout=15)
        except WebError:
            pass
    save_state({})
    return Result(True, "OK", {"open": False}, text="Browser closed.")


def register(sub):
    p = sub.add_parser("web", help="web browser driven by the assistant")
    s = p.add_subparsers(dest="action", required=True)
    o = s.add_parser("open", help="open an address or search the web")
    o.add_argument("target", nargs="+")
    o.set_defaults(func=open_)
    r = s.add_parser("read", help="text of the page and its numbered links and fields")
    r.add_argument("--more", action="store_true", help="longer text")
    r.set_defaults(func=read)
    g = s.add_parser("go", help="follow a numbered link")
    g.add_argument("n", type=int)
    g.set_defaults(func=go)
    c = s.add_parser("click", help="press a numbered button")
    c.add_argument("n", type=int)
    c.set_defaults(func=click)
    f = s.add_parser("fill", help="type into a numbered field (never passwords or cards)")
    f.add_argument("n", type=int)
    f.add_argument("text", nargs="+")
    f.set_defaults(func=fill)
    s.add_parser("back", help="previous page").set_defaults(func=back)
    s.add_parser("status", help="is it open, and on which page").set_defaults(func=status)
    s.add_parser("close", help="close the browser").set_defaults(func=close)
