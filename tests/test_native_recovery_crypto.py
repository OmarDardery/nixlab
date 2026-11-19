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


@unittest.skipUnless(CRYPTO_OK and shutil.which("age-keygen") and shutil.which("sops"),
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
        }, staging=root)

    def test_key_and_restic_recovery_can_bootstrap_second_host_without_leaking(self):
        created = self.manager(self.host_a).enroll_identity(
            "owner", "generate", acknowledge=True)
        passphrase = "high-entropy-offline-recovery-test-passphrase-123"
        restic = self.host_a / "restic" / "restic-password"
        restic.write_bytes(b"test-restic-unlock-key-not-a-real-password")
        restic.chmod(0o600)
        exported = self.kit(self.host_a).export("owner", passphrase, include_backup=True)
        self.assertFalse(exported["verified"])
        self.assertTrue(exported["requiresOfflineUnlockVerification"])
        envelope = base64.b64decode(exported["encrypted"])
        self.assertNotIn(b"AGE-SECRET-KEY", envelope)
        self.assertNotIn(b"test-restic-unlock", envelope)
        self.assertNotIn(passphrase.encode(), envelope)
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.kit(self.host_b).inspect("owner", exported["encrypted"], "wrong-passphrase-long-enough")
        checked = self.kit(self.host_b).inspect("owner", exported["encrypted"], passphrase)
        self.assertTrue(checked["verified"])
        self.assertEqual(checked["recipient"], created["recipient"])
        restored = self.kit(self.host_b).restore("owner", exported["encrypted"], passphrase,
                                                acknowledge=True)
        self.assertTrue(restored["restored"])
        self.assertEqual(self.manager(self.host_b).identity_status()["recipient"],
                         created["recipient"])
        self.assertEqual((self.host_b / "restic" / "restic-password").read_bytes(),
                         restic.read_bytes())
        with self.assertRaisesRegex(ValueError, "already exists|existing"):
            self.kit(self.host_b).restore("owner", exported["encrypted"], passphrase,
                                          acknowledge=True)

    def test_encrypted_source_and_offline_kit_restore_to_separate_host_then_rotate_service(self):
        from native_secrets_delivery import ScopedSecrets
        source = self.host_a / "projects" / "web"
        source.mkdir()
        (source / "default.nix").write_text("{ ... }: { nixlab.project.name = \"web\"; }\n")
        manager_a = self.manager(self.host_a)
        manager_a.enroll_identity("owner", "generate", acknowledge=True)
        pending = manager_a.review("owner", "web", "API_TOKEN", "set", "original-value", None)
        first = manager_a.publish("owner", pending["reviewId"], acknowledge=True)
        encrypted = source / "secrets.enc.yaml"
        self.assertNotIn(b"original-value", encrypted.read_bytes())
        self.assertNotIn(b"AGE-SECRET-KEY", encrypted.read_bytes())

        # Only public Nix files and ciphertext travel in the source archive.
        restored_project = self.host_b / "projects" / "web"
        restored_project.mkdir()
        for file in ("default.nix", ".sops.yaml", "secrets.enc.yaml"):
            shutil.copyfile(source / file, restored_project / file)
        self.assertFalse(self.manager(self.host_b).identity.exists())
        kit = self.kit(self.host_a).export("owner", "long-separate-password-host-restore")
        validated = self.kit(self.host_b).inspect(
            "owner", kit["encrypted"], "long-separate-password-host-restore")
        self.assertTrue(validated["verified"])
        self.assertTrue(self.kit(self.host_b).restore(
            "owner", kit["encrypted"], "long-separate-password-host-restore",
            acknowledge=True)["restored"])
        manager_b = self.manager(self.host_b)
        indexed = manager_b.reindex("owner", "web", acknowledge=True)
        self.assertEqual(indexed["revision"], first["revision"])

        inventory = {"name": "web", "incusProject": "web",
                     "secrets": {"file": "secrets.enc.yaml", "bindings": {
                         "TOKEN": {"instance": "service", "key": "API_TOKEN",
                                   "backend": "incus-file",
                                   "destination": "/run/secrets/TOKEN",
                                   "restartUnits": []}}}}
        delivery = ScopedSecrets(manager_b, staging=self.host_b)
        observed = []
        def restart_service(revision):
            with delivery.open("web", revision, inventory, "service") as files:
                observed.append(files["paths"]["TOKEN"].read_text())
                self.assertNotIn("original-value", str(files))
            self.assertEqual(list(self.host_b.glob(".nixlab-scoped-*")), [])
        restart_service(indexed["revision"])
        review = manager_b.review("owner", "web", "API_TOKEN", "set",
                                  "rotated-value", indexed["revision"])
        updated = manager_b.publish("owner", review["reviewId"], acknowledge=True)
        restart_service(updated["revision"])
        self.assertEqual(observed, ["original-value", "rotated-value"])
        self.assertNotIn(b"rotated-value", (restored_project / "secrets.enc.yaml").read_bytes())

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
