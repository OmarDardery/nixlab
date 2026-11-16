"""Portal identities. Configuration authority stays with administrators."""
import re
import os
import runtime as rt

ROLES = {'admin', 'operator', 'viewer'}
OPERATOR_ACTIONS = {'start', 'stop', 'restart', 'snapshot', 'export'}


class AuthorizationError(PermissionError):
    pass


def record(username):
    from enrollment import valid_verifier
    try:
        if username == 'admin':
            path = rt.STATE / 'auth.json'
            password = rt.read_json(path)
            return {'role': 'admin', 'password': password} if not path.is_symlink() and valid_verifier(password) else None
        users = rt.read_json(rt.STATE / 'users.json', {})
        item = users.get(username) if isinstance(users, dict) else None
        if (isinstance(item, dict) and item.get('role') in ROLES
                and valid_verifier(item.get('password'))):
            return item
    except (OSError, ValueError, TypeError):
        pass
    return None


def matches(username, password):
    item = record(username)
    # Keep unknown-name work comparable to a known identity when admin is valid.
    baseline = item or record('admin')
    valid = baseline is not None and rt.password_matches(password, baseline['password'])
    return bool(item and valid)


def fingerprint(item):
    return item['role'] + ':' + item['password']['hash']


def list_users():
    users = rt.read_json(rt.STATE / 'users.json', {})
    return ([{'username': 'admin', 'role': 'admin'}] if record('admin') else []) + [{'username': name, 'role': item['role']} for name, item in sorted(users.items())]


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


def change_password(username, password):
    if username == 'admin':
        verifier = rt.password_record(password)
        with rt.locked(blocking=False):
            import enrollment
            enrollment.reconcile()
            rt.write_json(rt.STATE / 'auth.json', verifier, mode=0o640, group='nixlab-ui')
            enrollment.save('migrated')
            enrollment.retire_legacy_capabilities()
            # Retire the old private bootstrap value only AFTER the supplied
            # replacement verifier is committed. Failed writes preserve access.
            rt.durable_unlink(rt.STATE / 'setup-password')
    else:
        current = record(username)
        if not current: raise ValueError('Unknown account')
        update(username, current['role'], password)
    return {'ok': True}


def enrollment_status():
    import enrollment
    return enrollment.status()


def enrollment_open():
    return enrollment_status()['available']


def enable_enrollment():
    import enrollment
    return enrollment.enable()


def approve_enrollment(code):
    import enrollment
    return enrollment.approve(code)


def claim_enrollment(code, password):
    import enrollment
    return enrollment.claim(code, password)


def local_root():
    return os.geteuid() == 0


def recover_password(password):
    """Explicit local-root recovery; never reachable from anonymous web claiming."""
    import enrollment
    if not local_root():
        raise AuthorizationError('Local root approval is required for account recovery')
    verifier = rt.password_record(password)
    with rt.locked(blocking=False):
        enrollment.load()  # Future/corrupt authority requires supported-state restoration.
        enrollment.save('recovery-authorized')
        enrollment.retire_legacy_capabilities()
        rt.write_json(rt.STATE / 'auth.json', verifier, mode=0o640, group='nixlab-ui')
        enrollment.save('migrated')
        rt.durable_unlink(rt.STATE / 'setup-password')
    rt.audit('portal-account-recovered')
    return {'ok': True}


def authorize(session, path, body):
    if session['role'] == 'admin' or path in ('/api/logout', '/api/password'): return
    if (session['role'] == 'operator' and path == '/api/operation'
            and body.get('project', 'lab') == 'lab' and body.get('action') in OPERATOR_ACTIONS): return
    raise AuthorizationError('This operation requires an administrator account')
