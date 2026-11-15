"""Host approval is short-lived and the first portal account is claimed once."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ('admin', 'scripts')]
import accounts
from credential_fixtures import SECRETS, assert_public_clean
import runtime as rt


class EnrollmentTests(unittest.TestCase):
    def test_only_the_approved_browser_can_claim_the_first_admin_once(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), \
                patch.object(rt.grp, 'getgrnam', return_value=Mock(gr_gid=0)), patch.object(rt.os, 'chown'):
            code = 'a' * 32
            self.assertFalse(accounts.enrollment_open())
            accounts.enable_enrollment()
            with self.assertRaisesRegex(ValueError, 'Approve this browser'):
                accounts.claim_enrollment(code, SECRETS['setup'])
            accounts.approve_enrollment(code)
            self.assertNotIn(code, (rt.STATE / 'web-enrollment-approval.json').read_text())
            with self.assertRaisesRegex(ValueError, 'Approve this browser'):
                accounts.claim_enrollment('b' * 32, SECRETS['operator'])

            def claim(password):
                try:
                    accounts.claim_enrollment(code, password)
                    return True
                except ValueError:
                    return False

            with ThreadPoolExecutor(max_workers=2) as workers:
                outcomes = list(workers.map(claim, [SECRETS['setup'], SECRETS['operator']]))
            self.assertEqual(sum(outcomes), 1)
            winner = [SECRETS['setup'], SECRETS['operator']][outcomes.index(True)]
            self.assertTrue(accounts.matches('admin', winner))
            self.assertFalse(accounts.enrollment_open())
            self.assertFalse((rt.STATE / 'web-enrollment-approval.json').exists())
            self.assertFalse((rt.STATE / 'setup-password').exists())
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                accounts.claim_enrollment(code, SECRETS['staged'])
            assert_public_clean(self, [(rt.STATE / 'auth.json').read_text(), (rt.STATE / 'audit.jsonl').read_text()])

    def test_enrollment_rejects_expired_or_previous_boot_approval_and_existing_accounts(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)):
            code = 'a' * 32
            accounts.enable_enrollment()
            with patch.object(accounts.time, 'monotonic', return_value=100), patch.object(accounts, 'boot_id', return_value='boot-1'):
                accounts.approve_enrollment(code)
            for boot, now in [('boot-1', 701), ('boot-2', 120)]:
                with self.subTest(boot=boot), patch.object(accounts, 'boot_id', return_value=boot), \
                        patch.object(accounts.time, 'monotonic', return_value=now), self.assertRaisesRegex(ValueError, 'Approve this browser'):
                    accounts.claim_enrollment(code, SECRETS['setup'])
            self.assertFalse((rt.STATE / 'auth.json').exists())
            rt.write_json(rt.STATE / 'auth.json', rt.password_record(SECRETS['operator']))
            self.assertFalse(accounts.enrollment_open())
            with self.assertRaisesRegex(ValueError, 'already has'):
                accounts.enable_enrollment()

