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
    rt.write_json(Path('/root/recovery-targets.json'), result)
elif sys.argv[1] == 'verify':
    report = rt.read_json(Path('/root/recovery-targets.json'))
    guest = next(g['target'] for g in report['guests'] if g['source'] == 'lab/native')
    current = replacement.api(replacement.guest_path(report['project'], guest))
    assert current['status'] == 'Stopped' and current['config']['boot.autostart'] == 'false'
    assert current['profiles'] == []
    assert not any(device.get('type') in ('nic', 'usb', 'pci', 'unix-char', 'proxy') for device in current['expanded_devices'].values())
    operations.incus('start', guest, '--project', report['project'])
    try:
        assert operations.incus('exec', guest, '--project', report['project'], '--', '/bin/cat', '/var/lib/test/index.html').strip() == 'preserved'
        assert operations.incus('exec', guest, '--project', report['project'], '--', '/bin/cat', '/recoverdata/record').strip() == 'attached-data'
    finally:
        operations.incus('stop', guest, '--project', report['project'], '--timeout', '120', timeout=180)
    assert replacement.resume(True)['phase'] == 'isolated'
    assert replacement.status()['restore'] == 'unverified'
    print('Independent Incus node imported real container and attached-volume data; stopped quarantine and resumability verified. Synthetic config kits; no physical qualification.')
else:
    raise ValueError('Unknown replacement fixture action')
