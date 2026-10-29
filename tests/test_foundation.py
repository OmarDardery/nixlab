"""Failure boundaries for the focused host foundation; no host mutations."""
import copy
import hashlib
import importlib.util
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import backup
import backup_health
import foundation
import infrastructure
import model
import operations
import runtime as rt
import upgrades
import controller

spec = importlib.util.spec_from_file_location('helper_runner', BASE / 'helpers/runner.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)


class BackupGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.states = {'one': 'Running', 'two': 'Running', 'off': 'Stopped'}
        self.calls = []
        self.fail = None
        for attribute, value in [('ROOT', self.root), ('PENDING', self.root / 'pending.json')]:
            p = patch.object(backup, attribute, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(backup, 'incus', side_effect=self.incus); p.start(); self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'NIXLAB_DEMO_BACKUP': '0', 'NIXLAB_BACKUP_POLICY': ''}); p.start(); self.addCleanup(p.stop)

    def incus(self, *args):
        self.calls.append(args)
        if args[:2] == ('project', 'list'): return json.dumps([{'name': 'lab', 'config': {'features.storage.volumes': 'true'}}])
        if args[0] == 'list': return json.dumps([{'name': n, 'status': s} for n, s in self.states.items()])
        if args[0] == 'query':
            return json.dumps({'config': {}} if args[1] == '/1.0' else {'status': self.states[args[1].split('/')[3]]})
        if args[0] in ('start', 'stop'):
            self.states[args[1]] = 'Running' if args[0] == 'start' else 'Stopped'; return ''
        if args[0] == 'export':
            if args[1] == self.fail: raise RuntimeError('second export failed')
            Path(args[4]).write_bytes(('data-' + args[1]).encode()); return ''
        return '[]'

    def test_failed_later_export_preserves_entire_published_generation_and_legacy(self):
        (self.root / 'inventory.json').write_text('legacy')
        previous = backup.backup(); before = {p.name: p.read_bytes() for p in previous.iterdir()}
        pointer = (self.root / 'latest.json').read_bytes()
        self.fail = 'two'
        with self.assertRaisesRegex(RuntimeError, 'second export'): backup.backup()
        self.assertEqual((self.root / 'latest.json').read_bytes(), pointer)
        self.assertEqual({p.name: p.read_bytes() for p in previous.iterdir()}, before)
        self.assertEqual((self.root / 'inventory.json').read_text(), 'legacy')
        self.assertEqual(list((self.root / '.incomplete').iterdir()), [])
        self.assertEqual(self.states, {'one': 'Running', 'two': 'Running', 'off': 'Stopped'})

    def test_one_guest_recovers_before_next_stops(self):
        backup.backup()
        start_one = next(i for i, args in enumerate(self.calls) if args[:2] == ('start', 'one'))
        stop_two = next(i for i, args in enumerate(self.calls) if args[:2] == ('stop', 'two'))
        self.assertLess(start_one, stop_two)
        self.assertFalse(any(args[:2] == ('start', 'off') for args in self.calls))

    def test_backup_freshness_uses_timer_trigger_not_a_daily_interval(self):
        evaluate = backup_health.evaluate
        self.assertEqual(evaluate(True, 'complete', 'inactive', 'success', 100, 101, 1000000), 'successful')
        self.assertEqual(evaluate(True, '', 'inactive', 'success', 100, 0, 1000), 'never-run')
        self.assertEqual(evaluate(True, '', 'inactive', 'success', 100, 0, 7300), 'overdue')
        self.assertEqual(evaluate(True, '', 'activating', 'success', 100, 0, 1000000), 'running')
        self.assertEqual(evaluate(True, 'complete-with-exclusions', 'inactive', 'success', 100, 0, 1000000), 'partial')
        self.assertEqual(evaluate(True, 'complete', 'failed', 'exit-code', 100, 101, 1000000), 'failed')
        self.assertEqual(evaluate(False, '', '', '', 0, 0, 0), 'disabled')

    def test_cancellation_recovers_guest_and_never_publishes(self):
        original = self.incus
        def cancel(*args):
            if args[0] == 'export': raise InterruptedError('cancelled')
            return original(*args)
        with patch.object(backup, 'incus', side_effect=cancel), self.assertRaises(InterruptedError): backup.backup()
        self.assertEqual(self.states['one'], 'Running')
        self.assertFalse((self.root / 'latest.json').exists())
        self.assertEqual(json.loads(backup.PENDING.read_text()), [])

    def test_recovery_kit_failure_cannot_publish_or_replace_previous_generation(self):
        previous = backup.backup()
        with patch.dict(os.environ, {'NIXLAB_RECOVERY_TOOL': '/fake/admin'}), \
                patch.object(backup.subprocess, 'run', side_effect=RuntimeError('kit failed')), self.assertRaisesRegex(RuntimeError, 'kit failed'):
            backup.backup(with_recovery=True)
        self.assertEqual(json.loads((self.root / 'latest.json').read_text())['generation'], previous.name)

    def test_keeps_two_complete_generations_and_checksums_match(self):
        paths = [backup.backup() for _ in range(3)]
        self.assertFalse(paths[0].exists()); self.assertTrue(paths[1].is_dir()); self.assertTrue(paths[2].is_dir())
        manifest = json.loads((paths[-1] / 'inventory.json').read_text())
        for name, digest in manifest['archiveSHA256'].items():
            self.assertEqual(hashlib.sha256((paths[-1] / name).read_bytes()).hexdigest(), digest)

    def test_excluded_guest_is_not_stopped_and_partial_coverage_is_explicit(self):
        policy = self.root / 'policy.json'; policy.write_text(json.dumps({'exclude': ['lab/one']}))
        with patch.dict(os.environ, {'NIXLAB_BACKUP_POLICY': str(policy)}): directory = backup.backup()
        manifest = json.loads((directory / 'inventory.json').read_text())
        self.assertEqual(manifest['excluded'], ['lab/one']); self.assertEqual(manifest['coverage'], 'selected')
        self.assertNotIn('one', [g['name'] for g in manifest['guests']])
        self.assertFalse(any(args[:2] == ('stop', 'one') for args in self.calls))
        self.assertEqual(json.loads((self.root / 'backup-status.json').read_text())['phase'], 'complete-with-exclusions')

    def test_capacity_failure_happens_before_stopping_any_guest(self):
        with patch.object(backup.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 1})()), \
                self.assertRaisesRegex(ValueError, 'space'):
            backup.backup()
        self.assertFalse(any(args[0] == 'stop' for args in self.calls))

    def test_shared_volume_and_explicit_groups_join_consumers_across_projects(self):
        device = {'data': {'type': 'disk', 'pool': 'guests', 'path': '/data', 'source': 'shared'}}
        guests = [{'project': p, 'name': n, 'expanded_devices': device if n != 'three' else {}}
                  for p, n in [('lab', 'one'), ('other', 'two'), ('lab', 'three')]]
        volume = {'project': 'default', 'pool': 'guests', 'name': 'shared'}
        groups = backup.consistency_groups(guests, [volume], [['lab/one', 'lab/three']], {'lab': 'default', 'other': 'default'})
        self.assertEqual(len(groups), 1); self.assertEqual(len(groups[0]['guests']), 3)
        self.assertEqual(groups[0]['volumes'], [volume])

    def test_readonly_volume_survives_reader_exclusions_and_unknown_exclusions_refuse_shutdown(self):
        device = {'data': {'type': 'disk', 'pool': 'guests', 'source': 'shared', 'path': '/data', 'readonly': 'true'}}
        guests = [{'project': 'lab', 'name': n, 'expanded_devices': device} for n in ('one', 'two')]
        volume = {'project': 'lab', 'pool': 'guests', 'name': 'shared'}
        groups = backup.consistency_groups(guests, [volume], [])
        self.assertEqual(len(groups), 3)
        self.assertEqual(next(g for g in groups if g['volumes'])['guests'], [])
        policy = self.root / 'policy.json'; policy.write_text(json.dumps({'exclude': ['lab/typo']}))
        with patch.dict(os.environ, {'NIXLAB_BACKUP_POLICY': str(policy)}), self.assertRaisesRegex(ValueError, 'absent'):
            backup.backup()
        self.assertFalse(any(args[0] == 'stop' for args in self.calls))

    def test_custom_volume_failure_recovers_every_group_member_without_publication(self):
        original = self.incus
        def fake(*args):
            if args[:2] == ('storage', 'list'): return '[{"name":"guests"}]'
            if args[:3] == ('storage', 'volume', 'list'): return '[{"name":"shared","type":"custom"}]'
            if args[0] == 'list':
                values = json.loads(original(*args))
                for g in values:
                    if g['name'] in ('one', 'two'):
                        g['expanded_devices'] = {'data': {'type':'disk','pool':'guests','source':'shared','path':'/data'}}
                return json.dumps(values)
            if args[:3] == ('storage', 'volume', 'export'): raise RuntimeError('volume failed')
            return original(*args)
        with patch.object(backup, 'incus', side_effect=fake), self.assertRaisesRegex(RuntimeError, 'volume failed'):
            backup.backup()
        self.assertEqual(self.states, {'one':'Running','two':'Running','off':'Stopped'})
        self.assertFalse((self.root / 'latest.json').exists())
        self.assertEqual(list((self.root / '.incomplete').iterdir()), [])

    def test_exclusion_protects_entire_explicit_group(self):
        policy = self.root / 'policy.json'
        policy.write_text(json.dumps({'groups': [['lab/one','lab/two']], 'exclude':['lab/one']}))
        with patch.dict(os.environ, {'NIXLAB_BACKUP_POLICY': str(policy)}): directory = backup.backup()
        manifest = json.loads((directory / 'inventory.json').read_text())
        self.assertEqual(set(manifest['excluded']), {'lab/one','lab/two'})
        self.assertFalse(any(args[0] == 'stop' for args in self.calls))
        policy.write_text(json.dumps({'groups': [['lab/one','lab/two']]}))
        original = self.incus
        def manual_profile(*args):
            result = original(*args)
            if args[0] == 'list':
                values = json.loads(result)
                values[0]['expanded_config'] = {'user.nixlab.backup': 'manual'}
                return json.dumps(values)
            return result
        with patch.dict(os.environ, {'NIXLAB_BACKUP_POLICY': str(policy)}), patch.object(backup, 'incus', side_effect=manual_profile):
            directory = backup.backup()
        self.assertEqual(set(json.loads((directory / 'inventory.json').read_text())['excluded']), {'lab/one','lab/two'})
        self.assertFalse(any(args[0] == 'stop' for args in self.calls))


class ArchiveTests(unittest.TestCase):
    def test_archive_catalog_is_independent_of_thirty_recent_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); ops = operations.Operations(root / 'root', root / 'state')
            export_id = 'a' * 32; directory = ops.directory / export_id; directory.mkdir()
            (directory / 'guest.tar.gz').write_bytes(b'export')
            rt.write_json(directory / 'job.json', {'id': export_id, 'action': 'export', 'status': 'complete',
                                                  'project': 'lab', 'name': 'guest', 'created': 1})
            os.utime(directory, (1, 1))
            for index in range(35):
                job = ops.directory / f'{index:032x}'; job.mkdir()
                rt.write_json(job / 'job.json', {'status': 'complete', 'action': 'start'})
                os.utime(job, (index + 2, index + 2))
            self.assertEqual(len(ops.status()), 30)
            self.assertEqual(ops.archives()[0]['id'], export_id)

    def test_generation_archives_are_restorable_without_an_on_demand_job(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); ops = operations.Operations(root / 'root', root / 'state')
            generation = '123-' + 'a' * 32; name = backup.artifact('instance', 'lab', 'guest')
            directory = root / 'staging/generations' / generation; directory.mkdir(parents=True)
            archive = directory / name; archive.write_bytes(b'scheduled export')
            rt.write_json(directory / 'inventory.json', {'created': 1, 'guests': [{'project': 'lab', 'name': 'guest', 'archive': name}],
                                                        'archiveSHA256': {name: operations.file_hash(archive)}})
            with patch.object(ops, 'archive_settings', return_value={'archiveRoot': str(root / 'staging/on-demand'), 'archiveMount': None}):
                record = ops.generation_archives()[0]
                self.assertTrue(ops.archives()[0]['available'])
                self.assertEqual(ops.archive_path(record), archive)
                with patch.object(operations, 'guests', return_value=[]), patch.object(operations, 'incus') as incus:
                    result = ops.perform({'action': 'restore', 'project': 'lab', 'name': 'guest', 'target': 'copy',
                                          'archive': record['id'], 'archiveRecord': record})
                    self.assertTrue(result['requiresReview']); self.assertIn(str(archive), incus.call_args.args)
                archive.write_bytes(b'corrupted')
                with patch.object(operations, 'guests', return_value=[]), self.assertRaisesRegex(ValueError, 'checksum'):
                    ops.perform({'action': 'restore', 'project': 'lab', 'name': 'guest', 'target': 'copy', 'archiveRecord': record})


class HostContractTests(unittest.TestCase):
    def setUp(self):
        self.site = json.loads((BASE / 'tests/fixtures/defaults.json').read_text())
        self.site['management']['interface'] = 'eth0'
        self.host = {'interfaces': [{'name': 'eth0', 'kind': 'ethernet', 'addresses': ['192.0.2.10/24']},
                                    {'name': 'eth1', 'kind': 'ethernet', 'addresses': []},
                                    {'name': 'wlan0', 'kind': 'ethernet', 'wireless': True, 'addresses': []}]}

    def test_metrics_setting_preserves_existing_hosts_and_is_off_for_new_ones(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'root'; source = Path(folder) / 'source'; source.mkdir()
            rt.write_json(source / 'default-settings.json', self.site)
            settings = copy.deepcopy(self.site)
            settings['ready'] = True
            settings['monitoring'].pop('collection')
            rt.write_json(root / 'local/settings.json', settings)
            with patch.object(rt, 'SOURCE', source):
                manager = controller.Manager(root, Path(folder) / 'state')
                self.assertTrue(manager.settings()['monitoring']['collection'])
                settings['ready'] = False
                rt.write_json(root / 'local/settings.json', settings)
                self.assertFalse(manager.settings()['monitoring']['collection'])

    def test_management_bridge_requires_address_transfer_and_no_wifi_ports(self):
        self.site['connectivity']['bridges'] = [{'name': 'br-lan', 'interfaces': ['eth0']}]
        with self.assertRaisesRegex(ValueError, 'management addressing'): foundation.validate(self.site, self.host)
        self.site['management'].update(interface='br-lan', configure=True)
        self.assertIn('br-lan', foundation.validate(self.site, self.host))
        self.site['connectivity']['bridges'][0]['interfaces'] = ['wlan0']
        with self.assertRaisesRegex(ValueError, 'Ethernet'): foundation.validate(self.site, self.host)
        self.host['interfaces'].append({'name': 'incus-native', 'kind': 'bridge', 'addresses': []})
        self.site['connectivity']['bridges'][0]['interfaces'] = ['incus-native']
        with self.assertRaisesRegex(ValueError, 'Ethernet'): foundation.validate(self.site, self.host)

    def test_vlan_ports_and_private_firewall_registration(self):
        self.site['connectivity'].update(vlans=[{'name': 'vlan20', 'interface': 'eth1', 'id': 20}],
                                         bridges=[{'name': 'br-apps', 'interfaces': ['vlan20']}])
        foundation.validate(self.site, self.host)
        self.host['interfaces'].append({'name': 'br-native', 'kind': 'bridge', 'addresses': []})
        self.site['connectivity']['vlans'][0]['interface'] = 'br-native'
        with self.assertRaisesRegex(ValueError, 'Ethernet'): foundation.validate(self.site, self.host)
        self.site['connectivity']['vlans'][0]['interface'] = 'eth1'
        self.site['connectivity']['guestInterfaces'].append('eth0')
        with self.assertRaisesRegex(ValueError, 'separate'): foundation.validate(self.site, self.host)

    def test_udev_values_and_private_rules_reject_injection(self):
        value = {'name': 'printer', 'vendorId': '1234', 'productId': 'abcd', 'serial': 'one'}
        self.site['hardware']['serialAliases'] = [value]
        foundation.validate(self.site)
        for serial in ('*', 'x" RUN+="bad', '', 123):
            self.site['hardware']['serialAliases'] = [value | {'serial': serial}]
            with self.assertRaises(ValueError): foundation.validate(self.site)
        self.site['hardware']['serialAliases'] = []
        self.site['connectivity']['privateAccess'] = [{'cidr': '192.168.0.2/32', 'protocol': 'udp', 'ports': [53]}]
        foundation.validate(self.site)
        self.site['connectivity']['privateAccess'][0]['ports'] = [0]
        with self.assertRaisesRegex(ValueError, 'ports'): foundation.validate(self.site)

    def test_serial_inventory_reports_exact_usb_ancestor_and_alias(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); usb = base / 'sys/usb1'; usb.mkdir(parents=True)
            for key, value in {'idVendor': '1234', 'idProduct': '5678', 'serial': 'ABC', 'product': 'Example'}.items(): (usb / key).write_text(value)
            tty = base / 'class/ttyUSB0'; tty.mkdir(parents=True); (tty / 'device').symlink_to(usb)
            dev = base / 'dev'; (dev / 'serial/by-id').mkdir(parents=True); (dev / 'ttyUSB0').touch()
            (dev / 'serial/by-id/example').symlink_to(dev / 'ttyUSB0')
            record = model.serial_inventory(base / 'class', dev)[0]
            self.assertEqual(record['serial'], 'ABC'); self.assertEqual(record['vendorId'], '1234')
            self.assertIn(str(dev / 'serial/by-id/example'), record['stablePaths'])


class OwnershipTests(unittest.TestCase):
    def test_job_cleanup_preserves_active_pending_and_last_confirmed(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / 'state'
            with patch.object(rt, 'STATE', state), patch.object(rt, 'audit'):
                manager = controller.Manager(Path(folder) / 'root', state)
                for index in range(6):
                    directory = state / 'jobs' / f'{index:032x}'
                    directory.mkdir()
                    rt.write_json(directory / 'job.json', {'status': 'confirmed'})
                    os.utime(directory, (index + 1, index + 1))
                for filename, index in [('active.json', 0), ('pending.json', 1), ('last-confirmed.json', 2)]:
                    rt.write_json(state / filename, {'id': f'{index:032x}'})
                self.assertEqual(manager.prune_jobs(keep=2)['jobs'], [f'{3:032x}'])
                manager.prune_jobs(apply=True, keep=2)
                self.assertFalse((state / 'jobs' / f'{3:032x}').exists())
                for index in (0, 1, 2, 4, 5): self.assertTrue((state / 'jobs' / f'{index:032x}').is_dir())

    def test_native_objects_are_untouched_and_reserved_drift_requires_review(self):
        declared = {'profiles': [{'name': 'base', 'project': 'lab', 'config': {'limits.cpu': '2'}}]}
        actual = {'config': {'limits.cpu': '2'}, 'devices': {}, 'description': ''}
        with tempfile.TemporaryDirectory() as folder, patch.object(infrastructure, 'query', return_value=actual) as query:
            state = Path(folder) / 'owned.json'
            infrastructure.check(declared, state); infrastructure.record(declared, state)
            infrastructure.check(declared, state)
            self.assertTrue(all('/profiles/base' in c.args[0] for c in query.call_args_list))
            actual['config']['limits.cpu'] = '4'
            with self.assertRaisesRegex(ValueError, 'drift'): infrastructure.check(declared, state)
            adopted = copy.deepcopy(declared); adopted['profiles'][0]['config']['limits.cpu'] = '4'
            infrastructure.check(adopted, state)
            actual['config']['user.extra'] = 'native'
            with self.assertRaisesRegex(ValueError, 'drift'): infrastructure.check(adopted, state)

    def test_first_upgrade_detects_existing_conflict_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(infrastructure, 'query', return_value={'config': {'limits.cpu': '8'}}):
            with self.assertRaisesRegex(ValueError, 'drift'):
                infrastructure.check({'profiles': [{'name': 'base', 'config': {'limits.cpu': '2'}}]}, Path(folder) / 'none')


class HelperRunnerTests(unittest.TestCase):
    def test_version_is_read_from_candidate_not_the_current_installed_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(upgrades.version(root), 'unknown')
            (root / 'VERSION').write_text('0.2.0-rc1\n')
            self.assertEqual(upgrades.version(root), '0.2.0-rc1')

    def test_release_selection_resolves_annotated_tags_to_immutable_commits(self):
        data = [{'tag_name': 'v0.2.0', 'name': 'Candidate', 'draft': False},
                {'tag_name': 'v0.3.0', 'draft': True}]
        refs = 'a'*40 + '\trefs/tags/v0.2.0\n' + 'b'*40 + '\trefs/tags/v0.2.0^{}\n' + 'c'*40 + '\trefs/tags/v0.3.0\n'
        with patch.object(upgrades.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(data).encode())), \
                patch.object(upgrades.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=refs)):
            result = upgrades.releases()
        self.assertEqual(result, [{'name': 'Candidate', 'tag': 'v0.2.0', 'revision': 'b'*40}])

    def test_private_client_and_both_origins_are_pinned_without_inherited_overrides(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'INCUS_REMOTE': 'danger', 'INCUS_PROJECT': 'wrong', '_INCUS_INSIDE_CONTAINER': '1', 'LXC_PLATFORM': 'pve', 'COMMUNITY_SCRIPTS_URL': 'https://bad'}):
            env = helper.environment('lab', Path(folder))
            self.assertNotIn('INCUS_REMOTE', env); self.assertEqual(env['LXC_PLATFORM'], 'incus')
            self.assertEqual(env['INCUS_PROJECT'], 'lab'); self.assertNotIn('_INCUS_INSIDE_CONTAINER', env)
            for key, source in [('COMMUNITY_SCRIPTS_CORE_URL', 'core'), ('COMMUNITY_SCRIPTS_URL', 'scripts')]:
                self.assertTrue(env[key].endswith(helper.pins()[source]['revision']))
            config = (Path(env['INCUS_CONF']) / 'config.yml').read_text()
            self.assertIn('project: lab', config); self.assertIn('addr: unix://', config)
            self.assertIn('protocol: simplestreams', config)

    def test_unqualified_upstream_entries_never_appear_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'ct').mkdir()
            for name in ('debian', 'adguard', 'unqualified'): (root / 'ct' / (name + '.sh')).write_text('#!/bin/bash')
            with patch.object(helper, 'SCRIPTS', root):
                self.assertEqual(len(helper.catalog()), 3)
                self.assertTrue(all(not item['verifiedOnNixlab'] for item in helper.catalog()))

    def test_remote_project_and_non_tty_install_are_rejected_before_commands(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError): helper.environment('remote:lab', Path(folder))
        with patch.object(helper.os, 'geteuid', return_value=0), patch.object(helper, 'catalog', return_value=[{'id': 'debian'}]), \
                patch.object(sys, 'argv', ['nixlab-helpers', 'run', 'debian']), patch.object(sys.stdin, 'isatty', return_value=False), \
                patch.object(helper, 'doctor') as doctor, self.assertRaises(SystemExit):
            helper.main()
        doctor.assert_not_called()


if __name__ == '__main__': unittest.main()
