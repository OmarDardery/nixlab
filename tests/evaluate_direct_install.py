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
      programs.bash.shellAliases.ll = "ls --all";
      programs.starship.settings.directory.truncation_length = 5;
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
    # The backend reads the same pure Nix entrypoint, without a Python migration.
    # Reading legacy JSON is idempotent and cannot rewrite the installed file.
    from settings_reader import read_settings
    legacy = json.loads(json.dumps(settings))
    legacy.pop('schemaVersion')
    legacy['ready'] = True
    legacy['monitoring'].pop('collection')
    legacy_bytes = json.dumps(legacy)
    path.write_text(legacy_bytes)
    normalized = read_settings(root)
    assert normalized['schemaVersion'] == 1 and normalized['monitoring']['collection'] is True
    assert path.read_text() == legacy_bytes
    assert read_settings(root) == normalized
    path.write_text(json.dumps(normalized))
    assert read_settings(root) == normalized
    path.write_text('{"schemaVersion":2}')
    try:
        read_settings(root)
    except ValueError as error:
        assert 'Unsupported NixLab settings schema' in str(error)
    else:
        raise AssertionError('Future schema was accepted')
    path.write_text('{"secret":"SENTINEL-malformed-config"')
    try:
        read_settings(root)
    except ValueError as error:
        assert 'SENTINEL' not in str(error)
    else:
        raise AssertionError('Malformed JSON was accepted')
    # Roll back to the original source-compatible input bytes, then normal flow.
    path.write_text(legacy_bytes)
    assert read_settings(root) == normalized
    # Model a pre-schema source contract returning v0 intent. The reader must
    # obey the selected source rather than impose a newer Python interpretation.
    site_path = root / 'site.nix'
    source_bytes = site_path.read_text()
    try:
        site_path.write_text('builtins.fromJSON (builtins.readFile ./machine/settings.json)')
        assert read_settings(root) == legacy
    finally:
        site_path.write_text(source_bytes)
    assert read_settings(root) == normalized
    path.write_text(json.dumps(settings))
    bootstrap = evaluate(root, 'nixosConfigurations.bootstrap.config', '''c: {
      ready = c.nixlab.settings.ready;
      stateVersion = c.system.stateVersion;
      rootDevice = c.fileSystems."/".device;
      networkManager = c.networking.networkmanager.enable;
      portal = c.nixlab.settings.setup.enable;
      incus = c.virtualisation.incus.enable;
      cockpit = c.services.cockpit.enable;
      docker = c.virtualisation.docker.enable;
      guard = c.systemd.timers.nixlab-rollback-guard.wantedBy;
    }''')
    assert not bootstrap['ready'] and bootstrap['portal'], bootstrap
    assert bootstrap['stateVersion'] == '25.11' and bootstrap['networkManager'], bootstrap
    assert bootstrap['rootDevice'] == '/dev/disk/by-label/TEST-ONLY', bootstrap
    assert not bootstrap['incus'] and not bootstrap['cockpit'] and not bootstrap['docker'], bootstrap
    assert 'timers.target' in bootstrap['guard'], bootstrap
    evaluate(root, 'nixosConfigurations.bootstrap.config.system.build.toplevel.drvPath')
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
      desired = c.nixlab.desiredSettings;
      incus = c.virtualisation.incus.enable;
      cockpit = c.services.cockpit.enable;
      hostDocker = c.virtualisation.docker.enable;
      passwordSSH = c.services.openssh.settings.PasswordAuthentication;
      rootSSH = c.services.openssh.settings.PermitRootLogin;
      displayManager = c.services.displayManager.enable;
      suspend = c.systemd.sleep.settings.Sleep.AllowSuspend;
      readerKeys = c.users.users.reader.openssh.authorizedKeys.keys;
      alias = c.programs.bash.shellAliases.ll;
      promptLength = c.programs.starship.settings.directory.truncation_length;
    }''')
    assert actual['stateVersion'] == '25.11', actual
    assert actual['rootDevice'] == '/dev/disk/by-label/TEST-ONLY', actual
    assert actual['networkManager'] and actual['incus'] and actual['cockpit'], actual
    assert not actual['hostDocker'], actual
    assert actual['passwordSSH'] and actual['rootSSH'] == 'no', actual
    assert not actual['displayManager'] and not actual['suspend'], actual
    assert actual['readerKeys'] == [key(2)], actual
    assert actual['alias'] == 'ls --all' and actual['promptLength'] == 5, actual
    assert actual['settings']['management'] == settings['management'], actual
    assert actual['desired'] == read_settings(root), actual
    assert set(actual['settings']['adminKeys']) == {key(1), key(3)}, actual
    module_dir = root / 'machine/modules'
    module_dir.mkdir(parents=True, exist_ok=True)
    override_module = module_dir / 'settings-override.nix'
    override_module.write_text('{ lib, ... }: { nixlab.settings.hostName = lib.mkForce "file-owned"; }')
    overridden = evaluate(root, 'nixosConfigurations.hypervisor.config',
        'c: { desired = c.nixlab.desiredSettings.hostName; effective = c.nixlab.settings.hostName; }')
    assert overridden == {'desired': candidate['hostName'], 'effective': 'file-owned'}, overridden
    override_module.unlink()
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
    assert adopted == {'pool': 'nixlab-guests', 'bridge': 'nixlabbr', 'nat': ['nixlabbr'], 'passwordLogin': True}, adopted
    evaluate(root, 'nixosConfigurations.hypervisor.config.system.build.toplevel.drvPath')
    candidate['access']['sshPasswordLogin'] = False
    path.write_text(json.dumps(candidate))
    assert not evaluate(root, 'nixosConfigurations.hypervisor.config.services.openssh.settings.PasswordAuthentication')
    print('Direct conversion: preserved machine inputs and full hypervisor evaluation passed.')

    # Source-defined native profiles must keep autoconnect after Nix activation,
    # rather than relying on an nmcli runtime edit that the next reload erases.
    import network_repair
    module_dir = root / 'machine/modules'
    module_dir.mkdir(parents=True, exist_ok=True)
    fixture_module = module_dir / 'repair-profile-fixture.nix'
    fixture_module.write_text('''{ ... }: {
      networking.networkmanager.ensureProfiles.profiles.existing-management.connection = {
        id = "existing-management"; type = "ethernet";
        interface-name = "eth0"; autoconnect = false;
      };
    }''')
    selected = [{'id': 'existing-management', 'interface': 'eth0'}]
    modules, names = network_repair.modules_for(root, selected)
    assert names == ['existing-management'], names
    for name, content in modules.items(): (module_dir / name).write_text(content)
    assert network_repair.declared_profiles(root)['existing-management']['auto'] is True
    evaluate(root, 'nixosConfigurations.hypervisor.config.system.build.toplevel.drvPath')
    fixture_module.write_text('''{ lib, ... }: {
      networking.networkmanager.ensureProfiles.profiles.existing-management.connection = {
        id = "existing-management"; type = "ethernet";
        interface-name = "eth0"; autoconnect = lib.mkForce false;
      };
    }''')
    assert network_repair.declared_profiles(root)['existing-management']['auto'] is False
    print('Recovery profile module evaluates persistently; stronger user overrides remain visible to the activation refusal gate.')
