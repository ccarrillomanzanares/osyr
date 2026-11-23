#!/usr/bin/env python3
"""Inside the sandbox container: listens on a local port and passes every
connection, byte for byte, to a Unix socket (the model filter outside). The
container has no network; this socket is its only way out, and what may go
through it is decided on the other side.

    relay.py HOST PORT SOCKET
"""
import socket
import sys
import threading


def pump(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def serve(conn, path):
    out = socket.socket(socket.AF_UNIX)
    try:
        out.connect(path)
    except OSError:
        conn.close()
        return
    t = threading.Thread(target=pump, args=(out, conn), daemon=True)
    t.start()
    pump(conn, out)
    t.join()
    conn.close()
    out.close()


def main():
    host, port, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(16)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve, args=(conn, path), daemon=True).start()


if __name__ == "__main__":
    main()
