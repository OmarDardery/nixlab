"""Browser proof binding, PAM boundaries, replay, persistent expiry and secrecy."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ('admin', 'scripts')]
import accounts
import browser_bootstrap as bootstrap
import enrollment
import host_proof
import runtime as rt
from credential_fixtures import SECRETS, assert_public_clean

ADDRESS = '192.0.2.10'
ORIGIN = 'https://192.0.2.20:9440'


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        patched = patch.object(rt, 'STATE', self.state); patched.start(); self.addCleanup(patched.stop)
        patched = patch.dict(os.environ, {'NIXLAB_TEST': '1'}); patched.start(); self.addCleanup(patched.stop)
        accounts.enable_enrollment()

    def session(self):
        return bootstrap.start(None, ADDRESS, ORIGIN)

    def fields(self, session):
        return session['token'], session['csrf'], ADDRESS, ORIGIN

    def test_host_proof_yields_one_named_admin_and_bootstrap_never_becomes_ordinary_auth(self):
        browser = self.session(); stolen_code = browser['approvalCode']
        with patch.object(host_proof, 'verify', return_value='operator') as verifier:
            self.assertEqual(bootstrap.verify(*self.fields(browser), 'operator', SECRETS['operator']), {'approved': True})
        verifier.assert_called_once_with('operator', SECRETS['operator'])
        other = self.session()
        self.assertFalse(other['approved'])
        with self.assertRaises(ValueError): bootstrap.claim(*self.fields(other), 'attacker', SECRETS['arcane'])
        self.assertEqual(bootstrap.claim(*self.fields(browser), 'owner', SECRETS['setup']), {'ok': True})
        self.assertTrue(accounts.matches('owner', SECRETS['setup']))
        self.assertFalse(accounts.matches('admin', SECRETS['setup']))
        self.assertEqual(accounts.list_users(), [{'username': 'owner', 'role': 'admin'}])
        enrollment.ensure_legacy_account()  # An update must not create a new legacy identity.
        self.assertFalse((self.state / 'auth.json').exists())
        for session in (browser, other):
            with self.assertRaises(ValueError): bootstrap.claim(*self.fields(session), 'owner', SECRETS['setup'])
            with self.assertRaises(ValueError): bootstrap.start(session['token'], ADDRESS, ORIGIN)
        self.assertEqual(enrollment.status(), {'available': False, 'recoveryRequired': False})
        artifacts = [(self.state / name).read_text() for name in ('users.json', enrollment.STATE_FILE, bootstrap.FILE, 'audit.jsonl')]
        assert_public_clean(self, artifacts)
        self.assertTrue(all(browser['token'] not in text and browser['csrf'] not in text and stolen_code not in text for text in artifacts),
                        'Stored bootstrap metadata contains reusable browser material')
        self.assertEqual((self.state / bootstrap.FILE).stat().st_mode & 0o777, 0o600)

    def test_wrong_browser_csrf_scope_expiry_boot_and_unapproved_claim_fail(self):
        browser = self.session(); other = self.session()
        calls = [
            (browser['token'], other['csrf'], ADDRESS, ORIGIN),
            (other['token'], browser['csrf'], ADDRESS, ORIGIN),
            (browser['token'], browser['csrf'], '192.0.2.11', ORIGIN),
            (browser['token'], browser['csrf'], ADDRESS, 'https://attacker.invalid'),
            ('missing', browser['csrf'], ADDRESS, ORIGIN),
        ]
        with patch.object(host_proof, 'verify') as verifier:
            for fields in calls:
                with self.assertRaises(ValueError): bootstrap.verify(*fields, 'operator', SECRETS['operator'])
            verifier.assert_not_called()
        with self.assertRaises(ValueError): bootstrap.claim(*self.fields(browser), 'owner', SECRETS['setup'])
        accounts.approve_enrollment(browser['approvalCode'])
        with patch.object(bootstrap.time, 'monotonic', return_value=time.monotonic() + 601):
            with self.assertRaises(ValueError): bootstrap.claim(*self.fields(browser), 'owner', SECRETS['setup'])
        with patch.object(enrollment, 'boot_id', return_value='new-boot'):
            with self.assertRaises(ValueError): bootstrap.claim(*self.fields(browser), 'owner', SECRETS['setup'])
        self.assertFalse(enrollment.account_valid())

    def test_root_console_approval_and_restart_keep_scope_and_deadline(self):
        browser = self.session()
        accounts.approve_enrollment(browser['approvalCode'])
        reloaded = importlib.reload(bootstrap)
        resumed = reloaded.start(browser['token'], ADDRESS, ORIGIN)
        self.assertEqual(resumed['csrf'], browser['csrf'])
        self.assertTrue(resumed['approved'])
        self.assertLessEqual(resumed['expiresIn'], browser['expiresIn'])
        self.assertEqual(reloaded.claim(*self.fields(browser), 'owner', SECRETS['setup']), {'ok': True})

    def test_rate_limits_persist_across_reloads_and_browsers(self):
        browser = self.session()
        with patch.object(host_proof, 'verify', side_effect=ValueError(SECRETS['operator'])) as verifier:
            errors = []
            for _ in range(5):
                with self.assertRaises(ValueError) as stopped: bootstrap.verify(*self.fields(browser), 'operator', SECRETS['operator'])
                errors.append(str(stopped.exception))
            browser = bootstrap.start(None, ADDRESS, ORIGIN)
            importlib.reload(bootstrap)
            with self.assertRaisesRegex(ValueError, 'Too many'): bootstrap.verify(*self.fields(browser), 'operator', SECRETS['operator'])
            self.assertEqual(verifier.call_count, 5)
            assert_public_clean(self, errors)
        self.assertFalse(enrollment.account_valid())

    def test_session_capacity_and_corrupt_future_session_state_fail_closed(self):
        for i in range(64): bootstrap.start(None, '192.0.2.' + str(i + 1), ORIGIN)
        with self.assertRaises(ValueError): bootstrap.start(None, '192.0.2.100', ORIGIN)
        for value in ('{', '{"version":2}', '{"version":1,"boot":null}'):
            rt.atomic(self.state / bootstrap.FILE, value)
            with self.assertRaises(ValueError): bootstrap.start(None, ADDRESS, ORIGIN)
        self.assertFalse(enrollment.account_valid())

    def test_two_eligible_browsers_cannot_create_two_accounts(self):
        browsers = [self.session(), self.session()]
        with patch.object(host_proof, 'verify', return_value='operator'):
            for browser in browsers: bootstrap.verify(*self.fields(browser), 'operator', SECRETS['operator'])
        def claim(pair):
            browser, name = pair
            for _ in range(5):
                try: return bootstrap.claim(*self.fields(browser), name, SECRETS['setup']) == {'ok': True}
                except rt.BusyError: time.sleep(.02)
                except ValueError: return False
            return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, zip(browsers, ['ownerone', 'ownertwo'])))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(accounts.list_users()), 1)

    def test_missing_or_corrupt_authority_never_gets_a_browser_proof(self):
        rt.atomic(self.state / enrollment.STATE_FILE, '{')
        with patch.object(host_proof, 'verify') as verifier:
            with self.assertRaises(ValueError): self.session()
            verifier.assert_not_called()


class HostProofTests(unittest.TestCase):
    def fixture(self):
        # Fixed fake system files, helper paths and local identity; never mutate host PAM.
        from contextlib import ExitStack
        stack = ExitStack(); self.addCleanup(stack.close)
        stack.enter_context(patch.object(host_proof.Path, 'read_text', return_value='operator:x:1000:100:operator:/home/operator:/bin/bash\n'))
        stack.enter_context(patch.object(host_proof.Path, 'is_file', return_value=True))
        stack.enter_context(patch.object(host_proof.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1000, pw_shell='/bin/bash')))
        stack.enter_context(patch.object(host_proof.shutil, 'which', return_value='/trusted/pamtester'))
        return stack

    def test_pam_auth_and_account_checks_use_private_stdin_then_real_sudo_policy(self):
        self.fixture()
        with patch.object(host_proof.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(host_proof.verify('operator', SECRETS['operator']), 'operator')
        pam, sudo = run.call_args_list
        self.assertIn('acct_mgmt', pam.args[0])
        self.assertIn('authenticate(PAM_SILENT|PAM_DISALLOW_NULL_AUTHTOK)', pam.args[0])
        self.assertTrue(pam.kwargs['input'] == (SECRETS['operator'] + '\n').encode(), 'PAM input is not protected stdin')
        self.assertEqual(pam.kwargs['stdout'], subprocess.DEVNULL)
        self.assertEqual(pam.kwargs['stderr'], subprocess.DEVNULL)
        self.assertEqual(sudo.args[0], [host_proof.SUDO, '-n', '-l', '-U', 'operator', '-u', 'root', '--', host_proof.ROOT_SHELL])
        self.assertNotIn('input', sudo.kwargs)
        assert_public_clean(self, [pam.args, sudo.args, sudo.kwargs])

    def test_wrong_locked_nonadmin_missing_helper_and_timeout_deny_without_echo(self):
        self.fixture()
        cases = [
            [subprocess.CompletedProcess([], 1)],
            [subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1)],
            [subprocess.TimeoutExpired(['helper'], 10, output=SECRETS['operator'])],
            [OSError(SECRETS['operator'])],
        ]
        for results in cases:
            with patch.object(host_proof.subprocess, 'run', side_effect=results):
                with self.assertRaises(ValueError) as stopped: host_proof.verify('operator', SECRETS['operator'])
                assert_public_clean(self, str(stopped.exception))
        for missing in ('helper', 'pam', 'user'):
            with patch.object(host_proof.shutil, 'which', return_value=None if missing == 'helper' else '/trusted/pamtester'), \
                    patch.object(host_proof.Path, 'is_file', return_value=missing != 'pam'), \
                    patch.object(host_proof.pwd, 'getpwnam', side_effect=KeyError() if missing == 'user' else None, return_value=SimpleNamespace(pw_uid=1000, pw_shell='/bin/bash')), \
                    patch.object(host_proof.subprocess, 'run') as run:
                with self.assertRaises(ValueError): host_proof.verify('operator', SECRETS['operator'])
                run.assert_not_called()
        for password in ('', 'x' * 1025, 'line\nbreak', 'null\x00value'):
            with patch.object(host_proof.subprocess, 'run') as run:
                with self.assertRaises(ValueError): host_proof.verify('operator', password)
                run.assert_not_called()


class ConsoleProofTests(unittest.TestCase):
    def test_console_approval_requires_root_tty_and_refuses_echo_fallback(self):
        import getpass
        import main
        import warnings
        for uid, tty in ((1001, True), (0, False)):
            with patch.object(main.os, 'geteuid', return_value=uid), patch.object(main.sys.stdin, 'isatty', return_value=tty), \
                    patch.object(getpass, 'getpass') as prompt:
                with self.assertRaises(SystemExit): main.approve_browser_console()
                prompt.assert_not_called()
        def echo(*args): warnings.warn(SECRETS['setup'], getpass.GetPassWarning)
        with patch.object(main.os, 'geteuid', return_value=0), patch.object(main.sys.stdin, 'isatty', return_value=True), \
                patch.object(getpass, 'getpass', side_effect=echo), patch.object(accounts, 'approve_enrollment') as approve:
            with self.assertRaises(SystemExit) as stopped: main.approve_browser_console()
            approve.assert_not_called()
            assert_public_clean(self, str(stopped.exception))
