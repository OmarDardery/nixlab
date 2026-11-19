"""Disposable real PAM/sudo, HTTPS, restart/reboot and console qualification."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import ssl
import subprocess
import sys
import termios
import time
import urllib.error
import urllib.request

import accounts
import browser_bootstrap as bootstrap
import enrollment
import runtime as rt

ORIGIN = 'https://127.0.0.1:9440'
PASSWORDS = ('vm-enrollment-first-password', 'vm-enrollment-second-password')
HOST_PASSWORD = 'vm-cockpit-password'
OUTSIDER_PASSWORD = 'vm-nonadmin-password'
RECOVERY_PASSWORD = 'vm-enrollment-root-recovered'
WRONG_PASSWORD = 'vm-host-proof-wrong-password'
NAMES = ('auth.json', 'users.json', 'setup-password', enrollment.STATE_FILE, bootstrap.FILE,
         'web-enrollment', 'web-enrollment-approval.json')
STASH = rt.STATE / 'enrollment-fixture-original'


def service(action):
    subprocess.run(['systemctl', action, 'nixlab-ui.service', 'nixlab-admin-backend.service'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def request(path, body=None, session=None):
    headers = {'Origin': ORIGIN}
    if session: headers |= {'Cookie': session['cookie'], 'X-Nixlab-CSRF': session['csrf']}
    if body is not None: headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(ORIGIN + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        response = urllib.request.urlopen(req, context=ssl.create_default_context(cafile=str(rt.STATE / 'ca.crt')), timeout=30)
    except urllib.error.HTTPError as exc:
        response = exc
    raw = response.read()
    assert all(password.encode() not in raw for password in (*PASSWORDS, HOST_PASSWORD, OUTSIDER_PASSWORD, RECOVERY_PASSWORD, WRONG_PASSWORD)), 'Enrollment response disclosed a password'
    return response.status, json.loads(raw), response.headers


def browser():
    status, result, headers = request('/api/bootstrap')
    assert status == 200 and 'token' not in result, 'Browser proof session unavailable or leaked cookie token'
    return result | {'cookie': headers['Set-Cookie'].split(';')[0]}


def wait():
    for _ in range(100):
        try:
            if request('/api/enrollment')[0] == 200: return
        except (OSError, urllib.error.URLError): pass
        time.sleep(.1)
    raise AssertionError('Portal did not restart')


def console_approve(code):
    master, slave = pty.openpty()
    def terminal():
        os.setsid(); fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    child = subprocess.Popen(['nixlab-admin', 'approve-enrollment'], stdin=slave, stdout=slave, stderr=slave, preexec_fn=terminal)
    output = bytearray()
    try:
        deadline = time.monotonic() + 15
        while b'Code displayed in your browser: ' not in output and time.monotonic() < deadline:
            if select.select([master], [], [], .1)[0]:
                try: output.extend(os.read(master, 4096))
                except OSError: break
        assert b'Code displayed in your browser: ' in output, 'Private console approval prompt unavailable'
        assert not termios.tcgetattr(slave)[3] & termios.ECHO, 'Console approval echoed input'
        os.write(master, code.encode() + b'\n')
        child.wait(timeout=15)
        while select.select([master], [], [], .1)[0]:
            try:
                data = os.read(master, 4096)
                if not data: break
                output.extend(data)
            except OSError: break
        assert child.returncode == 0, 'Local-root console approval failed'
        assert code.encode() not in output, 'Console output disclosed browser material'
    finally:
        if child.poll() is None: child.kill(); child.wait()
        os.close(master); os.close(slave)


if sys.argv[1] == 'prepare':
    service('stop'); STASH.mkdir(mode=0o700)
    for name in NAMES:
        if (rt.STATE / name).exists(): os.replace(rt.STATE / name, STASH / name)
    accounts.enable_enrollment()
    service('start'); wait()
    current = browser()
    # Actual PAM and sudo policy, including a valid-password nonadministrator.
    for username, password in (('operator', WRONG_PASSWORD), ('outsider', OUTSIDER_PASSWORD),
                               ('lockedadmin', HOST_PASSWORD), ('missing-host-user', HOST_PASSWORD)):
        assert request('/api/bootstrap/verify', {'hostUsername': username, 'hostPassword': password}, current)[0] == 400, 'Ineligible host identity approved'
    assert request('/api/bootstrap/verify', {'hostUsername': 'operator', 'hostPassword': HOST_PASSWORD}, current)[0] == 200, 'Real PAM/sudo administrator rejected'
    current = request('/api/bootstrap', session=current)[1] | {'cookie': current['cookie']}
    assert current['approved'], 'PAM verification did not approve its browser'
    service('restart'); wait()
    resumed = request('/api/bootstrap', session=current)[1]
    assert resumed['approved'] and resumed['expiresIn'] <= current['expiresIn'], 'Service restart lost or extended host proof'
    state = bootstrap.load(); state['sessions'][bootstrap.digest(current['cookie'].split('=',1)[1])]['expires'] = time.monotonic() - 1
    bootstrap.save(state)
    assert request('/api/bootstrap/claim', {'username': 'labowner', 'password': PASSWORDS[0]}, current)[0] == 400, 'Expired browser proof accepted'
    current = browser()
    # Key-only administrators can use the actual root CLI on a private terminal.
    console_approve(current['approvalCode'])
    assert request('/api/bootstrap', session=current)[1]['approved'], 'Console approval not bound to browser'
    rt.write_json(STASH / 'browser.json', current)
    # Leave that live console-approved browser across the actual VM reboot.
elif sys.argv[1] == 'verify':
    wait(); previous = rt.read_json(STASH / 'browser.json')
    assert request('/api/bootstrap/claim', {'username': 'labowner', 'password': PASSWORDS[0]}, previous)[0] == 400, 'Previous-boot browser proof accepted'
    assert not enrollment.account_valid(), 'Rejected proof created an account'
    current = browser()
    assert request('/api/bootstrap/verify', {'hostUsername': 'operator', 'hostPassword': HOST_PASSWORD}, current)[0] == 200, 'Fresh PAM proof failed after reboot'
    other = browser()
    assert request('/api/bootstrap/verify', {'hostUsername': 'operator', 'hostPassword': HOST_PASSWORD}, other)[0] == 200, 'Second eligible browser could not verify its host admin'
    pairs = list(zip((current, other), PASSWORDS))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: request('/api/bootstrap/claim', {'username': 'labowner', 'password': pair[1]}, pair[0]), pairs))
    if not any(status == 200 for status, _, _ in results):
        # The existing nonblocking mutation lock may ask the eligible claimant
        # to retry while the other browser's obsolete approval is rejected.
        results[1] = request('/api/bootstrap/claim', {'username': 'labowner', 'password': PASSWORDS[1]}, other)
    assert sum(status == 200 for status, _, _ in results) == 1, 'More than one first claim committed'
    assert sum(accounts.matches('labowner', password) for password in PASSWORDS) == 1, 'Conflicting first-admin verifiers'
    success = next(item for item in results if item[0] == 200)
    cookies = success[2].get_all('Set-Cookie')
    cookie = next(item.split(';')[0] for item in cookies if item.startswith('nixlab_session='))
    assert any('__Host-nixlab_bootstrap=' in item and 'Max-Age=0' in item for item in cookies), 'Bootstrap cookie was not retired'
    ordinary = {'cookie': cookie, 'csrf': success[1]['csrf']}
    assert request('/api/session', session=ordinary)[0] == 200, 'No ordinary portal session after enrollment'
    assert request('/api/bootstrap/claim', {'username': 'labowner', 'password': PASSWORDS[0]}, current)[0] == 400, 'Consumed browser proof replayed'
    assert request('/api/enrollment', {'code': current['approvalCode'], 'password': PASSWORDS[0]})[0] == 401, 'Copied code bypassed browser binding'
    (rt.STATE / 'users.json').unlink()
    assert request('/api/enrollment')[1] == {'available': False, 'recoveryRequired': True}, 'Missing verifier reopened first claim'
    accounts.recover_password(RECOVERY_PASSWORD)
    assert request('/api/session', session=ordinary)[0] == 401, 'Recovery left the old session usable'
    assert request('/api/login', {'password': RECOVERY_PASSWORD})[0] == 200, 'Root recovery failed'
    assert request('/api/enrollment')[1]['available'] is False, 'Recovery reopened first claim'
    for name in (enrollment.STATE_FILE, bootstrap.FILE):
        assert (rt.STATE / name).stat().st_mode & 0o777 == 0o600
        assert subprocess.run(['runuser', '-u', 'nixlab-ui', '--', 'test', '-r', str(rt.STATE / name)]).returncode != 0, 'Web uid can read private bootstrap authority'
    journal = subprocess.run(['journalctl', '--no-pager', '-o', 'cat', '-u', 'nixlab-ui', '-u', 'nixlab-admin-backend'], check=True, capture_output=True).stdout
    assert all(value.encode() not in journal for value in (*PASSWORDS, HOST_PASSWORD, OUTSIDER_PASSWORD, RECOVERY_PASSWORD, WRONG_PASSWORD, current['approvalCode'], current['cookie'].split('=',1)[1], other['approvalCode'], other['cookie'].split('=',1)[1])), 'Journal disclosed bootstrap credentials'
    service('stop')
    for name in NAMES:
        rt.durable_unlink(rt.STATE / name)
        if (STASH / name).exists(): os.replace(STASH / name, rt.STATE / name)
    (STASH / 'browser.json').unlink(); STASH.rmdir()
    service('start'); wait()
else:
    raise SystemExit('Choose prepare or verify')
