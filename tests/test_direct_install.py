"""Direct conversion planning and activation boundaries; no target host is changed."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'installer'), str(BASE / 'admin'), str(BASE / 'scripts')]
import direct
import runtime as rt

KEY = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB fixture'


def inventory():
    return {'architecture': 'x86_64', 'cpuVendor': 'intel', 'kvm': True,
            'interfaces': [{'name': 'eth0', 'wireless': False, 'addresses': ['192.0.2.10/24']}],
            'routes': [{'dst': '192.0.2.0/24', 'dev': 'eth0'}], 'disks': []}


def initial():
    settings = json.loads((BASE / 'tests/fixtures/defaults.json').read_text())
    settings.update(cpuVendor='intel', hostName='existing-host')
    settings['setup']['enable'] = True
    settings['management'].update(interface='eth0', address='192.0.2.10', prefixLength=24,
                                  trustedCIDRs=['192.0.2.0/24'], gateway='192.0.2.1')
    return settings


class DirectPlanningTests(unittest.TestCase):
    def test_route_selection_skips_all_overlapping_networks(self):
        routes = [{'dst': 'default'}, {'dst': '10.90.0.0/16'}, {'dst': '10.77.0.0/24'}]
        self.assertEqual(direct.guest_network(routes)['cidr'], '10.78.0.0/24')
        with self.assertRaisesRegex(ValueError, 'non-overlapping'):
            direct.guest_network(routes, '10.90.90.0/24')
        for invalid in ['8.8.8.0/24', '10.77.0.0/16', '::/24', '10.77.0.1/24',
                        '127.0.0.0/24', '169.254.1.0/24', '192.0.2.0/24']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                direct.guest_network([], invalid)

    def test_direct_candidate_preserves_uplink_and_enables_management(self):
        current = initial()
        original = copy.deepcopy(current)
        value = direct.prepare(current, {'adminKeys': [KEY]}, inventory())
        self.assertEqual(current, original)
        self.assertTrue(value['ready'])
        self.assertTrue(value['cockpit']['enable'])
        self.assertTrue(value['apps']['enable'])
        self.assertEqual(value['apps']['portainerPort'], 9443)
        self.assertEqual(value['management'], current['management'])
        self.assertEqual(value['mounts'], [])
        self.assertEqual(value['adminKeys'], [KEY])

    def test_explicit_key_and_no_apps_do_not_require_a_guest_vm(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / 'admin.pub'
            key.write_text(KEY + '\n')
            value = direct.prepare(initial(), {}, inventory(), [key], '10.78.0.0/24', apps=False)
        self.assertFalse(value['apps']['enable'])
        self.assertEqual(value['guests']['cidr'], '10.78.0.0/24')

    def test_local_console_needs_no_key_but_remote_access_must_be_explicit(self):
        self.assertEqual(direct.prepare(initial(), {}, inventory())['adminKeys'], [])
        with self.assertRaisesRegex(ValueError, 'client SSH public key'):
            direct.prepare(initial(), {}, inventory(), remote=True)
        self.assertTrue(direct.prepare(initial(), {}, inventory(), remote=True, password_login=True)['access']['sshPasswordLogin'])
        current = initial()
        current['radios']['wifi'].update(enable=True, interface='eth0')
        with self.assertRaisesRegex(ValueError, 'wpa_supplicant'):
            direct.prepare(current, {'adminKeys': [KEY], 'wpaProfiles': True}, inventory())

    def test_retry_reuses_partially_initialized_network_identity(self):
        host = inventory()
        host['routes'].append({'dst': '10.90.90.0/24', 'dev': 'incusbr0'})
        prior = direct.guest_network([])
        value = direct.prepare(initial(), {'adminKeys': [KEY]}, host, previous_guests=prior)
        self.assertEqual(value['guests'], dict(prior, bridge='incusbr0'))
        with self.assertRaisesRegex(ValueError, 'cannot change'):
            direct.prepare(initial(), {'adminKeys': [KEY]}, host,
                           subnet='10.78.0.0/24', previous_guests=prior)


class DirectActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = Mock()
        self.manager.review.return_value = {'id': 'a' * 32}
        self.manager.job_path.return_value = self.root
        self.candidate = initial()
        self.candidate['apps']['enable'] = True
        self.secret_patch = patch.dict(rt.SECRET_PATHS, portainerPassword=self.root / 'portainer-password')
        self.secret_patch.start()
        self.addCleanup(self.secret_patch.stop)

    def test_separate_secret_and_confirm_only_after_successful_activation(self):
        with patch('direct.wait_for', side_effect=[{'status': 'ready'}, {'status': 'awaiting-confirmation'}]), patch('builtins.print'):
            password, _ = direct.activate(self.candidate, self.manager)
        self.assertGreaterEqual(len(password), 24)
        args, kwargs = self.manager.review.call_args
        self.assertNotIn(password, json.dumps(args[0]))
        self.assertEqual(kwargs['secret_values'], {})
        self.assertEqual(rt.SECRET_PATHS['portainerPassword'].read_text().strip(), password)
        self.assertEqual([call[0] for call in self.manager.method_calls], ['review', 'apply', 'confirm'])

    def test_build_failure_never_applies_or_confirms(self):
        with patch('direct.wait_for', side_effect=ValueError('failed')), patch('builtins.print'):
            with self.assertRaises(ValueError):
                direct.activate(self.candidate, self.manager)
        self.manager.apply.assert_not_called()
        self.manager.confirm.assert_not_called()

    def test_activation_failure_never_confirms(self):
        with patch('direct.wait_for', side_effect=[{'status': 'ready'}, ValueError('rolled-back')]), patch('builtins.print'):
            with self.assertRaises(ValueError):
                direct.activate(self.candidate, self.manager)
        self.manager.apply.assert_called_once()
        self.manager.confirm.assert_not_called()

    def test_resume_preserves_existing_portainer_credential(self):
        rt.SECRET_PATHS['portainerPassword'].write_text('existing-secret')
        with patch('direct.wait_for', return_value={'status': 'awaiting-confirmation'}), patch('builtins.print'):
            password, _ = direct.activate(self.candidate, self.manager)
        self.assertIsNone(password)
        self.assertEqual(self.manager.review.call_args.kwargs['secret_values'], {})

    def test_failed_worker_status_is_reported_with_log_path(self):
        self.manager.job.return_value = {'status': 'failed', 'error': 'fixture build failure'}
        with self.assertRaisesRegex(ValueError, 'fixture build failure.*build.log'):
            direct.wait_for(self.manager, 'a' * 32, {'ready'}, timeout=1)

    def test_initial_auth_permissions_are_set_after_user_creation(self):
        (self.root / 'auth.json').write_text('{}')
        with patch.object(rt, 'STATE', self.root), patch.object(rt.grp, 'getgrnam', return_value=Mock(gr_gid=123)), patch.object(rt.os, 'chown') as chown:
            rt.prepare_state_access()
        self.assertEqual((self.root / 'auth.json').stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o710)
        self.assertEqual(chown.call_count, 2)


if __name__ == '__main__':
    unittest.main()

