#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Names for the cameras in a session's /dev, as udev gives them under /dev/v4l.

   python3 v4l_links.py video2 video3 ...

For each of these nodes of /dev that it can name, prints a line "by-id/<name> <node>" and a
line "by-path/<name> <node>"; enter.sh makes the links (/dev/v4l/<...> -> ../../<node>).

An env has no udev, so nothing makes /dev/v4l there, and programs count on it: OBS Studio keeps
a camera by these names, so that it is the same camera after it was unplugged and got another
number, and Debian's build of OBS ends in a double free where the folders are missing.

udev names a camera from its USB descriptors in sysfs. An env may not be let into that part of
sysfs (on a phone it carries a vendor's label), so the names here come from what can be had:
the path the node's sysfs entry links to, read without following it, and what the node says of
itself (VIDIOC_QUERYCAP). by-path names come out as udev's; by-id names have the product's
name and no serial number, so two cameras of one model share one, and the first keeps it.
"""
import fcntl
import os
import re
import sys

SYS = "/sys/class/video4linux"
VIDIOC_QUERYCAP = 0x80685600
# .../<controller>/usb2/2-1/2-1.3/2-1.3:1.0/video4linux/video2
USB = re.compile(r"/([^/]+)/usb\d+/(?:[^/]+/)*?\d+-([0-9.]+):(\d+\.\d+)/video4linux/[^/]+$")


def target(node, sys_dir=SYS):
    """Where the node's sysfs entry points, as written in the link; "" when it can't be read."""
    try:
        return os.readlink(os.path.join(sys_dir, node))
    except OSError:
        return ""


def index(node, sys_dir=SYS):
    """The node's place among the video nodes of its device: a camera has one that gives
    pictures and often a second for metadata, numbered in that order."""
    mine = os.path.dirname(os.path.dirname(target(node, sys_dir)))
    if not mine:
        return 0
    try:
        others = os.listdir(sys_dir)
    except OSError:
        return 0
    number = int(node[5:])
    return sum(1 for o in others
               if re.fullmatch(r"video\d+", o) and int(o[5:]) < number
               and os.path.dirname(os.path.dirname(target(o, sys_dir))) == mine)


def by_path(link, idx):
    """udev's by-path name for a USB camera whose sysfs entry links to [link]; None for others."""
    m = USB.search("/" + link)
    if not m:
        return None
    controller, ports, interface = m.groups()
    bus = "pci" if "/pci" in link else "platform"
    return "%s-%s-usb-0:%s:%s-video-index%d" % (bus, controller, ports, interface, idx)


def by_id(card, bus_info, idx):
    """A by-id name from what a USB camera calls itself ("<product>: <stream>"); None for others."""
    if not bus_info.startswith("usb-"):
        return None
    name = re.sub(r"[^A-Za-z0-9_.+-]", "", re.sub(r"\s+", "_", card.split(":")[0].strip()))
    return "usb-%s-video-index%d" % (name, idx) if name else None


def querycap(path):
    """What the node says of itself: (card, bus_info), or two empty strings."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return "", ""
    try:
        cap = bytearray(104)
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, cap)
    except OSError:
        return "", ""
    finally:
        os.close(fd)
    text = lambda b: bytes(b).split(b"\0")[0].decode("utf-8", "replace")
    return text(cap[16:48]), text(cap[48:80])


def main(nodes, dev="/dev", sys_dir=SYS, out=sys.stdout):
    for node in nodes:
        if not re.fullmatch(r"video\d+", node):
            continue
        idx = index(node, sys_dir)
        card, bus_info = querycap(os.path.join(dev, node))
        for folder, name in (("by-id", by_id(card, bus_info, idx)), ("by-path", by_path(target(node, sys_dir), idx))):
            if name:
                out.write("%s/%s %s\n" % (folder, name, node))


if __name__ == "__main__":
    main(sys.argv[1:])
