"""Offline exports with durable restart intent and atomic replacement.
All running guests are stopped together, including for custom-volume consistency.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import quote
from locking import open_lock

ROOT = Path(os.environ.get('NIXLAB_EXPORT_DIRECTORY', '/var/lib/nixlab/exports'))
PENDING = Path('/var/lib/nixlab/restart-pending.json')

def incus(*args):
    result = subprocess.run(['incus', *args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Incus exited {result.returncode}")
    return result.stdout

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

def export(args, filename):
    fd, temporary = tempfile.mkstemp(prefix='.export-', suffix='.tar.gz', dir=ROOT)
    os.close(fd)
    Path(temporary).unlink() # Incus creates the export path itself
    try:
        incus(*args, temporary, '--compression', 'gzip')
        if Path(temporary).stat().st_size == 0:
            raise RuntimeError('Export is empty')
        os.chmod(temporary, 0o600)
        os.replace(temporary, ROOT / filename)
    finally:
        Path(temporary).unlink(missing_ok=True)

def backup():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    recover()
    projects = json.loads(incus('project', 'list', '--format=json'))
    guests = []
    volumes = []
    profiles, networks = {}, {}
    trusted = json.loads(incus('config', 'trust', 'list', '--format=json'))
    server = query('/1.0').get('config', {})
    pools = json.loads(incus('storage', 'list', '--format=json'))
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
                    volumes.append({'project': project['name'], 'pool': pool['name'], 'name': volume['name'],
                                    'archive': artifact('volume', project['name'], pool['name'], volume['name'])})
    # Application-consistent dump hooks happen while guests still run. Failures abort before stopping.
    if os.environ.get('NIXLAB_DEMO_BACKUP') == '1':
        incus('exec', 'apps', '--project', 'lab', '--', 'nixlab-project', 'backup', 'demo')
    pending = [{'project': g['project'], 'name': g['name']} for g in guests if g['status'] == 'Running']
    save_pending(pending)
    try:
        for guest in pending:
            # No --force: a failed graceful stop aborts the backup and restarts stopped guests.
            incus('stop', guest['name'], '--project', guest['project'], '--timeout', '180')
        manifest = {'guests': guests, 'projects': projects, 'pools': pools, 'customVolumes': volumes,
                    'profiles': profiles, 'networks': networks, 'trustedCertificates': trusted, 'serverConfiguration': server}
        expected = {'inventory.json'}
        for guest in guests:
            filename = guest['archive']
            export(['export', guest['name'], '--project', guest['project']], filename)
            expected.add(filename)
        for volume in volumes:
            filename = volume['archive']
            export(['storage', 'volume', 'export', volume['pool'], volume['name'], '--project', volume['project']], filename)
            expected.add(filename)
        checksums = {}
        for filename in expected - {'inventory.json'}:
            digest = hashlib.sha256()
            with (ROOT / filename).open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
            checksums[filename] = digest.hexdigest()
        manifest['archiveSHA256'] = checksums
        fd, temporary = tempfile.mkstemp(dir=ROOT, prefix='.inventory-')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(manifest, stream, indent=2); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, ROOT / 'inventory.json')
        finally:
            Path(temporary).unlink(missing_ok=True)
        # Retire stale local exports only after a complete export set exists.
        # Historical backups remain governed by restic retention.
        for f in ROOT.glob('*.tar.gz'):
            if f.name not in expected:
                f.unlink()
    finally:
        recover()

if __name__ == '__main__':
    os.umask(0o077)
    with open_lock('/run/lock/nixlab-incus.lock') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            recover() if '--recover' in sys.argv else backup()
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)
