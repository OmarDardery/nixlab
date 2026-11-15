"""Disposable real-service enrollment restart/reboot qualification; no secret output."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

import accounts
import enrollment
import runtime as rt

ORIGIN = 'https://127.0.0.1:9440'
CODE = 'b' * 32
PASSWORDS = ('vm-enrollment-first-password', 'vm-enrollment-second-password')
NAMES = ('auth.json', 'users.json', 'setup-password', enrollment.STATE_FILE,
         'web-enrollment', 'web-enrollment-approval.json')
STASH = rt.STATE / 'enrollment-fixture-original'


def service(action):
    subprocess.run(['systemctl', action, 'nixlab-ui.service', 'nixlab-admin-backend.service'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def request(path, body=None, cookie=None):
    headers = {'Origin': ORIGIN}
    if cookie: headers['Cookie'] = cookie
    if body is not None: headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(ORIGIN + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        response = urllib.request.urlopen(req, context=ssl.create_default_context(cafile=str(rt.STATE / 'ca.crt')), timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    raw = response.read()
    # Report fixed assertion categories; never dump request/response payloads.
    assert all(password.encode() not in raw for password in PASSWORDS), 'Enrollment response disclosed a password'
    assert CODE.encode() not in raw, 'Enrollment response disclosed approval material'
    return response.status, json.loads(raw), response.headers


def wait():
    for _ in range(100):
        try:
            if request('/api/enrollment')[0] == 200: return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(.1)
    raise AssertionError('Portal did not restart')


if sys.argv[1] == 'prepare':
    service('stop')
    STASH.mkdir(mode=0o700)
    for name in NAMES:
        if (rt.STATE / name).exists(): os.replace(rt.STATE / name, STASH / name)
    accounts.enable_enrollment()
    accounts.approve_enrollment(CODE)
    service('start'); wait()
    assert request('/api/enrollment')[1]['available'], 'Fresh opt-in enrollment unavailable'
    # Full broker/web restart must retain the same bounded approval.
    service('restart'); wait()
    assert request('/api/enrollment')[1]['available'], 'Service restart lost enrollment state'
    value = enrollment.load(); value['proof']['expires'] = time.monotonic() - 1
    rt.write_json(rt.STATE / enrollment.STATE_FILE, value)
    assert request('/api/enrollment', {'code': CODE, 'password': PASSWORDS[0]})[0] == 400, 'Expired approval accepted'
    accounts.approve_enrollment(CODE)
    # Leave a live approval for the actual VM reboot below.
elif sys.argv[1] == 'verify':
    wait()
    assert request('/api/enrollment', {'code': CODE, 'password': PASSWORDS[0]})[0] == 400, 'Previous-boot approval accepted'
    assert not (rt.STATE / 'auth.json').exists(), 'Rejected approval created an account'
    accounts.approve_enrollment(CODE)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda password: request('/api/enrollment', {'code': CODE, 'password': password}), PASSWORDS))
    assert sum(status == 200 and result == {'ok': True} for status, result, _ in results) == 1, 'More than one first claim committed'
    assert sum(accounts.matches('admin', password) for password in PASSWORDS) == 1, 'Conflicting first-admin verifiers'
    winning = next(password for password in PASSWORDS if accounts.matches('admin', password))
    status, _, headers = request('/api/login', {'password': winning})
    assert status == 200, 'Committed first administrator cannot sign in'
    cookie = headers['Set-Cookie'].split(';')[0]
    assert request('/api/session', cookie=cookie)[0] == 200, 'Fresh session rejected'
    assert request('/api/enrollment', {'code': CODE, 'password': PASSWORDS[1]})[1].get('alreadyComplete'), 'Lost-response retry not idempotent'
    (rt.STATE / 'auth.json').unlink()
    assert request('/api/enrollment')[1] == {'available': False, 'recoveryRequired': True}, 'Missing verifier reopened claim'
    accounts.recover_password('vm-enrollment-root-recovered')
    assert request('/api/session', cookie=cookie)[0] == 401, 'Root recovery left the old session usable'
    assert request('/api/login', {'password': 'vm-enrollment-root-recovered'})[0] == 200, 'Root-authorized recovery failed'
    assert request('/api/enrollment')[1]['available'] is False, 'Recovery reopened anonymous enrollment'
    assert (rt.STATE / enrollment.STATE_FILE).stat().st_mode & 0o777 == 0o600
    assert subprocess.run(['runuser', '-u', 'nixlab-ui', '--', 'test', '-r', str(rt.STATE / enrollment.STATE_FILE)]).returncode != 0, 'Web process can read private claim authority'
    journal = subprocess.run(['journalctl', '--no-pager', '-o', 'cat', '-u', 'nixlab-ui', '-u', 'nixlab-admin-backend'], check=True, capture_output=True).stdout
    assert all(value.encode() not in journal for value in (*PASSWORDS, 'vm-enrollment-root-recovered', CODE)), 'Public journal disclosed enrollment secrets'
    service('stop')
    for name in NAMES:
        rt.durable_unlink(rt.STATE / name)
        if (STASH / name).exists(): os.replace(STASH / name, rt.STATE / name)
    STASH.rmdir()
    service('start'); wait()
else:
    raise SystemExit('Choose prepare or verify')
