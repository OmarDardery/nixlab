"""Portal identities. Configuration authority stays with administrators."""
import re
import runtime as rt

ROLES = {'admin', 'operator', 'viewer'}
OPERATOR_ACTIONS = {'start', 'stop', 'restart', 'snapshot', 'export'}


class AuthorizationError(PermissionError):
    pass


def record(username):
    if username == 'admin':
        return {'role': 'admin', 'password': rt.read_json(rt.STATE / 'auth.json')}
    return rt.read_json(rt.STATE / 'users.json', {}).get(username)


def matches(username, password):
    item = record(username)
    # Use the same expensive hash check for unknown identities.
    baseline = item['password'] if item else rt.read_json(rt.STATE / 'auth.json')
    valid = rt.password_matches(password, baseline)
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


def change_password(username, password):
    if username == 'admin':
        rt.write_json(rt.STATE / 'auth.json', rt.password_record(password), mode=0o640, group='nixlab-ui')
    else:
        current = record(username)
        if not current: raise ValueError('Unknown account')
        update(username, current['role'], password)
    return {'ok': True}


def authorize(session, path, body):
    if session['role'] == 'admin' or path in ('/api/logout', '/api/password'): return
    if (session['role'] == 'operator' and path == '/api/operation'
            and body.get('project', 'lab') == 'lab' and body.get('action') in OPERATOR_ACTIONS): return
    raise AuthorizationError('This operation requires an administrator account')
