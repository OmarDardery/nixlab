"""Disposable real apps/Portainer test; never prints passwords, JWTs or bodies."""
import copy
import json
from pathlib import Path
import secrets
import ssl
import subprocess
import time
import urllib.error
import urllib.request

import deployments as d
import runtime as rt
from guest_deploy import DeploymentError, Transport

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
    failure = 'not ready'
    while True:
        try:
            with urllib.request.urlopen(request, context=context, timeout=5) as response:
                assert response.status == 200
                token = json.load(response)['jwt']
            request_user = urllib.request.Request('https://10.77.0.10:9443/api/users',
                headers={'Authorization': 'Bearer ' + token})
            with urllib.request.urlopen(request_user, context=context, timeout=5) as response:
                users = json.load(response)
                return next(user['Id'] for user in users if user['Username'] == 'admin')
        except urllib.error.HTTPError as exc:
            failure = 'HTTP ' + str(exc.code)
        except urllib.error.URLError as exc:
            failure = type(exc.reason).__name__
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                failure += ' (verification code ' + str(exc.reason.verify_code) + ')'
        except (OSError, ValueError) as exc:
            failure = type(exc).__name__
        if time.monotonic() >= deadline:
            # Fixed metadata only: no API bodies, environment, secrets or JWTs.
            for label, args in (
                ('Portainer unit', ('systemctl', 'show', 'docker-portainer.service', '--property=ActiveState,SubState,Result,ExecMainStatus')),
                ('Container state', ('docker', 'inspect', '--format', '{{.State.Status}} {{.State.ExitCode}}', 'portainer')),
                ('Published ports', ('docker', 'port', 'portainer')),
                ('Firewall unit', ('systemctl', 'is-active', 'nixlab-portainer-firewall.service')),
                ('Guest IPv4', ('ip', '-o', '-4', 'addr', 'show')),
            ):
                result = apps.execute(*args, check=False, timeout=15)
                print(label + ':', result.stdout.strip()[:1000], flush=True)
            result = apps.execute('journalctl', '--no-pager', '-n', '25', '-o', 'cat', '-u', 'docker-portainer.service', check=False, timeout=15)
            for line in result.stdout.splitlines():
                if not any(word in line.lower() for word in ('password', 'token', 'secret', 'private key', 'jwt')):
                    print('Portainer startup:', line.replace(password, '[redacted]')[:1000], flush=True)
            raise AssertionError('Real Portainer authentication did not become ready: ' + failure) from None
        time.sleep(2)


portainer_user = authenticated()
# Preserve safe switch diagnostics from this disposable guest. Production errors
# intentionally omit command output; this fixture contains only generated secrets.
original_execute = Transport.execute


def observed_execute(self, *args, **kwargs):
    check = kwargs.pop('check', True)
    result = original_execute(self, *args, check=False, **kwargs)
    if check and result.returncode:
        print('Guest command failure:', self.name, Path(args[0]).name, 'exit', result.returncode, flush=True)
        if args[:4] == ('timeout', '--kill-after=10s', '600', 'flock'):
            for line in (result.stdout + '\n' + result.stderr).splitlines():
                if not any(word in line.lower() for word in ('password', 'token', 'secret', 'private key', 'jwt')):
                    print('Guest activation:', line.replace(password, '[redacted]')[:1000], flush=True)
        raise DeploymentError('Guest command failed; inspect the guest locally in Incus')
    return result


Transport.execute = observed_execute

# Exercise the production transfer deadline on the dedicated physical runner.
manifest = rt.read_json(Path('/etc/nixlab-test-deployments.json'))
state = rt.STATE
manager = d.Deployments(state / 'source', state)
manager.spawn = lambda _: None
identities = {name: Transport('lab', name).inspect() for name in ('apps', 'worker')}
for name in identities:
    transport = Transport('lab', name)
    transport.execute('mkdir', '-p', '/var/lib/nixlab')
    transport.execute('sh', '-eu', '-c', 'printf "%s\n" original-data > /var/lib/nixlab/preserved-data')
apps.execute('docker', 'volume', 'create', 'nixlab-persistence-test')
volume = apps.execute('docker', 'volume', 'inspect', '--format', '{{.Mountpoint}}', 'nixlab-persistence-test').stdout.strip()
assert volume.startswith('/var/lib/docker/volumes/') and volume.endswith('/_data')
apps.execute('sh', '-eu', '-c', 'printf "%s\n" docker-volume-data > "$1/record"', 'fixture', volume)


def preserved(expected):
    for name, identity in identities.items():
        transport = Transport('lab', name)
        current = transport.inspect()
        assert current['uuid'] == identity['uuid'] and current['fingerprint'] == identity['fingerprint']
        assert current['active'] == expected[name]
        assert transport.execute('cat', '/var/lib/nixlab/preserved-data').stdout.strip() == 'original-data'
    assert apps.execute('cat', volume + '/record').stdout.strip() == 'docker-volume-data'
    assert authenticated() == portainer_user


def deploy(job_id, targets):
    directory = state / 'jobs' / job_id
    directory.mkdir(parents=True)
    source = directory / 'source'; source.mkdir(); rt.write_json(source / '.nixlab-files.json', [])
    (directory / 'system').symlink_to('/run/current-system')
    rt.write_json(directory / 'job.json', {'id': job_id, 'status': 'confirmed', 'baseDigest': 'fixture', 'candidateDigest': rt.digest_source(source)})
    rt.write_json(state / 'active.json', {'id': job_id})
    d.prepare(directory, targets)
    manager.submit(job_id)
    return manager.run(job_id)


failed = copy.deepcopy(manifest)
failed['apps'].update(rt.read_json(Path('/etc/nixlab-test-failed-deployment.json')))
failed['apps']['healthTimeout'] = 1
report = deploy('c' * 32, failed)
assert report['status'] == 'partial', report
assert report['targets']['apps']['phase'] == 'rolled-back', report
assert report['targets']['worker']['phase'] == 'blocked', report
preserved({name: identity['active'] for name, identity in identities.items()})
manager.abandon('c' * 32)
print('KVM_STAGE: real apps OS health failure rolled back; dependent guest blocked; data and authentication preserved', flush=True)
report = deploy('d' * 32, manifest)
assert report['status'] == 'healthy', report
preserved({name: spec['system'] for name, spec in manifest.items()})
for name in identities:
    assert Transport('lab', name).execute('cat', '/etc/nixlab-deploy-version').stdout.strip() == 'v2'

previous_boot = apps.execute('cat', '/proc/sys/kernel/random/boot_id').stdout.strip()
apps.reboot()
deadline = time.monotonic() + 120
while True:
    try:
        assert apps.execute('cat', '/proc/sys/kernel/random/boot_id', timeout=10).stdout.strip() != previous_boot
        apps.execute('systemctl', 'is-active', 'docker-portainer.service', timeout=10)
        break
    except (DeploymentError, AssertionError):
        if time.monotonic() >= deadline:
            raise AssertionError('Apps VM did not recover after deliberate reboot') from None
        time.sleep(2)
preserved({name: spec['system'] for name, spec in manifest.items()})
print('Real apps VM update, failed-update recovery and reboot verified; UUIDs, disk topology, Docker volume records and Portainer identity preserved.')
