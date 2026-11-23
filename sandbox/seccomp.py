#!/usr/bin/env python3
"""Writes the sandbox sessions' system-call filter (seccomp, classic BPF, x86_64)
to standard output, for bwrap's --seccomp.

The container already has no privileges, no network and its own namespaces;
this closes kernel doors that nothing in a session needs: new namespaces,
mounting, tracing other processes, kernel keys, BPF, performance counters,
io_uring, modules, and sockets other than Unix and Internet ones (the session
has no network, but the chat talks to its relay on the container's loopback).
Everything else is allowed.
"""
import struct
import sys

ARCH_X86_64 = 0xC000003E
ALLOW, KILL = 0x7FFF0000, 0x80000000
EPERM, ENOSYS, EAFNOSUPPORT = 1, 38, 97

DENIED = {
    "ptrace": 101, "process_vm_readv": 310, "process_vm_writev": 311, "kcmp": 312, "pidfd_getfd": 438,
    "process_madvise": 440,
    "mount": 165, "umount2": 166, "pivot_root": 155, "chroot": 161, "open_tree": 428, "move_mount": 429,
    "fsopen": 430, "fsconfig": 431, "fsmount": 432, "fspick": 433, "mount_setattr": 442,
    "name_to_handle_at": 303, "open_by_handle_at": 304,
    "unshare": 272, "setns": 308,
    "swapon": 167, "swapoff": 168, "reboot": 169, "sethostname": 170, "setdomainname": 171,
    "init_module": 175, "delete_module": 176, "finit_module": 313, "kexec_load": 246, "kexec_file_load": 320,
    "acct": 163, "quotactl": 179, "quotactl_fd": 443, "nfsservctl": 180, "syslog": 103,
    "add_key": 248, "request_key": 249, "keyctl": 250,
    "bpf": 321, "perf_event_open": 298, "userfaultfd": 323, "lookup_dcookie": 212,
    "io_uring_setup": 425, "io_uring_enter": 426, "io_uring_register": 427,
    "personality": 135, "iopl": 172, "ioperm": 173, "modify_ldt": 154, "vhangup": 153,
}
CLONE, CLONE3, SOCKET, IOCTL = 56, 435, 41, 16
CLONE_NEW = 0x7E020080          # every CLONE_NEW* flag
TIOCSTI, TIOCLINUX = 0x5412, 0x541C
AF_UNIX, AF_INET, AF_INET6 = 1, 2, 10

LD, JEQ, JGE, JSET, RET = 0x20, 0x15, 0x35, 0x45, 0x06
NR, ARCH = 0, 4


def arg(n):
    return 16 + 8 * n   # its low 32 bits


def program():
    p = []
    add = lambda code, k, jt=0, jf=0: p.append(struct.pack("<HBBI", code, jt, jf, k))  # noqa: E731
    errno = lambda e: 0x00050000 | e  # noqa: E731
    add(LD, ARCH)
    add(JEQ, ARCH_X86_64, 1, 0)
    add(RET, KILL)
    add(LD, NR)
    add(JGE, 0x40000000, 0, 1)      # the x32 numbers: the same calls under other names
    add(RET, errno(EPERM))
    for nr in sorted(DENIED.values()):
        add(JEQ, nr, 0, 1)
        add(RET, errno(EPERM))
    add(JEQ, CLONE3, 0, 1)          # its flags are behind a pointer: the C library falls back to clone
    add(RET, errno(ENOSYS))
    add(JEQ, CLONE, 0, 4)
    add(LD, arg(0))
    add(JSET, CLONE_NEW, 0, 1)
    add(RET, errno(EPERM))
    add(RET, ALLOW)
    add(JEQ, SOCKET, 0, 6)
    add(LD, arg(0))
    add(JEQ, AF_UNIX, 2, 0)
    add(JEQ, AF_INET, 1, 0)
    add(JEQ, AF_INET6, 0, 1)
    add(RET, ALLOW)
    add(RET, errno(EAFNOSUPPORT))
    add(JEQ, IOCTL, 0, 5)
    add(LD, arg(1))
    add(JEQ, TIOCSTI, 1, 0)         # typing into the terminal on someone's behalf
    add(JEQ, TIOCLINUX, 0, 1)
    add(RET, errno(EPERM))
    add(RET, ALLOW)
    add(RET, ALLOW)
    return b"".join(p)


if __name__ == "__main__":
    sys.stdout.buffer.write(program())
