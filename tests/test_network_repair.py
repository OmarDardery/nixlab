"""Failure boundaries of console-only management recovery, using public fixtures."""
import contextlib
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / p) for p in ('installer', 'admin', 'scripts')]
import network_repair as repair
from test_update_command import management_settings, update

BRIDGE = {'uuid': '11111111-1111-4111-8111-111111111111', 'id': 'management-bridge',
          'interface': 'br0', 'type': 'bridge', 'controller': '', 'auto': 'no'}
PORT = {'uuid': '22222222-2222-4222-8222-222222222222', 'id': 'management-port',
        'interface': 'eth0', 'type': '802-3-ethernet', 'controller': BRIDGE['uuid'], 'auto': 'no'}
LINKS = [{'ifname': 'eth0', 'master': 'br0'}, {'ifname': 'br0'}]


class SelectionTests(unittest.TestCase):
    def settings(self):
        settings = management_settings(); settings['management']['configure'] = False
        return settings

    def test_follows_active_bridge_and_retains_exact_profile_identities(self):
        self.assertEqual(repair.select(self.settings(), LINKS, [BRIDGE, PORT]), ('br0', [BRIDGE, PORT]))

    def test_follows_saved_port_when_bridge_is_down_or_absent(self):
        self.assertEqual(repair.select(self.settings(), [{'ifname': 'eth0'}], [BRIDGE, PORT]),
                         ('br0', [BRIDGE, PORT]))

    def test_refuses_ambiguous_missing_or_non_ethernet_ports(self):
        for connections in ([BRIDGE], [BRIDGE, PORT, dict(BRIDGE)],
                            [BRIDGE, dict(PORT, type='wifi')],
                            [BRIDGE, dict(PORT, interface='missing')],
                            [BRIDGE, PORT, dict(BRIDGE, uuid=PORT['uuid'], interface='other')]):
            with self.subTest(connections=connections), self.assertRaises(ValueError):
                repair.select(self.settings(), LINKS, connections)

    def test_never_adopts_guest_bridge_or_changes_managed_network_ownership(self):
        settings = self.settings(); settings['connectivity']['guestInterfaces'].append('br0')
        with self.assertRaisesRegex(ValueError, 'guest network'): repair.select(settings, LINKS, [BRIDGE, PORT])
        settings = self.settings(); settings['management']['configure'] = True
        with self.assertRaisesRegex(ValueError, 'preserves existing'): repair.select(settings, LINKS, [BRIDGE, PORT])

    def test_native_shutdown_hook_is_started_without_restarting_guests(self):
        with patch.object(repair, 'run', return_value=Mock(stdout='loaded')) as run:
            repair.start_incus()
        self.assertEqual(run.call_args.args[0], ['systemctl', 'start', 'incus.service', 'incus-startup.service'])
        with patch.object(repair, 'run', return_value=Mock(stdout='not-found')) as run:
            repair.start_incus()
        self.assertEqual(run.call_args.args[0], ['systemctl', 'start', 'incus.service'])
        with patch.object(repair, 'run', return_value=Mock(stdout='masked')) as run:
            with self.assertRaisesRegex(ValueError, 'shutdown hook needs review'): repair.start_incus()
        self.assertEqual(run.call_count, 1)

    def test_active_bridge_and_ports_are_never_reactivated(self):
        observed = BRIDGE['uuid'] + '\n' + PORT['uuid'] + '\n'
        with patch.object(repair, 'run', side_effect=lambda args, **kwargs:
                Mock(stdout=observed if '--active' in args else '100 (connected)')) as run:
            repair.activate([BRIDGE, PORT])
        self.assertFalse(any('up' in call.args[0] for call in run.call_args_list))

    def test_bridge_readiness_is_observed_without_repeated_activation(self):
        state = iter(['70 (connecting)', '100 (connected)'])
        with patch.object(repair, 'run', side_effect=lambda args, **kwargs:
                Mock(stdout=next(state) if 'GENERAL.STATE' in args else '')) as run, \
                patch.object(repair.time, 'sleep'):
            repair.activate([BRIDGE, PORT])
        self.assertEqual(sum('up' in call.args[0] for call in run.call_args_list), 2)
        with patch.object(repair, 'run', return_value=Mock(stdout='70 (connecting)')), \
                patch.object(repair.time, 'monotonic', side_effect=[0, 61]):
            with self.assertRaisesRegex(ValueError, 'did not become ready'):
                repair.activate([BRIDGE, PORT])
        for observed in ('120 (failed)', '', 'unexpected'):
            with patch.object(repair, 'run', return_value=Mock(stdout=observed)):
                with self.assertRaisesRegex(ValueError, 'did not become ready'):
                    repair.activate([BRIDGE, PORT])

    def test_plain_ethernet_is_supported_without_creating_a_bridge(self):
        port = dict(PORT, controller='')
        self.assertEqual(repair.select(self.settings(), [{'ifname': 'eth0'}], [port]), ('eth0', [port]))

    def test_changed_uuid_or_profile_binding_is_not_reused(self):
        for key in ('uuid', 'interface', 'type', 'controller', 'id'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'changed identity'):
                repair.check_profiles([PORT], [dict(PORT, **{key: 'changed'})])
        repair.check_profiles([PORT], [dict(PORT, auto='yes')])

    def test_native_profile_parser_has_bounded_commands_and_no_secret_fields(self):
        replies = [Mock(stdout=BRIDGE['uuid'] + '\n'), Mock(stdout='\n'.join(BRIDGE.values()) + '\n')]
        # Metadata order is independent of our fixture dictionary insertion order.
        replies[1].stdout = '\n'.join(BRIDGE[key] for key in ('uuid', 'id', 'type', 'interface', 'controller', 'auto')) + '\n'
        with patch.object(repair, 'run', side_effect=replies) as run:
            self.assertEqual(repair.profiles(), [BRIDGE])
        self.assertNotIn('password', str(run.call_args_list))
        self.assertNotIn('--show-secrets', str(run.call_args_list))

    def test_profile_parser_refuses_unstructured_or_multiline_metadata(self):
        for stdout in ('bad', BRIDGE['uuid'] + '\nextra'):
            with patch.object(repair, 'run', side_effect=[Mock(stdout=BRIDGE['uuid']), Mock(stdout=stdout)]):
                with self.assertRaises(ValueError): repair.profiles()

    def test_profile_names_cannot_inject_terminal_control_sequences(self):
        values = dict(BRIDGE, id='unsafe\x1b[2J')
        reply = '\n'.join(values[key] for key in ('uuid', 'id', 'type', 'interface', 'controller', 'auto'))
        with patch.object(repair, 'run', side_effect=[Mock(stdout=BRIDGE['uuid']), Mock(stdout=reply)]):
            with self.assertRaisesRegex(ValueError, 'Ambiguous NetworkManager'):
                repair.profiles()

    def test_profile_observation_has_a_total_deadline_and_size_limit(self):
        with patch.object(repair, 'run', return_value=Mock(stdout='\n'.join([BRIDGE['uuid']] * 129))):
            with self.assertRaisesRegex(ValueError, 'profile limit exceeded'): repair.profiles()
        with patch.object(repair, 'run', return_value=Mock(stdout=BRIDGE['uuid'])), \
                patch.object(repair.time, 'monotonic', side_effect=[0, 31]):
            with self.assertRaisesRegex(ValueError, 'exceeded its deadline'): repair.profiles()

    def test_live_bridge_membership_must_be_represented_by_saved_port_profiles(self):
        with self.assertRaisesRegex(ValueError, 'Live bridge membership differs'):
            repair.select(self.settings(), LINKS + [{'ifname': 'eth9'}], [BRIDGE, dict(PORT, interface='eth9')])

    def test_only_selected_source_profiles_receive_a_reviewable_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            declared = {'bridge': {'id': BRIDGE['id'], 'interface': 'br0', 'auto': False},
                        'port': {'id': PORT['id'], 'interface': 'eth0', 'auto': False},
                        'other': {'id': 'other', 'interface': 'eth9', 'auto': False}}
            with patch.object(repair, 'declared_profiles', return_value=declared):
                modules, names = repair.modules_for(root, [BRIDGE, PORT])
            self.assertEqual(names, ['bridge', 'port'])
            content = modules[repair.MODULE]
            self.assertIn('lib.mkOverride 90 true', content)
            self.assertNotIn('other', content)
            self.assertNotIn('password', content)
            self.assertNotIn('ipv4', content)

    def test_no_override_for_native_or_already_enabled_profiles(self):
        with patch.object(repair, 'declared_profiles', return_value={}):
            self.assertEqual(repair.modules_for(BASE, [BRIDGE, PORT]), ({}, []))
        with patch.object(repair, 'declared_profiles', return_value={
                'bridge': {'id': BRIDGE['id'], 'interface': 'br0', 'auto': True}}):
            self.assertEqual(repair.modules_for(BASE, [BRIDGE]), ({}, []))

    def test_unsafe_nix_profile_names_cannot_enter_generated_source(self):
        with patch.object(repair, 'declared_profiles', return_value={
                '${bad}': {'id': BRIDGE['id'], 'interface': 'br0', 'auto': False}}):
            with self.assertRaisesRegex(ValueError, 'Unusual'): repair.modules_for(BASE, [BRIDGE])

    def test_saved_settings_are_corrected_even_if_test_activation_already_matches(self):
        saved = self.settings(); effective = copy.deepcopy(saved)
        effective['management'].update(interface='br0', address='192.0.2.20')
        with patch.object(update, 'inventory', return_value={'interfaces': [
                {'name': 'br0', 'addresses': ['192.0.2.20/24']}]}):
            self.assertEqual(update.management_change(effective, 'br0', saved)['management']['interface'], 'br0')
            self.assertEqual(update.management_change(effective, 'br0', effective), {})


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.manager = Mock(state=self.folder, root=self.folder / 'source')
        settings = management_settings(); settings['management']['configure'] = False
        self.manager.effective_settings.return_value = settings
        self.manager.status.return_value = {'setupComplete': True}
        self.stack.enter_context(patch.object(repair.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name))
        self.stack.enter_context(patch.object(repair, 'DROPIN', self.folder / 'runtime.conf'))
        database = self.folder / 'local.db'; database.write_bytes(b'initialized fixture')
        self.stack.enter_context(patch.object(repair, 'DATABASE', database))
        self.stack.enter_context(patch.object(repair.workflow, 'remote_session', return_value=False))
        self.stack.enter_context(patch.object(repair.rt, 'locked', return_value=contextlib.nullcontext()))
        self.stack.enter_context(patch.object(repair.rt, 'digest_source', return_value='source-digest'))
        self.stack.enter_context(patch.object(repair.rt, 'pin_worker_runtime'))
        self.identity = self.stack.enter_context(patch.object(repair.diagnostics, 'identity'))
        self.observe = self.stack.enter_context(patch.object(repair, 'profiles', return_value=[BRIDGE, PORT]))
        self.plan = self.stack.enter_context(patch.object(repair, 'modules_for', return_value=({}, [])))
        self.commands = self.stack.enter_context(patch.object(repair, 'run', side_effect=lambda args, **kwargs:
            Mock(stdout=json.dumps(LINKS) if args[0] == 'ip' else 'loaded' if '--value' in args
                 else '100 (connected)' if 'GENERAL.STATE' in args else '' if '--active' in args
                 else 'Id=incus.service\nActiveState=active\n')))
        self.stack.enter_context(patch('builtins.print'))
        self.choose = Mock(return_value={'management': {'interface': 'br0', 'address': '192.0.2.20', 'prefixLength': 24}})
        self.repair = repair.Repair(self.manager)

    def prepare(self):
        return self.repair.prepare(lambda _: True, self.choose)

    def test_console_only_and_cancelled_requests_do_not_change_profiles(self):
        with patch.object(repair.workflow, 'remote_session', return_value=True):
            with self.assertRaisesRegex(ValueError, 'local host console'): self.prepare()
        self.commands.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            self.repair.prepare(lambda _: False, self.choose)
        self.assertFalse(repair.DROPIN.exists())
        self.assertFalse(any('modify' in call.args[0] for call in self.commands.call_args_list))

    def test_unrelated_pending_activation_is_never_adopted(self):
        self.manager.status.return_value['pending'] = {'id': 'other'}
        with self.assertRaisesRegex(ValueError, 'pending activation'): self.prepare()
        self.commands.assert_not_called()

    def test_uninitialized_incus_is_not_recreated(self):
        with patch.object(repair, 'DATABASE', self.folder / 'absent'):
            with self.assertRaisesRegex(ValueError, 'existing Incus database'): self.prepare()
        self.commands.assert_not_called()

    def test_prepare_starts_controller_asynchronously_then_ports_and_unlocks_real_incus(self):
        self.assertEqual(self.prepare(), 'br0')
        commands = [call.args[0] for call in self.commands.call_args_list]
        ups = [command[-1] for command in commands if 'up' in command]
        self.assertEqual(ups, [BRIDGE['uuid'], PORT['uuid']])
        self.assertIn(['nmcli', '--wait', '0', 'connection', 'up', 'uuid', BRIDGE['uuid']], commands)
        self.assertIn(['systemctl', 'start', 'nixlab-management-identity.service'], commands)
        self.assertEqual(self.identity.call_args.args[0]['management']['interface'], 'br0')
        self.assertEqual(commands[-1], ['systemctl', 'start', 'incus.service', 'incus-startup.service'])
        self.assertEqual(self.repair.record['phase'], 'reviewing')
        self.assertTrue(repair.DROPIN.is_file())
        self.assertFalse(any('nft' in command or 'preseed' in command or 'init' in command for command in commands))
        self.assertFalse(self.manager.review.called)

    def test_active_guest_deployment_blocks_preflight_before_network_changes(self):
        repair.rt.write_json(self.folder / 'deployment-active.json', {'id': 'active'})
        with self.assertRaisesRegex(ValueError, 'active guest deployment'): self.prepare()
        self.commands.assert_not_called()
        self.assertFalse(repair.DROPIN.exists())

    def test_resume_awaiting_confirmation_does_not_restart_identity_or_dependents(self):
        self.prepare()
        self.repair.reviewed({'id': 'a' * 32})
        self.manager.status.return_value.update(
            pending={'id': 'a' * 32}, job={'id': 'a' * 32, 'principal': 'root-update-command',
                                         'status': 'awaiting-confirmation'})
        self.commands.reset_mock()
        again = repair.Repair(self.manager)
        again.prepare(lambda _: self.fail('Already authorized'), self.choose)
        self.assertFalse(any('restart' in call.args[0] for call in self.commands.call_args_list))
        self.identity.assert_called()

    def test_connectivity_is_restored_before_nix_input_evaluation(self):
        def evaluate(root, selected):
            self.assertTrue(any('GENERAL.STATE' in call.args[0] for call in self.commands.call_args_list))
            self.choose.assert_called_once()
            return {}, []
        self.plan.side_effect = evaluate
        self.prepare()
        self.plan.assert_called_once()
        self.assertEqual(self.repair.record['modules'], {})

    def test_interrupted_preflight_resumes_same_checkpoint(self):
        self.prepare()
        checkpoint = json.loads(self.repair.path.read_text())
        again = repair.Repair(self.manager)
        self.assertEqual(again.prepare(lambda _: self.fail('Must not create another checkpoint'), self.choose), 'br0')
        self.assertEqual(again.record['sourceDigest'], checkpoint['sourceDigest'])
        self.assertEqual(again.record['profiles'], checkpoint['profiles'])

    def test_resume_reuses_pinned_identity_when_a_new_launcher_is_downloaded(self):
        self.prepare()
        previous = repair.DROPIN.read_text()
        again = repair.Repair(self.manager)
        with patch.object(repair, 'dropin', return_value='different launcher'):
            again.prepare(lambda _: self.fail('Already authorized'), self.choose)
            self.assertEqual(repair.DROPIN.read_text(), previous)
            self.observe.return_value = [dict(p, auto='yes') for p in (BRIDGE, PORT)]
            again.native_identity()
        self.assertFalse(repair.DROPIN.exists())

    def test_source_edits_and_profile_rebinding_refuse_resume(self):
        self.prepare()
        with patch.object(repair.rt, 'digest_source', return_value='edited'):
            with self.assertRaisesRegex(ValueError, 'Source changed'):
                repair.Repair(self.manager).prepare(lambda _: True, self.choose)
        self.observe.return_value = [BRIDGE, dict(PORT, interface='other')]
        with self.assertRaisesRegex(ValueError, 'changed identity'):
            repair.Repair(self.manager).prepare(lambda _: True, self.choose)

    def test_native_identity_and_autoconnect_are_verified_without_override_before_confirmation(self):
        self.prepare()
        self.observe.return_value = [dict(p, auto='yes') for p in (BRIDGE, PORT)]
        self.repair.native_identity()
        self.assertFalse(repair.DROPIN.exists())
        self.repair.finish(True)
        self.assertEqual(json.loads(self.repair.path.read_text())['phase'], 'completed')

    def test_identity_restart_restores_active_services_without_starting_disabled_ones(self):
        self.prepare()
        self.observe.return_value = [dict(p, auto='yes') for p in (BRIDGE, PORT)]
        self.commands.side_effect = lambda args, **kwargs: Mock(stdout='loaded' if '--value' in args else (
            'Id=incus.service\nActiveState=active\n\n'
            'Id=nixlab-ui.service\nActiveState=active\n\n'
            'Id=nginx.service\nActiveState=inactive\n'))
        self.repair.native_identity()
        self.assertTrue(any(call.args[0] == ['systemctl', 'start', 'incus.service', 'nixlab-ui.service']
                            for call in self.commands.call_args_list))

    def test_missing_dependency_observation_keeps_bootstrap_identity_for_inspection(self):
        self.prepare()
        self.commands.return_value = Mock(stdout='')
        self.commands.side_effect = None
        with self.assertRaisesRegex(ValueError, 'Cannot observe Incus'):
            self.repair.native_identity()
        self.assertTrue(repair.DROPIN.exists())

    def test_unknown_checkpoint_is_not_silently_replaced(self):
        repair.rt.write_json(self.repair.path, {'format': 99, 'phase': 'prepared'})
        with self.assertRaisesRegex(ValueError, 'Unknown network recovery checkpoint'):
            self.prepare()
        self.commands.assert_not_called()

    def test_drift_or_foreign_dropin_cannot_be_hidden_by_recovery(self):
        self.prepare()
        repair.DROPIN.write_text('foreign override')
        with self.assertRaisesRegex(ValueError, 'override changed'): self.repair.native_identity()
        self.assertEqual(repair.DROPIN.read_text(), 'foreign override')

    def test_disabled_autoconnect_after_activation_fails_before_confirmation(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'autoconnect disabled'): self.repair.native_identity()

    def test_failed_review_restores_previous_autoconnect_flags_and_preserves_primary_failure(self):
        self.prepare()
        # The original broken identity may still fail on rollback; report it,
        # but restore every selected native profile rather than masking the error.
        self.commands.side_effect = subprocess.CalledProcessError(1, ['systemctl'])
        self.repair.finish(False)
        self.assertEqual(json.loads(self.repair.path.read_text())['phase'], 'needs-attention')
        self.assertTrue(any(call.args[0][-2:] == ['connection.autoconnect', 'no'] for call in self.commands.call_args_list))

    def test_background_activation_keeps_checkpoint_for_guarded_resume(self):
        self.prepare()
        self.manager.status.return_value['job'] = {'status': 'applying'}
        self.repair.finish(False)
        self.assertTrue(repair.DROPIN.exists())
        self.assertEqual(json.loads(self.repair.path.read_text())['phase'], 'reviewing')

    def test_source_autoconnect_override_prevents_activation(self):
        self.prepare()
        self.repair.record['declared'] = ['bridge']
        self.manager.job_path.return_value = self.folder
        with patch.object(repair, 'declared_profiles', return_value={'bridge': {'auto': False}}):
            with self.assertRaisesRegex(ValueError, 'prevents persistent'): self.repair.verify({'id': 'a', 'moduleChanges': []})


if __name__ == '__main__': unittest.main()
