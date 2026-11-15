"""Owned Incus browser identities; private keys live only in protected /run storage."""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import tempfile
import time

import diagnostics
from model import command
import runtime as rt

RUNTIME = Path('/run/nixlab-browser')
VALID_DAYS = 90
PRIVATE_LIFETIME = 300


def registry(state):
    value = rt.read_job_record(Path(state) / 'browser-certificates.json', {'version': 1, 'certificates': {}})
    try:
        if value.get('version') != 1 or not isinstance(value.get('certificates'), dict):
            raise ValueError
        for identity, record in value['certificates'].items():
            fingerprint(identity)
            if (record['fingerprint'] != identity or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', record['device'])
                    or not re.fullmatch('nixlab-browser-' + re.escape(record['device']) + '-[0-9a-f]{12}', record['name'])
                    or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', record['owner'])
                    or type(record['restricted']) is not bool
                    or record['projects'] != (['lab'] if record['restricted'] else [])
                    or record['status'] not in ('issuing', 'active', 'revoked', 'expired')
                    or type(record['issuedAt']) not in (int, float) or not math.isfinite(record['issuedAt'])):
                raise ValueError
            ssl.cert_time_to_seconds(record['expiresAt'])
    except (ValueError, TypeError, KeyError):
        raise ValueError('Browser certificate ownership registry is invalid; use local recovery') from None
    return value


def save(state, value):
    rt.write_json(Path(state) / 'browser-certificates.json', value)


def fingerprint(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('Select a complete owned certificate fingerprint')
    return value


def public(record):
    return {key: record[key] for key in ('name', 'device', 'fingerprint', 'owner', 'issuedAt',
                                       'expiresAt', 'restricted', 'projects', 'status')}


def trusted():
    items = json.loads(command(['incus', '--force-local', 'query', '/1.0/certificates?recursion=1']))
    if not isinstance(items, list):
        raise ValueError('Incus certificate inventory is unavailable')
    return {item['fingerprint']: item for item in items if isinstance(item, dict) and 'fingerprint' in item}


def inventory(state):
    with rt.locked(blocking=False):
        records = registry(state)['certificates']
        live = trusted()
        result = []
        for identity, record in records.items():
            item = public(record)
            if identity in live:
                item.update(status='trusted', restricted=live[identity]['restricted'], projects=live[identity]['projects'])
            elif record['status'] not in ('revoked', 'expired'):
                item['status'] = 'not-trusted'
            result.append(item)
        return {'certificates': result}


def private_runtime():
    with rt.staged_directory(RUNTIME.parent):
        RUNTIME.mkdir(mode=0o700, exist_ok=True)
    with rt.staged_directory(RUNTIME) as descriptor:
        if os.fstat(descriptor).st_mode & 0o077:
            raise ValueError('Browser certificate runtime storage must be private')


def cleanup_private():
    """Called under the mutation lock by the existing guard; never follow aliases."""
    if not RUNTIME.exists():
        return
    with rt.staged_directory(RUNTIME) as descriptor:
        if os.fstat(descriptor).st_mode & 0o077:
            raise ValueError('Browser certificate runtime storage must be private')
        for name in os.listdir(descriptor):
            if not re.fullmatch(r'issue-[a-z0-9_]{8}', name):
                continue
            path = RUNTIME / name
            if path.is_symlink():
                raise ValueError('Browser certificate runtime storage contains an alias')
            with rt.staged_directory(path):
                if time.time() - path.stat().st_mtime >= PRIVATE_LIFETIME:
                    shutil.rmtree(path)


def openssl(*args, **kwargs):
    return subprocess.run(['openssl', *args], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, timeout=15, **kwargs).stdout


def issue(state, device, passphrase, owner='root', full_admin=False, renew=None):
    diagnostics.require_plausible_clock()
    if not isinstance(device, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', device):
        raise ValueError('Use a device name of up to 32 letters, digits, hyphens or underscores')
    if not isinstance(passphrase, str) or not 16 <= len(passphrase) <= 256 or any(c in passphrase for c in '\r\n\0'):
        raise ValueError('Use a bundle passphrase of 16–256 characters with no line breaks')
    if type(full_admin) is not bool:
        raise ValueError('Select an explicit certificate scope')
    if not isinstance(owner, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', owner):
        raise ValueError('Invalid browser identity owner')
    with rt.locked(blocking=False):
        records = registry(state)
        if renew is not None:
            old = records['certificates'].get(fingerprint(renew))
            if not old or old['device'] != device:
                raise ValueError('Renew only an owned identity for the selected device')
        name = 'nixlab-browser-' + device + '-' + rt.secrets.token_hex(6)
        try:
            private_runtime()
            cleanup_private()
            with tempfile.TemporaryDirectory(prefix='issue-', dir=RUNTIME) as folder:
                path = Path(folder)
                openssl('req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:secp384r1',
                        '-nodes', '-sha384', '-days', str(VALID_DAYS), '-subj', '/CN=' + name,
                        '-addext', 'basicConstraints=critical,CA:FALSE',
                        '-addext', 'keyUsage=critical,digitalSignature', '-addext', 'extendedKeyUsage=clientAuth',
                        '-keyout', str(path / 'key'), '-out', str(path / 'cert'))
                der = openssl('x509', '-in', str(path / 'cert'), '-outform', 'DER')
                identity = hashlib.sha256(der).hexdigest()
                expiration = openssl('x509', '-in', str(path / 'cert'), '-noout', '-enddate').decode().strip().split('=', 1)[1]
                openssl('pkcs12', '-export', '-in', str(path / 'cert'), '-inkey', str(path / 'key'),
                        '-out', str(path / 'client.p12'), '-passout', 'stdin', input=(passphrase + '\n').encode())
                data = base64.b64encode((path / 'client.p12').read_bytes()).decode()
                record = {'name': name, 'device': device, 'fingerprint': identity, 'owner': owner,
                          'issuedAt': time.time(), 'expiresAt': expiration, 'restricted': not full_admin,
                          'projects': [] if full_admin else ['lab'], 'status': 'issuing'}
                if renew is not None:
                    record['renews'] = renew
                # Durable ownership intent precedes Incus mutation. A crash or
                # lost download remains visible and individually revocable.
                records['certificates'][identity] = record
                save(state, records)
                scope = [] if full_admin else ['--projects', 'lab', '--restricted']
                command(['incus', '--force-local', 'config', 'trust', 'add-certificate', str(path / 'cert'),
                         '--name', name, *scope])
                record['status'] = 'active'
                save(state, records)
            rt.audit('browser-certificate-issued', fingerprint=identity, principal=owner, restricted=not full_admin)
            # Private broker delivery only. The frontend must not return this
            # field in its JSON response; it owns the session-bound binary claim.
            return {**public(record), 'validDays': VALID_DAYS, 'pkcs12': data}
        except (OSError, ValueError, subprocess.SubprocessError):
            raise ValueError('Browser certificate issuance failed; refresh the identity list before retrying') from None


def revoke(state, identity, owner='root'):
    identity = fingerprint(identity)
    with rt.locked(blocking=False):
        records = registry(state)
        record = records['certificates'].get(identity)
        if not record:
            raise ValueError('Only NixLab-owned browser identities can be revoked here')
        if identity in trusted():
            command(['incus', '--force-local', 'config', 'trust', 'remove', identity])
        record['status'] = 'revoked'
        save(state, records)
        rt.audit('browser-certificate-revoked', fingerprint=identity, principal=owner)
        return {'ok': True, 'fingerprint': identity}


def expire_owned(state):
    """Existing guard owns the mutation lock; never touch externally owned trust."""
    records = registry(state)
    try:
        expired = [identity for identity, record in records['certificates'].items()
                   if record['status'] not in ('expired', 'revoked')
                   and time.time() >= ssl.cert_time_to_seconds(record['expiresAt'])]
    except (ValueError, TypeError, KeyError):
        raise ValueError('Browser identity expiration metadata is invalid; inspect owned trust before retrying') from None
    if not expired:
        return
    diagnostics.require_plausible_clock()
    live = trusted()
    for identity in expired:
        fingerprint(identity)
        if identity in live:
            command(['incus', '--force-local', 'config', 'trust', 'remove', identity])
        records['certificates'][identity]['status'] = 'expired'
        save(state, records)
        rt.audit('browser-certificate-expired', fingerprint=identity)
