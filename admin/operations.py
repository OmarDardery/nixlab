"""Allowlisted Incus operations and durable jobs; no browser shell or arbitrary paths."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from urllib.parse import quote
import runtime as rt
from locking import open_lock
import incus_client

OWNER = 'user.nixlab.managed'
LAST = 'user.nixlab.last-applied'
ACTIONS = {'start', 'stop', 'restart', 'snapshot', 'snapshot-copy', 'export', 'restore', 'backup', 'backup-check', 'release-restore'}
INCUS_LOCK = Path('/run/lock/nixlab-incus.lock')


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}', value):
        raise ValueError('Use a local project/instance/snapshot name without remote prefixes or paths')
    return value


def incus(*args, timeout=60):
    return incus_client.run(args, timeout, ValueError, 'Incus operation failed', limit=4096)


def guests(project='lab'):
    project = name(project)
    items = json.loads(incus('list', '--project', project, '--format=json'))
    result = []
    for guest in items:
        addresses = [a['address'] for network in (guest.get('state') or {}).get('network', {}).values()
                     for a in network.get('addresses', []) if a.get('scope') == 'global']
        config = guest.get('expanded_config', guest.get('config', {}))
        drift = ''
        if guest.get('config', {}).get(OWNER) == 'true':
            try:
                declared = rt.read_json(Path('/etc/nixlab/desired-instances.json'), [])
                spec = next((d for d in declared if d['project'] == project and d['name'] == guest['name']), None)
                if spec:
                    import reconcile
                    if reconcile.plan(guest, spec) is not None: drift = 'Declared fields differ from runtime state'
            except (ValueError, TypeError, KeyError) as exc:
                drift = str(exc)
        result.append({'name': guest['name'], 'project': project, 'type': guest['type'], 'status': guest['status'],
                       'addresses': addresses, 'managed': config.get(OWNER) == 'true',
                       'restoreReview': config.get('user.nixlab.restore-review') == 'required',
                       'cpu': config.get('limits.cpu', ''), 'memory': config.get('limits.memory', ''), 'drift': drift,
                       'snapshots': [s['name'].rsplit('/', 1)[-1] for s in guest.get('snapshots', [])]})
    return result


def instance(project, guest):
    return json.loads(incus('query', '/1.0/instances/' + quote(name(guest), safe='') + '?project=' + quote(name(project), safe='')))


def reset_restore_macs(project, guest):
    """New-name restores get new generated NIC identities; declared MACs need review."""
    current = instance(project, guest)
    if current.get('status') != 'Stopped':
        raise ValueError('The restored instance must remain stopped while resetting generated NIC identities')
    for key in sorted(current.get('config', {})):
        if re.fullmatch(r'volatile\.[A-Za-z0-9_.-]+\.hwaddr', key):
            incus('config', 'unset', guest, key, '--project', project)


def mac_addresses(current):
    config = current.get('expanded_config', current.get('config', {}))
    values = [v for k, v in config.items() if re.fullmatch(r'volatile\.[A-Za-z0-9_.-]+\.hwaddr', k)]
    values += [d['hwaddr'] for d in current.get('expanded_devices', {}).values() if d.get('type') == 'nic' and d.get('hwaddr')]
    return {v.lower() for v in values if isinstance(v, str) and v}


def check_config_idle(state):
    active = rt.read_json(state / 'active.json', {})
    job = rt.read_json(state / 'jobs' / active.get('id', '-') / 'job.json', {})
    if rt.read_json(state / 'pending.json') or job.get('status') in ('building', 'applying', 'awaiting-confirmation', 'rolling-back'):
        raise ValueError('Finish the configuration transaction before changing guest state')


class Operations:
    def __init__(self, root=None, state=None):
        self.root, self.state = Path(root or rt.ROOT), Path(state or rt.STATE)
        self.directory = self.state / 'operations'
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)

    def path(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise ValueError('Invalid operation ID')
        directory = self.directory / job_id
        if not directory.is_dir(): raise ValueError('Unknown operation')
        return directory

    def status(self, limit=30):
        records = []
        directories = sorted(self.directory.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
        for directory in directories if limit is None else directories[:limit]:
            if not directory.is_dir() or not re.fullmatch(r'[0-9a-f]{32}', directory.name): continue
            record = rt.read_json(directory / 'job.json', {})
            if record.get('status') in ('queued', 'running') and time.time() - record.get('created', 0) > 30:
                unit = subprocess.run(['systemctl', 'is-active', '--quiet', 'nixlab-operation-' + directory.name], capture_output=True)
                if unit.returncode:
                    record.update(status='failed', error='Worker interrupted. Inspect guest state and recovery intent before retrying')
                    rt.write_json(directory / 'job.json', record)
            records.append(record)
        return records

    def archives(self):
        result = []
        for job in self.status(limit=None):
            if job.get('action') == 'export' and job.get('status') == 'complete':
                try:
                    path = self.archive_path(job)
                    available = path.is_file() and not path.is_symlink()
                except ValueError:
                    available = False
                result.append({'id': job['id'], 'name': job['name'], 'project': job['project'], 'available': available,
                               'size': job.get('size', 0), 'sha256': job.get('sha256'), 'created': job['created']})
        for job in self.generation_archives():
            try: available = self.archive_path(job).is_file()
            except (OSError, ValueError): available = False
            result.append({key: job.get(key) for key in ('id', 'name', 'project', 'sha256', 'created', 'generation')} | {'available': available})
        return result

    def generation_archives(self):
        import backup
        try: settings = self.archive_settings()
        except (KeyError, TypeError): return []  # Legacy/test state before setup.
        root = Path(settings['archiveRoot']).parent
        result = []
        for generation in backup.generations(root):
            path = root / 'generations' / generation['id']
            manifest = rt.read_json(path / 'inventory.json')
            for guest in manifest.get('guests', []):
                filename = guest.get('archive', '')
                if not re.fullmatch(r'instance-[0-9a-f]{64}\.tar\.gz', filename): continue
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', guest.get('name', '')): continue
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', guest.get('project', '')): continue
                job_id = hashlib.sha256((generation['id'] + '\0' + guest['project'] + '\0' + guest['name']).encode()).hexdigest()[:32]
                result.append({'id': job_id, 'name': guest['name'], 'project': guest['project'],
                               'generation': generation['id'], 'archiveFile': filename,
                               'archiveRoot': str(root), 'archiveMount': settings['archiveMount'],
                               'sha256': manifest['archiveSHA256'].get(filename), 'created': manifest['created']})
        return result

    def archive_path(self, job, create=False):
        if not job.get('archiveRoot'):
            return self.path(job['id']) / 'guest.tar.gz'
        parent = Path(job['archiveRoot'])
        if not parent.is_absolute() or parent.resolve() != parent:
            raise ValueError('Archive storage cannot traverse symlinks')
        mount = job.get('archiveMount')
        if mount:
            actual = subprocess.run(['findmnt', '--mountpoint', mount['path'], '--noheadings', '--output', 'UUID'],
                                    capture_output=True, text=True, timeout=10)
            if actual.returncode or actual.stdout.strip().lower() != mount['uuid'].lower():
                raise ValueError('The selected backup filesystem is missing or has the wrong UUID')
        if create:
            parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            if parent.stat().st_uid != os.geteuid() or parent.stat().st_mode & 0o022:
                raise ValueError('Archive storage must be root-owned and not writable by other users')
        if job.get('generation'):
            if not re.fullmatch(r'[0-9]+-[0-9a-f]{32}', job['generation']) or not re.fullmatch(r'instance-[0-9a-f]{64}\.tar\.gz', job.get('archiveFile', '')):
                raise ValueError('Invalid generation archive identity')
            path = parent / 'generations' / job['generation'] / job['archiveFile']
            if path.resolve() != path: raise ValueError('Generation archive cannot traverse symlinks')
            return path
        return parent / (job['id'] + '.tar.gz')

    def archive_settings(self):
        settings = rt.read_json(Path('/etc/nixlab/effective-settings.json'), rt.read_json(rt.machine_path(self.root, 'settings'), {}))
        staging = settings['backup']['stagingDirectory']
        mount = next((m for m in settings.get('mounts', []) if m['role'] == 'backup'), None)
        return {'archiveRoot': str(Path(staging) / 'on-demand'), 'archiveMount': mount}

    def submit(self, request, principal='root'):
        if not isinstance(request, dict) or set(request) - {'action', 'project', 'name', 'snapshot', 'target', 'archive', 'acknowledge'}:
            raise ValueError('Unknown guest-operation fields')
        action = request.get('action')
        if action not in ACTIONS: raise ValueError('Unsupported guest operation')
        project = name(request.get('project', 'lab'))
        guest = name(request.get('name', 'all') if action in ('backup', 'backup-check') else request.get('name'))
        settings = rt.read_json(Path('/etc/nixlab/effective-settings.json'), rt.read_json(rt.machine_path(self.root, 'settings'), {}))
        if not settings.get('ready'): raise ValueError('Complete host setup before managing guests')
        if action in ('backup', 'backup-check') and not settings['backup']['enable']:
            raise ValueError('Configure an independent backup destination first')
        if action in ('stop', 'restart', 'export', 'restore', 'snapshot-copy', 'release-restore', 'backup') and request.get('acknowledge') is not True:
            raise ValueError('Acknowledge downtime or isolated restore before running this operation')
        parameters = {'action': action, 'project': project, 'name': guest}
        if action in ('snapshot', 'snapshot-copy'): parameters['snapshot'] = name(request.get('snapshot'))
        if action in ('restore', 'snapshot-copy'):
            parameters['target'] = name(request.get('target'))
            if parameters['target'] == guest or (project == 'lab' and parameters['target'] == 'apps'):
                raise ValueError('Restore to a new, unreserved instance name')
        if action == 'restore':
            archive_id = request.get('archive')
            known = next((a for a in self.archives() if a['id'] == archive_id), None)
            if not known or not known['available'] or known['project'] != project or known['name'] != guest:
                raise ValueError('Choose an existing completed export for this project and instance')
            parameters['archive'] = archive_id
            scheduled = next((a for a in self.generation_archives() if a['id'] == archive_id), None)
            if scheduled: parameters['archiveRecord'] = scheduled
        with rt.locked(blocking=False):
            check_config_idle(self.state)
            if any(j.get('status') in ('queued', 'running') for j in self.status(limit=None)):
                raise ValueError('Finish the active guest operation first')
            job_id = secrets.token_hex(16)
            directory = self.directory / job_id
            directory.mkdir(mode=0o700)
            record = {**parameters, 'id': job_id, 'status': 'queued', 'created': time.time(), 'principal': principal}
            if action == 'export': record.update(self.archive_settings())
            rt.write_json(directory / 'job.json', record)
            try:
                subprocess.run(['systemd-run', '--quiet', '--collect', '--no-block', '--unit=nixlab-operation-' + job_id,
                                '--property=RuntimeMaxSec=43200', '--setenv=NIXLAB_ROOT=' + str(self.root),
                                '--setenv=NIXLAB_STATE=' + str(self.state), '--setenv=PATH=' + os.environ['PATH'],
                                sys.executable, str(rt.SOURCE / 'main.py'), 'operation-job', job_id],
                               check=True, capture_output=True, text=True)
            except Exception:
                record.update(status='failed', error='Could not start operation worker')
                rt.write_json(directory / 'job.json', record)
                raise
        rt.audit('guest-operation-requested', operation=job_id, actionName=action, project=project, name=guest)
        return {'id': job_id}

    def perform(self, job):
        action, project, guest = job['action'], name(job['project']), name(job['name'])
        if action in ('backup', 'backup-check'):
            unit = 'restic-backups-nixlab.service' if action == 'backup' else 'nixlab-backup-check.service'
            subprocess.run(['systemctl', 'start', unit], check=True, timeout=43200)
            return {}
        if action == 'export':
            import backup
            backup.PENDING.parent.mkdir(parents=True, exist_ok=True)
            backup.recover()
        current = {} if action == 'restore' else instance(project, guest)
        if action in ('start', 'restart') and current.get('config', {}).get('user.nixlab.restore-review') == 'required':
            raise ValueError('Review restored networking, disks and credentials, then release the restore before starting it')
        if action in ('start', 'stop', 'restart'):
            args = [action, guest, '--project', project]
            if action != 'start': args += ['--timeout', '180']
            incus(*args, timeout=240)
        elif action == 'snapshot':
            incus('snapshot', 'create', guest, name(job['snapshot']), '--project', project, timeout=3600)
        elif action in ('restore', 'snapshot-copy'):
            target = name(job['target'])
            if any(g['name'] == target for g in guests(project)):
                raise ValueError('Restore target already exists; no instance is overwritten')
            overrides = ['--config', 'boot.autostart=false', '--config', OWNER + '=false',
                         '--config', LAST + '=', '--config', 'user.nixlab.restore-review=required']
            if action == 'snapshot-copy':
                incus('copy', guest + '/' + name(job['snapshot']), target, '--project', project, '--stateless', *overrides, timeout=43200)
            else:
                archive_job = job.get('archiveRecord') or rt.read_json(self.path(job['archive']) / 'job.json')
                archive = self.archive_path(archive_job)
                if archive.is_symlink() or not archive.is_file() or file_hash(archive) != archive_job.get('sha256'):
                    raise ValueError('Export checksum changed; refusing import')
                incus('import', str(archive), target, '--project', project, *overrides, timeout=43200)
            reset_restore_macs(project, target)
            return {'restoredInstance': target, 'requiresReview': True}
        elif action == 'release-restore':
            # A restored copy remains stopped and autostart-disabled. Fixed IPs
            # must be changed in Incus before it can be released from quarantine.
            addresses = {d['ipv4.address'] for d in current.get('expanded_devices', {}).values() if d.get('ipv4.address')}
            writable = {(d.get('pool', ''), d['source']) for d in current.get('expanded_devices', {}).values()
                        if d.get('type') == 'disk' and d.get('path') != '/' and d.get('source') and d.get('readonly') != 'true'}
            all_instances = json.loads(incus('list', '--project', project, '--format=json'))
            for other in all_instances:
                if other['name'] != guest and mac_addresses(current) & mac_addresses(other):
                    raise ValueError('A restored MAC address conflicts with another instance; change the explicit NIC identity before release')
                if other['name'] != guest and addresses & {d['ipv4.address'] for d in other.get('expanded_devices', {}).values() if d.get('ipv4.address')}:
                    raise ValueError('A restored fixed address conflicts with another instance; change its NIC configuration first')
                volumes = {(d.get('pool', ''), d['source']) for d in other.get('expanded_devices', {}).values()
                           if d.get('type') == 'disk' and d.get('path') != '/' and d.get('source')}
                if other['name'] != guest and writable & volumes:
                    raise ValueError('A writable restored data volume is shared with another instance; clone or detach it before release')
            incus('config', 'set', guest, 'user.nixlab.restore-review=complete', '--project', project)
        elif action == 'export':
            import backup
            directory = self.path(job['id'])
            target = self.archive_path(job, create=True)
            temporary = target.with_suffix('.partial.tar.gz')
            running = current.get('status') == 'Running'
            if current.get('status') not in ('Running', 'Stopped'):
                raise ValueError('Export requires a running or stopped instance')
            backup.save_pending([{'name': guest, 'project': project}] if running else [])
            try:
                if running: incus('stop', guest, '--project', project, '--timeout', '180', timeout=240)
                incus('export', guest, str(temporary), '--project', project, '--compression', 'gzip', timeout=43200)
                if not temporary.is_file() or not temporary.stat().st_size: raise ValueError('Export is empty')
                temporary.chmod(0o600); os.replace(temporary, target)
                return {'sha256': file_hash(target), 'size': target.stat().st_size}
            finally:
                temporary.unlink(missing_ok=True)
                backup.recover()
        return {}

    def register_archive(self, source, project, guest):
        source = Path(source).absolute()
        if source.resolve() != source or not source.is_file() or source.stat().st_size == 0:
            raise ValueError('Select a nonempty regular export file without symlink traversal')
        project, guest = name(project), name(guest)
        with rt.locked(blocking=False):
            check_config_idle(self.state)
            job_id = secrets.token_hex(16)
            directory = self.directory / job_id; directory.mkdir(mode=0o700)
            job = {'id': job_id, 'action': 'export', 'project': project, 'name': guest, 'status': 'importing',
                   'created': time.time(), 'principal': 'root', **self.archive_settings()}
            rt.write_json(directory / 'job.json', job)
            target = self.archive_path(job, create=True)
            temporary = target.with_suffix('.partial.tar.gz')
            try:
                if shutil.disk_usage(target.parent).free < source.stat().st_size + 100 * 1024**2:
                    raise ValueError('Insufficient free staging space for this archive')
                shutil.copyfile(source, temporary); temporary.chmod(0o600)
                digest = file_hash(temporary)
                os.replace(temporary, target)
                job.update(status='complete', completed=time.time(), sha256=digest, size=target.stat().st_size)
            except Exception:
                job.update(status='failed', error='Archive staging failed; original file was not modified')
                raise
            finally:
                temporary.unlink(missing_ok=True)
                rt.write_json(directory / 'job.json', job)
            rt.audit('guest-export-registered', operation=job_id, project=project, name=guest)
            return job

    def run(self, job_id):
        directory = self.path(job_id)
        job = rt.read_json(directory / 'job.json')
        if job.get('status') != 'queued': raise ValueError('Operation was already executed')
        job.update(status='running', started=time.time()); rt.write_json(directory / 'job.json', job)
        try:
            import contextlib
            with (contextlib.nullcontext() if job['action'] in ('backup', 'backup-check') else rt.locked()), open_lock(INCUS_LOCK) as lock:
                # The scheduled backup takes this lock itself. Do not hold it
                # while starting that unit, or it would wait on our own lock.
                if job['action'] not in ('backup', 'backup-check'): fcntl.flock(lock, fcntl.LOCK_EX)
                result = self.perform(job)
            job.update(status='complete', completed=time.time(), **result)
        except Exception as exc:
            job.update(status='failed', completed=time.time(), error=str(exc)[-4096:])
            raise
        finally:
            rt.write_json(directory / 'job.json', job)
            rt.audit('guest-operation-finished', operation=job_id, status=job['status'])

    def recover_interrupted(self):
        import backup
        try:
            with rt.locked(blocking=False), open_lock(INCUS_LOCK) as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for record in self.status(limit=None):
                    # Registration holds the configuration lock for its entire
                    # copy. An importing record observed here is interrupted.
                    if record.get('status') == 'importing':
                        record.update(status='failed', error='Archive registration interrupted; register the original file again')
                        rt.write_json(self.path(record['id']) / 'job.json', record)
                    if record.get('action') == 'export' and record.get('status') == 'failed':
                        try:
                            self.archive_path(record).with_suffix('.partial.tar.gz').unlink(missing_ok=True)
                        except ValueError:
                            pass  # Missing backup disks must never cause fallback writes.
                if backup.PENDING.is_file() and rt.read_json(backup.PENDING, []):
                    backup.recover()
        except (rt.BusyError, BlockingIOError):
            return


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()
