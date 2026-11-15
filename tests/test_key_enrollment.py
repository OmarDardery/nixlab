"""Key enrollment must leave client access proof to an explicit confirmation."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'admin'))
import main


class KeyEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.key = Path(self.directory.name) / 'laptop.pub'
        self.key.write_text('# client public key\nssh-ed25519 fixture laptop\n')
        self.manager = Mock()
        self.manager.settings.return_value = {'adminKeys': ['ssh-ed25519 existing']}
        self.manager.review.return_value = {'id': 'a' * 32}
        self.pending = {'id': 'a' * 32, 'status': 'awaiting-confirmation', 'deadline': 1800000000}
        self.manager.job.side_effect = [
            {'status': 'building'}, {'status': 'ready'},
            {'status': 'applying'}, self.pending]
        # A concurrent/latest status result must never choose the job to apply.
        self.manager.status.return_value = {'job': {'id': 'b' * 32, 'status': 'ready'}}

    def run_enrollment(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(main.time, 'sleep'), \
                patch('diagnostics.wait_ready') as readiness, \
                patch('diagnostics.management_address', return_value='192.0.2.10'):
            record = main.enroll_key(self.manager, self.key, {})
        return record, output.getvalue(), readiness

    def test_key_activation_requires_explicit_access_confirmation(self):
        record, output, readiness = self.run_enrollment()
        self.assertEqual(record, self.pending)
        self.manager.confirm.assert_not_called()
        self.manager.apply.assert_called_once_with('a' * 32)
        self.manager.review.assert_called_once_with(
            {'adminKeys': ['ssh-ed25519 existing', 'ssh-ed25519 fixture laptop']},
            principal='root-key-enrollment')
        readiness.assert_called_once()
        self.assertIn('fresh session: ssh operator@192.0.2.10', output)
        self.assertIn('sudo nixlab-admin config confirm ' + 'a' * 32, output)
        self.assertIn('automatic rollback remains armed', output)
        self.assertIn('Confirmation deadline:', output)
        self.assertNotIn('fixture', output)
        self.assertTrue(all(call.args == ('a' * 32,) for call in self.manager.job.call_args_list))

    def test_empty_file_has_no_transaction(self):
        self.key.write_text('# no client key\n')
        with self.assertRaisesRegex(ValueError, 'empty'):
            self.run_enrollment()
        self.manager.review.assert_not_called()
        self.manager.apply.assert_not_called()

    def test_failed_build_does_not_activate_or_confirm(self):
        self.manager.job.side_effect = [{'status': 'failed', 'error': 'build failed'}]
        with self.assertRaisesRegex(ValueError, 'build failed'):
            self.run_enrollment()
        self.manager.apply.assert_not_called()
        self.manager.confirm.assert_not_called()

    def test_rolled_back_activation_is_not_reported_as_pending(self):
        self.manager.job.side_effect = [{'status': 'ready'}, {'status': 'rolled-back'}]
        with self.assertRaisesRegex(ValueError, 'rolled-back'):
            self.run_enrollment()
        self.manager.confirm.assert_not_called()

    def test_local_health_failure_leaves_guard_armed(self):
        self.manager.job.side_effect = [{'status': 'ready'}, self.pending]
        with patch('diagnostics.wait_ready', side_effect=ValueError('health failed')), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'health failed'):
                main.enroll_key(self.manager, self.key, {})
        self.manager.confirm.assert_not_called()
        self.manager.rollback.assert_not_called()
