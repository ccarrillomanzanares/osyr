#!/usr/bin/env python3
"""OSYR — osyr box and osyr app against a stand-in for podman (tests/fake_podman.py).

Usage: tests/box_cases.py     prints the cases that fail; exit code 1 if any.
Checks the logic around the container engine: when the box is created, what it
is given to see, the errors, the list of applications. The real engine is
tested inside the image (tests/vm-checks.sh).
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "board", "osyr", "rootfs-overlay", "usr", "lib", "osyr")
HOME = tempfile.mkdtemp(prefix="osyr-box-")
STATE = os.path.join(HOME, "fake")
os.makedirs(STATE)
os.makedirs(os.path.join(HOME, "run"))
os.makedirs(os.path.join(HOME, ".config", "osyr"))
os.makedirs(os.path.join(HOME, "apps"))
open(os.path.join(HOME, "apps", "foot.desktop"), "w").write("[Desktop Entry]\nName=Foot\nExec=foot\n")
open(os.path.join(HOME, "apps", "viewer.desktop"), "w").write("[Desktop Entry]\nName=Image Viewer\nExec=true %f\n")
open(os.path.join(HOME, "photo.png"), "w").close()
ENV = {**os.environ, "HOME": HOME, "XDG_RUNTIME_DIR": os.path.join(HOME, "run"), "LANG": "es_ES.UTF-8",
       "PYTHONPATH": LIB, "OSYR_PODMAN": os.path.join(ROOT, "tests", "fake_podman.py"),
       "FAKE_PODMAN_STATE": STATE, "OSYR_APPLICATIONS": os.path.join(HOME, "apps"), "WAYLAND_DISPLAY": "wayland-test"}
for k in ("XDG_DATA_HOME", "SWAYSOCK"):
    ENV.pop(k, None)


def osyr(*args):
    p = subprocess.run([sys.executable, "-B", "-m", "osyr.cli", *args, "--json"], capture_output=True, text=True,
                       env=ENV, cwd=HOME, stdin=subprocess.DEVNULL, timeout=120)
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"code": "NO_JSON", "data": {"out": p.stdout[-300:], "err": p.stderr[-300:]}}


def calls():
    try:
        return [json.loads(line) for line in open(os.path.join(STATE, "calls.log"))]
    except OSError:
        return []


def main():
    failed = []
    count = [0]

    def check(name, ok, detail=""):
        count[0] += 1
        if not ok:
            failed.append(name)
            print(f"FAIL {name}: {str(detail)[:400]}")

    # Nothing exists yet: reading commands do not create the box
    r = osyr("box", "status")
    check("status-before", r.get("ok") and r["data"]["created"] is False, r)
    r = osyr("box", "search", "video")
    check("search-does-not-create", r.get("code") == "BOX_NOT_CREATED" and not any(c[0] == "pull" for c in calls()), r)
    r = osyr("box", "install", "Bad;Name")
    check("bad-name", r.get("code") == "BOX_BAD_NAME" and not any(c[0] == "exec" for c in calls()), r)
    r = osyr("box", "install", "$(reboot)")
    check("no-shell-in-names", r.get("code") == "BOX_BAD_NAME", r)

    # No network: nothing is created
    open(os.path.join(STATE, "offline"), "w").close()
    r = osyr("box", "install", "hello")
    check("offline", r.get("code") == "BOX_DOWNLOAD_FAILED" and not os.path.exists(os.path.join(STATE, "box.json")), r)
    os.unlink(os.path.join(STATE, "offline"))

    # The first install creates the box
    r = osyr("box", "install", "hello")
    check("first-install", r.get("ok") and r["data"]["installed"] == ["hello"] and r["data"].get("downloaded") == "56.1 kB"
          and "box run" in (r.get("hint") or ""), r)
    made = next((c for c in calls() if c[0] == "create"), [])
    text = " ".join(made)
    check("box-sees-home", f"{HOME}:{HOME}" in made and "--userns=keep-id" in made, text)
    check("box-hides-secrets", all(f"destination={HOME}/{d}," in text for d in (".ssh", ".gnupg", ".config/osyr"))
          and f"destination={HOME}/run/osyr," in text, text)
    check("ssh-folder-private", oct(os.stat(os.path.join(HOME, ".ssh")).st_mode & 0o777) == "0o700")
    check("language-set-up", any(c[0] == "exec" and "locale-gen" in c[-1] and "es_ES.UTF-8" in c[-1] for c in calls()))

    # An application with a window shows up for osyr app
    r = osyr("box", "install", "vlc")
    apps = r.get("data", {}).get("applications")
    check("install-finds-app", r.get("ok") and apps == [{"id": "vlc", "name": "Reproductor multimedia VLC"}]
          and "osyr app run vlc" in (r.get("hint") or ""), r)
    r = osyr("box", "install", "vlc")
    check("install-again", r.get("ok") and r["data"].get("already_installed") is True, r)
    r = osyr("box", "install", "nosuchprogram")
    check("no-such-program", r.get("code") == "BOX_NO_SUCH_PROGRAM" and "box search" in r.get("hint", ""), r)
    r = osyr("box", "list")
    check("list", r.get("ok") and r["data"]["programs"] == ["hello", "vlc"], r)
    r = osyr("box", "search", "video", "player")
    names = [x["name"] for x in r.get("data", {}).get("results", [])]
    check("search-order", names == ["vlc", "mpv", "vlc-data", "libvlc-dev"], names)  # programs before their parts
    r = osyr("box", "run", "hello")
    check("run", r.get("ok") and r["data"]["stdout"].strip() == "Hello, world!", r)
    r = osyr("box", "run", "nothere", "--version")
    check("run-missing", r.get("code") == "BOX_NO_SUCH_COMMAND" and "box install nothere" in r.get("hint", ""), r)

    # Applications: the system's (never the terminal) and the box's
    r = osyr("app", "list")
    ids = {a["id"]: a for a in r.get("data", {}).get("applications", [])}
    check("app-list", set(ids) == {"viewer", "vlc"} and ids["vlc"]["where"] == "box" and not ids["vlc"]["running"], r)
    r = osyr("app", "run", "VLC", "photo.png", "--wait", "1")
    check("app-run-box", r.get("ok") and r["data"]["id"] == "vlc", r)
    last = next((c for c in reversed(calls()) if c[0] == "exec" and "/usr/bin/vlc" in c), [])
    check("app-run-arguments", last[-3:] == ["/usr/bin/vlc", "--started-from-file", os.path.join(HOME, "photo.png")]
          and "WAYLAND_DISPLAY=wayland-test" in last and "%U" not in last, last)
    r = osyr("app", "list")
    check("app-running", any(a["id"] == "vlc" and a["running"] for a in r.get("data", {}).get("applications", [])), r)
    r = osyr("app", "stop", "vlc")
    check("app-stop", r.get("ok") and r["data"].get("closed") is True, r)
    r = osyr("app", "stop", "vlc")
    check("app-stop-not-running", r.get("code") == "APP_NOT_RUNNING", r)
    r = osyr("app", "run", "gimp")
    check("app-not-found", r.get("code") == "APP_NOT_FOUND" and "box install gimp" in r.get("hint", ""), r)
    r = osyr("app", "run", "vlc", "missing.mp4")
    check("app-missing-file", r.get("code") == "APP_NO_SUCH_FILE", r)
    r = osyr("app", "run", "viewer", "photo.png", "--wait", "1")
    check("app-run-system", r.get("ok") and r["data"]["where"] == "system", r)
    r = osyr("app", "run", "foot")
    check("terminal-not-an-app", r.get("code") == "APP_NOT_FOUND", r)

    # Removing, and deleting the box (asks first)
    r = osyr("box", "remove", "ffmpeg")
    check("remove-not-installed", r.get("code") == "BOX_NOT_INSTALLED", r)
    r = osyr("box", "remove", "vlc")
    check("remove", r.get("ok") and osyr("box", "list")["data"] == {"programs": ["hello"], "applications": []}, r)
    r = osyr("box", "update")
    check("update", r.get("ok") and r["data"]["upgraded"] == 3, r)
    r = osyr("box", "reset")
    check("reset-asks", r.get("code") == "CONFIRM_REQUIRED" and r.get("confirm") and os.path.exists(os.path.join(STATE, "box.json")), r)
    r = osyr("box", "reset", "--confirm", r.get("confirm") or "x")
    check("reset", r.get("ok") and not os.path.exists(os.path.join(STATE, "box.json"))
          and osyr("box", "list")["data"]["programs"] == [], r)

    print(f"{count[0] - len(failed)}/{count[0]} cases pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
