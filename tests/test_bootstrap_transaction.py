"""Initial wizard shares durable review/build/activate/recovery, without guests."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ('installer', 'admin', 'scripts')]
import bootstrap
from controller import Manager
import diagnostics
import model
import runtime as rt
import worker
from test_direct_install import initial, inventory


class BootstrapTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'
        self.state = Path(self.temp.name) / 'state'
        self.settings = initial()
        # A genuine first wizard has not selected any guest addresses yet.
        for name in ('cidr', 'gatewayCIDR', 'dhcpRange', 'appsAddress'):
            self.settings['guests'][name] = ''
        rt.atomic(self.root / 'flake.nix', '{}')
        rt.write_json(self.root / '.nixlab-files.json', ['flake.nix'])
        rt.write_json(self.root / 'machine/format.json', {'version': 1})
        rt.write_json(self.root / 'machine/settings.json', self.settings)
        self.manager = Manager(self.root, self.state)
        for item in (patch.object(rt, 'ROOT', self.root), patch.object(rt, 'STATE', self.state),
                     patch('controller.read_settings', side_effect=lambda root:
                           rt.read_json(rt.machine_path(root, 'settings'))),
                     patch('controller.inventory', return_value=inventory()),
                     patch.object(self.manager, 'spawn')):
            item.start()
            self.addCleanup(item.stop)

    def review(self):
        result = self.manager.review({}, host_role='bootstrap', principal=bootstrap.PRINCIPAL)
        return self.manager.job_path(result['id'])

    def build(self, directory, effective=None):
        projection = {'settings': effective or copy.deepcopy(self.settings),
                      'desiredSettings': self.settings, 'disks': {}, 'preseed': {}, 'instances': [], 'deployments': {}}
        with patch.object(worker, 'run') as run, patch('model.inventory', return_value=inventory()), \
                patch.object(worker.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(projection))) as evaluate, \
                patch('impact.prepare'), patch('deployments.prepare'):
            worker.build(directory)
        return run, evaluate

    def test_review_keeps_unconfigured_intent_and_has_no_guest_or_secret_requirement(self):
        directory = self.review()
        job = rt.read_json(directory / 'job.json')
        self.assertEqual(job['hostRole'], 'bootstrap')
        self.assertFalse(job['settings']['ready'])
        self.assertEqual(job['settings']['guests']['cidr'], '')
        self.assertFalse((directory / 'secrets.json').exists())
        self.assertEqual(rt.read_json(rt.machine_path(self.root, 'settings')), self.settings)
        self.manager.spawn.assert_called_once_with('build', directory.name)

    def test_review_refuses_unknown_targets_and_other_entrypoints(self):
        for options in ({'host_role': 'arbitrary'}, {'host_role': 'bootstrap'},
                        {'host_role': 'bootstrap', 'principal': bootstrap.PRINCIPAL, 'update_inputs': True},
                        {'host_role': 'bootstrap', 'principal': bootstrap.PRINCIPAL, 'secret_values': {'x': 'private'}}):
            with self.subTest(options=sorted(options)), self.assertRaises(ValueError):
                self.manager.review({}, **options)
        self.assertEqual(list((self.state / 'jobs').iterdir()), [])

    def test_review_refuses_settings_changes_and_configured_effective_hosts(self):
        with self.assertRaisesRegex(ValueError, 'cannot change settings'):
            self.manager.review({'hostName': 'new'}, host_role='bootstrap', principal=bootstrap.PRINCIPAL)
        with patch.object(self.manager, 'effective_settings', return_value={'ready': True}):
            with self.assertRaisesRegex(ValueError, 'already configured'):
                self.review()
        self.manager.spawn.assert_not_called()

    def test_build_targets_bootstrap_and_uses_no_hypervisor_resource_projection(self):
        directory = self.review()
        run, evaluate = self.build(directory)
        self.assertIn('#nixosConfigurations.bootstrap.config.system.build.toplevel', run.call_args_list[0].args[0][4])
        args = evaluate.call_args.args[0]
        self.assertIn('path:' + str(directory / 'source') + '#nixosConfigurations.bootstrap.config', args)
        self.assertNotIn('c.virtualisation.incus', args[-1])
        job = rt.read_json(directory / 'job.json')
        self.assertEqual(job['status'], 'ready')
        self.assertFalse(job['effectiveSettings']['ready'])

    def test_build_refuses_a_source_that_marks_bootstrap_configured(self):
        directory = self.review()
        effective = dict(self.settings, ready=True)
        with self.assertRaisesRegex(ValueError, 'marks the host configured'):
            self.build(directory, effective)
        self.assertNotEqual(rt.read_json(directory / 'job.json')['status'], 'ready')

    def test_apply_uses_existing_pending_guard_and_health_without_hypervisor_units(self):
        directory = self.review()
        self.build(directory)
        with patch('impact.recheck'), patch.object(worker, 'save_recovery'), \
                patch.object(worker.subprocess, 'run', return_value=Mock(returncode=0)), \
                patch.object(worker, 'run') as run, patch.object(rt, 'prepare_state_access'), \
                patch('diagnostics.wait_ready', return_value={'ok': True}) as health:
            worker.apply(directory)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any('switch-to-configuration' in cmd[0] and cmd[-1] == 'switch' for cmd in commands))
        self.assertFalse(any('incus.service' in cmd or 'nixlab-instances.service' in cmd for cmd in commands))
        health.assert_called_once_with(self.settings)
        job = rt.read_json(directory / 'job.json')
        self.assertEqual(job['status'], 'awaiting-confirmation')
        self.assertEqual(rt.read_json(self.state / 'pending.json')['id'], directory.name)
        with self.assertRaisesRegex(ValueError, 'running generation differs'):
            self.manager.confirm(directory.name)

    def test_invalid_roles_and_legacy_jobs_fail_closed_or_keep_hypervisor_target(self):
        self.assertEqual(worker.host_role({}), 'hypervisor')
        for job in ({'hostRole': 'other'}, {'hostRole': 'bootstrap'},
                    {'hostRole': 'bootstrap', 'principal': 'root-update-command'}):
            with self.subTest(job=job), self.assertRaises(ValueError):
                worker.host_role(job)

    def test_expired_bootstrap_after_restart_uses_existing_recovery_manifest(self):
        directory = self.review()
        self.build(directory)
        previous = self.root.parent / 'previous'
        rt.atomic(previous / 'bin/switch-to-configuration', 'fixture')
        def process(args, **kwargs):
            return subprocess.CompletedProcess(args, 1 if args[0] == 'nmcli' else 0,
                                               '{}' if args[0] == 'rfkill' else '')
        with patch('impact.recheck'), patch.dict(rt.SECRET_PATHS, {}, clear=True), \
                patch.object(worker.subprocess, 'run', side_effect=process), \
                patch.object(worker, 'run'), patch.object(rt, 'prepare_state_access'), \
                patch('diagnostics.wait_ready', return_value={'ok': True}), \
                patch.object(worker.shutil, 'which', return_value=None), \
                patch('operations.Operations.recover_interrupted'):
            worker.apply(directory)
            record = rt.read_json(directory / 'job.json')
            record['previousSystem'] = str(previous)
            rt.write_json(directory / 'job.json', record)
            rt.write_json(self.state / 'pending.json', {'id': directory.name, 'deadline': 0})
            rt.write_json(rt.machine_path(self.root, 'settings'), dict(self.settings, hostName='interrupted'))
            worker.guard()
        self.assertEqual(rt.read_json(directory / 'job.json')['status'], 'rolled-back')
        self.assertEqual(rt.read_json(rt.machine_path(self.root, 'settings')), self.settings)
        self.assertFalse((self.state / 'pending.json').exists())
        self.assertFalse((directory / 'secrets.json').exists())

    def test_bootstrap_health_requires_only_portal_and_https_redirect(self):
        with patch('diagnostics.management_address', return_value='192.0.2.10'), \
                patch('diagnostics.request', return_value=(200, b'{}')) as request, \
                patch('diagnostics.http_entry', return_value=(308, True)):
            report = diagnostics.probe(self.settings)
        self.assertTrue(report['ok'])
        self.assertEqual([check['name'] for check in report['checks']], ['Nixlab', 'Nixlab HTTP entry'])
        request.assert_called_once_with('192.0.2.10', self.settings['setup']['port'], '/')

    def test_bootstrap_validation_preserves_access_and_rejects_optional_activation(self):
        for section in ('apps', 'cockpit', 'backup', 'tunnel'):
            changed = copy.deepcopy(self.settings)
            changed[section]['enable'] = True
            with self.subTest(section=section), self.assertRaises(ValueError):
                model.validate_bootstrap(changed, inventory())
        changed = copy.deepcopy(self.settings)
        changed['management']['configure'] = True
        with self.assertRaises(ValueError):
            model.validate_bootstrap(changed, inventory())


class BootstrapClientTests(unittest.TestCase):
    def test_client_activates_and_never_auto_confirms(self):
        manager = Mock()
        manager.review.return_value = {'id': 'a' * 32}
        pending = {'id': 'a' * 32, 'status': 'awaiting-confirmation'}
        with patch('direct.wait_for', side_effect=[{'status': 'ready'}, pending]), \
                patch('builtins.print'):
            self.assertEqual(bootstrap.activate(manager=manager), pending)
        manager.review.assert_called_once_with({}, host_role='bootstrap', principal=bootstrap.PRINCIPAL,
                                               staged_source=None, expected_base_digest=None)
        manager.apply.assert_called_once_with('a' * 32)
        manager.confirm.assert_not_called()

    def test_resume_reuses_direct_recovery_without_accepting_other_roles(self):
        manager = Mock()
        job = {'id': 'a' * 32, 'hostRole': 'bootstrap', 'principal': bootstrap.PRINCIPAL,
               'status': 'awaiting-confirmation', 'settings': initial()}
        with patch('diagnostics.wait_ready'), patch('builtins.print'):
            self.assertEqual(bootstrap.resume(manager, job), job)
        manager.apply.assert_not_called()
        manager.confirm.assert_not_called()
        with self.assertRaises(ValueError):
            bootstrap.resume(manager, dict(job, hostRole='hypervisor'))
