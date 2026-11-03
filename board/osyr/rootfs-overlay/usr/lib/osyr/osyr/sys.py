"""osyr sys selftest — test this computer's hardware in one go.

Run by osyr-diag (and uploaded with the rest) or on its own. Each test gives
ok / warn / fail with one line of detail. The sound test plays a 1 kHz tone
through the speaker while recording with the microphone and looks for it in
the recording (Goertzel): if it is there, both work. A short beep is heard.
"""
import glob
import math
import os
import struct
import subprocess
import tempfile
import time
import urllib.request
import wave

from . import audio, power, thermal, voice
from .result import Result

SYSFS = os.environ.get("OSYR_SYSFS", "/sys")
TONE_HZ, RATE = 1000, 16000


def sh(args, timeout=30):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def first_line(path):
    try:
        return open(path).read().strip()
    except OSError:
        return None


# --- tests: each one returns (status, detail) -------------------------------------

def t_cpu():
    model = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), "?")
    mem = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemTotal"))
    return "ok", f"{model}, {os.cpu_count()} threads, RAM {mem / 1048576:.1f} GiB"


def t_gpu():
    rc, out, _ = sh(["osyr-gpu-probe"])
    log = first_line(os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
                                  "osyr", "gpu.log")) or ""
    renderer = log.split("renderer:")[-1].strip() if "renderer:" in log else "?"
    if rc == 0:
        return "ok", f"3D driver {out.split()[0]}; screen drawn with {renderer}"
    return "warn", f"no 3D driver ({out or 'none'}): screen drawn by the CPU ({renderer})"


def t_displays():
    from . import display
    outs = display.outputs()
    if outs is None:
        return "warn", "no graphical session"
    on = [f'{o["name"]} {o["mode"]}' for o in outs if o["enabled"]]
    return ("ok" if on else "fail"), f'{len(outs)} display(s): ' + (", ".join(on) or "none on")


def t_backlight():
    devs = glob.glob(os.path.join(SYSFS, "class", "backlight", "*"))
    if not devs:
        return "warn", "no adjustable backlight (normal on desktops and VMs)"
    return "ok", ", ".join(os.path.basename(d) for d in devs)


def t_battery():
    bats = glob.glob(os.path.join(SYSFS, "class", "power_supply", "BAT*"))
    if not bats:
        return "ok", "no battery (desktop or VM)"
    parts, state = [], "ok"
    for b in bats:
        cap, st = first_line(f"{b}/capacity"), first_line(f"{b}/status")
        full = first_line(f"{b}/energy_full") or first_line(f"{b}/charge_full")
        design = first_line(f"{b}/energy_full_design") or first_line(f"{b}/charge_full_design")
        health = f", health {int(full) * 100 // int(design)}%" if full and design and int(design) else ""
        if health and int(full) * 100 // int(design) < 60:
            state = "warn"
        parts.append(f"{os.path.basename(b)} {cap}% {st}{health}")
    return state, "; ".join(parts)


def t_wifi():
    from . import net
    objs = net.iwd_objects()
    if objs is None:
        return "fail", "wifi service (iwd) not responding"
    devs = net.wifi_devices(objs)
    if not devs:
        return "warn", "no wifi card found (or no firmware for it)"
    d = devs[0]
    return "ok", f'{d["name"]}: ' + (f'connected to "{d["ssid"]}"' if d["ssid"] else (d["state"] or "off"))


def t_bluetooth():
    from . import bt
    objs = bt.objects()
    path, a = bt.adapter(objs or {})
    if objs is None:
        # Without an adapter, systemd does not even start bluetooth.service (ConditionPathIsDirectory)
        if not os.path.isdir("/sys/class/bluetooth"):
            return "warn", "no Bluetooth adapter (or no firmware for it)"
        return "fail", "Bluetooth service not running"
    if not path:
        return "warn", "no Bluetooth adapter (or no firmware for it)"
    return "ok", f'{a.get("Name") or path}: {"on" if a.get("Powered") else "off"}'


def tone_wav(path, seconds=1.5):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        n = int(48000 * seconds)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * TONE_HZ * i / 48000)))
                               for i in range(n)))


def goertzel(samples, rate, freq):
    """Relative power of 'freq' in the signal (0..1)."""
    k = round(len(samples) * freq / rate)
    w = 2 * math.pi * k / len(samples)
    c, s1, s2 = 2 * math.cos(w), 0.0, 0.0
    for x in samples:
        s1, s2 = x + c * s1 - s2, s1
    power = s1 * s1 + s2 * s2 - c * s1 * s2
    total = sum(x * x for x in samples) * len(samples) / 2 or 1
    return power / total


def t_sound():
    """Speaker -> microphone: 1 kHz tone played and looked for in the recording."""
    sinks, sources = audio.devices()
    if not sinks:
        return "fail", "no sound output (PipeWire sees no card)"
    if not sources:
        return "warn", f"output OK ({len(sinks)}), but no microphone"
    out_v, in_v = audio.get_volume(audio.SINK), audio.get_volume(audio.SOURCE)
    tmp = tempfile.mkdtemp()
    tone, rec = f"{tmp}/tone.wav", f"{tmp}/rec.wav"
    tone_wav(tone)
    try:
        # Reasonable volume only during the test; then it is put back as it was
        sh(["wpctl", "set-mute", audio.SINK, "0"])
        sh(["wpctl", "set-mute", audio.SOURCE, "0"])
        if out_v and out_v[0] < 40:
            sh(["wpctl", "set-volume", audio.SINK, "0.4"])
        recorder = subprocess.Popen(["pw-record", "--rate", str(RATE), "--channels", "1",
                                     "--format", "s16", rec], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        time.sleep(0.5)
        sh(["pw-play", tone])
        time.sleep(0.3)
        recorder.terminate()
        recorder.wait(5)
        with wave.open(rec) as w:
            data = w.readframes(w.getnframes())
        samples = struct.unpack(f"<{len(data) // 2}h", data)[RATE // 4:]
    except (OSError, wave.Error, subprocess.TimeoutExpired, struct.error) as e:
        return "fail", f"could not play/record: {e}"
    finally:
        if out_v:
            sh(["wpctl", "set-volume", audio.SINK, f"{out_v[0] / 100:.2f}"])
            sh(["wpctl", "set-mute", audio.SINK, "1" if out_v[1] else "0"])
        if in_v:
            sh(["wpctl", "set-mute", audio.SOURCE, "1" if in_v[1] else "0"])
        for f in (tone, rec):
            try:
                os.unlink(f)
            except OSError:
                pass
        os.rmdir(tmp)
    if not samples:
        return "fail", "the microphone recorded nothing"
    rms = math.sqrt(sum(x * x for x in samples) / len(samples))
    ratio = goertzel(samples, RATE, TONE_HZ)
    if ratio > 0.05:
        return "ok", f"speaker and microphone work (tone heard, level {rms:.0f})"
    if rms < 30:
        return "fail", f"the microphone records silence (level {rms:.0f}): muted, no driver or no SOF firmware"
    return "warn", f"microphone records sound (level {rms:.0f}) but did not hear the speaker: volume low or speaker off?"


def t_model():
    port = os.environ.get("OSYR_LLAMA_PORT", "8083")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            ok = r.status == 200
    except OSError:
        ok = False
    rc, out, _ = sh(["systemctl", "is-active", "osyr-llama"])
    if ok:
        return "ok", "local model loaded and answering"
    return ("warn" if out == "activating" or out == "active" else "fail"), f"local model not ready ({out})"


def t_voice():
    """Speech recognition: espeak says a sentence and whisper transcribes it."""
    if not voice.model() or not os.path.exists(voice.WHISPER):
        return "fail", "speech recognition not installed"
    tmp = tempfile.mkdtemp()
    raw, wav16 = f"{tmp}/say.wav", f"{tmp}/say16.wav"
    try:
        rc, _, err = sh(["espeak", "-v", "en", "-w", raw, "This is a voice test."])
        if rc:
            return "fail", f"espeak: {err}"
        resample(raw, wav16)
        t0 = time.time()
        text, _ = voice.transcribe(wav16, "en")
        dt = time.time() - t0
        with wave.open(wav16) as w:
            dur = w.getnframes() / w.getframerate()
    except (OSError, RuntimeError, wave.Error) as e:
        return "fail", str(e)
    finally:
        for f in (raw, wav16):
            try:
                os.unlink(f)
            except OSError:
                pass
        os.rmdir(tmp)
    heard = "voice" in text.lower() or "test" in text.lower()
    return ("ok" if heard else "warn"), f'{dt:.1f} s to recognise {dur:.1f} s of speech: "{text}"'


def resample(src, dst):
    with wave.open(src) as w:
        rate, n = w.getframerate(), w.getnframes()
        data = struct.unpack(f"<{n}h", w.readframes(n))
    out_n = int(n * 16000 / rate)
    res = [data[min(int(i * rate / 16000), n - 1)] for i in range(out_n)]
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack(f"<{len(res)}h", *res))


TESTS = [("cpu", t_cpu), ("gpu", t_gpu), ("displays", t_displays), ("backlight", t_backlight),
         ("battery", t_battery), ("thermal", lambda: thermal.selftest_check()), ("wifi", t_wifi), ("bluetooth", t_bluetooth), ("sound", t_sound),
         ("model", t_model), ("voice", t_voice)]


def selftest(args):
    results = []
    for name, fn in TESTS:
        if args.only and name not in args.only.split(","):
            continue
        if name == "sound" and not args.json:
            print("Testing speaker and microphone: you will hear a short beep...", flush=True)
        try:
            state, detail = fn()
        except Exception as e:  # a broken test does not bring down the others
            state, detail = "fail", f"{type(e).__name__}: {e}"
        results.append({"test": name, "state": state, "detail": detail})
        if not args.json:
            print(f'{state.upper():<4}  {name:<10} {detail}', flush=True)
    bad = [r for r in results if r["state"] == "fail"]
    return Result(not bad, "OK" if not bad else "SELFTEST_FAILED", {"results": results},
                  "Send it for support with: osyr-diag" if bad else None,
                  text=f'{len(results) - len(bad)}/{len(results)} without failures.')


def register(sub):
    p = sub.add_parser("sys", help="system: hardware self-test, temperature, power profile")
    s = p.add_subparsers(dest="action", required=True)
    t = s.add_parser("selftest", help="test the hardware of this computer (plays a short beep)")
    t.add_argument("--only", help="comma-separated tests: " + ",".join(n for n, _ in TESTS))
    t.set_defaults(func=selftest)
    s.add_parser("thermal", help="CPU temperature and thermal throttling").set_defaults(func=thermal.thermal)
    power.register(s)
