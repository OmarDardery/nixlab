"""Repository checks keep bounded, non-secret evidence distinct from restoration."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ('admin', 'scripts')]
import backup_verification as verification
from credential_fixtures import SECRETS, assert_public_clean

NOW = 1800000000
CLOCK = {'state': 'synchronized', 'evidenceWarning': False}
REPOSITORY = 'sftp:backup@example.invalid:/nixlab'


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / 'state.json'
        self.policy = Path(self.directory.name) / 'policy.json'
        self.settings = {'version': 1, 'enable': True, 'mode': 'metadata', 'schedule': 'weekly'}
        self.policy.write_text(json.dumps(self.settings))
        self.service = {'LoadState': 'loaded', 'ActiveState': 'inactive', 'Result': 'success'}
        self.timer = {'LoadState': 'loaded', 'ActiveState': 'active', 'LastTriggerUSec': 'n/a'}
        for item in (patch.object(verification.time, 'time', return_value=NOW),
                     patch.dict(verification.os.environ, RESTIC_REPOSITORY=REPOSITORY),
                     patch.object(verification.diagnostics, 'clock_status', return_value=CLOCK),
                     patch.object(verification.backup_health, 'properties', side_effect=lambda unit, fields:
                                  self.timer if unit.endswith('.timer') else self.service)):
            item.start()
            self.addCleanup(item.stop)

    def status(self):
        return verification.status(True, self.policy, self.state, repository=REPOSITORY)

    def check(self, mode='metadata', code=0, error=None):
        with patch.object(verification.subprocess, 'run',
                          side_effect=error, return_value=subprocess.CompletedProcess([], code)) as process:
            result = verification.check(mode, self.state)
        return result, process

    def test_metadata_and_full_modes_have_explicit_scope_and_no_output(self):
        for mode, arguments in (('metadata', []), ('full', ['--read-data'])):
            with self.subTest(mode=mode), contextlib.redirect_stdout(io.StringIO()) as output:
                result, process = self.check(mode)
                self.assertEqual(result, 0)
                process.assert_called_once_with(['restic', '--no-cache', 'check'] + arguments,
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=43140)
                status = self.status()
                self.assertEqual(status['state'], 'successful')
                self.assertEqual(status['lastMode'], mode)
                self.assertEqual(status['lastSuccess']['mode'], mode)
                self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
                assert_public_clean(self, {'status': status, 'output': output.getvalue(),
                                          'record': self.state.read_text(), 'arguments': process.call_args.args})

    def test_failed_check_dominates_previous_success_and_never_discloses_errors(self):
        self.check()
        previous = self.status()['lastSuccess']
        errors = [OSError(SECRETS['staged']),
                  subprocess.TimeoutExpired(SECRETS['private-key'], 1, output=SECRETS['portainer'])]
        for error in errors:
            with self.subTest(kind=type(error)), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(self.check('full', error=error)[0], 1)
                status = self.status()
                self.assertEqual(status['state'], 'failed')
                self.assertEqual(status['lastSuccess'], previous)
                assert_public_clean(self, {'status': status, 'record': self.state.read_text(), 'output': output.getvalue()})
        self.assertEqual(self.check(code=1)[0], 1)
        self.assertEqual(self.status()['state'], 'failed')

    def test_disabled_never_run_unknown_running_and_interrupted(self):
        self.assertEqual(verification.status(False), {'state': 'disabled'})
        self.assertEqual(self.status()['state'], 'never-run')
        self.service = {}
        self.assertEqual(self.status()['state'], 'unknown')
        self.service = {'LoadState': 'loaded', 'ActiveState': 'activating', 'Result': 'success'}
        self.assertEqual(self.status()['state'], 'running')
        self.service = {'LoadState': 'loaded', 'ActiveState': 'inactive', 'Result': 'success'}
        self.state.write_text(json.dumps({'version': 1, 'phase': 'running',
                                         'repositoryFingerprint': verification.repository_fingerprint(REPOSITORY)}))
        self.assertEqual(self.status()['state'], 'interrupted')

    def test_malformed_future_or_incomplete_records_never_report_success(self):
        for text in ('{', '[]', '{"version":2}', '{"version":true}',
                     '{"version":1,"phase":"successful","finishedAt":1800000000,"mode":"full"}',
                     '{"version":1,"phase":"successful","finishedAt":"secret","mode":[]}',
                     'x' * 20000):
            with self.subTest(size=len(text)):
                self.state.write_text(text)
                self.assertEqual(self.status()['state'], 'unknown')
        self.policy.write_text('{"version":2}')
        self.assertEqual(self.status()['state'], 'unknown')

    def test_schedule_disable_does_not_hide_a_manual_check(self):
        self.settings['enable'] = False
        self.policy.write_text(json.dumps(self.settings))
        self.assertEqual(self.status()['state'], 'disabled')
        self.check()
        status = self.status()
        self.assertEqual(status['state'], 'successful')
        self.assertFalse(status['scheduled'])

    def test_native_timer_detects_missing_schedule_and_overdue_trigger(self):
        self.check()
        self.timer['ActiveState'] = 'inactive'
        self.assertEqual(self.status()['state'], 'unknown')
        self.timer.update(ActiveState='active', LastTriggerUSec='@' + str(NOW + 1))
        self.assertEqual(self.status()['state'], 'queued')
        with patch.object(verification.time, 'time', return_value=NOW + 8000):
            self.assertEqual(self.status()['state'], 'overdue')

    def test_policy_change_cannot_relabel_a_previous_metadata_check_as_full(self):
        self.check()
        self.settings['mode'] = 'full'
        self.policy.write_text(json.dumps(self.settings))
        status = self.status()
        self.assertEqual(status['mode'], 'full')
        self.assertEqual(status['lastMode'], 'metadata')
        self.assertEqual(status['lastSuccess']['mode'], 'metadata')

    def test_repository_change_invalidates_previous_evidence_without_exposing_identity(self):
        self.check()
        status = verification.status(True, self.policy, self.state, repository='new-target-' + SECRETS['setup'])
        self.assertEqual(status['state'], 'unknown')
        self.assertNotIn('lastSuccess', status)
        self.assertNotIn('repositoryFingerprint', status)
        assert_public_clean(self, status)
        with patch.dict(verification.os.environ, RESTIC_REPOSITORY='new-target-' + SECRETS['setup']):
            self.check(code=1)
        self.assertIsNone(json.loads(self.state.read_text())['lastSuccess'])
        assert_public_clean(self, self.state.read_text())

    def test_clock_uncertainty_and_record_write_failure_are_not_hidden(self):
        with patch.object(verification.diagnostics, 'clock_status', return_value={
                'state': 'plausible-but-unsynchronized', 'evidenceWarning': True}):
            self.check()
        self.assertTrue(self.status()['timeWarning'])
        with patch.object(verification, 'check', side_effect=OSError(SECRETS['staged'])), \
                contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(verification.main(['--mode', 'metadata']), 1)
        assert_public_clean(self, {'stdout': output.getvalue(), 'stderr': error.getvalue()})
        self.assertIn('unverified', output.getvalue())
