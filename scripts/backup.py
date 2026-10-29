"""Publish complete offline backup generations with durable restart intent.

Only members of one consistency group stop together. Incomplete generations are
never published and the previous complete generations remain independently usable.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import quote
from locking import open_lock
import incus_client

ROOT = Path(os.environ.get('NIXLAB_EXPORT_DIRECTORY', '/var/lib/nixlab/exports'))
PENDING = Path('/var/lib/nixlab/restart-pending.json')

def incus(*args):
    return incus_client.run(args, 43200, RuntimeError, 'Incus exited {code}')

def query(path):
    return json.loads(incus('query', path))

def save_pending(pending):
    temporary = PENDING.with_suffix('.tmp')
    temporary.write_text(json.dumps(pending))
    os.chmod(temporary, 0o600)
    with temporary.open('rb') as f:
        os.fsync(f.fileno())
    os.replace(temporary, PENDING)
    directory = os.open(PENDING.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)

def recover():
    pending = json.loads(PENDING.read_text()) if PENDING.exists() else []
    failed = []
    for guest in pending:
        try:
            state = query('/1.0/instances/' + quote(guest['name'], safe='') + '/state?project=' + quote(guest['project'], safe=''))
            if state['status'] == 'Stopped':
                incus('start', guest['name'], '--project', guest['project'])
            elif state['status'] != 'Running':
                raise RuntimeError('Guest is neither stopped nor running')
        except Exception as exc:
            print(f"Recovery failed for {guest['project']}/{guest['name']}: {exc}", file=sys.stderr)
            failed.append(guest)
    save_pending(failed)
    if failed:
        raise RuntimeError('Some formerly running guests could not be restarted; see restart-pending.json')

def artifact(kind, *identity):
    digest = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    return f"{kind}-{digest}.tar.gz"

def export(args, filename, directory=None):
    destination = Path(directory or ROOT)
    fd, temporary = tempfile.mkstemp(prefix='.export-', suffix='.tar.gz', dir=destination)
    os.close(fd)
    Path(temporary).unlink() # Incus creates the export path itself
    try:
        incus(*args, temporary, '--compression', 'gzip')
        if Path(temporary).stat().st_size == 0:
            raise RuntimeError('Export is empty')
        os.chmod(temporary, 0o600)
        with open(temporary, 'rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, destination / filename)
    finally:
        Path(temporary).unlink(missing_ok=True)

def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def progress(phase, **fields):
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(PENDING.with_name('backup-status.json'), {'phase': phase, 'updated': time.time(), **fields})


def volume_identity(project, pool, source):
    return (project, pool, source.removeprefix('custom/'))


def consistency_groups(guests, volumes, configured, namespaces=None):
    """Connected components include all consumers of shared writable volumes."""
    by_id = {(g['project'], g['name']): g for g in guests}
    parents = {key: key for key in by_id}
    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]; key = parents[key]
        return key
    def join(keys):
        keys = list(keys)
        for key in keys[1:]: parents[find(key)] = find(keys[0])
    consumers, writable = {}, set()
    for key, guest in by_id.items():
        for device in guest.get('expanded_devices', guest.get('devices', {})).values():
            if device.get('type') != 'disk' or device.get('path') == '/' or not device.get('pool') or not device.get('source'):
                continue
            identity = volume_identity((namespaces or {}).get(key[0], key[0]), device['pool'], device['source'])
            consumers.setdefault(identity, set()).add(key)
            if device.get('readonly') != 'true': writable.add(identity)
    for identity in writable: join(consumers[identity])
    for group in configured:
        keys = [tuple(member.split('/', 1)) for member in group]
        if any(key not in by_id for key in keys): raise ValueError('Backup group references an absent project/guest')
        join(keys)
    result = {}
    for key, guest in by_id.items():
        result.setdefault(find(key), {'guests': [], 'volumes': []})['guests'].append(guest)
    standalone = []
    for volume in volumes:
        identity = volume_identity(volume['project'], volume['pool'], volume['name'])
        keys = consumers.get(identity, set())
        if keys and identity in writable:
            result[find(sorted(keys)[0])]['volumes'].append(volume)
        else:
            # A volume with only read-only consumers has no guest writer to stop.
            # Export it independently so excluding one reader cannot hide data
            # needed by another selected guest.
            standalone.append(volume)
    groups = list(result.values())
    if standalone: groups.append({'guests': [], 'volumes': standalone})
    return groups


@contextlib.contextmanager
def cancellable():
    """systemd stop and Ctrl-C unwind the same restart-recovery finally blocks."""
    def cancel(_signum, _frame): raise InterruptedError('Backup cancelled; recovering guests')
    previous = {s: signal.signal(s, cancel) for s in (signal.SIGTERM, signal.SIGINT)}
    try: yield
    finally:
        for s, handler in previous.items(): signal.signal(s, handler)


def backup(with_recovery=False):
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if ROOT.resolve() != ROOT.absolute() or ROOT.stat().st_uid != os.geteuid() or ROOT.stat().st_mode & 0o022:
        raise ValueError('Backup staging must be owned by root and cannot traverse symlinks or be writable by others')
    recover()
    progress('inventory')
    projects = json.loads(incus('project', 'list', '--format=json'))
    guests = []
    volumes = []
    profiles, networks = {}, {}
    trusted = json.loads(incus('config', 'trust', 'list', '--format=json'))
    server = query('/1.0').get('config', {})
    pools = json.loads(incus('storage', 'list', '--format=json'))
    namespaces = {p['name']: p['name'] if p.get('config', {}).get('features.storage.volumes') == 'true' else 'default' for p in projects}
    seen_volumes = set()
    for project in projects:
        profiles[project['name']] = json.loads(incus('profile', 'list', '--project', project['name'], '--format=json'))
        networks[project['name']] = json.loads(incus('network', 'list', '--project', project['name'], '--format=json'))
        for guest in json.loads(incus('list', '--project', project['name'], '--format=json')):
            guest['project'] = project['name']
            if guest['status'] not in ('Running', 'Stopped'):
                raise RuntimeError('Refusing backup of frozen/error-state guest: ' + guest['name'])
            guest['archive'] = artifact('instance', project['name'], guest['name'])
            guests.append(guest)
        for pool in pools:
            for volume in json.loads(incus('storage', 'volume', 'list', pool['name'], '--project', project['name'], '--format=json')):
                if volume['type'] == 'custom':
                    namespace = namespaces[project['name']]
                    identity = (namespace, pool['name'], volume['name'])
                    if identity in seen_volumes: continue
                    seen_volumes.add(identity)
                    volumes.append({'project': namespace, 'pool': pool['name'], 'name': volume['name'],
                                    'archive': artifact('volume', namespace, pool['name'], volume['name'])})
    settings_file = os.environ.get('NIXLAB_BACKUP_POLICY')
    policy = json.loads(Path(settings_file).read_text()) if settings_file else {}
    groups = consistency_groups(guests, volumes, policy.get('groups', []), namespaces)
    excluded = set(policy.get('exclude', []))
    unknown = excluded - {g['project'] + '/' + g['name'] for g in guests}
    if unknown: raise ValueError('Backup exclusion references an absent project/guest: ' + ', '.join(sorted(unknown)))
    skipped, selected = [], []
    for group in groups:
        members = [g['project'] + '/' + g['name'] for g in group['guests']]
        protected = any(member in excluded or g.get('expanded_config', g.get('config', {})).get('user.nixlab.backup') == 'manual'
                        for member, g in zip(members, group['guests']))
        if protected:
            skipped.extend(members)
        else: selected.append(group)
    selected_guests = [g for group in selected for g in group['guests']]
    selected_volumes = [v for group in selected for v in group['volumes']]
    if os.environ.get('NIXLAB_DEMO_BACKUP') == '1' and any(g['project'] == 'lab' and g['name'] == 'apps' for g in selected_guests):
        incus('exec', 'apps', '--project', 'lab', '--', 'nixlab-project', 'backup', 'demo')
    reserve = policy.get('reserveMiB', 1024) * 1024**2
    if shutil.disk_usage(ROOT).free < reserve:
        raise ValueError('Insufficient free backup staging space; no guests stopped')
    generation = str(time.time_ns()) + '-' + uuid.uuid4().hex
    incomplete = ROOT / '.incomplete'; incomplete.mkdir(mode=0o700, exist_ok=True)
    published = ROOT / 'generations'; published.mkdir(mode=0o700, exist_ok=True)
    if incomplete.is_symlink() or published.is_symlink(): raise ValueError('Backup generation directories cannot be symlinks')
    destination = incomplete / generation
    destination.mkdir(mode=0o700)
    try:
        windows = []
        for index, group in enumerate(selected):
            members = [g['project'] + '/' + g['name'] for g in group['guests']]
            pending = [{'project': g['project'], 'name': g['name']} for g in group['guests'] if g['status'] == 'Running']
            save_pending(pending)
            started = time.time()
            try:
                progress('exporting', generation=generation, group=index + 1, groups=len(selected), members=members)
                for guest in pending:
                    incus('stop', guest['name'], '--project', guest['project'], '--timeout', '180')
                for guest in group['guests']:
                    export(['export', guest['name'], '--project', guest['project']], guest['archive'], destination)
                for volume in group['volumes']:
                    export(['storage', 'volume', 'export', volume['pool'], volume['name'], '--project', volume['project']], volume['archive'], destination)
                windows.append({'members': members, 'started': started, 'finished': time.time()})
            finally:
                progress('recovering', generation=generation, members=members)
                recover()
        manifest = {'format': 2, 'generation': generation, 'created': time.time(), 'consistencyGroups': windows,
                    'excluded': skipped, 'coverage': 'selected' if skipped else 'all',
                    'guests': selected_guests, 'projects': projects, 'pools': pools, 'customVolumes': selected_volumes,
                    'profiles': profiles, 'networks': networks, 'trustedCertificates': trusted, 'serverConfiguration': server}
        if with_recovery:
            tool = os.environ['NIXLAB_RECOVERY_TOOL']
            subprocess.run([tool, 'recovery-backup', str(destination)], check=True)
        checksums = {}
        for path in sorted(destination.glob('*.tar.gz')):
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
                os.fsync(stream.fileno())
            checksums[path.name] = digest.hexdigest()
        manifest['archiveSHA256'] = checksums
        atomic_json(destination / 'inventory.json', manifest)
        os.rename(destination, published / generation)
        sync_directory(incomplete); sync_directory(published)
        atomic_json(ROOT / 'latest.json', {'format': 2, 'generation': generation})
        # Only complete generation directories are eligible; legacy files stay put.
        complete = sorted((p for p in published.iterdir() if p.is_dir() and not p.is_symlink()
                           and re.fullmatch(r'[0-9]+-[0-9a-f]{32}', p.name) and (p / 'inventory.json').is_file()), reverse=True)
        for path in complete[2:]: shutil.rmtree(path)
        progress('complete-with-exclusions' if skipped else 'complete', generation=generation,
                 guests=len(selected_guests), groups=len(selected), excluded=skipped)
        return published / generation
    finally:
        recover()
        if destination.exists(): shutil.rmtree(destination)


def generations(root):
    """Public summaries only. Inventory can contain guest credentials/configuration."""
    root = Path(root)
    result = []
    directory = root / 'generations'
    if directory.resolve() != directory.absolute() or not directory.is_dir(): return result
    for path in sorted(directory.iterdir(), reverse=True):
        if not re.fullmatch(r'[0-9]+-[0-9a-f]{32}', path.name) or path.is_symlink(): continue
        inventory = path / 'inventory.json'
        if inventory.is_symlink() or not inventory.is_file(): continue
        try:
            data = json.loads(inventory.read_text())
            result.append({'id': path.name, 'created': data['created'], 'coverage': data.get('coverage', 'all'),
                           'excluded': data.get('excluded', []), 'guests': len(data['guests']),
                           'archives': len(data['archiveSHA256'])})
        except (OSError, ValueError, KeyError, TypeError): continue
    return result

if __name__ == '__main__':
    os.umask(0o077)
    with open_lock('/run/lock/nixlab-incus.lock') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with cancellable():
                recover() if '--recover' in sys.argv else backup('--with-recovery' in sys.argv)
        except Exception as exc:
            progress('failed', error=str(exc))
            print(str(exc), file=sys.stderr)
            sys.exit(1)
