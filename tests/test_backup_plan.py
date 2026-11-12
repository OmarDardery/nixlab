"""Regression checks for missing coverage and pre-shutdown capacity refusal."""
import json
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
import backup_plan
import recovery
import runtime as rt


class CoverageTests(unittest.TestCase):
    def test_block_usb_and_pci_passthrough_cannot_silently_count_as_exported_storage(self):
        for kind in ('unix-block', 'usb', 'pci'):
            guest = {'project': 'lab', 'name': 'vm', 'devices': {'hardware': {'type': kind, 'source': '/dev/example'}}}
            result = backup_plan.coverage([guest], [], [guest], [], {})
            self.assertFalse(result['complete'])
            self.assertEqual(result['items'][-1]['kind'], 'passthrough')
            self.assertEqual(result['items'][-1]['status'], 'external')

    def test_external_mount_and_missing_custom_volume_cannot_be_complete(self):
        guest = {'project': 'lab', 'name': 'apps', 'expanded_devices': {
            'root': {'type': 'disk', 'path': '/', 'pool': 'guests'},
            'bind': {'type': 'disk', 'path': '/media', 'source': '/srv/media', 'readonly': 'true'},
            'data': {'type': 'disk', 'path': '/data', 'source': 'custom/data', 'pool': 'guests'}}}
        result = backup_plan.coverage([guest], [], [guest], [], {'lab': 'default'})
        self.assertFalse(result['complete'])
        self.assertEqual([row['status'] for row in result['items']], ['included', 'external', 'missing'])
        self.assertNotIn('/srv/media', json.dumps(result))
        self.assertEqual(result['restore'], 'unverified')

    def test_shared_namespace_custom_volume_is_included_and_excluded_volume_is_visible(self):
        guest = {'project': 'lab', 'name': 'apps', 'devices': {
            'data': {'type': 'disk', 'path': '/data', 'source': 'custom/data', 'pool': 'guests'}}}
        volume = {'project': 'default', 'pool': 'guests', 'name': 'data'}
        self.assertTrue(backup_plan.coverage([guest], [volume], [guest], [volume], {'lab': 'default'})['complete'])
        result = backup_plan.coverage([guest], [volume], [], [], {'lab': 'default'})
        self.assertEqual([row['status'] for row in result['items']], ['excluded', 'excluded'])
        self.assertFalse(result['complete'])

    def test_missing_configured_credentials_are_reported_without_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'password'
            configured = {'backup': {'enable': True, 'passwordFile': str(path)}}
            with patch.object(recovery, 'settings', return_value=configured):
                result = recovery.coverage()
                self.assertFalse(result['complete'])
                self.assertIn('backup-passwordFile', result['missing'])
                path.write_text('secret-value')
                self.assertTrue(recovery.coverage()['complete'])
                self.assertNotIn('secret-value', json.dumps(recovery.coverage()))

    def test_sops_symlink_is_archived_as_checked_regular_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); secret = root / 'sops-secret'; secret.write_text('runtime-value')
            link = root / 'restic-password'; link.symlink_to(secret)
            configured = {'backup': {'enable': True, 'passwordFile': str(link)}}
            with patch.object(recovery, 'settings', return_value=configured):
                self.assertEqual(recovery.secret_files()['runtime/backup-passwordFile'], secret)
                self.assertTrue(recovery.coverage()['complete'])
                secret.chmod(0o666)
                self.assertFalse(recovery.coverage()['complete'])
                self.assertNotIn('runtime/backup-passwordFile', recovery.secret_files())

    def test_secret_link_and_target_ancestry_must_both_be_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); links = root / 'links'; targets = root / 'targets'
            links.mkdir(); targets.mkdir()
            secret = targets / 'password'; secret.write_text('runtime-value')
            link = links / 'password'; link.symlink_to(secret)
            configured = {'backup': {'enable': True, 'passwordFile': str(link)}}
            with patch.object(recovery, 'settings', return_value=configured):
                self.assertEqual(recovery.secret_source(link), secret)
                for directory in (links, targets):
                    with self.subTest(directory=directory.name):
                        directory.chmod(0o777)
                        self.assertIsNone(recovery.secret_source(link))
                        self.assertFalse(recovery.coverage()['complete'])
                        self.assertNotIn('runtime/backup-passwordFile', recovery.secret_files())
                        directory.chmod(0o700)
                        self.assertTrue(recovery.coverage()['complete'])
                # Sticky roots protect owned children and remain usable in tests
                # and temporary root-controlled staging, like ordinary /tmp.
                root.chmod(0o1777)
                self.assertEqual(recovery.secret_source(link), secret)

    def test_missing_age_key_cannot_be_hidden_by_an_empty_secret_archive(self):
        with patch.object(recovery, 'settings', return_value={'secrets': {'enable': True, 'ageKeyFile': '/nonexistent-key'}}):
            self.assertIn('sops-age-key', recovery.coverage()['missing'])

    def test_fresh_apps_bootstrap_does_not_require_optional_password_reset_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('ca.key', 'ca.crt', 'tls.key', 'tls.crt', 'auth.json', 'apps-tls.key', 'apps-tls.crt', 'bootstrap'):
                (root / name).write_text('fixture')
            configured = {'setup': {'enable': True}, 'apps': {'enable': True}}
            with patch.object(recovery, 'settings', return_value=configured), patch.object(rt, 'STATE', root), \
                    patch.object(rt, 'SECRET_PATHS', {'portainerPassword': root / 'bootstrap'}):
                self.assertTrue(recovery.coverage()['complete'])
                self.assertFalse((root / 'portainer-account.json').exists())
                account = root / 'portainer-account.json'; account.write_text('{}')
                self.assertTrue(recovery.coverage()['complete'])
                account.chmod(0o666)
                self.assertIn('apps-account', recovery.coverage()['missing'])
                account.unlink(); account.symlink_to(root / 'missing-account')
                self.assertIn('apps-account', recovery.coverage()['missing'])


class CapacityTests(unittest.TestCase):
    def test_estimates_include_retained_space_reserve_and_recovery_kits(self):
        result = backup_plan.capacity([('guest', 100, 200), ('volume', 400, None)], 1000, 100, 200)
        self.assertEqual(result['estimatedNewBytes'], 2 * 16 * 1024**2 + 750 + 200)
        self.assertFalse(result['sufficientForEstimate'])
        self.assertEqual(result['unknown'], [])

    def test_missing_usage_and_previous_only_estimate_remain_explicit(self):
        result = backup_plan.capacity([('unknown', None, None), ('old', None, 200), ('invalid', True, -1)], 10**12, 10)
        self.assertEqual(result['unknown'], ['unknown', 'old', 'invalid'])
        self.assertIsNone(result['items'][0]['estimatedBytes'])
        self.assertEqual(result['items'][1]['basis'], 'previous-archive')

    def test_actual_usage_queries_are_bounded_and_timeout_remains_unknown(self):
        guest = {'project': 'lab', 'name': 'apps', 'archive': 'guest.tar.gz'}
        with tempfile.TemporaryDirectory() as folder, patch.object(backup, 'ROOT', Path(folder)), \
                patch.object(backup, 'incus', side_effect=subprocess.TimeoutExpired('incus', 15)) as incus:
            result = backup.staging_plan([guest], [], 0, False)
            self.assertEqual(result['unknown'], ['guest.tar.gz'])
            self.assertEqual(incus.call_args.kwargs['timeout'], 15)

    def test_previous_generation_cannot_escape_staging(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(backup, 'ROOT', Path(folder)):
            (Path(folder) / 'latest.json').write_text('{"generation":"../../elsewhere"}')
            self.assertEqual(backup.previous_sizes(), {})


if __name__ == '__main__':
    unittest.main()
