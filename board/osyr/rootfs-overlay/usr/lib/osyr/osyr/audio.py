"""osyr audio — sound: default output and microphone, volume, mute, test.

Everything goes through PipeWire, which serves native, ALSA and PulseAudio
applications. The state is read from pw-dump (JSON); the volume is changed
with wpctl (WirePlumber). Maximum volume 100 %: no amplification, no clipping.
"""
import json
import os
import re
import subprocess
import tempfile

from .result import Result, fail

SINK, SOURCE = "@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"
STEP = 5


def run(args, timeout=10):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def get_volume(target):
    """(percentage, muted) or None. wpctl: 'Volume: 0.40 [MUTED]'."""
    rc, out, _ = run(["wpctl", "get-volume", target])
    m = re.search(r"Volume:\s*([\d.]+)(.*)", out) if rc == 0 else None
    if not m:
        return None
    return round(float(m.group(1)) * 100), "MUTED" in m.group(2)


def devices():
    """Audio outputs and inputs, and which ones are the defaults."""
    rc, out, _ = run(["pw-dump"], timeout=15)
    try:
        objs = json.loads(out) if rc == 0 else []
    except ValueError:
        objs = []
    defaults = {}
    for o in objs:
        if o.get("type") == "PipeWire:Interface:Metadata" and \
                (o.get("props") or {}).get("metadata.name") == "default":
            for m in o.get("metadata") or []:
                if m.get("key") in ("default.audio.sink", "default.audio.source"):
                    v = m.get("value")
                    defaults[m["key"]] = v.get("name") if isinstance(v, dict) else v
    sinks, sources = [], []
    for o in objs:
        props = (o.get("info") or {}).get("props") or {}
        cls = props.get("media.class")
        if cls not in ("Audio/Sink", "Audio/Source"):
            continue
        d = {"id": o.get("id"), "name": props.get("node.name"),
             "description": props.get("node.description") or props.get("node.nick")}
        if cls == "Audio/Sink":
            d["default"] = d["name"] == defaults.get("default.audio.sink")
            sinks.append(d)
        else:
            d["default"] = d["name"] == defaults.get("default.audio.source")
            sources.append(d)
    return sinks, sources


def refresh_bar():
    from . import voice
    voice.refresh_bar()


def no_audio():
    rc, _, err = run(["wpctl", "status"])
    if rc != 0:
        return fail("AUDIO_NO_SERVICE", "Run: systemctl --user status pipewire wireplumber",
                    {"error": err.strip()}, text="The sound service (PipeWire) is not running.")
    return fail("AUDIO_NO_DEVICE", "Check the sound card with: osyr-diag",
                text="No sound device found.")


def status(args):
    sinks, sources = devices()
    out_v, in_v = get_volume(SINK), get_volume(SOURCE)
    if not sinks or out_v is None:
        return no_audio()
    data = {"output": {"volume": out_v[0], "muted": out_v[1],
                       "device": next((s["description"] for s in sinks if s["default"]), None)},
            "input": None if in_v is None else
            {"volume": in_v[0], "muted": in_v[1],
             "device": next((s["description"] for s in sources if s["default"]), None)},
            "outputs": sinks, "inputs": sources}
    lines = [f'Output: {data["output"]["device"]}  {"MUTED" if out_v[1] else str(out_v[0]) + "%"}']
    if in_v:
        lines.append(f'Mic:    {data["input"]["device"]}  {"MUTED" if in_v[1] else str(in_v[0]) + "%"}')
    else:
        lines.append("Mic:    none")
    return Result(True, "OK", data, text="\n".join(lines))


def volume(args):
    target = SOURCE if args.mic else SINK
    cur = get_volume(target)
    if cur is None:
        return no_audio()
    lvl = (args.level or "").strip().rstrip("%")
    if not lvl:
        return Result(True, "OK", {"volume": cur[0], "muted": cur[1]},
                      text=f'{"Mic" if args.mic else "Volume"}: {cur[0]}%' + (" (muted)" if cur[1] else ""))
    try:
        pct = {"up": cur[0] + STEP, "down": cur[0] - STEP}.get(lvl)
        pct = int(lvl) if pct is None else pct
    except ValueError:
        return fail("AUDIO_BAD_LEVEL", "Use: osyr audio volume [up|down|0-100]", {"level": lvl})
    pct = max(0, min(100, pct))
    rc, _, err = run(["wpctl", "set-volume", target, f"{pct / 100:.2f}"])
    if rc != 0:
        return fail("AUDIO_FAILED", "Run: osyr audio status", {"error": err.strip()})
    if pct > 0 and cur[1] and lvl == "up":
        run(["wpctl", "set-mute", target, "0"])  # raising the volume unmutes
    refresh_bar()
    return Result(True, "OK", {"volume": pct}, text=f'{"Mic" if args.mic else "Volume"}: {pct}%')


def mute(args):
    target = SOURCE if args.mic else SINK
    if get_volume(target) is None:
        return no_audio()
    state = {"on": "1", "off": "0", None: "toggle", "toggle": "toggle"}[args.state]
    rc, _, err = run(["wpctl", "set-mute", target, state])
    if rc != 0:
        return fail("AUDIO_FAILED", "Run: osyr audio status", {"error": err.strip()})
    now = get_volume(target)
    refresh_bar()
    what = "Mic" if args.mic else "Sound"
    return Result(True, "OK", {"muted": now[1] if now else None},
                  text=f'{what} {"muted" if now and now[1] else "on"}.')


def test(args):
    """Says a sentence through the default output (espeak -> pw-play)."""
    if get_volume(SINK) is None:
        return no_audio()
    text = args.text or "OSYR sound test. Prueba de sonido."
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = f.name
    try:
        rc, _, err = run(["espeak", "-v", args.voice, "-w", wav, text], timeout=30)
        if rc != 0:
            return fail("AUDIO_TTS_FAILED", None, {"error": err.strip()})
        rc, _, err = run(["pw-play", wav], timeout=60)
        if rc != 0:
            return fail("AUDIO_FAILED", "Run: osyr audio status", {"error": err.strip()})
    finally:
        os.unlink(wav)
    return Result(True, "OK", {"text": text}, text="Played a test sound.")


def register(sub):
    p = sub.add_parser("audio", help="sound: volume, mute, microphone, test")
    s = p.add_subparsers(dest="action", required=True)
    s.add_parser("status", help="output and microphone in use").set_defaults(func=status)
    v = s.add_parser("volume", help="show or set the volume")
    v.add_argument("level", nargs="?", help="up, down or a percentage (0-100)")
    v.add_argument("--mic", action="store_true", help="the microphone instead of the output")
    v.set_defaults(func=volume)
    m = s.add_parser("mute", help="mute or unmute")
    m.add_argument("state", nargs="?", choices=["on", "off", "toggle"])
    m.add_argument("--mic", action="store_true", help="the microphone instead of the output")
    m.set_defaults(func=mute)
    t = s.add_parser("test", help="play a short spoken test")
    t.add_argument("text", nargs="?")
    t.add_argument("--voice", default="es", help="espeak voice (default: es)")
    t.set_defaults(func=test)
