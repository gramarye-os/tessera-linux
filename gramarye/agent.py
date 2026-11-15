#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tessera's agent inside a gramarye (AOHP) env.

Tessera has no root on the phone and the env cannot see Tessera's folders, so launches come in
over the loopback interface the env shares with Android. One connection is one session:

    TESSERA1 <token> <session id> <base64 settings>\n     then the session's terminal, both ways
    TESSERAX1 <token> <session id> <display> <window tag> <android package>\n
                                              the session's X server, started on the env's side;
                                              answered READY or FAILED <why>, stopped on hang-up

The token is the only thing between any Android app and a root shell in this env: it is compared
before anything else is read from the line, and the port is bound to 127.0.0.1 only.
The session runs under enter.sh in its own PID namespace; when the connection closes the agent
ends its process group, and the namespace goes with it.
"""
import hmac
import os
import re
import select
import shutil
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
TAG = re.compile(rb"^[A-Za-z0-9_-]{1,48}$")
PKG = re.compile(rb"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
UNITS = "/etc/aohp/system"
XROOT = "/var/lib/tessera/x"  # on the rootfs: the host side sees it under $AOHP_ROOTFS
ABI_DIR = {"aarch64": "arm64", "x86_64": "x86_64", "armv7l": "arm", "armv8l": "arm", "i686": "x86"}


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


def run(*cmd):
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)


def x_unit(sid):
    return "tessera-x-%s" % sid


def x_stop(sid):
    unit = x_unit(sid)
    path = "%s/%s.service" % (UNITS, unit)
    if os.path.exists(path):
        try:
            run("systemctl", "stop", unit)
        except Exception as e:
            log("x", sid, "stop:", repr(e))
        try:
            os.unlink(path)
        except OSError:
            pass
        try:
            run("systemctl", "daemon-reload")
        except Exception:
            pass
    shutil.rmtree("%s/%s" % (XROOT, sid), ignore_errors=True)


def x_accepts(path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(1)
        s.connect(path)
        return True
    except OSError:
        return False
    finally:
        s.close()


def x_start(sid, display, tag, pkg):
    """The session's X server as a host-side unit: Tessera's own server, from its APK, with its
    socket folder inside this env so that a client here can pass it buffers. Returns None when it
    accepts clients, else why it does not."""
    if not os.path.isdir(UNITS):
        return "this env has no unit manager"
    abi = ABI_DIR.get(os.uname().machine)
    if abi is None:
        return "unknown machine %s" % os.uname().machine
    x_stop(sid)  # a session id is reused after the launcher restarts
    xdir = "%s/%s" % (XROOT, sid)
    os.makedirs(xdir, mode=0o700)
    os.chmod(XROOT, 0o700)
    unit = x_unit(sid)
    script = (
        "export TMPDIR=$AOHP_ROOTFS%s; export XKB_CONFIG_ROOT=$AOHP_ROOTFS/usr/share/X11/xkb; "
        "for f in /data/app/*/%s-*/base.apk; do export CLASSPATH=$f; "
        "export XLORIE_LIBRARY=${f%%/base.apk}/lib/%s/libXlorie.so; break; done; "
        "exec /system/bin/app_process / --nice-name=%s com.termux.x11.CmdEntryPoint :%d -tag %s"
    ) % (xdir, pkg, abi, unit, display, tag)
    with open("%s/%s.service" % (UNITS, unit), "w") as f:
        f.write(
            "[Unit]\nDescription=Tessera X server for session %s (HostExec, made by tessera-agent)\n"
            "[Service]\nHostExec=yes\nExecStart=/system/bin/sh -c '%s'\nKillMode=control-group\n" % (sid, script)
        )
    run("systemctl", "daemon-reload")
    r = run("systemctl", "start", unit)
    if r.returncode != 0:
        return "unit did not start: %s" % r.stdout.decode(errors="replace").strip().splitlines()[-1:]
    sock = "%s/.X11-unix/X%d" % (xdir, display)
    deadline = time.time() + 10
    while time.time() < deadline:
        if os.path.exists(sock) and x_accepts(sock):
            return None
        time.sleep(0.1)
    return "no X socket after 10 s"


def handle_x(conn, peer, parts):
    sid, display, tag, pkg = parts[2], parts[3], parts[4], parts[5]
    if not (SID.match(sid) and display.isdigit() and 0 < int(display) < 1000 and TAG.match(tag) and PKG.match(pkg)):
        conn.sendall(b"FAILED bad request\n")
        return
    sid = sid.decode()
    try:
        why = x_start(sid, int(display), tag.decode(), pkg.decode())
        if why is not None:
            log("x", sid, "failed:", why)
            conn.sendall(("FAILED %s\n" % why).encode())
            return
        log("x", sid, "display", int(display), "ready for", peer)
        conn.sendall(b"READY\n")
        conn.settimeout(None)
        poller = select.poll()
        poller.register(conn.fileno(), select.POLLIN | select.POLLRDHUP | select.POLLHUP | select.POLLERR)
        while True:
            if poller.poll(2000):
                break  # anything from the launcher's side ends it: it only ever hangs up
        log("x", sid, "peer gone, stopping the server")
    finally:
        x_stop(sid)


def handle(conn, peer):
    proc = None
    try:
        conn.settimeout(10)
        line = read_line(conn)
        parts = line.split(b" ") if line else []
        with open(TOKEN_FILE, "rb") as f:
            token = f.read().strip()
        shape = {b"TESSERA1": 4, b"TESSERAX1": 6}.get(parts[0] if parts else b"")
        if shape != len(parts) or not token or not hmac.compare_digest(parts[1], token):
            log("refused", peer)
            return
        if parts[0] == b"TESSERAX1":
            handle_x(conn, peer, parts)
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
