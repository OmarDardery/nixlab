"""Exercise the installed interactive Bash configuration through a real PTY."""
import errno
import fcntl
import os
import pty
import select
import struct
import subprocess
import tempfile
import termios
import time

with tempfile.TemporaryDirectory() as home:
    # A precmd marker synchronizes with Bash itself. Stripping terminal control
    # codes cannot reconstruct ble.sh's cursor-based prompt rendering.
    with open(home + '/.bash_profile', 'w') as profile:
        profile.write("PROMPT_COMMAND+=('printf \"\\033]0;NIXLAB_TEST_READY\\007\"')\n")
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
    env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=home + '/.config', TERM='xterm-256color')
    env.pop('NIXLAB_NO_FETCH', None)
    command = ('if test "$(type -t ble-attach)" = function && '
               'test "$(type -t fzf-history-widget)" = function && '
               'test "$STARSHIP_SHELL" = bash && test "$_ble_attached" = 1; then printf NIXLAB_CONSOLE_OK; exit 0; '
               'else printf NIXLAB_CONSOLE_FAILED; exit 1; fi\n')
    def controlling_terminal():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    process = subprocess.Popen(['bash', '--login', '-i'], stdin=slave, stdout=slave,
                               stderr=slave, env=env, preexec_fn=controlling_terminal)
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 20
    sent = False
    try:
        while True:
            if time.monotonic() >= deadline:
                raise AssertionError('Interactive Bash exceeded its startup deadline: ' + repr(bytes(output[-16384:])))
            if select.select([master], [], [], 0.1)[0]:
                try: data = os.read(master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO: break
                    raise
                if not data: break
                output.extend(data)
                if b'\x1b[6n' in data: os.write(master, b'\x1b[1;1R')
                if b'\x1b[>c' in data: os.write(master, b'\x1b[>0;136;0c')
                if b'\x1b[c' in data: os.write(master, b'\x1b[?1;2c')
                if not sent and b'NIXLAB_TEST_READY' in output:
                    os.write(master, command.encode()); sent = True
            elif process.poll() is not None:
                break
        assert process.wait(timeout=2) == 0, output.decode(errors='replace')
        assert b'NIXLAB_CONSOLE_OK' in output, output.decode(errors='replace')
        assert b'NIXLAB' in output, output.decode(errors='replace')
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        os.close(master)
