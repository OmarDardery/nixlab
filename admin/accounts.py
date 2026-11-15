"""Portal identities. Configuration authority stays with administrators."""
import hashlib
from pathlib import Path
import re
import secrets
import time
import runtime as rt

ROLES = {'admin', 'operator', 'viewer'}
OPERATOR_ACTIONS = {'start', 'stop', 'restart', 'snapshot', 'export'}


class AuthorizationError(PermissionError):
    pass


def record(username):
    if username == 'admin':
        password = rt.read_json(rt.STATE / 'auth.json')
        return {'role': 'admin', 'password': password} if password else None
    return rt.read_json(rt.STATE / 'users.json', {}).get(username)


def matches(username, password):
    item = record(username)
    # Use the same expensive hash check for unknown identities.
    baseline = item['password'] if item else rt.read_json(rt.STATE / 'auth.json')
    valid = baseline is not None and rt.password_matches(password, baseline)
    return bool(item and valid)


def fingerprint(item):
    return item['role'] + ':' + item['password']['hash']


def list_users():
    users = rt.read_json(rt.STATE / 'users.json', {})
    return [{'username': 'admin', 'role': 'admin'}] + [{'username': name, 'role': item['role']} for name, item in sorted(users.items())]


def update(username, role=None, password=None, delete=False):
    if not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', username) or username == 'admin':
        raise ValueError('Use a named account other than the reserved recovery administrator')
    with rt.locked(blocking=False):
        users = rt.read_json(rt.STATE / 'users.json', {})
        if delete:
            users.pop(username, None)
        else:
            if role not in ROLES: raise ValueError('Choose admin, operator or viewer')
            item = users.get(username, {})
            if password: item['password'] = rt.password_record(password)
            if 'password' not in item: raise ValueError('New accounts require a password')
            item['role'] = role; users[username] = item
        rt.write_json(rt.STATE / 'users.json', users, mode=0o640, group='nixlab-ui')
    rt.audit('portal-account-updated', username=username, role=role, deleted=bool(delete))
    return {'users': list_users()}


def change_password(username, password, *, clear_setup_password=False):
    if username == 'admin':
        verifier = rt.password_record(password)
        with rt.locked(blocking=False):
            rt.write_json(rt.STATE / 'auth.json', verifier, mode=0o640, group='nixlab-ui')
            if clear_setup_password:
                rt.durable_unlink(rt.STATE / 'setup-password')
    else:
        current = record(username)
        if not current: raise ValueError('Unknown account')
        update(username, current['role'], password)
    return {'ok': True}


def enrollment_open():
    return ((rt.STATE / 'web-enrollment').is_file()
            and not any((rt.STATE / name).exists() or (rt.STATE / name).is_symlink()
                        for name in ('auth.json', 'users.json', 'setup-password')))


def enable_enrollment():
    with rt.locked():
        marker = rt.STATE / 'web-enrollment'
        if any((rt.STATE / name).exists() or (rt.STATE / name).is_symlink()
               for name in ('auth.json', 'users.json', 'setup-password')):
            raise ValueError('This host already has portal account state')
        rt.atomic(marker, '1\n')


def proof_digest(code):
    if not isinstance(code, str) or not re.fullmatch(r'[0-9a-f]{32}', code):
        raise ValueError('Use the approval code displayed in your browser')
    return hashlib.sha256(code.encode('ascii')).hexdigest()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def approve_enrollment(code):
    digest = proof_digest(code)
    with rt.locked(blocking=False):
        if not enrollment_open():
            raise ValueError('First administrator enrollment is unavailable')
        rt.write_json(rt.STATE / 'web-enrollment-approval.json', {
            'digest': digest, 'boot': boot_id(), 'expires': time.monotonic() + 600,
        })


def claim_enrollment(code, password):
    digest = proof_digest(code)
    with rt.locked(blocking=False):
        if not enrollment_open():
            raise ValueError('First administrator enrollment is unavailable')
        path = rt.STATE / 'web-enrollment-approval.json'
        approval = rt.read_json(path, {})
        if (approval.get('boot') != boot_id() or time.monotonic() >= approval.get('expires', 0)
                or not secrets.compare_digest(approval.get('digest', ''), digest)):
            raise ValueError('Approve this browser from the host console before enrolling')
        rt.write_json(rt.STATE / 'auth.json', rt.password_record(password), mode=0o640, group='nixlab-ui')
        rt.durable_unlink(path)
        rt.durable_unlink(rt.STATE / 'web-enrollment')
        rt.audit('first-admin-enrolled')
        return {'ok': True}


def authorize(session, path, body):
    if session['role'] == 'admin' or path in ('/api/logout', '/api/password'): return
    if (session['role'] == 'operator' and path == '/api/operation'
            and body.get('project', 'lab') == 'lab' and body.get('action') in OPERATOR_ACTIONS): return
    raise AuthorizationError('This operation requires an administrator account')
