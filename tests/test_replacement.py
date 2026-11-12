"""Recovery checkpoints refuse uncertain imports, changed material and cutover."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import replacement as r
import recovery
import recovery_guard
import runtime as rt


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name); self.source = self.root / 'generation'; self.source.mkdir()
        self.state = self.root / 'state'; self.state.mkdir()
        self.mode = self.root / 'mode'; self.mode.write_text('1')
        for module, key, value in ((rt, 'STATE', self.state), (r, 'MODE', self.mode), (r, 'DESIRED', self.root / 'desired.json')):
            mocked = patch.object(module, key, value); mocked.start(); self.addCleanup(mocked.stop)
        payload = self.root / 'payload'; payload.write_text('independent-data')
        self.hashes = {}
        for name, kind, member in (('config', 'configuration', 'config/flake.nix'), ('secrets', 'secrets', 'runtime/password')):
            stream = io.BytesIO(); recovery.write_archive(stream, {member: payload}, kind)
            self.archive('nixlab-' + name + '.tar.gz', stream.getvalue())
        self.archive('instance-' + 'a' * 64 + '.tar.gz', b'guest archive')
        self.archive('volume-' + 'b' * 64 + '.tar.gz', b'volume archive')
        self.inventory = {'format': 2, 'created': 100, 'archiveSHA256': self.hashes,
                          'projects': [{'name': 'lab', 'config': {'features.storage.volumes': 'true'}}],
                          'coverageDetails': {'complete': True},
                          'customVolumes': [{'project': 'lab', 'pool': 'old', 'name': 'data', 'archive': 'volume-' + 'b' * 64 + '.tar.gz'}],
                          'guests': [{'project': 'lab', 'name': 'apps', 'type': 'virtual-machine',
                                      'archive': 'instance-' + 'a' * 64 + '.tar.gz', 'profiles': ['base'],
                                      'expanded_devices': {'root': {'type': 'disk', 'path': '/', 'pool': 'old'},
                                                           'eth0': {'type': 'nic', 'network': 'oldnet'},
                                                           'data': {'type': 'disk', 'path': '/data', 'pool': 'old', 'source': 'data'}}}]}
        self.write_inventory()
        self.pool = {'driver': 'dir', 'config': {'source': '/newpool'}, 'used_by': []}
        self.projects, self.networks, self.profiles, self.volumes, self.guests = {}, {}, {}, {}, {}
        self.calls = []; self.crash_import = False
        for key, implementation in (('api', self.api), ('edit', self.edit)):
            mocked = patch.object(r, key, side_effect=implementation); mocked.start(); self.addCleanup(mocked.stop)
        mocked = patch.object(r.ops, 'incus', side_effect=self.incus); mocked.start(); self.addCleanup(mocked.stop)

    def archive(self, name, data):
        (self.source / name).write_bytes(data); self.hashes[name] = hashlib.sha256(data).hexdigest()

    def write_inventory(self):
        rt.write_json(self.source / 'inventory.json', self.inventory)

    def api(self, path):
        if path == '/1.0/storage-pools/new': return copy.deepcopy(self.pool)
        if path == '/1.0/projects?recursion=1': return [{'name': name, **value} for name, value in self.projects.items()]
        if path == '/1.0/networks?recursion=1': return [{'name': name, **value} for name, value in self.networks.items()]
        if path.startswith('/1.0/projects/'): return copy.deepcopy(self.projects[path.split('/')[-1]])
        if path.startswith('/1.0/networks/'): return copy.deepcopy(self.networks[path.split('/')[-1]])
        if path.startswith('/1.0/profiles?'): return [{'name': name, **value} for name, value in self.profiles.items()]
        if '/volumes/custom?' in path: return [{'name': name, **value} for name, value in self.volumes.items()]
        if '/volumes/custom/' in path: return copy.deepcopy(self.volumes[path.split('/custom/')[1].split('?')[0]])
        if path.startswith('/1.0/instances?'): return [{'name': name, **value} for name, value in self.guests.items()]
        if path.startswith('/1.0/instances/'): return copy.deepcopy(self.guests[path.split('/instances/')[1].split('?')[0]])
        raise AssertionError(path)

    def incus(self, *args, **kwargs):
        self.calls.append(args)
        if args[:2] == ('project', 'create'):
            config = dict(args[index + 1].split('=', 1) for index, value in enumerate(args) if value == '-c')
            self.projects[args[2]] = {'config': config}; self.profiles['default'] = {'config': {}, 'devices': {}}
        elif args[:2] == ('network', 'create'):
            self.networks[args[2]] = {'config': dict(value.split('=', 1) for value in args[3:])}
        elif args[:2] == ('profile', 'create'): self.profiles[args[2]] = {'config': {}, 'devices': {}}
        elif args[:3] == ('storage', 'volume', 'import'):
            self.volumes[args[5]] = {'config': {}}
            if self.crash_import: raise OSError('controller stopped after import')
        elif args[:3] == ('storage', 'volume', 'set'):
            key, value = args[5].split('=', 1); self.volumes[args[4]]['config'][key] = value
        elif args[0] == 'import':
            config = dict(args[index + 1].split('=', 1) for index, value in enumerate(args) if value == '--config')
            config['volatile.uuid'] = 'preserved-import-identity'
            self.guests[args[2]] = {'status': 'Stopped', 'config': config, 'profiles': ['base'], 'devices': {}}
        else: raise AssertionError(args)
        return ''

    def edit(self, args, document):
        self.calls.append(tuple(args))
        if args[0] == 'profile': self.profiles[args[2]] = copy.deepcopy(document)
        else: self.guests[args[2]].update(copy.deepcopy(document))

    def plan(self):
        return r.plan(self.source, {'pool': 'new', 'interfaces': {}, 'disks': {}})

    def test_material_corruption_refuses_before_journaling_or_incusing(self):
        (self.source / next(iter(self.hashes))).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum'): self.plan()
        self.assertFalse(r.journal().exists()); self.assertEqual(self.calls, [])

    def test_recovery_provenance_comes_from_verified_configuration_material(self):
        lock = self.root / 'lock'; lock.write_text('{"nodes":{}}\n')
        stream = io.BytesIO()
        with patch.object(recovery.upgrades, 'metadata', return_value={'revision': 'a' * 40}):
            recovery.write_archive(stream, {'config/flake.lock': lock}, 'configuration')
        self.archive('nixlab-config.tar.gz', stream.getvalue()); self.write_inventory()
        result = self.plan()
        from commissioning import digest
        self.assertEqual(result['sourceRevision'], 'a' * 40)
        self.assertEqual(result['sourceInputsDigest'], digest(lock.read_text().strip()))

    def test_missing_volume_or_external_attachment_refuses_before_import(self):
        self.inventory['customVolumes'] = []; self.write_inventory()
        with self.assertRaisesRegex(ValueError, 'custom volume'): self.plan()
        self.inventory['guests'][0]['expanded_devices']['data'] = {'type': 'disk', 'path': '/data', 'source': '/production'}
        self.write_inventory()
        with self.assertRaisesRegex(ValueError, 'External'): self.plan()
        self.assertEqual(self.calls, [])

    def test_confirmation_and_recovery_mode_are_mandatory(self):
        self.plan()
        with self.assertRaisesRegex(ValueError, '--confirm'): r.resume()
        self.mode.unlink()
        with self.assertRaisesRegex(ValueError, 'recovery.enable'): r.resume(True)
        self.assertEqual(self.calls, [])

    def test_imports_are_stopped_isolated_and_repeated_resume_does_not_reimport(self):
        self.plan(); result = r.resume(True)
        self.assertEqual(result['phase'], 'isolated'); self.assertEqual(result['restore'], 'unverified')
        guest = next(iter(self.guests.values()))
        self.assertEqual(guest['devices']['eth0'], {'type': 'none'})
        self.assertEqual(guest['profiles'], [])
        self.assertEqual(guest['config']['boot.autostart'], 'false')
        self.assertIn(guest['devices']['data']['source'], self.volumes)
        imports = [call for call in self.calls if call[0] == 'import' or call[:3] == ('storage', 'volume', 'import')]
        self.pool['used_by'] = ['/newly-imported-object']
        r.resume(True)
        self.assertEqual(imports, [call for call in self.calls if call[0] == 'import' or call[:3] == ('storage', 'volume', 'import')])
        self.assertFalse(any(call[0] in ('start', 'init', 'delete') for call in self.calls))
        self.assertTrue((self.state / ('recovery-' + result['id']) / 'secrets/runtime/password').exists())

    def test_uncertain_volume_import_requires_operator_verification(self):
        self.plan(); self.crash_import = True
        with self.assertRaises(OSError): r.resume(True)
        self.assertEqual(r.status()['phase'], 'blocked')
        self.crash_import = False
        with self.assertRaisesRegex(ValueError, 'operator verification'): r.resume(True)
        self.assertEqual(len([call for call in self.calls if call[:3] == ('storage', 'volume', 'import')]), 1)
        next(iter(self.volumes.values()))['config']['user.nixlab.recovery-id'] = r.status()['id']
        self.assertEqual(r.resume(True)['phase'], 'isolated')

    def test_mapping_and_source_changes_cannot_resume(self):
        self.plan(); self.pool['config']['source'] = '/different'
        with self.assertRaisesRegex(ValueError, 'pool configuration'): r.resume(True)
        self.pool['config']['source'] = '/newpool'
        self.inventory['created'] = 200; self.write_inventory()
        with self.assertRaisesRegex(ValueError, 'inventory changed'): r.resume(True)
        self.assertEqual(self.calls, [])

    def test_post_checkpoint_guest_drift_stops_resume(self):
        self.plan(); r.resume(True)
        next(iter(self.guests.values()))['devices']['eth0'] = {'type': 'nic', 'network': 'production'}
        with self.assertRaisesRegex(ValueError, 'guest changed'): r.resume(True)

    def test_release_requires_data_attestation_and_never_reconnects_network(self):
        self.plan(); r.resume(True)
        with self.assertRaisesRegex(ValueError, '--data-verified'): r.release(True)
        rt.write_json(r.DESIRED, [{'project': 'lab', 'name': 'missing'}])
        with self.assertRaisesRegex(ValueError, 'empty guests'): r.release(True, True)
        rt.write_json(r.DESIRED, [{'project': 'lab', 'name': 'apps'}])
        imported = self.guests.pop(next(iter(self.guests))); self.guests['apps'] = imported
        before = len(self.calls)
        self.assertEqual(r.release(True, True)['restore'], 'operator-attested')
        self.assertEqual(len(self.calls), before)


class RecoveryGuardTests(unittest.TestCase):
    def test_incomplete_or_corrupt_journal_blocks_automation_after_mode_removal(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'NIXLAB_STATE': folder}), \
                patch.object(recovery_guard, 'MODE', Path(folder) / 'mode'):
            recovery_guard.require_normal()
            path = Path(folder) / 'replacement-recovery.json'
            for content in ('invalid', '{"format":1,"phase":"isolated"}'):
                path.write_text(content)
                with self.assertRaisesRegex(ValueError, 'Unfinished'): recovery_guard.require_normal()
            path.write_text('{"format":1,"phase":"complete"}')
            recovery_guard.require_normal()
            recovery_guard.MODE.touch()
            with self.assertRaisesRegex(ValueError, 'active'): recovery_guard.require_normal()


if __name__ == '__main__':
    unittest.main()
