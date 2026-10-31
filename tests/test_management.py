"""Management listener collisions and local-account credential recovery boundaries."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts'), str(BASE / 'tests')]
import model
import runtime as rt
import worker
import operations
from test_setup import settings, HOST


class ManagementTests(unittest.TestCase):
    def test_restore_resets_only_generated_mac_on_stopped_target(self):
        current = {'status': 'Stopped', 'config': {'volatile.eth0.hwaddr': '00:16:3e:00:00:01', 'user.keep': 'value'},
                   'expanded_devices': {'eth1': {'type': 'nic', 'hwaddr': '00:16:3e:00:00:02'}}}
        with patch.object(operations, 'instance', return_value=current), patch.object(operations, 'incus') as incus:
            operations.reset_restore_macs('lab', 'restored')
            incus.assert_called_once_with('config', 'unset', 'restored', 'volatile.eth0.hwaddr', '--project', 'lab')
        self.assertEqual(current['expanded_devices']['eth1']['hwaddr'], '00:16:3e:00:00:02')
        current['status'] = 'Running'
        with patch.object(operations, 'instance', return_value=current), patch.object(operations, 'incus') as incus:
            with self.assertRaises(ValueError): operations.reset_restore_macs('lab', 'restored')
            incus.assert_not_called()

    def test_explicit_mac_collision_blocks_restore_release(self):
        with tempfile.TemporaryDirectory() as folder:
            ops = operations.Operations(Path(folder)/'root', Path(folder)/'state')
            current = {'expanded_devices': {'eth0': {'type': 'nic', 'hwaddr': '00:16:3E:AA:BB:CC'}}}
            other = {'name': 'original', 'config': {'volatile.eth0.hwaddr': '00:16:3e:aa:bb:cc'}}
            with patch.object(operations, 'instance', return_value=current), patch.object(operations, 'incus', return_value=json.dumps([other])) as incus:
                with self.assertRaisesRegex(ValueError, 'MAC address conflicts'):
                    ops.perform({'action': 'release-restore', 'project': 'lab', 'name': 'restored'})
                self.assertEqual(incus.call_count, 1)

    def test_listener_collisions_and_custom_private_ports(self):
        cfg = settings(); cfg['cockpit']['enable'] = True; cfg['apps']['enable'] = True
        cfg['apps']['portainerPort'] = 9443
        model.validate_settings(copy.deepcopy(cfg), cfg, HOST, require_devices=False)
        for port in (9440, 8443, 9090, 9100, True, 65536):
            candidate = copy.deepcopy(cfg); candidate['apps']['portainerPort'] = port
            with self.assertRaises(ValueError): model.validate_settings(candidate, cfg, HOST, require_devices=False)

    def test_custom_management_ports_are_not_public_tunnel_origins(self):
        cfg = settings(); cfg['cockpit']['enable'] = True; cfg['cockpit']['port'] = 19090
        cfg['tunnel'].update(enable=True, uuid='11111111-1111-1111-1111-111111111111')
        for port in (19090, 9443):
            cfg['tunnel']['ingress'] = {'app.example.com': 'https://192.0.2.10:' + str(port) + '?test=1'}
            with self.assertRaisesRegex(ValueError, 'Management endpoints'):
                model.validate_settings(copy.deepcopy(cfg), cfg, HOST, require_devices=False)

    def test_local_password_is_salted_hash_only(self):
        cfg = settings(); cfg['cockpit'].update(enable=True, managePassword=True)
        password = 'test-cockpit-password'
        a = rt.encode_secrets({'cockpitPassword': password}, cfg)['cockpitPassword']
        b = rt.encode_secrets({'cockpitPassword': password}, cfg)['cockpitPassword']
        self.assertTrue(a.startswith('$6$')); self.assertNotEqual(a, b); self.assertNotIn(password, a)
        for value in ('short', 'long-enough-but\nnewline', 'a' * 257):
            with self.assertRaises(ValueError): rt.encode_secrets({'cockpitPassword': value}, cfg)
        cfg['cockpit']['enable'] = False
        # A Nix module can own enable=true; the worker checks evaluated settings.
        self.assertTrue(rt.encode_secrets({'cockpitPassword': password}, cfg)['cockpitPassword'].startswith('$6$'))
        cfg['cockpit']['managePassword'] = False
        with self.assertRaises(ValueError): rt.encode_secrets({'cockpitPassword': password}, cfg)

    def test_operator_credential_recovery_does_not_restore_other_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            recovery = Path(folder)
            record = subprocess.CompletedProcess([], 0, 'operator:$6$old:20000:0:99999:7:::\n')
            with patch('worker.subprocess.run', return_value=record): worker.save_operator_password(recovery)
            self.assertEqual((recovery / 'operator-password').stat().st_mode & 0o777, 0o600)
            with patch('worker.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'operator:x\n')) as run:
                worker.restore_operator_password(recovery)
            self.assertEqual(run.call_args.args[0], ['chpasswd', '--encrypted'])
            self.assertEqual(run.call_args.kwargs['input'], 'operator:$6$old\n')
            self.assertNotIn('$6$old', str(run.call_args.args))

    def test_first_enrollment_is_locked_on_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            recovery = Path(folder)
            with patch('worker.subprocess.run', return_value=subprocess.CompletedProcess([], 2, '')):
                worker.save_operator_password(recovery)
            self.assertEqual((recovery / 'operator-password').read_text(), '!\n')


if __name__ == '__main__': unittest.main()
