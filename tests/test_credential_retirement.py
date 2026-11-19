"""Credential consumers return status, and console rotation cannot echo input."""
import contextlib
import getpass
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import select
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ('installer', 'admin', 'scripts')]
import accounts
import direct
import local_access
import main
import arcane
import runtime as rt
import workflow
from credential_fixtures import SECRETS, assert_public_clean
from test_direct_install import initial

spec = importlib.util.spec_from_file_location('retirement_installer', BASE / 'installer/install.py')
installer = importlib.util.module_from_spec(spec); spec.loader.exec_module(installer)


class RetirementTests(unittest.TestCase):
    def test_doctor_does_not_export_legacy_exception_details(self):
        import diagnostics
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), \
                patch.object(diagnostics, 'probe', return_value={'ok': True}), patch.object(diagnostics, 'clock_status', return_value={}), \
                patch.object(diagnostics.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='LoadState=not-found\n')):
            rt.write_json(rt.STATE / 'active.json', {'id': 'a' * 32})
            rt.write_json(rt.STATE / 'jobs' / ('a' * 32) / 'job.json', {
                'id': 'a' * 32, 'status': 'failed', 'error': SECRETS['setup'], 'rollbackError': SECRETS['operator']})
            report = diagnostics.doctor({})
            assert_public_clean(self, [report, (rt.STATE / 'last-health.json').read_text()])

    def test_network_repair_cleanup_does_not_print_child_errors(self):
        import network_repair
        for success in (False, True):
            with self.subTest(success=success), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                rt.atomic(root / 'dropin', '')
                manager = Mock(state=root); manager.status.return_value = {}
                repair = network_repair.Repair(manager); repair.record = {'profiles': []}
                output = io.StringIO()
                with patch.object(network_repair, 'DROPIN', root / 'dropin'), \
                        patch.object(repair, 'native_identity', side_effect=ValueError(SECRETS['staged'])), \
                        patch.object(repair, 'validate_live', side_effect=ValueError(SECRETS['arcane'])), \
                        patch.object(network_repair, 'check_profiles'), patch.object(network_repair, 'profiles', return_value=[]), \
                        contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    if success:
                        with self.assertRaises(ValueError) as failed: repair.finish(True)
                        assert_public_clean(self, str(failed.exception))
                    else: repair.finish(False)
                assert_public_clean(self, [output.getvalue(), (root / 'network-repair.json').read_text()])

    def test_summary_refuses_the_retired_password_argument_before_any_output(self):
        output = io.StringIO()
        with patch.object(installer, 'run') as command, patch.object(arcane, 'authentication_ready') as probe, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(ValueError) as caught:
                installer.summarize(initial(), SECRETS['setup'])
        command.assert_not_called(); probe.assert_not_called()
        self.assertEqual(output.getvalue(), '')
        assert_public_clean(self, str(caught.exception))

    def test_plaintext_retrieval_helpers_are_retired(self):
        self.assertFalse(hasattr(arcane, 'credentials'))
        self.assertFalse(hasattr(local_access, 'operator_password'))
        self.assertFalse(hasattr(installer, 'setup_password'))

    def test_arcane_readiness_needs_no_retained_password(self):
        with patch.object(arcane,'credential_status',return_value={'state':'initialized'}), \
                patch.object(arcane,'guest',return_value=json.dumps({'ok':True})) as request:
            result=arcane.authentication_ready(initial())
            assert_public_clean(self,result)
            self.assertIs(result,True)
            self.assertEqual(request.call_args.args,('nixlab-arcane-credentials','verified'))

    def test_unexpected_arcane_response_shapes_are_not_authentication(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), \
                patch.dict(rt.SECRET_PATHS, arcanePassword=Path(folder) / 'password'), \
                patch('diagnostics.management_address', return_value='192.0.2.20'):
            rt.atomic(rt.SECRET_PATHS['arcanePassword'], SECRETS['arcane'])
            for body in (b'not-json', b'[]', b'null', b'{"jwt": true}', b'{"jwt": ""}'):
                with self.subTest(body=body), patch('diagnostics.request', return_value=(200, body)):
                    self.assertIs(arcane.authentication_ready(initial()), False)

    def test_existing_private_bootstrap_input_is_not_regenerated_or_returned(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), \
                patch.object(installer.secrets, 'token_urlsafe') as generate:
            rt.atomic(rt.STATE / 'setup-password', SECRETS['setup'])
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                result = installer.ensure_portal_account()
            generate.assert_not_called()
            self.assertTrue(result is None, 'Credential initialization must not return data')
            self.assertTrue(accounts.matches('admin', SECRETS['setup']))
            original = (rt.STATE / 'auth.json').read_bytes()
            self.assertTrue(installer.ensure_portal_account() is None, 'Credential initialization must not return data')
            self.assertTrue((rt.STATE / 'auth.json').read_bytes() == original, 'Existing verifier changed unexpectedly')
            self.assertTrue((rt.STATE / 'setup-password').exists())
            assert_public_clean(self, [result, output.getvalue()])

    def test_supplied_password_commit_retires_setup_plaintext_and_revokes_old_session(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), \
                patch.object(rt.grp, 'getgrnam', return_value=Mock(gr_gid=os.getgid())):
            rt.atomic(rt.STATE / 'setup-password', SECRETS['setup'])
            rt.write_json(rt.STATE / 'auth.json', rt.password_record(SECRETS['setup']))
            auth = main.Auth()
            token, _ = auth.login(SECRETS['setup'], '192.0.2.20')
            result = accounts.change_password('admin', SECRETS['operator'])
            self.assertFalse((rt.STATE / 'setup-password').exists())
            self.assertTrue(accounts.matches('admin', SECRETS['operator']))
            self.assertFalse(accounts.matches('admin', SECRETS['setup']))
            self.assertTrue(auth.get('nixlab_session=' + token) is None, 'Old session remained valid')
            assert_public_clean(self, result)

    def test_failed_replacement_write_preserves_legacy_access(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)):
            rt.atomic(rt.STATE / 'setup-password', SECRETS['setup'])
            rt.write_json(rt.STATE / 'auth.json', rt.password_record(SECRETS['setup']))
            original = (rt.STATE / 'auth.json').read_bytes()
            with patch.object(rt, 'write_json', side_effect=OSError(SECRETS['setup'])):
                with self.assertRaises(OSError): accounts.change_password('admin', SECRETS['operator'])
            self.assertTrue((rt.STATE / 'auth.json').read_bytes() == original, 'Existing verifier changed unexpectedly')
            self.assertTrue((rt.STATE / 'setup-password').read_text() == SECRETS['setup'], 'Legacy private input changed unexpectedly')
            self.assertTrue(accounts.matches('admin', SECRETS['setup']))

    def test_resume_failure_hides_legacy_logs_and_exception_values(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)):
            root = Path(folder)
            rt.atomic(root / 'build.log', SECRETS['setup'] + '\nHost command started\n')
            manager = Mock()
            manager.job_path.return_value = root
            manager.job.return_value = {'status': 'failed', 'error': SECRETS['arcane'], 'rollbackError': SECRETS['operator']}
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaises(ValueError) as failed: direct.wait_for(manager, 'a' * 32, {'ready'}, timeout=1)
                rt.write_json(root / 'pending.json', {'id': 'a' * 32})
                with patch('worker.rollback', side_effect=ValueError(SECRETS['staged'])):
                    with self.assertRaises(ValueError) as recovery:
                        workflow.finish_interrupted_recovery(manager, {'id': 'a' * 32, 'status': 'failed', 'error': SECRETS['setup']})
            assert_public_clean(self, [output.getvalue(), str(failed.exception), str(recovery.exception)])
            self.assertTrue(recovery.exception.__suppress_context__)

    def test_update_entrypoint_does_not_echo_domain_errors(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['nixlab-update']), patch.object(os, 'umask'), \
                patch.object(os, 'geteuid', side_effect=OSError(SECRETS['staged'])), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(BASE / 'installer/update.py'), run_name='__main__')
        assert_public_clean(self, [output.getvalue(), str(stopped.exception)])
        self.assertIn('Update stopped', str(stopped.exception))


class ConsoleRotationTests(unittest.TestCase):
    def test_nonroot_or_nonterminal_cannot_reach_password_input(self):
        for uid, terminal in ((1001, True), (0, False)):
            with self.subTest(uid=uid, terminal=terminal), patch.object(main.os, 'geteuid', return_value=uid), \
                    patch.object(main.sys.stdin, 'isatty', return_value=terminal), patch.object(getpass, 'getpass') as prompt:
                with self.assertRaises(SystemExit): main.reset_portal_password()
                prompt.assert_not_called()

    def test_echo_fallback_and_write_errors_are_bounded_without_disclosure(self):
        def warning(*args): warnings.warn(SECRETS['setup'], getpass.GetPassWarning)
        for failure in ('echo', 'write'):
            output = io.StringIO()
            with self.subTest(failure=failure), patch.object(main.os, 'geteuid', return_value=0), \
                    patch.object(main.sys.stdin, 'isatty', return_value=True), \
                    patch.object(getpass, 'getpass', side_effect=warning if failure == 'echo' else None, return_value=SECRETS['setup']), \
                    patch.object(accounts, 'recover_password', side_effect=OSError(SECRETS['operator'])) as change, \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaises(SystemExit) as stopped: main.reset_portal_password()
            assert_public_clean(self, [output.getvalue(), str(stopped.exception)])
            if failure == 'echo': change.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'POSIX controlling terminal required')
    def test_real_terminal_disables_echo_and_commits_supplied_password(self):
        import pty
        import termios
        code = '''
import fcntl, os, termios, types
os.setsid()
fcntl.ioctl(0, termios.TIOCSCTTY, 0)
import main
# Only emulate CLI authorization on non-root CI; storage still uses the real uid.
main.os = types.SimpleNamespace(geteuid=lambda: 0)
import accounts
accounts.local_root = lambda: True
main.reset_portal_password()
'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rt.atomic(root / 'setup-password', SECRETS['setup'])
            master, slave = pty.openpty()
            process = subprocess.Popen([sys.executable, '-c', code], stdin=slave, stdout=slave, stderr=slave,
                                       env=os.environ | {'NIXLAB_STATE': folder, 'NIXLAB_TEST': '1',
                                                         'PYTHONPATH': str(BASE / 'admin') + os.pathsep + str(BASE / 'scripts')})
            output = bytearray()
            try:
                deadline = time.monotonic() + 10
                while b'characters): ' not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.2)[0]: output.extend(os.read(master, 4096))
                    if process.poll() is not None: break
                assert_public_clean(self, bytes(output))
                self.assertTrue(b'characters): ' in output, 'Private console prompt unavailable')
                self.assertFalse(termios.tcgetattr(slave)[3] & termios.ECHO)
                os.write(master, SECRETS['operator'].encode() + b'\n')
                process.wait(timeout=10)
                while select.select([master], [], [], 0.1)[0]: output.extend(os.read(master, 4096))
                assert_public_clean(self, bytes(output))
                self.assertEqual(process.returncode, 0)
                self.assertFalse((root / 'setup-password').exists())
                with patch.object(rt, 'STATE', root):
                    self.assertTrue(accounts.matches('admin', SECRETS['operator']))
            finally:
                if process.poll() is None: process.kill(); process.wait(timeout=5)
                os.close(master); os.close(slave)
