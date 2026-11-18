"""Run inside the disposable NixOS VM against actual HTTPS and UNIX-socket services."""
import base64
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import tarfile
import urllib.error
import urllib.request
import socket
import warnings

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


# Exercise the real NixOS TLS service, not just a mocked parser.
def wire_request(packet):
    with socket.create_connection(('127.0.0.1', 9440), timeout=8) as raw:
        with context.wrap_socket(raw, server_hostname='127.0.0.1') as tls:
            tls.settimeout(8)
            tls.sendall(packet)
            return tls.recv(4096)


for packet, expected in (
    (b'GET /api/status HTTP/1.1\\r\\nHost: 127.0.0.1:9440\\r\\n'
     b'Content-Length: 2\\r\\nConnection: close\\r\\n\\r\\n{}', b'400'),
    (b'GET /api/status HTTP/1.1\\r\\nHost: 127.0.0.1:9555\\r\\n'
     b'Connection: close\\r\\n\\r\\n', b'401'),
    (b'POST /api/login HTTP/1.1\\r\\nHost: 127.0.0.1:9440\\r\\n'
     b'Content-Length: 2\\r\\nContent-Length: 2\\r\\n'
     b'Connection: close\\r\\n\\r\\n{}', b'400'),
):
    response = wire_request(packet)
    assert expected in response.split(b'\\r\\n', 1)[0], response[:160]

with warnings.catch_warnings():
    warnings.simplefilter('ignore', DeprecationWarning)
    legacy = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    legacy.check_hostname = False
    legacy.verify_mode = ssl.CERT_NONE
    legacy.minimum_version = ssl.TLSVersion.TLSv1
    legacy.maximum_version = ssl.TLSVersion.TLSv1_1
    try:
        with socket.create_connection(('127.0.0.1', 9440), timeout=8) as raw:
            with legacy.wrap_socket(raw, server_hostname='127.0.0.1'):
                raise AssertionError('Insecure TLS 1.0/1.1 connected')
    except (ssl.SSLError, OSError):
        pass

assert request('/api/status')[0] == 401
assert request('/api/guest-network')[0] == 401
assert request('/api/main-revision')[0] == 401
admin = login('admin', 'vm-integration-password')
# A stale/absent CSRF header must not log out a valid browser.
wire = (b'POST /api/logout HTTP/1.1\\r\\nHost: 127.0.0.1:9440\\r\\n'
        b'Origin: https://127.0.0.1:9440\\r\\nCookie: ' + admin['cookie'].encode() +
        b'\\r\\nContent-Length: 2\\r\\nConnection: close\\r\\n\\r\\n{}')
assert b'401' in wire_request(wire).split(b'\\r\\n', 1)[0]
assert request('/api/session', session=admin)[0] == 200

# Disposable legacy input: the authenticated replacement must commit before
# this private file is removed. Never echo the value through an API/console.
legacy = Path('/var/lib/nixlab-admin/setup-password')
descriptor = os.open(legacy, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
with os.fdopen(descriptor, 'w') as stream:
    os.fchmod(stream.fileno(), 0o600)
    stream.write('vm-integration-password')
for endpoint in ('/api/credentials', '/api/arcane/credentials', '/api/setup-password', '/api/operator-password'):
    assert request(endpoint, session=admin)[0] == 404
assert request('/api/status', session=admin)[0] == 200
status, guide, _ = request('/docs/setup.md', session=admin)
assert status == 200 and b'Install on an existing NixOS system' in guide
status, response, _ = request('/api/users', {'username': 'observer', 'role': 'viewer', 'password': 'vm-observer-password',
                                           'current': 'vm-integration-password'}, admin)
assert status == 200, response
viewer = login('observer', 'vm-observer-password')
assert request('/api/status', session=viewer)[0] == 200
status, response, _ = request('/api/guest-network', session=viewer)
assert status == 200 and response == {'available': False, 'guests': [], 'conflicts': []}, response
assert request('/api/main-revision', session=viewer)[0] == 403
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
status, response, _ = request('/api/password', {'current': 'vm-integration-password', 'password': 'vm-rotated-portal-password'}, admin)
assert status == 200 and response == {'ok': True}
assert not legacy.exists()
assert request('/api/status', session=admin)[0] == 401
assert request('/api/login', {'password': 'vm-integration-password'})[0] == 400
rotated = login('admin', 'vm-rotated-portal-password')
assert request('/api/status', session=rotated)[0] == 200
print('Actual NixOS HTTPS, UNIX broker, role enforcement, password rotation, legacy retirement and encrypted recovery passed.')

