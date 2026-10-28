"""Root-owned transaction storage, certificate issuance and fixed secret destinations."""
import base64
import contextlib
import fcntl
import grp
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import tempfile
import time

ROOT = Path(os.environ.get('NIXLAB_ROOT', '/var/lib/nixlab/config'))
STATE = Path(os.environ.get('NIXLAB_STATE', '/var/lib/nixlab-admin'))
SOURCE = Path(__file__).resolve().parent
SECRET_PATHS = {
    'wifiPassword': Path('/var/lib/nixlab-secrets/wifi.env'),
    'portainerPassword': Path('/var/lib/nixlab-secrets/apps-portainer-password'),
    'demoPassword': Path('/var/lib/nixlab-secrets/apps-demo-password'),
    'resticPassword': Path('/var/lib/nixlab-secrets/restic-password'),
    'resticEnvironment': Path('/var/lib/nixlab-secrets/restic-environment'),
    'tunnelCredentials': Path('/var/lib/nixlab-secrets/cloudflared.json'),
}
ENV_KEYS = {'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_DEFAULT_REGION',
            'B2_ACCOUNT_ID', 'B2_ACCOUNT_KEY', 'AZURE_ACCOUNT_NAME', 'AZURE_ACCOUNT_KEY',
            'RESTIC_REST_USERNAME', 'RESTIC_REST_PASSWORD'}


def atomic(path, data, mode=0o600, group=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data.encode() if isinstance(data, str) else data)
            stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if group is not None:
            try:
                os.chown(temporary, -1, grp.getgrnam(group).gr_gid)
            except KeyError:
                if os.environ.get('NIXLAB_TEST') != '1':
                    raise
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


@contextlib.contextmanager
def locked():
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'mutation.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
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
    write_json(destination / '.nixlab-files.json', [name for name, _ in files])


def encode_secrets(values, settings):
    result = {}
    if not isinstance(values, dict) or set(values) - set(SECRET_PATHS):
        raise ValueError('Unknown secret field')
    if settings['secrets']['enable'] and values:
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
