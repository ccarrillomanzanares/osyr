#!/usr/bin/env python3
"""Inside the sandbox container: listens on a local port and passes every
connection, byte for byte, to a Unix socket (the model filter outside). The
container has no network; this socket is its only way out, and what may go
through it is decided on the other side.

    relay.py HOST PORT SOCKET

The other way round, outside the container, for the benchmark (which gives the
chat the model server itself, as on a laptop, not the web sandbox's filter):

    relay.py --from-socket SOCKET HOST PORT
"""
import os
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


def serve(conn, to):
    out = socket.socket(socket.AF_UNIX if isinstance(to, str) else socket.AF_INET)
    try:
        out.connect(to)
    except OSError:
        conn.close()
        return
    t = threading.Thread(target=pump, args=(out, conn), daemon=True)
    t.start()
    pump(conn, out)
    t.join()
    conn.close()
    out.close()


def listen_socket(path, host, port):
    """Unix socket -> TCP. Returns the listening socket; serve_forever(srv, (host, port)) runs it."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(path)
    srv.listen(16)
    return srv


def serve_forever(srv, to):
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        threading.Thread(target=serve, args=(conn, to), daemon=True).start()


def main():
    if sys.argv[1] == "--from-socket":
        path, host, port = sys.argv[2], sys.argv[3], int(sys.argv[4])
        return serve_forever(listen_socket(path, host, port), (host, port))
    host, port, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(16)
    serve_forever(srv, path)


if __name__ == "__main__":
    main()
