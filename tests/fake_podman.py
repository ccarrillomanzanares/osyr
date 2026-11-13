#!/usr/bin/env python3
"""A stand-in for podman, for tests/box_cases.py: remembers one container in
$FAKE_PODMAN_STATE (a folder), writes every call to calls.log there and answers
the few commands osyr box and osyr app use, with a pretend Debian inside."""
import json
import os
import sys

STATE = os.environ["FAKE_PODMAN_STATE"]
ARGS = sys.argv[1:]
KNOWN = {"hello": [], "vlc": ["vlc"], "ffmpeg": []}  # package: its .desktop files


def get(name, default=None):
    try:
        return json.load(open(os.path.join(STATE, name)))
    except (OSError, ValueError):
        return default


def put(name, value):
    json.dump(value, open(os.path.join(STATE, name), "w"))


with open(os.path.join(STATE, "calls.log"), "a") as f:
    f.write(json.dumps(ARGS) + "\n")
box = get("box.json")
if ARGS[:2] == ["container", "exists"]:
    sys.exit(0 if box else 1)
if ARGS[0] == "inspect":
    print("true" if box and box["running"] else "false")
    sys.exit(0 if box else 125)
if ARGS[0] == "pull":
    sys.exit(1 if os.path.exists(os.path.join(STATE, "offline")) else 0)
if ARGS[0] == "create":
    put("box.json", {"running": False, "packages": []})
    sys.exit(0)
if ARGS[0] in ("start", "stop"):
    box["running"] = ARGS[0] == "start"
    put("box.json", box)
    sys.exit(0)
if ARGS[0] == "rm":
    os.unlink(os.path.join(STATE, "box.json"))
    sys.exit(0)
if ARGS[0] == "top":
    print("HPID COMMAND")
    try:
        pid = int(open(os.path.join(STATE, "vlc-running")).read())
        os.kill(pid, 0)
        print(f"{pid} vlc")
    except (OSError, ValueError):
        pass
    sys.exit(0)
if ARGS[0] == "exec":
    if not (box and box["running"]):
        print("Error: container state improper", file=sys.stderr)
        sys.exit(125)
    if "sh" in ARGS and "-c" in ARGS:
        script = ARGS[ARGS.index("-c") + 1]
        if script.startswith("apt-get update"):
            sys.exit(0)
        if script.startswith("apt-get install"):
            names = [w for w in script.split()[2:] if not w.startswith("-") and w not in ("&&",)]
            if "locales" in names:
                sys.exit(0)
            missing = [n for n in names if n not in KNOWN]
            if missing:
                print(f"E: Unable to locate package {missing[0]}", file=sys.stderr)
                sys.exit(100)
            new = [n for n in names if n not in box["packages"]]
            if not new:
                print(f"{names[0]} is already the newest version (1.0).")
            else:
                print("Need to get 56.1 kB of archives.\nAfter this operation, 284 kB of additional disk space will be used.")
            box["packages"] += new
            put("box.json", box)
            sys.exit(0)
        if script.startswith("apt-get remove"):
            box["packages"] = [n for n in box["packages"] if n not in script.split()]
            put("box.json", box)
            sys.exit(0)
        if script.startswith("apt-get upgrade"):
            print("3 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.")
            sys.exit(0)
        if script.startswith("apt-cache search"):
            print("libvlc-dev - development files for libvlc\nvlc - multimedia player and streamer\n"
                  "vlc-data - common data for VLC\nmpv - video player based on MPlayer/mplayer2")
            sys.exit(0)
        if script.startswith("for f in /usr/share/applications"):
            for pkg in box["packages"]:
                for app in KNOWN.get(pkg, []):
                    print(f"@@ /usr/share/applications/{app}.desktop\n[Desktop Entry]\nType=Application\n"
                          f"Name=VLC media player\nName[es]=Reproductor multimedia VLC\nComment=Play your media\n"
                          f"Exec=/usr/bin/{app} --started-from-file %U\nTerminal=false\n\n[Desktop Action x]\nExec=nope")
            print("@@ /usr/share/applications/hidden.desktop\n[Desktop Entry]\nName=Hidden\nExec=x\nNoDisplay=true")
            sys.exit(0)
        sys.exit(0)
    # a program run for the user: everything after the container's name
    cmd = ARGS[ARGS.index(os.environ.get("OSYR_BOX_NAME", "osyr-box")) + 1:]
    if cmd[0] == "hello":
        print("Hello, world!")
        sys.exit(0)
    if cmd[0] == "/usr/bin/vlc":
        open(os.path.join(STATE, "vlc-running"), "w").write(str(os.getpid()))
        import time
        time.sleep(30)
        sys.exit(0)
    print(f"sh: {cmd[0]}: not found", file=sys.stderr)
    sys.exit(127)
sys.exit(0)
