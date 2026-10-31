"""Portable configuration kits, separately encrypted secrets and bounded diagnostics."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import time
import runtime as rt
import upgrades

UNITS = ['incus.service', 'incus-preseed.service', 'nixlab-kvm-check.service', 'nixlab-instances.service',
         'cockpit.socket', 'cockpit.service', 'nginx.service',
         'nixlab-admin-backend.service', 'nixlab-ui.service', 'nixlab-apps-onboarding.service',
         'restic-backups-nixlab.service', 'nixlab-storage.target', 'nixlab-rollback-guard.service', 'smartd.service']


def health():
    result = {'release': upgrades.metadata(rt.ROOT), 'services': [], 'certificates': {}, 'lastBackupSuccess': None}
    for unit in UNITS:
        process = subprocess.run(['systemctl', 'show', unit, '--property=Id,LoadState,ActiveState,SubState,Result,ExecMainStatus'],
                                 capture_output=True, text=True, timeout=10)
        result['services'].append(dict(line.split('=', 1) for line in process.stdout.splitlines() if '=' in line))
    for filename in ('tls.crt', 'apps-tls.crt', 'ca.crt'):
        path = rt.STATE / filename
        if not path.is_file():
            result['certificates'][filename] = {'present': False}; continue
        end = subprocess.run(['openssl', 'x509', '-in', str(path), '-noout', '-enddate'], capture_output=True, text=True, timeout=10)
        soon = subprocess.run(['openssl', 'x509', '-in', str(path), '-noout', '-checkend', '2592000'], capture_output=True, timeout=10)
        result['certificates'][filename] = {'present': True, 'expires': end.stdout.strip(), 'expiresWithin30Days': bool(soon.returncode)}
    success = Path('/var/lib/nixlab/last-backup-success')
    if success.is_file():
        value = success.read_text().strip()
        if value.isdigit(): result['lastBackupSuccess'] = int(value)
    pending = rt.read_json(Path('/var/lib/nixlab/restart-pending.json'), [])
    result['guestsAwaitingRestart'] = len(pending)
    try:
        filesystem = os.statvfs('/')
        result['rootStorage'] = {'totalBytes': filesystem.f_blocks * filesystem.f_frsize,
                                 'availableBytes': filesystem.f_bavail * filesystem.f_frsize}
    except OSError:
        result['rootStorage'] = None
    status = rt.read_json(Path('/var/lib/nixlab/backup-status.json'), {})
    result['backup'] = {key: status[key] for key in ('phase', 'updated', 'generation', 'group', 'groups', 'members', 'excluded') if key in status}
    import backup_health
    settings = rt.read_json(Path('/etc/nixlab/effective-settings.json'), rt.read_json(rt.machine_path(rt.ROOT, 'settings'), {}))
    result['backup']['health'] = backup_health.status(settings.get('backup', {}).get('enable', False))
    # No arbitrary journal or command output is collected: either can contain
    # bootstrap passwords, URLs with tokens, application data or private material.
    return result


def secret_files():
    files = {}
    for name in ('auth.json', 'users.json', 'ca.key', 'ca.crt', 'tls.key', 'tls.crt', 'apps-tls.key', 'apps-tls.crt'):
        files['admin/' + name] = rt.STATE / name
    for name, path in rt.SECRET_PATHS.items(): files['runtime/' + name] = path
    settings = rt.read_json(Path('/etc/nixlab/effective-settings.json'), rt.read_json(rt.machine_path(rt.ROOT, 'settings'), {}))
    for section, key in (('backup', 'passwordFile'), ('backup', 'environmentFile'), ('tunnel', 'credentialsFile')):
        value = settings.get(section, {}).get(key)
        if value: files['runtime/' + section + '-' + key] = Path(value)
    notification = rt.read_json(Path('/etc/nixlab/notification-settings.json'), {})
    if notification.get('webhookFile'): files['runtime/notification-url'] = Path(notification['webhookFile'])
    key = Path(settings.get('secrets', {}).get('ageKeyFile', '/var/lib/sops-nix/key.txt'))
    if key.is_file(): files['sops/age-key.txt'] = key
    for path in Path('/var/lib/nixlab-secrets').rglob('*'):
        if path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)):
            files['runtime-files/' + str(path.relative_to('/var/lib/nixlab-secrets'))] = path
    for parent in (Path('/etc/NetworkManager/system-connections'), Path('/run/NetworkManager/system-connections')):
        for path in parent.glob('*.nmconnection'):
            files['network/' + path.name] = path
    return {n: p for n, p in files.items() if p.is_file() and not p.is_symlink()}


def write_archive(stream, files, kind):
    manifest = {'format': 1, 'kind': kind, 'created': time.time(), 'release': upgrades.metadata(rt.ROOT), 'files': {}}
    with tarfile.open(fileobj=stream, mode='w|gz') as archive:
        total = 0
        if len(files) >= 10000: raise ValueError('Recovery kit has too many files')
        for name, path in sorted(files.items()):
            if not path.is_file() or path.is_symlink(): raise ValueError('Recovery source changed or is a symlink')
            if path.stat().st_size > 16 * 1024**2: raise ValueError('Recovery member exceeds 16 MiB')
            with path.open('rb') as source: data = source.read(16 * 1024**2 + 1)
            total += len(data)
            if len(data) > 16 * 1024**2 or total > 100 * 1024**2: raise ValueError('Recovery kit exceeds size limits')
            manifest['files'][name] = hashlib.sha256(data).hexdigest()
            item = tarfile.TarInfo(name); item.size = len(data); item.mode = 0o600
            archive.addfile(item, io.BytesIO(data))
        body = json.dumps(manifest, indent=2).encode()
        item = tarfile.TarInfo('manifest.json'); item.size = len(body); item.mode = 0o600
        archive.addfile(item, io.BytesIO(body))


def export(directory, recipient=None, include_secrets=False):
    if include_secrets and (not recipient or not re.fullmatch(r'age1[0-9a-z]{50,100}', recipient)):
        raise ValueError('Supply an age public recipient for the separate secret archive')
    directory = Path(directory).absolute()
    if directory.resolve() != directory:
        raise ValueError('Recovery destination cannot traverse symlinks')
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    if directory.stat().st_uid != os.geteuid() or directory.stat().st_mode & 0o022:
        raise ValueError('Recovery destination must be owned by root and not writable by other users')
    config = directory / 'nixlab-config.tar.gz'
    files = {'config/' + n: p for n, p in rt.source_files(rt.ROOT) if n != '.nixlab-files.json'}
    stream = io.BytesIO(); write_archive(stream, files, 'configuration')
    rt.atomic(config, stream.getvalue())
    result = {'configuration': str(config), 'sha256': hashlib.sha256(config.read_bytes()).hexdigest()}
    if include_secrets:
        output = directory / 'nixlab-secrets.tar.gz.age'
        output.unlink(missing_ok=True)
        process = subprocess.Popen(['age', '--encrypt', '--recipient', recipient, '--output', str(output)], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            write_archive(process.stdin, secret_files(), 'secrets')
            process.stdin.close()
            error = process.stderr.read()
            if process.wait(timeout=60): raise ValueError('Secret encryption failed: ' + error.decode(errors='replace')[-1000:])
            output.chmod(0o600)
        except Exception:
            if process.poll() is None: process.kill(); process.wait()
            output.unlink(missing_ok=True)
            raise
        finally:
            process.stderr.close()
        result['encryptedSecrets'] = str(output)
    return result


def backup(directory):
    """Called under the backup prepare lock. Restic encrypts these root-only files off-host."""
    directory = Path(directory)
    result = export(directory)
    secrets = directory / 'nixlab-secrets.tar.gz'
    stream = io.BytesIO(); write_archive(stream, secret_files(), 'secrets')
    rt.atomic(secrets, stream.getvalue())
    return result


def inspect(archive):
    entries = {}
    with tarfile.open(archive, 'r:gz') as stream:
        total = 0
        for item in stream:
            path = PurePosixPath(item.name)
            total += item.size
            if (not item.isfile() or not path.parts or str(path) != item.name or path.is_absolute() or '..' in path.parts or item.name in entries
                    or len(entries) >= 10000 or total > 100 * 1024**2):
                raise ValueError('Recovery kit contains unsafe paths, types, duplicates or excessive data')
            if item.name != 'manifest.json' and path.parts[0] not in {'config', 'admin', 'runtime', 'runtime-files', 'sops', 'network'}:
                raise ValueError('Unexpected recovery member')
            entries[item.name] = stream.extractfile(item).read()
    if 'manifest.json' not in entries: raise ValueError('Recovery manifest missing')
    manifest = json.loads(entries.pop('manifest.json'))
    if manifest.get('format') != 1 or manifest.get('kind') not in {'configuration', 'secrets'}:
        raise ValueError('Unsupported recovery kit format')
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()}
    if hashes != manifest.get('files'): raise ValueError('Recovery kit checksum mismatch')
    return manifest, entries


def stage(archive, destination):
    destination = Path(destination).absolute()
    if destination.resolve() != destination: raise ValueError('Recovery staging cannot traverse symlinks')
    if destination.exists() or destination.is_symlink(): raise ValueError('Recovery staging destination must not exist')
    manifest, entries = inspect(archive)
    destination.mkdir(mode=0o700, parents=True)
    for name, data in entries.items(): rt.atomic(destination / name, data)
    rt.write_json(destination / 'manifest.json', manifest)
    return manifest


def download(recipient=None):
    with rt.locked(blocking=False), tempfile.TemporaryDirectory(dir=rt.STATE, prefix='recovery-') as folder:
        result = export(folder, recipient, bool(recipient))
        files = {Path(result['configuration']).name: base64.b64encode(Path(result['configuration']).read_bytes()).decode()}
        if recipient:
            path = Path(result['encryptedSecrets']); files[path.name] = base64.b64encode(path.read_bytes()).decode()
            envelope = io.BytesIO()
            with tarfile.open(fileobj=envelope, mode='w') as archive:
                for name, encoded in files.items():
                    body = base64.b64decode(encoded)
                    item = tarfile.TarInfo(name); item.size = len(body); item.mode = 0o600
                    archive.addfile(item, io.BytesIO(body))
            files = {'nixlab-recovery-kit.tar': base64.b64encode(envelope.getvalue()).decode()}
        return {'files': files}


def cli(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='nixlab-admin recovery')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('export'); create.add_argument('directory', type=Path)
    create.add_argument('--recipient'); create.add_argument('--include-secrets', action='store_true')
    verify = commands.add_parser('inspect'); verify.add_argument('archive', type=Path)
    restore = commands.add_parser('stage'); restore.add_argument('archive', type=Path); restore.add_argument('destination', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'export':
        with rt.locked(blocking=False): result = export(args.directory, args.recipient, args.include_secrets)
    elif args.command == 'inspect': result = inspect(args.archive)[0]
    else: result = stage(args.archive, args.destination)
    print(json.dumps(result, indent=2))
