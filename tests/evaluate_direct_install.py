"""Evaluate real copied-machine inputs, including admin-key privilege filtering."""
import base64
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ('installer', 'admin', 'scripts')]
import direct


def evaluate(root, attribute, apply=None):
    args = ['nix', 'eval', '--json', 'path:' + str(root) + '#' + attribute]
    if apply:
        args += ['--apply', apply]
    return json.loads(subprocess.check_output(args, text=True))


def key(value):
    wire = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes([value]) * 32
    return 'ssh-ed25519 ' + base64.b64encode(wire).decode() + ' fixture'


with tempfile.TemporaryDirectory(prefix='nixlab-direct-eval-') as folder:
    root = Path(folder) / 'source'
    subprocess.run(['git', 'clone', '--quiet', '--no-hardlinks', str(ROOT), str(root)], check=True)
    installed = root / 'machine/installed-system'
    installed.mkdir(parents=True)
    (root / 'machine/format.json').write_text('{"version":1}')
    (installed / 'hardware-configuration.nix').write_text((ROOT / 'tests/hardware.nix').read_text())
    (installed / 'configuration.nix').write_text('''{ ... }: {
      imports = [ ./hardware-configuration.nix ];
      system.stateVersion = "25.11";
      networking.networkmanager.enable = true;
      users.users.root.openssh.authorizedKeys.keys = [ %s ];
      users.users.reader = { isNormalUser = true; openssh.authorizedKeys.keys = [ %s ]; };
      users.users.administrator = {
        isNormalUser = true; extraGroups = [ "wheel" ];
        openssh.authorizedKeys.keys = [ %s ];
      };
    }''' % tuple(json.dumps(key(n)) for n in (1, 2, 3)))
    settings = json.loads((ROOT / 'tests/fixtures/defaults.json').read_text())
    settings.update(cpuVendor='intel', hostName='direct-fixture')
    settings['setup']['enable'] = True
    settings['management'].update(interface='eth0', address='192.0.2.10', prefixLength=24,
                                  gateway='192.0.2.1', trustedCIDRs=['192.0.2.0/24'])
    path = root / 'machine/settings.json'
    path.write_text(json.dumps(settings))
    overview = evaluate(root, 'nixlabOverview')
    assert set(overview['adminKeys']) == {key(1), key(3)}, overview['adminKeys']
    host = {'architecture': 'x86_64', 'cpuVendor': 'intel', 'kvm': True,
            'interfaces': [{'name': 'eth0', 'wireless': False, 'addresses': ['192.0.2.10/24']}],
            'routes': [{'dst': '192.0.2.0/24', 'dev': 'eth0'}], 'disks': []}
    candidate = direct.prepare(settings, overview, host, apps=False)
    path.write_text(json.dumps(candidate))
    actual = evaluate(root, 'nixosConfigurations.hypervisor.config', '''c: {
      stateVersion = c.system.stateVersion;
      rootDevice = c.fileSystems."/".device;
      networkManager = c.networking.networkmanager.enable;
      settings = c.nixlab.settings;
      incus = c.virtualisation.incus.enable;
      cockpit = c.services.cockpit.enable;
      hostDocker = c.virtualisation.docker.enable;
      readerKeys = c.users.users.reader.openssh.authorizedKeys.keys;
    }''')
    assert actual['stateVersion'] == '25.11', actual
    assert actual['rootDevice'] == '/dev/disk/by-label/TEST-ONLY', actual
    assert actual['networkManager'] and actual['incus'] and actual['cockpit'], actual
    assert not actual['hostDocker'], actual
    assert actual['readerKeys'] == [key(2)], actual
    assert actual['settings']['management'] == settings['management'], actual
    assert set(actual['settings']['adminKeys']) == {key(1), key(3)}, actual
    # Evaluate the actual system derivation to force NixOS assertions too.
    evaluate(root, 'nixosConfigurations.hypervisor.config.system.build.toplevel.drvPath')
    # A physical-console install is valid without a key. Adoption can use an
    # independent bridge/pool without changing native Incus resources.
    candidate['adminKeys'] = []
    candidate['guests']['bridge'] = 'nixlabbr'
    candidate['storage']['pool'] = 'nixlab-guests'
    candidate['storage']['source'] = '/var/lib/incus/storage-pools/nixlab-guests'
    candidate['connectivity']['guestInterfaces'] = ['nixlabbr']
    candidate['connectivity']['preservedIncusBridges'] = ['incusbr0']
    path.write_text(json.dumps(candidate))
    adopted = evaluate(root, 'nixosConfigurations.hypervisor.config', '''c: {
      pool = (builtins.head c.virtualisation.incus.preseed.storage_pools).name;
      bridge = (builtins.head c.virtualisation.incus.preseed.networks).name;
      nat = c.networking.nat.internalInterfaces;
      passwordLogin = c.services.openssh.settings.PasswordAuthentication;
    }''')
    assert adopted == {'pool': 'nixlab-guests', 'bridge': 'nixlabbr', 'nat': ['nixlabbr'], 'passwordLogin': False}, adopted
    evaluate(root, 'nixosConfigurations.hypervisor.config.system.build.toplevel.drvPath')
    print('Direct conversion: preserved machine inputs and full hypervisor evaluation passed.')

