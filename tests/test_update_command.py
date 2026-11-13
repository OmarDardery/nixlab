"""Single-command updates retain pinned revisions, ownership and rollback gates."""
import contextlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / p) for p in ('installer', 'admin', 'scripts')]
spec = importlib.util.spec_from_file_location('update_command', BASE / 'installer/update.py')
update = importlib.util.module_from_spec(spec); spec.loader.exec_module(update)
SHA = 'a' * 40
ID = 'b' * 32


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.manager = Mock()
        self.manager.status.return_value = {'setupComplete': True, 'release': {'revision': 'c' * 40}}
        self.ready = {'id': ID, 'status': 'ready', 'platformRevision': SHA, 'principal': update.PRINCIPAL,
                      'impactPlan': {'blocked': False, 'changes': [{'message': 'Service restart'}]}}
        self.pending = {**self.ready, 'status': 'awaiting-confirmation', 'effectiveSettings': {}, 'deadline': 2000000000}
        self.manager.review.return_value = {'id': ID}
        self.manager.job.return_value = self.ready
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.resolve = self.stack.enter_context(patch.object(update, 'main_revision', return_value=SHA))
        self.wait = self.stack.enter_context(patch.object(update.direct, 'wait_for', return_value=self.pending))
        self.health = self.stack.enter_context(patch.object(update.diagnostics, 'wait_ready'))
        self.ask = self.stack.enter_context(patch.object(update, 'ask', return_value=True))
        self.stack.enter_context(patch('builtins.print'))

    def test_latest_main_is_pinned_once_without_changing_settings(self):
        update.run(self.manager)
        self.resolve.assert_called_once_with()
        self.manager.review.assert_called_once_with({}, revision=SHA, principal=update.PRINCIPAL)
        self.manager.apply.assert_called_once_with(ID)
        self.health.assert_called_once()
        self.manager.confirm.assert_called_once_with(ID)
        self.manager.rollback.assert_not_called()

    def test_declined_activation_discards_only_unapplied_candidate(self):
        self.ask.return_value = False
        with self.assertRaisesRegex(ValueError, 'cancelled'): update.run(self.manager)
        self.manager.cancel.assert_called_once_with(ID)
        self.manager.apply.assert_not_called(); self.manager.confirm.assert_not_called()

    def test_blocked_or_absent_impact_never_activates(self):
        for plan in ({'blocked': True}, None, {}):
            with self.subTest(plan=plan):
                self.manager.job.return_value = {**self.ready, 'impactPlan': plan}
                with self.assertRaisesRegex(ValueError, 'impact plan'): update.run(self.manager)
        self.manager.apply.assert_not_called(); self.manager.confirm.assert_not_called()

    def test_build_failure_never_applies_or_confirms(self):
        self.manager.job.return_value = {**self.ready, 'status': 'building'}
        self.wait.side_effect = ValueError('Build failed')
        with self.assertRaisesRegex(ValueError, 'Build failed'): update.run(self.manager)
        self.manager.apply.assert_not_called(); self.manager.confirm.assert_not_called()

    def test_disconnect_during_build_can_resume_same_pinned_transaction(self):
        self.manager.status.return_value = {'job': {**self.ready, 'status': 'building'}}
        self.wait.side_effect = [self.ready, self.pending]
        update.run(self.manager)
        self.resolve.assert_not_called(); self.manager.review.assert_not_called()
        self.manager.apply.assert_called_once_with(ID); self.manager.confirm.assert_called_once_with(ID)

    def test_resume_applying_or_awaiting_confirmation_never_reapplies(self):
        for phase in ('applying', 'awaiting-confirmation'):
            with self.subTest(phase=phase):
                self.manager.status.return_value = {'job': {**self.pending, 'status': phase}}
                update.run(self.manager)
        self.resolve.assert_not_called(); self.manager.review.assert_not_called(); self.manager.apply.assert_not_called()
        self.assertEqual(self.manager.confirm.call_count, 2)

    def test_other_configuration_and_unfinished_recovery_are_not_adopted(self):
        for state in ({'job': {**self.ready, 'principal': 'root'}}, {'pending': {'id': ID}}):
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'first'):
                self.manager.status.return_value = state
                update.run(self.manager)
        self.manager.review.assert_not_called(); self.manager.apply.assert_not_called()

    def test_same_revision_is_an_idempotent_noop(self):
        self.manager.status.return_value['release']['revision'] = SHA
        update.run(self.manager)
        self.manager.review.assert_not_called(); self.manager.apply.assert_not_called(); self.ask.assert_not_called()

    def test_failed_health_or_rejected_access_rolls_back_without_confirmation(self):
        self.manager.job.side_effect = [self.ready, self.pending]
        self.health.side_effect = ValueError('Health failed')
        with self.assertRaisesRegex(ValueError, 'Health failed'): update.run(self.manager)
        self.manager.rollback.assert_called_once_with(ID); self.manager.confirm.assert_not_called()
        self.health.side_effect = None; self.manager.reset_mock()
        self.manager.job.side_effect = [self.ready, self.pending]
        self.ask.side_effect = [True, False]
        with self.assertRaisesRegex(ValueError, 'not confirmed'): update.run(self.manager)
        self.manager.rollback.assert_called_once_with(ID); self.manager.confirm.assert_not_called()

    def test_main_resolution_is_bounded_and_rejects_other_refs(self):
        # Exercise the actual resolver independently of the orchestration mock.
        actual = importlib.util.module_from_spec(spec); spec.loader.exec_module(actual)
        for output, valid in ((SHA + '\trefs/heads/main\n', True), (SHA + '\trefs/tags/main\n', False),
                              ('invalid\trefs/heads/main\n', False), ('', False),
                              ((SHA + '\trefs/heads/main\n') * 2, False)):
            with self.subTest(output=output), patch.object(actual.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)) as git:
                if valid: self.assertEqual(actual.main_revision(), SHA)
                else:
                    with self.assertRaises(ValueError): actual.main_revision()
                self.assertEqual(git.call_args.kwargs['timeout'], 30)
                self.assertTrue(git.call_args.kwargs['check'])

    def test_bootstrap_runtime_is_pinned_in_existing_job_retention(self):
        with patch.object(update.rt, 'SOURCE', Path('/nix/store/example-installer-source/admin')), \
                patch.object(update.rt.subprocess, 'run') as command:
            update.rt.pin_worker_runtime(Path('/var/lib/nixlab-admin/jobs') / ID)
        args = command.call_args.args[0]
        self.assertEqual(args[-1], '/nix/store/example-installer-source')
        self.assertEqual(args[2], '/var/lib/nixlab-admin/jobs/' + ID + '/worker-runtime')
        self.assertTrue(command.call_args.kwargs['check'])
        self.assertEqual(command.call_args.kwargs['timeout'], 30)
        with patch.object(update.rt, 'SOURCE', Path('/workspace/source/admin')), \
                patch.object(update.rt.subprocess, 'run') as command:
            update.rt.pin_worker_runtime(Path('/tmp/job'))
            command.assert_not_called()


if __name__ == '__main__': unittest.main()
