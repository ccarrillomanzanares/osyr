"""osyr shellguard — classifies a shell command the agent asks for.

Design in docs/SHELL-DESIGN.md. Used by the shell tool of osyr-chat.

classify(command, cwd, home) returns a dict:
  class   "pass"     only reads: run without asking
          "confirm"  changes something, or cannot be analysed: ask the user
          "block"    never run it
  code    for a block: BLOCKED (dangerous), INTERACTIVE (needs a keyboard) or
          USE_OSYR_TOOL (osyr commands go through the osyr tool)
  reason  one line in English, for the model and for the dim line the user sees
  reads   files of the user's folder whose content would reach the model (the
          chat asks before sending them to a remote provider)
  root    True when some part of it runs with sudo

rule(command, cwd, home) gives the permission a "yes, always" would save for a
command, or None when it must ask every time.

effect(command, cwd, home) gives one plain line for the question of a heavy
command ("This deletes 214 files (1.2 GB) in ~/Downloads"), or None.

It guards against the model's mistakes; it is not a security boundary. A list of
patterns can be dodged: the shell runs as the user, root needs sudo and the
user's password, and the base system is A/B. Anything it does not understand
is "confirm", never "pass".
"""
import glob
import os
import re
import shlex
import time

PASS, CONFIRM, BLOCK = "pass", "confirm", "block"
ORDER = {PASS: 0, CONFIRM: 1, BLOCK: 2}

# --- Zones of the file system ------------------------------------------------------------
# user: the person's files and removable media (changes ask)
# config: system configuration and state (single files ask; whole trees are blocked)
# base: the system itself (changes are blocked)
USER_ROOTS = ("/tmp", "/var/tmp", "/run/media", "/mnt", "/media")
CONFIG_ROOTS = ("/etc", "/var", "/proc", "/sys", "/run", "/srv", "/home")
# Files nobody edits by hand, whatever the zone
PROTECTED = re.compile(
    r"^/etc/(sudoers(\.d(/.*)?)?|shadow-?|gshadow-?|passwd-?|group-?|fstab|crypttab|machine-id"
    r"|osyr/update-signers|osyr/update\.conf|pam\.d(/.*)?|security(/.*)?|ld\.so\.(conf|preload).*)$"
    r"|^/(boot|efi)(/.*)?$|^/var/lib/osyr(/.*)?$")
# Content that must not reach the conversation (checked on the raw text and on resolved paths)
SECRET = re.compile(
    r"/etc/g?shadow-?\b|\.ssh/(id_[A-Za-z0-9_]+|[^/\s]*_key)(?!\.pub)\b|\.ssh/identity\b"
    r"|/var/lib/iwd\b|/etc/NetworkManager/system-connections|wpa_supplicant[^/\s]*\.conf"
    r"|\.gnupg/(private-keys|secring|openpgp-revocs)|\.config/osyr/(ai|permissions)\.json|\.git-credentials|\.netrc\b"
    r"|\.mozilla/[^\s]*(logins\.json|key4\.db|cookies\.sqlite)|\.password-store|\.aws/credentials"
    r"|/etc/ssh/ssh_host_[a-z0-9_]+_key(?!\.pub)\b|\.local/share/keyrings|\.kdbx\b"
    r"|/proc/[^/\s]+/environ\b|/proc/kcore\b|/dev/(mem|kmem|port)\b"
    r"|\.(bash|zsh|python|mysql|psql|node_repl)_history\b|\.lesshst\b")
# Folders whose files are secret unless they are one of the public ones
SECRET_DIRS = re.compile(r"(^|/)(\.ssh|\.gnupg|\.password-store|\.local/share/keyrings)(/|$)|^/var/lib/iwd(/|$)"
                         r"|/\.mozilla/firefox/[^/]+(/|$)|^/etc/ssh(/|$)")
PUBLIC_IN_SECRET_DIRS = re.compile(r"(\.pub|/known_hosts|/config|/authorized_keys|/sshd?_config|/moduli"
                                   r"|/profiles\.ini|/gpg\.conf|/prefs\.js)$")
SAFE_ENV = re.compile(r"^(LC_[A-Z]+|LANG|LANGUAGE|TZ|COLUMNS|LINES|NO_COLOR|TERM)=")
SAFE_SED = re.compile(r"^(\d+|\$)?(,(\d+|\$))?[pdq]$|^/[^/]*/[pd]$|^s([/|#,@])[^\n]*\4[^\n]*\4[gIi0-9p]*$")
# The agent's own guard rails and audit trail
OWN = re.compile(r"\.local/state/osyr/(chat|shell)\.log|/usr/(bin/osyr[^/\s]*|lib/osyr)\b|osyr/shellguard")
DISK_DEV = re.compile(r"^/dev/(sd|hd|vd|xvd|nvme|mmcblk|dm-|md|loop|sr|mapper/|disk/|zram)")
HARMLESS_DEV = ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty", "/dev/zero", "/dev/urandom", "/dev/random")

RAW_BLOCK = [
    (re.compile(r":\s*\(\s*\)\s*\{[^}]*\|[^}]*&[^}]*\}"), "it is a fork bomb"),
    (re.compile(r"\b(curl|wget|fetch)\b[^;&]*\|\s*(sudo(\s+-\S+)*\s+)?(env\s+)?(ba|z|da|a|k|fi)?sh\b"),
     "it runs code straight from the network; install software with osyr ext or osyr box"),
    (re.compile(r"\b(curl|wget)\b[^;&]*\|\s*(sudo(\s+-\S+)*\s+)?(python[0-9.]*|perl|ruby|node)\b"),
     "it runs code straight from the network; install software with osyr ext or osyr box"),
    (re.compile(r"\b(ba|z|da)?sh\b[^;|&]*(\$\(|`|<\()\s*(curl|wget)\b"),
     "it runs code straight from the network; install software with osyr ext or osyr box"),
]
# In a command that cannot be analysed, these words are enough to block it
RAW_DISK = re.compile(r"\b(mkfs(\.\w+)?|mke2fs|wipefs|blkdiscard|sgdisk|sfdisk|fdisk|parted)\b|\bdd\b[^;|&]*\bof=/dev/")

SHELLS = {"sh", "bash", "zsh", "dash", "ash", "ksh", "fish", "csh", "tcsh"}
INTERPRETERS = {"python", "python3", "perl", "ruby", "node", "lua", "php"}
WRAPPERS = {"env", "nice", "ionice", "nohup", "time", "command", "stdbuf", "setsid", "chrt"}
CONTROL = {"for", "while", "until", "if", "case", "select", "function", "{", "}", "do", "done", "then",
           "fi", "esac", "[[", "!", "coproc"}
UNANALYSABLE_PROGS = {"eval", "exec", "xargs", "parallel", "source", ".", "trap", "watch", "script", "expect",
                      "busybox", "nsenter", "unshare", "chroot", "systemd-run", "at", "batch", "crontab"}
INTERACTIVE = {"vi", "vim", "nvim", "nano", "emacs", "pico", "ed", "top", "htop", "btop", "atop", "iotop",
               "mc", "ranger", "tmux", "screen", "ftp", "sftp", "telnet", "passwd", "chsh", "chfn", "visudo",
               "vipw", "vigr", "alsamixer", "nmtui", "cfdisk", "gdb", "mysql", "psql", "sqlite3", "bc", "dc"}
DISK_PROGS = {"mke2fs", "mkswap", "wipefs", "fdisk", "sfdisk", "parted", "sgdisk", "gdisk", "blkdiscard",
              "mkdosfs", "mkntfs", "pvcreate", "pvremove", "vgremove", "lvremove", "zpool", "badblocks"}
BOOT_PROGS = {"grub-install", "grub-mkconfig", "grub-editenv", "grub-reboot", "grub-set-default", "bootctl",
              "mkinitcpio", "dracut", "kexec", "flashrom", "fwupdmgr", "depmod"}
PRIV_PROGS = {"su", "visudo", "vipw", "vigr", "pkexec", "doas", "setcap", "newgrp", "sg"}
# Programs that write the content of some argument to a destination argument
WRITERS = {"cp", "mv", "install", "ln", "rsync", "scp", "tee", "touch", "mkdir", "truncate", "dd", "shred",
           "rm", "rmdir", "unlink", "chmod", "chown", "chgrp", "chattr", "setfacl", "sed", "patch", "tar",
           "unzip", "gzip", "gunzip", "xz", "bzip2", "zstd", "mknod", "mkfifo", "wget", "curl", "git"}
# Programs whose output is the content of the files given
CONTENT_READERS = {"cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "sed", "awk", "sort",
                   "uniq", "cut", "diff", "cmp", "strings", "xxd", "hexdump", "od", "base64", "nl", "tac",
                   "wc", "tr", "column", "fold", "paste", "join", "comm", "zcat", "xzcat", "bzcat", "zless",
                   "jq", "pdftotext"}

# Programs that only read, whatever their arguments (secrets and zones are checked before)
READERS = CONTENT_READERS - {"sed", "awk"} | {
    "ls", "dir", "tree", "stat", "file", "du", "df", "free", "uptime", "uname", "arch", "nproc", "id",
    "whoami", "groups", "date", "cal", "pwd", "echo", "printf", "true", "false", "test", "[", "cd", "ps",
    "pgrep", "pidof", "pstree", "lsblk", "lsusb", "lspci", "lscpu", "lsmod", "lsmem", "lsof", "blkid",
    "findmnt", "printenv", "which", "type", "whereis", "realpath", "readlink", "basename", "dirname",
    "md5sum", "sha1sum", "sha256sum", "sha512sum", "cksum", "b2sum", "ss", "netstat", "dig", "nslookup",
    "host", "getent", "w", "who", "last", "lastlog", "users", "locale", "man", "apropos", "whatis", "help",
    "vmstat", "iostat", "mpstat", "sensors", "upower", "acpi", "lslocks", "lsns", "lsipc", "ldd", "seq",
    "expr", "sleep", "tty", "zipinfo", "getfacl", "lsattr", "namei", "numfmt", "tput",
}
SYSTEMCTL_READ = {"status", "show", "cat", "list-units", "list-unit-files", "list-timers", "list-sockets",
                  "list-dependencies", "list-jobs", "list-machines", "is-active", "is-enabled", "is-failed",
                  "is-system-running", "get-default", "show-environment", "help"}
SYSTEMCTL_BLOCK = {"isolate", "emergency", "rescue", "kexec", "soft-reboot", "switch-root", "default",
                   "set-default", "mask"}
GIT_READ = {"status", "log", "diff", "show", "blame", "shortlog", "describe", "rev-parse", "ls-files",
            "ls-tree", "ls-remote", "cat-file", "grep", "reflog", "whatchanged", "version", "help"}
CTL_READ = {"status", "show", "list", "list-sessions", "list-users", "list-seats", "session-status",
            "user-status", "seat-status", "show-session", "show-user", "list-timezones", "list-locales",
            "list-keymaps", "timesync-status", "query", "statistics", "dns", "domain"}


def result(cls, reason="", code=None, reads=()):
    return {"class": cls, "code": code or ("BLOCKED" if cls == BLOCK else None), "reason": reason,
            "reads": list(reads), "root": False}


def block(reason, code="BLOCKED"):
    return result(BLOCK, reason, code)


def expand(arg, cwd, home):
    """Absolute path of an argument, with ~, $HOME and .. resolved; symlinks as written."""
    a = arg
    if a == "~" or a.startswith("~/"):
        a = home + a[1:]
    elif a == "~+" or a.startswith("~+/"):
        a = cwd + a[2:]
    elif a.startswith("~"):  # ~name: that user's home folder
        name, _, rest = a[1:].partition("/")
        a = (home if name in (os.path.basename(home), os.environ.get("USER", "")) else
             "/root" if name == "root" else "/home/" + name) + ("/" + rest if rest else "")
    a = a.replace("${HOME}", home).replace("$HOME", home)
    if not a.startswith("/"):
        a = os.path.join(cwd, a)
    return os.path.normpath(a)


def resolve(arg, cwd, home):
    """Absolute path of an argument, with ~, $HOME, .. and existing symlinks resolved."""
    a = expand(arg, cwd, home)
    # symlinks of the part that exists (so that ~/link-to-etc is seen as /etc)
    head, tail = a, []
    while head != "/" and not os.path.lexists(head):
        head, t = os.path.split(head)
        tail.insert(0, t)
    try:
        head = os.path.realpath(head)
    except OSError:
        pass
    return os.path.normpath(os.path.join(head, *tail))


def secret_path(arg, cwd, home, content=True):
    """Does this argument name a secret? content: the command reads or copies what is inside
    (then a whole secret folder counts too; listing its names does not)."""
    p = resolve(arg, cwd, home)
    found = [p] + ([os.path.realpath(x) for x in glob.glob(p)[:200]] if any(ch in p for ch in "*?[") else [])
    if not content:
        return False
    for f in found:
        if SECRET.search(f):
            return True
        if content and SECRET_DIRS.search(f) and not PUBLIC_IN_SECRET_DIRS.search(f):
            return True
    return False


def braces(word, limit=64):
    """bash brace expansion of the comma form: a{b,c}d -> abd, acd (bounded)."""
    m = re.search(r"\{([^{}]*,[^{}]*)\}", word)
    if not m:
        return [word]
    out = []
    for alt in m.group(1).split(","):
        out += braces(word[:m.start()] + alt + word[m.end():], limit)
        if len(out) > limit:
            break
    return out[:limit]


def under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def zone(path, home):
    if under(path, home) or any(under(path, r) for r in USER_ROOTS):
        return "user"
    if path.startswith("/dev/"):
        return "dev"
    if any(under(path, r) for r in CONFIG_ROOTS):
        return "config"
    return "base"


def is_glob_all(name):
    return name in ("*", ".*", ".[!.]*", "..?*", "{*,.*}", "{.,}*", ".??*")


def looks_like_path(arg):
    return arg.startswith(("/", "~", "./", "../", "$HOME", "${HOME}")) or "/" in arg


class Cmd:
    """One simple command: program, arguments, redirections."""

    def __init__(self):
        self.words, self.writes, self.reads_from = [], [], []
        self.root = False
        self.opaque = None  # why it cannot be analysed


def lex(command):
    """Splits into simple commands. Returns (commands, opaque reason or None, background)."""
    if "`" in command or "$(" in command or "<(" in command or ">(" in command:
        return [], "command substitution", False
    if "<<" in command:
        return [], "here-document", False
    try:
        lx = shlex.shlex(command, posix=True, punctuation_chars=True)
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        return [], "unbalanced quotes", False
    cmds, cur, background, i = [], Cmd(), False, 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t in ("|", "||", "&&", ";", "|&", ";;"):
            cmds.append(cur)
            cur = Cmd()
        elif t == "&":
            background = True
            cmds.append(cur)
            cur = Cmd()
        elif t in (">&", "<&", "&>", "&>>") and nxt is not None and (nxt.isdigit() or nxt == "-") and t in (">&", "<&"):
            i += 1  # 2>&1: a duplicated descriptor, not a file
        elif t in (">", ">>", ">|", "&>", "&>>", ">&"):
            if nxt is None:
                return [], "redirection without a target", False
            if nxt not in HARMLESS_DEV:
                cur.writes.append(nxt)
            i += 1
        elif t == "<":
            if nxt is None:
                return [], "redirection without a source", False
            cur.reads_from.append(nxt)
            i += 1
        elif t and set(t) <= set("();<>|&"):
            return [], "shell syntax it does not analyse", False
        else:
            # '2' in '2>/dev/null' arrives as a word of its own
            if t in ("0", "1", "2") and nxt in (">", ">>", ">&", "<", ">|"):
                pass
            else:
                cur.words.append(t)
        i += 1
    cmds.append(cur)
    return [c for c in cmds if c.words or c.writes or c.reads_from], None, background


def strip_wrappers(c):
    """Drops VAR=value, sudo and harmless wrappers from the front. Returns a block result or None."""
    w = c.words
    while w:
        head = os.path.basename(w[0])
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0]):
            if not SAFE_ENV.match(w[0]):
                c.opaque = "it sets a variable that can change what the program does"
            w = w[1:]
        elif head == "sudo":
            c.root = True
            w = w[1:]
            while w and w[0].startswith("-"):
                f = w[0]
                if f in ("-s", "-i", "--shell", "--login", "-e", "--edit") or (
                        not f.startswith("--") and set(f[1:]) & set("sie")):
                    return block("it opens a root shell or editor; ask for the exact command with sudo instead")
                takes = f in ("-u", "-g", "-U", "-h", "-p", "-C", "-D", "-R", "-T", "--user", "--group")
                w = w[2:] if takes else w[1:]
            if not w:
                c.words = ["sudo"]
                return None
        elif head == "timeout":
            w = w[1:]
            while w and w[0].startswith("-"):
                w = w[2:] if w[0] in ("-s", "-k", "--signal", "--kill-after") else w[1:]
            w = w[1:]  # the duration
        elif head in WRAPPERS:
            w = w[1:]
            while w and (w[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w[0])):
                if head in ("nice", "ionice", "chrt", "stdbuf") and w[0] in ("-n", "-c", "-p", "-i", "-o", "-e"):
                    w = w[1:]
                w = w[1:]
        else:
            break
    c.words = w
    return None


def args_of(c):
    """(options, operands) of the command, after its program name."""
    opts, ops, only_ops = [], [], False
    for a in c.words[1:]:
        if only_ops or not a.startswith("-") or a == "-":
            ops.append(a)
        elif a == "--":
            only_ops = True
        else:
            opts.append(a)
    return opts, ops


def has_flag(opts, short="", longs=()):
    for o in opts:
        if o.startswith("--"):
            if o.split("=", 1)[0] in longs:
                return True
        elif any(ch in o[1:] for ch in short):
            return True
    return False


def check_write(path_arg, cwd, home, root, recursive=False, delete=False, what="writes to"):
    """Block result for changing this path, or None when it only needs confirmation."""
    p = resolve(path_arg, cwd, home)
    base = os.path.basename(p)
    if p in HARMLESS_DEV:
        return None
    if SECRET.search(p) or SECRET.search(path_arg):
        return block("it touches a file that holds passwords or keys")
    if OWN.search(p):
        return block("it changes the assistant's own programs or its log")
    if PROTECTED.search(p):
        return block(f"{p} is not edited by hand: it can lock you out or stop the system from starting")
    z = zone(p, home)
    if z == "dev":
        if DISK_DEV.search(p):
            return block(f"it {what} the disk {p} directly and would destroy what is on it")
        return block(f"it {what} the device {p}")
    if z == "base":
        return block(f"it {what} {p}, part of the system itself; the system is replaced as a whole by updates")
    if is_glob_all(base) and delete:
        parent = os.path.dirname(p)
        if parent == home or zone(parent, home) in ("base", "dev") or (
                zone(parent, home) == "config" and len([x for x in parent.split("/") if x]) <= 2):
            return block(f"it would delete everything in {parent}")
    if z == "user":
        if delete and p == home:
            return block("it would delete your whole home folder")
        if delete and p in USER_ROOTS:
            return block(f"it would delete everything in {p}")
        if under(home, p) and p != home and (delete or recursive):
            return block(f"it would affect every user's files ({p})")
        return None
    # config zone: one file is an administrator's job; whole trees are not
    depth = len([x for x in p.split("/") if x])
    if p in CONFIG_ROOTS or ((recursive or delete) and depth <= 2 and (recursive or os.path.isdir(p))):
        return block(f"it would change all of {p}")
    if delete and recursive and depth <= 3 and os.path.isdir(p) and p.startswith("/etc/"):
        return block(f"it would delete the configuration in {p}")
    return None


def classify_simple(c, cwd, home):
    """One simple command, wrappers included."""
    r = strip_wrappers(c)
    if r:
        return r
    c.words = c.words[:1] + [x for a in c.words[1:] for x in braces(a)]
    if not c.words:  # only redirections: '> file' empties it
        for t in c.writes:
            r = check_write(t, cwd, home, c.root)
            if r:
                return r
        if c.writes or c.root or c.opaque:
            return result(CONFIRM, "it writes to a file" if c.writes else c.opaque or "it runs as administrator")
        return result(PASS)
    prog = os.path.basename(c.words[0])
    opts, ops = args_of(c)
    raw = " ".join(c.words)

    if "$" in c.words[0] or prog in CONTROL or prog in UNANALYSABLE_PROGS:
        c.opaque = c.opaque or f"'{prog}' runs other commands that are not analysed"
    # A shell or interpreter with -c: what counts is the command inside
    if prog in SHELLS:
        if "-c" in c.words[1:] or any(o.startswith("-") and not o.startswith("--") and "c" in o for o in opts):
            idx = next(i for i, a in enumerate(c.words) if a == "-c" or (a.startswith("-") and "c" in a and i > 0))
            inner = c.words[idx + 1] if idx + 1 < len(c.words) else ""
            r = classify(inner, cwd, home, _root=c.root)
            if r["class"] == BLOCK:
                return r
            return result(CONFIRM, "it runs a command inside a shell", reads=r["reads"])
        if not ops:
            if c.root:
                return block("it opens a root shell; ask for the exact command with sudo instead")
            return block("a shell without a command needs a keyboard", "INTERACTIVE")
        return result(CONFIRM, "it runs a script")
    if prog == "osyr":
        return block("osyr commands go through the osyr tool, not the shell", "USE_OSYR_TOOL")
    if prog in PRIV_PROGS or (c.root and prog in ("su", "login")):
        return block("it changes who you are or what programs are allowed to do; "
                     "ask for the exact command with sudo instead")
    if prog in INTERACTIVE and not (prog in ("top", "htop") and has_flag(opts, "b")) and not (
            prog == "passwd" and has_flag(opts, "S", ("--status",))):
        return block(f"'{prog}' needs a keyboard; use a command that does the same without one", "INTERACTIVE")
    if prog in INTERPRETERS and not ops and not has_flag(opts, "ce", ("--version", "--help")):
        return block(f"'{prog}' without a script needs a keyboard", "INTERACTIVE")

    # Secrets: any path argument, even only read
    lists_names = prog in ("ls", "dir", "tree", "stat", "file", "du", "find", "cd", "test", "[", "realpath",
                           "readlink", "namei", "getfacl", "lsattr", "mkdir", "chmod", "chown")
    # (creating a key or using one to connect does not show it)
    uses_keys = prog in ("ssh-keygen", "ssh-add", "ssh", "ssh-copy-id")
    for a in ops + c.reads_from + [o.split("=", 1)[1] for o in opts if "=" in o]:
        if uses_keys:
            break
        if looks_like_path(a) or any(ch in a for ch in "*?[") or os.path.lexists(os.path.join(cwd, a)):
            if secret_path(a, cwd, home, content=not lists_names):
                return block("it reads a file that holds passwords or keys; they must not end up in the conversation")
    if SECRET.search(raw) and not lists_names and not uses_keys:
        return block("it reads a file that holds passwords or keys; they must not end up in the conversation")

    # Disks, boot, sessions
    if prog.startswith("mkfs") or prog in DISK_PROGS:
        if prog == "badblocks" and not has_flag(opts, "wn"):
            return result(CONFIRM, "it scans a disk")
        if prog in ("fdisk", "sfdisk", "parted", "sgdisk", "gdisk") and (
                has_flag(opts, "lpJ", ("--list", "--print", "--json", "--dump")) or "print" in ops):
            return result(CONFIRM if c.root else PASS, "it lists partitions")
        return block("it formats or repartitions a disk and destroys what is on it; "
                     "for a USB stick use the osyr disk commands")
    if prog == "cryptsetup" and set(ops) & {"luksFormat", "erase", "luksErase", "reencrypt", "luksKillSlot",
                                              "luksRemoveKey", "luksAddKey", "luksChangeKey"}:
        return block("it changes or destroys disk encryption")
    if prog in BOOT_PROGS or (prog == "efibootmgr" and (opts and not set(opts) <= {"-v", "--verbose"})):
        return block("it changes how the computer starts; a mistake leaves it unable to boot")
    if prog == "dd":
        for a in c.words[1:]:
            if a.startswith("of="):
                r = check_write(a[3:], cwd, home, c.root)
                if r:
                    return r
    if prog == "hdparm" and any("security" in o or o in ("--trim-sector-ranges", "--make-bad-sector") for o in opts):
        return block("it can erase or lock a disk")
    if prog in ("kill", "pkill", "killall", "killall5", "skill"):
        if prog == "killall5" or (prog == "kill" and ("1" in ops or "-1" in c.words[2:] or c.words[1:] == ["-1"])):
            return block("it would kill every process, or the system's first one")
        if re.search(r"\bosyr|\bsway\b", raw) and prog != "kill":
            return block("it would close the assistant or the desktop session")
        if has_flag(opts, "l", ("--list", "--table")):
            return result(PASS)
        return result(CONFIRM, "it stops a process")
    if prog in ("systemctl", "service"):
        verb = next((o for o in ops), "")
        if prog == "systemctl" and (verb in SYSTEMCTL_READ or not verb) and not c.root:
            return result(PASS)
        if verb in SYSTEMCTL_BLOCK:
            return block(f"'systemctl {verb}' can leave the system without a session or a service for good")
        return result(CONFIRM, "it changes a service or the system state")
    if prog in ("init", "telinit"):
        return block("it changes the system's run level")
    if prog in ("chmod", "chown", "chgrp") and prog == "chmod" and ops and (
            re.match(r"^[0-7]?[4-7][0-7]{3}$", ops[0]) or re.search(r"[ug]?\+[rwx]*s", ops[0])):
        return block("it makes a program run with its owner's privileges")
    if prog in ("iptables", "ip6tables", "nft") and (has_flag(opts, "FX", ("--flush",)) or "flush" in ops):
        return result(CONFIRM, "it clears the firewall rules")

    # Writes: every destination is checked against the zones
    recursive = has_flag(opts, "rR", ("--recursive",))
    if prog in ("rm", "rmdir", "unlink", "shred", "truncate"):
        for a in ops:
            r = check_write(a, cwd, home, c.root, recursive=recursive, delete=True, what="deletes")
            if r:
                return r
        return result(CONFIRM, "it deletes files")
    if prog in ("chmod", "chown", "chgrp", "chattr", "setfacl"):
        for a in ops[1:]:
            r = check_write(a, cwd, home, c.root, recursive=recursive, what="changes the permissions of")
            if r:
                return r
            if recursive and resolve(a, cwd, home) == home and prog != "chmod":
                return block("it would change the owner of your whole home folder")
        return result(CONFIRM, "it changes permissions")
    if prog in ("mv",):
        for a in ops[:-1] if len(ops) > 1 else ops:  # the sources disappear from where they were
            r = check_write(a, cwd, home, c.root, recursive=True, delete=True, what="moves away")
            if r:
                return r
    if prog in WRITERS and prog not in ("sed", "git", "curl", "wget", "tar", "unzip"):
        dests = ops[-1:] if prog in ("cp", "mv", "install", "ln", "rsync", "scp") else ops
        t = next((o.split("=", 1)[1] for o in opts if o.startswith("--target-directory=")), None)
        for a in dests + ([t] if t else []):
            if ":" in a and prog in ("rsync", "scp") and not a.startswith("/"):
                continue  # another machine
            r = check_write(a, cwd, home, c.root, recursive=recursive)
            if r:
                return r
        return result(CONFIRM, "it creates or changes files")
    for t in c.writes:
        r = check_write(t, cwd, home, c.root)
        if r:
            return r
    for o in opts:  # -o FILE / --output=FILE of curl, wget, sort, tar -C...
        if "=" in o and o.split("=", 1)[0] in ("--output", "--output-document", "--directory", "--file"):
            r = check_write(o.split("=", 1)[1], cwd, home, c.root)
            if r:
                return r
    if prog in ("curl", "wget", "tar", "unzip", "git", "sed", "sort") and c.root:
        for i, a in enumerate(c.words[1:-1], 1):
            if a in ("-o", "-O", "-C", "-d", "-P", "--output", "--directory", "--directory-prefix"):
                r = check_write(c.words[i + 1], cwd, home, c.root)
                if r:
                    return r
    if prog in ("sed",) and has_flag(opts, "i", ("--in-place",)):
        for a in ops[1:] or ops:
            r = check_write(a, cwd, home, c.root)
            if r:
                return r
    if prog in ("mount", "umount", "swapon", "swapoff", "losetup") and (ops or opts):
        if prog == "mount" and has_flag(opts, "lhV", ("--help", "--version")) and not ops:
            return result(PASS)
        for a in ops[-1:]:
            p = resolve(a, cwd, home)
            if prog in ("mount", "umount") and zone(p, home) == "base" and p not in ("/mnt",):
                return block(f"it mounts or unmounts {p}, part of the system itself")
        return result(CONFIRM, "it mounts or unmounts a disk")

    # From here on nothing is blocked: pass if it certainly only reads
    if c.opaque or c.root or c.writes:
        return result(CONFIRM, c.opaque or ("it runs as administrator" if c.root else "it writes to a file"))
    # (in the script of awk, sed or grep a $ is not a shell variable)
    if any("$" in a for a in c.words[1:]) and prog not in ("awk", "gawk", "mawk", "sed", "grep", "egrep",
                                                           "fgrep", "rg", "jq"):
        return result(CONFIRM, "it uses a variable whose value is not known")
    reads = []
    if prog in CONTENT_READERS:
        start = 1 if prog in ("grep", "egrep", "fgrep", "rg", "sed", "awk", "jq", "tr") and ops else 0
        for a in ops[start:] + c.reads_from:
            p = resolve(a, cwd, home)
            if under(p, home) and not os.path.isdir(p):
                reads.append(p)
    if prog in READERS:
        if prog == "ping" or (prog == "sleep" and ops and not re.match(r"^\d{1,2}(\.\d+)?s?$", ops[0])):
            return result(CONFIRM, "it may not finish by itself")
        if prog in ("tail", "less", "more") and has_flag(opts, "fF", ("--follow",)):
            return block("following a file never ends; read the last lines with tail -n instead", "INTERACTIVE")
        if prog in ("less", "more", "man", "zless") and (opts or any(a.startswith("+") for a in ops)):
            return result(CONFIRM, "a pager with options can run other programs")
        if prog in CONTENT_READERS and any(resolve(a, cwd, home).startswith("/dev/")
                                           and resolve(a, cwd, home) not in HARMLESS_DEV for a in ops):
            return result(CONFIRM, "it reads a device directly")
        if prog == "sort" and has_flag(opts, "o", ("--output",)):
            return result(CONFIRM, "it writes to a file")
        if prog == "date" and (has_flag(opts, "s", ("--set",)) or any(re.match(r"^\d{8,}", a) for a in ops)):
            return result(CONFIRM, "it sets the clock")
        return result(PASS, reads=reads)
    if prog == "find":
        if set(c.words) & {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls", "-fprint0"}:
            return result(CONFIRM, "find changes or runs things with these options")
        return result(PASS)
    if prog == "sed":
        script = ops[0] if ops else ""
        if has_flag(opts, "ifes", ("--in-place", "--file", "--expression")) or not SAFE_SED.match(script):
            return result(CONFIRM, "sed may write or run things with this script")
        return result(PASS, reads=reads)
    if prog in ("awk", "gawk", "mawk"):
        script = next((a for a in ops), "")
        if has_flag(opts, "fi", ("--file", "--include")) or re.search(r"system\s*\(|>|\||getline|close\s*\(", script):
            return result(CONFIRM, "awk may write or run things with this script")
        return result(PASS, reads=reads)
    if prog == "ping":
        if has_flag(opts, "c", ("--count",)) and not has_flag(opts, "f"):
            return result(PASS)
        return result(CONFIRM, "ping without a count does not finish by itself")
    if prog == "journalctl":
        if any(o.split("=", 1)[0] in ("--rotate", "--flush", "--sync", "--setup-keys", "--relinquish-var",
                                       "--update-catalog") or o.startswith("--vacuum") for o in opts):
            return result(CONFIRM, "it changes the system journal")
        if has_flag(opts, "f", ("--follow",)):
            return block("following the journal never ends; read the last lines with -n instead", "INTERACTIVE")
        return result(PASS)
    if prog == "dmesg":
        if has_flag(opts, "cCDEnw", ("--clear", "--read-clear", "--console-off", "--console-on",
                                     "--console-level", "--follow", "--follow-new")):
            return result(CONFIRM, "it changes the kernel log or does not finish by itself")
        return result(PASS)
    if prog == "ip":
        words = [a for a in ops]
        verbs = {"add", "del", "delete", "set", "flush", "change", "replace", "append", "up", "down", "netns",
                 "exec", "monitor"}
        if has_flag(opts, "bf", ("-batch", "-force")) or set(words) & verbs:
            return result(CONFIRM, "it changes the network configuration")
        return result(PASS)
    if prog in ("iw", "ethtool", "rfkill"):
        if set(ops) & {"set", "connect", "disconnect", "del", "add", "scan", "block", "unblock", "trigger"} or (
                prog == "ethtool" and opts and not has_flag(opts, "iSkagcTPm")):
            return result(CONFIRM, "it changes a network device")
        return result(PASS)
    if prog in ("loginctl", "timedatectl", "hostnamectl", "localectl", "resolvectl", "networkctl", "busctl",
                "bluetoothctl", "iwctl", "wpctl", "pactl", "coredumpctl", "systemd-analyze", "udevadm", "nmcli"):
        verb = next((o for o in ops), "")
        if prog in ("bluetoothctl", "iwctl") and not ops:
            return block(f"'{prog}' without a command needs a keyboard", "INTERACTIVE")
        if (not verb and prog not in ("busctl", "udevadm")) or verb in CTL_READ or (
                prog == "systemd-analyze" and verb in ("blame", "time", "critical-chain", "verify", "security", "")) or (
                prog == "udevadm" and verb == "info") or (prog in ("wpctl", "pactl") and verb in ("status", "info", "list", "inspect", "get-volume")) or (
                prog == "bluetoothctl" and verb in ("show", "devices", "info", "paired-devices")) or (
                prog == "iwctl" and ops[-1:] in (["list"], ["show"], ["get-networks"], ["get-connected-network"])) or (
                prog == "coredumpctl" and verb in ("list", "info")) or (prog == "networkctl" and verb in ("status", "list", "lldp")):
            return result(PASS)
        return result(CONFIRM, f"it changes a setting with {prog}")
    if prog == "git":
        verb = next((o for o in ops), "")
        if verb in GIT_READ and not has_flag(opts, "c", ("--output", "--exec-path")):
            return result(PASS)
        if verb == "stash" and ops[1:2] in (["list"], ["show"]):
            return result(PASS)
        if verb in ("branch", "tag", "remote", "config") and (
                len(ops) == 1 or ops[1:2] in (["list"], ["show"], ["get-url"], ["-v"])) \
                and not has_flag(opts, "dDmMcCf", ("--delete", "--move", "--copy", "--force", "--unset", "--add",
                                                    "--replace-all", "--edit", "--global", "--system")):
            return result(PASS)
        return result(CONFIRM, "it changes a git repository or talks to a server")
    if prog in ("tar", "bsdtar"):
        mode = "".join(o.lstrip("-") for o in c.words[1:2]) + "".join(o[1:] for o in opts if not o.startswith("--"))
        if ("t" in mode or "--list" in opts) and not set(mode) & set("xcru") and not {"--extract", "--create", "--delete"} & set(opts):
            return result(PASS)
        return result(CONFIRM, "it packs or unpacks files")
    if prog in ("unzip", "zip", "7z", "unrar"):
        if prog == "unzip" and has_flag(opts, "lZv") or (prog in ("7z", "unrar") and ops[:1] in (["l"], ["t"])):
            return result(PASS)
        return result(CONFIRM, "it packs or unpacks files")
    if prog in ("top", "htop") and has_flag(opts, "b"):
        return result(PASS if has_flag(opts, "n") else CONFIRM, "top in batch mode")
    if prog in ("mount", "swapon", "losetup", "hostname", "alias", "ulimit", "umask", "jobs") and not ops and not opts:
        return result(PASS)
    if prog in ("efibootmgr", "lsinitrd", "uptime", "hostname") and not ops:
        return result(PASS)
    if (has_flag(opts, "", ("--version", "--help")) or opts == ["-V"]) and not ops:
        return result(PASS)
    return result(CONFIRM, f"'{prog}' is not known to be read-only")


def classify(command, cwd=None, home=None, _root=False):
    """Class of a whole command line. See the module description."""
    home = os.path.normpath(home or os.path.expanduser("~"))
    cwd = os.path.normpath(cwd or home)
    command = command.strip()
    if not command:
        return result(PASS)
    if "\x00" in command or len(command) > 4000:
        return block("the command is malformed or too long")
    for rx, why in RAW_BLOCK:
        if rx.search(command):
            return block(why)
    cmds, opaque, background = lex(command)
    if not opaque and any(c.words and os.path.basename(c.words[0]) in CONTROL for c in cmds):
        opaque = "loops and conditions"
    variables = "$" in command.replace("${HOME}", "").replace("$HOME", "")
    if (opaque or variables) and (SECRET.search(command) or SECRET_DIRS.search(command.replace("~", home))):
        # (what a variable or a substitution ends up reading cannot be followed)
        return block("it may read a file that holds passwords or keys; name the file directly, "
                     "and never one with passwords or keys")
    if opaque:
        if RAW_DISK.search(command):
            return block("it formats, repartitions or overwrites a disk")
        if re.search(r"\bsudo\s+(-[sie]\b|su\b|(ba|z|da)?sh\s*($|[;&|]))", command):
            return block("it opens a root shell; ask for the exact command with sudo instead")
        if re.search(r"\brm\s+(-[A-Za-z]*\s+)*(/|/\*|~|~/\*|\$HOME|\$HOME/\*)(\s|$|;|&|\))", command):
            return block("it would delete the whole system or your whole home folder")
        r = result(CONFIRM, f"it cannot be analysed ({opaque})")
        r["root"] = bool(re.search(r"\bsudo\b", command))
        return r
    worst, reads, root = result(PASS), [], False
    for c in cmds:
        c.root = c.root or _root
        r = classify_simple(c, cwd, home)
        if r["class"] == BLOCK:
            return r
        reads += r["reads"]
        root = root or c.root
        if ORDER[r["class"]] > ORDER[worst["class"]]:
            worst = r
        # cd changes where the next command of the line runs
        if c.words and os.path.basename(c.words[0]) == "cd":
            target = c.words[1] if len(c.words) > 1 and c.words[1] != "-" else home
            cwd = resolve(target, cwd, home)
    if background and worst["class"] == PASS:
        worst = result(CONFIRM, "it leaves a process running in the background")
    worst["reads"] = sorted(set(reads))
    worst["root"] = root
    return worst


# --- Permissions: what a "yes, always" covers --------------------------------------------

# Programs whose first word after the name says what they do ('git commit', 'npm install')
SUBCOMMANDS = {"git", "npm", "pnpm", "yarn", "pip", "pip3", "cargo", "go", "make", "docker", "podman",
               "flatpak", "systemctl", "tar", "zip", "unzip"}


def rule(command, cwd=None, home=None):
    """The permission a "yes, always" would save for this command: program, its
    subcommand and the folder it works in. None when it must ask every time:
    administrator commands, what cannot be analysed, lines that change things
    with more than one program, and anything outside a folder of the user's
    (the home folder itself is too wide to be one)."""
    home = os.path.normpath(home or os.path.expanduser("~"))
    cwd = os.path.normpath(cwd or home)
    cmds, opaque, background = lex(command.strip())
    if opaque or background or not cmds:
        return None
    if any(c.words and os.path.basename(c.words[0]) in CONTROL for c in cmds):
        return None
    changing = []
    for c in cmds:
        r = classify_simple(c, cwd, home)
        if r["class"] == BLOCK or c.root or c.opaque or not c.words:
            return None
        if r["class"] == CONFIRM:
            changing.append((c, cwd))
        if os.path.basename(c.words[0]) == "cd":
            cwd = resolve(c.words[1] if len(c.words) > 1 and c.words[1] != "-" else home, cwd, home)
    if len(changing) != 1:
        return None
    c, where = changing[0]
    prog = os.path.basename(c.words[0])
    if prog in SHELLS or prog in UNANALYSABLE_PROGS or prog == "cd":
        return None
    opts, ops = args_of(c)
    sub = ""
    if prog in SUBCOMMANDS:
        if not ops or not re.match(r"^[a-z][a-z0-9-]*$", ops[0]):
            return None
        sub, ops = ops[0], ops[1:]
    # The folder of the rule holds every file the command names; programs that
    # work on "where they are" (git, make, an interpreter) count that folder too
    paths = [resolve(a, where, home) for a in ops + c.writes if "://" not in a]
    folders = [os.path.dirname(q) or "/" for q in paths]
    if not folders or prog in SUBCOMMANDS or prog in INTERPRETERS:
        folders.append(where)
    folder = os.path.commonpath(folders)
    if zone(folder, home) != "user" or under(home, folder) or folder in USER_ROOTS:
        return None
    if SECRET_DIRS.search(folder + "/") or under(folder, os.path.join(home, ".config", "osyr")):
        return None
    return {"program": prog, "sub": sub, "dir": folder}


def rule_text(r, home=None):
    home = os.path.normpath(home or os.path.expanduser("~"))
    d = "~" + r["dir"][len(home):] if under(r["dir"], home) else r["dir"]
    return f"'{(r['program'] + ' ' + r['sub']).strip()}' in {d}"


def allowed_by(rules, command, cwd=None, home=None):
    """True when a saved rule covers this command (same program and subcommand,
    inside the rule's folder)."""
    r = rule(command, cwd, home)
    return bool(r) and any(x.get("program") == r["program"] and x.get("sub", "") == r["sub"]
                           and x.get("dir") and under(r["dir"], x["dir"]) for x in rules)


# --- The plain line for heavy commands ----------------------------------------------------

COUNT_LIMIT, COUNT_SECONDS = 200000, 2.0
# unit (without .service): (what it is, what happens without it)
UNITS = {
    "iwd": ("the wifi", "the wireless connection drops"),
    "systemd-networkd": ("the network", "wired and wireless connections stop working"),
    "systemd-resolved": ("name resolution", "web addresses stop working"),
    "bluetooth": ("Bluetooth", "devices connected by it stop working"),
    "osyr-llama": ("the local model", "the assistant cannot answer without it"),
    "osyr-firewall": ("the firewall", "the computer accepts connections from the network"),
    "nftables": ("the firewall", "the computer accepts connections from the network"),
    "pipewire": ("the sound", "nothing plays or records"),
    "pipewire-pulse": ("the sound", "some programs lose it"),
    "wireplumber": ("the sound", "nothing plays or records"),
}
POWER_OFF = "This turns the computer off: anything not saved is lost"
POWER_RESTART = "This restarts the computer: anything not saved is lost"


def short(path, home):
    return "~" + path[len(home):] if under(path, home) else path


def size_text(n):
    if n < 1000:
        return f"{n} bytes"
    for unit in ("kB", "MB", "GB", "TB"):
        n /= 1000
        if n < 1000 or unit == "TB":
            return f"{n:.1f} {unit}" if n < 10 else f"{n:.0f} {unit}"


def delete_effect(opts, ops, cwd, home):
    """What an rm takes with it, when the command line does not show it: folders and patterns."""
    recursive = has_flag(opts, "rR", ("--recursive",))
    targets, hidden = [], False
    for a in ops:
        p = expand(a, cwd, home)
        if any(ch in a for ch in "*?["):
            hidden = True
            targets += sorted(glob.glob(p))
        else:
            targets.append(p)
    targets = list(dict.fromkeys(targets))
    files = size = folders = 0
    partial = False  # folders that could not be read: the real figure is higher
    stop = time.monotonic() + COUNT_SECONDS

    def note(e):
        nonlocal partial
        partial = True

    for t in targets:
        try:
            st = os.lstat(t)
        except OSError:
            continue
        if not os.path.isdir(t) or os.path.islink(t):
            files, size = files + 1, size + st.st_size
            continue
        if not recursive:
            continue  # rm without -r leaves folders alone
        hidden = True
        folders += 1
        for where, dirs, names in os.walk(t, onerror=note):
            folders += len(dirs)
            for n in names + [d for d in dirs if os.path.islink(os.path.join(where, d))]:
                try:
                    size += os.lstat(os.path.join(where, n)).st_size
                    files += 1
                except OSError:
                    partial = True
            if files > COUNT_LIMIT or time.monotonic() > stop:
                return f"This deletes more than {files:,} files in {short(t, home)}"
    if not hidden or not (files or folders):
        return None
    parents = [t if os.path.isdir(t) and not os.path.islink(t) and len(targets) == 1 else os.path.dirname(t)
               for t in targets if os.path.lexists(t)]
    where = short(os.path.commonpath(parents), home)
    if not files:
        if folders == 1:
            return f"This deletes an empty folder: {where}"
        return f"This deletes only empty folders in {where}"
    return (f"This deletes {'at least ' if partial else ''}{files:,} file{'s' if files != 1 else ''} "
            f"({size_text(size)}) in {where}")


def service_effect(opts, ops):
    verb, units = (ops[0] if ops else ""), [re.sub(r"\.(service|socket)$", "", u) for u in ops[1:]]
    if verb in ("poweroff", "halt"):
        return POWER_OFF
    if verb in ("reboot", "kexec", "soft-reboot"):
        return POWER_RESTART
    known = [UNITS[u] for u in units if u in UNITS]
    if not known:
        return None
    thing, then = known[0]
    if verb in ("stop", "kill") or (verb in ("disable", "mask") and has_flag(opts, "", ("--now",))):
        return f"This stops {thing}: {then}"
    if verb in ("restart", "try-restart", "reload-or-restart"):
        return f"This restarts {thing}: it is off for a moment"
    if verb in ("disable", "mask"):
        return f"This keeps {thing} off from the next start: {then}"
    return None


def effect(command, cwd=None, home=None):
    """One plain line, for the person, about what a heavy command does; None for the rest.
    It is shown with the question and is computed here, never written by the model."""
    home = os.path.normpath(home or os.path.expanduser("~"))
    cwd = os.path.normpath(cwd or home)
    try:
        cmds, opaque, _ = lex(command.strip())
        if opaque:
            return None
        for c in cmds:
            if strip_wrappers(c) or not c.words:
                continue
            c.words = c.words[:1] + [x for a in c.words[1:] for x in braces(a)]
            prog = os.path.basename(c.words[0])
            opts, ops = args_of(c)
            line = None
            if prog == "rm":
                line = delete_effect(opts, ops, cwd, home)
            elif prog == "systemctl":
                line = service_effect(opts, ops)
            elif prog in ("poweroff", "halt"):
                line = POWER_OFF
            elif prog == "reboot":
                line = POWER_RESTART
            elif prog == "shutdown" and not has_flag(opts, "ck", ("--help", "--show")):
                line = POWER_RESTART if has_flag(opts, "r", ("--reboot",)) else POWER_OFF
            if line:
                return line
            if prog == "cd":
                cwd = resolve(c.words[1] if len(c.words) > 1 and c.words[1] != "-" else home, cwd, home)
    except Exception:  # the line is a courtesy: a mistake here must not stop the question
        return None
    return None
