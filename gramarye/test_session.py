#!/usr/bin/env python3
# Copyright (C) 2026 Chris Anderson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks a session's terminal end to end on one machine: the phone's relay (app/src/main/cpp/relay.c,
built here with cc) against agent.py on a port of its own, with a stand-in for enter.sh.
   python3 gramarye/test_session.py
Needs cc, setsid and stty; nothing of an env or a phone. Exits 0 when every check passes."""
import fcntl, os, pty, select, signal, socket, struct, subprocess, sys, tempfile, termios, time

here = os.path.dirname(os.path.abspath(__file__))
work = tempfile.mkdtemp(prefix='tessera-session-')
relay = os.path.join(work, 'relay')
subprocess.check_call(['cc', '-Wall', '-Wextra', '-Werror', '-O1', '-o', relay,
                       os.path.join(here, '..', 'app', 'src', 'main', 'cpp', 'relay.c')])
with socket.socket() as s:
    s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
open(os.path.join(work, 'token'), 'w').write('sesame\n')
# The stand-in: runs the script named after the session, on the terminal if it got one.
open(os.path.join(work, 'enter.sh'), 'w').write(
    '[ -t 0 ] && exec setsid --ctty --wait sh %s/app-$1.sh\nexec sh %s/app-$1.sh\n' % (work, work))
apps = {
    'size': 'stty size; read x; echo "got $x"; stty size; exit 7',
    'intr': 'trap "echo caught; exit 5" INT; echo ready; sleep 100 & wait',
    'pipe': 'if [ -t 0 ]; then echo tty; else echo no-tty; fi; cat; echo done; exit 3',
    'big': 'seq 1 200000; exit 0',
    'hang': 'echo $$ > %s/hang.pid; echo ready; sleep 100' % work,
}
for name, body in apps.items():
    open(os.path.join(work, 'app-%s.sh' % name), 'w').write(body + '\n')
agent_log = open(os.path.join(work, 'agent.log'), 'w')
agent = subprocess.Popen([sys.executable, os.path.join(here, 'agent.py')], stdout=agent_log, stderr=agent_log,
                         env=dict(os.environ, TESSERA_PORT=str(port), TESSERA_TOKEN_FILE=os.path.join(work, 'token'),
                                  TESSERA_ENTER=os.path.join(work, 'enter.sh')))
for _ in range(50):
    try:
        socket.create_connection(('127.0.0.1', port), timeout=0.2).close(); break
    except OSError:
        time.sleep(0.1)

ok = True
def check(name, cond, detail=''):
    global ok
    print(('ok   ' if cond else 'FAIL ') + name + ('' if cond else '  ' + detail)); ok = ok and cond

def hello(sid, token='sesame'):
    path = os.path.join(work, 'hello-' + sid)
    open(path, 'w').write('TESSERA2 %s %s QQ==' % (token, sid))
    return path

class Term:
    """The relay on a terminal of its own, as the launcher runs it."""
    def __init__(self, sid, rows, cols, token='sesame'):
        self.hello = hello(sid, token)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execv(relay, [relay, str(port), self.hello])
        self.resize(rows, cols)
        self.out = b''
    def resize(self, rows, cols):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
    def until(self, what, timeout=5):
        end = time.time() + timeout
        while what not in self.out and time.time() < end:
            if select.select([self.fd], [], [], 0.1)[0]:
                try: d = os.read(self.fd, 65536)
                except OSError: break
                if not d: break
                self.out += d
        return what in self.out
    def wait(self, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if select.select([self.fd], [], [], 0.05)[0]:
                try: self.out += os.read(self.fd, 65536)
                except OSError: pass
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                try:
                    while select.select([self.fd], [], [], 0.1)[0]: self.out += os.read(self.fd, 65536)
                except OSError: pass
                return os.waitstatus_to_exitcode(status)
        os.kill(self.pid, signal.SIGKILL); os.waitpid(self.pid, 0)
        return None

try:
    # 1. size at start, size after a change, echo and line editing from the far terminal, exit code
    t = Term('size', 30, 90)
    check('size at start', t.until(b'30 90\r\n'), repr(t.out))
    check('hello file is gone', not os.path.exists(t.hello))
    t.resize(41, 123); time.sleep(0.3)
    os.write(t.fd, b'gx\x7fo\r')        # g, x, Backspace, o, Return
    check('line edited and echoed by the far terminal', t.until(b'got go\r\n'), repr(t.out))
    check('size after a change', t.until(b'41 123\r\n'), repr(t.out))
    rc = t.wait()
    check('exit code', rc == 7, 'rc=%r' % rc)

    # 2. Ctrl+C reaches the app as a signal
    t = Term('intr', 24, 80)
    check('app started', t.until(b'ready'))
    os.write(t.fd, b'\x03')
    rc = t.wait()
    check('Ctrl+C is a signal from the far terminal', rc == 5 and b'caught' in t.out, 'rc=%r out=%r' % (rc, t.out))

    # 3. much output, complete and in order
    t = Term('big', 24, 80)
    t.until(b'\r\n200000\r\n', 20)
    rc = t.wait(10)
    lines = t.out.replace(b'\r', b'').split()
    check('200000 lines arrive whole', rc == 0 and lines == [str(i).encode() for i in range(1, 200001)], 'rc=%r n=%d' % (rc, len(lines)))

    # 4. no terminal: pipes, the input's end, exit code
    p = subprocess.run([relay, str(port), hello('pipe')], input=b'hello\n', capture_output=True, timeout=10)
    check('pipes when the input is no terminal', p.stdout == b'no-tty\nhello\ndone\n' and p.returncode == 3, 'rc=%r out=%r err=%r' % (p.returncode, p.stdout, p.stderr))

    # 5. the terminal going away ends the session
    t = Term('hang', 24, 80)
    check('app started', t.until(b'ready'))
    app_pid = int(open(os.path.join(work, 'hang.pid')).read())
    os.kill(t.pid, signal.SIGKILL); os.waitpid(t.pid, 0); os.close(t.fd)
    gone = False
    for _ in range(50):
        try: os.kill(app_pid, 0)
        except ProcessLookupError: gone = True; break
        time.sleep(0.1)
    # The stand-in has no PID namespace to take the app with it; the session's leader must be gone.
    log = open(os.path.join(work, 'agent.log')).read()
    check('agent ends the session when the relay dies', 'session hang peer gone' in log.replace(',', ''), log[-300:])

    # 6. a wrong token is refused, and said so
    t = Term('size', 24, 80, token='wrong')
    rc = t.wait()
    check('refusal is reported', rc == 1 and b'refused this launch' in t.out, 'rc=%r out=%r' % (rc, t.out))
finally:
    agent.terminate(); agent.wait()
    subprocess.call(['pkill', '-f', work])
    if ok:
        import shutil; shutil.rmtree(work, ignore_errors=True)
    else:
        print('left for a look:', work); print(open(os.path.join(work, 'agent.log')).read()[-1500:])
sys.exit(0 if ok else 1)
