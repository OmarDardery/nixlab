"""Recover an existing management connection, then use the normal update engine.

The temporary identity selection only unlocks the local Incus inventory. It
changes no firewall rules, authentication, guest declarations or storage.
"""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'admin'))
import diagnostics
import runtime as rt
import workflow

MODULE = 'nixlab-network-autoconnect.nix'
DATABASE = Path('/var/lib/incus/database/local.db')
DROPIN = Path('/run/systemd/system/nixlab-management-identity.service.d/90-nixlab-network-repair.conf')
FIELDS = 'connection.uuid,connection.id,connection.type,connection.interface-name,connection.controller,connection.autoconnect'
PROJECTION = '''c: builtins.mapAttrs (name: p: {
  id = p.connection.id or name;
  interface = p.connection."interface-name" or "";
  auto = p.connection.autoconnect or true;
}) c.networking.networkmanager.ensureProfiles.profiles'''
ACTIVE = {'building', 'ready', 'applying', 'awaiting-confirmation', 'rolling-back'}


def run(args, timeout=30, **kwargs):
    return subprocess.run(args, check=kwargs.pop('check', True), text=True, timeout=timeout,
                          env=dict(os.environ, LC_ALL='C'), **kwargs)


def profiles():
    deadline = time.monotonic() + 30
    identities = run(['nmcli', '--get-values', 'UUID', 'connection', 'show'],
                     timeout=10, capture_output=True).stdout.splitlines()
    if len(identities) > 128:
        raise ValueError('Management recovery profile limit exceeded; review networking manually')
    result = []
    for identity in identities:
        if str(uuid.UUID(identity)) != identity.lower():
            raise ValueError('Invalid NetworkManager profile identity')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('Management profile observation exceeded its deadline')
        values = run(['nmcli', '--escape', 'no', '--get-values', FIELDS,
                      'connection', 'show', 'uuid', identity], timeout=min(5, remaining), capture_output=True).stdout.splitlines()
        if (len(values) != 6 or values[0] != identity or values[-1] not in ('yes', 'no')
                or any(value and not value.isprintable() for value in values)):
            raise ValueError('Ambiguous NetworkManager profile metadata; no profile was changed')
        result.append(dict(zip(('uuid', 'id', 'type', 'interface', 'controller', 'auto'), values)))
    return result


def select(settings, links, connections, requested=None):
    """Follow one existing bridge; never create profiles or guess another NIC."""
    origin = requested or settings['management']['interface']
    name = origin
    by_name = {link['ifname']: link for link in links}
    name = by_name.get(name, {}).get('master') or name
    bound = [p for p in connections if p['interface'] == name]
    if len(bound) == 1 and bound[0]['controller']:
        parent = bound[0]['controller']
        bridges = [p for p in connections if p['type'] == 'bridge'
                   and parent in (p['uuid'], p['id'], p['interface'])]
        if len(bridges) != 1:
            raise ValueError('Saved management port has an ambiguous bridge controller')
        name = bridges[0]['interface']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}', name):
        raise ValueError('Invalid management interface')
    if name in {settings['guests']['bridge'], *settings['connectivity']['guestInterfaces']}:
        raise ValueError('A guest network cannot become the management connection')
    controllers = [p for p in connections if p['interface'] == name and p['type'] in ('bridge', '802-3-ethernet')]
    if len(controllers) != 1:
        raise ValueError('Repair requires one existing, interface-bound Ethernet/bridge profile; use --interface if needed')
    controller = controllers[0]
    selected = [controller]
    if controller['type'] == 'bridge':
        if sum(p['id'] == controller['id'] for p in connections) != 1:
            raise ValueError('Duplicate bridge profile names require manual review')
        ports = [p for p in connections if p['controller'] in (name, controller['uuid'], controller['id'])]
        if not ports or any(p['type'] != '802-3-ethernet' or p['interface'] not in by_name for p in ports):
            raise ValueError('Management bridge needs existing Ethernet port profiles and connected hardware')
        for port in ports:
            targets = [p for p in connections if p['type'] == 'bridge'
                       and port['controller'] in (p['uuid'], p['id'], p['interface'])]
            if len(targets) != 1 or targets[0]['uuid'] != controller['uuid']:
                raise ValueError('Saved bridge port controller is ambiguous')
        selected += ports
        if origin != name and not any(p['interface'] == origin for p in ports):
            raise ValueError('Live bridge membership differs from saved management profiles')
    if settings['management']['configure']:
        raise ValueError('This recovery preserves existing networking; managed static networking needs a reviewed configuration change')
    return name, selected


def declared_profiles(root):
    return json.loads(run(['nix', '--extra-experimental-features', 'nix-command flakes',
                          'eval', '--json', 'path:' + str(root) + '#nixosConfigurations.hypervisor.config',
                          '--apply', PROJECTION], timeout=120, capture_output=True).stdout)


def modules_for(root, selected):
    declared = declared_profiles(root)
    names = [name for name, value in declared.items() if any(
        value['id'] == p['id'] and value['interface'] == p['interface'] for p in selected)
        and value['auto'] not in (True, 'true', 'yes', 1)]
    if not names:
        return {}, []
    if any(not re.fullmatch(r'[A-Za-z0-9_.-]+', name) for name in names):
        raise ValueError('Unusual declarative profile names require manual review')
    target = rt.machine_path(root, 'modules') / MODULE
    if target.exists():
        raise ValueError('The autoconnect recovery module already exists; review its overrides before retrying')
    content = ('# NixLab recovery: autoconnect existing management profiles only.\n'
               '{ lib, ... }: {\n  networking.networkmanager.ensureProfiles.profiles = lib.genAttrs\n'
               '    (builtins.fromJSON ' + json.dumps(json.dumps(sorted(names))) + ')\n'
               '    (_: { connection.autoconnect = lib.mkOverride 90 true; });\n}\n')
    return {MODULE: content}, names


def check_profiles(selected, observed):
    current = {p['uuid']: p for p in observed}
    for profile in selected:
        if any(current.get(profile['uuid'], {}).get(key) != profile[key]
               for key in ('id', 'type', 'interface', 'controller')):
            raise ValueError('A saved management profile changed identity; refusing repair')


def dropin(settings_path):
    # systemd command quoting: only immutable source/Python and a fixed state path.
    tools = {name: shutil.which(name) for name in ('env', 'ip', 'openssl')}
    if not all(tools.values()):
        raise ValueError('Recovery runtime needs env, ip and openssl')
    # The native unit has a narrower PATH than the nix-run wrapper. Carry only
    # its required, resolved tool directories into this one command.
    tool_path = ':'.join(str(Path(tools[name]).resolve().parent) for name in ('ip', 'openssl'))
    arguments = [tools['env'], 'PATH=' + tool_path, sys.executable,
                 str(Path(__file__).resolve()), '--identity', str(settings_path)]
    if any('%' in value or '\n' in value for value in arguments):
        raise ValueError('Unsupported recovery runtime path')
    return '[Service]\nExecStart=\nExecStart=' + ' '.join(json.dumps(value) for value in arguments) + '\n'


def activate(selected):
    # Starting an already active bridge reactivates it, detaches its ports and
    # clears its address. Leave active connections alone. Start an inactive
    # controller asynchronously before its ports, so it can wait for carrier.
    for profile in selected:
        active = run(['nmcli', '--get-values', 'UUID', 'connection', 'show', '--active'],
                     timeout=5, capture_output=True).stdout.splitlines()
        if profile['uuid'] not in active:
            wait = '0' if profile['type'] == 'bridge' else '30'
            run(['nmcli', '--wait', wait, 'connection', 'up', 'uuid', profile['uuid']], timeout=40)
    # NM can report a bridge activation request accepted while still waiting
    # for ports. Allow bounded STP/carrier + DHCP convergence, without another
    # activation or weakening the subsequent address/trust checks.
    deadline = time.monotonic() + 60
    while True:
        state = run(['nmcli', '--get-values', 'GENERAL.STATE', 'device', 'show', selected[0]['interface']],
                    timeout=5, capture_output=True).stdout.strip()
        number = state.split(maxsplit=1)[0] if state else ''
        if number == '100': return
        if not number.isdigit() or number == '120' or time.monotonic() >= deadline:
            raise ValueError('Management connection did not become ready: ' + state)
        time.sleep(0.5)


def start_incus():
    # The native startup oneshot also owns graceful shutdown. Merely starting
    # the daemon can leave this previously failed hook inactive across repair.
    observed = run(['systemctl', 'show', 'incus-startup.service', '--property=LoadState', '--value'],
                   capture_output=True, check=False).stdout.strip()
    if observed not in ('loaded', 'not-found'):
        raise ValueError('Incus shutdown hook needs review: ' + observed)
    units = ['incus.service'] + (['incus-startup.service'] if observed == 'loaded' else [])
    # start is idempotent; restarting this oneshot would shut down guests.
    run(['systemctl', 'start', *units], timeout=150)


class Repair:
    def __init__(self, manager):
        self.manager = manager
        self.path = manager.state / 'network-repair.json'
        self.record = None

    def save(self, phase):
        self.record['phase'] = phase
        rt.write_json(self.path, self.record)

    def prepare(self, ask, choose_management, interface=None):
        if workflow.remote_session():
            raise ValueError('--repair-network requires the local host console')
        with rt.locked(blocking=False):
            state = self.manager.status()
            record = rt.read_json(self.path, {})
            if not isinstance(record, dict):
                raise ValueError('Invalid network recovery checkpoint')
            if record and (record.get('format') != 1 or record.get('phase') not in {
                    'prepared', 'reviewing', 'activating', 'needs-attention', 'completed', 'cancelled'}):
                raise ValueError('Unknown network recovery checkpoint; inspect it before retrying')
            job = state.get('job') or {}
            if state.get('pending') or job.get('status') in ACTIVE:
                # Recover the small disconnect window between review creation
                # and recording its ID, without adopting another proposal.
                matching = (record.get('management') and job.get('principal') == 'root-update-command'
                            and job.get('settings', {}).get('management') == record['management']
                            and job.get('moduleChanges') == [
                                {'path': name, 'before': None, 'after': content}
                                for name, content in record.get('modules', {}).items()])
                if record and not record.get('job') and matching:
                    record['job'] = job['id']
                    rt.write_json(self.path, record)
                if not record or job.get('id') != record.get('job') or job.get('principal') != 'root-update-command':
                    raise ValueError('Finish the pending activation before repairing management networking')
            if not state.get('setupComplete') or not DATABASE.is_file() or DATABASE.stat().st_size == 0:
                raise ValueError('Recovery requires an installed host and its existing Incus database; use #install for installation')
            if record.get('phase') in ('prepared', 'reviewing', 'activating', 'needs-attention'):
                if interface and interface != record['interface']:
                    raise ValueError('Resume the repair with its original interface')
                self.record = record
                print('Resuming management repair on ' + record['interface'], flush=True)
            else:
                settings = self.manager.effective_settings()
                links = json.loads(run(['ip', '-j', '-d', 'address', 'show'], capture_output=True).stdout)
                name, selected = select(settings, links, profiles(), interface)
                print('Repair existing management connection on ' + name + '; enable autoconnect on:', flush=True)
                for p in selected: print('  ' + p['id'] + ' [' + p['uuid'] + ']', flush=True)
                print('No address assignment, new profile or firewall change. Incus may autostart its existing guests.', flush=True)
                if not ask('Enable these saved connections and review the normal guarded update?'):
                    raise ValueError('Repair cancelled before changes')
                self.record = {'format': 1, 'interface': name, 'profiles': selected,
                               'modules': None, 'declared': [], 'job': None,
                               'sourceDigest': rt.digest_source(self.manager.root)}
                rt.pin_worker_runtime(self.manager.state)
                self.save('prepared')
            if self.record.get('format') != 1:
                raise ValueError('Unsupported network repair checkpoint')
            if not self.record.get('job') and rt.digest_source(self.manager.root) != self.record['sourceDigest']:
                raise ValueError('Source changed since the recovery checkpoint; review it before retrying')
            check_profiles(self.record['profiles'], profiles())
            for p in self.record['profiles']:
                run(['nmcli', 'connection', 'modify', 'uuid', p['uuid'], 'connection.autoconnect', 'yes'])
            activate(self.record['profiles'])
            changes = choose_management(self.manager.effective_settings(), self.record['interface'])
            settings = copy.deepcopy(self.manager.effective_settings())
            settings['management'].update(changes.get('management', {}))
            self.record['management'] = settings['management']
            # Restoring connectivity must precede Nix evaluation: old flake
            # inputs may have been garbage-collected on an offline host.
            if self.record['modules'] is None:
                self.record['modules'], self.record['declared'] = modules_for(
                    self.manager.root, self.record['profiles'])
                self.save('prepared')
            rt.write_json(self.manager.state / 'network-repair-settings.json', settings)
            # Genuine identity validation/certificate generation for the observed
            # address unlocks Incus's local socket. Source and firewall stay intact.
            if not DROPIN.exists() or DROPIN.read_text() == dropin(self.manager.state / 'network-repair-settings.json'):
                rt.atomic(DROPIN, dropin(self.manager.state / 'network-repair-settings.json'), mode=0o644)
            else:
                raise ValueError('Another runtime identity override needs manual review')
            run(['systemctl', 'daemon-reload'])
            run(['systemctl', 'restart', 'nixlab-management-identity.service'], timeout=150)
            start_incus()
            self.save('reviewing')
            return self.record['interface']

    def reviewed(self, job):
        self.record['job'] = job['id']
        self.save('reviewing')

    def verify(self, job):
        if job.get('moduleChanges') != [{'path': name, 'before': None, 'after': content}
                                        for name, content in self.record['modules'].items()]:
            raise ValueError('Reviewed recovery modules differ from the checkpoint')
        values = declared_profiles(self.manager.job_path(job['id']) / 'source')
        if any(values.get(name, {}).get('auto') not in (True, 'true', 'yes', 1) for name in self.record['declared']):
            raise ValueError('A source override prevents persistent autoconnect; refusing activation')
        self.save('activating')

    def validate_live(self):
        observed = profiles()
        check_profiles(self.record['profiles'], observed)
        current = {p['uuid']: p for p in observed}
        if any(current[p['uuid']]['auto'] != 'yes' for p in self.record['profiles']):
            raise ValueError('A management profile has autoconnect disabled after activation')

    def native_identity(self):
        # Restarting a required oneshot can stop its dependents. Restore only
        # units that were active, so an optional disabled service stays disabled.
        names = ['incus.service', 'incus-startup.service', 'incus-preseed.service', 'nixlab-instances.service',
                 'nixlab-admin-backend.service', 'nixlab-ui.service', 'nginx.service',
                 'cockpit.service', 'nixlab-apps-onboarding.service']
        # Optional units may be absent. Require an Incus observation rather than
        # treating a failed/empty query as an empty set of dependent services.
        observed = run(['systemctl', 'show', '--property=Id,ActiveState', *names],
                       capture_output=True, check=False).stdout
        if 'Id=incus.service\n' not in observed and not observed.endswith('Id=incus.service'):
            raise ValueError('Cannot observe Incus service dependencies before restoring identity')
        active = []
        for block in observed.strip().split('\n\n'):
            fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            if fields.get('Id') in names and fields.get('ActiveState') == 'active':
                active.append(fields['Id'])
        if DROPIN.exists():
            if DROPIN.read_text() != dropin(self.manager.state / 'network-repair-settings.json'):
                raise ValueError('Runtime identity override changed; inspect it before continuing')
            rt.durable_unlink(DROPIN)
            run(['systemctl', 'daemon-reload'])
        run(['systemctl', 'restart', 'nixlab-management-identity.service'], timeout=150)
        if active: run(['systemctl', 'start', *active], timeout=180)
        start_incus()
        self.validate_live()

    def finish(self, success):
        # Retain checkpoints while a background activation/rollback is running.
        state = self.manager.status()
        if state.get('pending') or (state.get('job') or {}).get('status') in ACTIVE:
            return
        if not self.record: return
        errors = []
        if DROPIN.exists():
            try: self.native_identity()
            except (OSError, ValueError, subprocess.SubprocessError) as error: errors.append(str(error))
        if success:
            try: self.validate_live()
            except (OSError, ValueError, subprocess.SubprocessError) as error: errors.append(str(error))
        else:
            try:
                check_profiles(self.record['profiles'], profiles())
            except (OSError, ValueError, subprocess.SubprocessError) as error: errors.append(str(error))
            else:
                for p in self.record['profiles']:
                    try: run(['nmcli', 'connection', 'modify', 'uuid', p['uuid'], 'connection.autoconnect', p['auto']])
                    except (OSError, ValueError, subprocess.SubprocessError) as error: errors.append(str(error))
        self.save('completed' if success and not errors else 'needs-attention' if errors else 'cancelled')
        if errors:
            message = 'Recovery needs attention: ' + '; '.join(errors) + '; checkpoint: ' + str(self.path)
            if success: raise ValueError(message)
            print(message, file=sys.stderr)


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--identity' or os.geteuid() != 0:
        sys.exit('This helper is invoked by #update --repair-network')
    diagnostics.identity(rt.read_json(Path(sys.argv[2])))
