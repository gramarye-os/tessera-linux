#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: Apache-2.0
"""Checks agent.py's mirror() against a folder of its own, playing the launcher over a socket pair:
   python3 gramarye/test_mirror.py
Needs nothing of an env or a phone. Exits 0 when every check passes."""
import importlib.util, os, socket, sys, tempfile, threading, time, json
spec = importlib.util.spec_from_file_location('agent', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agent.py'))
agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)
base = tempfile.mkdtemp(prefix='mirror-')
os.chmod(base, 0o1777)
a, b = socket.socketpair()
th = threading.Thread(target=lambda: agent.mirror(a, 't1', base), daemon=True); th.start()
buf = bytearray()
def expect(n, timeout=3):
    out = []; end = time.time() + timeout
    b.settimeout(0.2)
    while len(out) < n and time.time() < end:
        try: got = b.recv(65536)
        except socket.timeout: continue
        buf.extend(got); out += agent.frames(buf)
    return out
ok = True
def check(name, cond):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name); ok = ok and cond
# theme put + sync
b.sendall(agent.frame(b'P', b'tessera-theme/share/themes/Tessera-1/gtk-3.0/gtk.css', b'css1') + agent.frame(b'P', b'tessera-theme/portal', b'color-scheme=1\n') + agent.frame(b'S', b'sync1'))
r = expect(1)
check('sync ack', r == [(b'S', b'sync1', b'')])
check('theme file written', open(base + '/tessera-theme/share/themes/Tessera-1/gtk-3.0/gtk.css').read() == 'css1')
check('theme dir is 1777', oct(os.stat(base + '/tessera-theme').st_mode & 0o7777) == '0o1777')
# traversal refused
b.sendall(agent.frame(b'P', b'tessera-theme/../escape', b'x') + agent.frame(b'P', b'other/file', b'x') + agent.frame(b'P', b'/etc/x', b'x') + agent.frame(b'S', b's2'))
expect(1)
check('no escape', not os.path.exists(base + '/escape') and not os.path.exists(base + '/other') and not os.path.exists(os.path.dirname(base) + '/escape'))
# symlink planted by the session inside the tree
os.symlink('/tmp', base + '/tessera-theme/share/link')
b.sendall(agent.frame(b'P', b'tessera-theme/share/link/planted-by-mirror-test', b'x') + agent.frame(b'S', b's3')); expect(1)
check('symlinked folder not followed', not os.path.exists('/tmp/planted-by-mirror-test'))
os.symlink('/tmp/mirror-target-file', base + '/tessera-theme/portal2')
b.sendall(agent.frame(b'P', b'tessera-theme/portal2', b'new') + agent.frame(b'S', b's4')); expect(1)
check('symlinked file replaced, not followed', not os.path.exists('/tmp/mirror-target-file') and not os.path.islink(base + '/tessera-theme/portal2') and open(base + '/tessera-theme/portal2').read() == 'new')
# delete folder
b.sendall(agent.frame(b'D', b'tessera-theme/share/themes/Tessera-1') + agent.frame(b'S', b's5')); expect(1)
check('folder deleted', not os.path.exists(base + '/tessera-theme/share/themes/Tessera-1') and os.path.isdir(base + '/tessera-theme/share/themes'))
# whole file from a helper
with open(base + '/launcher-windows.tmp', 'w') as f: f.write(json.dumps({'windows': [1]}))
os.replace(base + '/launcher-windows.tmp', base + '/launcher-windows')
t0 = time.time(); r = expect(1); dt = time.time() - t0
check('window list relayed (%.0f ms)' % (dt * 1000), r == [(b'F', b'launcher-windows', b'{"windows": [1]}')])
# appended file
with open(base + '/launcher-focus', 'a') as f: f.write('text\n')
r = expect(1); check('focus appended', r == [(b'A', b'launcher-focus', b'text\n')])
with open(base + '/launcher-focus', 'a') as f: f.write('none\n')
r = expect(1); check('focus appended again, only the new part', r == [(b'A', b'launcher-focus', b'none\n')])
# a link under a report name is not read
os.symlink('/etc/passwd', base + '/launcher-notify')
with open(base + '/launcher-focus', 'a') as f: f.write('text\n')
r = expect(2, 1.0); check('linked report not read', r == [(b'A', b'launcher-focus', b'text\n')])
# command pipe
os.mkfifo(base + '/launcher-wincmd'); rd = os.open(base + '/launcher-wincmd', os.O_RDWR | os.O_NONBLOCK)
b.sendall(agent.frame(b'L', b'launcher-wincmd', b'activate 123\n') + agent.frame(b'L', b'launcher-nope', b'x') + agent.frame(b'S', b's6')); expect(1)
check('command reached the pipe', os.read(rd, 100) == b'activate 123\n')
# split frame delivery
whole = agent.frame(b'L', b'launcher-wincmd', b'close 5\n') + agent.frame(b'S', b's7')
b.sendall(whole[:7]); time.sleep(0.1); b.sendall(whole[7:]); expect(1)
check('split frame', os.read(rd, 100) == b'close 5\n')
# sound: the pipe a sound server plays into, sent on in whole frames
os.mkfifo(base + '/launcher-audio')
wr = os.open(base + '/launcher-audio', os.O_RDWR | os.O_NONBLOCK)
os.write(wr, b'abcdefghij')                    # two and a half frames
r = expect(1); check('sound sent in whole frames', r == [(b'U', b'launcher-audio', b'abcdefgh')])
os.write(wr, b'klmnop')
r = expect(1); check('the rest of a frame goes with the next', r == [(b'U', b'launcher-audio', b'ijklmnop')])
os.close(wr); time.sleep(0.3)
wr = os.open(base + '/launcher-audio', os.O_WRONLY | os.O_NONBLOCK)   # a new sound server
os.write(wr, b'1234')
r = expect(1); check('sound again after the server went and came back', r == [(b'U', b'launcher-audio', b'1234')])
with open(base + '/launcher-focus', 'a') as f: f.write('none\n')
os.write(wr, b'5678')
r = expect(2); check('reports still arrive beside sound', sorted(r) == [(b'A', b'launcher-focus', b'none\n'), (b'U', b'launcher-audio', b'5678')])
os.close(wr)
os.unlink(base + '/launcher-audio'); os.symlink('/etc/passwd', base + '/launcher-audio')
r = expect(1, 1.0); check('a link under the sound pipe\'s name is not read', r == [])
b.close(); th.join(3)
check('ends on hang-up', not th.is_alive())
import shutil; shutil.rmtree(base)
sys.exit(0 if ok else 1)
