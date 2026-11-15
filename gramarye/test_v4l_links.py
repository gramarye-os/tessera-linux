#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks the names v4l_links.py gives cameras, against a sysfs made of links here.
   python3 gramarye/test_v4l_links.py
Needs nothing of an env, a phone or a camera. Exits 0 when every check passes."""
import io, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v4l_links as v

failed = 0
def check(what, ok):
    global failed
    print(('ok   ' if ok else 'FAIL ') + what)
    failed += 0 if ok else 1

# As a OnePlus 13 has it, a webcam behind a dock's hub and the phone's own video codec.
PHONE = '../../devices/platform/soc/a600000.ssusb/a600000.dwc3/xhci-hcd.2.auto/usb2/2-1/2-1.3/2-1.3:1.0/video4linux/'
PC = '../../devices/pci0000:00/0000:00:14.0/usb1/1-4/1-4:1.0/video4linux/'
CODEC = '../../devices/platform/soc/aa00000.qcom,vidc/video4linux/'
sysfs = tempfile.mkdtemp(prefix='tessera-v4l-')
for name, to in (('video2', PHONE), ('video3', PHONE), ('video0', PC), ('video32', CODEC), ('v4l-subdev0', CODEC)):
    os.symlink(to + name, os.path.join(sysfs, name))

check('by-path of a camera behind a hub on a phone',
      v.by_path(PHONE + 'video2', 0) == 'platform-xhci-hcd.2.auto-usb-0:1.3:1.0-video-index0')
check('by-path of a camera on a PC', v.by_path(PC + 'video0', 0) == 'pci-0000:00:14.0-usb-0:4:1.0-video-index0')
check('no by-path for what is not on USB', v.by_path(CODEC + 'video32', 0) is None)
check('no by-path without a sysfs entry', v.by_path('', 0) is None)
check('by-id from the name a camera gives', v.by_id('OBSBOT Tiny 2: OBSBOT Tiny 2 St', 'usb-xhci-hcd.2.auto-1.3', 0) == 'usb-OBSBOT_Tiny_2-video-index0')
check('by-id keeps to plain characters', v.by_id('Web/Cam "HD" (R)', 'usb-0000:00:14.0-4', 1) == 'usb-WebCam_HD_R-video-index1')
check('no by-id for what is not on USB', v.by_id('msm_vidc_decoder', 'platform:msm_vidc_bus', 0) is None)
check('no by-id without a name', v.by_id(' : x', 'usb-1', 0) is None)
check('the picture node comes first, the metadata node second',
      (v.index('video2', sysfs), v.index('video3', sysfs)) == (0, 1))
check('nodes of other devices are not counted', (v.index('video0', sysfs), v.index('video32', sysfs)) == (0, 0))
check('a node sysfs does not know is the first', v.index('video9', sysfs) == 0)

# main(): the nodes are not cameras here (no such files), so only by-path names come out.
out = io.StringIO()
v.main(['video2', 'video3', 'video32', 'v4l-subdev0', '../video2', 'video9'], dev=os.path.join(sysfs, 'none'), sys_dir=sysfs, out=out)
check('only what can be named is printed, one line a link', out.getvalue().splitlines() == [
    'by-path/platform-xhci-hcd.2.auto-usb-0:1.3:1.0-video-index0 video2',
    'by-path/platform-xhci-hcd.2.auto-usb-0:1.3:1.0-video-index1 video3'])
check('a node that is no camera says nothing of itself', v.querycap('/dev/null') == ('', ''))
check('a node that is not there says nothing of itself', v.querycap(os.path.join(sysfs, 'none')) == ('', ''))

for name in os.listdir(sysfs):
    os.unlink(os.path.join(sysfs, name))
os.rmdir(sysfs)
sys.exit(1 if failed else 0)
