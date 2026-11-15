"""Failed systemd observation must not overwrite durable workflow state."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / name) for name in ('admin', 'scripts')]
from controller import Manager
from deployments import Deployments
from operations import Operations
import runtime as rt

ID = 'a' * 32
UNIT = 'nixlab-build-' + ID
SENTINEL = 'private-systemd-error-sentinel'


class WorkerObservationTests(unittest.TestCase):
    def test_active_inactive_and_missing_workers(self):
        for code, active in ((0, True), (3, False), (4, False)):
            with self.subTest(code=code), patch.object(rt.subprocess, 'run', return_value=Mock(returncode=code)) as run:
                self.assertEqual(rt.worker_active(UNIT), active)
                run.assert_called_once_with(['systemctl', 'is-active', '--quiet', UNIT],
                                            capture_output=True, timeout=10)

    def test_observation_errors_are_bounded_and_do_not_disclose_output(self):
        for error in (OSError(SENTINEL), subprocess.TimeoutExpired(UNIT, 10, stderr=SENTINEL)):
            with self.subTest(error=type(error)), patch.object(rt.subprocess, 'run', side_effect=error):
                with self.assertRaisesRegex(ValueError, 'Worker state unavailable') as caught:
                    rt.worker_active(UNIT)
                self.assertNotIn(SENTINEL, str(caught.exception))
        for code in (1, 2, -9):
            with self.subTest(code=code), patch.object(rt.subprocess, 'run', return_value=Mock(returncode=code, stderr=SENTINEL)):
                with self.assertRaisesRegex(ValueError, 'Worker state unavailable') as caught:
                    rt.worker_active(UNIT)
                self.assertNotIn(SENTINEL, str(caught.exception))

    def test_unit_names_are_restricted_before_systemctl(self):
        with patch.object(rt.subprocess, 'run') as run:
            for unit in (None, '--all', 'incus', 'nixlab-build-../etc', UNIT + '.service',
                         'nixlab-build-' + 'A' * 32, 'nixlab-guard-' + ID):
                with self.subTest(unit=unit), self.assertRaises(ValueError):
                    rt.worker_active(unit)
            run.assert_not_called()

    def test_unknown_inspection_does_not_mutate_any_domain_record(self):
        for domain in ('configuration', 'operation', 'deployment'):
            with self.subTest(domain=domain), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                state = root / 'state'
                manager = Manager(root, state)
                operations = Operations(root, state)
                directory = state / ('operations' if domain == 'operation' else 'jobs') / ID
                directory.mkdir()
                record = {'id': ID, 'created': time.time() - 120, 'updatedAt': time.time() - 120,
                          'status': 'building' if domain == 'configuration' else 'running', 'targets': {}}
                path = directory / ('deployment.json' if domain == 'deployment' else 'job.json')
                rt.write_json(path, record)
                rt.write_json(state / 'active.json', {'id': ID})
                original = path.read_bytes()
                with patch.object(rt.subprocess, 'run', side_effect=subprocess.TimeoutExpired(UNIT, 10)):
                    with self.assertRaisesRegex(ValueError, 'Worker state unavailable'):
                        if domain == 'configuration': manager.status()
                        elif domain == 'operation': operations.status()
                        else: Deployments(root, state).status(ID)
                self.assertEqual(path.read_bytes(), original)

    def test_operation_domains_keep_their_existing_stopped_worker_semantics(self):
        with tempfile.TemporaryDirectory() as folder:
            operations = Operations(folder, folder)
            directory = operations.directory / ID
            directory.mkdir()
            path = directory / 'job.json'
            record = {'id': ID, 'created': time.time() - 120, 'status': 'running',
                      'recovery': {'restartRequired': True}}
            rt.write_json(path, record)
            with patch.object(rt.subprocess, 'run', return_value=Mock(returncode=0)):
                self.assertEqual(operations.status()[0], record)
                self.assertEqual(rt.read_json(path), record)
            with patch.object(rt.subprocess, 'run', return_value=Mock(returncode=3)):
                result = operations.status()[0]
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['recovery'], record['recovery'])
            self.assertIn('Inspect guest state and recovery intent', result['error'])
