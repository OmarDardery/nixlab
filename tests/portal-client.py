"""Run inside the disposable NixOS VM against actual HTTPS and UNIX-socket services."""
import base64
import io
import json
from pathlib import Path
import ssl
import subprocess
import tarfile
import urllib.error
import urllib.request

origin = 'https://127.0.0.1:9440'
context = ssl.create_default_context(cafile='/var/lib/nixlab-admin/ca.crt')


def request(path, body=None, session=None):
    headers = {'Origin': origin}
    if session:
        headers |= {'Cookie': session['cookie'], 'X-Nixlab-CSRF': session['csrf']}
    if body is not None: headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(origin + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        response = urllib.request.urlopen(req, context=context, timeout=60)
    except urllib.error.HTTPError as exc:
        response = exc
    data = response.read()
    payload = json.loads(data) if 'application/json' in response.headers.get('Content-Type', '') else data
    return response.status, payload, response.headers


def login(username, password):
    status, result, headers = request('/api/login', {'username': username, 'password': password})
    assert status == 200, result
    return result | {'cookie': headers['Set-Cookie'].split(';')[0]}


assert request('/api/status')[0] == 401
admin = login('admin', 'vm-integration-password')
assert request('/api/status', session=admin)[0] == 200
status, guide, _ = request('/docs/setup.md', session=admin)
assert status == 200 and b'Install on an existing NixOS system' in guide
status, response, _ = request('/api/users', {'username': 'observer', 'role': 'viewer', 'password': 'vm-observer-password',
                                           'current': 'vm-integration-password'}, admin)
assert status == 200, response
viewer = login('observer', 'vm-observer-password')
assert request('/api/status', session=viewer)[0] == 200
assert request('/api/review', {'settings': {}}, viewer)[0] == 403
assert request('/api/operation', {'action': 'start', 'name': 'guest'}, viewer)[0] == 403
assert request('/api/recovery-export', {}, viewer)[0] == 403
status, response, _ = request('/api/users', {'username': 'observer', 'delete': True, 'current': 'vm-integration-password'}, admin)
assert status == 200, response
assert request('/api/status', session=viewer)[0] == 401
status, response, _ = request('/api/recovery-export', {'current': 'vm-integration-password'}, admin)
assert status == 200, response
archive = base64.b64decode(response['files']['nixlab-config.tar.gz'])
with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as stream:
    assert all(not name.startswith(('admin/', 'runtime/')) for name in stream.getnames())
    assert 'config/machine/settings.json' in stream.getnames()
subprocess.run(['age-keygen', '-o', '/tmp/recovery-test.key'], check=True, capture_output=True)
recipient = subprocess.check_output(['age-keygen', '-y', '/tmp/recovery-test.key'], text=True).strip()
subprocess.run(['nixlab-admin', 'recovery', 'export', '/tmp/recovery-test', '--include-secrets', '--recipient', recipient], check=True)
subprocess.run(['age', '-d', '-i', '/tmp/recovery-test.key', '-o', '/tmp/recovery-test-secrets.tar.gz',
                '/tmp/recovery-test/nixlab-secrets.tar.gz.age'], check=True)
with tarfile.open('/tmp/recovery-test-secrets.tar.gz', 'r:gz') as stream:
    assert 'admin/auth.json' in stream.getnames()
    assert 'admin/ca.key' in stream.getnames()
print('Actual NixOS HTTPS, UNIX broker, role enforcement, session revocation and encrypted recovery passed.')

