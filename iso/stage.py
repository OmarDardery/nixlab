"""Stage first-boot Nixlab conversion on an explicitly mounted NixOS target.
Never partitions, formats, installs a bootloader or invokes nixos-install.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

WRAPPER = '{ ... }: { imports = [ ./nixlab-base.nix ./nixlab-firstboot.nix ]; }\n'


def mount_type(root):
    result = subprocess.run(['findmnt', '--mountpoint', str(root), '--noheadings', '--output', 'FSTYPE'],
                            check=True, capture_output=True, text=True)
    return result.stdout.strip()


def atomic(path, content, mode=0o600):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def stage(root, module, confirmed):
    root = Path(root).absolute()
    if str(root) != confirmed or root == Path('/') or root.resolve() != root:
        raise ValueError('Confirm the exact mounted target path; never use the running root')
    if mount_type(root) in ('', 'tmpfs', 'squashfs', 'overlay', 'iso9660', 'ramfs'):
        raise ValueError('Target must be a mounted persistent filesystem')
    directory = root / 'etc/nixos'
    config, base, first = [directory / n for n in ('configuration.nix', 'nixlab-base.nix', 'nixlab-firstboot.nix')]
    for path in (directory, config, base, first, directory / 'hardware-configuration.nix'):
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('Target configuration must not traverse symlinks')
    if not config.is_file() or not (directory / 'hardware-configuration.nix').is_file():
        raise ValueError('Run nixos-generate-config --root on the mounted target first')
    contents = Path(module).read_text()
    if base.exists() or first.exists():
        if base.is_file() and first.is_file() and config.read_text() == WRAPPER and first.read_text() == contents:
            return False
        raise ValueError('Existing or partial Nixlab staging found; inspect it before continuing')
    try:
        atomic(first, contents)
        config.rename(base)
        atomic(config, WRAPPER)
    except Exception:
        if base.exists():
            base.replace(config)
        first.unlink(missing_ok=True)
        raise
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/mnt')
    parser.add_argument('--confirm-target', required=True)
    parser.add_argument('--module', required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run as root from the installation media')
    try:
        changed = stage(args.root, args.module, args.confirm_target)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, str(exc) + '\n')
    print('Nixlab first-boot setup staged.' if changed else 'Nixlab first-boot setup was already staged.')
    print('Review bootloader, networking and users in etc/nixos/nixlab-base.nix, then run nixos-install.')


if __name__ == '__main__':
    main()
