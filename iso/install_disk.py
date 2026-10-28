"""Console disk installer. Destruction requires a fresh disk identity and typed confirmation.

Never run by the web portal or automatically on boot. --dry-run only prints a plan.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def disks():
    data = json.loads(run(['lsblk', '--json', '--bytes', '--paths', '--output',
                          'PATH,TYPE,SIZE,MODEL,SERIAL,WWN,RO,RM,MOUNTPOINTS,MAJ:MIN,PARTLABEL,FSTYPE'],
                         capture_output=True, text=True, timeout=20).stdout)
    pool_devices = set()
    zpool = shutil.which('zpool')
    pool_known = False
    if zpool:
        try:
            status = subprocess.run([zpool, 'status', '-P'], capture_output=True, text=True, timeout=10)
            pool_known = status.returncode == 0 or 'no pools available' in (status.stdout + status.stderr).lower()
            pool_devices = {str(Path(word).resolve()) for word in status.stdout.split() if word.startswith('/dev/')}
        except subprocess.TimeoutExpired:
            pass  # Unknown ZFS state keeps ZFS members ineligible below.
    for disk in data['blockdevices']:
        nodes = list(walk(disk))
        disk['busy'] = (any(node.get('type') not in ('disk', 'part') for node in nodes)
                        or any(str(Path(node['path']).resolve()) in pool_devices for node in nodes)
                        or any(node.get('fstype') == 'zfs_member' for node in nodes) and not pool_known)
        for node in nodes:
            holders = Path('/sys/class/block') / Path(node['path']).name / 'holders'
            if holders.exists() and any(holders.iterdir()): disk['busy'] = True
        aliases = []
        for directory in (Path('/dev/disk/by-id'), Path('/dev/disk/by-path')):
            for link in sorted(directory.glob('*')):
                try:
                    if link.resolve(strict=True) == Path(disk['path']).resolve(strict=True): aliases.append(str(link))
                except OSError:
                    continue
        disk['byId'] = aliases[0] if aliases else None
    return data['blockdevices']


def walk(device):
    yield device
    for child in device.get('children', []):
        yield from walk(child)


def identity(device):
    return {key: device.get(key) for key in ('path', 'size', 'serial', 'wwn', 'maj:min', 'model', 'byId')}


def eligible(device, minimum=32 * 1024**3):
    return (device.get('type') == 'disk' and not device.get('ro') and not device.get('busy')
            and int(device.get('size', 0)) >= minimum
            and re.fullmatch(r'/dev/[a-zA-Z0-9_./-]+', device.get('path', ''))
            and not any(m for node in walk(device) for m in node.get('mountpoints', []) or []))


def plan(device, hostname, efi):
    if not eligible(device):
        raise ValueError('Choose an unmounted writable whole disk of at least 32 GiB; live/system disks are excluded')
    if not efi and not device.get('byId'):
        raise ValueError('BIOS installation requires a stable /dev/disk/by-id or by-path identity; use manual installation otherwise')
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,62}', hostname) or hostname.endswith('-'):
        raise ValueError('Use a lowercase host name containing letters, numbers and internal hyphens')
    return {'version': 1, 'disk': identity(device), 'hostName': hostname, 'firmware': 'UEFI' if efi else 'BIOS',
            'filesystem': 'ext4', 'root': '/mnt', 'erasesEntireDisk': True,
            'confirmation': 'ERASE ' + device['path'] + ' ' + str(device.get('size')),
            'preserveNetworkManagerProfiles': True}


def verify_target(proposal, actual):
    found = next((d for d in actual if d.get('path') == proposal['disk']['path']), None)
    if not found or not eligible(found) or identity(found) != proposal['disk']:
        raise ValueError('Disk identity, capacity or mount state changed. Start a new installation review')
    return found


def configuration(proposal):
    def quoted(value):
        return json.dumps(value).replace('${', '\\${')
    boot = ('boot.loader.systemd-boot.enable = true;\n  boot.loader.efi.canTouchEfiVariables = true;'
            if proposal['firmware'] == 'UEFI' else
            'boot.loader.grub.enable = true;\n  boot.loader.grub.device = ' + quoted(proposal['disk']['byId']) + ';')
    return ('{ ... }: {\n  imports = [ ./hardware-configuration.nix ];\n  ' + boot
            + '\n  networking.hostName = ' + quoted(proposal['hostName'])
            + ';\n  networking.networkmanager.enable = true;\n'
            + '  services.logind.settings.Login = { HandleLidSwitch = "ignore"; HandleLidSwitchExternalPower = "ignore"; HandleLidSwitchDocked = "ignore"; };\n'
            + '  nix.settings.experimental-features = [ "nix-command" "flakes" ];\n'
            + '  system.stateVersion = "26.05";\n}\n')


def install(proposal, confirmed, module):
    if confirmed != proposal['confirmation']:
        raise ValueError('Erase confirmation does not match; no disk changes made')
    if not Path('/etc/nixlab-iso-revision').is_file():
        raise ValueError('Run this command only from the Nixlab live installation media')
    if not sys.stdin.isatty():
        raise ValueError('A local interactive terminal is required')
    root = Path('/mnt')
    if root.is_symlink() or root.resolve() != root or root.is_mount() or (root.exists() and any(root.iterdir())):
        raise ValueError('/mnt must be an empty, unmounted ordinary directory')
    # Re-read immediately before the first destructive command. No path, size,
    # serial/WWN, major:minor or mount state from the selection screen is trusted.
    device = verify_target(proposal, disks())['path']
    run(['wipefs', '--all', '--', device])
    run(['parted', '--script', '--', device, 'mklabel', 'gpt'])
    if proposal['firmware'] == 'UEFI':
        run(['parted', '--script', '--', device, 'mkpart', 'NIXLAB_EFI', 'fat32', '1MiB', '1025MiB'])
        run(['parted', '--script', '--', device, 'set', '1', 'esp', 'on'])
        start = '1025MiB'
    else:
        run(['parted', '--script', '--', device, 'mkpart', 'NIXLAB_BIOS', '1MiB', '3MiB'])
        run(['parted', '--script', '--', device, 'set', '1', 'bios_grub', 'on'])
        start = '3MiB'
    run(['parted', '--script', '--', device, 'mkpart', 'NIXLAB_ROOT', 'ext4', start, '100%'])
    run(['udevadm', 'settle', '--timeout=30'])
    disk = next(d for d in disks() if identity(d) == proposal['disk'])
    parts = {p.get('partlabel'): p['path'] for p in walk(disk) if p.get('type') == 'part'}
    if 'NIXLAB_ROOT' not in parts or proposal['firmware'] == 'UEFI' and 'NIXLAB_EFI' not in parts:
        raise ValueError('New partitions were not discovered. Inspect the disk before retrying')
    run(['mkfs.ext4', '-F', '-L', 'NIXLAB_ROOT', parts['NIXLAB_ROOT']])
    root.mkdir(exist_ok=True)
    mounted = False
    try:
        run(['mount', '--', parts['NIXLAB_ROOT'], str(root)]); mounted = True
        if proposal['firmware'] == 'UEFI':
            run(['mkfs.fat', '-F', '32', '-n', 'NIXLAB_EFI', parts['NIXLAB_EFI']])
            (root / 'boot').mkdir()
            run(['mount', '--', parts['NIXLAB_EFI'], str(root / 'boot')])
        run(['nixos-generate-config', '--root', str(root)])
        (root / 'etc/nixos/configuration.nix').write_text(configuration(proposal))
        # Keep Wi-Fi passwords in root-only runtime profiles, never in Nix source.
        network = root / 'etc/NetworkManager/system-connections'
        network.mkdir(parents=True, mode=0o700, exist_ok=True)
        for directory in (Path('/etc/NetworkManager/system-connections'), Path('/run/NetworkManager/system-connections')):
            for source in directory.glob('*.nmconnection'):
                if source.is_file() and not source.is_symlink():
                    target = network / source.name
                    shutil.copyfile(source, target); target.chmod(0o600)
        import stage
        stage.stage(root, module, str(root))
        print('Installing the selected target. Set its root password when prompted.', flush=True)
        run(['nixos-install', '--root', str(root)])
    finally:
        if mounted:
            run(['umount', '--recursive', str(root)])
    print('Installation complete. Remove the USB and reboot; setup details appear on the installed console.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', required=True)
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run with sudo on the installation media')
    available = disks()
    if args.list:
        print(json.dumps([{'identity': identity(d), 'eligible': bool(eligible(d))} for d in available], indent=2)); return
    choices = [d for d in available if eligible(d)]
    if not choices:
        parser.exit(1, 'No eligible unmounted installation disk. Use the manual installation guide for custom storage.\n')
    for number, disk in enumerate(choices, 1):
        print(str(number) + '. ' + json.dumps(identity(disk)))
    try:
        selected = int(input('Installation disk number (all data on that disk will be erased): '))
        if not 1 <= selected <= len(choices): raise ValueError('Invalid disk number')
        hostname = input('Host name [nixlab]: ').strip() or 'nixlab'
        proposal = plan(choices[selected - 1], hostname, Path('/sys/firmware/efi').exists())
        print(json.dumps(proposal, indent=2))
        if args.dry_run:
            print('Dry run: no changes made.'); return
        print('Other disks are not selected. This operation replaces the ENTIRE selected disk.')
        confirmed = input('Type exactly ' + repr(proposal['confirmation']) + ' to continue: ')
        install(proposal, confirmed, args.module)
    except (ValueError, OSError, StopIteration, subprocess.CalledProcessError, EOFError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    os.umask(0o077)
    main()
