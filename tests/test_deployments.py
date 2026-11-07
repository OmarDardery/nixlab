"""Rollout recovery, ownership and health gates using a persistent transaction journal."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import accounts
import deployments as d
import guest_deploy as gd
import runtime as rt
from main import deploy_cli

OLD = '/nix/store/' + '1' * 32 + '-nixos-system-old'
NEW = '/nix/store/' + '2' * 32 + '-nixos-system-new'
PROBE = '/nix/store/' + '3' * 32 + '-health'
OTHER = '/nix/store/' + '4' * 32 + '-native'
ID = 'a' * 32


def spec(name='apps', deps=None):
    return {'project': 'lab', 'name': name, 'system': NEW, 'probe': PROBE, 'dependsOn': deps or [],
            'restartPolicy': 'defer', 'maxAttempts': 3, 'healthTimeout': 1, 'rollbackOnFailure': True,
            'healthUnits': ['example.service'], 'httpChecks': 0}


class Fake:
    def __init__(self, name):
        self.current = {'uuid': 'uuid-' + name, 'fingerprint': 'private-' + name,
                        'profile': OLD, 'active': OLD, 'type': 'container'}
        self.calls, self.fail_transfer, self.fail_activation = [], False, False
        self.good, self.reboot_needed, self.interrupt = True, False, False
        self.fail_rollback = False

    def inspect(self): return copy.deepcopy(self.current)
    def transfer(self, *roots, **kwargs):
        self.calls.append(('transfer', roots))
        if self.interrupt:
            self.interrupt = False
            raise SystemExit('simulated worker loss')
        if self.fail_transfer: raise gd.DeploymentError('interrupted transfer')
    def retain(self, *args): self.calls.append(('retain', args))
    def activate(self, closure):
        self.calls.append(('activate', closure)); self.current['profile'] = closure
        if self.fail_activation and closure == NEW: raise gd.DeploymentError('activation failure')
        if self.fail_rollback and closure == OLD: raise gd.DeploymentError('rollback failure')
        self.current['active'] = closure
    def reboot_required(self, current): return self.reboot_needed
    def reboot(self): self.calls.append(('reboot',)); self.reboot_needed = False
    def healthy(self, *args, **kwargs): return self.good


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'state'; self.root = Path(self.temp.name) / 'source'
        self.root.mkdir(); (self.root / 'site.nix').write_text('{}')
        self.directory = self.state / 'jobs' / ID; self.directory.mkdir(parents=True)
        (self.directory / 'source').mkdir(); (self.directory / 'source/site.nix').write_text('{}')
        rt.write_json(self.directory / 'source/.nixlab-files.json', ['site.nix'])
        self.system = Path(self.temp.name) / 'host'; self.system.mkdir()
        (self.directory / 'system').symlink_to(self.system)
        self.job = {'id': ID, 'status': 'confirmed', 'baseDigest': 'original',
                    'candidateDigest': rt.digest_source(self.directory / 'source')}
        rt.write_json(self.directory / 'job.json', self.job)
        rt.write_json(self.state / 'active.json', {'id': ID})
        self.fakes = {key: Fake(key) for key in ('apps', 'worker')}
        for p in (patch.object(rt, 'STATE', self.state), patch.object(d, 'INCUS_LOCK', Path(self.temp.name) / 'incus.lock'),
                  patch.object(d, 'active_system', return_value=str(self.system)),
                  patch.object(d, 'Transport', side_effect=lambda project, name: self.fakes[name])):
            p.start(); self.addCleanup(p.stop)
        self.manager = d.Deployments(self.root, self.state)
        self.targets = {'apps': spec(), 'worker': spec('worker', ['apps'])}
        d.prepare(self.directory, self.targets)

    def queue(self, resume=False):
        with patch.object(d.subprocess, 'run', side_effect=lambda args, **kwargs: subprocess.CompletedProcess(args, 1 if args[0] == 'systemctl' else 0)):
            return self.manager.submit(ID, resume=resume)

    def report(self): return rt.read_json(self.directory / 'deployment.json')

    def test_dependency_order_updates_two_existing_guests_and_retains_generations(self):
        self.queue(); report = self.manager.run(ID)
        self.assertEqual(report['status'], 'healthy')
        self.assertEqual(list(report['targets']), ['apps', 'worker'])
        for fake in self.fakes.values():
            self.assertEqual(fake.current['active'], NEW)
            self.assertTrue(any(call[0] == 'retain' for call in fake.calls))
            self.assertEqual([call for call in fake.calls if call[0] == 'activate'], [('activate', NEW)])
        self.assertFalse((self.state / 'deployment-active.json').exists())
        self.assertNotIn('precondition', self.manager.status(ID)['targets']['apps'])

    def test_failed_dependency_blocks_its_consumer_without_transferring(self):
        self.fakes['apps'].fail_transfer = True
        self.queue(); report = self.manager.run(ID)
        self.assertEqual(report['targets']['apps']['phase'], 'failed')
        self.assertEqual(report['targets']['worker']['phase'], 'blocked')
        self.assertEqual(self.fakes['worker'].calls, [])
        self.fakes['apps'].fail_transfer = False
        self.queue(resume=True)
        self.assertEqual(self.manager.run(ID)['status'], 'healthy')

    def test_interrupted_transfer_survives_new_controller_and_resumes_without_recreating(self):
        self.fakes['apps'].interrupt = True
        self.queue()
        with self.assertRaises(SystemExit): self.manager.run(ID)
        self.assertEqual(self.report()['targets']['apps']['phase'], 'transferring')
        record = self.report(); record['updatedAt'] = time.time() - 60
        rt.write_json(self.directory / 'deployment.json', record)
        with patch.object(d.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            self.assertEqual(self.manager.status(ID)['status'], 'interrupted')
        self.manager = d.Deployments(self.root, self.state)
        self.queue(resume=True)
        self.assertEqual(self.manager.run(ID)['status'], 'healthy')
        self.assertEqual(self.report()['targets']['apps']['attempts'], 2)

    def test_health_failure_restores_previous_os_and_blocks_dependents(self):
        self.fakes['apps'].good = False
        self.queue(); report = self.manager.run(ID)
        self.assertEqual(report['targets']['apps']['phase'], 'rolled-back')
        self.assertEqual(self.fakes['apps'].current['active'], OLD)
        self.assertEqual(report['targets']['worker']['phase'], 'blocked')
        self.assertIn('databases', report['boundary'])

    def test_activation_failure_restores_profile_and_active_generation(self):
        self.fakes['apps'].fail_activation = True
        self.queue(); report = self.manager.run(ID)
        self.assertEqual(report['targets']['apps']['phase'], 'rolled-back')
        self.assertEqual(self.fakes['apps'].current['profile'], OLD)
        self.assertEqual(self.fakes['apps'].current['active'], OLD)

    def test_retry_budget_persists_across_workers(self):
        self.targets['apps']['maxAttempts'] = 1; d.prepare(self.directory, self.targets)
        self.fakes['apps'].fail_transfer = True
        self.queue(); self.manager.run(ID)
        self.fakes['apps'].fail_transfer = False
        self.queue(resume=True); report = self.manager.run(ID)
        self.assertEqual(report['targets']['apps']['attempts'], 1)
        self.assertIn('budget', report['targets']['apps']['error'])
        self.assertEqual(self.fakes['apps'].current['active'], OLD)

    def test_reboot_pending_blocks_dependencies_until_external_reboot_and_resume(self):
        self.fakes['apps'].reboot_needed = True
        self.queue(); report = self.manager.run(ID)
        self.assertEqual(report['targets']['apps']['phase'], 'reboot-pending')
        self.assertEqual(report['targets']['worker']['phase'], 'blocked')
        self.fakes['apps'].reboot_needed = False
        self.queue(resume=True); self.assertEqual(self.manager.run(ID)['status'], 'healthy')

    def test_explicit_reboot_policy_reboots_before_healthy(self):
        self.targets['apps']['restartPolicy'] = 'reboot'; d.prepare(self.directory, self.targets)
        self.fakes['apps'].reboot_needed = True
        self.queue(); self.assertEqual(self.manager.run(ID)['status'], 'healthy')
        self.assertIn(('reboot',), self.fakes['apps'].calls)

    def test_health_failure_after_deferred_reboot_restores_previous_generation(self):
        self.fakes['apps'].reboot_needed = True
        self.queue(); self.manager.run(ID)
        self.fakes['apps'].reboot_needed = False
        self.fakes['apps'].good = False
        self.queue(resume=True); report = self.manager.run(ID)
        self.assertEqual(report['targets']['apps']['phase'], 'rolled-back')
        self.assertEqual(self.fakes['apps'].current['active'], OLD)
        self.assertEqual(report['targets']['worker']['phase'], 'blocked')

    def test_native_generation_or_identity_change_after_queue_refuses_all_mutation(self):
        for change in ({'uuid': 'replacement'}, {'profile': OTHER}, {'fingerprint': 'native-edit'}):
            self.fakes['apps'].current = dict(Fake('apps').current)
            (self.directory / 'deployment.json').unlink(missing_ok=True)
            self.queue(); self.fakes['apps'].current.update(change)
            report = self.manager.run(ID)
            self.assertEqual(report['targets']['apps']['phase'], 'failed')
            self.assertEqual(self.fakes['apps'].calls, [])

    def test_changed_os_since_review_is_not_implicitly_adopted(self):
        self.fakes['apps'].current['profile'] = OTHER
        self.queue(); report = self.manager.run(ID)
        self.assertEqual(report['targets']['apps']['attempts'], 0)
        self.assertEqual(self.fakes['apps'].calls, [])

    def test_tampered_plan_is_rejected_before_queue(self):
        bundle = rt.read_json(self.directory / 'deployment-plan.json'); bundle['targets']['apps']['system'] = OTHER
        rt.write_json(self.directory / 'deployment-plan.json', bundle)
        with self.assertRaisesRegex(ValueError, 'evidence changed'): self.queue()
        self.assertFalse((self.directory / 'deployment.json').exists())

    def test_host_confirmation_generation_and_source_are_preconditions(self):
        for status in ('ready', 'awaiting-confirmation', 'rolled-back'):
            rt.write_json(self.directory / 'job.json', dict(self.job, status=status))
            with self.assertRaisesRegex(ValueError, 'Confirm host access'): self.queue()
        rt.write_json(self.directory / 'job.json', self.job)
        with patch.object(d, 'active_system', return_value=OTHER):
            with self.assertRaisesRegex(ValueError, 'host generation'): self.queue()
        (self.directory / 'source/site.nix').write_text('{changed=true;}')
        with self.assertRaisesRegex(ValueError, 'source changed'): self.queue()

    def test_abandon_interrupted_journal_releases_stale_lock_without_guest_changes(self):
        self.queue()
        record = self.report(); record.update(updatedAt=time.time() - 60)
        rt.write_json(self.directory / 'deployment.json', record)
        with self.assertRaisesRegex(ValueError, 'active guest'): d.check_idle(self.state)
        with patch.object(d.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)):
            self.manager.abandon(ID)
        d.check_idle(self.state)
        self.assertEqual(self.report()['status'], 'abandoned')
        self.assertEqual(self.fakes['apps'].calls, [])

    def test_plan_and_journal_are_private_and_public_metadata_contains_no_preconditions(self):
        job = rt.read_json(self.directory / 'job.json')
        self.assertIn('guestPlan', job)
        self.assertNotIn('private-apps', json.dumps(job['guestPlan']))
        self.assertEqual((self.directory / 'deployment-plan.json').stat().st_mode & 0o777, 0o600)
        self.queue()
        self.assertEqual((self.directory / 'deployment.json').stat().st_mode & 0o777, 0o600)

    def test_already_current_guest_runs_health_without_reactivation(self):
        self.fakes['apps'].current.update(profile=NEW, active=NEW)
        self.queue(); self.assertEqual(self.manager.run(ID)['status'], 'healthy')
        self.assertFalse(any(call[0] == 'activate' for call in self.fakes['apps'].calls))

    def test_probe_import_failure_on_already_current_guest_has_a_finite_budget(self):
        self.targets['apps']['maxAttempts'] = 1; d.prepare(self.directory, self.targets)
        self.fakes['apps'].current.update(profile=NEW, active=NEW)
        self.fakes['apps'].fail_transfer = True
        self.queue(); self.manager.run(ID)
        self.queue(resume=True); report = self.manager.run(ID)
        self.assertEqual(report['targets']['apps']['attempts'], 1)
        self.assertIn('budget', report['targets']['apps']['error'])
        self.assertFalse(any(call[0] == 'activate' for call in self.fakes['apps'].calls))

    def test_failed_rollback_is_completed_before_retrying_forward(self):
        self.fakes['apps'].fail_activation = self.fakes['apps'].fail_rollback = True
        self.queue(); report = self.manager.run(ID)
        self.assertEqual(report['targets']['apps']['phase'], 'rollback-failed')
        self.fakes['apps'].fail_activation = self.fakes['apps'].fail_rollback = False
        self.fakes['apps'].calls.clear()
        self.queue(resume=True); report = self.manager.run(ID)
        self.assertEqual(report['status'], 'healthy')
        activations = [call for call in self.fakes['apps'].calls if call[0] == 'activate']
        self.assertEqual(activations, [('activate', OLD), ('activate', NEW)])

    def test_exhausted_interrupted_activation_restores_previous_generation(self):
        self.queue()
        record = self.report(); state = record['targets']['apps']
        state.update(phase='activating', attempts=3, previousSystem=OLD, targetSystem=NEW)
        rt.write_json(self.directory / 'deployment.json', record)
        self.fakes['apps'].current['profile'] = NEW
        report = self.manager.run(ID)
        self.assertEqual(report['targets']['apps']['phase'], 'rolled-back')
        self.assertEqual(self.fakes['apps'].current['profile'], OLD)
        self.assertIn('budget', report['targets']['apps']['error'])

    def test_mutation_lock_and_journal_exist_before_transfer(self):
        original = self.fakes['apps'].transfer
        def inspect_lock(*roots):
            with self.assertRaises(rt.BusyError):
                with rt.locked(blocking=False): pass
            self.assertEqual(self.report()['targets']['apps']['phase'], 'transferring')
            original(*roots)
        self.fakes['apps'].transfer = inspect_lock
        self.queue(); self.assertEqual(self.manager.run(ID)['status'], 'healthy')


class ContractTests(unittest.TestCase):
    def test_dependency_cycles_unknown_targets_and_duplicate_guests_are_rejected(self):
        for targets in ({'apps': spec(deps=['missing'])}, {'apps': spec(deps=['apps'])},
                        {'apps': spec(), 'alias': spec()}):
            with self.assertRaises(ValueError): d.validate(targets)

    def test_transport_rejects_remote_names_and_non_store_paths(self):
        for value in ('remote:guest', '../guest', '-guest', 'one/two'):
            with self.assertRaises(ValueError): gd.Transport('lab', value)
        for value in ('/tmp/system', NEW + '/bin/sh', NEW + ';touch /tmp/x'):
            with self.assertRaises(ValueError): gd.store_path(value)

    def test_native_and_non_nixos_guests_are_observed_only(self):
        base = {'status': 'Running', 'type': 'container', 'config': {'volatile.uuid': 'id'}}
        with patch.object(gd.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(base), '')):
            with self.assertRaisesRegex(ValueError, 'Native-owned'): gd.Transport('lab', 'native').inspect()
        base['config'][gd.OWNER] = 'true'
        with patch.object(gd.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(base), '')), \
             patch.object(gd.Transport, 'execute', return_value=subprocess.CompletedProcess([], 1, '', '')):
            with self.assertRaisesRegex(ValueError, 'Non-NixOS'): gd.Transport('lab', 'native').inspect()

    def test_switch_timeout_and_lock_live_in_the_guest_after_client_loss(self):
        transport = gd.Transport('lab', 'apps')
        with patch.object(transport, 'execute') as execute:
            transport.activate(NEW)
        args = execute.call_args.args
        self.assertEqual(args[:7], ('timeout', '--kill-after=10s', '600', 'flock', '--exclusive', '--timeout', '300'))
        self.assertIn('/run/lock/nixlab-guest-deployment.lock', args)
        self.assertEqual(args[-1], NEW)
        self.assertIn('nix-env', args[-3])
        self.assertIn('switch-to-configuration', args[-3])
        self.assertGreater(execute.call_args.kwargs['timeout'], int(args[2]))

    def test_transfer_timeout_closes_parent_pipe_and_terminates_both_children(self):
        exporter, receiver = Mock(), Mock()
        exporter.poll.return_value = receiver.poll.return_value = None
        receiver.wait.side_effect = [subprocess.TimeoutExpired('incus', 1), 0]
        exporter.wait.return_value = 0
        with patch.object(gd.subprocess, 'check_output', return_value=NEW + '\n'), \
             patch.object(gd.subprocess, 'Popen', side_effect=[exporter, receiver]):
            with self.assertRaisesRegex(ValueError, 'interrupted or timed out'): gd.Transport('lab', 'apps').transfer(NEW, timeout=1)
        exporter.stdout.close.assert_called_once()
        exporter.terminate.assert_called_once(); receiver.terminate.assert_called_once()

    def test_guest_deployment_is_admin_only_and_cli_uses_the_broker(self):
        for role in ('operator', 'viewer'):
            with self.assertRaises(accounts.AuthorizationError): accounts.authorize({'role': role}, '/api/deploy-guests', {'id': ID})
        with patch('main.broker_request', return_value={'id': ID}) as broker, patch('builtins.print'):
            deploy_cli(['resume', ID])
        self.assertEqual(broker.call_args.args, ('POST', '/deploy-guests', {'id': ID, 'resume': True, 'abandon': False}))
