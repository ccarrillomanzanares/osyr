"""Text input in the terminal: passwords with one * per character."""
import getpass
import os
import sys


def read_password(prompt):
    """Like getpass, but draws one * per character so typing is visible.
    Backspace deletes one and Ctrl+U the line; Ctrl+C still works (ISIG)."""
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return getpass.getpass(prompt)
    import codecs
    import termios
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[3] &= ~(termios.ECHO | termios.ICANON)
    new[6][termios.VMIN], new[6][termios.VTIME] = 1, 0
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    chars = []
    # Echo off before showing the prompt, and no TCSAFLUSH: whatever is typed
    # just as it appears (a slow computer, the serial console) is neither shown
    # in clear nor lost
    termios.tcsetattr(fd, termios.TCSANOW, new)
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        while True:
            b = os.read(fd, 1)
            if not b or b == b"\x04":
                if not chars:
                    raise EOFError
                continue
            if b in (b"\r", b"\n"):
                break
            if b in (b"\x7f", b"\x08"):
                if chars:
                    chars.pop()
                    sys.stdout.write("\b \b")
            elif b == b"\x15":
                sys.stdout.write("\b \b" * len(chars))
                chars.clear()
            elif b >= b" " or b[0] >= 0x80:
                for ch in dec.decode(b):
                    chars.append(ch)
                    sys.stdout.write("*")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        sys.stdout.write("\n")
        sys.stdout.flush()
    return "".join(chars)
