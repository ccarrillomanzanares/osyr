"""osyr audio — sound: default output and microphone, volume, mute, test, and
the assistant's voice (which speaker reads aloud, how fast it listens).

Everything goes through PipeWire, which serves native, ALSA and PulseAudio
applications. The state is read from pw-dump (JSON); the volume is changed
with wpctl (WirePlumber). Maximum volume 100 %: no amplification, no clipping.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from . import voice as vc
from .image import USER_AGENT, file_sha256
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
    vc.refresh_bar()


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


# A sentence to hear each voice with
SAMPLES = {"es": "Hola, esta es mi voz.", "en": "Hello, this is my voice.",
           "ca": "Hola, aquesta és la meva veu.", "de": "Hallo, das ist meine Stimme.",
           "fr": "Bonjour, voici ma voix.", "it": "Ciao, questa è la mia voce.",
           "pt": "Olá, esta é a minha voz."}


def _lang(args):
    return vc.lang_code(getattr(args, "lang", None))


def voice_status(args):
    lang = _lang(args)
    n = vc.speakers(lang)
    name = ""
    try:  # e.g. "es_ES-sharvard-medium:1"
        name = open(os.path.join(vc.PIPER_DIR, lang, "voice")).read().split(":")[0].split("-")[1]
    except (OSError, IndexError):
        pass
    listen = "fast" if vc.prefs().get("listen") == "fast" else "accurate"
    fast_file = os.path.join(vc.USER_MODELS, vc.FAST_MODEL["file"])
    data = {"language": lang, "voice": name or ("espeak" if not n else ""), "speakers": n,
            "speaker": vc.speaker(lang) + 1 if n else None, "listen": listen,
            "fast_downloaded": os.path.exists(fast_file), "listen_model": os.path.basename(vc.model() or "")}
    if n > 1:
        speak = (f'Voice: {name or "Piper"} ({lang}), speaker {data["speaker"]} of {n}. '
                 f"Change it: osyr audio voice speaker <1-{n}>")
    elif n == 1:
        speak = f'Voice: {name or "Piper"} ({lang}), the only speaker in this language.'
    else:
        speak = f"Voice: eSpeak ({lang}): there is no natural voice for this language."
    hear = ("Listening: fast (whisper base: about twice as fast, makes more mistakes)." if listen == "fast" else
            "Listening: accurate (whisper small). On a slow computer: osyr audio voice listen fast")
    return Result(True, "OK", data, text=speak + "\n" + hear)


def voice_speaker(args):
    lang = _lang(args)
    n = vc.speakers(lang)
    if n == 0:
        return fail("VOICE_NO_PIPER", None, {"language": lang},
                    text=f"There is no natural voice for '{lang}' (it uses eSpeak, with one voice).")
    if not 1 <= args.number <= n:
        return fail("VOICE_BAD_SPEAKER", f"Choose 1 to {n}.", {"language": lang, "speakers": n},
                    text=f"The '{lang}' voice has {n} speaker(s): choose 1 to {n}.")
    p = vc.prefs()
    if not isinstance(p.get("speaker"), dict):  # a hand-edited or old file
        p["speaker"] = {}
    p["speaker"][lang] = args.number - 1
    vc.save_prefs(p)
    if not args.quiet:
        vc.speak(SAMPLES.get(lang, SAMPLES["en"]), lang)
    return Result(True, "OK", {"language": lang, "speaker": args.number, "speakers": n},
                  text=f"Speaker {args.number} of {n} for '{lang}'. Spoken answers use it from now on.")


NET_HINT = "Check the Internet connection: osyr net status"


def _download_fast():
    """Downloads whisper base to the person's folder (resumable, like the system update)
    and checks its sha256. None, or (what went wrong, hint)."""
    m = vc.FAST_MODEL
    os.makedirs(vc.USER_MODELS, exist_ok=True)
    dst = os.path.join(vc.USER_MODELS, m["file"])
    part = dst + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    need = m["size"] - have + 64 * 1024 * 1024  # a margin so the home folder is not left full
    free = shutil.disk_usage(vc.USER_MODELS).free
    if free < need:
        return (f"not enough free space ({need >> 20} MB needed, {free >> 20} MB free)",
                "Free some space in your folder: osyr disk list")
    tty = sys.stderr.isatty()
    print("Downloading the fast listening model (60 MB)" + (f", resuming at {have >> 20} MB" if have else "")
          + "...", file=sys.stderr, flush=True)
    try:
        p = subprocess.run(["curl", "-fL", "-C", "-", "--retry", "5", "--retry-delay", "5",
                            "-A", USER_AGENT, "-o", part, m["url"]] + (["-#"] if tty else ["-sS"]),
                           stdin=subprocess.DEVNULL, timeout=3600)
    except OSError as e:
        return str(e), "Run: osyr-diag"
    except subprocess.TimeoutExpired:
        return "the download took too long; run it again to resume it", NET_HINT
    if p.returncode != 0:
        return "the download stopped; run it again to resume it", NET_HINT
    try:
        if os.path.getsize(part) != m["size"]:
            os.unlink(part)
            return "the downloaded file has the wrong size; try again", NET_HINT
        if file_sha256(part) != m["sha256"]:
            os.unlink(part)
            return "the download is corrupt (checksum mismatch); try again", NET_HINT
        os.replace(part, dst)
    except OSError as e:
        return str(e), "Run: osyr-diag"
    return None


def voice_listen(args):
    if args.mode == "fast" and not os.path.exists(os.path.join(vc.USER_MODELS, vc.FAST_MODEL["file"])):
        err = _download_fast()
        if err:
            return fail("VOICE_DOWNLOAD_FAILED", err[1], {"error": err[0]},
                        text=f"Could not download the fast model: {err[0]}")
    p = vc.prefs()  # read after the download: it may have taken minutes
    p["listen"] = args.mode
    vc.save_prefs(p)
    text = ("Listening fast from now on (whisper base): about twice as fast, but it makes more "
            "mistakes. Back: osyr audio voice listen accurate" if args.mode == "fast" else
            "Listening accurately from now on (whisper small).")
    return Result(True, "OK", {"listen": args.mode, "model": os.path.basename(vc.model() or "")}, text=text)


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
    v = s.add_parser("voice", help="the assistant's voice: speaker, listening speed")
    vs = v.add_subparsers(dest="voice_action")
    v.set_defaults(func=voice_status)
    st = vs.add_parser("status", help="voice in use and listening mode")
    st.add_argument("--lang", help="language (default: the system's)")
    st.set_defaults(func=voice_status)
    sp = vs.add_parser("speaker", help="which speaker reads aloud (it says a sample)")
    sp.add_argument("number", type=int, help="1, 2... (status says how many there are)")
    sp.add_argument("--lang", help="language (default: the system's)")
    sp.add_argument("--quiet", action="store_true", help="without the spoken sample")
    sp.set_defaults(func=voice_speaker)
    ls = vs.add_parser("listen", help="fast (whisper base, downloads 60 MB) or accurate (small)")
    ls.add_argument("mode", choices=["fast", "accurate"])
    ls.set_defaults(func=voice_listen)
