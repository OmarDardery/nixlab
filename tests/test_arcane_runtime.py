"""Arcane key persistence and service password requirements."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
BASE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('arcane_key', BASE / 'scripts/arcane_key.py')
key = importlib.util.module_from_spec(spec); spec.loader.exec_module(key)
sys.path[:0] = [str(BASE/'admin'), str(BASE/'scripts')]
import runtime as rt
from test_direct_install import initial

class ArcaneRuntimeTests(unittest.TestCase):
    def test_key_is_private_and_not_regenerated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'data/encryption-key'
            key.ensure(path); original = path.read_bytes(); key.ensure(path)
            self.assertTrue(path.read_bytes() == original, 'Encryption key changed')
            self.assertEqual(len(original), 64)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob('.arcane-key-*')), [])

    def test_corrupt_and_symlink_keys_fail_without_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'key'; path.write_text('corrupt')
            with self.assertRaises(ValueError): key.ensure(path)
            self.assertEqual(path.read_text(), 'corrupt')
            alias = Path(folder)/'alias'; alias.symlink_to(path)
            with self.assertRaises(OSError): key.ensure(alias)
            self.assertTrue(alias.is_symlink())

    def test_password_policy_preserves_upstream_requirements(self):
        for value in ('onlylowercasepassword!', 'ONLYUPPERCASEPASSWORD1!', 'NoNumbersInPassword!', 'NoSymbolsInPassword1'):
            with self.assertRaises(ValueError): rt.encode_secrets({'arcanePassword':value},initial())
        self.assertIn('arcanePassword',rt.encode_secrets({'arcanePassword':'Strong-service-password1!'},initial()))
