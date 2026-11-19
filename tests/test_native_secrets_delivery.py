"""Isolated runtime-file delivery: no host age identity or secret in config."""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "admin"))
import native_secrets_delivery as delivery


class FakeSecrets:
    def __init__(self, root, raw):
        self.root = root
        self.raw = raw
        self.revision = hashlib.sha256(raw).hexdigest()
        self.values = {"API_TOKEN": "literal-private-value", "DB_PASSWORD": "different-secret"}
        self.identity = "AGE-SECRET-KEY-1NEVER-EXPORTED"
    def _project(self, project):
        if project != "alpha":
            raise ValueError("Cross-project secrets denied")
        return self.root
    def _identity(self):
        return "age1" + "q" * 58
    def _check_policy(self, root, host):
        return [host]
    def _cipher(self, root):
        return self.raw
    def _metadata(self, project):
        return {"keys": ["API_TOKEN", "DB_PASSWORD"], "revision": self.revision}
    def _decode(self, raw):
        if raw == b"corrupt":
            raise ValueError("Cannot authenticate project ciphertext")
        return dict(self.values)


class ScopedDeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.secrets = FakeSecrets(self.root, b"encrypted-sops-envelope")
        self.delivery = delivery.ScopedSecrets(self.secrets, staging=self.root)
        self.inventory = {"name": "alpha", "incusProject": "alpha",
                          "secrets": {"file": "secrets.enc.yaml", "bindings": {
                              "TOKEN": {"instance": "api", "key": "API_TOKEN",
                                        "backend": "incus-file", "destination": "/run/secrets/token",
                                        "restartUnits": []},
                              "PASSWORD": {"instance": "db", "key": "DB_PASSWORD",
                                           "backend": "incus-file", "destination": "/run/secrets/password",
                                           "restartUnits": []}}}}
        self.patcher = patch.object(delivery.rt, "audit")
        self.audit = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_one_workload_isolated_read_only_and_no_leak(self):
        with self.delivery.open("alpha", self.secrets.revision, self.inventory, "api") as scoped:
            self.assertEqual(list(scoped["paths"]), ["TOKEN"])
            path = scoped["paths"]["TOKEN"]
            self.assertEqual(path.read_bytes(), b"literal-private-value")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertFalse((path.parent / "PASSWORD").exists())
            self.assertNotIn("literal-private-value", str(scoped))
            self.assertNotIn("different-secret", str(self.audit.call_args_list))
            self.assertNotIn(self.secrets.identity, str(scoped))
        self.assertFalse(path.parent.exists())

    def test_scope_revision_and_backend_are_fail_closed(self):
        for project, revision, inventory, instance in (
            ("beta", self.secrets.revision, self.inventory, "api"),
            ("alpha", "0" * 64, self.inventory, "api"),
            ("alpha", self.secrets.revision, self.inventory, "wrong"),
            ("alpha", self.secrets.revision, {**self.inventory, "name": "beta"}, "api"),
        ):
            with self.subTest(project=project, instance=instance):
                with self.assertRaises(ValueError):
                    with self.delivery.open(project, revision, inventory, instance):
                        self.fail("Unknown project/workload gained a secret")
        self.assertEqual(list(self.root.glob(".nixlab-scoped-*")), [])
        changed = json.loads(json.dumps(self.inventory))
        changed["secrets"]["bindings"]["TOKEN"]["backend"] = "nixos-sops"
        with self.assertRaisesRegex(ValueError, "independent"):
            with self.delivery.open("alpha", self.secrets.revision, changed, "api"):
                pass

    def test_corrupted_ciphertext_or_metadata_denies_decrypt(self):
        with patch.object(self.secrets, "_decode", side_effect=ValueError("Wrong key")):
            with self.assertRaisesRegex(ValueError, "Wrong key"):
                with self.delivery.open("alpha", self.secrets.revision, self.inventory, "api"):
                    pass
        with patch.object(self.secrets, "_metadata", return_value={"revision": self.secrets.revision, "keys": []}):
            with self.assertRaises(ValueError):
                with self.delivery.open("alpha", self.secrets.revision, self.inventory, "api"):
                    pass
        self.assertEqual(list(self.root.glob(".nixlab-scoped-*")), [])

    def test_mid_write_failure_cleans_staged_files(self):
        with patch.object(delivery.os, "write", side_effect=OSError("simulated IO crash")):
            with self.assertRaises(OSError):
                with self.delivery.open("alpha", self.secrets.revision, self.inventory, "api"):
                    pass
        self.assertEqual(list(self.root.glob(".nixlab-scoped-*")), [])

    def test_signal_handler_cleans_staging(self):
        original = signal.getsignal(signal.SIGTERM)
        path = None
        try:
            with self.delivery.open("alpha", self.secrets.revision, self.inventory, "api") as result:
                path = result["paths"]["TOKEN"]
                handler = signal.getsignal(signal.SIGTERM)
                self.assertTrue(callable(handler))
                handler(signal.SIGTERM, None)
        except SystemExit as exc:
            self.assertEqual(exc.code, 128 + signal.SIGTERM)
        else:
            self.fail("SIGTERM did not end secret delivery")
        self.assertFalse(path.exists())
        self.assertEqual(original, signal.getsignal(signal.SIGTERM))


if __name__ == "__main__":
    unittest.main()
