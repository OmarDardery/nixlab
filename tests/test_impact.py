"""Impact review must identify consumers and refuse stale or destructive activation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts'), str(BASE / 'tests')]
import impact
import infrastructure
import reconcile
import runtime as rt
import worker
from controller import Manager
from main import config_cli
from test_setup import settings

PROFILE = 'profiles:lab/base'
POOL = 'storage_pools:default/guests'


def projection():
    cfg = settings(); cfg['ready'] = True
    return {'settings': cfg, 'disks': {}, 'instances': [], 'preseed': {
        'profiles': [{'name': 'base', 'project': 'lab', 'config': {'limits.cpu': '2'},
                      'devices': {'root': {'type': 'disk', 'pool': 'guests', 'path': '/'}}}],
        'storage_pools': [{'name': 'guests', 'driver': 'dir', 'config': {'source': '/srv/nixlab/ssd/incus'}}]}}


def guest(name='one', managed=False):
    spec = {'name': name, 'project': 'lab', 'type': 'container', 'profiles': ['base'],
            'config': {'limits.memory': '1GiB'}, 'devices': {}}
    actual = copy.deepcopy(spec)
    actual.update(status='Running', expanded_devices={'root': {'type': 'disk', 'pool': 'guests', 'path': '/'}})
    actual['config']['environment.PRIVATE'] = 'DO-NOT-EXPORT'
    if managed:
        actual['config'][reconcile.OWNER] = 'true'
        actual['config'][reconcile.LAST_APPLIED] = reconcile.encode_state(spec)
    return spec, actual


def snapshot():
    desired = projection()
    values = impact.declarations(desired['preseed'])
    return {'state': 'observed', 'system': '/nix/store/current', 'settingsDigest': 'settings',
            'owned': {'objects': copy.deepcopy(values), 'declarations': copy.deepcopy(values)},
            'objects': {key: {'value': copy.deepcopy(value), 'etag': '"old"'} for key, value in values.items()},
            'resources': copy.deepcopy(values), 'projects': {'lab': {'features.profiles': 'true'}},
            'guests': [guest('one')[1], guest('two')[1]]}


class PlanningTests(unittest.TestCase):
    def test_shared_profile_lists_every_native_consumer_without_adoption(self):
        before, after, runtime = projection(), projection(), snapshot()
        after['preseed']['profiles'][0]['config']['limits.cpu'] = '4'
        original = copy.deepcopy(runtime)
        plan = impact.make_plan(before, after, runtime)
        row = next(c for c in plan['changes'] if c['identity'] == PROFILE)
        self.assertEqual(row['affected'], ['lab/one', 'lab/two'])
        self.assertEqual(row['kind'], 'restart-required')
        self.assertIn('config.limits.cpu', row['fields'])
        self.assertFalse(plan['blocked'])
        self.assertTrue(all('Incus' in owner['owner'] for owner in plan['ownership'] if owner['identity'].startswith('instance:')))
        self.assertEqual(runtime, original)
        self.assertNotIn('DO-NOT-EXPORT', json.dumps(plan))

    def test_backing_mount_change_blocks_dependents(self):
        for source in ('/srv/nixlab/ssd/incus', '/srv/nixlab/ssd'):
            with self.subTest(source=source):
                before, after, runtime = projection(), projection(), snapshot()
                for value in (before['preseed']['storage_pools'][0], after['preseed']['storage_pools'][0],
                              runtime['objects'][POOL]['value'], runtime['resources'][POOL],
                              runtime['owned']['objects'][POOL], runtime['owned']['declarations'][POOL]):
                    value['config']['source'] = source
                mount = {'name': 'ssd', 'uuid': 'old-uuid', 'path': '/srv/nixlab/ssd', 'fsType': 'ext4', 'role': 'guests', 'required': True}
                before['settings']['mounts'] = [mount]
                before['settings']['backup']['enable'] = True
                after['settings']['mounts'] = [dict(mount, uuid='new-uuid')]
                plan = impact.make_plan(before, after, runtime)
                row = next(c for c in plan['changes'] if c['scope'] == 'mount')
                self.assertTrue(row['blocked'])
                self.assertEqual(row['kind'], 'migration-required')
                self.assertTrue({POOL, 'lab/one', 'lab/two', 'incus.service', 'incus-preseed.service',
                                 'nixlab-instances.service', 'restic-backups-nixlab.service', 'nixlab-storage.target'}.issubset(row['affected']))
                self.assertNotIn('old-uuid', json.dumps(plan))

    def test_backing_pool_or_profile_device_remap_requires_migration(self):
        for kind in ('pool', 'device'):
            before, after = projection(), projection()
            if kind == 'pool': after['preseed']['storage_pools'][0]['config']['source'] = '/another/path'
            else: after['preseed']['profiles'][0]['devices']['root']['pool'] = 'other'
            plan = impact.make_plan(before, after, snapshot())
            self.assertTrue(any(c['kind'] == 'migration-required' for c in plan['changes']))
            self.assertTrue(plan['blocked'])

    def test_native_drift_uses_the_real_infrastructure_guard(self):
        runtime = snapshot(); runtime['objects'][PROFILE]['value']['config']['limits.cpu'] = '8'
        plan = impact.make_plan(projection(), projection(), runtime)
        self.assertTrue(plan['blocked'])
        self.assertTrue(any(c['kind'] == 'ownership-conflict' and c['resolution'] for c in plan['changes']))

    def test_managed_key_removal_matches_reconciler_and_preserves_unowned_values(self):
        before, after, runtime = projection(), projection(), snapshot()
        spec, actual = guest(managed=True)
        before['instances'] = [copy.deepcopy(spec)]
        spec['config'] = {}; after['instances'] = [spec]; runtime['guests'] = [actual]
        plan = impact.make_plan(before, after, runtime)
        row = next(c for c in plan['changes'] if c['scope'] == 'guest')
        self.assertIn('config.limits.memory', row['fields'])
        self.assertFalse(plan['blocked'])
        self.assertNotIn('DO-NOT-EXPORT', json.dumps(plan))
        self.assertEqual(actual['config']['environment.PRIVATE'], 'DO-NOT-EXPORT')

    def test_managed_device_removal_is_never_a_safe_key_cleanup(self):
        before, after, runtime = projection(), projection(), snapshot()
        spec, actual = guest(managed=True)
        spec['devices']['data'] = {'type': 'disk', 'pool': 'guests', 'source': 'data', 'path': '/data'}
        actual['devices'] = copy.deepcopy(spec['devices'])
        actual['config'][reconcile.LAST_APPLIED] = reconcile.encode_state(spec)
        before['instances'] = [copy.deepcopy(spec)]; spec['devices'] = {}
        after['instances'] = [spec]; runtime['guests'] = [actual]
        plan = impact.make_plan(before, after, runtime)
        self.assertTrue(plan['blocked'])
        self.assertTrue(any(c['kind'] == 'migration-required' for c in plan['changes']))

    def test_unowned_guest_collision_blocks_and_declaration_removal_never_deletes(self):
        before, after, runtime = projection(), projection(), snapshot()
        after['instances'] = [guest()[0]]
        self.assertTrue(impact.make_plan(before, after, runtime)['blocked'])
        before['instances'] = after['instances']; after['instances'] = []
        plan = impact.make_plan(before, after, runtime)
        self.assertFalse(plan['blocked'])
        self.assertTrue(any('remain untouched' in c['message'] for c in plan['changes']))

    def test_missing_inventory_is_blocked_not_assumed_empty(self):
        runtime = snapshot(); runtime['state'] = 'unavailable'; runtime['guests'] = []
        plan = impact.make_plan(projection(), projection(), runtime)
        self.assertTrue(plan['blocked'])
        self.assertTrue(any(c['kind'] == 'unknown' and c['blocked'] for c in plan['changes']))

    def test_lost_database_is_not_treated_as_a_fresh_install(self):
        before, after, runtime = projection(), projection(), snapshot()
        runtime.update(state='absent', guests=[], objects={}, resources={}, owned={'objects': {}, 'declarations': {}})
        self.assertTrue(impact.make_plan(before, after, runtime)['blocked'])
        before['expectedIncus'] = False
        before['settings']['ready'] = False
        self.assertFalse(impact.make_plan(before, after, runtime)['blocked'])

    def test_opaque_code_has_explicit_unknown_effects(self):
        plan = impact.make_plan(projection(), projection(), snapshot(), opaque=True)
        self.assertTrue(any(c['kind'] == 'unknown' for c in plan['changes']))
        self.assertTrue(any('not guest disks' in line for line in plan['boundaries']))

    def test_native_power_state_and_volatile_guest_keys_do_not_invalidate_plan(self):
        a, b = snapshot(), snapshot()
        b['guests'][0]['status'] = 'Stopped'
        b['guests'][0]['config']['volatile.last_state.power'] = 'STOPPED'
        self.assertEqual(impact.fingerprint(a), impact.fingerprint(b))
        b['guests'][0]['profiles'] = ['other']
        self.assertNotEqual(impact.fingerprint(a), impact.fingerprint(b))

    def test_profile_namespace_inheritance_is_accounted_for(self):
        runtime = snapshot()
        runtime['projects']['lab']['features.profiles'] = 'false'
        self.assertEqual(impact.consumers('profiles:default/base', runtime), ['lab/one', 'lab/two'])
        self.assertEqual(impact.consumers(PROFILE, runtime), ['lab/one', 'lab/two'])


class RevalidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        rt.write_json(self.directory / 'job.json', {'status': 'ready'})
        self.before, self.after, self.runtime = projection(), projection(), snapshot()
        self.after['preseed']['profiles'][0]['config']['limits.cpu'] = '4'
        with patch.object(impact, 'capture', return_value=self.runtime):
            impact.prepare(self.directory, self.before, self.after)

    def test_compatible_runtime_edit_refreshes_and_requires_another_review(self):
        fresh = copy.deepcopy(self.runtime)
        fresh['objects'][PROFILE] = {'value': copy.deepcopy(impact.declarations(self.after['preseed'])[PROFILE]), 'etag': '"new"'}
        with patch.object(impact, 'capture', return_value=fresh):
            with self.assertRaises(impact.ReviewRequired): impact.recheck(self.directory)
            plan = rt.read_json(self.directory / 'job.json')['impactPlan']
            self.assertTrue(plan['refreshedAfterDrift'])
            self.assertFalse(plan['blocked'])
            self.assertFalse(impact.recheck(self.directory)['blocked'])

    def test_incompatible_native_edit_cannot_be_accepted_on_retry(self):
        fresh = copy.deepcopy(self.runtime)
        fresh['objects'][PROFILE]['value']['config']['limits.cpu'] = '8'
        with patch.object(impact, 'capture', return_value=fresh):
            with self.assertRaises(impact.ReviewRequired): impact.recheck(self.directory)
            with self.assertRaisesRegex(ValueError, 'blockers'): impact.recheck(self.directory)

    def test_host_change_requires_rebuild_even_after_refresh(self):
        fresh = copy.deepcopy(self.runtime); fresh['system'] = '/nix/store/unreviewed'
        with patch.object(impact, 'capture', return_value=fresh):
            with self.assertRaises(impact.ReviewRequired): impact.recheck(self.directory)
            with self.assertRaisesRegex(ValueError, 'blockers'): impact.recheck(self.directory)

    def test_private_evidence_is_integrity_checked_and_never_returned_by_plan_cli(self):
        path = self.directory / 'impact.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn('DO-NOT-EXPORT', path.read_text())
        public = rt.read_json(self.directory / 'job.json')
        self.assertNotIn('DO-NOT-EXPORT', json.dumps(public))
        with patch('main.broker_request', return_value={'job': public}) as broker, patch('builtins.print') as printed:
            config_cli(['plan'])
        self.assertEqual(broker.call_args.args, ('GET', '/status'))
        self.assertEqual(json.loads(printed.call_args.args[0]), public['impactPlan'])
        bundle = rt.read_json(path); bundle['after']['settings']['hostName'] = 'tampered'; rt.write_json(path, bundle)
        with patch.object(impact, 'capture') as capture:
            with self.assertRaisesRegex(ValueError, 'evidence changed'): impact.recheck(self.directory)
            capture.assert_not_called()

    def test_controller_refuses_drift_before_spawning_activation(self):
        state = self.directory / 'state'; state.mkdir()
        manager = Manager(self.directory / 'root', state)
        job_id = 'a' * 32; directory = state / 'jobs' / job_id; directory.mkdir(parents=True)
        for name in ('job.json', 'impact.json'): (directory / name).write_bytes((self.directory / name).read_bytes())
        job = rt.read_json(directory / 'job.json'); job.update(baseDigest='same', candidateDigest='same'); rt.write_json(directory / 'job.json', job)
        rt.write_json(state / 'active.json', {'id': job_id})
        fresh = copy.deepcopy(self.runtime); fresh['objects'][PROFILE]['etag'] = '"new"'
        with patch.object(rt, 'STATE', state), patch.object(rt, 'digest_source', return_value='same'), \
             patch.object(impact, 'capture', return_value=fresh), patch.object(manager, 'spawn') as spawn:
            with self.assertRaises(impact.ReviewRequired): manager.apply(job_id)
            spawn.assert_not_called()
        self.assertEqual(rt.read_json(directory / 'job.json')['status'], 'ready')

    def test_worker_rechecks_after_queue_before_publishing_or_saving_recovery(self):
        state = self.directory / 'state'; state.mkdir()
        job_id = 'b' * 32; directory = state / 'jobs' / job_id; directory.mkdir(parents=True)
        for name in ('job.json', 'impact.json'): (directory / name).write_bytes((self.directory / name).read_bytes())
        job = rt.read_json(directory / 'job.json'); job.update(status='applying', baseDigest='same', candidateDigest='same'); rt.write_json(directory / 'job.json', job)
        fresh = copy.deepcopy(self.runtime); fresh['objects'][PROFILE]['etag'] = '"queued-edit"'
        with patch.object(rt, 'STATE', state), patch.object(rt, 'digest_source', return_value='same'), \
             patch.object(impact, 'capture', return_value=fresh), patch.object(worker, 'save_recovery') as recovery, patch.object(worker, 'run') as run:
            worker.job('apply', job_id)
        self.assertEqual(rt.read_json(directory / 'job.json')['status'], 'ready')
        recovery.assert_not_called(); run.assert_not_called()
        self.assertFalse((state / 'pending.json').exists())

    def test_legacy_candidates_cannot_skip_new_gate(self):
        (self.directory / 'impact.json').unlink()
        with self.assertRaisesRegex(ValueError, 'no impact plan'): impact.recheck(self.directory)


class ObservationTests(unittest.TestCase):
    def test_api_is_get_only_and_rejects_oversized_responses(self):
        conn = Mock(); conn.getresponse.return_value.status = 200
        conn.getresponse.return_value.read1.return_value = b'x' * (impact.MAX_BYTES + 1)
        with patch.object(reconcile, 'LocalConnection', return_value=conn):
            with self.assertRaisesRegex(ValueError, 'size limit'): impact.api('/1.0', time.monotonic() + 10)
        self.assertEqual(conn.request.call_args.args, ('GET', '/1.0'))
        conn.close.assert_called_once()

    def test_unit_diff_uses_hashes_not_secret_bearing_unit_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'etc/systemd/system'; root.mkdir(parents=True)
            (root / 'example.service').write_text('Environment=SECRET=private')
            before = impact.units(Path(folder))
            (root / 'example.service').write_text('Environment=SECRET=changed')
            self.assertNotEqual(before, impact.units(Path(folder)))
            self.assertNotIn('SECRET', json.dumps(before))


if __name__ == '__main__': unittest.main()
