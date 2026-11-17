"""Root-owned first-administrator authority; legacy verifiers remain unchanged."""
import hashlib
import math
from pathlib import Path
import re
import secrets
import time

import runtime as rt

STATE_FILE = 'bootstrap-state.json'
PHASES = {'unclaimed', 'host-verified', 'committing', 'account-committed',
          'legacy-provisioning', 'migrated', 'recovery-authorized'}
def recovery_error():
    return rt.PublicError('Portal enrollment is locked; use sudo nixlab-admin reset-password from a private host terminal or restore supported account state')


def present(name):
    path = rt.STATE / name
    return path.exists() or path.is_symlink()


def valid_verifier(value):
    return (isinstance(value, dict) and set(value) == {'salt', 'hash', 'iterations'}
            and isinstance(value['salt'], str) and re.fullmatch(r'[0-9a-f]{48}', value['salt']) is not None
            and isinstance(value['hash'], str) and re.fullmatch(r'[0-9a-f]{64}', value['hash']) is not None
            and type(value['iterations']) is int and 10000 <= value['iterations'] <= 10000000)


def auth_valid():
    try:
        path = rt.STATE / 'auth.json'
        return not path.is_symlink() and valid_verifier(rt.read_json(path))
    except (ValueError, OSError):
        return False


def account_valid():
    if auth_valid():
        return True
    try:
        path = rt.STATE / 'users.json'
        users = rt.read_json(path, {})
        return (not path.is_symlink() and isinstance(users, dict)
                and any(isinstance(name, str) and name != 'admin' and re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', name)
                        and isinstance(item, dict) and item.get('role') == 'admin'
                        and valid_verifier(item.get('password')) for name, item in users.items()))
    except (ValueError, OSError):
        return False


def load():
    if not present(STATE_FILE):
        return None
    try:
        path = rt.STATE / STATE_FILE
        if path.is_symlink() or path.stat().st_size > 16384:
            raise ValueError()
        value = rt.read_json(path)
        if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
                or value.get('phase') not in PHASES or set(value) - {'version', 'phase', 'proof', 'claimDigest'}):
            raise ValueError()
        if value['phase'] in {'host-verified', 'committing'}:
            proof = value.get('proof')
            if (not isinstance(proof, dict) or set(proof) != {'digest', 'boot', 'expires'}
                    or not isinstance(proof['digest'], str) or not re.fullmatch(r'[0-9a-f]{64}', proof['digest'])
                    or not isinstance(proof['boot'], str) or not proof['boot']
                    or type(proof['expires']) not in (int, float) or not math.isfinite(proof['expires'])
                    or proof['expires'] <= 0):
                raise ValueError()
        elif 'proof' in value:
            raise ValueError()
        if 'claimDigest' in value and (value['phase'] != 'account-committed'
                or not isinstance(value['claimDigest'], str) or not re.fullmatch(r'[0-9a-f]{64}', value['claimDigest'])):
            raise ValueError()
        return value
    except (ValueError, OSError, TypeError):
        raise recovery_error() from None


def save(phase, **fields):
    rt.write_json(rt.STATE / STATE_FILE, {'version': 1, 'phase': phase, **fields})


def retire_legacy_capabilities():
    # Remove the old marker BEFORE publishing auth. Previous source versions then
    # remain closed even if auth is subsequently lost, or publication is interrupted.
    for name in ('web-enrollment', 'web-enrollment-approval.json'):
        rt.durable_unlink(rt.STATE / name)


def reconcile():
    """Caller holds mutation.lock. Never derive an open claim from missing files."""
    state = load()
    if account_valid():
        if state is None or state['phase'] not in {'account-committed', 'migrated'}:
            retire_legacy_capabilities()
            if state and state['phase'] == 'committing':
                save('account-committed', claimDigest=state['proof']['digest'])
            else:
                save('migrated')
            state = load()
    return state


def host_history():
    return (any(present(name) for name in ('active.json', 'last-confirmed.json', 'pending.json'))
            or ((rt.STATE / 'jobs').is_dir() and any((rt.STATE / 'jobs').iterdir())))


def status():
    with rt.locked():
        try:
            state = reconcile()
        except rt.PublicError:
            return {'available': False, 'recoveryRequired': True}
        phase = state['phase'] if state else None
        artifacts = any(present(name) for name in ('auth.json', 'users.json', 'setup-password'))
        available = phase in {'unclaimed', 'host-verified'} and not artifacts
        recovery = not account_valid() and (artifacts or phase in {'committing', 'account-committed', 'migrated', 'recovery-authorized'}
                                        or (phase is None and (present('web-enrollment') or host_history())))
        return {'available': available, 'recoveryRequired': bool(recovery)}


def enable():
    with rt.locked():
        state = reconcile()
        if (any(present(name) for name in ('auth.json', 'users.json', 'setup-password'))
                or (state and state['phase'] not in {'unclaimed', 'host-verified'})):
            raise rt.PublicError('This host already has portal account state; use authorized account recovery')
        if state is None:
            # This is an explicit root installer operation, not a browser request.
            save('unclaimed')
        rt.atomic(rt.STATE / 'web-enrollment', '1\n')


def proof_digest(code):
    if not isinstance(code, str) or not re.fullmatch(r'[0-9a-f]{32}', code):
        raise rt.PublicError('Use the approval code displayed in your browser')
    return hashlib.sha256(code.encode('ascii')).hexdigest()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def approve(code):
    digest = proof_digest(code)
    with rt.locked(blocking=False):
        state = reconcile()
        if any(present(name) for name in ('auth.json', 'users.json', 'setup-password')):
            raise rt.PublicError('First administrator enrollment is unavailable; use authorized account recovery')
        if state is None and present('web-enrollment'):
            # Upgrade an old opt-in marker only after a NEW explicit root approval.
            state = {'phase': 'unclaimed'}
        if not state or state['phase'] not in {'unclaimed', 'host-verified', 'committing'}:
            raise recovery_error()
        rt.durable_unlink(rt.STATE / 'web-enrollment-approval.json')
        save('host-verified', proof={'digest': digest, 'boot': boot_id(), 'expires': time.monotonic() + 600})


def claim(code, password, username='admin'):
    digest = proof_digest(code)
    with rt.locked(blocking=False):
        return claim_locked(digest, password, username)


def claim_locked(digest, password, username='admin'):
    """Same commit authority for root compatibility and browser-bound enrollment."""
    state = reconcile()
    if (state and state['phase'] == 'account-committed' and account_valid()
            and secrets.compare_digest(state.get('claimDigest', ''), digest)):
        return {'ok': True, 'alreadyComplete': True}
    if (not state or state['phase'] not in {'unclaimed', 'host-verified'}
            or any(present(name) for name in ('auth.json', 'users.json', 'setup-password'))):
        raise rt.PublicError('First administrator enrollment is unavailable; use authorized account recovery')
    proof = state.get('proof', {})
    if (proof.get('boot') != boot_id() or time.monotonic() >= proof.get('expires', 0)
            or not secrets.compare_digest(proof.get('digest', ''), digest)):
        raise rt.PublicError('Approve this browser from the host console before enrolling')
    if not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', username):
        raise rt.PublicError('Choose a portal username starting with a letter, followed by up to 31 lowercase letters, numbers, underscores or hyphens')
    verifier = rt.password_record(password)
    save('committing', proof=proof)
    retire_legacy_capabilities()
    if username == 'admin':
        rt.write_json(rt.STATE / 'auth.json', verifier, mode=0o640, group='nixlab-ui')
    else:
        rt.write_json(rt.STATE / 'users.json', {username: {'role': 'admin', 'password': verifier}}, mode=0o640, group='nixlab-ui')
    save('account-committed', claimDigest=digest)
    rt.audit('first-admin-enrolled')
    return {'ok': True}



def ensure_legacy_account():
    """Private installer compatibility, never an anonymous recovery mechanism."""
    with rt.locked():
        state = reconcile()
        if state and state['phase'] in {'unclaimed', 'host-verified'}:
            return
        if account_valid():
            return
        if state and state['phase'] != 'legacy-provisioning':
            raise recovery_error()
        if any(present(name) for name in ('auth.json', 'users.json')):
            raise recovery_error()
        if state is None:
            if present('web-enrollment'):
                raise rt.PublicError('Approve the browser again from the host console to migrate legacy enrollment')
            # A legacy private setup value is preserved and migrated, never returned.
            if host_history():
                raise recovery_error()
            save('legacy-provisioning')
        path = rt.STATE / 'setup-password'
        if path.is_symlink():
            raise recovery_error()
        if not path.exists():
            rt.atomic(path, secrets.token_urlsafe(24) + '\n')
        verifier = rt.password_record(path.read_text().strip())
        rt.write_json(rt.STATE / 'auth.json', verifier, mode=0o640)
        save('migrated')


def observe(*, locked=False):
    if locked:
        reconcile()
    else:
        with rt.locked():
            reconcile()
