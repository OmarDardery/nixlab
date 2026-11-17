"""Browser-bound, locally approved portal password recovery.

Only the root CLI may approve. The HTTPS gateway transports private cookies,
never an approval token or the new password through console output.
"""
import hashlib
import ipaddress
import math
import os
import re
import secrets
import time

import accounts
import enrollment
import runtime as rt

FILE = 'account-recovery-requests.json'
TTL = 300
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')
REQUEST_ID = re.compile(r'[0-9a-f]{24}')


def digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def csrf_for(token):
    return digest('recovery-csrf:' + token)


def identity(username):
    item = accounts.record(username)
    return digest(accounts.fingerprint(item)) if item else None


def scope(address, origin):
    try:
        ipaddress.ip_address(address)
        if not isinstance(origin, str) or len(origin) > 256 or not origin.startswith('https://'):
            raise ValueError()
    except (TypeError, ValueError):
        raise rt.PublicError('Invalid recovery browser scope') from None


def load():
    now = time.monotonic()
    boot = enrollment.boot_id()
    empty = {'version': 1, 'boot': boot, 'requests': {}, 'attempts': {}}
    path = rt.STATE / FILE
    try:
        if path.is_symlink() or (path.exists() and path.stat().st_size > 131072):
            raise ValueError()
        value = rt.read_json(path, empty)
        if (not isinstance(value, dict) or set(value) != set(empty)
                or value['version'] != 1 or not isinstance(value['boot'], str)
                or not isinstance(value['requests'], dict) or not isinstance(value['attempts'], dict)
                or len(value['requests']) > 64 or len(value['attempts']) > 128):
            raise ValueError()
        if value['boot'] != boot:
            return empty
        for request_id, item in list(value['requests'].items()):
            if (not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id)
                    or not isinstance(item, dict) or set(item) != {
                        'tokenHash', 'csrfHash', 'address', 'origin', 'username', 'identity', 'expires', 'approved'}
                    or not all(isinstance(item[k], str) for k in ('tokenHash', 'csrfHash', 'address', 'origin', 'username'))
                    or not all(re.fullmatch(r'[0-9a-f]{64}', item[k]) for k in ('tokenHash', 'csrfHash'))
                    or (item['identity'] is not None and
                        (not isinstance(item['identity'], str) or not re.fullmatch(r'[0-9a-f]{64}', item['identity'])))
                    or type(item['expires']) not in (int, float) or not math.isfinite(item['expires'])
                    or type(item['approved']) is not bool):
                raise ValueError()
            if now >= item['expires']:
                del value['requests'][request_id]
        for address, times in list(value['attempts'].items()):
            if (not isinstance(address, str) or not isinstance(times, list) or len(times) > 64
                    or any(type(t) not in (int, float) or not math.isfinite(t) for t in times)):
                raise ValueError()
            value['attempts'][address] = [t for t in times if 0 <= now - t < TTL]
            if not value['attempts'][address]:
                del value['attempts'][address]
        return value
    except (OSError, ValueError, TypeError):
        raise rt.PublicError('Recovery authorization state is unavailable; inspect local host state') from None


def save(value):
    rt.write_json(rt.STATE / FILE, value)


def checked(value, token, csrf, address, origin):
    scope(address, origin)
    if (not isinstance(token, str) or not TOKEN.fullmatch(token)
            or not isinstance(csrf, str) or not re.fullmatch(r'[0-9a-f]{64}', csrf)):
        raise rt.PublicError('Recovery request expired or browser proof is invalid; start again')
    token_hash, csrf_hash = digest(token), digest(csrf)
    for request_id, item in value['requests'].items():
        if (secrets.compare_digest(item['tokenHash'], token_hash)
                and secrets.compare_digest(item['csrfHash'], csrf_hash)
                and item['address'] == address and item['origin'] == origin):
            return request_id, item
    raise rt.PublicError('Recovery request expired or browser proof is invalid; start again')


def start(token, username, address, origin):
    scope(address, origin)
    if not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', username):
        raise rt.PublicError('Enter a valid portal account name')
    with rt.locked(blocking=False):
        value = load()
        found = None
        if isinstance(token, str) and TOKEN.fullmatch(token):
            for request_id, item in value['requests'].items():
                if secrets.compare_digest(item['tokenHash'], digest(token)):
                    if item['address'] != address or item['origin'] != origin:
                        raise rt.PublicError('Recovery request belongs to a different browser scope')
                    if item['username'] == username:
                        found = (request_id, item)
                    else:
                        del value['requests'][request_id]
                    break
        if found is None:
            times = value['attempts'].setdefault(address, [])
            if (len(times) >= 6 or sum(len(bucket) for bucket in value['attempts'].values()) >= 64
                    or len(value['requests']) >= 64 or len(value['attempts']) > 128):
                raise rt.PublicError('Too many recovery requests; try again in five minutes')
            times.append(time.monotonic())
            token = secrets.token_urlsafe(32)
            request_id = secrets.token_hex(12)
            item = {'tokenHash': digest(token), 'csrfHash': digest(csrf_for(token)),
                    'address': address, 'origin': origin, 'username': username,
                    'identity': identity(username), 'expires': time.monotonic() + TTL, 'approved': False}
            value['requests'][request_id] = item
        else:
            request_id, item = found
        save(value)
        # Token is private broker-to-HTTPS-cookie delivery only.
        return {'token': token, 'csrf': csrf_for(token), 'requestId': request_id,
                'approved': item['approved'], 'expiresIn': max(0, int(item['expires'] - time.monotonic()))}


def status(token, csrf, address, origin):
    with rt.locked(blocking=False):
        request_id, item = checked(load(), token, csrf, address, origin)
        return {'requestId': request_id, 'approved': item['approved'],
                'expiresIn': max(0, int(item['expires'] - time.monotonic()))}


def approve(request_id):
    if os.geteuid() != 0:
        raise rt.PublicError('Only local root can approve account recovery')
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise rt.PublicError('Recovery request is missing or expired')
    with rt.locked(blocking=False):
        value = load()
        item = value['requests'].get(request_id)
        if (not item or item['approved'] or item['identity'] is None
                or identity(item['username']) != item['identity']
                or any(other['approved'] and other['username'] == item['username']
                       for key, other in value['requests'].items() if key != request_id)):
            raise rt.PublicError('Recovery request is not eligible for approval')
        item['approved'] = True
        save(value)
        rt.audit('portal-recovery-approved', username=item['username'])
    return {'ok': True}


def reset(token, csrf, address, origin, password):
    if not isinstance(password, str) or not 16 <= len(password) <= 256:
        raise rt.PublicError('New portal password must contain 16–256 characters')
    with rt.locked(blocking=False):
        value = load()
        request_id, item = checked(value, token, csrf, address, origin)
        username = item['username']
        if not item['approved'] or item['identity'] is None or identity(username) != item['identity']:
            raise rt.PublicError('Local root approval is missing or the account changed; start again')
        # Persistent consumption precedes any account mutation. Crash/retry
        # cannot reuse approval, and a failed write leaves the old login intact.
        del value['requests'][request_id]
        save(value)
        verifier = rt.password_record(password)
        if username == 'admin':
            enrollment.load()
            enrollment.save('recovery-authorized')
            enrollment.retire_legacy_capabilities()
            rt.write_json(rt.STATE / 'auth.json', verifier, mode=0o640, group='nixlab-ui')
            enrollment.save('migrated')
            rt.durable_unlink(rt.STATE / 'setup-password')
        else:
            path = rt.STATE / 'users.json'
            if path.is_symlink():
                raise rt.PublicError('Portal account state cannot be changed safely')
            users = rt.read_json(path, {})
            if not isinstance(users, dict) or username not in users:
                raise rt.PublicError('Portal account changed; start recovery again')
            users[username] = {**users[username], 'password': verifier}
            rt.write_json(path, users, mode=0o640, group='nixlab-ui')
        rt.audit('portal-recovery-completed', username=username)
    # Auth.get checks the changed verifier on every request, including old
    # sessions still held in a web process.
    return {'ok': True}
