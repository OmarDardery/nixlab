"""Real encrypted offline key recovery, using pinned cryptography and age binaries."""
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "admin"))
sys.path.insert(0, str(BASE / "scripts"))
import native_secrets
import native_secrets_recovery as recovery

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO_OK = bool(AESGCM)
except ImportError:
    CRYPTO_OK = False


@unittest.skipUnless(CRYPTO_OK and shutil.which("age-keygen"),
                     "Pinned cryptography and age runtime required")
class RealRecoveryKit(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.audit = patch.object(native_secrets.rt, "audit")
        self.audit.start()
        self.addCleanup(self.audit.stop)
        self.host_a = self.root / "original"
        self.host_b = self.root / "replacement"
        for root in (self.host_a, self.host_b):
            root.mkdir(mode=0o700)
            (root / "keys").mkdir(mode=0o700)
            (root / "state").mkdir(mode=0o700)
            (root / "projects").mkdir()
            (root / "restic").mkdir(mode=0o700)

    def manager(self, root):
        return native_secrets.Secrets(
            state=root / "state", projects=root / "projects",
            identity=root / "keys" / "projects.agekey")

    def kit(self, root):
        return recovery.RecoveryKit(self.manager(root), credentials={
            "restic-password": root / "restic" / "restic-password",
            "restic-environment": root / "restic" / "restic-environment",
        })

    def test_key_and_restic_recovery_can_bootstrap_second_host_without_leaking(self):
        created = self.manager(self.host_a).enroll_identity(
            "owner", "generate", acknowledge=True)
        passphrase = "high-entropy-offline-recovery-test-passphrase-123"
        restic = self.host_a / "restic" / "restic-password"
        restic.write_bytes(b"test-restic-unlock-key-not-a-real-password")
        restic.chmod(0o600)
        exported = self.kit(self.host_a).export("owner", passphrase, include_backup=True)
        self.assertTrue(exported["verified"])
        envelope = base64.b64decode(exported["encrypted"])
        self.assertNotIn(b"AGE-SECRET-KEY", envelope)
        self.assertNotIn(b"test-restic-unlock", envelope)
        self.assertNotIn(passphrase.encode(), envelope)
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.kit(self.host_b).inspect("owner", exported["encrypted"], "wrong-passphrase-long-enough")
        checked = self.kit(self.host_b).inspect("owner", exported["encrypted"], passphrase)
        self.assertEqual(checked["recipient"], created["recipient"])
        restored = self.kit(self.host_b).restore("owner", exported["encrypted"], passphrase,
                                                acknowledge=True)
        self.assertTrue(restored["restored"])
        self.assertEqual(self.manager(self.host_b).identity_status()["recipient"],
                         created["recipient"])
        self.assertEqual((self.host_b / "restic" / "restic-password").read_bytes(),
                         restic.read_bytes())
        with self.assertRaisesRegex(ValueError, "existing"):
            self.kit(self.host_b).restore("owner", exported["encrypted"], passphrase,
                                          acknowledge=True)

    def test_malformed_or_tampered_ciphertext_cannot_change_key(self):
        self.manager(self.host_a).enroll_identity("owner", "generate", acknowledge=True)
        exported = self.kit(self.host_a).export("owner", "another-offline-passphrase-345")
        envelope = json.loads(base64.b64decode(exported["encrypted"]))
        original = base64.b64decode(envelope["ciphertext"])
        envelope["ciphertext"] = base64.b64encode(original[:-1] + bytes([original[-1] ^ 1])).decode()
        tampered = base64.b64encode(json.dumps(envelope).encode()).decode()
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.kit(self.host_b).restore("owner", tampered, "another-offline-passphrase-345",
                                          acknowledge=True)
        self.assertFalse((self.host_b / "keys" / "projects.agekey").exists())


if __name__ == "__main__":
    unittest.main()
