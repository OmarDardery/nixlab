"""Private delivery, owned trust lifecycle and failed issuance with synthetic inputs."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ('admin', 'scripts')]
import browser_certificates as certs
from main import Auth
import runtime as rt
from credential_fixtures import SECRETS, assert_public_clean


class BrowserCertificateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.live = {}
        for item in (patch.object(rt, 'STATE', self.root), patch.object(certs, 'RUNTIME', self.root / 'runtime'),
                     patch.object(certs.diagnostics, 'require_plausible_clock'),
                     patch.object(certs, 'command', side_effect=self.command)):
            item.start(); self.addCleanup(item.stop)

    def command(self, args):
        if 'query' in args: return json.dumps(list(self.live.values()))
        if 'add-certificate' in args:
            records = certs.registry(self.root)['certificates']
            identity, record = next((identity, record) for identity, record in records.items() if record['status'] == 'issuing')
            self.live[identity] = dict(record)
            return ''
        if 'remove' in args:
            self.live.pop(args[-1], None); return ''
        raise AssertionError('Unexpected trust action')

    def issue(self, **kwargs):
        return certs.issue(self.root, 'laptop', SECRETS['p12'], owner='admin', **kwargs)

    def test_profile_encryption_renewal_scope_and_preserved_foreign_trust(self):
        self.live['f' * 64] = {'fingerprint': 'f' * 64, 'name': 'existing-external'}
        first = self.issue()
        archive = self.root / 'identity.p12'; archive.write_bytes(base64.b64decode(first['pkcs12']))
        extracted = subprocess.run(['openssl', 'pkcs12', '-in', str(archive), '-nokeys', '-passin', 'stdin'],
                                   input=(SECRETS['p12'] + '\n').encode(), capture_output=True, check=True).stdout
        text = subprocess.run(['openssl', 'x509', '-text', '-noout'], input=extracted,
                              capture_output=True, check=True).stdout
        for field in (b'secp384r1', b'ecdsa-with-SHA384', b'CA:FALSE', b'Digital Signature', b'TLS Web Client Authentication'):
            self.assertIn(field, text)
        bad = subprocess.run(['openssl', 'pkcs12', '-in', str(archive), '-nokeys', '-passin', 'stdin'],
                             input=b'wrong\n', capture_output=True)
        self.assertNotEqual(bad.returncode, 0)
        second = self.issue(renew=first['fingerprint'])
        elevated = self.issue(full_admin=True)
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])
        self.assertIn(first['fingerprint'], self.live)
        self.assertFalse(elevated['restricted']); self.assertEqual(elevated['projects'], [])
        self.assertEqual(first['projects'], ['lab']); self.assertTrue(first['restricted'])
        self.assertIn('f' * 64, self.live)
        with self.assertRaisesRegex(ValueError, 'Only NixLab-owned'):
            certs.revoke(self.root, 'f' * 64)
        certs.revoke(self.root, first['fingerprint'])
        certs.revoke(self.root, first['fingerprint'])  # Retry after already removed.
        self.assertNotIn(first['fingerprint'], self.live)
        self.assertIn(second['fingerprint'], self.live)
        self.assertIn('f' * 64, self.live)
        inventory = certs.inventory(self.root)
        self.assertEqual(len(inventory['certificates']), 3)
        self.assertEqual(next(item for item in inventory['certificates'] if item['fingerprint'] == first['fingerprint'])['status'], 'revoked')
        assert_public_clean(self, [inventory, (self.root / 'browser-certificates.json').read_text(),
                                  (self.root / 'audit.jsonl').read_text()])
        self.assertEqual(list(certs.RUNTIME.iterdir()), [])
        with patch.object(certs.time, 'time', return_value=time.time() + 91 * 86400), rt.locked():
            certs.expire_owned(self.root)
        self.assertEqual(set(self.live), {'f' * 64})
        remaining = certs.inventory(self.root)['certificates']
        self.assertEqual(sum(item['status'] == 'expired' for item in remaining), 2)

    def test_uncertain_trust_registration_has_durable_ownership_and_no_private_file(self):
        original = self.command
        def failed(args):
            result = original(args)
            if 'add-certificate' in args: raise OSError(SECRETS['p12'])
            return result
        with patch.object(certs, 'command', side_effect=failed):
            with self.assertRaises(ValueError) as caught: self.issue()
        assert_public_clean(self, str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(list(certs.RUNTIME.iterdir()), [])
        inventory = certs.inventory(self.root)['certificates']
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]['status'], 'trusted')
        certs.revoke(self.root, inventory[0]['fingerprint'])
        self.assertEqual(self.live, {})

    def test_process_failure_timeout_and_expired_private_directory_are_clean(self):
        for failure in (OSError(SECRETS['private-key']), subprocess.TimeoutExpired('openssl', 15, output=SECRETS['p12'])):
            with self.subTest(failure=type(failure)), patch.object(certs, 'openssl', side_effect=failure):
                with self.assertRaises(ValueError) as caught: self.issue()
                assert_public_clean(self, str(caught.exception))
                self.assertEqual(list(certs.RUNTIME.iterdir()), [])
                self.assertEqual(self.live, {})
        orphan = certs.RUNTIME / 'issue-abcdefgh'; orphan.mkdir(mode=0o700)
        rt.atomic(orphan / 'key', SECRETS['private-key'])
        os.utime(orphan, (time.time() - 301, time.time() - 301))
        with rt.locked(): certs.cleanup_private()
        self.assertFalse(orphan.exists())

    def test_private_runtime_rejects_aliases(self):
        destination = self.root / 'elsewhere'; destination.mkdir()
        certs.RUNTIME.symlink_to(destination, target_is_directory=True)
        with self.assertRaises(ValueError): self.issue()
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(self.live, {})

    def test_hostile_registry_metadata_cannot_become_a_public_error_or_inventory(self):
        bundle = self.issue()
        value = certs.registry(self.root)
        value['certificates'][bundle['fingerprint']]['expiresAt'] = SECRETS['private-key']
        certs.save(self.root, value)
        for action in (lambda: certs.inventory(self.root), lambda: certs.expire_owned(self.root)):
            with self.assertRaises(ValueError) as caught: action()
            assert_public_clean(self, str(caught.exception))
        self.assertIn(bundle['fingerprint'], self.live)

    def test_other_recovery_failures_do_not_suppress_certificate_expiry_or_guest_restart(self):
        import worker
        rt.atomic(self.root / 'pending.json', SECRETS['staged'])
        with patch.object(certs, 'cleanup_private') as private, patch.object(certs, 'expire_owned') as expire, \
                patch('operations.Operations') as operations:
            with self.assertRaisesRegex(ValueError, 'cleanup failed') as caught: worker.guard()
        assert_public_clean(self, str(caught.exception))
        private.assert_called_once(); expire.assert_called_once_with(self.root)
        operations.return_value.recover_interrupted.assert_called_once()


class CertificateDownloadTests(unittest.TestCase):
    def setUp(self):
        self.auth = Auth(); self.session = {}
        self.auth.sessions['session'] = self.session
        self.result = {'name': 'nixlab-browser-fixture-000000000000', 'device': 'fixture',
                       'fingerprint': 'a' * 64, 'owner': 'admin', 'issuedAt': 1000,
                       'expiresAt': 'future', 'restricted': True, 'projects': ['lab'],
                       'status': 'active', 'validDays': 90,
                       'pkcs12': base64.b64encode(b'encrypted-test-bundle').decode()}

    def test_metadata_only_session_bound_atomic_single_claim_and_ttl(self):
        with patch('main.time.monotonic', return_value=100):
            self.auth.begin_certificate(self.session)
            metadata = self.auth.stage_certificate(self.session, self.result)
        self.assertNotIn('pkcs12', metadata)
        self.assertEqual(metadata['downloadSeconds'], 120)
        with self.assertRaises(ValueError): self.auth.take_certificate({})
        def claim(_):
            try: return self.auth.take_certificate(self.session)['data']
            except ValueError: return None
        with patch('main.time.monotonic', return_value=219), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))
        self.assertEqual(results.count(b'encrypted-test-bundle'), 1)
        self.assertEqual(results.count(None), 1)
        with patch('main.time.monotonic', return_value=100):
            self.auth.stage_certificate(self.session, self.result)
        with patch('main.time.monotonic', return_value=220), self.assertRaisesRegex(ValueError, 'expired'):
            self.auth.take_certificate(self.session)
        self.assertNotIn('certificateDownload', self.session)

    def test_failed_delivery_cannot_replay_and_revoke_cancels_pending_download(self):
        self.auth.stage_certificate(self.session, self.result)
        # Taking claims the bytes before any network write; a broken pipe cannot
        # put them back. Trust remains separately listed/revocable.
        self.auth.take_certificate(self.session)
        with self.assertRaises(ValueError): self.auth.take_certificate(self.session)
        self.auth.stage_certificate(self.session, self.result)
        self.auth.discard_certificate('a' * 64)
        with self.assertRaises(ValueError): self.auth.take_certificate(self.session)

    def test_private_payload_does_not_escape_through_unexpected_broker_fields(self):
        self.result.update(password=SECRETS['p12'], privateKey=SECRETS['private-key'])
        result = self.auth.stage_certificate(self.session, self.result)
        assert_public_clean(self, result)
        self.assertNotIn('privateKey', result)
