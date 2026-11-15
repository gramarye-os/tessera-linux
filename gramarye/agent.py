#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tessera's agent inside a gramarye (AOHP) env.

Tessera has no root on the phone and the env cannot see Tessera's folders, so launches come in
over the loopback interface the env shares with Android. One connection is one session:

    TESSERA1 <token> <session id> <base64 settings>\n     then the session's terminal, both ways

The token is the only thing between any Android app and a root shell in this env: it is compared
before anything else is read from the line, and the port is bound to 127.0.0.1 only.
The session runs under enter.sh in its own PID namespace; when the connection closes the agent
ends its process group, and the namespace goes with it.
"""
import hmac
import os
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time

PORT = int(os.environ.get("TESSERA_PORT", "7377"))
TOKEN_FILE = os.environ.get("TESSERA_TOKEN_FILE", "/etc/tessera/token")
ENTER = os.environ.get("TESSERA_ENTER", "/opt/tessera/enter.sh")
MAX_LINE = 4 * 1024 * 1024
CHUNK = 6000  # enter.sh joins its arguments again; one argument may not exceed 128 KiB
SID = re.compile(rb"^[A-Za-z0-9_-]{1,32}$")
B64 = re.compile(rb"^[A-Za-z0-9+/=]+$")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def read_line(conn):
    """The first line, without taking a byte past it: the rest belongs to the session."""
    line = b""
    while len(line) < MAX_LINE:
        peek = conn.recv(65536, socket.MSG_PEEK)
        if not peek:
            return None
        nl = peek.find(b"\n")
        take = len(peek) if nl < 0 else nl + 1
        line += conn.recv(take)
        if nl >= 0:
            return line[:-1]
    return None


def end(proc):
    # SIGKILL at once: the group's leader is unshare's waiting parent, which ignores SIGTERM,
    # and its death is what takes the session's PID namespace down.
    for sig, wait in ((signal.SIGKILL, 3.0),):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(wait)
            return
        except subprocess.TimeoutExpired:
            pass


def handle(conn, peer):
    proc = None
    try:
        conn.settimeout(10)
        line = read_line(conn)
        parts = line.split(b" ") if line else []
        with open(TOKEN_FILE, "rb") as f:
            token = f.read().strip()
        if len(parts) != 4 or parts[0] != b"TESSERA1" or not token or not hmac.compare_digest(parts[1], token):
            log("refused", peer)
            return
        sid, payload = parts[2], parts[3]
        if not SID.match(sid) or not B64.match(payload):
            conn.sendall(b"tessera-agent: bad session id or settings\n")
            return
        conn.settimeout(None)
        chunks = [payload[i:i + CHUNK].decode() for i in range(0, len(payload), CHUNK)]
        fd = conn.fileno()
        proc = subprocess.Popen(
            ["/bin/sh", ENTER, sid.decode()] + chunks,
            stdin=fd, stdout=fd, stderr=fd, start_new_session=True, close_fds=True, cwd="/",
            env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        log("session", sid.decode(), "pid", proc.pid, "from", peer)
        # The session holds the socket too, so its end isn't seen by reading: watch for the
        # peer hanging up instead.
        poller = select.poll()
        poller.register(fd, select.POLLRDHUP | select.POLLHUP | select.POLLERR)
        while proc.poll() is None:
            if poller.poll(500):
                log("session", sid.decode(), "peer gone, ending it")
                end(proc)
                break
        log("session", sid.decode(), "exit", proc.returncode)
    except Exception as e:  # one bad connection must not take the agent down
        log("error", peer, repr(e))
    finally:
        if proc is not None and proc.poll() is None:
            end(proc)
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        conn.close()


def main():
    if not os.path.exists(TOKEN_FILE):
        sys.exit("tessera-agent: no token at %s (run gramarye/install.sh)" % TOKEN_FILE)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(16)
    log("listening on 127.0.0.1:%d" % PORT)
    while True:
        conn, peer = srv.accept()
        threading.Thread(target=handle, args=(conn, "%s:%d" % peer), daemon=True).start()


if __name__ == "__main__":
    main()
