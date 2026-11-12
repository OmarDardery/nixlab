"""Offline-material recovery into a fresh, stopped Incus quarantine project.

No disk formatting, source activation, credentials overwrite or guest start.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
from urllib.parse import quote

import operations as ops
import recovery
import runtime as rt
from locking import open_lock
import fcntl

MODE = Path('/etc/nixlab/recovery-mode')
DESIRED = Path('/etc/nixlab/desired-instances.json')


def journal():
    return rt.STATE / 'replacement-recovery.json'


def digest(path):
    return ops.file_hash(path)


def read_json(path):
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_file() or path.stat().st_size > 16 * 1024**2:
        raise ValueError('Expected a bounded regular JSON file without symlinks')
    return json.loads(path.read_text())


def material(directory):
    directory = Path(directory).absolute()
    if directory.resolve() != directory or not directory.is_dir():
        raise ValueError('Recovery material cannot traverse symlinks')
    if directory.stat().st_uid != os.geteuid() or directory.stat().st_mode & 0o077:
        raise ValueError('Recovery material must be owned by root in a mode-0700 directory')
    for parent in directory.parents:
        metadata = parent.stat()
        if metadata.st_uid not in (0, os.geteuid()) or (metadata.st_mode & 0o022 and not metadata.st_mode & 0o1000):
            raise ValueError('Recovery material has an untrusted or writable parent directory')
    inventory = read_json(directory / 'inventory.json')
    if inventory.get('format') != 2 or not isinstance(inventory.get('archiveSHA256'), dict):
        raise ValueError('Use a complete version-2 backup generation with archive checksums')
    for name, expected in inventory['archiveSHA256'].items():
        if not re.fullmatch(r'(?:instance-[0-9a-f]{64}|volume-[0-9a-f]{64}|nixlab-config|nixlab-secrets)\.tar\.gz', name):
            raise ValueError('Unsupported recovery archive name')
        path = directory / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o022
                or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected) or digest(path) != expected):
            raise ValueError('Recovery archive missing or checksum mismatch')
    for name in ('nixlab-config.tar.gz', 'nixlab-secrets.tar.gz'):
        if name not in inventory['archiveSHA256']:
            raise ValueError('Recovery generation lacks configuration/secrets; provide independent recovery material first')
        recovery.inspect(directory / name)
    return directory, inventory


def api(path):
    return json.loads(ops.incus('query', path))


def project_path(project):
    return '/1.0/projects/' + quote(project, safe='')


def guest_path(project, name):
    return '/1.0/instances/' + quote(name, safe='') + '?project=' + quote(project, safe='')


def volume_path(project, pool, name):
    return '/1.0/storage-pools/' + quote(pool, safe='') + '/volumes/custom/' + quote(name, safe='') + '?project=' + quote(project, safe='')


def exists(collection, name):
    return any(item['name'] == name for item in collection)


def identifier(kind, *parts):
    return kind + '-' + hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:16]


def pool_fingerprint(pool):
    return hashlib.sha256(json.dumps({key: pool.get(key) for key in ('driver', 'config')}, sort_keys=True).encode()).hexdigest()


def plan(directory, mapping):
    if journal().exists():
        raise ValueError('A recovery journal already exists; inspect status and resume it')
    directory, inventory = material(directory)
    if not isinstance(mapping, dict) or set(mapping) != {'pool', 'interfaces', 'disks'}:
        raise ValueError('Mappings require pool, interfaces and disks; preserve replacement boot/storage configuration')
    pool = ops.name(mapping['pool'])
    for kind, parent in (('interfaces', Path('/sys/class/net')), ('disks', Path('/dev/disk/by-uuid'))):
        if not isinstance(mapping[kind], dict) or any(not isinstance(old, str) or not isinstance(new, str)
                or '/' in new or new in ('', '.', '..') or not (parent / new).exists() for old, new in mapping[kind].items()):
            raise ValueError('Mapped replacement interfaces/disks must exist on this host')
    pool_state = api('/1.0/storage-pools/' + quote(pool, safe=''))
    if pool_state.get('driver') not in ('dir', 'zfs', 'btrfs', 'lvm'):
        raise ValueError('This recovery increment supports local dir/zfs/btrfs/lvm pools; review other backends manually')
    recovery_id = secrets.token_hex(16)
    project = 'recovery-' + recovery_id[:12]
    bridge = 'nlr-' + recovery_id[:10]
    if exists(api('/1.0/projects?recursion=1'), project) or exists(api('/1.0/networks?recursion=1'), bridge):
        raise ValueError('Quarantine name collision; no existing resource is adopted')
    volumes = []
    namespaces = {p['name']: p['name'] if p.get('config', {}).get('features.storage.volumes') == 'true' else 'default'
                  for p in inventory.get('projects', [])}
    for volume in inventory.get('customVolumes', []):
        for key in ('project', 'pool', 'name'): ops.name(volume[key])
        if volume['archive'] not in inventory['archiveSHA256']: raise ValueError('Volume archive is not checksummed')
        volumes.append({**{key: volume[key] for key in ('project', 'pool', 'name', 'archive')},
                        'target': identifier('volume', volume['project'], volume['pool'], volume['name'])})
    guests, profiles = [], set()
    for guest in inventory.get('guests', []):
        for key in ('project', 'name'): ops.name(guest[key])
        if guest['archive'] not in inventory['archiveSHA256']: raise ValueError('Guest archive is not checksummed')
        if guest.get('type') not in ('container', 'virtual-machine'):
            raise ValueError('Unsupported guest type')
        config = guest.get('expanded_config', guest.get('config', {}))
        if config.get('security.privileged') == 'true' or any(key.startswith('raw.') and value for key, value in config.items()):
            raise ValueError('Privileged/raw guest settings require a separate recovery review')
        devices = guest.get('expanded_devices', guest.get('devices', {}))
        if not any(device.get('type') == 'disk' and device.get('path') == '/' for device in devices.values()):
            raise ValueError('Guest root storage information is incomplete')
        imported_devices = {}
        for name, device in devices.items():
            ops.name(name)
            if device.get('type') == 'disk' and device.get('path') == '/':
                if device.get('source'): raise ValueError('Raw root-disk sources require a separate recovery review')
                imported_devices[name] = {**device, 'pool': pool}
            elif device.get('type') == 'nic' and device.get('network'):
                imported_devices[name] = {'type': 'none'}
            elif device.get('type') == 'none':
                imported_devices[name] = {'type': 'none'}
            elif device.get('type') == 'disk' and device.get('pool') and device.get('source'):
                identity = (namespaces.get(guest['project'], 'default'), device['pool'], device['source'].removeprefix('custom/'))
                volume = next((v for v in volumes if tuple(v[key] for key in ('project', 'pool', 'name')) == identity), None)
                if volume is None: raise ValueError('Attached custom volume is missing from recovery coverage')
                imported_devices[name] = {**device, 'pool': pool, 'source': volume['target']}
            else:
                raise ValueError('External disks, unmanaged NICs and passthrough require a separate recovery review')
        for name in guest.get('profiles', []): profiles.add(ops.name(name))
        guests.append({'project': guest['project'], 'name': guest['name'], 'archive': guest['archive'],
                       'target': identifier('guest', guest['project'], guest['name']), 'devices': imported_devices,
                       'originalDevices': devices})
    if not guests: raise ValueError('No recoverable guests are present')
    if len({(g['project'], g['name']) for g in guests}) != len(guests) or len({v['target'] for v in volumes}) != len(volumes):
        raise ValueError('Duplicate recovery identities are not supported')
    configuration, files = recovery.inspect(directory / 'nixlab-config.tar.gz')
    source_revision = configuration.get('release', {}).get('revision', 'unknown')
    if not isinstance(source_revision, str) or not re.fullmatch(r'[0-9a-f]{40}', source_revision): source_revision = 'unknown'
    lock = files.get('config/flake.lock')
    if lock is not None:
        from commissioning import digest as context_digest
        source_inputs = context_digest(lock.decode().strip())
    else: source_inputs = 'unknown'
    record = {'format': 1, 'id': recovery_id, 'phase': 'planned', 'created': time.time(),
              'source': str(directory), 'inventorySHA256': digest(directory / 'inventory.json'),
              'mapping': mapping, 'poolFingerprint': pool_fingerprint(pool_state),
              'project': project, 'bridge': bridge, 'profiles': sorted(profiles | {'default'}),
              'guests': guests, 'volumes': volumes, 'completed': [], 'running': None,
              'coverage': {'complete': inventory.get('coverageDetails', {}).get('complete') is True,
                           'assessment': 'source-inventory' if 'coverageDetails' in inventory else 'unknown-legacy'},
              'backupCreated': inventory.get('created'), 'restore': 'unverified'}
    record.update(sourceRevision=source_revision, sourceInputsDigest=source_inputs)
    rt.write_json(journal(), record)
    return status(record)


def status(record=None):
    record = record or read_json(journal())
    return {key: record.get(key) for key in ('format', 'id', 'phase', 'created', 'project', 'bridge', 'mapping',
            'completed', 'running', 'coverage', 'backupCreated', 'restore', 'durationSeconds', 'error', 'sourceRevision', 'sourceInputsDigest')} | {
                'guests': [{'source': g['project'] + '/' + g['name'], 'target': g['target']} for g in record['guests']],
                'volumes': [{'source': '/'.join(v[key] for key in ('project', 'pool', 'name')), 'target': v['target']} for v in record['volumes']]}


def save(record):
    rt.write_json(journal(), record)


def edit(arguments, document):
    result = subprocess.run(['incus', '--force-local', *arguments], input=json.dumps(document),
                            capture_output=True, text=True, timeout=60)
    if result.returncode: raise ValueError('Recovery metadata edit failed; inspect the stopped target locally')


def resume(confirmed=False):
    if not confirmed: raise ValueError('Explicit --confirm is required for recovery imports')
    if not MODE.is_file() or MODE.read_text().strip() != '1':
        raise ValueError('Enable nixlab.recovery.enable on the replacement host before importing')
    record = read_json(journal())
    if record['phase'] == 'complete': raise ValueError('Recovery was released; no imports are repeated')
    source, inventory = material(record['source'])
    if digest(source / 'inventory.json') != record['inventorySHA256']:
        raise ValueError('Recovery inventory changed; inspect the journal and original material')
    pool = record['mapping']['pool']
    current_pool = api('/1.0/storage-pools/' + quote(pool, safe=''))
    if pool_fingerprint(current_pool) != record['poolFingerprint']:
        raise ValueError('Replacement pool configuration changed after review')
    token = 'user.nixlab.recovery-id'
    project, bridge = record['project'], record['bridge']

    def step(key, action):
        if key in record['completed']: return
        record.update(running=key, phase='importing', error=None); save(record)
        action()
        record['completed'].append(key); record['running'] = None; save(record)

    def verify_completed():
        if 'project' in record['completed']:
            if api(project_path(project)).get('config', {}).get(token) != record['id']:
                raise ValueError('Quarantine project changed after checkpoint')
        if 'isolated-bridge' in record['completed']:
            network = api('/1.0/networks/' + bridge)
            config = network.get('config', {})
            if config.get(token) != record['id'] or any(config.get(key) not in ('none', '') for key in ('ipv4.address', 'ipv6.address')) or config.get('bridge.external_interfaces'):
                raise ValueError('Quarantine network changed after checkpoint')
        for item in record['volumes']:
            if 'volume/' + item['target'] in record['completed']:
                if api(volume_path(project, pool, item['target'])).get('config', {}).get(token) != record['id']:
                    raise ValueError('Recovered volume changed after checkpoint')
        for item in record['guests']:
            if 'guest/' + item['target'] in record['completed']:
                current = api(guest_path(project, item['target']))
                if (current.get('status') != 'Stopped' or current.get('config', {}).get('volatile.uuid') != item.get('uuid')
                        or current.get('devices') != item['devices'] or current.get('profiles')):
                    raise ValueError('Recovered guest changed after checkpoint; stop and inspect it')

    def create_project():
        if exists(api('/1.0/projects?recursion=1'), project):
            if api(project_path(project)).get('config', {}).get(token) != record['id']:
                raise ValueError('Existing project is not owned by this recovery')
        else:
            ops.incus('project', 'create', project, '-c', token + '=' + record['id'],
                      '-c', 'features.profiles=true', '-c', 'features.storage.volumes=true', '-c', 'features.networks=false')

    def create_bridge():
        if exists(api('/1.0/networks?recursion=1'), bridge):
            if api('/1.0/networks/' + bridge).get('config', {}).get(token) != record['id']:
                raise ValueError('Existing network is not owned by this recovery')
        else:
            ops.incus('network', 'create', bridge, token + '=' + record['id'], 'ipv4.address=none',
                      'ipv6.address=none', 'ipv4.nat=false', 'ipv6.nat=false', 'ipv4.routing=false', 'ipv6.routing=false')

    def stage_kit(name):
        destination = rt.STATE / ('recovery-' + record['id']) / name
        archive = source / ('nixlab-' + name + '.tar.gz')
        if destination.exists():
            manifest, entries = recovery.inspect(archive)
            if read_json(destination / 'manifest.json') != manifest or any(
                    (destination / key).is_symlink() or not (destination / key).is_file()
                    or digest(destination / key) != hashlib.sha256(value).hexdigest() for key, value in entries.items()):
                raise ValueError('Staged recovery material changed; no files are overwritten')
        else: recovery.stage(archive, destination)

    def profile(name):
        existing = api('/1.0/profiles?recursion=1&project=' + quote(project, safe=''))
        item = next((p for p in existing if p['name'] == name), None)
        if item is not None and item.get('config', {}).get(token) != record['id']:
            if name != 'default' or item.get('config') or item.get('devices'):
                raise ValueError('Unexpected existing profile; no metadata is overwritten')
        if item is None: ops.incus('profile', 'create', name, '--project', project)
        edit(['profile', 'edit', name, '--project', project],
             {'config': {token: record['id']}, 'devices': {'root': {'type': 'disk', 'path': '/', 'pool': pool}}})

    def volume(item):
        existing = api('/1.0/storage-pools/' + pool + '/volumes/custom?recursion=1&project=' + project)
        current = next((v for v in existing if v['name'] == item['target']), None)
        if current is not None:
            if current.get('config', {}).get(token) != record['id']:
                raise ValueError('Volume import outcome needs operator verification; refusing overwrite or automatic adoption')
        else:
            ops.incus('storage', 'volume', 'import', pool, str(source / item['archive']), item['target'], '--project', project, timeout=43200)
            ops.incus('storage', 'volume', 'set', pool, item['target'], token + '=' + record['id'], '--project', project)

    def guest(item):
        existing = api('/1.0/instances?recursion=1&project=' + project)
        current = next((g for g in existing if g['name'] == item['target']), None)
        if current is None:
            overrides = ['--config', 'boot.autostart=false', '--config', ops.OWNER + '=false',
                         '--config', ops.LAST + '=', '--config', 'user.nixlab.restore-review=required',
                         '--config', token + '=' + record['id']]
            for name, device in item['originalDevices'].items():
                if device.get('type') == 'nic':
                    for key, value in (('network', bridge), ('ipv4.address', ''), ('ipv6.address', '')):
                        overrides += ['--device', name + ',' + key + '=' + value]
                elif device.get('type') == 'disk' and device.get('path') != '/':
                    for key in ('pool', 'source'):
                        overrides += ['--device', name + ',' + key + '=' + item['devices'][name][key]]
            ops.incus('import', str(source / item['archive']), item['target'], '--project', project, '--storage', pool, *overrides, timeout=43200)
            current = api(guest_path(project, item['target']))
        if current.get('config', {}).get(token) != record['id'] or current.get('status') != 'Stopped':
            raise ValueError('Unexpected or running guest; refusing automatic recovery changes')
        config = current['config'].copy()
        config.update({'boot.autostart': 'false', 'snapshots.schedule': '', 'user.nixlab.restore-review': 'required'})
        for key in list(config):
            if re.fullmatch(r'volatile\.[A-Za-z0-9_.-]+\.hwaddr', key): config.pop(key)
        edit(['config', 'edit', item['target'], '--project', project],
             {'config': config, 'devices': item['devices'], 'profiles': [], 'description': 'Isolated replacement-host recovery'})
        item['uuid'] = api(guest_path(project, item['target']))['config']['volatile.uuid']

    try:
        verify_completed()
        step('configuration-staged', lambda: stage_kit('config'))
        step('credentials-staged', lambda: stage_kit('secrets'))
        step('project', create_project)
        step('isolated-bridge', create_bridge)
        for name in record['profiles']: step('profile/' + name, lambda name=name: profile(name))
        for item in record['volumes']: step('volume/' + item['target'], lambda item=item: volume(item))
        for item in record['guests']: step('guest/' + item['target'], lambda item=item: guest(item))
        record.update(phase='isolated', durationSeconds=time.time() - record['created'], error=None)
        save(record)
    except (ValueError, OSError, subprocess.SubprocessError):
        record.update(phase='blocked', error='Recovery stopped safely; inspect the running checkpoint and destination before resuming')
        save(record)
        raise
    return status(record)


def release(confirmed=False, data_verified=False, acknowledge_exclusions=False):
    if not confirmed or not data_verified:
        raise ValueError('Release requires --confirm and --data-verified after an independent application-data drill')
    record = read_json(journal())
    if record['phase'] != 'isolated': raise ValueError('Complete isolated imports before release')
    if not record['coverage'].get('complete') and not acknowledge_exclusions:
        raise ValueError('Coverage is incomplete or unknown; review it and explicitly --acknowledge-exclusions for a selected-data recovery')
    if not DESIRED.is_file(): raise ValueError('Evaluate the reviewed desired-instance inventory before release')
    desired = rt.read_json(DESIRED)
    if not isinstance(desired, list): raise ValueError('Invalid desired-instance inventory')
    restored = {(item['project'], item['name']) for item in record['guests']}
    if any((item['project'], item['name']) not in restored for item in desired if not item.get('manageExistingOnly')):
        raise ValueError('Desired configuration would create empty guests; reconcile the source inventory before release')
    for item in record['guests']:
        # Cutover is deliberately performed in native Incus after hardware and
        # network review. Never reconnect original addresses automatically.
        current = api(guest_path(item['project'], item['name']))
        if (current.get('status') != 'Stopped' or current.get('config', {}).get('volatile.uuid') != item.get('uuid')
                or current.get('config', {}).get('user.nixlab.recovery-id') != record['id']):
            raise ValueError('Move verified guests to their reviewed original project/name, stopped, before release')
    record.update(phase='complete', restore='operator-attested' if record['coverage'].get('complete') else 'operator-attested-selected', verifiedAt=time.time(),
                  durationSeconds=time.time() - record['created'])
    save(record)
    return status(record)


def cli(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='nixlab-admin recovery')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('plan'); create.add_argument('generation', type=Path); create.add_argument('--mappings', type=Path, required=True)
    commands.add_parser('status')
    apply = commands.add_parser('resume'); apply.add_argument('--confirm', action='store_true')
    finish = commands.add_parser('release'); finish.add_argument('--confirm', action='store_true'); finish.add_argument('--data-verified', action='store_true'); finish.add_argument('--acknowledge-exclusions', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'status': result = status()
    else:
        with rt.locked(blocking=False), open_lock(ops.INCUS_LOCK) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.command == 'plan': result = plan(args.generation, read_json(args.mappings))
            elif args.command == 'resume': result = resume(args.confirm)
            else: result = release(args.confirm, args.data_verified, args.acknowledge_exclusions)
    print(json.dumps(result, indent=2))
