"""Disposable real apps/Portainer test; never prints passwords, JWTs or bodies."""
import json
from pathlib import Path
import secrets
import ssl
import subprocess
import time
import urllib.request

import deployments as d
import runtime as rt
from guest_deploy import Transport

password = secrets.token_hex(24)
root = Path('/tmp/nixlab-kvm-credentials'); root.mkdir(mode=0o700)
rt.atomic(root / 'password', password)
subprocess.run(['openssl', 'req', '-new', '-newkey', 'rsa:2048', '-nodes', '-x509', '-days', '1',
                '-subj', '/CN=apps.internal', '-addext', 'subjectAltName=DNS:apps.internal,IP:10.77.0.10',
                '-keyout', str(root / 'tls.key'), '-out', str(root / 'tls.crt')], check=True,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
apps = Transport('lab', 'apps')
assert apps.inspect()['type'] == 'virtual-machine'
apps.execute('install', '-d', '-m', '0700', '/var/lib/nixlab-secrets')
for file, destination, mode in [('password', 'portainer-admin-password', '0600'), ('tls.key', 'tls.key', '0600'), ('tls.crt', 'tls.crt', '0644')]:
    subprocess.run(['incus', '--force-local', 'file', 'push', str(root / file), 'apps/var/lib/nixlab-secrets/' + destination,
                    '--project', 'lab', '--mode', mode], check=True, stdout=subprocess.DEVNULL)
apps.execute('systemctl', 'restart', 'docker-portainer.service', timeout=120)
context = ssl.create_default_context(cafile=str(root / 'tls.crt'))


def authenticated():
    request = urllib.request.Request('https://10.77.0.10:9443/api/auth',
        data=json.dumps({'Username': 'admin', 'Password': password}).encode(), headers={'Content-Type': 'application/json'})
    deadline = time.monotonic() + 90
    while True:
        try:
            with urllib.request.urlopen(request, context=context, timeout=5) as response:
                assert response.status == 200 and json.load(response).get('jwt')
            return
        except (OSError, ValueError):
            if time.monotonic() >= deadline: raise AssertionError('Real Portainer authentication did not become ready') from None
            time.sleep(2)


authenticated()
manifest = rt.read_json(Path('/etc/nixlab-test-deployments.json'))
identities = {}
for name in ('apps', 'worker'):
    transport = Transport('lab', name); identities[name] = transport.inspect()['uuid']
    transport.execute('mkdir', '-p', '/var/lib/nixlab')
    transport.execute('touch', '/var/lib/nixlab/preserved-data')
job_id = 'd' * 32
state = rt.STATE; directory = state / 'jobs' / job_id; directory.mkdir(parents=True)
source = directory / 'source'; source.mkdir(); rt.write_json(source / '.nixlab-files.json', [])
(directory / 'system').symlink_to('/run/current-system')
rt.write_json(directory / 'job.json', {'id': job_id, 'status': 'confirmed', 'baseDigest': 'fixture', 'candidateDigest': rt.digest_source(source)})
rt.write_json(state / 'active.json', {'id': job_id})
d.prepare(directory, manifest)
manager = d.Deployments(state / 'source', state); manager.spawn = lambda _: None
manager.submit(job_id)
report = manager.run(job_id)
assert report['status'] == 'healthy', report
for name in ('apps', 'worker'):
    transport = Transport('lab', name); current = transport.inspect()
    assert current['uuid'] == identities[name] and current['active'] == manifest[name]['system']
    transport.execute('test', '-e', '/var/lib/nixlab/preserved-data')
    assert transport.execute('cat', '/etc/nixlab-deploy-version').stdout.strip() == 'v2'
authenticated()
print('Real apps VM and second NixOS guest updated in place; Portainer authentication and persistent data survived.')
