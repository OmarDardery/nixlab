"""Root-owned transaction storage, certificate issuance and fixed secret destinations."""
import base64
import contextlib
import fcntl
import grp
import hashlib
import json
import math
import os
import re
from pathlib import Path
import secrets
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
from locking import open_lock

ROOT = Path(os.environ.get('NIXLAB_ROOT', '/var/lib/nixlab/config'))
STATE = Path(os.environ.get('NIXLAB_STATE', '/var/lib/nixlab-admin'))
SOURCE = Path(__file__).resolve().parent


class PublicError(ValueError):
    """A fixed, reviewed user message, never formatted from input or child output."""


def public_error(error):
    return str(error) if type(error) is PublicError else 'Request rejected; check inputs and authentication, then retry'


SECRET_PATHS = {
    'cockpitPassword': Path('/var/lib/nixlab-secrets/cockpit-operator-password'),
    'wifiPassword': Path('/var/lib/nixlab-secrets/wifi.env'),
    'arcanePassword': Path('/run/nixlab-secrets/apps-arcane-password'),
    'demoPassword': Path('/var/lib/nixlab-secrets/apps-demo-password'),
    'resticPassword': Path('/var/lib/nixlab-secrets/restic-password'),
    'resticEnvironment': Path('/var/lib/nixlab-secrets/restic-environment'),
    'tunnelCredentials': Path('/var/lib/nixlab-secrets/cloudflared.json'),
}
ENV_KEYS = {'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_DEFAULT_REGION',
            'B2_ACCOUNT_ID', 'B2_ACCOUNT_KEY', 'AZURE_ACCOUNT_NAME', 'AZURE_ACCOUNT_KEY',
            'RESTIC_REST_USERNAME', 'RESTIC_REST_PASSWORD'}


def pin_worker_runtime(directory):
    """Keep bootstrap worker code alive after its nix-run launcher disconnects.

    The root belongs to the existing job directory, so normal job retention also
    releases it. Installed admin runtimes use this same harmless lifetime bound.
    """
    launcher = os.environ.get('NIXLAB_WORKER_RUNTIME')
    if launcher and Path(launcher).parent != Path('/nix/store'):
        raise ValueError('Worker runtime must be a Nix store output')
    # A launcher closure also retains its PATH dependencies, not only Python
    # source files. Older installed wrappers fall back to their source output.
    paths = (Path(launcher),) if launcher else (SOURCE, *SOURCE.parents)
    for path in paths:
        if path.parent == Path('/nix/store'):
            subprocess.run(['nix-store', '--add-root', str(directory / 'worker-runtime'),
                            '--indirect', '--realise', str(path)],
                           capture_output=True, check=True, timeout=30)
            break


def atomic(path, data, mode=0o600, group=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data.encode() if isinstance(data, str) else data)
            stream.flush()
            os.chmod(temporary, mode)
            if group is not None:
                try:
                    os.chown(temporary, -1, grp.getgrnam(group).gr_gid)
                except KeyError:
                    if os.environ.get('NIXLAB_TEST') != '1':
                        raise
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, value, **kwargs):
    atomic(path, json.dumps(value, indent=2, sort_keys=True) + '\n', **kwargs)


def durable_unlink(path):
    path = Path(path)
    path.unlink(missing_ok=True)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_json(path, fallback=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else fallback


def job_directory(base, job_id, label='transaction'):
    """Resolve an existing durable job without accepting paths or symlink aliases."""
    if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise ValueError('Invalid ' + label + ' ID')
    directory = Path(base) / job_id
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Unknown ' + label)
    return directory


_JOB_RECORD_LIMIT = 32 * 1024 * 1024
_REQUIRED = object()


@contextlib.contextmanager
def open_job_file(path, *, private=False):
    """Open one regular job file using the directory descriptor for lookup."""
    path = Path(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        # A concurrent atomic replacement may unlink the already-open inode.
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink > 1 or info.st_mode & (0o077 if private else 0o022)):
            raise ValueError
        yield stream, info.st_size


def read_job_record(path, fallback=_REQUIRED):
    """Read a bounded job object without following job or file aliases."""
    try:
        with open_job_file(path) as (stream, size):
            if size > _JOB_RECORD_LIMIT:
                raise ValueError
            raw = stream.read(_JOB_RECORD_LIMIT + 1)
        record = json.loads(raw) if len(raw) <= _JOB_RECORD_LIMIT else None
        if not isinstance(record, dict):
            raise ValueError
        return record
    except FileNotFoundError:
        if fallback is not _REQUIRED:
            return fallback
        raise ValueError('Job record is missing; inspect the transaction from the host console') from None
    except (OSError, ValueError, RecursionError):
        raise ValueError('Job record is invalid or unreadable; inspect the transaction from the host console') from None


def tail_log(path, limit=65536):
    """Read a bounded UTF-8 view without loading a potentially large build log."""
    try:
        with open_job_file(path) as (stream, size):
            stream.seek(max(0, size - limit))
            return stream.read(limit).decode(errors='replace')
    except FileNotFoundError:
        return ''
    except (OSError, ValueError):
        raise ValueError('Job log is invalid or unreadable; inspect the transaction from the host console') from None


def public_progress(text):
    """Project only fixed worker progress, including when reading pre-upgrade logs."""
    fixed = {'Host command started', 'ROLLBACK FAILED: local recovery may be required',
             'FAILED: transaction did not complete'}
    return ''.join(line + '\n' for line in text.splitlines()
                   if line in fixed or re.fullmatch(r'Host command finished with status -?[0-9]{1,3}', line))


def worker_active(unit: str) -> bool:
    """Observe only NixLab workers; unknown systemd state is not stopped work."""
    if not isinstance(unit, str) or not re.fullmatch(r'nixlab-(?:build|apply|rollback|operation|deploy|project-deploy|oci-build)-[0-9a-f]{32}', unit):
        raise ValueError('Invalid worker unit')
    try:
        result = subprocess.run(['systemctl', 'is-active', '--quiet', unit],
                                capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('Worker state unavailable; retry status or inspect systemd from the host console') from None
    # systemctl's LSB codes distinguish inactive/failed (3) and missing (4)
    # from an invocation, permission or transport failure. Never publish stderr.
    if result.returncode not in (0, 3, 4):
        raise ValueError('Worker state unavailable; retry status or inspect systemd from the host console')
    return result.returncode == 0


STAGED_SECRET_LIFETIME = 24 * 60 * 60


@contextlib.contextmanager
def staged_directory(directory):
    """Pin a trusted directory through no-follow lookups of every ancestor."""
    path = Path(directory).absolute()
    if '..' in path.parts:
        raise ValueError('Staged credential storage is not protected')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        info = os.fstat(descriptor)
        if info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022:
            raise ValueError('Staged credential storage is not protected')
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            # A root-owned sticky temporary-directory ancestor is safe: other
            # users cannot replace a privileged user's existing child directory.
            sticky = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in (0, os.geteuid()) or (info.st_mode & 0o022 and not sticky):
                raise ValueError('Staged credential storage is not protected')
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('Staged credential storage is not protected')
        yield descriptor
    finally:
        os.close(descriptor)


def staged_temporary_names(descriptor):
    # Include the eight-character tempfile suffix used before the dedicated
    # staging helper. Never sweep arbitrary hidden files or recovery directories.
    return sorted(name for name in os.listdir(descriptor)
                  if re.fullmatch(r'\.secrets\.json(?:[a-z0-9_]{8}|-[0-9a-f]{32})', name))


def unlink_staged(descriptor, name):
    try:
        os.unlink(name, dir_fd=descriptor)
    except FileNotFoundError:
        pass
    os.fsync(descriptor)


def stage_secrets(directory, values):
    """Publish once, without overwrite; caller holds the mutation lock.

    A crash can leave a private temporary name. The locked guard removes those
    names independently of the committed secret file and transaction recovery.
    """
    if not isinstance(values, dict) or any(key not in SECRET_PATHS or not isinstance(value, str)
                                           for key, value in values.items()):
        raise ValueError('Invalid staged credential fields')
    if not values:
        return
    data = (json.dumps(values, sort_keys=True) + '\n').encode()
    if len(data) > 1024 * 1024:
        raise ValueError('Staged credential input is too large')
    try:
        with staged_directory(directory) as descriptor:
            temporary = '.secrets.json-' + secrets.token_hex(16)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=descriptor)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # link is atomic and refuses an existing file, symlink or other
                # object. Unlike replace it cannot overwrite another publisher.
                os.link(temporary, 'secrets.json', src_dir_fd=descriptor,
                        dst_dir_fd=descriptor, follow_symlinks=False)
                os.fsync(descriptor)
            finally:
                unlink_staged(descriptor, temporary)
    except (OSError, ValueError):
        raise ValueError('Staged credential publication failed; inspect protected transaction storage and retry') from None


def staged_secrets_expired(job, now=None):
    """A fixed review-time deadline; retries and reboot do not renew it."""
    created = job.get('created')
    now = time.time() if now is None else now
    if (type(created) not in (int, float) or not math.isfinite(created) or created <= 0
            or not math.isfinite(now) or now < created):
        raise ValueError('Staged credential age is unknown; check the host clock and review the change again')
    return now - created >= STAGED_SECRET_LIFETIME


def require_fresh_staged_secrets(job):
    if job.get('secretChanges') and staged_secrets_expired(job):
        raise ValueError('Staged credentials expired; review the change again')


def read_staged_secrets(directory, expected):
    """Load only the reviewed keys, from a private regular file."""
    try:
        with staged_directory(directory) as descriptor:
            fd = os.open('secrets.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 1024 * 1024):
                    raise ValueError
                raw = stream.read(1024 * 1024 + 1)
                record = json.loads(raw) if len(raw) <= 1024 * 1024 else None
        if (not isinstance(record, dict) or sorted(record) != sorted(expected)
                or any(key not in SECRET_PATHS or not isinstance(value, str) for key, value in record.items())):
            raise ValueError
    except FileNotFoundError:
        if not expected:
            return {}
        raise ValueError('Staged credentials are missing; review the change again') from None
    except (OSError, ValueError, RecursionError):
        raise ValueError('Staged credentials are invalid or unreadable; review the change again') from None
    return record


def remove_staged_secrets(directory):
    try:
        with staged_directory(directory) as descriptor:
            for name in ['secrets.json', *staged_temporary_names(descriptor)]:
                unlink_staged(descriptor, name)
    except (OSError, ValueError):
        raise ValueError('Staged credential cleanup failed; inspect NixLab transaction storage and retry') from None


def cleanup_terminal_secrets(state):
    """Sweep under the mutation lock, preserving unknown/pending recovery.

    Temporary publication files are never a consumer input. Committed secrets
    expire only after the candidate is disposable; recovery/live files are not
    staging and are never traversed here. Unknown state causes a health failure.
    """
    state = Path(state)
    try:
        pending = read_job_record(state / 'pending.json', {})
        if pending and (not isinstance(pending.get('id'), str) or not re.fullmatch(r'[0-9a-f]{32}', pending['id'])):
            raise ValueError
    except ValueError:
        raise ValueError('Staged credential recovery state is unavailable; inspect transaction storage') from None
    jobs = state / 'jobs'
    try:
        with staged_directory(jobs) as descriptor:
            names = os.listdir(descriptor)
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        raise ValueError('Staged credential cleanup failed; inspect protected transaction storage') from None
    problems = False
    for name in names:
        if not re.fullmatch(r'[0-9a-f]{32}', name):
            continue
        directory = jobs / name
        try:
            with staged_directory(directory) as descriptor:
                # The mutation lock excludes the only writer, even if a worker
                # is still building. A partial publication is never resumable.
                for name in staged_temporary_names(descriptor):
                    unlink_staged(descriptor, name)
                # Retry directory durability even if a prior unlink succeeded
                # but its fsync failed and the final name is now absent.
                os.fsync(descriptor)
                present = 'secrets.json' in os.listdir(descriptor)
                if present:
                    info = os.stat('secrets.json', dir_fd=descriptor, follow_symlinks=False)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                            or info.st_nlink != 1 or info.st_mode & 0o077):
                        raise ValueError
            if not present or pending.get('id') == directory.name:
                continue
            record = directory / 'job.json'
            job = read_job_record(record)
            status = job.get('status')
            if status in {'confirmed', 'rolled-back', 'cancelled', 'superseded', 'expired'}:
                remove_staged_secrets(directory)
            elif status in {'reviewed', 'ready', 'failed', 'building'}:
                if not staged_secrets_expired(job):
                    continue
                phases = ('build', 'apply', 'rollback') if status == 'failed' else ('build',) if status == 'building' else ()
                if any(worker_active('nixlab-' + phase + '-' + directory.name) for phase in phases):
                    continue
                if read_job_record(record) != job:
                    continue
                # Commit disposal before unlink, so a crash cannot make this
                # candidate eligible for activation with missing credentials.
                job.update(status='cancelled', error='Staged credentials expired; review the change again')
                write_json(record, job)
                remove_staged_secrets(directory)
            elif status in {'applying', 'awaiting-confirmation', 'rolling-back'}:
                if staged_secrets_expired(job):
                    # Missing pending state is not proof of a safe terminal
                    # transition. Preserve evidence and flag manual recovery.
                    problems = True
            else:
                problems = True
        except (OSError, ValueError, AttributeError):
            problems = True
    if problems:
        raise ValueError('Staged credential cleanup failed; inspect transaction storage, worker state and host clock; protected recovery was retained')


def prepare_state_access(*, locked=False):
    """After declarative users exist, let the portal read its initial auth file."""
    import enrollment
    enrollment.observe(locked=locked)
    auth = STATE / 'auth.json'
    if not auth.exists() and not (STATE / 'web-enrollment').exists():
        return
    gid = grp.getgrnam('nixlab-ui').gr_gid
    os.chown(STATE, -1, gid)
    STATE.chmod(0o710)
    if auth.exists():
        os.chown(auth, -1, gid)
        auth.chmod(0o640)


class BusyError(ValueError):
    pass


@contextlib.contextmanager
def locked(blocking=True):
    STATE.mkdir(parents=True, exist_ok=True)
    with open_lock(STATE / 'mutation.lock') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise BusyError('Another configuration or guest operation is active; retry after it finishes')
        yield


def password_record(password):
    if not isinstance(password, str) or len(password) < 16 or len(password) > 256:
        raise ValueError('Administrator passwords must contain 16–256 characters')
    salt = secrets.token_bytes(24)
    return {'salt': salt.hex(), 'hash': hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000).hex(), 'iterations': 600000}


def password_matches(password, record):
    if not isinstance(password, str) or len(password) > 256:
        return False
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(record['salt']), record['iterations']).hex()
    return secrets.compare_digest(actual, record['hash'])


def audit(action, **fields):
    record = {'time': time.time(), 'action': action, **fields}
    with (STATE / 'audit.jsonl').open('a') as stream:
        stream.write(json.dumps(record, sort_keys=True) + '\n')
    # Never log request bodies, credentials or generated browser certificates.


def modern_layout(root):
    marker = Path(root) / 'machine/format.json'
    if not marker.exists():
        return False
    if read_json(marker).get('version') != 1:
        raise ValueError('Unsupported machine directory version')
    return True


def machine_path(root, name):
    modern = modern_layout(root)
    paths = {'settings': 'settings.json' if modern else 'local/settings.json',
             'installation': 'installation.json' if modern else 'local/installation.json',
             'modules': 'modules' if modern else 'site-modules',
             'installed': 'installed-system'}
    return Path(root) / ('machine' if modern else '') / paths[name]


def migrate_idle_layout(root, state):
    with locked():
        state = Path(state)
        active = read_json(state / 'active.json', {})
        job = read_job_record(state / 'jobs' / active.get('id', '-') / 'job.json', {})
        if read_json(state / 'pending.json') or job.get('status') in ('building', 'ready', 'applying', 'awaiting-confirmation', 'rolling-back'):
            raise ValueError('Finish or discard the configuration transaction before migrating layout')
        return migrate_layout(root)


def migrate_layout(root):
    """Atomically select a local machine directory; legacy files remain recoverable."""
    root = Path(root)
    if modern_layout(root):
        return {'changed': False, 'directory': str(root / 'machine')}
    if (root / 'machine').exists():
        raise ValueError('Inspect the existing machine directory before migration')
    if not machine_path(root, 'settings').is_file():
        raise ValueError('No installed settings inventory to migrate')
    files = list(source_files(root))  # Validate all source paths before copying.
    staging = Path(tempfile.mkdtemp(prefix='.machine-', dir=root))
    try:
        for name, source in files:
            if name == 'local/settings.json': relative = 'settings.json'
            elif name == 'local/installation.json': relative = 'installation.json'
            elif name.startswith('site-modules/'): relative = 'modules/' + name[len('site-modules/'):]
            elif name.startswith('installed-system/'): relative = name
            else: continue
            atomic(staging / relative, source.read_bytes(), mode=source.stat().st_mode & 0o777)
        write_json(staging / 'format.json', {'version': 1})
        os.rename(staging, root / 'machine')
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    return {'changed': True, 'directory': str(root / 'machine')}


def validate_modules(changes):
    if not isinstance(changes, dict) or len(changes) > 16:
        raise ValueError('Upload at most 16 named Nix module files')
    total = 0
    for name, content in changes.items():
        if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_/-]*\.nix', name) or any(part in ('', '.', '..') for part in name.split('/')):
            raise ValueError('Use a relative .nix module path without traversal')
        if not isinstance(content, str) or '\x00' in content:
            raise ValueError('Nix module content must be text')
        size = len(content.encode())
        if size > 65536:
            raise ValueError('A module exceeds the 64 KiB limit')
        total += size
    if total > 131072:
        raise ValueError('Module upload exceeds the 128 KiB limit')
    return changes


def install_modules(source, root):
    files = module_files(source)
    module_files(root)  # Validate the live tree before the first write.
    for path in files:
        relative = path.relative_to(source)
        atomic(Path(root) / relative, path.read_bytes(), mode=0o644)


def module_files(root):
    """Approved administrator module files, including untracked additions."""
    directory = machine_path(root, 'modules')
    if directory.is_symlink():
        raise ValueError('Module directory cannot be a symlink')
    if not directory.exists():
        return []
    result = []
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Module paths cannot be symlinks')
        if path.is_dir():
            continue
        if not path.is_file() or path.suffix not in {'.nix', '.md'}:
            raise ValueError('Only Nix modules and documentation belong in site-modules')
        result.append(path)
    return result


def source_files(root=ROOT):
    if (root / '.nixlab-files.json').exists():
        names = set(read_json(root / '.nixlab-files.json'))
        names.add('.nixlab-files.json')
    else:
        result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z'], capture_output=True, check=True)
        names = set(n.decode() for n in result.stdout.split(b'\0') if n)
    modern = modern_layout(root)
    if modern:
        names = {name for name in names if not name.startswith(('local/', 'site-modules/', 'installed-system/'))}
    names.add('hardware-configuration.nix')
    names.update(str(f.relative_to(root)) for f in module_files(root))
    for prefix in (('machine',) if modern else ('installed-system', 'local')):
        directory = root / prefix
        if directory.exists():
            names.update(str(f.relative_to(root)) for f in directory.rglob('*') if f.is_file())
    for name in sorted(names):
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Invalid source manifest path')
        path = root / name
        if not path.exists():
            continue
        if any(part.is_symlink() for part in [path, *path.parents] if part != root.parent):
            raise ValueError('Managed source cannot traverse symlinks: ' + name)
        if path.is_symlink() or not path.is_file():
            raise ValueError('Managed source cannot contain symlinks: ' + name)
        if name.startswith('machine/') and not (name in {'machine/format.json', 'machine/settings.json', 'machine/installation.json'} or (name.startswith('machine/modules/') and path.suffix in {'.nix', '.md'}) or (name.startswith('machine/installed-system/') and path.suffix == '.nix')):
            raise ValueError('Unexpected file in machine configuration: ' + name)
        if name.startswith('local/') and name not in {'local/settings.json', 'local/installation.json'}:
            raise ValueError('Unexpected local state file in source tree')
        if name.startswith('site-modules/') and path.suffix not in {'.nix', '.md'}:
            raise ValueError('Only Nix modules and documentation belong in site-modules')
        if name.startswith('installed-system/') and path.suffix != '.nix':
            raise ValueError('Only reviewed Nix modules may be imported from the installed system')
        yield name, path


def digest_source(root=ROOT):
    digest = hashlib.sha256()
    for name, path in source_files(root):
        digest.update(name.encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def snapshot(destination, root=ROOT):
    destination.mkdir(mode=0o700, parents=True)
    files = list(source_files(root))
    for name, path in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(path.stat().st_mode & 0o777)
    write_json(destination / '.nixlab-files.json', [name for name, _ in files])


def encode_secrets(values, settings):
    result = {}
    if not isinstance(values, dict) or set(values) - set(SECRET_PATHS):
        raise ValueError('Unknown secret field')
    if settings['secrets']['enable'] and set(values) - {'arcanePassword'}:
        raise ValueError('sops-nix owns secret provisioning on this installation')
    for field, value in values.items():
        if value in ('', None):
            continue
        if not isinstance(value, str) or len(value) > 16384 or '\0' in value:
            raise ValueError('Invalid secret value')
        if field == 'wifiPassword':
            if '\n' in value or '\r' in value or not (8 <= len(value) <= 63 or (len(value) == 64 and all(c in '0123456789abcdefABCDEF' for c in value))):
                raise ValueError('Wi-Fi WPA password must contain 8–63 characters, or 64 hex digits')
            value = 'NIXLAB_WIFI_PSK=' + shlex.quote(value.replace('\\', '\\\\')) + '\n'
        elif field == 'cockpitPassword':
            # A staged file module may enable Cockpit without changing the JSON
            # overlay. The build worker checks the evaluated enable/ownership flags.
            if not settings['cockpit']['managePassword']:
                raise ValueError('Cockpit password enrollment must manage the operator password')
            if not 16 <= len(value) <= 256 or '\n' in value or '\r' in value:
                raise ValueError('Cockpit passwords must contain 16–256 characters and no newline')
            hashed = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=value + '\n',
                                    text=True, capture_output=True, check=True, timeout=30)
            value = hashed.stdout.strip() + '\n'
            if not value.startswith('$6$'):
                raise ValueError('Failed to encode the local account password')
        elif field == 'arcanePassword':
            if (not 16 <= len(value) <= 256 or any(ord(c) < 32 or ord(c) == 127 for c in value)
                    or not any(c.isupper() for c in value) or not any(c.islower() for c in value)
                    or not any(c.isdigit() for c in value) or not any(not c.isalnum() and not c.isspace() for c in value)):
                raise ValueError('Arcane passwords require 16–256 characters with uppercase, lowercase, a number and a symbol')
        elif field == 'resticEnvironment':
            credentials = json.loads(value)
            if not isinstance(credentials, dict) or set(credentials) - ENV_KEYS:
                raise ValueError('Unsupported backup credential environment variable')
            if any(not isinstance(v, str) or any(c in v for c in '\n\r\0') for v in credentials.values()):
                raise ValueError('Backup credential values must be single-line strings')
            value = '\n'.join(k + '=' + shlex.quote(v) for k, v in sorted(credentials.items())) + '\n'
        elif field == 'tunnelCredentials':
            data = json.loads(value)
            if not isinstance(data, dict) or set(data) - {'AccountTag', 'TunnelSecret', 'TunnelID'} or not all(isinstance(data.get(k), str) for k in ('AccountTag', 'TunnelSecret', 'TunnelID')):
                raise ValueError('Use the tunnel-specific Cloudflare credentials JSON')
            if data['TunnelID'] != settings['tunnel']['uuid']:
                raise ValueError('Tunnel credentials do not match the configured UUID')
            value = json.dumps(data) + '\n'
        elif len(value) < 16 or '\n' in value or '\r' in value:
            raise ValueError('Service passwords must contain at least 16 characters and no newline')
        result[field] = value
    return result


def issue_tls(address, hostname):
    """A local CA stays on the host; application guests receive distinct leaf keys."""
    STATE.mkdir(parents=True, exist_ok=True)
    ca_key, ca_crt = STATE / 'ca.key', STATE / 'ca.crt'
    if not ca_key.exists():
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-sha256', '-days', '3650',
                        '-subj', '/CN=Nixlab local management CA', '-keyout', str(ca_key), '-out', str(ca_crt),
                        '-addext', 'basicConstraints=critical,CA:TRUE', '-addext', 'keyUsage=critical,keyCertSign,cRLSign'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ca_key.chmod(0o600); ca_crt.chmod(0o644)
    def leaf(prefix, cn, alt):
        with tempfile.TemporaryDirectory(dir=STATE) as directory:
            temp = Path(directory)
            subprocess.run(['openssl', 'req', '-new', '-newkey', 'rsa:3072', '-nodes', '-subj', '/CN=' + cn,
                            '-keyout', str(temp / 'key'), '-out', str(temp / 'csr')], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (temp / 'extensions').write_text('basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=' + alt + '\n')
            subprocess.run(['openssl', 'x509', '-req', '-in', str(temp / 'csr'), '-CA', str(ca_crt), '-CAkey', str(ca_key),
                            '-set_serial', str(secrets.randbits(120)), '-days', '825', '-sha256', '-extfile', str(temp / 'extensions'),
                            '-out', str(temp / 'crt')], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            atomic(STATE / (prefix + '.key'), (temp / 'key').read_bytes())
            atomic(STATE / (prefix + '.crt'), (temp / 'crt').read_bytes() + ca_crt.read_bytes(), mode=0o644)
    leaf('tls', hostname, f'IP:{address},IP:127.0.0.1,DNS:localhost,DNS:{hostname}')
    if not (STATE / 'apps-tls.key').exists():
        leaf('apps-tls', 'apps.internal', 'DNS:apps.internal')


def pending_access(job_id, record):
    """Local probes cannot prove that an administrator can reconnect."""
    print('Activation is awaiting access confirmation; automatic rollback remains armed.', flush=True)
    print('Open a NEW SSH/Cockpit login after activation (or use the physical console), verify access, then run:', flush=True)
    print('sudo nixlab-admin config confirm ' + job_id, flush=True)
    print('You can also confirm this transaction in the NixLab portal.', flush=True)
    if record.get('deadline'):
        print('Confirmation deadline: ' + time.strftime('%Y-%m-%d %H:%M:%S UTC',
              time.gmtime(record['deadline'])), flush=True)
