#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: Apache-2.0
"""Run a command inside a running Tessera session of this env, as the app it holds.

  in_session.py <pid of the app> [NAME=value ...] -- command [args ...]

For tests and measurements (gramarye/teartest.c) that have to see what the app sees: its display,
its /dev, its GL driver. Run as root in the env, from a path the app can read (/var/tmp). The
command gets the app's uid, groups and environment, plus the NAME=value pairs.

Root in an env has no ptrace rights over another uid, so it can neither read the app's
/proc/<pid>/environ nor enter its namespaces through it. It goes in through the nearest ancestor
that is root's (the session's enter.sh), switches uid, and only then, as the app's own uid and
inside the session (where the app has another pid, so it is found by name), reads the environment.
"""
import os
import sys


def status(pid):
    with open("/proc/%s/status" % pid) as f:
        return dict(line.split(":\t", 1) for line in f.read().splitlines() if ":\t" in line)


def inside(comm, extra, cmd):
    for p in sorted((x for x in os.listdir("/proc") if x.isdigit()), key=int):
        try:
            with open("/proc/%s/comm" % p) as f:
                if f.read().strip() != comm:
                    continue
            with open("/proc/%s/environ" % p, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        env = dict(e.decode().split("=", 1) for e in raw.split(b"\0") if b"=" in e)
        env.update(e.split("=", 1) for e in extra)
        os.execvpe(cmd[0], cmd, env)
    sys.exit("in_session.py: no %s in this session whose environment can be read" % comm)


def main(argv):
    if "--" not in argv or len(argv) < 4:
        sys.exit(__doc__)
    i = argv.index("--")
    extra, cmd = argv[2:i], argv[i + 1:]
    if argv[1].startswith("--inner="):
        inside(argv[1][8:], extra, cmd)
    pid = argv[1]
    with open("/proc/%s/comm" % pid) as f:
        comm = f.read().strip()
    st = status(pid)
    uid, gid = st["Uid"].split()[0], st["Gid"].split()[0]
    groups = ",".join(st["Groups"].split()) or gid
    ns = pid
    while status(ns)["Uid"].split()[0] != "0":
        ns = status(ns)["PPid"].strip()
    os.execvp("nsenter", ["nsenter", "-t", ns, "-m", "-p", "--", "setpriv", "--reuid=" + uid, "--regid=" + gid,
                          "--groups=" + groups, "--", sys.executable, os.path.abspath(argv[0]), "--inner=" + comm]
              + extra + ["--"] + cmd)


if __name__ == "__main__":
    main(sys.argv)
