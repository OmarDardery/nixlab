"""Crash cleanup must not dispose of pending recovery or live credentials."""
from pathlib import Path
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import runtime as rt


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
