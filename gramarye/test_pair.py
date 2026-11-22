#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: Apache-2.0
"""Checks agent.py's pairing and ping on a port and files of its own:
   python3 gramarye/test_pair.py
Needs nothing of an env or a phone. Exits 0 when every check passes."""
import os, re, socket, subprocess, sys, tempfile, time

here = os.path.dirname(os.path.abspath(__file__))
work = tempfile.mkdtemp(prefix='tessera-pair-')
with socket.socket() as s:
    s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
open(os.path.join(work, 'token'), 'w').write('sesame\n')
open(os.path.join(work, 'caps'), 'w').write('vk=/usr/share/vulkan/icd.d/x.json gl=kgsl x=host\n')
# A stand-in for Droidspaces: answers "--json show" with what ds.json holds, and fails without it.
ds = os.path.join(work, 'droidspaces')
ds_json = os.path.join(work, 'ds.json')
open(ds, 'w').write('#!/bin/sh\n[ "$1 $2" = "--json show" ] && exec cat %s\nexit 2\n' % ds_json)
os.chmod(ds, 0o755)
env = dict(os.environ, TESSERA_PORT=str(port), TESSERA_TOKEN_FILE=os.path.join(work, 'token'),
           TESSERA_CAPS_FILE=os.path.join(work, 'caps'), TESSERA_PAIR_FILE=os.path.join(work, 'pair'),
           TESSERA_DROIDSPACES=ds)
agent_py = os.path.join(here, 'agent.py')
agent = subprocess.Popen([sys.executable, agent_py], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
for _ in range(50):
    try:
        socket.create_connection(('127.0.0.1', port), timeout=0.2).close(); break
    except OSError:
        time.sleep(0.1)

ok = True
def check(name, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else '  ' + repr(detail))); ok = ok and cond

def ask(line):
    with socket.create_connection(('127.0.0.1', port), timeout=3) as s:
        s.sendall(line.encode() + b'\n')
        out = b''
        while not out.endswith(b'\n'):
            d = s.recv(4096)
            if not d: break
            out += d
        return out.decode().rstrip('\n')

def open_pairing():
    out = subprocess.run([sys.executable, agent_py, 'pair'], env=env, capture_output=True, text=True).stdout
    return re.search(r'(\d{3}) (\d{3})', out).group(1, 2)

want = 'OK %d sesame vk=/usr/share/vulkan/icd.d/x.json gl=kgsl x=host' % port
try:
    check('no pairing open', ask('TESSERAPAIR 123456').startswith('NO no pairing is open'))
    a, b = open_pairing()
    code = a + b
    check('the code is kept private', oct(os.stat(os.path.join(work, 'pair')).st_mode & 0o777) == '0o600')
    wrong = '%06d' % ((int(code) + 1) % 10 ** 6)
    check('a wrong code is refused', ask('TESSERAPAIR ' + wrong) == 'NO wrong code')
    check('not six digits is refused', ask('TESSERAPAIR 12ab').startswith('NO a pairing code is six digits'))
    r = ask('TESSERAPAIR ' + code)
    check('the code gives the line', r == want, r)
    check('a code works once', ask('TESSERAPAIR ' + code).startswith('NO no pairing is open'))

    code = ''.join(open_pairing())
    wrong = '%06d' % ((int(code) + 1) % 10 ** 6)
    answers = [ask('TESSERAPAIR ' + wrong) for _ in range(5)]
    check('five wrong guesses close it', answers[:4] == ['NO wrong code'] * 4 and 'too many tries' in answers[4], answers)
    check('and the right code is no use after', ask('TESSERAPAIR ' + code).startswith('NO no pairing is open'))

    code = ''.join(open_pairing())
    path = os.path.join(work, 'pair')
    open(path, 'w').write('%s %d 0\n' % (code, time.time() - 1))
    check('a code runs out', 'run out' in ask('TESSERAPAIR ' + code))

    check('ping with the token', ask('TESSERAPING sesame') == 'PONG vk=/usr/share/vulkan/icd.d/x.json gl=kgsl x=host')
    check('ping with a wrong token gets nothing', ask('TESSERAPING wrong') == '')

    # The running containers follow what the env can do; a name that is none is left out.
    open(ds_json, 'w').write('{"running":[{"name":"box","pid":1},{"name":"--help"},{"name":"other.one"}],"total":3}\n')
    time.sleep(5.5)  # the agent asks Droidspaces again after five seconds
    r = ask('TESSERAPING sesame')
    check('ping names the running containers', r == 'PONG vk=/usr/share/vulkan/icd.d/x.json gl=kgsl x=host ds=box,other.one', r)
    open(ds_json, 'w').write('not json\n')
    time.sleep(5.5)
    r = ask('TESSERAPING sesame')
    check('an answer that is no list names none', r == 'PONG vk=/usr/share/vulkan/icd.d/x.json gl=kgsl x=host', r)
finally:
    agent.terminate(); agent.wait()
    import shutil; shutil.rmtree(work, ignore_errors=True)
sys.exit(0 if ok else 1)
