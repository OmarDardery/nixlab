"""Disposable real apps/Portainer test; never prints passwords, JWTs or bodies."""
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
                assert response.status == 200 and json.load(response).get('jwt')
            return
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


authenticated()
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

# The hosted test contains two nested hypervisors and a software-backed Incus
# guest disk. Its closure import is significantly slower than on a real host.
# Keep the production 600 s bound; extend only the disposable test fixture.
original_transfer = Transport.transfer

def hosted_transfer(self, *roots, **kwargs):
    print('KVM_STAGE: transfer starting for ' + self.name, flush=True)
    kwargs.setdefault('timeout', 1200)
    result = original_transfer(self, *roots, **kwargs)
    print('KVM_STAGE: transfer complete for ' + self.name, flush=True)
    return result

Transport.transfer = hosted_transfer
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
print('KVM_STAGE: guest inventory and Portainer authentication ready', flush=True)
manager.submit(job_id)
print('KVM_STAGE: deployment submitted; beginning live closure transfer and activation', flush=True)
report = manager.run(job_id)
print('KVM_STAGE: deployment completed with status ' + report['status'], flush=True)
assert report['status'] == 'healthy', report
for name in ('apps', 'worker'):
    transport = Transport('lab', name); current = transport.inspect()
    assert current['uuid'] == identities[name] and current['active'] == manifest[name]['system']
    transport.execute('test', '-e', '/var/lib/nixlab/preserved-data')
    assert transport.execute('cat', '/etc/nixlab-deploy-version').stdout.strip() == 'v2'
authenticated()
print('Real apps VM and second NixOS guest updated in place; Portainer authentication and persistent data survived.')
