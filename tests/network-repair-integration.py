"""Existing-profile recovery against native NM/systemd/Incus in the foundation VM.

This exercises the offline bootstrap and its removal, not a physical-host or
full platform-update qualification. Normal updater orchestration has separate
regression tests; no Incus inventory responses are mocked here.
"""
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import patch

import impact
import network_repair as repair
import runtime as rt
from update import management_change


def command(args):
    return subprocess.check_output(args, text=True).strip()


base = json.loads(Path('/etc/nixlab-repair-base.json').read_text())
original = command(['incus', 'config', 'get', 'native', 'volatile.uuid', '--project', 'lab'])
root = Path('/var/lib/nixlab-repair-source')
root.mkdir(mode=0o700, exist_ok=True)
rt.write_json(root / 'machine/format.json', {'version': 1})
rt.write_json(root / 'machine/settings.json', base)
rt.write_json(root / '.nixlab-files.json', ['machine/format.json', 'machine/settings.json'])

# Disposable VM only: seed existing saved profiles with the reported failure.
command(['nmcli', 'connection', 'add', 'type', 'bridge', 'ifname', 'br-repair',
         'con-name', 'repair-bridge', 'connection.autoconnect', 'no',
         'ipv4.method', 'manual', 'ipv4.addresses', '198.51.100.10/24', 'ipv6.method', 'disabled'])
controller = command(['nmcli', '--get-values', 'connection.uuid', 'connection', 'show', 'repair-bridge'])
command(['nmcli', 'connection', 'add', 'type', 'ethernet', 'ifname', 'repair-port',
         'con-name', 'repair-port', 'controller', controller, 'slave-type', 'bridge', 'connection.autoconnect', 'no'])
command(['nmcli', '--wait', '30', 'connection', 'up', 'repair-port'])

broken = copy.deepcopy(base)
broken['management'].update(interface='repair-port', address='198.51.100.10',
                            configure=False, trustedCIDRs=['198.51.100.0/24'])
rt.write_json(Path('/var/lib/nixlab-test-identity.json'), broken)
command(['systemctl', 'stop', 'incus.service'])
# Fixture only: fail the native shutdown hook while the daemon is stopped, so
# its ExecStop is a no-op and existing containers remain running.
command(['systemctl', 'stop', 'incus-startup.service'])
failed_hook = Path('/run/systemd/system/incus-startup.service.d/90-fixture-failure.conf')
rt.atomic(failed_hook, '[Service]\nExecStart=\nExecStart=' + shutil.which('false') + '\n')
command(['systemctl', 'daemon-reload'])
assert subprocess.run(['systemctl', 'start', 'incus-startup.service']).returncode != 0
failed_hook.unlink()
command(['systemctl', 'daemon-reload'])
assert subprocess.run(['systemctl', 'restart', 'nixlab-management-identity.service']).returncode != 0
assert subprocess.run(['systemctl', 'start', 'incus.service']).returncode != 0
assert subprocess.run(['systemctl', 'is-active', '--quiet', 'incus.service']).returncode != 0


class Manager:
    state = rt.STATE
    def __init__(self): self.root = root
    def status(self): return {'setupComplete': True}
    def effective_settings(self):
        return json.loads(Path('/var/lib/nixlab-test-identity.json').read_text())


manager = Manager()
recovery = repair.Repair(manager)

def firewall():
    # Packet counters are observations, not policy changes.
    return re.sub(r'counter packets \d+ bytes \d+', 'counter',
                  command(['nft', 'list', 'table', 'inet', 'nixos-fw']))

original_firewall = firewall()
# Native saved profiles have no declarative Nix owner in this fixture.
with patch.object(repair, 'modules_for', return_value=({}, [])):
    assert recovery.prepare(lambda _: True, management_change) == 'br-repair'
assert command(['systemctl', 'is-active', 'incus.service']) == 'active'
assert command(['systemctl', 'is-active', 'incus-startup.service']) == 'active'
assert firewall() == original_firewall, 'Bootstrap changed the host firewall policy'
assert command(['incus', 'config', 'get', 'native', 'volatile.uuid', '--project', 'lab']) == original
assert command(['incus', 'exec', 'native', '--project', 'lab', '--disable-stdin', '--',
                '/bin/cat', '/var/lib/test/index.html']) == 'preserved'
snapshot = impact.capture(json.loads(Path('/etc/nixlab-impact-fixture.json').read_text()))
assert snapshot['state'] == 'observed', snapshot['state']
assert any(g['name'] == 'native' for g in snapshot['guests']), snapshot['guests']
assert repair.DROPIN.is_file()
assert all(p['auto'] == 'yes' for p in repair.profiles() if p['id'] in ('repair-port', 'repair-bridge'))
# Model the native configuration becoming corrected at the normal updater seam.
# The actual native identity command must then work without the runtime override.
corrected = copy.deepcopy(broken)
corrected['management']['interface'] = 'br-repair'
rt.write_json(Path('/var/lib/nixlab-test-identity.json'), corrected)
recovery.native_identity()
assert command(['incus', 'config', 'get', 'native', 'volatile.uuid', '--project', 'lab']) == original
assert command(['incus', 'exec', 'native', '--project', 'lab', '--disable-stdin', '--',
                '/bin/cat', '/var/lib/test/index.html']) == 'preserved'
assert not repair.DROPIN.exists()
recovery.finish(True)
assert rt.read_json(recovery.path)['phase'] == 'completed'
print('Native bridge recovery unlocks real Incus inventory and retains existing guest identity/data; runtime override removed.')
