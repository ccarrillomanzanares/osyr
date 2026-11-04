#!/usr/bin/env python3
"""OSYR — image tests ('make test'). Two phases:

  native  the image's real binaries run on the VPS (fast):
          the agent with the real model using tools, 'look' with
          vision, speech recognition and the chat against an external provider.
  vm      the image booted in QEMU (snapshot: nothing is modified):
          tests/vm-checks.sh over the serial console, first boot, and from
          outside the audio recorded by QEMU and a screenshot of the session.

  install osyr-install from the USB stick to a second disk, and that disk booted
          in BIOS and in UEFI (/home passphrase over the serial console).

Usage: tests/run.py [native|vm|install|all], or tests/run.py boot DISK [format]
(boots a publishable disk in BIOS and UEFI, used by scripts/publish.sh). Results in build/test-results/.
Without KVM the VM is slow (~10 min); the native phase does not need it.
"""
import glob
import json
import os
import pty
import re
import select
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "output")
TARGET = os.path.join(OUT, "target")
IMAGE = os.path.join(OUT, "images", "osyr.img")
RESULTS = os.path.join(ROOT, "build", "test-results")
OVERLAY = os.path.join(ROOT, "board", "osyr", "rootfs-overlay")
LIB = os.path.join(OVERLAY, "usr", "lib", "osyr")
MODELS = os.path.join(TARGET, "usr", "share", "osyr", "models")

results = []


def report(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail and not ok else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def cpu_variant():
    """The ggml variant for the VPS CPU (the image ships all of them)."""
    flags = open("/proc/cpuinfo").read()
    return "haswell" if " avx2" in flags else "sandybridge" if " avx" in flags else "x64"


def target_cmd(binary, *args):
    """Runs an image binary on the VPS with its own libraries.
    whisper carries its own ggml with the same name as llama.cpp's: its
    libraries go first, as its RPATH does in the image."""
    libs = [f"{TARGET}/usr/lib", f"{TARGET}/lib"]
    if "/opt/whisper/" in binary:
        libs.insert(0, f"{TARGET}/opt/whisper/lib")
    libs = ":".join(libs)
    return [f"{TARGET}/lib/ld-linux-x86-64.so.2", "--library-path", libs, binary, *args]


def wait_http(url, seconds, proc=None):
    end = time.time() + seconds
    while time.time() < end:
        if proc and proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


def pty_run(argv, env, script, timeout=600):
    """Conversation over a pseudoterminal: script = [(wait_regex, text_to_send)]."""
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(env)
        os.execvp(argv[0], argv)
    out = b""
    end = time.time() + timeout

    def read_until(pat):
        nonlocal out
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    return False
            # without colours: the chat prints "[N tokens, X tok/s]" in grey
            if re.search(pat, re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", out[-4000:])):
                return True
        return False

    ok = True
    for pat, send in script:
        if not read_until(pat):
            ok = False
            break
        if send is not None:
            os.write(fd, send.encode() + b"\r")
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except OSError:
        pass
    return ok, re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out.decode(errors="replace"))


# --- Native phase -----------------------------------------------------------------------

def native():
    print("== native: the image's binaries on this machine", flush=True)
    tmp = tempfile.mkdtemp(prefix="osyr-test-")
    procs = []
    try:
        variant = cpu_variant()
        model = sorted(f for f in glob.glob(f"{MODELS}/*.gguf") if "mmproj" not in f)[0]
        mmproj = glob.glob(f"{MODELS}/*mmproj*.gguf")[0]
        port = free_port()
        env = dict(os.environ, GGML_BACKEND_PATH=f"{TARGET}/usr/lib/ggml/libggml-cpu-{variant}.so")
        # Like osyr-llama: template with a stable history and states saved to disk
        tpl, slots = f"{tmp}/chat-template.jinja", f"{tmp}/slots"
        os.makedirs(slots)
        subprocess.run(["python3", "-I", f"{LIB}/osyr/gguf.py", "template", model, tpl])
        # Like VISION=ondemand: the agent without the projector and vision on another server
        vport = free_port()
        vision = subprocess.Popen(target_cmd(f"{TARGET}/usr/bin/llama-server", "-m", model, "--mmproj", mmproj,
                                             "--host", "127.0.0.1", "--port", str(vport), "-c", "8192",
                                             "-t", str(min(8, os.cpu_count())), "-np", "1", "--jinja"),
                                  env=env, stdout=open(f"{tmp}/vision.log", "w"), stderr=subprocess.STDOUT)
        procs.append(vision)
        llama = subprocess.Popen(target_cmd(f"{TARGET}/usr/bin/llama-server", "-m", model,
                                            "--host", "127.0.0.1", "--port", str(port), "-c", "8192",
                                            "-t", str(min(8, os.cpu_count())), "-np", "2", "--kv-unified", "--jinja",
                                            *(["--chat-template-file", tpl] if os.path.exists(tpl) else []),
                                            "--slot-save-path", slots),
                                 env=env, stdout=open(f"{tmp}/llama.log", "w"), stderr=subprocess.STDOUT)
        procs.append(llama)
        report("native-llama-server", wait_http(f"http://127.0.0.1:{port}/health", 300, llama),
               "did not start; see llama.log")
        report("native-vision-server", wait_http(f"http://127.0.0.1:{vport}/health", 300, vision),
               "did not start; see vision.log")

        # Test 'osyr': the overlay's CLI, with a fake /sys (brightness at 50 %)
        sysfs = f"{tmp}/sys/class/backlight/intel_backlight"
        os.makedirs(sysfs)
        for k, v in (("type", "raw"), ("max_brightness", "1000"), ("brightness", "500")):
            open(f"{sysfs}/{k}", "w").write(v + "\n")
        bindir = f"{tmp}/bin"
        os.makedirs(bindir)
        with open(f"{bindir}/osyr", "w") as f:
            f.write(f"#!/bin/sh\nexec python3 -B -c \"import sys; sys.path.insert(0, '{LIB}'); "
                    f"from osyr.cli import main; sys.exit(main())\" \"$@\"\n")
        os.chmod(f"{bindir}/osyr", 0o755)
        chat_env = {"OSYR_LLAMA_PORT": str(port), "OSYR_LIB": LIB, "OSYR_SYSFS": f"{tmp}/sys",
                    "PATH": f"{bindir}:{os.environ['PATH']}", "TERM": "dumb", "HOME": tmp,
                    "XDG_RUNTIME_DIR": tmp, "XDG_CONFIG_HOME": f"{tmp}/cfg"}
        chat = ["python3", "-B", f"{OVERLAY}/usr/bin/osyr-chat"]

        # Agent: a query (without asking) and a change (asks for confirmation)
        ok, out = pty_run(chat, chat_env, [
            (rb"> $", "¿Qué brillo tiene la pantalla ahora mismo? Usa la herramienta."),
            (rb"tok/s\]\s*\r?\n\s*> $", "Pon el brillo al 80 por ciento."),
            (rb"\[y/N\] $", "y"),
            (rb"tok/s\]\s*\r?\n\s*> $", "/exit")], timeout=600)
        open(f"{RESULTS}/native-agent.txt", "w").write(out)
        report("agent-reads-with-tool", "[osyr ui brightness]" in out and "50" in out, "no tool call or no 50%")
        report("agent-asks-before-change", "Run 'osyr ui brightness 80'" in out, "no confirmation prompt")
        now = int(open(f"{sysfs}/brightness").read())
        report("agent-change-applied", now == 800, f"brightness is {now}")
        report("chat-history-saved", bool(glob.glob(f"{tmp}/.local/share/osyr/chats/*.json")), "no history file")

        # /install: the installer in the same window (fake sudo: only checks that it is launched)
        fakebin = f"{tmp}/fakesudo"
        os.makedirs(fakebin, exist_ok=True)
        with open(f"{fakebin}/sudo", "w") as f:
            f.write('#!/bin/sh\necho "FAKE-SUDO $*"\n')
        os.chmod(f"{fakebin}/sudo", 0o755)
        with open(f"{fakebin}/osyr-diag", "w") as f:
            f.write('#!/bin/sh\necho "FAKE-DIAG $*"\n')
        os.chmod(f"{fakebin}/osyr-diag", 0o755)
        # /install, /update, /update undo and /report: each launches its own program in the same window
        ok, out = pty_run(chat, dict(chat_env, PATH=f"{fakebin}:{chat_env['PATH']}"), [
            (rb"> $", "/install"),
            (rb"FAKE-SUDO osyr-install\s*\r?\n\s*> $", "/update"),
            (rb"FAKE-SUDO osyr update apply\s*\r?\n\s*> $", "/update undo"),
            (rb"FAKE-SUDO osyr update undo\s*\r?\n\s*> $", "/report"),
            (rb"FAKE-DIAG\s*\r?\n\s*> $", "/exit")], timeout=120)
        open(f"{RESULTS}/native-install.txt", "w").write(out)
        report("chat-install-runs-installer", "FAKE-SUDO osyr-install" in out, "the installer was not started")
        report("chat-update-runs-update", "FAKE-SUDO osyr update apply" in out
               and "FAKE-SUDO osyr update undo" in out, "/update or /update undo did not run")
        report("chat-report-runs-diag", "FAKE-DIAG" in out, "/report did not run osyr-diag")

        # Update without rebooting: if the chat changed, it relaunches itself with the same conversation
        livebin = f"{tmp}/fakesudo-live"
        os.makedirs(livebin, exist_ok=True)
        reload_file = f"{tmp}/session-reload"
        with open(f"{livebin}/sudo", "w") as f:
            f.write(f'#!/bin/sh\necho "FAKE-SUDO $*"\n'
                    f'[ "$*" = "osyr update apply" ] && printf \'{{"id": %s, "actions": ["chat"]}}\' "$(date +%s%N)" > {reload_file}\n'
                    f'exit 0\n')
        os.chmod(f"{livebin}/sudo", 0o755)
        before = set(glob.glob(f"{tmp}/.local/share/osyr/chats/*.json"))
        ok, out = pty_run(chat, dict(chat_env, PATH=f"{livebin}:{chat_env['PATH']}", OSYR_SESSION_RELOAD=reload_file), [
            (rb"> $", "Hola, di solo: vale."),
            (rb"tok/s\]\s*\r?\n\s*> $", "/update"),
            (rb"conversation goes on \(1 earlier questions\)\]\s*> $", "/exit")], timeout=600)
        open(f"{RESULTS}/native-live-update.txt", "w").write(out)
        after = set(glob.glob(f"{tmp}/.local/share/osyr/chats/*.json"))
        report("chat-update-relaunches", ok and "restarting the chat with the new version" in out
               and "Restart now?" not in out and len(after - before) == 1, out[-400:])

        # Memory: it saves something when asked and knows it in another conversation
        ok, out = pty_run(chat, chat_env, [
            (rb"> $", "Recuerda que mi hermana se llama Ana."),
            (rb"tok/s\]\s*\r?\n\s*> $", "/exit")], timeout=600)
        open(f"{RESULTS}/native-memory.txt", "w").write(out)
        try:
            saved = " ".join(i["text"] for i in json.load(open(f"{tmp}/.local/share/osyr/memory.json")))
        except (OSError, ValueError, KeyError, TypeError):
            saved = ""
        report("memory-saved", "Ana" in saved, f"memory.json: {saved[:120]!r}")
        ok, out = pty_run(chat, chat_env, [
            (rb"> $", "¿Cómo se llama mi hermana?"),
            (rb"tok/s\]\s*\r?\n\s*> $", "/exit")], timeout=600)
        open(f"{RESULTS}/native-memory2.txt", "w").write(out)
        report("memory-recalled", "Ana" in out.split("¿Cómo se llama mi hermana?", 1)[-1], out[-200:])

        # Vision: the screenshot of the OSYR session
        p = subprocess.run(["python3", "-B", "-c", f"import sys; sys.path.insert(0,'{LIB}'); "
                            "from osyr.cli import main; sys.exit(main())",
                            "ai", "look", "--file", f"{ROOT}/tests/data/osyr-session.png",
                            "What words are written at the top of the screen?", "--json"],
                           capture_output=True, text=True, timeout=600,
                           env=dict(os.environ, OSYR_LLAMA_PORT=str(port), OSYR_VISION_PORT=str(vport),
                                    XDG_CONFIG_HOME=f"{tmp}/cfg"))
        try:
            ans = json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            ans = {}
        text = (ans.get("data") or {}).get("answer", "")
        open(f"{RESULTS}/native-look.txt", "w").write(text or p.stdout + p.stderr)
        report("look-sees-screen", ans.get("ok") and re.search(r"OSYR|TERMINAL|LOGON", text, re.I),
               (text or p.stderr)[-200:])

        # Voice: the image's espeak says a sentence, the image's whisper transcribes it
        spoken = f"{tmp}/voice.wav"
        espeak_env = dict(os.environ, ESPEAK_DATA_PATH=f"{TARGET}/usr/share")
        subprocess.run(target_cmd(f"{TARGET}/usr/bin/espeak", "-v", "es", "-s", "140", "-w", spoken,
                                  "Hola, esto es una prueba de voz del sistema."),
                       env=espeak_env, capture_output=True, timeout=60)
        resample_16k(spoken, f"{tmp}/voice16.wav")
        wmodel = glob.glob(f"{MODELS}/whisper/*.bin")
        p = subprocess.run(target_cmd(f"{TARGET}/opt/whisper/bin/whisper-cli", "-m", wmodel[0] if wmodel else "",
                                      "-f", f"{tmp}/voice16.wav", "-l", "auto", "-nt", "-np", "-t", "4"),
                           capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, GGML_BACKEND_PATH=f"{TARGET}/opt/whisper/lib/libggml-cpu-{variant}.so"))
        open(f"{RESULTS}/native-voice.txt", "w").write(p.stdout + "\n---\n" + p.stderr[-2000:])
        report("voice-transcribes", "prueba" in p.stdout.lower(), p.stdout.strip()[:120] or p.stderr[-200:])

        # Chat against an external provider (simulated OpenAI server with X-API-Key)
        mport = free_port()
        mock = subprocess.Popen(["python3", "-B", f"{ROOT}/tests/mock_openai.py", str(mport)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(mock)
        time.sleep(1)
        cli = ["python3", "-B", "-c", f"import sys; sys.path.insert(0,'{LIB}'); from osyr.cli import main; sys.exit(main())"]
        cenv = dict(os.environ, XDG_CONFIG_HOME=f"{tmp}/cfg")
        subprocess.run(cli + ["ai", "add", "mock", "--url", f"http://127.0.0.1:{mport}/v1", "--api-key",
                              "secret123", "--key-header", "X-API-Key"], env=cenv, capture_output=True)
        subprocess.run(cli + ["ai", "use", "mock", "--confirm", "send-mock"], env=cenv, capture_output=True)
        ok, out = pty_run(chat, dict(chat_env, XDG_CONFIG_HOME=f"{tmp}/cfg"),
                          [(rb"> $", "hello"), (rb"Remote answer", "/exit")], timeout=60)
        report("chat-remote-provider", ok and "REMOTE AI: mock" in out, out[-200:])

        # Switching to a remote provider while the local model is still warming up
        # (/pair right after opening the chat on a slow CPU): the remote answers at once
        sport = free_port()
        slow = subprocess.Popen(["python3", "-B", f"{ROOT}/tests/slow_llama.py", str(sport), "60"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(slow)
        time.sleep(1)
        subprocess.run(cli + ["ai", "use", "local"], env=cenv, capture_output=True)
        t0 = time.time()
        ok, out = pty_run(chat, dict(chat_env, OSYR_LLAMA_PORT=str(sport), XDG_CONFIG_HOME=f"{tmp}/cfg"),
                          [(rb"> $", "/ai mock"), (rb"\[y/N\]", "y"), (rb"> $", "hello"),
                           (rb"(Remote answer|Local answer)", "/exit")], timeout=50)
        report("chat-switch-during-warmup", ok and "Remote answer" in out and time.time() - t0 < 30,
               f"{time.time() - t0:.0f} s; " + out[-200:])

        # Saved fixed prompt: the first time it is processed and saved; the second time
        # it is restored and a new message only processes the question
        probe = (f"import importlib.machinery as M, importlib.util as U, json\n"
                 f"l=M.SourceFileLoader('c','{OVERLAY}/usr/bin/osyr-chat'); m=U.module_from_spec(U.spec_from_loader('c',l)); l.exec_module(m)\n"
                 f"m.warm_prefix(); m.warm_prefix()\n"
                 # another request in between (like /look or the diag): it goes to slot 1 and does not clear the chat's
                 f"m.local_post('/completion', {{'prompt': 'An unrelated request. ' * 40, 'n_predict': 1, 'id_slot': 1}})\n"
                 f"r=m.local_post('/v1/chat/completions', {{'messages': [{{'role':'system','content':m.system_prompt()}}, {{'role':'user','content':'Hello'}}], "
                 f"'tools': m.TOOLS, 'max_tokens': 8, 'chat_template_kwargs': {{'enable_thinking': False}}, 'id_slot': m.SLOT}})\n"
                 f"print(json.dumps(r['timings']))")
        r = subprocess.run(["python3", "-B", "-c", probe], capture_output=True, text=True, timeout=600,
                           env=dict(chat_env, OSYR_LLAMA_PORT=str(port)))
        saved = glob.glob(f"{slots}/prefix-*.bin")
        try:
            processed = json.loads(r.stdout.strip().splitlines()[-1])["prompt_n"]
        except (ValueError, IndexError, KeyError):
            processed = None
        report("prefix-warm", processed is not None and processed < 60,
               f"processed={processed} saved_to_disk={len(saved)} {r.stderr[-200:]}")
        # Without --mmproj on the agent's server, llama.cpp does save the state to disk
        # (one per distinct fixed prompt: before and after the memory test)
        report("prefix-saved-to-disk", len(saved) >= 1, f"saved_to_disk={len(saved)}")

        # osyr-hwtest: the model test (two messages; the second does not reread everything)
        hw_state = f"{tmp}/hwstate"
        r = subprocess.run(["python3", "-B", f"{OVERLAY}/usr/bin/osyr-hwtest", "--auto", "--no-send", "--only", "model"],
                           capture_output=True, text=True, timeout=900,
                           env=dict(chat_env, OSYR_LLAMA_PORT=str(port), PYTHONPATH=LIB, XDG_STATE_HOME=hw_state))
        try:
            hw = {x["test"]: x for x in json.load(open(f"{hw_state}/osyr/hwtest/last.json"))["results"]}
        except (OSError, ValueError, KeyError):
            hw = {}
        report("hwtest-model", hw.get("model", {}).get("state") == "ok"
               and hw.get("model-cache", {}).get("state") == "ok", (r.stdout + r.stderr)[-300:])
    except (IndexError, OSError) as e:
        report("native-setup", False, str(e))
    finally:
        for p in procs:
            p.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def resample_16k(src, dst):
    """Mono WAV at 16 kHz (what whisper wants), with linear interpolation."""
    try:
        with wave.open(src) as w:
            rate, n = w.getframerate(), w.getnframes()
            data = struct.unpack(f"<{n}h", w.readframes(n))
    except (OSError, wave.Error, struct.error):
        return
    out_n = int(n * 16000 / rate)
    res = []
    for i in range(out_n):
        x = i * rate / 16000
        j = int(x)
        a, b = data[min(j, n - 1)], data[min(j + 1, n - 1)]
        res.append(int(a + (b - a) * (x - j)))
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack(f"<{len(res)}h", *res))


# --- VM phase ----------------------------------------------------------------------------

class Serial:
    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.connect(path)
        self.s.settimeout(1)
        self.buf = b""

    def wait(self, pat, seconds):
        end = time.time() + seconds
        while time.time() < end:
            try:
                d = self.s.recv(65536)
                if d:
                    self.buf += d
            except socket.timeout:
                pass
            if re.search(pat, self.buf):
                return True
        return False

    def send(self, text):
        self.s.sendall(text.encode() + b"\r")

    def text(self):
        txt = re.sub(r"\x1b(\[[0-9;?]*[A-Za-z]|\][^\\]*\\)", "", self.buf.decode(errors="replace"))
        return txt.replace("\r", "")


def monitor(path, cmd):
    m = socket.socket(socket.AF_UNIX)
    m.connect(path)
    time.sleep(0.5)
    m.recv(4096)
    m.send((cmd + "\n").encode())
    time.sleep(2)
    m.close()


def serve_repo(tmp):
    """A git repo that can be served over HTTP: the current one + a test commit."""
    repo = f"{tmp}/www/osyr.git"
    subprocess.run(["git", "clone", "-q", "--bare", ROOT, repo], check=True)
    work = f"{tmp}/work"
    subprocess.run(["git", "clone", "-q", repo, work], check=True)
    f = f"{work}/board/osyr/rootfs-overlay/usr/bin/osyr-test-update"
    open(f, "w").write("#!/bin/sh\necho 'update OK'\n")
    os.chmod(f, 0o755)
    g = ["git", "-C", work, "-c", "user.name=test", "-c", "user.email=test@osyr"]
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["commit", "-qm", "test update"], check=True)
    subprocess.run(g + ["push", "-q", "origin", "HEAD:main"], check=True)
    subprocess.run(["git", "-C", repo, "update-server-info"], check=True)
    shutil.copy(f"{ROOT}/tests/vm-checks.sh", f"{tmp}/www/vm-checks.sh")


def vm():
    print("== vm: the image in QEMU (snapshot)", flush=True)
    if not os.path.exists(IMAGE):
        report("vm-image", False, "no image: run 'make' first")
        return
    tmp = tempfile.mkdtemp(prefix="osyr-vm-")
    procs = []
    try:
        serve_repo(tmp)
        hport, mport = free_port(), free_port()
        procs.append(subprocess.Popen(["python3", "-m", "http.server", str(hport), "--bind", "127.0.0.1",
                                       "--directory", f"{tmp}/www"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        procs.append(subprocess.Popen(["python3", "-B", f"{ROOT}/tests/mock_openai.py", str(mport)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        # Pairing receiver (osyr ai pair) like the one on the VPS
        pport = free_port()
        os.makedirs(f"{tmp}/diag")
        open(f"{tmp}/diag.token", "w").write("test-token-0123456789")
        procs.append(subprocess.Popen(["python3", "-I", f"{ROOT}/scripts/diag-server.py", "--port", str(pport),
                                       "--dir", f"{tmp}/diag", "--token-file", f"{tmp}/diag.token",
                                       "--public-url", f"http://10.0.2.2:{pport}"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        ser, mon, wav = f"{tmp}/serial.sock", f"{tmp}/monitor.sock", f"{RESULTS}/vm-audio.wav"
        kvm = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.W_OK) else []
        # Without KVM each virtual CPU is a VPS thread: more CPUs, faster test.
        # 12 of 16 by default: the VPS also runs the llama of llama-hardened.
        smp = os.environ.get("OSYR_TEST_SMP", "12")
        # USB stick (FAT, label OSYRTEST, with hello.txt): it has to mount by itself.
        # With a partition table (MBR, one FAT32 partition from 1 MiB), like a real
        # one: without it, Linux sometimes takes the boot sector for a table
        stick = f"{tmp}/stick.img"
        size = 64 * 1024 ** 2
        with open(stick, "wb") as f:
            f.truncate(size)
            f.seek(446)
            f.write(bytes([0, 0, 0, 0, 0x0c, 0, 0, 0]) + (2048).to_bytes(4, "little")
                    + (size // 512 - 2048).to_bytes(4, "little"))
            f.seek(510)
            f.write(b"\x55\xaa")
        part = f"{stick}@@1M"
        subprocess.run(["mformat", "-i", part, "-F", "-v", "OSYRTEST", "::"], check=True)
        with open(f"{tmp}/hello.txt", "w") as f:
            f.write("hello from the stick\n")
        subprocess.run(["mcopy", "-i", part, f"{tmp}/hello.txt", "::hello.txt"], check=True)
        qemu = subprocess.Popen(["qemu-system-x86_64", *kvm, "-m", "6G", "-smp", smp,
                                 "-drive", f"file={IMAGE},format=raw,if=virtio,snapshot=on",
                                 "-device", "qemu-xhci,id=xhci",
                                 "-drive", f"if=none,id=stick,file={stick},format=raw",
                                 "-device", "usb-storage,bus=xhci.0,drive=stick",
                                 # two displays: extend, mirror, turn one off
                                 "-device", "virtio-vga,max_outputs=2", "-display", "none",
                                 "-audiodev", f"wav,id=snd0,path={wav}",
                                 "-device", "intel-hda", "-device", "hda-duplex,audiodev=snd0",
                                 "-monitor", f"unix:{mon},server,nowait",
                                 "-serial", f"unix:{ser},server,nowait",
                                 "-nic", "user,model=virtio-net-pci"],
                                stdout=subprocess.DEVNULL, stderr=open(f"{RESULTS}/qemu.log", "w"))
        procs.append(qemu)
        time.sleep(2)
        s = Serial(ser)
        end = time.time() + 600
        while not s.wait(rb"login: $", 15):
            s.send("")
            if time.time() > end or qemu.poll() is not None:
                report("vm-boots", False, "no login prompt")
                return
        report("vm-boots", True)
        time.sleep(60)  # graphical session, user services and model
        s.send("osyr")
        s.wait(rb"Password: ", 30)
        s.send("osyr")
        s.wait(rb"\$ $", 60)
        s.buf = b""
        # Downloaded to a file and with stdin from /dev/null: with 'curl | sh', a
        # command that reads standard input eats a piece of the script itself
        s.send(f"curl -s -o /tmp/vm-checks.sh http://10.0.2.2:{hport}/vm-checks.sh && HOST=10.0.2.2 "
               f"GITPORT={hport} MOCKPORT={mport} PAIRPORT={pport} sh /tmp/vm-checks.sh </dev/null 2>&1; "
               f"echo CHECKS-END")
        s.wait(rb"\nCHECKS-END\r?\n", 3600)  # \n in front: not the echo of the command
        out = s.text()
        open(f"{RESULTS}/vm-checks.txt", "w").write(out)
        for m in re.finditer(r"^(PASS|FAIL) (\S+)(?:: (.*))?$", out, re.M):
            report(m.group(2), m.group(1) == "PASS", m.group(3) or "")
        if "CHECKS DONE" not in out:
            report("vm-checks-complete", False, "the script did not finish")

        # First boot: new password and root locked
        s.buf = b""
        s.send("osyr-firstboot --force")
        steps = [(rb"can make mistakes(.|\n)*Press Enter to continue\. $", ""),
                 (rb"Language \(number, Enter to keep\): $", ""),
                 (rb"Keyboard \(number, Enter to keep\): $", ""),
                 (rb"Time zone \(number, Enter to keep\): $", ""),
                 (rb"\[Y/n\] $", "y"), (rb"New password: $", "new-password1"),
                 # one * per character: you can see that you are typing
                 (rb"New password: \*{13}\r?\nRepeat it: $", "new-password1"),
                 (rb"Repeat it: \*{13}\r?\n", None), (rb"Password changed", None)]
        ok = True
        for pat, ans in steps:
            if not s.wait(pat, 120):
                ok = False
                break
            if ans is not None:
                s.send(ans)
        s.wait(rb"\$ $", 120)
        report("firstboot-password", ok, s.text()[-300:])
        s.buf = b""
        s.send("echo new-password1 | sudo -S -p '' grep '^root:' /etc/shadow | cut -d: -f2 | cut -c1; echo FB-END")
        s.wait(rb"\nFB-END\r?\n", 60)
        report("firstboot-root-locked", re.search(r"^!$", s.text(), re.M) is not None, s.text()[-200:])

        monitor(mon, f"screendump {tmp}/screen.ppm")
        try:
            from PIL import Image  # only to save the screenshot as PNG
            Image.open(f"{tmp}/screen.ppm").save(f"{RESULTS}/vm-screen.png")
        except Exception:
            shutil.copy(f"{tmp}/screen.ppm", f"{RESULTS}/vm-screen.ppm")
        monitor(mon, "quit")
        qemu.wait(30)
        report("audio-reaches-speakers", audio_has_sound(wav), "the recorded output is silent")
    except (OSError, subprocess.SubprocessError) as e:
        report("vm-setup", False, str(e))
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)


# --- Install phase: osyr-install from the USB stick to a second disk -----------------

def qemu_boot(tmp, drives, uefi=False, name="vm", fmt="raw"):
    """QEMU with some disks (the first one boots) and the serial console on a socket."""
    ser = f"{tmp}/{name}.sock"
    fw = []
    if uefi:
        code = "/usr/share/OVMF/OVMF_CODE_4M.fd"
        shutil.copy("/usr/share/OVMF/OVMF_VARS_4M.fd", f"{tmp}/{name}-vars.fd")
        fw = ["-drive", f"if=pflash,format=raw,readonly=on,file={code}",
              "-drive", f"if=pflash,format=raw,file={tmp}/{name}-vars.fd"]
    kvm = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.W_OK) else []
    disks = []
    for path, snapshot in drives:
        disks += ["-drive", f"file={path},format={fmt},if=virtio" + (",snapshot=on" if snapshot else "")]
    q = subprocess.Popen(["qemu-system-x86_64", *kvm, "-m", "6G", "-smp", os.environ.get("OSYR_TEST_SMP", "12"),
                          *fw, *disks, "-display", "none", "-serial", f"unix:{ser},server,nowait",
                          "-nic", "user,model=virtio-net-pci"],
                         stdout=subprocess.DEVNULL, stderr=open(f"{RESULTS}/qemu-{name}.log", "w"))
    time.sleep(2)
    return q, Serial(ser)


def serial_login(s, q, seconds=900, passphrase=None, password="osyr"):
    """Waits for the login (first answering the /home passphrase if asked) and logs in as osyr."""
    end = time.time() + seconds
    asked = 0
    while time.time() < end and q.poll() is None:
        if passphrase and s.wait(rb"passphrase for", 5) and asked < 5:
            # wrong the first time: tries=0 must ask again
            s.send("wrong-passphrase" if asked == 0 else passphrase)
            asked += 1
            # when typing, systemd redraws the prompt (erases "press TAB"): it is not a
            # new question. If the new one arrived within these 2 s and were lost,
            # the Enter below counts as an empty attempt and it asks again.
            time.sleep(2)
            s.wait(rb"$^", 0.5)
            s.buf = s.buf.replace(b"passphrase for", b"")
            continue
        if s.wait(rb"login: $", 10):
            s.send("osyr")
            s.wait(rb"Password: ", 30)
            s.send(password)
            # osyr's prompt is coloured: "$\e[0m ". Up to 5 min: without KVM the login
            # coincides with the start of the tty1 session (user services, model)
            return s.wait(rb"\$(\x1b\[[0-9;]*m)* ", 300), asked
        s.send("")
    return False, asked


def serial_run(s, cmd, seconds=120):
    s.buf = b""
    s.send(f"{cmd}; echo RUN-END")
    s.wait(rb"\nRUN-END\r?\n", seconds)
    return s.text()


def install():
    print("== install: osyr-install from the USB stick to a second disk (QEMU)", flush=True)
    if not os.path.exists(IMAGE):
        report("install-image", False, "no image: run 'make' first")
        return
    # the target disk takes ~7 GB: in build/, not in /tmp (it may be tmpfs)
    tmp = tempfile.mkdtemp(prefix="osyr-install-", dir=os.path.join(ROOT, "build"))
    disk = f"{tmp}/disk.img"
    q = None
    try:
        with open(disk, "wb") as f:
            f.truncate(30 * 1024 ** 3)
        q, s = qemu_boot(tmp, [(IMAGE, True), (disk, False)], name="usb")
        ok, _ = serial_login(s, q)
        report("install-usb-boots", ok, s.text()[-300:])
        if not ok:
            return
        out = serial_run(s, "osyr-install --list --json")
        m = re.search(r"^\[.*\]$", out, re.M)
        lst = {d["name"]: d for d in json.loads(m.group(0))} if m else {}
        report("install-list", lst.get("vda", {}).get("usable") is False and lst.get("vdb", {}).get("usable"),
               out[-300:])
        out = serial_run(s, "echo osyr | sudo -S -p '' osyr-install --disk /dev/vdb --confirm vda "
                            "--passphrase-file /dev/null; echo RC=$?")
        report("install-refuses-mismatch", "RC=1" in out and "Not confirmed" in out, out[-300:])
        out = serial_run(s, "printf 'test-passphrase\\n' > /tmp/pp; echo osyr | sudo -S -p '' "
                            "osyr-install --disk /dev/vdb --confirm vdb --passphrase-file /tmp/pp; echo RC=$?", 3600)
        open(f"{RESULTS}/install-run.txt", "w").write(out)
        report("install-runs", "RC=0" in out and "OSYR is installed" in out, out[-400:])
        q.kill()
        q.wait()
        if "RC=0" not in out:
            return

        # The disk alone, in BIOS: /home passphrase (one wrong, one right), session and mounts
        q, s = qemu_boot(tmp, [(disk, True)], name="disk-bios")
        ok, asked = serial_login(s, q, passphrase="test-passphrase")
        open(f"{RESULTS}/install-serial-bios.txt", "w").write(s.text())
        report("install-boots-bios", ok, s.text()[-300:])
        report("install-asks-again", asked >= 2, f"passphrase asked {asked} times")
        if ok:
            out = serial_run(s, "findmnt -no SOURCE /; findmnt -no SOURCE,FSTYPE /home; "
                                "ls -a ~ | grep -c bash_profile; cat /etc/osyr/install.conf")
            open(f"{RESULTS}/install-bios.txt", "w").write(out)
            report("install-root-on-disk", re.search(r"^/dev/vda2$", out, re.M) is not None, out[-300:])
            report("install-home-encrypted", re.search(r"^/dev/mapper/home ext4$", out, re.M) is not None,
                   out[-300:])
            report("install-home-copied", re.search(r"^1$", out, re.M) is not None, out[-300:])
        q.kill()
        q.wait()

        # UEFI with the USB stick also plugged in: the disk boots, not the stick
        q, s = qemu_boot(tmp, [(disk, True), (IMAGE, True)], uefi=True, name="disk-uefi")
        ok, _ = serial_login(s, q, passphrase="test-passphrase")
        open(f"{RESULTS}/install-serial-uefi.txt", "w").write(s.text())
        report("install-boots-uefi", ok, s.text()[-300:])
        if ok:
            out = serial_run(s, "findmnt -no SOURCE /; cat /proc/cmdline")
            report("install-uefi-not-usb", re.search(r"^/dev/vda2$", out, re.M) is not None
                   and "a105a105" not in out, out[-300:])
        q.kill()
        q.wait()

        # Unencrypted (--no-encrypt), over the encrypted installation: boots without a passphrase
        q, s = qemu_boot(tmp, [(IMAGE, True), (disk, False)], name="usb-plain")
        ok, _ = serial_login(s, q)
        if not ok:
            report("install-plain-runs", False, s.text()[-300:])
            return
        out = serial_run(s, "echo osyr | sudo -S -p '' osyr-install --disk /dev/vdb --confirm vdb "
                            "--no-encrypt; echo RC=$?; ls /run/osyr/no-idle 2>&1", 3600)
        open(f"{RESULTS}/install-plain-run.txt", "w").write(out)
        report("install-plain-runs", "RC=0" in out and "OSYR is installed" in out, out[-400:])
        report("install-idle-resumed", "No such file" in out, out[-200:])
        q.kill()
        q.wait()
        if "RC=0" not in out:
            return
        q, s = qemu_boot(tmp, [(disk, True)], name="disk-plain")
        ok, asked = serial_login(s, q)
        open(f"{RESULTS}/install-serial-plain.txt", "w").write(s.text())
        report("install-plain-boots", ok and asked == 0, f"passphrase asked {asked} times; {s.text()[-300:]}")
        if ok:
            out = serial_run(s, "findmnt -no SOURCE,FSTYPE /home; grep ENCRYPTED /etc/osyr/install.conf; "
                                "ls /etc/crypttab 2>&1")
            report("install-home-plain", re.search(r"^/dev/vda4 ext4$", out, re.M) is not None
                   and "ENCRYPTED=no" in out and "No such file" in out, out[-300:])
        q.kill()
        q.wait()
        update_image_test(tmp, disk)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        report("install-setup", False, str(e))
    finally:
        if q and q.poll() is None:
            q.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def update_image_test(tmp, disk):
    """osyr update image on the installed (unencrypted) disk: a new version in slot B
    with the password, the wifi and a changed setting kept; rolling back; and a broken B
    that does not boot falls back to A by itself. The "new" image is the same one, signed
    by a test key and announced with a later commit (the one from serve_repo)."""
    www = f"{tmp}/www"
    os.makedirs(www, exist_ok=True)
    serve_repo(tmp)
    newer = subprocess.run(["git", "-C", f"{www}/osyr.git", "rev-parse", "main"],
                           capture_output=True, text=True, check=True).stdout.strip()
    xz = f"{www}/osyr.img.xz"
    subprocess.run(f"xz -T0 -1 -c '{IMAGE}' > '{xz}'", shell=True, check=True)
    key = f"{tmp}/testkey"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", key], check=True)
    def manifest(small):
        # small: with osyr-update.img.xz (the root without models); otherwise, only the whole image
        subprocess.run(["python3", f"{ROOT}/scripts/image-manifest.py", IMAGE, xz, "v99.0.0-test", newer,
                        f"{xz}.manifest", "--key", key] + (["--update", f"{www}/osyr-update.img.xz"] if small else []),
                       check=True, capture_output=True)
    manifest(True)
    listed = re.search(r"^MODELS=(.*)$", open(f"{xz}.manifest").read(), re.M)[1]
    sums = {p: d for p, d, _ in (x.rsplit(":", 2) for x in listed.split(","))}
    pub = open(key + ".pub").read().split()
    port = free_port()
    http = subprocess.Popen(["python3", "-m", "http.server", str(port), "--bind", "0.0.0.0", "--directory", www],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    host = f"http://10.0.2.2:{port}"
    q = None
    try:
        q, s = qemu_boot(tmp, [(disk, False)], name="upd-a")
        ok, _ = serial_login(s, q)
        report("update-image-boots", ok, s.text()[-300:])
        if not ok:
            return
        sudo = "echo osyr2 | sudo -S -p ''"
        setup = (f"echo osyr | sudo -S -p '' sh -c 'echo osyr:osyr2 | chpasswd'; sudo -k; "
                 # sh -c and not "... | sudo tee": with "echo osyr2 | sudo" in front, tee would read the password
                 f"{sudo} sh -c 'echo test@osyr namespaces=\\\"osyr-image\\\" {pub[0]} {pub[1]} >> /etc/osyr/update-signers; "
                 f"printf \"REPO={host}/osyr.git\\nIMAGE_URL={host}/osyr.img.xz\\n\" >> /etc/osyr/update.conf; "
                 f"mkdir -p /var/lib/iwd; echo test-psk > /var/lib/iwd/test.psk'; "
                 f"{sudo} sed -i 's/^CTX=auto/CTX=8192/' /etc/osyr/llama.conf")
        serial_run(s, setup, 120)
        out = serial_run(s, f"{sudo} osyr update image check; echo RC=$?", 600)
        report("update-image-check", "v99.0.0-test" in out and "RC=0" in out, out[-400:])
        # The manifest altered after signing it (the signature is from the good key) is rejected
        with open(f"{xz}.manifest", "a") as f:
            f.write("VERSION=v100.0.0-evil\n")
        out = serial_run(s, f"{sudo} osyr update image check; echo RC=$?", 600)
        report("update-image-tampered", "RC=0" not in out and "not signed" in out, out[-400:])
        manifest(True)

        def apply(name):
            out = serial_run(s, f"tok=$({sudo} osyr update apply --json | tail -1 | "
                                f"python3 -c 'import json,sys; print(json.load(sys.stdin)[\"confirm\"])'); "
                                f"{sudo} osyr update image apply --confirm $tok; echo RC=$?", 3600)
            open(f"{RESULTS}/{name}.txt", "w").write(out)
            return out
        out = apply("update-image-apply")
        report("update-image-apply", "RC=0" in out and "is ready (system B" in out, out[-500:])
        # the models are not downloaded: they come from the current slot
        report("update-image-small", "Copying Qwen3.5-4B-Q4_K_M.gguf" in out
               and re.search(r"Downloading OSYR v99.0.0-test \(0\.[0-9] GB", out) is not None, out[-500:])
        if "RC=0" not in out:
            return

        n = [0]

        def reboot(password="osyr2"):
            n[0] += 1
            s.buf = b""
            s.send(f"{sudo} systemctl reboot; echo REBOOT-RC=$?")
            ok = serial_login(s, q, password=password)[0]
            open(f"{RESULTS}/update-image-reboot{n[0]}.txt", "w").write(s.text())
            return ok
        ok = reboot()
        report("update-image-boots-b", ok, s.text()[-300:])
        if not ok:
            return
        time.sleep(20)  # osyr-boot-ok after multi-user
        out = serial_run(s, f"findmnt -no SOURCE /; {sudo} cat /var/lib/iwd/test.psk; grep ^CTX /etc/osyr/llama.conf; "
                            f"grep SLOT /etc/osyr/install.conf; {sudo} mount /dev/vda1 /mnt; "
                            f"head -3 /mnt/osyr/grub/grubenv; {sudo} umount /mnt; findmnt -no SOURCE /home", 120)
        open(f"{RESULTS}/update-image-b.txt", "w").write(out)
        report("update-image-root-b", re.search(r"^/dev/vda3$", out, re.M) is not None, out[-300:])
        report("update-image-kept-state", "test-psk" in out and "CTX=8192" in out
               and re.search(r"^/dev/vda4$", out, re.M) is not None, out[-300:])
        out2 = serial_run(s, "sha256sum " + " ".join(f"/{p}" for p in sums), 1800)
        report("update-image-models", all(f"{d}  /{p}" in out2 for p, d in sums.items()), out2[-400:])
        report("update-image-confirmed", re.search(r"^slot=b$", out, re.M) is not None
               and re.search(r"^try=$", out, re.M) is not None and "SLOT=b" in out, out[-300:])

        # Without a terminal it asks for the token (like apply); with it, it goes back to the other slot
        out = serial_run(s, f"tok=$({sudo} osyr update image rollback --json | python3 -c "
                         f"'import json,sys; print(json.loads(sys.stdin.read().strip().splitlines()[-1]).get(\"confirm\"))'); "
                         f"{sudo} osyr update image rollback --confirm $tok; echo RC=$?", 600)
        ok = "RC=0" in out and reboot()
        out = serial_run(s, f"findmnt -no SOURCE /; {sudo} cat /var/lib/iwd/test.psk", 60) if ok else s.text()
        report("update-image-rollback", ok and re.search(r"^/dev/vda2$", out, re.M) is not None
               and "test-psk" in out, out[-300:])
        if not ok:
            return

        # A new version that does not boot (no kernel): GRUB boots the previous one and does not try it again.
        # This time with the whole image (manifest without the small one, like those of v0.27)
        manifest(False)
        out = apply("update-image-apply2")
        report("update-image-full", "RC=0" in out and "Copying" not in out
               and re.search(r"Downloading OSYR v99.0.0-test \([3-9]\.[0-9] GB", out) is not None, out[-300:])
        if "RC=0" not in out:
            report("update-image-fallback", False, out[-300:])
            return
        serial_run(s, f"{sudo} mount /dev/vda3 /mnt && {sudo} rm /mnt/boot/bzImage; {sudo} umount /mnt", 60)
        ok = reboot()
        out = serial_run(s, f"findmnt -no SOURCE /; {sudo} mount /dev/vda1 /mnt; head -3 /mnt/osyr/grub/grubenv; "
                            f"{sudo} umount /mnt", 60) if ok else s.text()
        open(f"{RESULTS}/update-image-fallback.txt", "w").write(out)
        report("update-image-fallback", ok and re.search(r"^/dev/vda2$", out, re.M) is not None
               and re.search(r"^slot=a$", out, re.M) is not None and re.search(r"^try=$", out, re.M) is not None,
               out[-300:])
    finally:
        if q and q.poll() is None:
            q.kill()
        http.kill()


def boot_check(path, fmt):
    """Boots a publishable disk (e.g. the .ova's VMDK) in BIOS and in UEFI up to
    logging in as osyr: used by scripts/publish.sh before publishing."""
    print(f"== boot: {path} ({fmt})", flush=True)
    tmp = tempfile.mkdtemp(prefix="osyr-boot-", dir=os.path.join(ROOT, "build"))
    try:
        for uefi in (False, True):
            name = "uefi" if uefi else "bios"
            q, s = qemu_boot(tmp, [(path, True)], uefi=uefi, name=f"boot-{name}", fmt=fmt)
            ok, _ = serial_login(s, q)
            if ok:
                out = serial_run(s, "cat /etc/osyr/build-info")
                ok = "COMMIT=" in out
            report(f"boot-{name}", ok, s.text()[-300:])
            q.kill()
            q.wait()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def audio_has_sound(path):
    """Is there sound in what QEMU recorded from the sound card (osyr audio test, aplay)?"""
    try:
        with wave.open(path) as w:
            n, width, ch = w.getnframes(), w.getsampwidth(), w.getnchannels()
            data = w.readframes(n)
    except (OSError, wave.Error):
        return False
    if width != 2 or not data:
        return False
    samples = struct.unpack(f"<{len(data) // 2}h", data)
    loud = sum(1 for x in samples if abs(x) > 1000)
    return loud > 0.01 * len(samples) / ch * 0.1


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    os.makedirs(RESULTS, exist_ok=True)
    if which == "boot":  # tests/run.py boot DISK [format]
        boot_check(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "raw")
    if which in ("native", "all"):
        native()
    if which in ("vm", "all"):
        vm()
    if which in ("install", "all"):
        install()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed. Details in {RESULTS}/")
    for name, _, detail in failed:
        print(f"  FAIL {name}: {detail}")
    return 1 if failed or not results else 0


if __name__ == "__main__":
    sys.exit(main())
