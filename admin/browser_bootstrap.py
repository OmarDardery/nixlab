"""Bounded root-owned browser enrollment sessions, using the existing claim lock."""
import hashlib
import ipaddress
import math
import re
import secrets
import time

import enrollment
import runtime as rt

FILE = 'bootstrap-sessions.json'
TTL = 600
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')


def digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def material(token, label):
    return hashlib.sha256((label + ':' + token).encode('ascii')).hexdigest()


def require_open():
    state = enrollment.reconcile()
    if (not state or state['phase'] not in {'unclaimed', 'host-verified'}
            or any(enrollment.present(name) for name in ('auth.json', 'users.json', 'setup-password'))):
        raise rt.PublicError('First administrator enrollment is closed; sign in or use authorized account recovery')
    return state


def load():
    now = time.monotonic(); boot = enrollment.boot_id()
    path = rt.STATE / FILE
    try:
        if path.is_symlink() or (path.exists() and path.stat().st_size > 131072): raise ValueError()
        value = rt.read_json(path, {'version': 1, 'boot': boot, 'sessions': {}, 'attempts': {}, 'created': {}})
        if (not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1
                or not isinstance(value.get('boot'), str)
                or any(not isinstance(value.get(k), dict) for k in ('sessions', 'attempts', 'created'))):
            raise ValueError()
        if value['boot'] != boot:
            return {'version': 1, 'boot': boot, 'sessions': {}, 'attempts': {}, 'created': {}}
        if len(value['sessions']) > 64 or any(len(value[k]) > 128 for k in ('attempts', 'created')): raise ValueError()
        for key, item in list(value['sessions'].items()):
            if (not isinstance(key, str) or not re.fullmatch(r'[0-9a-f]{64}', key) or not isinstance(item, dict)
                    or type(item.get('expires')) not in (int, float) or not math.isfinite(item['expires'])
                    or not isinstance(item.get('csrfHash'), str) or not re.fullmatch(r'[0-9a-f]{64}', item['csrfHash'])
                    or not isinstance(item.get('address'), str) or not isinstance(item.get('origin'), str)):
                raise ValueError()
            if now >= item['expires']: del value['sessions'][key]
        for group in ('attempts', 'created'):
            for address, times in list(value[group].items()):
                if (not isinstance(address, str) or not isinstance(times, list) or len(times) > 64
                        or any(type(t) not in (int, float) or not math.isfinite(t) for t in times)):
                    raise ValueError()
                value[group][address] = [t for t in times if 0 <= now - t < 300]
                if not value[group][address]: del value[group][address]
        return value
    except (ValueError, OSError, TypeError):
        raise rt.PublicError('Bootstrap session state is unavailable; inspect supported root state or use console recovery') from None


def save(value):
    rt.write_json(rt.STATE / FILE, value)


def scope(address, origin):
    try:
        ipaddress.ip_address(address)
        if not isinstance(origin, str) or not origin.startswith('https://') or len(origin) > 256: raise ValueError()
    except (ValueError, TypeError):
        raise rt.PublicError('Invalid bootstrap request scope') from None


def charge(value, group, address, limit, global_limit):
    bucket = value[group]
    if len(bucket.get(address, [])) >= limit or sum(map(len, bucket.values())) >= global_limit or len(bucket) >= 128:
        raise rt.PublicError('Too many bootstrap attempts; wait five minutes before retrying')
    bucket.setdefault(address, []).append(time.monotonic())


def checked(value, token, csrf, address, origin):
    scope(address, origin)
    if not isinstance(token, str) or not TOKEN.fullmatch(token) or not isinstance(csrf, str):
        raise rt.PublicError('Bootstrap session expired or request token is invalid; reload and verify this browser again')
    record = value['sessions'].get(digest(token))
    if (not record or record['address'] != address or record['origin'] != origin
            or not secrets.compare_digest(record['csrfHash'], digest(csrf))):
        raise rt.PublicError('Bootstrap session expired or request token is invalid; reload and verify this browser again')
    return record


def start(token, address, origin):
    scope(address, origin)
    with rt.locked(blocking=False):
        state = require_open(); value = load()
        key = digest(token) if isinstance(token, str) and TOKEN.fullmatch(token) else None
        record = value['sessions'].get(key)
        if record and (record['address'] != address or record['origin'] != origin):
            raise rt.PublicError('Bootstrap browser scope changed; reload on the trusted management address')
        if not record:
            charge(value, 'created', address, 8, 64)
            if len(value['sessions']) >= 64:
                raise rt.PublicError('Bootstrap sessions are busy; wait and retry')
            token = secrets.token_urlsafe(32); key = digest(token)
            record = {'address': address, 'origin': origin, 'expires': time.monotonic() + TTL,
                      'csrfHash': digest(material(token, 'csrf'))}
            value['sessions'][key] = record
        save(value)
        code = material(token, 'console')[:32]
        proof = state.get('proof', {})
        approved = (proof.get('boot') == enrollment.boot_id() and time.monotonic() < proof.get('expires', 0)
                    and secrets.compare_digest(proof.get('digest', ''), enrollment.proof_digest(code)))
        # token is a PRIVATE broker-to-frontend cookie delivery field, stripped
        # before ordinary browser JSON serialization.
        return {'token': token, 'csrf': material(token, 'csrf'), 'approvalCode': code,
                'approved': approved, 'expiresIn': max(0, int(record['expires'] - time.monotonic()))}


def verify(token, csrf, address, origin, username, password):
    with rt.locked(blocking=False):
        require_open(); value = load(); record = checked(value, token, csrf, address, origin)
        if record.get('checkingUntil', 0) > time.monotonic():
            raise rt.PublicError('Host verification is busy; wait and retry')
        charge(value, 'attempts', address, 5, 20)
        nonce = secrets.token_hex(16)
        record.update(checking=nonce, checkingUntil=time.monotonic() + 15)
        save(value)
    try:
        import host_proof
        principal = host_proof.verify(username, password)
    except Exception:
        # Preserve the rate charge but allow a corrected password immediately.
        try:
            with rt.locked(blocking=False):
                value = load(); record = checked(value, token, csrf, address, origin)
                if record.get('checking') == nonce:
                    record.pop('checking', None); record.pop('checkingUntil', None); save(value)
        except (ValueError, OSError):
            pass
        # Do not copy helper/PAM details to the browser, log or journal.
        raise rt.PublicError('Host verification failed; check the local administrator credentials or use console approval') from None
    with rt.locked(blocking=False):
        require_open(); value = load(); record = checked(value, token, csrf, address, origin)
        if record.get('checking') != nonce:
            raise rt.PublicError('Host verification expired; verify this browser again')
        record.pop('checking', None); record.pop('checkingUntil', None); save(value)
        enrollment.save('host-verified', proof={'digest': enrollment.proof_digest(material(token, 'console')[:32]),
            'boot': enrollment.boot_id(), 'expires': min(record['expires'], time.monotonic() + TTL)})
        rt.audit('bootstrap-host-verified', principal=principal)
    return {'approved': True}


def claim(token, csrf, address, origin, username, password):
    with rt.locked(blocking=False):
        require_open(); value = load(); checked(value, token, csrf, address, origin)
        result = enrollment.claim_locked(enrollment.proof_digest(material(token, 'console')[:32]), password, username)
        # All first-claim browsers lose their capability at the same commit.
        value['sessions'] = {}; save(value)
        return result
