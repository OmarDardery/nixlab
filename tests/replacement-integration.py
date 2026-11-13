"""Disposable two-node recovery drill. Never qualifies a physical replacement."""
import io
import json
from pathlib import Path
import secrets
import subprocess
import sys

import operations
import recovery
import replacement
import runtime as rt


if sys.argv[1] == 'material':
    directory = Path(sys.argv[2])
    inventory = json.loads((directory / 'inventory.json').read_text())
    # Validate the producer's real coverage/identity data before supplying the
    # explicitly synthetic host kits; never upgrade missing coverage in a fixture.
    assert inventory['coverageDetails']['complete'] is True
    assert any(item['kind'] == 'custom-volume' and item['identity'].endswith('/guests/recoverdata')
               and item['status'] == 'included' for item in inventory['coverageDetails']['items'])
    guest = next(item for item in inventory['guests'] if (item['project'], item['name']) == ('lab', 'native'))
    current = replacement.api(replacement.guest_path('lab', 'native'))
    assert guest['config']['volatile.uuid'] == current['config']['volatile.uuid']
    # The source fixture has no installed NixLab config/accounts. Supply explicit
    # synthetic recovery kits; the real guest/volume archives remain unmodified.
    payload = Path('/tmp/recovery-fixture-payload'); payload.write_text(secrets.token_hex(24)); payload.chmod(0o600)
    for name, kind, member in (('config', 'configuration', 'config/fixture.nix'), ('secrets', 'secrets', 'runtime/fixture-key')):
        stream = io.BytesIO(); recovery.write_archive(stream, {member: payload}, kind)
        archive = directory / ('nixlab-' + name + '.tar.gz'); archive.write_bytes(stream.getvalue())
        inventory['archiveSHA256'][archive.name] = operations.file_hash(archive)
    rt.write_json(directory / 'inventory.json', inventory)
elif sys.argv[1] == 'restore':
    result = replacement.plan('/root/offhost-generation', {'pool': 'replacement', 'interfaces': {'old-uplink': 'eth1'}, 'disks': {}})
    try:
        replacement.resume()
    except ValueError:
        pass
    else:
        raise AssertionError('Imports require confirmation')
    result = replacement.resume(True)
    assert result['phase'] == 'isolated' and result['restore'] == 'unverified'
    assert result['coverage']['complete'] is True
    rt.write_json(Path('/root/recovery-targets.json'), result)
elif sys.argv[1] == 'verify':
    report = rt.read_json(Path('/root/recovery-targets.json'))
    guest = next(g['target'] for g in report['guests'] if g['source'] == 'lab/native')
    current = replacement.api(replacement.guest_path(report['project'], guest))
    assert current['status'] == 'Stopped' and current['config']['boot.autostart'] == 'false'
    assert current['profiles'] == []
    assert not any(device.get('type') in ('nic', 'usb', 'pci', 'unix-char', 'proxy') for device in current['expanded_devices'].values())
    try:
        print('REPLACEMENT_STAGE: start isolated guest', flush=True)
        operations.incus('start', guest, '--project', report['project'])
        print('REPLACEMENT_STAGE: guest started', flush=True)
    except ValueError:
        # This disposable fixture contains no real users/credentials. Keep
        # bounded LXC diagnostics, never complete host journals or configuration.
        details = operations.incus('info', guest, '--show-log', '--project', report['project'])
        for line in details.splitlines()[-60:]:
            if not any(word in line.lower() for word in ('password', 'token', 'secret', 'private key')):
                print('Replacement startup: ' + line[:1000], flush=True)
        raise
    try:
        assert operations.incus('exec', guest, '--project', report['project'], '--disable-stdin', '--mode=non-interactive', '--', '/bin/cat', '/var/lib/test/index.html').strip() == 'preserved'
        print('REPLACEMENT_STAGE: guest disk record verified', flush=True)
        assert operations.incus('exec', guest, '--project', report['project'], '--disable-stdin', '--mode=non-interactive', '--', '/bin/cat', '/recoverdata/record').strip() == 'attached-data'
        print('REPLACEMENT_STAGE: attached volume record verified', flush=True)
    finally:
        print('REPLACEMENT_STAGE: stop isolated guest', flush=True)
        operations.incus('stop', guest, '--project', report['project'], '--timeout', '120', timeout=180)
        print('REPLACEMENT_STAGE: guest stopped', flush=True)
    assert replacement.resume(True)['phase'] == 'isolated'
    print('REPLACEMENT_STAGE: durable checkpoint resume verified', flush=True)
    assert replacement.status()['restore'] == 'unverified'
    print('Independent Incus node imported real container and attached-volume data; stopped quarantine and resumability verified. Synthetic config kits; no physical qualification.')
else:
    raise ValueError('Unknown replacement fixture action')
