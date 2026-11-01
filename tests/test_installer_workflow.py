"""Retry, source-preservation, health-gating and recovery regressions."""
import copy
import contextlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ('installer', 'admin', 'scripts')]
import diagnostics
import direct
import portainer
import runtime as rt
import worker
import workflow
from test_direct_install import initial, inventory

spec = importlib.util.spec_from_file_location('installed_nixos_workflow', BASE / 'installer/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class SourceTests(unittest.TestCase):
    def test_fresh_console_install_and_retry_from_bundled_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original, target, source, state = [root / name for name in ('original', 'target', 'source', 'state')]
            original.mkdir(); source.mkdir(); state.mkdir()
            for name in ('configuration.nix', 'hardware-configuration.nix'):
                (original / name).write_text('{ }')
            for name in ('flake.nix', 'flake.lock', 'admin/main.py'):
                rt.atomic(source / name, 'fixture')
            flag = root / 'NIXOS'; flag.touch()
            path_type = Path
            def paths(value, *extra):
                if str(value) == '/etc/NIXOS': return flag
                if str(value) in ('/etc/nixlab-iso-revision', '/etc/nixos.pre-nixlab', '/var/lib/incus/database', '/etc/systemd/system/nixlab-firstboot.service'):
                    return root / ('missing-' + path_type(value).name)
                if str(value) in ('/root', '/home'): return root / 'homes'
                return path_type(value, *extra)
            real_run = subprocess.run
            def run(args, **kwargs):
                if args[0] == 'git': return real_run(args, check=True, **kwargs)
                return subprocess.CompletedProcess(args, 0, '')
            def evaluate(config, attribute):
                return copy.deepcopy(initial()) if attribute == 'nixlabDefaults' else {'adminKeys': [], 'wpaProfiles': False}
            def activate(candidate):
                rt.write_json(rt.machine_path(target, 'settings'), candidate)
                return None, {}
            patches = [
                patch.object(installer, 'installed_host', return_value=True),
                patch.object(installer, 'TARGET', target), patch.object(installer, 'ORIGINAL', original),
                 patch.object(installer, 'Path', side_effect=paths), patch.object(rt, 'ROOT', target), patch.object(rt, 'STATE', state),
                 patch.object(installer, 'choose_network', return_value=('eth0', '192.0.2.10', 24, '192.0.2.1')),
                 patch('model.inventory', return_value=inventory()), patch('workflow.remote_session', return_value=False),
                 patch.object(installer, 'evaluate', side_effect=evaluate), patch.object(installer, 'run', side_effect=run),
                 patch.object(installer, 'validate_kvm'), patch.object(installer, 'setup_password'),
                 patch.object(installer, 'publish_etc'), patch.object(installer, 'summarize'),
                 patch.object(rt, 'issue_tls'), patch.object(rt, 'prepare_state_access'),
                 patch('local_access.cockpit_accounts', return_value=['existing']),
                 patch('controller.Manager.status', return_value={'job': None}),
                 patch('direct.activate', side_effect=activate), patch('builtins.print'),
                 patch.object(sys, 'argv', ['nixlab-install', '--checkout', str(source)])
            ]
            with contextlib.ExitStack() as stack:
                for mocked in patches: stack.enter_context(mocked)
                installer.main()
                self.assertTrue(rt.read_json(rt.machine_path(target, 'settings'))['ready'])
                self.assertEqual(rt.read_json(rt.machine_path(target, 'settings'))['adminKeys'], [])
                rt.atomic(target / 'machine/modules/mine.nix', '{ }')
                (source / 'admin/main.py').write_text('updated')
                installer.main()
            self.assertEqual((target / 'admin/main.py').read_text(), 'updated')
            self.assertTrue((target / 'machine/modules/mine.nix').exists())
            self.assertEqual((target / 'machine/installed-system/configuration.nix').read_text(), '{ }')

    def test_bundled_source_without_git_or_manifest_can_be_updated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('flake.nix', 'flake.lock', 'admin/main.py'):
                rt.atomic(root / name, 'fixture')
            files = dict(workflow.incoming_files(root))
            self.assertEqual(set(files), {'flake.nix', 'flake.lock', 'admin/main.py'})
            (root / 'linked').symlink_to(root / 'flake.nix')
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                workflow.incoming_files(root)

    def test_source_update_is_staged_and_retains_machine_state(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name in ('live', 'incoming'):
                root = base / name
                root.mkdir()
                rt.atomic(root / 'flake.nix', '{}')
                rt.atomic(root / 'admin/main.py', name)
                rt.write_json(root / '.nixlab-files.json', ['flake.nix', 'admin/main.py'])
            live = base / 'live'
            rt.write_json(live / 'machine/format.json', {'version': 1})
            rt.write_json(live / 'machine/settings.json', {'hostName': 'my-host'})
            rt.atomic(live / 'machine/modules/custom.nix', '{ }')
            baseline = base / 'baseline.json'
            rt.write_json(baseline, workflow.hashes(live))
            stage = base / 'stage'
            changed, edits = workflow.stage_update(live, base / 'incoming', stage, baseline)
            self.assertTrue(changed); self.assertEqual(edits, [])
            self.assertEqual((live / 'admin/main.py').read_text(), 'live')
            self.assertEqual((stage / 'admin/main.py').read_text(), 'incoming')
            self.assertEqual(rt.read_json(stage / 'machine/settings.json'), {'hostName': 'my-host'})
            self.assertTrue((stage / 'machine/modules/custom.nix').exists())
            (live / 'admin/main.py').write_text('local-edit')
            changed, edits = workflow.stage_update(live, base / 'incoming', base / 'retry', baseline)
            self.assertFalse(changed); self.assertEqual(edits, ['admin/main.py'])
            self.assertEqual((base / 'retry/admin/main.py').read_text(), 'local-edit')

    def test_adoption_does_not_reuse_native_network_or_pool(self):
        data = {'projects': [{'name': 'default'}],
                'networks': [{'name': 'incusbr0', 'type': 'bridge', 'managed': True}, {'name': 'nixlabbr'}],
                'pools': [{'name': 'guests'}, {'name': 'nixlab-guests'}]}
        cfg = workflow.adoption_settings(initial(), data)
        self.assertEqual(cfg['guests']['bridge'], 'nixlabbr1')
        self.assertEqual(cfg['storage']['pool'], 'nixlab-guests1')
        self.assertEqual(cfg['connectivity']['preservedIncusBridges'], ['incusbr0'])
        data['projects'].append({'name': 'lab'})
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            workflow.adoption_settings(initial(), data)

    def test_incomplete_export_never_becomes_completed_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)
            data = {'instances': [{'name': 'important', 'project': 'default'}]}
            with patch('workflow.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['incus'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    workflow.backup_incus(data, destination)
            self.assertFalse((destination / 'complete.json').exists())


class ReadinessTests(unittest.TestCase):
    def test_dhcp_links_use_assigned_address(self):
        cfg = initial()
        result = subprocess.CompletedProcess([], 0, json.dumps([{'addr_info': [
            {'local': '192.0.2.20', 'family': 'inet', 'scope': 'global'}]}]))
        with patch('diagnostics.subprocess.run', return_value=result):
            self.assertEqual(diagnostics.management_address(cfg), '192.0.2.20')
            cfg['management']['configure'] = True
            with self.assertRaisesRegex(ValueError, 'no usable address'):
                diagnostics.management_address(cfg)

    def test_portainer_missing_admin_fails_health_even_with_live_proxy(self):
        cfg = initial(); cfg['apps']['enable'] = True; cfg['cockpit']['enable'] = True
        def response(address, port, path):
            return (404 if path == '/api/users/admin/check' else 200), b'{}'
        with patch('diagnostics.management_address', return_value='192.0.2.20'), patch('diagnostics.request', side_effect=response):
            report = diagnostics.probe(cfg)
        self.assertFalse(report['ok'])
        self.assertEqual([c['name'] for c in report['checks'] if not c['ok']], ['Portainer administrator'])

    def test_resume_waits_for_existing_worker_and_checks_health_before_confirm(self):
        manager = Mock()
        ready = {'id': 'a' * 32, 'status': 'ready', 'settings': initial()}
        awaiting = dict(ready, status='awaiting-confirmation')
        with patch('direct.wait_for', side_effect=[ready, awaiting]), patch('diagnostics.wait_ready') as healthy:
            direct.resume(manager, dict(ready, status='building'))
        manager.review.assert_not_called()
        manager.apply.assert_called_once_with('a' * 32)
        healthy.assert_called_once()
        manager.confirm.assert_called_once_with('a' * 32)
        with patch('diagnostics.wait_ready', side_effect=ValueError('not ready')):
            manager.reset_mock()
            with self.assertRaises(ValueError): direct.resume(manager, awaiting)
            manager.confirm.assert_not_called()

    def test_stored_password_is_never_presented_without_authentication(self):
        with tempfile.TemporaryDirectory() as folder:
            password = Path(folder) / 'password'; password.write_text('bootstrap-only')
            with patch.dict(rt.SECRET_PATHS, portainerPassword=password), patch.object(rt, 'STATE', Path(folder)), \
                 patch('diagnostics.management_address', return_value='192.0.2.20'), \
                 patch('diagnostics.request', return_value=(422, b'{}')):
                self.assertIsNone(portainer.credentials(initial()))

    def test_recovery_restarts_portainer_after_helper_failure(self):
        cfg = initial(); cfg['apps']['enable'] = True
        def command(*args, **kwargs):
            if args[:2] == ('docker', 'run'): raise ValueError('helper failed')
            return ''
        with patch('portainer.guest', side_effect=command) as guest:
            with self.assertRaisesRegex(ValueError, 'Portainer restarted'):
                portainer.reset(cfg)
        self.assertIn(('systemctl', 'start', 'docker-portainer.service'), [c.args for c in guest.call_args_list])

    def test_recovery_backs_up_then_resets_restarts_and_verifies(self):
        cfg = initial(); cfg['apps']['enable'] = True
        output = 'Password successfully updated for user: admin\nUse the following password to login: new-random-password\n'
        events = []
        def command(*args, **kwargs):
            events.append(args)
            return output if args[:2] == ('docker', 'run') else ''
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(rt, 'STATE', Path(folder)), patch.dict(rt.SECRET_PATHS, portainerPassword=Path(folder) / 'secret'), \
                 patch('portainer.guest', side_effect=command), patch('portainer.incus'), \
                 patch('diagnostics.wait_ready') as healthy, patch('diagnostics.management_address', return_value='192.0.2.20'), \
                 patch('portainer.credentials', return_value={'username': 'admin', 'password': 'new-random-password'}):
                result = portainer.reset(cfg)
                self.assertEqual(rt.SECRET_PATHS['portainerPassword'].read_text().strip(), 'new-random-password')
                self.assertEqual((Path(folder) / 'secret').stat().st_mode & 0o777, 0o600)
        self.assertLess(next(i for i, e in enumerate(events) if e[0] == 'tar'), next(i for i, e in enumerate(events) if e[:2] == ('docker', 'run')))
        self.assertEqual(events[-1], ('systemctl', 'start', 'docker-portainer.service'))
        healthy.assert_called_once()
        self.assertEqual(result['username'], 'admin')

    def test_rollback_restarts_only_loaded_units(self):
        results = [subprocess.CompletedProcess([], 0, 'not-found\n'), subprocess.CompletedProcess([], 0, 'loaded\n')]
        with patch('worker.subprocess.run', side_effect=results), patch('worker.run') as run:
            worker.restart_loaded(['missing.service', 'present.service'], Mock())
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ['systemctl', 'try-restart', 'present.service'])

    def test_original_activation_error_survives_failed_rollback(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            rt.write_json(directory / 'job.json', {'status': 'applying'})
            with patch('worker.rollback', side_effect=ValueError('recovery failed')):
                worker.recover_activation(directory, ValueError('proxy certificate missing'))
            job = rt.read_json(directory / 'job.json')
            self.assertEqual(job['error'], 'proxy certificate missing')
            self.assertEqual(job['rollbackError'], 'recovery failed')


if __name__ == '__main__': unittest.main()
