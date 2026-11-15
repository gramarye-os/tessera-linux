#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: Apache-2.0
"""Checks when agent.py tells a session's X server that its clients' drivers finish their frames
(the env's Mesa says so of itself, and the drivers installed are that Mesa):
   python3 gramarye/test_mesa.py
Needs nothing of an env or a phone. Exits 0 when every check passes."""
import importlib.util, os, shutil, sys, tempfile

here = os.path.dirname(os.path.abspath(__file__))
work = tempfile.mkdtemp(prefix='tessera-mesa-')
lib = os.path.join(work, 'lib'); os.mkdir(lib)
os.environ.update(TESSERA_MESA_FILE=os.path.join(work, 'mesa'), TESSERA_LIB_DIRS='%s/li*:%s/none' % (work, work))
spec = importlib.util.spec_from_file_location('agent', os.path.join(here, 'agent.py'))
agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)

ok = True
def check(name, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else '  ' + repr(detail))); ok = ok and cond

def said(text):
    if text is None:
        if os.path.exists(agent.MESA_FILE): os.unlink(agent.MESA_FILE)
    else:
        open(agent.MESA_FILE, 'w').write(text)

def drivers(vulkan, opengl, old=None):
    for name in os.listdir(lib): os.unlink(os.path.join(lib, name))
    for name, build in (('libvulkan_freedreno.so', vulkan), ('libgallium-26.3.0-devel.so', opengl), ('libgallium-25.0.7.so', old)):
        if build: open(os.path.join(lib, name), 'wb').write(b'\0ELF' + os.urandom(5000) + b'Mesa 26.3.0-devel (' + build + b')\0' + os.urandom(300))

OURS, THEIRS, WORD = b'git-8ae8f0b6e9', b'git-98f3d6229d', 'build=git-8ae8f0b6e9\npresent=finished\n'
SETTING = 'export TERMUX_X11_PRESENT_SETTLE_US=0; '
try:
    drivers(OURS, OURS, b'25.0.7')
    said(None); check('a Mesa that says nothing: the server keeps its hold', not agent.frames_finished())
    said(WORD); check('a Mesa that says so, and is the one installed', agent.frames_finished())
    said('# from gramarye/mesa\n' + WORD + 'later=word\n'); check('comments and words not known are passed over', agent.frames_finished())
    said('build=git-8ae8f0b6e9\n'); check('a build that does not say it finishes frames', not agent.frames_finished())
    said('build=git-8ae8f0b6e9\npresent=queued\n'); check('a build that says something else', not agent.frames_finished())
    said('build=\npresent=finished\n'); check('no build named', not agent.frames_finished())
    said('build=Mesa\npresent=finished\n'); check('a build name every Mesa has in it', not agent.frames_finished())
    said(WORD)
    drivers(THEIRS, OURS); check('another Vulkan driver put over it', not agent.frames_finished())
    drivers(OURS, THEIRS, b'25.0.7'); check('another OpenGL driver put over it', not agent.frames_finished())
    drivers(None, OURS); check('no Vulkan driver', not agent.frames_finished())
    drivers(OURS, None); check('no OpenGL driver', not agent.frames_finished())
    drivers(OURS, OURS, b'25.0.7')
    script = agent.x_script('/var/lib/tessera/x/s1', 'com.daryan.tessera', 'arm64', 'tessera-x-s1', 1, 'tag')
    check('the server is told', SETTING in script and script.index(SETTING) < script.index('exec '), script)
    check('the rest of its start is as it was', script.replace(SETTING, '') ==
          'export TMPDIR=$AOHP_ROOTFS/var/lib/tessera/x/s1; export XKB_CONFIG_ROOT=$AOHP_ROOTFS/usr/share/X11/xkb; '
          'for f in /data/app/*/com.daryan.tessera-*/base.apk; do export CLASSPATH=$f; '
          'export XLORIE_LIBRARY=${f%/base.apk}/lib/arm64/libXlorie.so; break; done; '
          'exec /system/bin/app_process / --nice-name=tessera-x-s1 com.termux.x11.CmdEntryPoint :1 -tag tag', script)
    check('nothing in it ends the quotes of its unit file', "'" not in script, script)
    said(None)
    script = agent.x_script('/var/lib/tessera/x/s1', 'com.daryan.tessera', 'arm64', 'tessera-x-s1', 1, 'tag')
    check('and is told nothing when the Mesa says nothing', 'PRESENT_SETTLE' not in script, script)
finally:
    shutil.rmtree(work, ignore_errors=True)
sys.exit(0 if ok else 1)
