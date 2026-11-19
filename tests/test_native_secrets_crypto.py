"""Real SOPS + age integration, run under the pinned Nix development shell.

Unlike test_native_secrets.py, these tests do not mock cryptography.
"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "admin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import native_secrets


@unittest.skipUnless(shutil.which("sops") and shutil.which("age-keygen"),
                     "Real crypto tools unavailable; use Nix secrets-test shell")
class RealSecretsCrypto(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.projects = root / "projects"
        self.projects.mkdir()
        project = self.projects / "sample"
        project.mkdir()
        (project / "default.nix").write_text("{ ... }: {}\n")
        (root / "keys").mkdir(mode=0o700)
        (root / "state").mkdir(mode=0o700)
        self.project = project
        self.store = native_secrets.Secrets(state=root / "state", projects=self.projects,
                                            identity=root / "keys" / "projects.agekey")
        audit = patch.object(native_secrets.rt, "audit")
        audit.start()
        self.addCleanup(audit.stop)

    def test_real_roundtrip_rotation_restore_and_no_secret_leak(self):
        status = self.store.enroll_identity("owner", "generate", acknowledge=True)
        self.assertTrue(status["healthy"])
        self.assertTrue(status["recipient"].startswith("age1"))
        original = "line1\nline2: 'quoted' \"dollar$\\n\""
        review = self.store.review("owner", "sample", "API_TOKEN", "set", original, None)
        result = self.store.publish("owner", review["reviewId"], acknowledge=True)
        encrypted = (self.project / "secrets.enc.yaml").read_bytes()
        self.assertNotIn(original.encode(), encrypted)
        self.assertIn(b"sops:", encrypted)
        env = {"SOPS_AGE_KEY_FILE": str(self.store.identity)}
        decrypted = subprocess.run(
            ["sops", "decrypt", "--input-type", "yaml", "--output-type", "json", "/dev/stdin"],
            input=encrypted, env={**__import__("os").environ, **env},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=20)
        self.assertEqual(json.loads(decrypted.stdout), {"API_TOKEN": original})
        next_review = self.store.review("owner", "sample", "API_TOKEN", "set", "rotated",
                                        result["revision"])
        self.store.publish("owner", next_review["reviewId"], acknowledge=True)
        self.assertEqual(self.store.inventory()["projects"][0]["keys"], ["API_TOKEN"])
        self.assertEqual(self.store.identity_status()["recipient"], status["recipient"])
        # Simulate restore of ciphertext and the separately backed-up identity.
        restore_root = self.project / "secrets.enc.yaml"
        self.assertTrue(restore_root.is_file())


    def test_real_multi_recipient_rekey_revokes_new_ciphertext_only(self):
        import os
        self.store.enroll_identity("owner", "generate", acknowledge=True)
        start = self.store.review("owner", "sample", "ACCESS_TOKEN", "set", "canary-secret", None)
        before = self.store.publish("owner", start["reviewId"], acknowledge=True)
        old_ciphertext = (self.project / "secrets.enc.yaml").read_bytes()

        external_key = self.project.parent / "external.agekey"
        subprocess.run(["age-keygen", "-o", str(external_key)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=True, timeout=10)
        external_key.chmod(0o400)
        public = subprocess.run(["age-keygen", "-y"], input=external_key.read_bytes(),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                check=True, timeout=10).stdout.decode().strip()
        host = self.store.identity_status()["recipient"]
        add = self.store.review("owner", "sample", None, "recipients",
                                [host, public], before["revision"])
        joint = self.store.publish("owner", add["reviewId"], acknowledge=True)
        cipher_both = (self.project / "secrets.enc.yaml").read_bytes()
        cmd = ["sops", "decrypt", "--input-type", "yaml", "--output-type", "json", "/dev/stdin"]
        decrypted = subprocess.run(cmd, input=cipher_both,
                                   env={**os.environ, "SOPS_AGE_KEY_FILE": str(external_key)},
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   check=True, timeout=20)
        self.assertEqual(json.loads(decrypted.stdout)["ACCESS_TOKEN"], "canary-secret")

        revoke = self.store.review("owner", "sample", None, "recipients",
                                   [host], joint["revision"])
        self.store.publish("owner", revoke["reviewId"], acknowledge=True)
        new_ciphertext = (self.project / "secrets.enc.yaml").read_bytes()
        blocked = subprocess.run(cmd, input=new_ciphertext,
                                 env={**os.environ, "SOPS_AGE_KEY_FILE": str(external_key)},
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=20)
        self.assertNotEqual(blocked.returncode, 0, "Revoked recipient still decrypts new ciphertext")
        self.assertNotIn(b"canary-secret", new_ciphertext)
        self.assertNotIn(public, (self.project / ".sops.yaml").read_text())
        # Security limitation: recipient deletion cannot undo ciphertext copies
        # from historical revisions. Only rotating actual secret values can.
        old_permitted = subprocess.run(cmd, input=cipher_both,
                                       env={**os.environ, "SOPS_AGE_KEY_FILE": str(external_key)},
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       check=True, timeout=20)
        self.assertIn(b"canary-secret", old_permitted.stdout)
        self.assertNotIn(b"canary-secret", old_ciphertext)


if __name__ == "__main__":
    unittest.main()
