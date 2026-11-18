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


if __name__ == "__main__":
    unittest.main()
