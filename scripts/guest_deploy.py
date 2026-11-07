"""Local Incus transport for existing NixOS guests; never creates or replaces disks."""
import hashlib
import json
import re
import subprocess
import time
from urllib.parse import quote

OWNER = 'user.nixlab.managed'
STORE = re.compile(r'/nix/store/[a-z0-9]{32}-[^/\s]+')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}')


class DeploymentError(ValueError):
    pass


def store_path(value):
    if not isinstance(value, str) or not STORE.fullmatch(value):
        raise DeploymentError('Expected an immutable local Nix store path')
    return value


class Transport:
    def __init__(self, project, name):
        if any(not isinstance(v, str) or not NAME.fullmatch(v) for v in (project, name)):
            raise DeploymentError('Expected local Incus project and guest names')
        self.project, self.name = project, name

    def exec_args(self, args, stdin=False):
        return ['incus', '--force-local', 'exec', self.name, '--project', self.project,
                '--mode=non-interactive', *([] if stdin else ['--disable-stdin']), '--', *args]

    def execute(self, *args, timeout=30, check=True):
        try:
            result = subprocess.run(self.exec_args(args), capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError):
            raise DeploymentError('Guest command did not finish within its transport deadline') from None
        if check and result.returncode:
            raise DeploymentError('Guest command failed; inspect the guest locally in Incus')
        return result

    def inspect(self):
        path = '/1.0/instances/' + quote(self.name, safe='') + '?project=' + quote(self.project, safe='')
        try:
            result = subprocess.run(['incus', '--force-local', 'query', path], check=True,
                                    capture_output=True, text=True, timeout=10)
            current = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise DeploymentError('Guest inventory unavailable; no guest is created by deployment') from None
        config = current.get('config', {})
        if config.get(OWNER) != 'true': raise DeploymentError('Native-owned guest is observed only; explicit source ownership is required')
        if config.get('user.nixlab.restore-review') == 'required': raise DeploymentError('Release the guest from restore quarantine before deployment')
        if current.get('status') != 'Running': raise DeploymentError('Start this source-owned guest in Incus before deployment')
        if not config.get('volatile.uuid'): raise DeploymentError('Guest has no stable instance identity')
        if self.execute('test', '-e', '/etc/NIXOS', timeout=10, check=False).returncode:
            raise DeploymentError('Non-NixOS guest is observed only; no OS deployment is attempted')
        profile = store_path(self.execute('readlink', '-f', '/nix/var/nix/profiles/system', timeout=10).stdout.strip())
        active = store_path(self.execute('readlink', '-f', '/run/current-system', timeout=10).stdout.strip())
        topology = {k: current.get(k) for k in ('type', 'profiles', 'devices', 'expanded_devices')}
        topology['config'] = {k: v for k, v in config.items() if not k.startswith('volatile.')}
        fingerprint = hashlib.sha256(json.dumps(topology, sort_keys=True).encode()).hexdigest()
        return {'uuid': config['volatile.uuid'], 'fingerprint': fingerprint, 'profile': profile,
                'active': active, 'type': current['type']}

    def transfer(self, *roots, timeout=600):
        roots = [store_path(path) for path in roots]
        try:
            paths = subprocess.check_output(['nix-store', '--query', '--requisites', *roots], text=True, timeout=60).splitlines()
        except (OSError, subprocess.SubprocessError):
            raise DeploymentError('Cannot enumerate the reviewed closure') from None
        if not paths or len(paths) > 20000: raise DeploymentError('Closure inventory is empty or exceeds the transport limit')
        for path in paths: store_path(path)
        processes = []
        deadline = time.monotonic() + timeout
        try:
            exporter = subprocess.Popen(['nix-store', '--export', *paths], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            processes.append(exporter)
            receiver = subprocess.Popen(self.exec_args(['nix-store', '--import'], stdin=True), stdin=exporter.stdout,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(receiver)
            exporter.stdout.close()
            received = receiver.wait(timeout=max(1, deadline - time.monotonic()))
            exported = exporter.wait(timeout=max(1, deadline - time.monotonic()))
            if received or exported: raise DeploymentError('Closure transfer failed; imported store paths can be reused on retry')
        except (OSError, subprocess.SubprocessError):
            raise DeploymentError('Closure transfer interrupted or timed out; retry is safe before activation') from None
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()

    def retain(self, deployment, previous, target):
        if not re.fullmatch(r'[a-z0-9-]{1,80}', deployment): raise DeploymentError('Invalid deployment identity')
        directory = '/nix/var/nix/gcroots/nixlab-deployments'
        self.execute('mkdir', '-p', '-m', '0700', directory)
        for suffix, path in (('previous', previous), ('target', target)):
            self.execute('nix-store', '--add-root', directory + '/' + deployment + '-' + suffix,
                         '--realise', store_path(path), timeout=60)

    def activate(self, closure):
        closure = store_path(closure)
        self.execute('test', '-x', closure + '/bin/switch-to-configuration', timeout=10)
        # This deadline and lock survive a dead controller/Incus client. Profile
        # publication and switching must serialize with a later recovery attempt.
        script = 'nix-env --profile /nix/var/nix/profiles/system --set "$1"\nexec "$1/bin/switch-to-configuration" switch'
        self.execute('timeout', '--kill-after=10s', '600', 'flock', '--exclusive', '--timeout', '300',
                     '/run/lock/nixlab-guest-deployment.lock', 'sh', '-eu', '-c', script,
                     'nixlab-guest-switch', closure, timeout=630)

    def reboot_required(self, current):
        if current['type'] != 'virtual-machine': return False
        booted = self.execute('readlink', '-f', '/run/booted-system/kernel', timeout=10).stdout.strip()
        target = self.execute('readlink', '-f', current['profile'] + '/kernel', timeout=10).stdout.strip()
        if not booted or not target: raise DeploymentError('Cannot determine the guest kernel generation')
        return booted != target

    def reboot(self):
        try:
            subprocess.run(['incus', '--force-local', 'restart', self.name, '--project', self.project, '--timeout=60'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
        except (OSError, subprocess.SubprocessError):
            raise DeploymentError('Guest reboot did not complete; no forced power-off was attempted') from None

    def healthy(self, probe, timeout):
        import math
        seconds = max(1, math.ceil(timeout))
        return self.execute('timeout', '--kill-after=2s', str(seconds), store_path(probe) + '/bin/nixlab-guest-health',
                            timeout=seconds + 3, check=False).returncode == 0
