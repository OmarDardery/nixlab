"""Crash cleanup must not dispose of pending recovery or live credentials."""
from pathlib import Path
import sys
import tempfile
import time
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import runtime as rt
from credential_fixtures import SECRETS, assert_public_clean


class CleanupTests(unittest.TestCase):
    def test_only_proven_terminal_candidate_secrets_are_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            states = ['confirmed', 'rolled-back', 'cancelled', 'superseded',
                      'failed', 'building', 'ready', 'applying', 'awaiting-confirmation', 'rolling-back']
            for i, status in enumerate(states):
                job = root / 'jobs' / f'{i:032x}'
                rt.write_json(job / 'job.json', {'status': status})
                rt.atomic(job / 'secrets.json', 'sensitive')
                rt.atomic(job / 'recovery/manifest.json', 'retained')
            rt.cleanup_terminal_secrets(root)
            for i, status in enumerate(states):
                job = root / 'jobs' / f'{i:032x}'
                self.assertEqual((job / 'secrets.json').exists(), i >= 4, status)
                self.assertEqual((job / 'recovery/manifest.json').read_text(), 'retained')
            rt.cleanup_terminal_secrets(root)  # Idempotent after a partial/complete sweep.

    def test_expired_review_is_cancelled_before_its_secret_is_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / 'jobs' / ('a' * 32)
            job = directory / 'job.json'
            path = directory / 'secrets.json'
            rt.write_json(job, {'status': 'ready', 'created': time.time() - rt.STAGED_SECRET_LIFETIME - 1})
            rt.write_json(path, {'cockpitPassword': SECRETS['staged']})
            rt.cleanup_terminal_secrets(root)
            self.assertFalse(path.exists())
            self.assertEqual(rt.read_job_record(job)['status'], 'cancelled')
            assert_public_clean(self, job.read_bytes())

    def test_staged_secret_reader_rejects_missing_aliased_and_nonprivate_files(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / 'secrets.json'
            expected = ['cockpitPassword']
            with self.assertRaisesRegex(ValueError, 'Staged credentials are missing'):
                rt.read_staged_secrets(directory, expected)
            rt.write_json(path, {'cockpitPassword': SECRETS['staged']})
            self.assertEqual(rt.read_staged_secrets(directory, expected)['cockpitPassword'], SECRETS['staged'])
            path.chmod(0o640)
            with self.assertRaisesRegex(ValueError, 'Staged credentials are invalid') as caught:
                rt.read_staged_secrets(directory, expected)
            assert_public_clean(self, str(caught.exception))
            other = path.with_name('private.json')
            path.rename(other)
            path.symlink_to(other)
            with self.assertRaisesRegex(ValueError, 'Staged credentials are invalid'):
                rt.read_staged_secrets(directory, expected)

    def test_pending_pointer_and_unknown_records_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for i in range(3):
                job = root / 'jobs' / f'{i:032x}'
                rt.write_json(job / 'job.json', {'status': 'confirmed'})
                rt.atomic(job / 'secrets.json', 'sensitive')
            rt.write_json(root / 'pending.json', {'id': f'{0:032x}'})
            (root / 'jobs' / f'{1:032x}' / 'job.json').write_text('corrupt')
            outside = root / 'outside'
            rt.write_json(outside / 'job.json', {'status': 'confirmed'})
            rt.atomic(outside / 'secrets.json', 'retained')
            (root / 'jobs' / f'{3:032x}').symlink_to(outside)
            rt.cleanup_terminal_secrets(root)
            self.assertTrue((root / 'jobs' / f'{0:032x}' / 'secrets.json').exists())
            self.assertTrue((root / 'jobs' / f'{1:032x}' / 'secrets.json').exists())
            self.assertFalse((root / 'jobs' / f'{2:032x}' / 'secrets.json').exists())
            self.assertEqual((outside / 'secrets.json').read_text(), 'retained')


if __name__ == '__main__':
    unittest.main()
