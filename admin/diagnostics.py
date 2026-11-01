"""Bounded management probes. No tokens, passwords or raw service logs in reports."""
import http.client
import json
from pathlib import Path
import ssl
import subprocess
import time

import runtime as rt


def management_address(settings):
    interface = settings['management']['interface']
    result = subprocess.run(['ip', '-j', '-4', 'address', 'show', 'dev', interface],
                            check=True, capture_output=True, text=True, timeout=10)
    assigned = [a['local'] for link in json.loads(result.stdout) for a in link.get('addr_info', [])
                if a.get('family') == 'inet' and a.get('scope') == 'global']
    configured = settings['management']['address']
    if configured in assigned:
        return configured
    if assigned and not settings['management']['configure']:
        return assigned[0]
    raise ValueError('Management interface has no usable address: ' + interface)


def request(address, port, path, body=None):
    # The host-created CA is trusted locally; hostname checking remains enabled.
    context = ssl.create_default_context(cafile=str(rt.STATE / 'ca.crt'))
    conn = http.client.HTTPSConnection(address, port, context=context, timeout=5)
    try:
        conn.request('POST' if body is not None else 'GET', path,
                     json.dumps(body) if body is not None else None,
                     {'Content-Type': 'application/json'} if body is not None else {})
        response = conn.getresponse()
        data = response.read(65537)
        if len(data) > 65536:
            raise ValueError('Oversized management response')
        return response.status, data
    finally:
        conn.close()


def probe(settings):
    address = management_address(settings)
    targets = [('Incus API', 8443, '/1.0', {200}), ('Incus UI', 8443, '/ui/', {200})]
    if settings['setup']['enable']:
        targets.append(('Nixlab', settings['setup']['port'], '/', {200}))
    if settings['cockpit']['enable']:
        targets.append(('Cockpit', settings['cockpit']['port'], '/ping', {200}))
    if settings['apps']['enable']:
        targets += [('Portainer', settings['apps']['portainerPort'], '/api/status', {200}),
                    ('Portainer administrator', settings['apps']['portainerPort'], '/api/users/admin/check', {204})]
    checks = []
    for name, port, path, expected in targets:
        try:
            status, _ = request(address, port, path)
            checks.append({'name': name, 'url': f'https://{address}:{port}', 'ok': status in expected,
                           'detail': 'HTTP ' + str(status)})
        except (OSError, ValueError, http.client.HTTPException) as exc:
            checks.append({'name': name, 'url': f'https://{address}:{port}', 'ok': False,
                           'detail': type(exc).__name__})
    return {'address': address, 'ok': all(check['ok'] for check in checks), 'checks': checks}


def wait_ready(settings, timeout=180):
    deadline = time.monotonic() + timeout
    report = None
    while True:
        try:
            report = probe(settings)
            if report['ok']:
                return report
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            report = {'ok': False, 'error': str(exc)}
        if time.monotonic() >= deadline:
            rt.write_json(rt.STATE / 'last-health.json', report)
            raise ValueError('Management health checks failed: ' + json.dumps(report) + '; run sudo nixlab doctor')
        time.sleep(2)


def identity(settings):
    address = management_address(settings)
    cert = rt.STATE / 'tls.crt'
    check = subprocess.run(['openssl', 'x509', '-in', str(cert), '-noout', '-checkip', address],
                           capture_output=True, text=True, timeout=10) if cert.is_file() else None
    valid = check is not None and check.returncode == 0 and 'does match certificate' in check.stdout
    if not valid:
        rt.issue_tls(address, settings['hostName'])
    return address


def doctor(settings):
    try:
        report = probe(settings)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report = {'ok': False, 'error': str(exc)}
    report['services'] = []
    for unit in ('incus', 'incus-preseed', 'nixlab-instances', 'nixlab-management-identity',
                 'nixlab-admin-backend', 'nixlab-ui', 'cockpit.socket', 'nixlab-apps-onboarding', 'nginx'):
        result = subprocess.run(['systemctl', 'show', unit, '-p',
                                 'Id,LoadState,ActiveState,SubState,Result,ExecMainStatus'],
                                capture_output=True, text=True, timeout=10)
        data = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if data.get('LoadState') == 'loaded':
            report['services'].append(data)
    active = rt.read_json(rt.STATE / 'active.json', {})
    if active.get('id'):
        job = rt.read_json(rt.STATE / 'jobs' / active['id'] / 'job.json', {})
        report['transaction'] = {key: job[key] for key in ('id', 'status', 'error', 'rollbackError') if key in job}
        report['log'] = str(rt.STATE / 'jobs' / active['id'] / 'build.log')
    rt.write_json(rt.STATE / 'last-health.json', report)
    return report
