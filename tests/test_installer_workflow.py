"""Retry, source-preservation, health-gating and recovery regressions."""
import copy
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


class SourceTests(unittest.TestCase):
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
