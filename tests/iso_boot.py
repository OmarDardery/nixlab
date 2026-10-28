"""Boot the built ISO in a disposable BIOS VM with no disks or network.

Runs only live-environment read/help/guard checks. Never installs, partitions,
stages or converts a target system.
"""
import argparse
import os
from pathlib import Path
import re
import selectors
import shlex
import subprocess
import sys
import time


CHECKS = """set -eu
test -e /run/current-system
test -r /etc/nixlab-source/flake.nix
test -r /etc/nixlab-source/flake.lock
test -s /etc/nixlab-iso-revision
test -r /etc/nixlab-installation.md
for name in machine local installed-system site-modules secrets; do
  test ! -e /etc/nixlab-source/"$name"
done
test ! -e /etc/nixlab-source/hardware-configuration.nix
test ! -e /var/lib/nixlab/auth.json
test ! -e /var/lib/incus/database
if systemctl is-active --quiet sshd.service; then exit 1; fi
if systemctl is-enabled --quiet sshd.service; then exit 1; fi
if systemctl is-enabled --quiet nixlab-firstboot.service; then exit 1; fi
nixlab-stage-install --help >/dev/null
nixlab-install --help >/dev/null
if nixlab-install >/tmp/nixlab-live-guard.log 2>&1; then exit 1; fi
grep -q 'live installation media' /tmp/nixlab-live-guard.log
printf '\\nNIXLAB_ISO_%s\\n' SMOKE_OK
"""


def boot(image, log, timeout):
    acceleration = 'kvm' if os.access('/dev/kvm', os.R_OK | os.W_OK) else 'tcg,thread=multi'
    command = ['qemu-system-x86_64', '-accel', acceleration, '-m', '2048', '-smp', '2',
               '-display', 'none', '-monitor', 'none', '-serial', 'stdio', '-no-reboot',
               '-nic', 'none', '-boot', 'order=d,menu=off', '-cdrom', str(image)]
    print('Booting installer ISO without disks/network, accelerator: ' + acceleration, flush=True)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    output = bytearray()
    sent = False
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as events, log.open('wb') as stream:
            events.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not events.select(timeout=1):
                    if process.poll() is not None:
                        raise RuntimeError('QEMU exited before the ISO checks completed')
                    continue
                block = os.read(process.stdout.fileno(), 65536)
                if not block:
                    raise RuntimeError('ISO serial console closed before the checks completed')
                stream.write(block); stream.flush()
                output.extend(block)
                # The upstream minimal installer autologins its nixos user.
                # Wait for its shell prompt rather than sending input to the bootloader.
                plain = re.sub(rb'\x1b\[[0-?]*[ -/]*[@-~]', b'', bytes(output[-16384:]))
                if not sent and re.search(rb'nixos@nixlab-installer:[^\r\n]*\]\$', plain):
                    script = ('sudo -n sh -c ' + shlex.quote(CHECKS)
                              + " || printf '\\nNIXLAB_ISO_%s\\n' SMOKE_FAILED\n")
                    process.stdin.write(script.encode()); process.stdin.flush()
                    sent = True
                # The complete marker never appears in the submitted command;
                # serial input echo cannot make this check pass.
                if re.search(rb'\r?\nNIXLAB_ISO_SMOKE_OK\r?\n', plain):
                    print('Installer ISO BIOS boot and live-environment checks passed.', flush=True)
                    return
                if re.search(rb'\r?\nNIXLAB_ISO_SMOKE_FAILED\r?\n', plain):
                    raise RuntimeError('The live environment failed an installer ISO check')
        raise RuntimeError('ISO boot/check timeout after ' + str(timeout) + ' seconds')
    except Exception:
        print(bytes(output[-10000:]).decode(errors='replace'), file=sys.stderr)
        raise
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
        process.stdin.close(); process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--log', type=Path, default=Path('iso-boot.log'))
    parser.add_argument('--timeout', type=int, default=600)
    args = parser.parse_args()
    if not args.image.is_file() or args.timeout <= 0:
        parser.error('Provide an existing ISO and a positive timeout')
    args.log.parent.mkdir(parents=True, exist_ok=True)
    try:
        boot(args.image.resolve(), args.log, args.timeout)
    except (OSError, RuntimeError) as exc:
        parser.exit(1, str(exc) + '; serial log: ' + str(args.log) + '\n')


if __name__ == '__main__':
    main()
