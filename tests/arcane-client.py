"""Real Arcane qualification inside a disposable VM; never print API bodies/secrets."""
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path('/var/lib/nixlab-arcane-test')
ORIGIN = 'https://127.0.0.2:9443'
context = ssl.create_default_context(cafile='/var/lib/nixlab-secrets/tls.crt')
password = (ROOT / 'login-password').read_text().strip()


def request(path, body=None, token=None, raw=None, content_type='application/json'):
    headers = {'Origin': ORIGIN}
    if token: headers['Authorization'] = 'Bearer ' + token
    data = raw if raw is not None else json.dumps(body).encode() if body is not None else None
    if data is not None: headers['Content-Type'] = content_type
    req = urllib.request.Request(ORIGIN + path, data=data, headers=headers)
    try: response = urllib.request.urlopen(req, context=context, timeout=120)
    except urllib.error.HTTPError as exc: response = exc
    result = response.read(4 * 1024 * 1024)
    if path != '/api/auth/login':
        assert password.encode() not in result, 'Arcane API disclosed a password'
    return response.status, result


def login():
    status, raw = request('/api/auth/login', {'username': 'admin', 'password': password})
    assert status == 200, 'Actual Arcane administrator login failed'
    response = json.loads(raw)
    token = response['data']['token']
    assert isinstance(token, str) and token, 'Arcane did not issue authentication token'
    return token


def reset_in_terminal(replacement):
    result = subprocess.run(['nixlab-arcane-credentials','reset'],input=replacement,
                            text=True,capture_output=True,timeout=240)
    assert result.returncode == 0, 'Protected supplied-password recovery failed'
    assert replacement not in result.stdout+result.stderr, 'Recovery disclosed supplied password'


# Docker's published port accepts connections before Arcane has completed its
# database initialization and bound its TLS listener. Wait for application
# readiness, keeping certificate verification enabled on every attempt.
deadline = time.monotonic() + 90
while True:
    try:
        if request('/api/health')[0] == 200:
            break
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        pass
    assert time.monotonic() < deadline, 'Arcane TLS health did not become ready'
    time.sleep(1)

token = login()
assert request('/api/auth/me', token=token)[0] == 200, 'Arcane authenticated API unavailable'
assert request('/api/auth/me')[0] in (401, 403), 'Arcane API allowed anonymous access'
assert request('/api/health')[0] == 200, 'Arcane health unavailable'
assert not Path('/srv/arcane/initial-admin-password').exists(), 'Arcane generated fallback plaintext'
assert not Path('/run/nixlab-arcane/password').exists(), 'Bootstrap plaintext was retained'
assert not Path('/var/lib/nixlab-secrets/arcane-admin-password').exists(), 'Legacy bootstrap plaintext was retained'
key = Path('/srv/arcane/encryption-key')
assert key.stat().st_mode & 0o777 == 0o600, 'Encryption key is not private'
key_hash = hashlib.sha256(key.read_bytes()).hexdigest()
if sys.argv[1] == 'prepare':
    compose = '''services:
  probe:
    image: nixlab-arcane-probe:1
    pull_policy: never
    restart: unless-stopped
    volumes:
      - probe-data:/data
volumes:
  probe-data: {}
'''
    boundary = 'nixlab-arcane-fixture'
    fields = {'project': json.dumps({'name': 'api-probe', 'composeContent': compose}), 'manifest': json.dumps({'fileChanges': []})}
    raw = ''.join('--' + boundary + '\r\nContent-Disposition: form-data; name="' + name + '"\r\n\r\n' + value + '\r\n' for name, value in fields.items()) + '--' + boundary + '--\r\n'
    status, result = request('/api/agents/0/projects', token=token, raw=raw.encode(), content_type='multipart/form-data; boundary=' + boundary)
    assert status in (200, 201), 'Arcane Compose project creation failed'
    project = json.loads(result)['data']['id']
    status, result = request('/api/agents/0/projects/' + project + '/deploy?action=up', {}, token=token)
    assert status == 200, 'Arcane Compose project deployment failed'
    # The data probe deliberately uses no network/registry and keeps a named volume.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        child = subprocess.run(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=api-probe', '--filter', 'status=running'], capture_output=True, text=True, check=True)
        if child.stdout.strip(): break
        time.sleep(1)
    assert child.stdout.strip(), 'Arcane did not start the Compose workload'
    identity = json.loads(request('/api/auth/me', token=token)[1])['data']['id']
    (ROOT / 'evidence.json').write_text(json.dumps({'project': project, 'identity': identity, 'keyHash': key_hash}))
    replacement = password + '-rotated'
    reset_in_terminal(replacement)
    # Bootstrap input has been consumed; restarting must use the durable DB.
    (ROOT / 'login-password').write_text(replacement)
elif sys.argv[1] == 'interrupt':
    # Reproduce the durable state of a crash after the password update, before
    # clearing its pending checkpoint. The next boot must restore the old DB.
    reset_in_terminal(password + '-interrupted')
    marker=Path('/srv/arcane-credential-checkpoint/pending')
    with marker.open('w') as stream:
        stream.write('pending\n'); stream.flush(); os.fsync(stream.fileno())
    directory=os.open(marker.parent,os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
elif sys.argv[1] == 'verify':
    evidence = json.loads((ROOT / 'evidence.json').read_text())
    assert key_hash == evidence['keyHash'], 'Encryption key changed across restart or restore'
    identity = json.loads(request('/api/auth/me', token=token)[1])['data']['id']
    assert identity == evidence['identity'], 'Arcane administrator identity was replaced'
    assert request('/api/agents/0/projects/' + evidence['project'], token=token)[0] == 200, 'Arcane project did not persist'
    assert any(Path('/srv/compose/api-probe').glob('*compose*.y*ml')), 'Compose files did not persist'
else:
    raise AssertionError('Unknown fixture phase')
subprocess.run(['docker', 'run', '--rm', '--pull', 'never', '-v', 'api-probe_probe-data:/data',
                'nixlab-arcane-probe:1', '/bin/sh', '-c', 'test "$(cat /data/record)" = persistent-arcane-test'],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
