#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Tessera's agent inside a gramarye (AOHP) env.

Tessera has no root on the phone and the env cannot see Tessera's folders, so launches come in
over the loopback interface the env shares with Android. One connection is one session:

    TESSERA1 <token> <session id> <base64 settings>\n     then the session's terminal, both ways
    TESSERA2 <token> <session id> <settings> <rows> <columns>\n
                                              the same session on a terminal of its own, of that
                                              size (0 0: none, pipes), spoken to in frames: see
                                              serve_session()
    TESSERAX1 <token> <session id> <display> <window tag> <android package>\n
                                              the session's X server, started on the env's side;
                                              answered READY or FAILED <why>, stopped on hang-up
    TESSERAX2 ...the same...                  and after READY the session's X folder is kept in
                                              step with the launcher's copy of it, see mirror()

The token is the only thing between any Android app and a root shell in this env: it is compared
before anything else is read from the line, and the port is bound to 127.0.0.1 only.
The session runs under enter.sh in its own PID namespace; when the connection closes the agent
ends its process group, and the namespace goes with it.
"""
import ctypes
import errno
import fcntl
import hmac
import os
import re
import select
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import termios
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
# What a session's helpers and the launcher tell each other through the X folder (see mirror()):
# files a helper replaces whole, files it appends to, the pipes it takes commands from, and the
# folder the launcher keeps the session's theme in.
MIRROR_WHOLE = ("launcher-windows", "launcher-desktop-items")
MIRROR_APPENDED = ("launcher-focus", "launcher-notify", "launcher-desktop-events")
MIRROR_PIPES = ("launcher-wincmd", "launcher-notifycmd", "launcher-desktop")
MIRROR_TREE = "tessera-theme"
MAX_FILE = 8 * 1024 * 1024
MAX_NAME = 1024
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


def frames(buf):
    """Takes the whole frames off the front of buf: [(verb, name, data)]. A frame is the line
    "<verb> <name length> <data length>", then the name's bytes, then the data's."""
    out = []
    while True:
        nl = buf.find(b"\n")
        if nl < 0:
            if len(buf) > 64:
                raise ValueError("no frame header")
            return out
        head = bytes(buf[:nl]).split(b" ")
        if len(head) != 3 or len(head[0]) != 1 or not head[1].isdigit() or not head[2].isdigit():
            raise ValueError("bad frame header")
        n, m = int(head[1]), int(head[2])
        if n > MAX_NAME or m > MAX_FILE:
            raise ValueError("frame too large")
        end_at = nl + 1 + n + m
        if len(buf) < end_at:
            return out
        out.append((head[0], bytes(buf[nl + 1:nl + 1 + n]), bytes(buf[nl + 1 + n:end_at])))
        del buf[:end_at]


def frame(verb, name, data=b""):
    return b"%s %d %d\n" % (verb, len(name), len(data)) + name + data


def tree_parts(name):
    """The path a launcher names inside the theme folder, as its parts; None when it isn't one."""
    try:
        parts = name.decode("utf-8").split("/")
    except UnicodeDecodeError:
        return None
    if not 2 <= len(parts) <= 16 or parts[0] != MIRROR_TREE:
        return None
    if any(p in ("", ".", "..") or "\0" in p or len(p.encode()) > 255 for p in parts):
        return None
    return parts


def tree_dir(base_fd, parts, make):
    """The folder parts name under the X folder, opened without following a link anywhere: the
    session's processes can write in that folder, and this runs as root."""
    fd = os.dup(base_fd)
    try:
        for i, p in enumerate(parts):
            made = False
            if make:
                try:
                    os.mkdir(p, 0o755, dir_fd=fd)
                    made = True
                except FileExistsError:
                    pass
            nxt = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            if made and i == 0:
                # The session's theme helper says it is ready with a file of its own here.
                os.fchmod(fd, 0o1777)
        return fd
    except BaseException:
        os.close(fd)
        raise


def tree_put(base_fd, parts, data):
    fd = tree_dir(base_fd, parts[:-1], True)
    try:
        tmp = ".%s.tmp" % os.urandom(8).hex()
        out = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        try:
            with os.fdopen(out, "wb") as f:
                f.write(data)
            # Whole files only: the theme helper or GTK may read at any moment.
            os.rename(tmp, parts[-1], src_dir_fd=fd, dst_dir_fd=fd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=fd)
            except OSError:
                pass
            raise
    finally:
        os.close(fd)


def tree_delete(base_fd, parts):
    try:
        fd = tree_dir(base_fd, parts[:-1], False)
    except OSError:
        return
    try:
        try:
            os.unlink(parts[-1], dir_fd=fd)
        except IsADirectoryError:
            shutil.rmtree(parts[-1], ignore_errors=True, dir_fd=fd)
        except OSError:
            pass
    finally:
        os.close(fd)


def pipe_write(base_fd, name, data):
    """Into a helper's command pipe; dropped when no helper reads it, as the launcher's own
    write would be."""
    try:
        fd = os.open(name, os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=base_fd)
    except OSError:
        return
    try:
        if stat.S_ISFIFO(os.fstat(fd).st_mode):
            os.write(fd, data)
    except OSError:
        pass
    finally:
        os.close(fd)


def watch_fd(path):
    """An inotify descriptor that turns readable when something in the folder changes, or None."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fd = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            return None
        # IN_MODIFY | IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE
        if libc.inotify_add_watch(fd, os.fsencode(path), 0x2 | 0x8 | 0x80 | 0x100) < 0:
            os.close(fd)
            return None
        return fd
    except Exception:
        return None


class Reports:
    """What the session's helpers wrote in the X folder since last asked."""

    def __init__(self, base_fd):
        self.base_fd = base_fd
        self.seen = {}
        self.offsets = {}

    def _open(self, name):
        # The session can put anything under these names: a regular file, not a link, or nothing.
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.base_fd)
        except OSError:
            return None, None
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            os.close(fd)
            return None, None
        return fd, st

    @staticmethod
    def _read(fd, offset, size):
        data = b""
        while len(data) < size:
            got = os.pread(fd, size - len(data), offset + len(data))
            if not got:
                break
            data += got
        return data

    def scan(self, send):
        for name in MIRROR_WHOLE:
            fd, st = self._open(name)
            if fd is None:
                continue
            try:
                key = (st.st_ino, st.st_mtime_ns, st.st_size)
                if self.seen.get(name) == key or st.st_size > MAX_FILE:
                    continue
                data = self._read(fd, 0, st.st_size)
                self.seen[name] = key
                send(frame(b"F", name.encode(), data))
            finally:
                os.close(fd)
        for name in MIRROR_APPENDED:
            fd, st = self._open(name)
            if fd is None:
                continue
            try:
                offset = self.offsets.get(name, 0)
                if st.st_size < offset:
                    offset = 0  # a new helper started the file over
                if st.st_size > offset:
                    data = self._read(fd, offset, min(st.st_size - offset, MAX_FILE))
                    self.offsets[name] = offset + len(data)
                    if data:
                        send(frame(b"A", name.encode(), data))
            finally:
                os.close(fd)


def mirror(conn, sid, base):
    """Keeps the session's X folder (base) and the launcher's copy of it in step, until the
    launcher hangs up. The session's helpers and the launcher talk through files in that folder,
    which the launcher cannot reach here, so both ends carry them over this connection in frames
    (see frames()):

      to the launcher    F <file>  a file a helper replaced whole (the window list)
                         A <file>  what a helper appended to a file (focus, notifications)
                         S <mark>  answers the launcher's S: everything before it is done
      from the launcher  P <path>  a file of the session's theme, under tessera-theme/
                         D <path>  one that is gone, or a folder of them
                         L <pipe>  bytes for a helper's command pipe (window commands)
                         S <mark>
    """
    base_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    ifd = watch_fd(base)
    put = 0
    try:
        reports = Reports(base_fd)
        poller = select.poll()
        poller.register(conn.fileno(), select.POLLIN | select.POLLRDHUP | select.POLLHUP | select.POLLERR)
        if ifd is not None:
            poller.register(ifd, select.POLLIN)
        buf = bytearray()
        while True:
            # Without inotify, look often enough that a window list or a keyboard isn't late.
            events = dict(poller.poll(2000 if ifd is not None else 100))
            ev = events.get(conn.fileno(), 0)
            if ev & select.POLLIN:
                try:
                    got = conn.recv(1 << 16, socket.MSG_DONTWAIT)
                except BlockingIOError:
                    got = None
                except OSError:
                    return
                if got == b"":
                    return
                if got:
                    buf += got
                    for verb, name, data in frames(buf):
                        if verb == b"P" or verb == b"D":
                            parts = tree_parts(name)
                            if parts is None:
                                log("x", sid, "mirror: refused path", repr(name[:80]))
                            elif verb == b"P":
                                try:
                                    tree_put(base_fd, parts, data)
                                    put += 1
                                except OSError as e:
                                    log("x", sid, "mirror: put", "/".join(parts), repr(e))
                            else:
                                tree_delete(base_fd, parts)
                        elif verb == b"L":
                            if name.decode("ascii", "replace") in MIRROR_PIPES:
                                pipe_write(base_fd, name.decode("ascii"), data)
                        elif verb == b"S":
                            if put:
                                log("x", sid, "mirror: %d theme files" % put)
                                put = 0
                            conn.sendall(frame(b"S", name))
            elif ev:
                return
            if ifd is not None and ifd in events:
                try:
                    while os.read(ifd, 65536):
                        pass
                except OSError:
                    pass
            reports.scan(conn.sendall)
    finally:
        os.close(base_fd)
        if ifd is not None:
            os.close(ifd)


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
        if parts[0] == b"TESSERAX2":
            try:
                mirror(conn, sid, "%s/%s/.X11-unix" % (XROOT, sid))
            except (OSError, ValueError) as e:
                log("x", sid, "mirror:", repr(e))
            log("x", sid, "peer gone, stopping the server")
            return
        poller = select.poll()
        poller.register(conn.fileno(), select.POLLIN | select.POLLRDHUP | select.POLLHUP | select.POLLERR)
        while True:
            if poller.poll(2000):
                break  # anything from the launcher's side ends it: it only ever hangs up
        log("x", sid, "peer gone, stopping the server")
    finally:
        x_stop(sid)


def set_size(fd, rows, cols):
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


def new_terminal(rows, cols):
    """A terminal for a session, as a (master, slave) pair: the launcher's own is in raw mode,
    so this one does the line editing, echo and signals."""
    master, slave = os.openpty()
    try:
        attrs = termios.tcgetattr(slave)
        attrs[0] |= getattr(termios, "IUTF8", 0x4000)  # Backspace takes a whole character
        attrs[0] &= ~(termios.IXON | termios.IXOFF)  # Ctrl+S and Ctrl+Q are the app's
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
    except termios.error:
        pass
    set_size(master, rows, cols)
    return master, slave


def serve_session(conn, sid, argv, env, rows, cols):
    """Runs a session and carries its input and output until it ends or the launcher hangs up;
    returns the process. Both ends send frames: a type byte, the payload's length as four
    bytes (big endian), the payload.

      from the launcher  D bytes typed       W rows, columns (two bytes each): the size changed
                         E the input ended
      to the launcher    D the app's output  X its exit status (four bytes, signed; -n: signal n)

    With rows and columns the session gets a terminal of that size, which it takes as its
    controlling terminal (enter.sh); with 0 0 it gets pipes.
    """
    master = None
    if rows and cols:
        master, slave = new_terminal(rows, cols)
        try:
            # A group of its own (end() kills by group) but not a session: a shell that leads a
            # session with no terminal can end up owning this one, and the app, which is to
            # own it (enter.sh's setsid --ctty), could then only take it away as root.
            proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, process_group=0,
                                    close_fds=True, cwd="/", env=env)
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        in_fd = out_fd = master
    else:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                start_new_session=True, close_fds=True, cwd="/", env=env, bufsize=0)
        in_fd, out_fd = proc.stdin.fileno(), proc.stdout.fileno()
    log("session", sid, "pid", proc.pid, "terminal %dx%d" % (cols, rows) if master is not None else "pipes")
    lock = threading.Lock()
    drained = threading.Event()
    gone = threading.Event()  # the launcher hung up

    def send(kind, data=b""):
        with lock:
            conn.sendall(kind + struct.pack(">I", len(data)) + data)

    def pump_out():
        # Until every process of the session has let go of its end: read then fails (a terminal)
        # or ends (a pipe).
        try:
            while True:
                select.select([out_fd], [], [])
                try:
                    data = os.read(out_fd, 65536)
                except BlockingIOError:
                    continue
                except OSError:
                    break
                if not data:
                    break
                send(b"D", data)
        except (OSError, ValueError):
            gone.set()
        finally:
            drained.set()

    def write_in(data):
        """All of data to the session's input, unless the launcher hangs up or the session ends
        while it isn't reading."""
        view = memoryview(data)
        waiter = select.poll()
        waiter.register(in_fd, select.POLLOUT)
        waiter.register(conn.fileno(), select.POLLRDHUP | select.POLLHUP | select.POLLERR)
        while len(view):
            try:
                view = view[os.write(in_fd, view):]
                continue
            except BlockingIOError:
                pass
            except OSError:
                return True  # nobody reads it anymore: the session's end shows elsewhere
            for fd, ev in waiter.poll(500):
                if fd == conn.fileno() and ev:
                    return False
            if proc.poll() is not None:
                return True
        return True

    os.set_blocking(in_fd, False)
    threading.Thread(target=pump_out, daemon=True).start()
    try:
        poller = select.poll()
        poller.register(conn.fileno(), select.POLLIN | select.POLLRDHUP | select.POLLHUP | select.POLLERR)
        buf = bytearray()
        input_open = True
        while not gone.is_set():
            if proc.poll() is not None:
                # What it wrote last is still on its way; a process it left behind that holds
                # the terminal open must not keep the launcher waiting.
                drained.wait(2.0)
                break
            if not poller.poll(100):
                continue
            try:
                got = conn.recv(1 << 16, socket.MSG_DONTWAIT)
            except BlockingIOError:
                continue
            except OSError:
                got = b""
            if not got:
                gone.set()
                break
            buf += got
            while len(buf) >= 5:
                size = struct.unpack(">I", buf[1:5])[0]
                if size > (1 << 20):
                    raise ValueError("frame too large")
                if len(buf) < 5 + size:
                    break
                kind, data = bytes(buf[:1]), bytes(buf[5:5 + size])
                del buf[:5 + size]
                if kind == b"D" and input_open:
                    if not write_in(data):
                        gone.set()
                        break
                elif kind == b"W" and size == 4 and master is not None:
                    set_size(master, *struct.unpack(">HH", data))
                elif kind == b"E" and input_open and master is None:
                    input_open = False
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
        if gone.is_set():
            log("session", sid, "peer gone, ending it")
            end(proc)
        else:
            try:
                send(b"X", struct.pack(">i", proc.returncode))
            except OSError:
                pass
    finally:
        if proc.poll() is None:
            end(proc)
        # The reader may still sit in select on these: let it see them fail, not a reused number.
        drained.wait(1.0)
        if master is not None:
            os.close(master)
        else:
            for f in (proc.stdin, proc.stdout):
                try:
                    f.close()
                except OSError:
                    pass
    return proc


def handle(conn, peer):
    proc = None
    try:
        conn.settimeout(10)
        line = read_line(conn)
        parts = line.split(b" ") if line else []
        with open(TOKEN_FILE, "rb") as f:
            token = f.read().strip()
        shape = {b"TESSERA1": 4, b"TESSERA2": 6, b"TESSERAX1": 6, b"TESSERAX2": 6}.get(parts[0] if parts else b"")
        if shape != len(parts) or not token or not hmac.compare_digest(parts[1], token):
            log("refused", peer)
            return
        if parts[0] in (b"TESSERAX1", b"TESSERAX2"):
            handle_x(conn, peer, parts)
            return
        sid, payload = parts[2], parts[3]
        if not SID.match(sid) or not B64.match(payload):
            conn.sendall(b"tessera-agent: bad session id or settings\n")
            return
        conn.settimeout(None)
        chunks = [payload[i:i + CHUNK].decode() for i in range(0, len(payload), CHUNK)]
        argv = ["/bin/sh", ENTER, sid.decode()] + chunks
        env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
        if parts[0] == b"TESSERA2":
            if not (parts[4].isdigit() and parts[5].isdigit() and len(parts[4]) <= 4 and len(parts[5]) <= 4):
                return
            proc = serve_session(conn, sid.decode(), argv, env, int(parts[4]), int(parts[5]))
            log("session", sid.decode(), "exit", proc.returncode)
            return
        fd = conn.fileno()
        proc = subprocess.Popen(argv, stdin=fd, stdout=fd, stderr=fd, start_new_session=True, close_fds=True, cwd="/", env=env)
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
