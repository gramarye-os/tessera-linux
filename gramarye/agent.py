#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: Apache-2.0
"""Tessera's agent inside a gramarye (AOHP) env.

Tessera has no root on the phone and the env cannot see Tessera's folders, so launches come in
over the loopback interface the env shares with Android. One connection is one session:

    TESSERA1 <token> <session id> <base64 settings>\n     then the session's terminal, both ways
    TESSERA2 <token> <session id> <settings> <rows> <columns>\n
                                              the same session on a terminal of its own, of that
                                              size (0 0: none, pipes), spoken to in frames: see
                                              serve_session()
    TESSERA3 <token> <session id> <container> <settings> <rows> <columns>\n
                                              as TESSERA2, in the Droidspaces container of that
                                              name (- for the env itself): see session_argv()
    TESSERAX1 <token> <session id> <display> <window tag> <android package>\n
                                              the session's X server, started on the env's side;
                                              answered READY or FAILED <why>, stopped on hang-up
    TESSERAX2 ...the same...                  and after READY the session's X folder is kept in
                                              step with the launcher's copy of it, see mirror()

and two that are no session:

    TESSERAPING <token>\n                     answered PONG <what this env can do> (- when that
                                              isn't known): the launcher asking whether its
                                              token still opens this env. A last word
                                              ds=<name>,<name> lists the running containers
    TESSERAPAIR <code>\n                      answered OK <port> <token> <what this env can do>,
                                              or NO <why>: how a launcher gets the token, see pair()

The token is the only thing between any Android app and a root shell in this env: it is compared
before anything else is read from the line, and the port is bound to 127.0.0.1 only.
The session runs under enter.sh in its own PID namespace; when the connection closes the agent
ends its process group, and the namespace goes with it.
"""
import ctypes
import errno
import fcntl
import glob
import hmac
import json
import mmap
import os
import re
import secrets
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
# What this env can do, as install.sh found: "[vk=<vulkan icd>] [gl=<opengl driver>] [x=host]".
CAPS_FILE = os.environ.get("TESSERA_CAPS_FILE", "/etc/tessera/caps")
# An open pairing: "<code> <until, seconds since 1970> <wrong tries>".
PAIR_FILE = os.environ.get("TESSERA_PAIR_FILE", "/etc/tessera/pair")
PAIR_SECONDS = 120
PAIR_TRIES = 5
CODE = re.compile(rb"^[0-9]{6}$")
pair_lock = threading.Lock()
ENTER = os.environ.get("TESSERA_ENTER", "/opt/tessera/enter.sh")
# A session can run in a Droidspaces container on this phone instead of in the env: the agent
# then starts enter.sh there with "droidspaces run". The container has to have this env's
# /opt/tessera and XROOT bound at the same paths (gramarye/CONTAINER-TARGET.md). Which one, when
# the launcher's line doesn't say (TESSERA1 and TESSERA2), is the one name in this file; with no
# such file those sessions run in the env.
DROIDSPACES = os.environ.get("TESSERA_DROIDSPACES", "droidspaces")
CONTAINER_FILE = os.environ.get("TESSERA_CONTAINER_FILE", "/etc/tessera/container")
# A container's name goes on a command line run as root: a plain word, and never an option.
CONTAINER = re.compile(rb"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
MAX_LINE = 4 * 1024 * 1024
CHUNK = 6000  # enter.sh joins its arguments again; one argument may not exceed 128 KiB
SID = re.compile(rb"^[A-Za-z0-9_-]{1,32}$")
B64 = re.compile(rb"^[A-Za-z0-9+/=]+$")
TAG = re.compile(rb"^[A-Za-z0-9_-]{1,48}$")
PKG = re.compile(rb"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
UNITS = "/etc/aohp/system"
# What the env's Mesa says of itself, in a file its build puts in the env with the drivers
# (gramarye/mesa): "build=git-<commit>", and "present=finished" when its drivers have the GPU
# finish a frame before they present it. And where the drivers are. See frames_finished().
MESA_FILE = os.environ.get("TESSERA_MESA_FILE", "/usr/share/tessera/mesa")
LIB_DIRS = os.environ.get("TESSERA_LIB_DIRS", "/usr/lib/*-linux-gnu:/usr/lib64:/usr/lib").split(":")
BUILD = re.compile(rb"^git-[0-9a-f]{7,40}$")
# While this file is there, a session's X server keeps its hold of Presents whatever the Mesa
# says: for an env where doing without the hold goes wrong.   touch /etc/tessera/keep-present-hold
HOLD_FILE = os.environ.get("TESSERA_HOLD_FILE", "/etc/tessera/keep-present-hold")
XROOT = "/var/lib/tessera/x"  # on the rootfs: the host side sees it under $AOHP_ROOTFS
# What a session's helpers and the launcher tell each other through the X folder (see mirror()):
# files a helper replaces whole, files it appends to, the pipes it takes commands from, and the
# folder the launcher keeps the session's theme in.
MIRROR_WHOLE = ("launcher-windows", "launcher-desktop-items")
MIRROR_APPENDED = ("launcher-focus", "launcher-notify", "launcher-desktop-events")
MIRROR_PIPES = ("launcher-wincmd", "launcher-notifycmd", "launcher-desktop")
MIRROR_TREE = "tessera-theme"
# The pipe the session's sound server plays into (16-bit stereo), and how much of it may wait in
# the pipe: what waits is heard late.
MIRROR_SOUND = "launcher-audio"
SOUND_PIPE = 16 * 1024
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


def holds(path, word):
    """Whether the file has these bytes in it."""
    try:
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            return m.find(word) >= 0
    except (OSError, ValueError):
        return False


def frames_finished():
    """Whether this env's clients present only frames their GPU has finished: the env's Mesa says
    so of itself (MESA_FILE), and the drivers installed are that build - a Mesa names its commit
    in its libraries - not others put over it since (a distro upgrade, another tarball)."""
    try:
        with open(MESA_FILE) as f:
            said = dict(line.strip().split("=", 1) for line in f if "=" in line and not line.startswith("#"))
    except (OSError, UnicodeDecodeError):
        return False
    build = said.get("build", "").encode()
    if said.get("present") != "finished" or not BUILD.match(build):
        return False
    vulkan = [p for d in LIB_DIRS for p in glob.glob(d + "/libvulkan_freedreno.so")]
    opengl = [p for d in LIB_DIRS for p in glob.glob(d + "/libgallium-*.so")]
    return bool(vulkan) and all(holds(p, build) for p in vulkan) and any(holds(p, build) for p in opengl)


def x_script(xdir, pkg, abi, unit, display, tag):
    """What the host side runs for a session's X server.
    An Android GPU driver puts nothing on a buffer for the server to wait on, so the server holds
    each Present of a client's buffer a few milliseconds, which costs a refresh, unless it is told
    that its clients' drivers wait for the GPU themselves. A server that doesn't know the setting
    ignores it. HOLD_FILE keeps the hold in any case."""
    told = "export TERMUX_X11_PRESENT_SETTLE_US=0; " if frames_finished() and not os.path.exists(HOLD_FILE) else ""
    return (
        "export TMPDIR=$AOHP_ROOTFS%s; export XKB_CONFIG_ROOT=$AOHP_ROOTFS/usr/share/X11/xkb; %s"
        "for f in /data/app/*/%s-*/base.apk; do export CLASSPATH=$f; "
        "export XLORIE_LIBRARY=${f%%/base.apk}/lib/%s/libXlorie.so; break; done; "
        "exec /system/bin/app_process / --nice-name=%s com.termux.x11.CmdEntryPoint :%d -tag %s"
    ) % (xdir, told, pkg, abi, unit, display, tag)


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
    script = x_script(xdir, pkg, abi, unit, display, tag)
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


def open_sound(base_fd):
    """The pipe the session's sound server plays into, open for reading; None while there is none.
    The session made it and can put anything under its name: a pipe, not a link, or nothing."""
    try:
        fd = os.open(MIRROR_SOUND, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=base_fd)
    except OSError:
        return None
    if not stat.S_ISFIFO(os.fstat(fd).st_mode):
        os.close(fd)
        return None
    try:
        fcntl.fcntl(fd, getattr(fcntl, "F_SETPIPE_SZ", 1031), SOUND_PIPE)
    except OSError:
        pass
    return fd


def changed(ifd):
    """The names in the folder that the inotify descriptor says something happened to."""
    names = set()
    while True:
        try:
            buf = os.read(ifd, 65536)
        except OSError:
            return names
        if not buf:
            return names
        at = 0
        while at + 16 <= len(buf):
            size = struct.unpack_from("iIII", buf, at)[3]
            names.add(buf[at + 16:at + 16 + size].split(b"\0", 1)[0].decode("utf-8", "replace"))
            at += 16 + size


def mirror(conn, sid, base):
    """Keeps the session's X folder (base) and the launcher's copy of it in step, until the
    launcher hangs up. The session's helpers and the launcher talk through files in that folder,
    which the launcher cannot reach here, so both ends carry them over this connection in frames
    (see frames()):

      to the launcher    F <file>  a file a helper replaced whole (the window list)
                         A <file>  what a helper appended to a file (focus, notifications)
                         U <pipe>  what the session plays: the sound its sound server wrote
                                   into its pipe, in whole 16-bit stereo frames
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
        sound = None
        played = b""
        watched = set(MIRROR_WHOLE + MIRROR_APPENDED)
        # Without inotify, look often enough that a window list or a keyboard isn't late.
        every = 2.0 if ifd is not None else 0.1
        looked = 0.0
        while True:
            if sound is None:
                sound = open_sound(base_fd)
                if sound is not None:
                    poller.register(sound, select.POLLIN)
                    log("x", sid, "mirror: sound")
            events = dict(poller.poll(int(every * 1000)))
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
            if sound is not None and sound in events:
                try:
                    data = os.read(sound, 65536)
                except BlockingIOError:
                    data = None
                except OSError:
                    data = b""
                if data:
                    # Whole frames only: the launcher may drop a piece that comes too late, and
                    # half a frame gone would swap left and right from there on.
                    played += data
                    whole = len(played) & ~3
                    if whole:
                        conn.sendall(frame(b"U", MIRROR_SOUND.encode(), played[:whole]))
                        played = played[whole:]
                elif data == b"":
                    # The sound server is gone; another may make the pipe anew.
                    poller.unregister(sound)
                    os.close(sound)
                    sound = None
                    played = b""
            # Sound arrives some fifty times a second, and each piece shows as a change in the
            # folder: the reports are looked at when one of them changed, or now and then.
            due = time.monotonic() - looked >= every
            if ifd is not None and ifd in events:
                due = bool(changed(ifd) & watched) or due
            if due:
                looked = time.monotonic()
                reports.scan(conn.sendall)
    finally:
        os.close(base_fd)
        if ifd is not None:
            os.close(ifd)
        if sound is not None:
            os.close(sound)


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


def default_container():
    """The container sessions run in when the launcher names none, or None: the env itself."""
    try:
        with open(CONTAINER_FILE, "rb") as f:
            name = f.read().strip()
    except OSError:
        return None
    if not CONTAINER.match(name):
        if name:
            log("ignoring", CONTAINER_FILE, "which holds no container name")
        return None
    return name.decode()


def session_argv(sid, chunks, container=None):
    """What runs a session: enter.sh here in the env, or the same script in a container, started
    there by Droidspaces. Its arguments are the same either way; Droidspaces takes no single
    argument over 8192 bytes, which CHUNK keeps under."""
    argv = ["/bin/sh", ENTER, sid] + chunks
    if container is None:
        return argv
    return [DROIDSPACES, "--name=" + container, "run", "--"] + argv


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


def caps():
    """What this env can do, as install.sh wrote it; None when it never did (an agent put here
    by hand): the launcher then keeps what it knew."""
    try:
        with open(CAPS_FILE) as f:
            return " ".join(f.read().split())
    except OSError:
        return None


# The running containers, and when they were last asked for: a launcher pings often.
containers_lock = threading.Lock()
containers_seen = [0.0, []]


def containers():
    """The Droidspaces containers a session could be started in now: the running ones, by name.
    None where there is no Droidspaces, or it doesn't answer."""
    with containers_lock:
        if time.monotonic() - containers_seen[0] < 5:
            return containers_seen[1]
        names = []
        try:
            out = subprocess.run([DROIDSPACES, "--json", "show"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=2).stdout
            for one in json.loads(out.decode("utf-8", "replace")).get("running", []):
                name = str(one.get("name", "")).encode()
                if CONTAINER.match(name) and name.decode() not in names:
                    names.append(name.decode())
        except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
            names = []
        # The launcher reads one short line: a phone with more containers than this names the first.
        containers_seen[0], containers_seen[1] = time.monotonic(), names[:8]
        return containers_seen[1]


def write_private(path, text):
    tmp = "%s.%s" % (path, os.urandom(4).hex())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.rename(tmp, path)


def open_pairing():
    """`agent.py pair`, run by root in the env: opens a pairing and prints its code."""
    code = "%06d" % secrets.randbelow(10 ** 6)
    write_private(PAIR_FILE, "%s %d 0\n" % (code, time.time() + PAIR_SECONDS))
    print("Pairing code: %s %s" % (code[:3], code[3:]))
    print("Enter it in Tessera, under Setup > Gramarye env, within %d minutes." % (PAIR_SECONDS // 60))


def pair(code, token):
    """The answer to a launcher that sent code. The token opens a root shell in this env to
    whoever holds it, and any app on the phone can reach this port: so it is given once, for a
    code root asked for in the env a moment ago (open_pairing), which a few wrong guesses close."""
    with pair_lock:
        try:
            with open(PAIR_FILE) as f:
                want, until, tries = f.read().split()
            until, tries = int(until), int(tries)
        except (OSError, ValueError):
            return b"NO no pairing is open: run tessera-pair in the env first"
        if time.time() > until:
            os.unlink(PAIR_FILE)
            return b"NO that code has run out: run tessera-pair in the env again"
        if not hmac.compare_digest(code, want.encode()):
            if tries + 1 >= PAIR_TRIES:
                os.unlink(PAIR_FILE)
                return b"NO wrong code, and too many tries: run tessera-pair in the env again"
            write_private(PAIR_FILE, "%s %d %d\n" % (want, until, tries + 1))
            return b"NO wrong code"
        os.unlink(PAIR_FILE)
        return ("OK %d %s %s" % (PORT, token.decode(), caps() or "")).rstrip().encode()


def handle(conn, peer):
    proc = None
    try:
        conn.settimeout(10)
        line = read_line(conn)
        parts = line.split(b" ") if line else []
        with open(TOKEN_FILE, "rb") as f:
            token = f.read().strip()
        if len(parts) == 2 and parts[0] == b"TESSERAPAIR" and token:
            answer = pair(parts[1], token) if CODE.match(parts[1]) else b"NO a pairing code is six digits"
            log("pairing", "done" if answer.startswith(b"OK") else answer.decode(), "for", peer)
            conn.sendall(answer + b"\n")
            return
        shape = {b"TESSERA1": 4, b"TESSERA2": 6, b"TESSERA3": 7, b"TESSERAX1": 6, b"TESSERAX2": 6,
                 b"TESSERAPING": 2}.get(parts[0] if parts else b"")
        if shape != len(parts) or not token or not hmac.compare_digest(parts[1], token):
            log("refused", peer)
            return
        if parts[0] == b"TESSERAPING":
            known = caps()
            # What the env can do, and after it the containers a session can be sent to now.
            # A launcher older than that word keeps what it knew, as for any word it can't read.
            running = containers()
            if known is not None and running:
                known = ("%s ds=%s" % (known, ",".join(running))).strip()
            conn.sendall(("PONG %s" % ("-" if known is None else known)).rstrip().encode() + b"\n")
            return
        if parts[0] in (b"TESSERAX1", b"TESSERAX2"):
            handle_x(conn, peer, parts)
            return
        if parts[0] == b"TESSERA3":
            # The container's name comes third; the rest is TESSERA2's.
            named = parts.pop(3)
            if named == b"-":
                container = None
            elif CONTAINER.match(named):
                container = named.decode()
            else:
                conn.sendall(b"tessera-agent: bad container name\n")
                return
            parts[0] = b"TESSERA2"
        else:
            container = default_container()
        sid, payload = parts[2], parts[3]
        if not SID.match(sid) or not B64.match(payload):
            conn.sendall(b"tessera-agent: bad session id or settings\n")
            return
        conn.settimeout(None)
        chunks = [payload[i:i + CHUNK].decode() for i in range(0, len(payload), CHUNK)]
        argv = session_argv(sid.decode(), chunks, container)
        if container is not None:
            log("session", sid.decode(), "in container", container)
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
    if sys.argv[1:] == ["pair"]:
        open_pairing()
    else:
        main()
