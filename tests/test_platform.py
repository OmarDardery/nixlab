import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'scripts'))

def module(name):
    spec = importlib.util.spec_from_file_location(name, BASE / 'scripts' / (name + '.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

reconcile = module('reconcile')
backup = module('backup')
preflight = module('preflight')

class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.current = {'type': 'container', 'profiles': ['base'], 'config': {
            reconcile.OWNER: 'true', 'user.note': 'keep'}, 'devices': {}}
        self.spec = {'name': 'guest', 'type': 'container', 'profiles': ['base'], 'config': {'limits.memory': '2GiB'}}

    def test_unowned_collision_is_refused(self):
        self.current['config'].pop(reconcile.OWNER)
        with self.assertRaises(ValueError):
            reconcile.plan(self.current, self.spec)

    def test_reconcile_cannot_remove_ownership_marker(self):
        self.spec['config'][reconcile.OWNER] = 'false'
        with self.assertRaisesRegex(ValueError, 'ownership marker'):
            reconcile.plan(self.current, self.spec)
        self.assertEqual(self.current['config'][reconcile.OWNER], 'true')

    def test_invalid_owner_is_rejected_before_creating_instance(self):
        self.spec.update(project='lab', image='images:example')
        self.spec['config'][reconcile.OWNER] = 'false'
        with patch.object(reconcile, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'ownership marker'):
                reconcile.reconcile(self.spec)
        run.assert_not_called()

    def test_idempotent_resource_update_preserves_runtime_keys(self):
        updated = reconcile.plan(self.current, self.spec)
        self.assertEqual(updated['config']['user.note'], 'keep')
        updated['type'] = 'container'
        self.assertIsNone(reconcile.plan(updated, self.spec))

    def test_reconcile_rerun_does_not_mutate_or_restart(self):
        self.current['name'] = 'guest'
        self.current['status'] = 'Running'
        self.current['config']['limits.memory'] = '2GiB'
        self.spec['project'] = 'lab'
        calls = []
        def fake(*args, data=None):
            calls.append(args)
            if args[0] == 'list':
                return json.dumps([self.current])
            if args[0] == 'query':
                return json.dumps(self.current)
            raise AssertionError('Unnecessary mutation: ' + str(args))
        self.current['config'][reconcile.LAST_APPLIED] = reconcile.encode_state(self.spec)
        with patch.object(reconcile, 'read_instance', return_value=(self.current, 'etag')), patch.object(reconcile, 'run', side_effect=fake):
            reconcile.reconcile(self.spec)
        self.assertFalse(any(c[0] in ('init', 'start', 'config', 'delete') for c in calls))

    def test_existing_stopped_guest_stays_stopped(self):
        self.current.update(name='guest', status='Stopped')
        self.current['config']['limits.memory'] = '2GiB'
        self.spec['project'] = 'lab'
        def fake(*args, data=None):
            if args[0] == 'list': return json.dumps([self.current])
            if args[0] == 'query': return json.dumps(self.current)
            raise AssertionError('Unexpected mutation: ' + str(args))
        self.current['config'][reconcile.LAST_APPLIED] = reconcile.encode_state(self.spec)
        with patch.object(reconcile, 'read_instance', return_value=(self.current, 'etag')), patch.object(reconcile, 'run', side_effect=fake):
            reconcile.reconcile(self.spec)

    def test_interrupted_initialization_is_resumed(self):
        self.current.update(name='guest', status='Stopped')
        self.current['config'].update({'limits.memory': '2GiB', reconcile.INITIALIZING: 'true'})
        self.spec['project'] = 'lab'
        calls = []
        def fake(*args, data=None):
            calls.append(args)
            if args[0] == 'list': return json.dumps([self.current])
            if args[0] == 'query': return json.dumps(self.current)
            return ''
        self.current['config'][reconcile.LAST_APPLIED] = reconcile.encode_state(self.spec)
        with patch.object(reconcile, 'read_instance', return_value=(self.current, 'etag')), patch.object(reconcile, 'run', side_effect=fake):
            reconcile.reconcile(self.spec)
        self.assertTrue(any(c[0] == 'start' for c in calls))
        self.assertTrue(any(c[:2] == ('config', 'unset') for c in calls))

    def test_initialization_marker_is_reserved(self):
        self.spec['config'][reconcile.INITIALIZING] = 'true'
        self.spec['project'] = 'lab'
        with patch.object(reconcile, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'reserved'):
                reconcile.reconcile(self.spec)
        run.assert_not_called()

    def test_list_failure_does_not_create_a_replacement(self):
        self.spec['project'] = 'lab'
        with patch.object(reconcile, 'run', side_effect=RuntimeError('API unavailable')) as run:
            with self.assertRaises(RuntimeError):
                reconcile.reconcile(self.spec)
        self.assertEqual(run.call_count, 1)

    def test_disabled_apps_is_not_created_when_absent(self):
        spec = {'name': 'apps', 'project': 'lab', 'type': 'virtual-machine', 'manageExistingOnly': True, 'start': False, 'stop': True}
        with patch.object(reconcile, 'run', return_value='[]') as run:
            reconcile.reconcile(spec)
            self.assertEqual(run.call_count, 1)

    def test_type_change_requires_explicit_migration(self):
        self.spec['type'] = 'virtual-machine'
        with self.assertRaises(ValueError):
            reconcile.plan(self.current, self.spec)

class FieldOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.spec={'name':'guest','type':'container','config':{'limits.memory':'2GiB','user.old':'owned'},'devices':{}}
        self.current={'type':'container','config':{reconcile.OWNER:'true','user.note':'keep'},'devices':{},'profiles':['base']}
        self.current=reconcile.plan(self.current,self.spec) | {'type':'container'}

    def test_removed_owned_key_is_pruned_but_runtime_key_remains(self):
        del self.spec['config']['user.old']
        changed=reconcile.plan(self.current,self.spec)
        self.assertNotIn('user.old',changed['config'])
        self.assertEqual(changed['config']['user.note'],'keep')
        self.assertIsNone(reconcile.plan(changed | {'type':'container'},self.spec))

    def test_shutdown_sentinel_preserves_owned_devices_and_limits(self):
        self.spec['devices']={'root':{'type':'disk','pool':'guests','path':'/'}}
        self.current=reconcile.plan(self.current,self.spec) | {'type':'container'}
        sentinel={'name':'guest','type':'container','manageExistingOnly':True,'config':{'boot.autostart':'false'}}
        changed=reconcile.plan(self.current,sentinel)
        self.assertEqual(changed['devices'],self.current['devices'])
        self.assertEqual(changed['config']['limits.memory'],'2GiB')
        self.assertEqual(changed['config']['boot.autostart'],'false')

    def test_owned_runtime_drift_is_refused(self):
        self.current['config']['limits.memory']='4GiB'
        with self.assertRaisesRegex(ValueError,'Runtime drift'):
            reconcile.plan(self.current,self.spec)

    def test_disk_removal_requires_migration(self):
        self.spec['devices']={'data':{'type':'disk','pool':'guests','source':'volume','path':'/data'}}
        self.current=reconcile.plan(self.current,self.spec) | {'type':'container'}
        self.spec['devices']={}
        with self.assertRaisesRegex(ValueError,'removal requires'):
            reconcile.plan(self.current,self.spec)

    def test_etag_is_forwarded_and_api_failure_is_not_hidden(self):
        with patch.object(reconcile,'api',return_value=({'type':'sync'},None)) as api:
            reconcile.write_instance('lab','guest',{'config':{}},'"expected"')
            self.assertEqual(api.call_args.args[-1],'"expected"')
        with patch.object(reconcile,'api',side_effect=RuntimeError('HTTP 412')):
            with self.assertRaisesRegex(RuntimeError,'412'):
                reconcile.write_instance('lab','guest',{},'old')

    def test_missing_etag_refuses_updates(self):
        with patch.object(reconcile,'api',return_value=({'metadata':self.current},None)):
            with self.assertRaisesRegex(RuntimeError,'ETag'):
                reconcile.read_instance('lab','guest')

class CliTests(unittest.TestCase):
    def test_argument_boundaries_and_guest_exit_status(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            fake = p / 'incus'
            fake.write_text('#!/usr/bin/env python3\nimport os,sys,json\nopen(os.environ["ARGS"],"w").write(json.dumps(sys.argv[1:]))\nsys.exit(23)\n')
            fake.chmod(0o755)
            cfg = p / 'config.json'
            cfg.write_text(json.dumps({'remote': 'labserver', 'project': 'lab', 'aliases': {'105': 'klipper'}}))
            env = os.environ | {'PATH': d + ':' + os.environ['PATH'], 'HOMELAB_CONFIG': str(cfg), 'ARGS': str(p / 'args')}
            proc = subprocess.run(['python3', str(BASE / 'scripts/homelab.py'), 'exec', '105', '--',
                                   'printf', '%s', 'one argument; $(never-run)'], env=env)
            self.assertEqual(proc.returncode, 23)
            args = json.loads((p / 'args').read_text())
            self.assertEqual(args[-3:], ['printf', '%s', 'one argument; $(never-run)'])
            self.assertIn('labserver:klipper', args)

class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patches = [patch.object(backup, 'ROOT', self.root), patch.object(backup, 'PENDING', self.root / 'pending.json')]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def test_failed_export_preserves_old_archive(self):
        archive = self.root / 'existing.tar.gz'
        archive.write_bytes(b'good old backup')
        with patch.object(backup, 'incus', side_effect=RuntimeError('disk full')):
            with self.assertRaises(RuntimeError):
                backup.export(['export', 'apps'], archive.name)
        self.assertEqual(archive.read_bytes(), b'good old backup')
        self.assertEqual(list(self.root.glob('.export-*')), [])

    def test_failed_backup_recovers_only_originally_running_guests(self):
        (self.root/'inventory.json').write_text('{"previous":"retain"}')
        states = {'apps': 'Running', 'off': 'Stopped'}
        calls = []
        def fake(*args):
            calls.append(args)
            if args[:2] in (('profile','list'),('network','list')) or args[:3] == ('config','trust','list'):
                return '[]'
            if args[:2] == ('project', 'list'):
                return json.dumps([{'name': 'lab'}])
            if args[:2] == ('storage', 'list'):
                return '[]'
            if args[0] == 'list':
                return json.dumps([{'name': n, 'status': s} for n,s in states.items()])
            if args[0] == 'stop':
                states[args[1]] = 'Stopped'; return ''
            if args[0] == 'start':
                states[args[1]] = 'Running'; return ''
            if args[0] == 'query':
                if args[1] == '/1.0': return '{"config":{}}'
                return json.dumps({'status': states[args[1].split('/')[3]]})
            if args[0] == 'export':
                raise RuntimeError('export failed')
            raise AssertionError(args)
        with patch.object(backup, 'incus', side_effect=fake), patch.dict(os.environ, {'NIXLAB_DEMO_BACKUP': '0'}):
            with self.assertRaises(RuntimeError):
                backup.backup()
        self.assertEqual(states, {'apps': 'Running', 'off': 'Stopped'})
        self.assertEqual(json.loads(backup.PENDING.read_text()), [])
        self.assertFalse(any(c[:2] == ('start', 'off') for c in calls))
        self.assertEqual(json.loads((self.root/'inventory.json').read_text()),{'previous':'retain'})

    def test_successful_inventory_records_mutable_profiles_networks_and_trust(self):
        def fake(*args):
            if args[:2] == ('project','list'): return '[{"name":"lab"}]'
            if args[:2] == ('profile','list'): return '[{"name":"custom","devices":{"data":{"type":"disk"}}}]'
            if args[:2] == ('network','list'): return '[{"name":"custom-network"}]'
            if args[:3] == ('config','trust','list'): return '[{"name":"client-identity"}]'
            if args[0] == 'query': return '{"config":{"core.https_address":"192.0.2.10:8443"}}'
            return '[]'
        with patch.object(backup,'incus',side_effect=fake),patch.dict(os.environ,{'NIXLAB_DEMO_BACKUP':'0'}):backup.backup()
        generation=json.loads((self.root/'latest.json').read_text())['generation']
        data=json.loads((self.root/'generations'/generation/'inventory.json').read_text())
        self.assertEqual(data['profiles']['lab'][0]['name'],'custom')
        self.assertEqual(data['networks']['lab'][0]['name'],'custom-network')
        self.assertEqual(data['trustedCertificates'][0]['name'],'client-identity')
        self.assertEqual(data['archiveSHA256'],{})

class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.site = {'management': {'interface': 'eth0', 'address': '192.0.2.10', 'prefixLength': 24,
            'trustedCIDRs': ['192.0.2.0/24'], 'configure': False},
            'guests': {'cidr': '10.77.0.0/24', 'gatewayCIDR': '10.77.0.1/24',
                'appsAddress': '10.77.0.10', 'dhcpRange': '10.77.0.100-10.77.0.199'},
            'storage': {'driver': 'dir', 'source': '/var/lib/incus/storage-pools/guests'}}

    def test_documentation_fixture_valid(self):
        preflight.validate(self.site)

    def test_overlapping_management_subnet_refused(self):
        self.site['management']['address'] = '10.77.0.2'
        with self.assertRaises(ValueError): preflight.validate(self.site)

    def test_static_ip_inside_dhcp_range_refused(self):
        self.site['guests']['appsAddress'] = '10.77.0.110'
        with self.assertRaises(ValueError): preflight.validate(self.site)

    def test_duplicate_guest_static_address_refused(self):
        self.site['instances'] = [{'name': 'duplicate', 'project': 'lab', 'devices': {
            'eth0': {'network': 'incusbr0', 'ipv4.address': '10.77.0.10'}}}]
        with self.assertRaises(ValueError): preflight.validate(self.site)

    def test_zfs_raw_device_refused(self):
        self.site['storage'] = {'driver': 'zfs', 'source': '/dev/sda', 'confirmedExistingZfsDataset': True, 'hostId': 'abcdef12'}
        with self.assertRaises(ValueError): preflight.validate(self.site)

if __name__ == '__main__':
    unittest.main()
