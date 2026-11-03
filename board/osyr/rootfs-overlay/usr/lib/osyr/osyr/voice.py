"""voice — talk to OSYR: record (PipeWire), transcribe (whisper.cpp, locally)
and read aloud (espeak).

Holding Super+Space records; on release it is transcribed and the text reaches
the chat (input file + SIGUSR1 to the osyr-chat process). In the chat, /voice
records until Enter is pressed.
"""
import json
import os
import re
import signal
import subprocess
import time

WHISPER = "/opt/whisper/bin/whisper-cli"
MODEL_DIR = "/usr/share/osyr/models/whisper"
MIN_SECONDS = 0.6   # shorter presses are ignored


def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"


def paths():
    d = runtime_dir()
    return {"wav": os.path.join(d, "osyr-voice.wav"), "pid": os.path.join(d, "osyr-voice.pid"),
            "inbox": os.path.join(d, "osyr-chat.inbox"), "chat": os.path.join(d, "osyr-chat.pid"),
            "status": os.path.join(d, "osyr-status.pid")}


def alive(pidfile, name):
    """pid recorded in pidfile if that process is still alive and is `name` (a dead chat
    that did not delete its pidfile would leave a pid the kernel may give to another program)."""
    try:
        pid = int(open(pidfile).read())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().split(b"\0")
    except (OSError, ValueError):
        return None
    return pid if any(a.endswith(name.encode()) for a in argv[:2]) else None


def recording():
    try:
        pid = int(open(paths()["pid"]).read())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def refresh_bar():
    """Redraws the bar (● REC indicator) without waiting for its 5 s cycle."""
    pid = alive(paths()["status"], "osyr-status")
    if pid:
        try:
            os.kill(pid, signal.SIGUSR1)
        except OSError:
            pass


def start():
    """Starts recording in the background (16 kHz mono, what Whisper wants).
    First it silences OSYR's voice: the person wants to talk, and the microphone must not hear it."""
    stop_speaking()
    if recording():
        return False
    p = paths()
    proc = subprocess.Popen(["pw-record", "--rate", "16000", "--channels", "1",
                             "--format", "s16", p["wav"]],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    with open(p["pid"], "w") as f:
        f.write(str(proc.pid))
    refresh_bar()
    return True


def stop():
    """Stops the recording; returns the WAV path, or None if it was too short."""
    p = paths()
    pid = recording()
    if not pid:
        return None
    os.kill(pid, signal.SIGINT)  # pw-record closes the WAV properly on SIGINT
    for _ in range(50):
        try:
            os.kill(pid, 0)
            time.sleep(0.05)
        except OSError:
            break
    try:
        os.unlink(p["pid"])
    except FileNotFoundError:
        pass
    refresh_bar()
    try:
        size = os.path.getsize(p["wav"])
    except OSError:
        return None
    return p["wav"] if size > 44 + 16000 * 2 * MIN_SECONDS else None


def model():
    try:
        files = sorted(f for f in os.listdir(MODEL_DIR) if f.endswith(".bin"))
    except OSError:
        files = []
    return os.path.join(MODEL_DIR, files[0]) if files else None


def transcribe(wav, language="auto"):
    """(text, language) with whisper.cpp; it detects the language itself."""
    m = model()
    if not m or not os.path.exists(WHISPER):
        raise RuntimeError("speech recognition is not installed")
    threads = str(max(1, min(4, os.cpu_count() or 1)))
    p = subprocess.run([WHISPER, "-m", m, "-f", wav, "-l", language, "-t", threads,
                        "-nt", "-np"], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[-300:] or "whisper failed")
    text = " ".join(line.strip() for line in p.stdout.splitlines() if line.strip())
    text = re.sub(r"\[[A-Z_ ]+\]|\((?:[^)]*music|[^)]*silence)[^)]*\)", "", text, flags=re.I).strip()
    lang = re.search(r"auto-detected language:\s*(\w+)", p.stderr)
    return text, (lang.group(1) if lang else (None if language == "auto" else language))


def deliver(text, lang):
    """Hands the text to the open chat. False if there is no chat."""
    p = paths()
    pid = alive(p["chat"], "osyr-chat")
    if not pid:
        return False
    with open(p["inbox"], "w") as f:
        json.dump({"text": text, "lang": lang}, f)
    try:
        os.kill(pid, signal.SIGUSR1)
    except OSError:
        return False
    return True


# Only one voice at a time in the whole session (chat, reminders, battery...): each voice
# is a process group (sh | espeak | pw-play) recorded in this file; a new voice cuts
# whole groups off (it used to kill only the sh, and espeak kept playing underneath).
WAIT_MAX = 30  # seconds a notice waits for whatever is being said to finish
# Neural voice (Piper with sherpa-onnx) for the languages that have one in
# PIPER_DIR/<language>; otherwise espeak. osyr-say-piper reads sentence by sentence.
PIPER_DIR = "/usr/share/osyr/models/piper"
PIPER_TTS = "/opt/sherpa-onnx/bin/sherpa-onnx-offline-tts"
SAY_PIPER = "/usr/lib/osyr/osyr-say-piper"
SENTENCE_MAX = 280  # characters: longer sentences are split at commas
VOICE_PROCS = ("sh", "bash", "espeak", "espeak-ng", "pw-play", "pw-cat", "osyr-say-piper",
               "sherpa-onnx-off")  # comm: 15 characters


def speaking_file():
    return os.path.join(runtime_dir(), "osyr", "speaking")


def _alive(pgid):
    """Is any process in that group still part of a voice?"""
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = open(f"/proc/{d}/stat").read()
        except OSError:
            continue
        comm, rest = stat[stat.index("(") + 1:stat.rindex(")")], stat[stat.rindex(")") + 2:].split()
        if rest[0] != "Z" and int(rest[2]) == pgid and comm in VOICE_PROCS:
            return True
    return False


def _voices():
    try:
        groups = [int(x) for x in open(speaking_file()).read().split()]
    except (OSError, ValueError):
        return []
    return [g for g in groups if _alive(g)]


def _save(groups):
    f = speaking_file()
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f + ".tmp", "w") as out:
        out.write(" ".join(map(str, groups)))
    os.replace(f + ".tmp", f)


def speaking():
    return bool(_voices())


def stop_speaking():
    """Silences everything being said (also from other processes)."""
    for g in _voices():
        try:
            os.killpg(g, signal.SIGTERM)
        except OSError:
            pass
    _save([])


def piper_voice(lang):
    d = os.path.join(PIPER_DIR, lang)
    ok = all(os.path.exists(p) for p in (f"{d}/model.onnx", f"{d}/tokens.txt", PIPER_TTS, SAY_PIPER))
    return d if ok else None


def sentences(text):
    """Sentences to read one at a time (so the first one is heard sooner)."""
    out = []
    for part in re.split(r"(?<=[.!?…])\s+|\n+", text):
        part = part.strip().lstrip("-–— ")  # a leading dash would look like an option
        while len(part) > SENTENCE_MAX:
            cut = part.rfind(",", 0, SENTENCE_MAX)
            cut = cut if cut > 40 else part.rfind(" ", 0, SENTENCE_MAX)
            cut = cut if cut > 0 else SENTENCE_MAX
            out.append(part[:cut + 1].strip())
            part = part[cut + 1:].strip()
        if re.search(r"\w", part):
            out.append(part)
    return out


def speak(text, lang=None, wait=False):
    """Reads the text aloud without blocking. By default it cuts off whatever was being
    said (a new answer from the chat). wait=True (notices): waits for it to finish,
    up to WAIT_MAX s, inside the voice's own process, without blocking the caller."""
    voice = (lang or os.environ.get("LANG", "es")[:2] or "es").lower()
    if not re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]+)?", voice):
        voice = "es"
    text = re.sub(r"[*_`#>|]", " ", text)[:2000]  # without markdown marks
    piper = piper_voice(voice)
    if piper:
        lines = sentences(text)
        if not lines:
            return
        d = os.path.join(runtime_dir(), "osyr")
        os.makedirs(d, exist_ok=True)
        lst = os.path.join(d, f"say-{os.getpid()}-{time.monotonic_ns()}.txt")
        with open(lst, "w") as f:
            f.write("\n".join(lines) + "\n")
        cmd, args = f'{SAY_PIPER} "$1" "$2"', [piper, lst]
    else:
        cmd, args = f'printf "%s" "$1" | espeak -v {voice} -s 165 --stdout | pw-play -', [text]
    if wait:
        before = _voices()
        if before:
            groups = " ".join(map(str, before))
            cmd = (f"i=0; for g in {groups}; do while [ $i -lt {WAIT_MAX * 5} ] && "
                   f"kill -0 -- -$g 2>/dev/null; do sleep 0.2; i=$((i + 1)); done; done; {cmd}")
    else:
        stop_speaking()
    # The outer sh leaves the voice in the background and exits at once: it is reaped here
    # and the rest of the group is reaped by the system (otherwise the zombies of an open
    # chat would look like live voices). The group keeps its pid as its id.
    try:
        p = subprocess.Popen(["sh", "-c", f"{{ {cmd}; }} </dev/null >/dev/null 2>&1 &", "osyr-voice", *args],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        p.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return
    _save(_voices() + [p.pid])
