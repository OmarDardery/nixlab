"""Inventory and eligibility checks for explicitly reviewed data-disk preparation."""
import json
from pathlib import Path
import re
import shutil
import subprocess


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

