"""Clock loss warns without blocking existing sessions or guest operations."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import diagnostics
from controller import Manager

NOW = 1800000000


class ClockTests(unittest.TestCase):
    def test_synchronized_and_unsynchronized_are_distinct(self):
        for observed, state in [('yes', 'synchronized'), ('no', 'plausible-but-unsynchronized')]:
            with self.subTest(observed=observed), patch.object(diagnostics.subprocess, 'run', return_value=Mock(returncode=0, stdout=observed)) as command:
                result = diagnostics.clock_status(NOW)
                self.assertEqual(result['state'], state)
                self.assertEqual(result['evidenceWarning'], observed != 'yes')
                self.assertEqual(command.call_args.kwargs['timeout'], 3)

    def test_missing_service_is_unknown_synchronization(self):
        for error in (OSError('missing'), subprocess.TimeoutExpired('timedatectl', 3)):
            with self.subTest(error=type(error)), patch.object(diagnostics.subprocess, 'run', side_effect=error):
                result = diagnostics.clock_status(NOW)
                self.assertEqual(result['state'], 'plausible-but-unsynchronized')
                self.assertIsNone(result['synchronized'])

    def test_invalid_clock_is_not_overridden_by_sync_flag(self):
        with patch.object(diagnostics.subprocess, 'run') as command:
            for value in (0, 4102444800, float('inf'), float('nan')):
                self.assertEqual(diagnostics.clock_status(value)['state'], 'invalid-clock')
            command.assert_not_called()

    def test_invalid_clock_blocks_before_any_key_or_trust_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = Manager(root=folder, state=folder)
            with patch('diagnostics.clock_status', return_value={'state': 'invalid-clock'}), patch('controller.command') as trust, patch('controller.subprocess.run') as openssl:
                with self.assertRaisesRegex(ValueError, 'Host clock'):
                    manager.browser_certificate()
                trust.assert_not_called()
                openssl.assert_not_called()

    def test_browser_bundle_requires_supplied_passphrase_and_restricts_incus_project(self):
        import base64
        import subprocess
        from credential_fixtures import SECRETS, assert_public_clean

        with tempfile.TemporaryDirectory() as folder:
            manager = Manager(root=folder, state=folder)
            with patch('diagnostics.require_plausible_clock'), patch('controller.command') as trust, patch('runtime.audit') as audit:
                with self.assertRaisesRegex(ValueError, 'bundle passphrase'):
                    manager.browser_certificate('test-device', 'short')
                trust.assert_not_called()
                bundle = manager.browser_certificate('test-device', SECRETS['p12'])
                trust_args = trust.call_args.args[0]
                self.assertEqual(trust_args[-3:], ['--projects', 'lab', '--restricted'])
                self.assertEqual(bundle['project'], 'lab')
                self.assertEqual(len(bundle['fingerprint']), 64)
                self.assertEqual(bundle['validDays'], 90)
                assert_public_clean(self, [trust_args, bundle, audit.call_args.args, audit.call_args.kwargs])
                self.assertEqual([p.name for p in Path(folder).iterdir()], ['jobs'])

            secret_bundle = Path(folder) / 'test.p12'
            secret_bundle.write_bytes(base64.b64decode(bundle['pkcs12']))
            args = ['openssl', 'pkcs12', '-in', str(secret_bundle), '-nokeys', '-passin', 'stdin']
            bad = subprocess.run(args, input=b'incorrect\n', capture_output=True)
            self.assertNotEqual(bad.returncode, 0)
            cert = subprocess.run(args, input=(SECRETS['p12'] + '\n').encode(), capture_output=True, check=True)
            inspected = subprocess.run(['openssl', 'x509', '-noout', '-text'], input=cert.stdout,
                                       capture_output=True, check=True)
            self.assertIn(b'secp384r1', inspected.stdout)

    def test_plausible_clock_does_not_require_ntp_for_issuance(self):
        with patch('diagnostics.clock_status', return_value={'state': 'plausible-but-unsynchronized'}):
            diagnostics.require_plausible_clock()


if __name__ == '__main__':
    unittest.main()
