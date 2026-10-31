"""Local-console-only preparation of an explicitly selected blank/replacement data disk."""
import json
import os
from pathlib import Path
import re
import sys
import disk_tools as disk
import runtime as rt
from locking import open_lock


def prepare():
    if os.geteuid() != 0 or not Path('/etc/NIXOS').exists() or not sys.stdin.isatty():
        raise ValueError('Use a local root/SSH terminal on the NixOS host')
    candidates = [d for d in disk.disks() if disk.eligible(d, minimum=1024**3)]
    for index, item in enumerate(candidates, 1): print(str(index) + '. ' + json.dumps(disk.identity(item)))
    if not candidates: raise ValueError('No unmounted, writable, inactive whole data disk of at least 1 GiB')
    selected = int(input('Data disk number (the entire selected disk will be erased): '))
    if not 1 <= selected <= len(candidates): raise ValueError('Invalid disk number')
    selected = candidates[selected - 1]
    name = input('Module/pool name: ').strip()
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', name): raise ValueError('Use a lowercase disk name')
    role = input('Role: data or pool [data]: ').strip() or 'data'
    if role not in ('data', 'pool'): raise ValueError('Choose data or pool')
    phrase = 'ERASE ' + selected['path'] + ' ' + str(selected['size'])
    print(json.dumps({'disk': disk.identity(selected), 'filesystem': 'ext4', 'module': name, 'role': role}, indent=2))
    if input('Type exactly ' + repr(phrase) + ': ') != phrase:
        raise ValueError('Erase confirmation did not match; no disk changes made')
    # Use the same configuration/Incus lock order as jobs and scheduled backup.
    import fcntl
    with rt.locked(blocking=False), open_lock('/run/lock/nixlab-incus.lock') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from operations import check_config_idle
        check_config_idle(rt.STATE)
        active = rt.read_json(rt.STATE / 'active.json', {})
        if rt.read_json(rt.STATE / 'jobs' / active.get('id', '-') / 'job.json', {}).get('status') == 'ready':
            raise ValueError('Discard or finish the reviewed configuration before preparing a disk')
        current = next((d for d in disk.disks() if d['path'] == selected['path']), None)
        if not current or not disk.eligible(current, minimum=1024**3) or disk.identity(current) != disk.identity(selected):
            raise ValueError('Disk identity or use changed; review it again')
        device = current['path']
        rt.audit('disk-prepare-approved', device=device, diskName=name, role=role)
        disk.run(['wipefs', '--all', '--', device])
        disk.run(['parted', '--script', '--', device, 'mklabel', 'gpt'])
        disk.run(['parted', '--script', '--', device, 'mkpart', 'NIXLAB_DATA', 'ext4', '1MiB', '100%'])
        disk.run(['udevadm', 'settle', '--timeout=30'])
        refreshed = next(d for d in disk.disks() if disk.identity(d) == disk.identity(current))
        partition = next(n['path'] for n in disk.walk(refreshed) if n.get('type') == 'part' and n.get('partlabel') == 'NIXLAB_DATA')
        disk.run(['mkfs.ext4', '-F', '-L', 'nixlab-' + name[:8], partition])
        uuid = disk.run(['blkid', '-s', 'UUID', '-o', 'value', '--', partition], capture_output=True, text=True).stdout.strip()
        if not re.fullmatch(r'[0-9a-fA-F-]{16,80}', uuid): raise ValueError('Filesystem UUID discovery failed; inspect the selected disk')
        module = '{ ... }: { nixlab.storage.disks.' + name + ' = { uuid = ' + json.dumps(uuid) + '; fsType = "ext4"; role = ' + json.dumps(role) + '; }; }\n'
        proposal = rt.STATE / 'proposals' / (name + '.nix')
        rt.atomic(proposal, module)
        rt.audit('disk-prepared', device=device, diskName=name, uuid=uuid, role=role)
    print('Filesystem prepared. No host configuration has been activated.')
    print('Review with: sudo nixlab-admin config review --module disks/' + name + '.nix=' + str(proposal))
    print('Inspect status, apply the reviewed transaction, verify access/storage, then confirm it.')
