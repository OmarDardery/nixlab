"""Restricted root broker. Only typed settings, fixed transactions and enrollment."""
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from model import command, differences, inventory, merge, validate_settings
import runtime as rt


class Manager:
    def __init__(self, root=None, state=None):
        self.root = Path(root or rt.ROOT)
        self.state = Path(state or rt.STATE)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'jobs').mkdir(mode=0o700, exist_ok=True)

    def settings(self):
        return rt.read_json(self.root / 'local/settings.json')

    def job_path(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
            raise ValueError('Invalid transaction ID')
        path = self.state / 'jobs' / job_id
        if not path.is_dir():
            raise ValueError('Unknown transaction')
        return path

    def job(self, job_id):
        job = rt.read_json(self.job_path(job_id) / 'job.json')
        log = self.job_path(job_id) / 'build.log'
        job['log'] = log.read_text(errors='replace')[-65536:] if log.exists() else ''
        # Credentials never enter this metadata or its log.
        return job

    def status(self):
        active = rt.read_json(self.state / 'active.json', {})
        job = self.job(active['id']) if active.get('id') else None
        if job and job['status'] == 'building' and time.time() - job['created'] > 30:
            active_unit = subprocess.run(['systemctl', 'is-active', '--quiet', 'nixlab-build-' + job['id']], capture_output=True)
            if active_unit.returncode:
                job.pop('log', None); job.update(status='failed', error='Build worker stopped; inspect its log and start a new review')
                rt.write_json(self.job_path(job['id']) / 'job.json', job)
                job = self.job(job['id'])
        current = str(Path('/run/current-system').resolve())
        last = rt.read_json(self.state / 'last-confirmed.json')
        if last and rt.read_json(self.job_path(last['id']) / 'job.json')['status'] != 'confirmed':
            last = None
        return {'settings': self.settings(), 'job': job, 'lastConfirmed': last, 'pending': rt.read_json(self.state / 'pending.json'),
                'currentSystem': current, 'setupComplete': self.settings()['ready']}

    def host_inventory(self):
        data = inventory()
        try:
            data['evaluated'] = json.loads(command(['nix', '--extra-experimental-features', 'nix-command flakes',
                                                  'eval', '--json', 'path:' + str(self.root) + '#nixlabOverview'], timeout=120))
        except (ValueError, subprocess.TimeoutExpired) as exc:
            data['evaluationError'] = str(exc)
        import ipaddress
        networks = [ipaddress.ip_network(r['dst'], strict=False) for r in data['routes'] if r.get('dst', 'default') != 'default']
        suggestion = next((ipaddress.ip_network(c) for c in ('10.77.0.0/24', '10.78.0.0/24', '172.30.77.0/24', '192.168.240.0/24')
                           if not any(ipaddress.ip_network(c).overlaps(n) for n in networks)), None)
        if suggestion:
            prefix = str(suggestion.network_address).rsplit('.', 1)[0]
            data['suggestedGuests'] = {'cidr': str(suggestion), 'gatewayCIDR': prefix + '.1/24',
                                       'dhcpRange': prefix + '.100-' + prefix + '.199', 'appsAddress': prefix + '.10'}
        data['secretPresent'] = {key: path.exists() for key, path in rt.SECRET_PATHS.items()}
        return data

    def spawn(self, phase, job_id):
        args = ['systemd-run', '--quiet', '--collect', '--no-block', '--unit=nixlab-' + phase + '-' + job_id,
                '--property=RuntimeMaxSec=10800', '--setenv=NIXLAB_ROOT=' + str(self.root),
                '--setenv=NIXLAB_STATE=' + str(self.state), '--setenv=PATH=' + os.environ['PATH'],
                sys.executable, str(rt.SOURCE / 'main.py'), 'job', phase, job_id]
        subprocess.run(args, check=True, capture_output=True, text=True)

    def review(self, changes, secret_values=None, update_inputs=False):
        with rt.locked():
            current = self.settings()
            active = rt.read_json(self.state / 'active.json', {})
            if active.get('id'):
                previous = self.job(active['id'])
                if previous['status'] in ('building', 'applying', 'awaiting-confirmation', 'rolling-back'):
                    raise ValueError('Finish the active transaction first')
            if not isinstance(changes, dict):
                raise ValueError('Settings must be an object')
            candidate = validate_settings(merge(current, changes), current, inventory())
            for section, key in [('backup', 'passwordFile'), ('backup', 'environmentFile'), ('tunnel', 'credentialsFile')]:
                if candidate[section][key] != current[section][key]:
                    raise ValueError('Secret file paths are source-managed')
            with tempfile.NamedTemporaryFile(mode='w', dir=self.state) as public_key_file:
                for key in candidate['adminKeys']:
                    public_key_file.seek(0); public_key_file.truncate(); public_key_file.write(key + '\n'); public_key_file.flush()
                    command(['ssh-keygen', '-l', '-f', public_key_file.name])
            install = rt.read_json(self.root / 'local/installation.json', {})
            wifi = candidate['radios']['wifi']
            if install.get('legacyWpaProfiles') and wifi['enable'] and not wifi['manageProfile']:
                raise ValueError('This host used Nix wpa_supplicant profiles. Declare its SSID/password when moving Wi-Fi to NetworkManager/iwd')
            encoded = rt.encode_secrets(secret_values or {}, candidate)
            if candidate['radios']['wifi']['manageProfile'] and candidate['radios']['wifi']['enable']:
                if 'wifiPassword' not in encoded and not rt.SECRET_PATHS['wifiPassword'].exists():
                    raise ValueError('Provide the WPA password for the managed Wi-Fi profile')
            if candidate['apps']['enable'] and not candidate['secrets']['enable'] and 'portainerPassword' not in encoded and not rt.SECRET_PATHS['portainerPassword'].exists():
                raise ValueError('Set the initial Portainer administrator password')
            if candidate['apps']['enableDemo'] and 'demoPassword' not in encoded and not rt.SECRET_PATHS['demoPassword'].exists():
                raise ValueError('Set the demo database password')
            if candidate['backup']['enable'] and not candidate['secrets']['enable']:
                if 'resticPassword' not in encoded and not rt.SECRET_PATHS['resticPassword'].exists():
                    raise ValueError('Provide the restic repository password')
                # Native service needs an EnvironmentFile even with no cloud credentials.
                if 'resticEnvironment' not in encoded and not rt.SECRET_PATHS['resticEnvironment'].exists():
                    encoded['resticEnvironment'] = ''
            if candidate['tunnel']['enable'] and not candidate['secrets']['enable'] and 'tunnelCredentials' not in encoded and not rt.SECRET_PATHS['tunnelCredentials'].exists():
                raise ValueError('Provide the tunnel-specific credentials JSON')
            initialized = Path('/var/lib/incus/database').exists()
            runtime_settings = previous['settings'] if initialized and not current['ready'] and active.get('id') and previous.get('previousSystem') else current
            if initialized and candidate['storage'] != runtime_settings['storage']:
                raise ValueError('Incus storage is initialized; migrate pools before changing their source/driver')
            if initialized and (candidate['guests'] != runtime_settings['guests']):
                raise ValueError('Existing guest address space requires an explicit migration, not a live setup edit')
            if initialized:
                old = {m['name']: m for m in runtime_settings['mounts'] if m['role'] in ('guests', 'pool')}
                new = {m['name']: m for m in candidate['mounts'] if m['role'] in ('guests', 'pool')}
                if any(new.get(k) != v for k, v in old.items()):
                    raise ValueError('Do not remove/remap an initialized Incus storage mount; migrate its pool first')
            # Detect overlaps with real host routes, ignoring the bridge we own and the default route.
            guest_net = __import__('ipaddress').ip_network(candidate['guests']['cidr'])
            for route in inventory()['routes']:
                if route.get('dst', 'default') == 'default' or route.get('dev') == 'incusbr0':
                    continue
                if guest_net.overlaps(__import__('ipaddress').ip_network(route['dst'], strict=False)):
                    raise ValueError('Guest address space overlaps an existing host/LAN/VPN route')
            job_id = secrets.token_hex(16)
            directory = self.state / 'jobs' / job_id
            rt.snapshot(directory / 'source', self.root)
            rt.write_json(directory / 'source/local/settings.json', candidate)
            rt.write_json(directory / 'secrets.json', encoded)
            job = {'id': job_id, 'status': 'building', 'created': time.time(), 'baseDigest': rt.digest_source(self.root),
                   'settings': candidate, 'changes': differences(current, candidate),
                   'secretChanges': sorted(encoded), 'updateInputs': bool(update_inputs)}
            rt.write_json(directory / 'job.json', job)
            rt.write_json(self.state / 'active.json', {'id': job_id})
            if active.get('id') and previous['status'] in ('ready', 'failed', 'cancelled'):
                obsolete = self.job_path(active['id'])
                record = rt.read_json(obsolete / 'job.json'); record['status'] = 'superseded'
                rt.write_json(obsolete / 'job.json', record)
                rt.durable_unlink(obsolete / 'secrets.json')
                rt.durable_unlink(obsolete / 'system')
            rt.audit('review', job=job_id, changes=len(job['changes']), secrets=list(encoded))
            try:
                self.spawn('build', job_id)
            except Exception:
                job['status'] = 'failed'; job['error'] = 'Could not start the build service'
                rt.write_json(directory / 'job.json', job)
                raise
            return {'id': job_id}

    def apply(self, job_id):
        with rt.locked():
            directory = self.job_path(job_id)
            job = rt.read_json(directory / 'job.json')
            if rt.read_json(self.state / 'active.json', {}).get('id') != job_id:
                raise ValueError('This candidate was superseded; review the active transaction')
            if job['status'] != 'ready':
                raise ValueError('Build and review this transaction before activation')
            if job['baseDigest'] != rt.digest_source(self.root):
                raise ValueError('Source/settings changed since review; build a new transaction')
            if job.get('candidateDigest') != rt.digest_source(directory / 'source'):
                raise ValueError('Reviewed snapshot changed; refusing activation')
            job['status'] = 'applying'; job['appliedAt'] = time.time()
            rt.write_json(directory / 'job.json', job)
            try:
                self.spawn('apply', job_id)
            except Exception:
                job.update(status='failed', error='Could not start the activation worker')
                rt.write_json(directory / 'job.json', job)
                raise
            rt.audit('apply-requested', job=job_id)
            return {'id': job_id}

    def confirm(self, job_id):
        with rt.locked():
            pending = rt.read_json(self.state / 'pending.json')
            job = rt.read_json(self.job_path(job_id) / 'job.json')
            if not pending or pending['id'] != job_id or job['status'] != 'awaiting-confirmation':
                raise ValueError('No completed activation is awaiting confirmation')
            if time.time() >= pending['deadline']:
                raise ValueError('Confirmation window expired; recovery is required')
            if str(Path('/run/current-system').resolve()) != str((self.job_path(job_id) / 'system').resolve()):
                raise ValueError('The running generation differs from the reviewed build')
            job['status'] = 'confirmed'; job['confirmedAt'] = time.time()
            rt.write_json(self.job_path(job_id) / 'job.json', job)
            last = rt.read_json(self.state / 'last-confirmed.json')
            if last and last['id'] != job_id:
                old = self.job_path(last['id'])
                rt.durable_unlink(old / 'system'); rt.durable_unlink(old / 'previous-system')
                if (old / 'recovery').exists():
                    shutil.rmtree(old / 'recovery')
            rt.write_json(self.state / 'last-confirmed.json', {'id': job_id})
            rt.durable_unlink(self.state / 'pending.json')
            # Staged plaintext is no longer needed; live root-only runtime files remain.
            (self.job_path(job_id) / 'secrets.json').unlink(missing_ok=True)
            rt.audit('confirmed', job=job_id)
            return {'ok': True}

    def rollback(self, job_id):
        with rt.locked():
            job = self.job(job_id)
            if job['status'] not in ('awaiting-confirmation', 'confirmed', 'failed') or not job.get('previousSystem'):
                raise ValueError('This transaction has no recoverable previous generation')
            active = rt.read_json(self.state / 'active.json', {})
            if active.get('id') != job_id:
                if rt.read_json(self.state / 'last-confirmed.json', {}).get('id') != job_id or rt.read_json(self.state / 'pending.json'):
                    raise ValueError('Only the current or last confirmed transaction can be rolled back')
                other = self.job(active['id']) if active.get('id') else None
                if other and other['status'] in ('applying', 'awaiting-confirmation', 'rolling-back'):
                    raise ValueError('Finish the active activation first')
                if other and other['status'] == 'building':
                    subprocess.run(['systemctl', 'stop', 'nixlab-build-' + other['id']], check=True, timeout=30)
                if other:
                    record = rt.read_json(self.job_path(other['id']) / 'job.json'); record['status'] = 'superseded'
                    rt.write_json(self.job_path(other['id']) / 'job.json', record)
                    rt.durable_unlink(self.job_path(other['id']) / 'secrets.json')
                    rt.durable_unlink(self.job_path(other['id']) / 'system')
                rt.write_json(self.state / 'active.json', {'id': job_id})
            self.spawn('rollback', job_id)
            rt.audit('rollback-requested', job=job_id)
            return {'ok': True}

    def cancel(self, job_id):
        with rt.locked():
            directory = self.job_path(job_id)
            job = rt.read_json(directory / 'job.json')
            if job['status'] not in ('building', 'ready', 'failed'):
                raise ValueError('Only an unapplied candidate can be discarded')
            if job['status'] == 'building':
                subprocess.run(['systemctl', 'stop', 'nixlab-build-' + job_id], check=True, timeout=30)
            job['status'] = 'cancelled'
            rt.write_json(directory / 'job.json', job)
            rt.durable_unlink(directory / 'secrets.json')
            rt.durable_unlink(directory / 'system')
            rt.audit('candidate-discarded', job=job_id)
            return {'ok': True}

    def change_password(self, password):
        rt.write_json(self.state / 'auth.json', rt.password_record(password), mode=0o640, group='nixlab-ui')
        rt.audit('password-changed')
        return {'ok': True}

    def browser_certificate(self):
        # A separate revocable client identity. Private material exists only in a
        # root-only temporary directory and the authenticated one-time response.
        name = 'nixlab-browser-' + secrets.token_hex(6)
        password = secrets.token_urlsafe(24)
        with tempfile.TemporaryDirectory(dir=self.state) as folder:
            p = Path(folder)
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '365',
                            '-subj', '/CN=' + name, '-keyout', str(p / 'key'), '-out', str(p / 'cert')],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            command(['incus', 'config', 'trust', 'add-certificate', str(p / 'cert'), '--name', name])
            subprocess.run(['openssl', 'pkcs12', '-export', '-in', str(p / 'cert'), '-inkey', str(p / 'key'),
                            '-out', str(p / 'client.p12'), '-passout', 'stdin'], input=password + '\n', text=True,
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            data = base64.b64encode((p / 'client.p12').read_bytes()).decode()
        rt.audit('browser-certificate-issued', name=name)
        return {'name': name, 'password': password, 'pkcs12': data}
