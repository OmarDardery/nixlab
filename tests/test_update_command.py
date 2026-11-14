"""Single-command updates retain pinned revisions, ownership and rollback gates."""
import contextlib
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / p) for p in ('installer', 'admin', 'scripts')]
spec = importlib.util.spec_from_file_location('update_command', BASE / 'installer/update.py')
update = importlib.util.module_from_spec(spec); spec.loader.exec_module(update)
SHA = 'a' * 40
ID = 'b' * 32


def management_settings():
    settings = json.loads((BASE / 'tests/fixtures/defaults.json').read_text())
    settings['management'].update(interface='eth0', address='192.0.2.10', prefixLength=24,
                                  trustedCIDRs=['192.0.2.0/24'])
    return settings


class InterfaceSelectionTests(unittest.TestCase):
    def test_selects_address_owner_and_preserves_addressing_policy_and_trust(self):
        for configure in (False, True):
            settings = management_settings(); settings['management']['configure'] = configure
            original = copy.deepcopy(settings)
            with patch.object(update, 'inventory', return_value={'interfaces': [
                    {'name': 'eth0', 'addresses': []}, {'name': 'br0', 'addresses': ['192.0.2.20/24']}]}):
                self.assertEqual(update.management_change(settings, 'br0'), {'management': {
                    'interface': 'br0', 'address': '192.0.2.20', 'prefixLength': 24}})
            self.assertEqual(settings, original)

    def test_refuses_unaddressed_ambiguous_or_unusable_interfaces(self):
        for addresses in ([], ['169.254.1.2/16'], ['127.0.0.1/8'], ['2001:db8::1/64'],
                          ['0.0.0.0/0'], ['224.0.0.1/24'],
                          ['192.0.2.20/24', '192.0.2.30/24']):
            with self.subTest(addresses=addresses), patch.object(update, 'inventory', return_value={
                    'interfaces': [{'name': 'br0', 'addresses': addresses}]}):
                with self.assertRaisesRegex(ValueError, 'unambiguous usable IPv4'):
                    update.management_change(management_settings(), 'br0')
        with patch.object(update, 'inventory', return_value={'interfaces': []}):
            with self.assertRaises(ValueError): update.management_change(management_settings(), 'missing')

    def test_does_not_expand_trusted_networks(self):
        with patch.object(update, 'inventory', return_value={'interfaces': [
                {'name': 'br0', 'addresses': ['198.51.100.10/24']}]}):
            with self.assertRaisesRegex(ValueError, 'outside existing trusted'):
                update.management_change(management_settings(), 'br0')

    def test_keeps_known_address_when_bridge_has_multiple_addresses(self):
        with patch.object(update, 'inventory', return_value={'interfaces': [
                {'name': 'br0', 'addresses': ['192.0.2.20/24', '192.0.2.10/24']}]}):
            self.assertEqual(update.management_change(management_settings(), 'br0')['management']['address'], '192.0.2.10')


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

    def test_interface_correction_is_reviewed_even_when_platform_is_current(self):
        settings = management_settings()
        self.manager.effective_settings.return_value = settings
        self.manager.settings.return_value = settings
        selected = {'interface': 'br0', 'address': '192.0.2.20', 'prefixLength': 24}
        built = copy.deepcopy(settings); built['management'].update(selected)
        self.ready.update(settings=built, effectiveSettings=built)
        self.pending['effectiveSettings'] = built
        with patch.object(update, 'inventory', return_value={'interfaces': [
                {'name': 'eth0', 'addresses': []}, {'name': 'br0', 'addresses': ['192.0.2.20/24']}]}), \
                patch.object(update, 'already_current', return_value=True) as unchanged:
            update.run(self.manager, interface='br0')
        unchanged.assert_not_called()
        self.manager.review.assert_called_once_with({'management': selected}, revision=SHA, principal=update.PRINCIPAL)
        self.manager.apply.assert_called_once_with(ID)
        self.health.assert_called_once_with(built)
        self.manager.confirm.assert_called_once_with(ID)

    def test_repeated_interface_selection_is_an_idempotent_noop(self):
        settings = management_settings(); settings['management']['interface'] = 'br0'
        self.manager.effective_settings.return_value = settings
        self.manager.settings.return_value = settings
        with patch.object(update, 'inventory', return_value={'interfaces': [
                {'name': 'br0', 'addresses': ['192.0.2.10/24']}]}), \
                patch.object(update, 'already_current', return_value=True):
            update.run(self.manager, interface='br0')
        self.manager.review.assert_not_called(); self.manager.apply.assert_not_called()

    def test_resume_refuses_a_different_interface_without_changing_the_job(self):
        self.manager.status.return_value = {'job': {**self.ready, 'settings': {'management': {'interface': 'br0'}}}}
        with self.assertRaisesRegex(ValueError, 'differs from the existing reviewed update'):
            update.run(self.manager, interface='eth0')
        self.manager.review.assert_not_called(); self.manager.apply.assert_not_called()

    def test_source_override_of_requested_management_selection_never_activates(self):
        settings = management_settings(); settings['management']['interface'] = 'br0'
        self.manager.status.return_value = {'job': {**self.ready, 'settings': settings}}
        for key, value in (('interface', 'eth0'), ('address', '192.0.2.99'), ('prefixLength', 25)):
            built = copy.deepcopy(settings); built['management'][key] = value
            self.manager.status.return_value['job']['effectiveSettings'] = built
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'source-owned Nix overrides'):
                update.run(self.manager, interface='br0')
        self.manager.apply.assert_not_called()

    def test_recovery_uses_normal_review_and_removes_override_before_health_and_confirmation(self):
        recovery = Mock()
        recovery.record = {'modules': {'recovery.nix': '{ ... }: {}'}}
        order = []
        recovery.native_identity.side_effect = lambda: order.append('native')
        self.health.side_effect = lambda _: order.append('health')
        self.manager.confirm.side_effect = lambda _: order.append('confirm')
        update.run(self.manager, repair=recovery)
        self.manager.review.assert_called_once_with({}, revision=SHA, principal=update.PRINCIPAL,
                                                   modules=recovery.record['modules'])
        recovery.reviewed.assert_called_once_with({'id': ID})
        recovery.verify.assert_called_once_with(self.ready)
        self.assertEqual(order, ['native', 'health', 'confirm'])

    def test_recovery_persistence_failure_rolls_back_before_confirmation(self):
        recovery = Mock(); recovery.record = {'modules': {}}
        recovery.native_identity.side_effect = ValueError('autoconnect disabled')
        self.manager.job.side_effect = [self.ready, self.pending]
        with self.assertRaisesRegex(ValueError, 'autoconnect disabled'):
            update.run(self.manager, repair=recovery)
        self.manager.rollback.assert_called_once_with(ID)
        self.manager.confirm.assert_not_called()

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
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); active = root / 'active'; active.mkdir()
            (root / 'system').symlink_to(active)
            self.manager.job_path.return_value = root
            self.manager.status.return_value.update(release={'revision': SHA}, lastConfirmed={'id': ID}, currentSystem=str(active))
            self.manager.job.return_value = {'status': 'confirmed', 'candidateDigest': {'source': 'hash'}}
            with patch.object(update.rt, 'digest_source', return_value={'source': 'hash'}):
                update.run(self.manager)
        self.manager.review.assert_not_called(); self.manager.apply.assert_not_called(); self.ask.assert_not_called()

    def test_matching_revision_after_rollback_or_local_edit_still_updates_system(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); active = root / 'active'; active.mkdir()
            (root / 'system').symlink_to(active)
            self.manager.job_path.return_value = root
            for current, digest in ((str(root / 'old-generation'), {'source': 'hash'}),
                                    (str(active), {'source': 'edited'})):
                with self.subTest(current=current, digest=digest):
                    self.manager.reset_mock()
                    self.manager.status.return_value.update(release={'revision': SHA}, lastConfirmed={'id': ID}, currentSystem=current)
                    self.manager.job.side_effect = [{'status': 'confirmed', 'candidateDigest': {'source': 'hash'}}, self.ready]
                    with patch.object(update.rt, 'digest_source', return_value=digest):
                        update.run(self.manager)
                    self.manager.review.assert_called_once()
                    self.manager.apply.assert_called_once_with(ID)

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
        with patch.dict(update.os.environ, {'NIXLAB_WORKER_RUNTIME': '/nix/store/update-launcher'}), \
                patch.object(update.rt.subprocess, 'run') as command:
            update.rt.pin_worker_runtime(Path('/tmp/job'))
            self.assertEqual(command.call_args.args[0][-1], '/nix/store/update-launcher')
        with patch.dict(update.os.environ, {'NIXLAB_WORKER_RUNTIME': '/tmp/untrusted'}), \
                patch.object(update.rt.subprocess, 'run') as command:
            with self.assertRaises(ValueError): update.rt.pin_worker_runtime(Path('/tmp/job'))
            command.assert_not_called()
        with patch.object(update.rt, 'SOURCE', Path('/workspace/source/admin')), \
                patch.object(update.rt.subprocess, 'run') as command:
            update.rt.pin_worker_runtime(Path('/tmp/job'))
            command.assert_not_called()


if __name__ == '__main__': unittest.main()
