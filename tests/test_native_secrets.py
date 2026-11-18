"""Native SOPS Secrets broker invariants with a deterministic crypto stand-in.

Separate real sops+age integration runs in test_native_secrets_crypto.py.
"""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "admin"))
import native_secrets as secrets


class SecretsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.projects = self.root / "projects"
        self.projects.mkdir()
        self.project = self.projects / "example"
        self.project.mkdir()
        (self.project / "default.nix").write_text("{ ... }: { }\n")
        self.keys = self.root / "keys"
        self.keys.mkdir(mode=0o700)
        self.identity = self.keys / "projects.agekey"
        self.state = self.root / "private"
        self.state.mkdir(mode=0o700)
        self.recipient = "age1" + "q" * 58
        self.private = "AGE-SECRET-KEY-1" + "Q" * 58
        self.encrypted = {}
        def mocked_run(argv, data=None, *, key_path=None):
            if argv[0] == "age-keygen":
                if "-y" in argv: return (self.recipient + "\n").encode()
                return ("# public key: " + self.recipient + "\n" + self.private + "\n").encode()
            if argv[:2] == ["sops", "encrypt"]:
                assert key_path is None
                assert self.private not in str(argv)
                obj = json.loads(data)
                nonce = len(self.encrypted)
                result = b"sops:\n  encrypted: ENC[AES256_GCM,data:" + str(nonce).encode() + b"]\n"
                self.encrypted[result] = json.dumps(obj).encode()
                return result
            if argv[:2] == ["sops", "decrypt"]:
                assert str(key_path) == str(self.identity)
                return self.encrypted[data]
            raise AssertionError("Unexpected crypto operation " + str(argv))
        self.fake = patch.object(secrets, "_run", side_effect=mocked_run)
        self.fake.start()
        self.addCleanup(self.fake.stop)
        audit = patch.object(secrets.rt, "audit")
        audit.start()
        self.addCleanup(audit.stop)
        self.store = secrets.Secrets(state=self.state, projects=self.projects,
                                     identity=self.identity)

    def enrolled(self):
        result = self.store.enroll_identity("admin", "generate", acknowledge=True)
        self.assertEqual(result["recipient"], self.recipient)
        self.assertNotIn(self.private, json.dumps(result))
        self.assertEqual(self.identity.stat().st_mode & 0o777, 0o400)
        return result

    def test_identity_generated_once_and_never_exported(self):
        self.assertFalse(self.store.identity_status()["configured"])
        with self.assertRaisesRegex(ValueError, "approval"):
            self.store.enroll_identity("admin", "generate")
        self.enrolled()
        self.assertEqual(self.store.identity_status()["recipient"], self.recipient)
        with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
            self.store.enroll_identity("admin", "generate", acknowledge=True)
        self.assertEqual(self.identity.read_text().strip(), self.private)

    def test_restore_explicit_and_never_rotates(self):
        status = self.store.enroll_identity(
            "admin", "restore", private_key=self.private, acknowledge=True)
        self.assertTrue(status["configured"])
        with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
            self.store.enroll_identity("admin", "restore",
                                       private_key=self.private, acknowledge=True)

    def test_first_secret_review_publishes_only_ciphertext_and_safe_metadata(self):
        self.enrolled()
        inventory = self.store.inventory()
        self.assertEqual(inventory["projects"][0]["keys"], [])
        self.assertTrue(inventory["projects"][0]["indexed"])
        review = self.store.review("admin", "example", "API_TOKEN", "set",
                                   "raw-super-secret-do-not-echo", None)
        self.assertEqual(review["key"], "API_TOKEN")
        self.assertNotIn("raw-super-secret", json.dumps(review))
        self.assertFalse((self.project / "secrets.enc.yaml").exists(),
                         "Review must never publish source")
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            self.store.publish("admin", review["reviewId"])
        publication = self.store.publish("admin", review["reviewId"], acknowledge=True)
        self.assertTrue(publication["requiresApply"])
        self.assertEqual((self.project / "secrets.enc.yaml").stat().st_mode & 0o077, 0)
        self.assertNotIn("raw-super-secret", (self.project / "secrets.enc.yaml").read_text())
        self.assertIn(self.recipient, (self.project / ".sops.yaml").read_text())
        self.assertNotIn(self.private, (self.project / ".sops.yaml").read_text())
        listed = self.store.inventory()
        self.assertEqual(listed["projects"][0]["keys"], ["API_TOKEN"])
        self.assertEqual(listed["projects"][0]["revision"], publication["revision"])
        self.assertNotIn("raw-super-secret", json.dumps(listed))

    def test_rotation_and_deletion_keep_metadata_revision_consistent(self):
        self.enrolled()
        a = self.store.review("admin", "example", "PASSWORD", "set", "first", None)
        first = self.store.publish("admin", a["reviewId"], acknowledge=True)
        b = self.store.review("admin", "example", "PASSWORD", "set", "second",
                              first["revision"])
        second = self.store.publish("admin", b["reviewId"], acknowledge=True)
        self.assertNotEqual(first["revision"], second["revision"])
        prior = (self.store.state / "projects" / "example" / "revisions" /
                 first["revision"]).read_bytes()
        self.assertEqual(hashlib.sha256(prior).hexdigest(), first["revision"])
        c = self.store.review("admin", "example", "PASSWORD", "delete",
                              None, second["revision"])
        third = self.store.publish("admin", c["reviewId"], acknowledge=True)
        self.assertEqual(self.store.inventory()["projects"][0]["keys"], [])
        self.assertNotEqual(third["revision"], first["revision"])

    def test_cas_and_actor_binding_prevent_cross_user_publish(self):
        self.enrolled()
        one = self.store.review("owner", "example", "A", "set", "one", None)
        two = self.store.review("owner", "example", "B", "set", "two", None)
        with self.assertRaisesRegex(ValueError, "owner"):
            self.store.publish("another", one["reviewId"], acknowledge=True)
        self.store.publish("owner", one["reviewId"], acknowledge=True)
        with self.assertRaisesRegex(ValueError, "revision changed"):
            self.store.publish("owner", two["reviewId"], acknowledge=True)
        self.assertEqual(self.store.inventory()["projects"][0]["keys"], ["A"])

    def test_invalid_names_and_untrusted_dirs_rejected(self):
        self.enrolled()
        for project in ("../escape", "other", "a/other"):
            with self.subTest(project=project), self.assertRaises((ValueError, FileNotFoundError)):
                self.store.review("admin", project, "K", "set", "v", None)
        for key in (".env", "../A", "x/y", "", "bad key"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "variable"):
                self.store.review("admin", "example", key, "set", "v", None)
        (self.project / "secrets.enc.yaml").symlink_to(self.identity)
        with self.assertRaises(ValueError):
            self.store.inventory()

    def test_corrupt_identity_blocks_without_overwrite(self):
        self.enrolled()
        self.identity.chmod(0o600)
        self.identity.write_text("not an age key")
        self.identity.chmod(0o400)
        with self.assertRaises((ValueError, KeyError)):
            self.store.review("admin", "example", "A", "set", "v", None)
        with self.assertRaisesRegex(ValueError, "cannot be overwritten"):
            self.store.enroll_identity("admin", "generate", acknowledge=True)

    def test_interrupted_publish_can_recover_previous_revision(self):
        self.enrolled()
        first = self.store.review("admin", "example", "K", "set", "original", None)
        saved = self.store.publish("admin", first["reviewId"], acknowledge=True)
        candidate = self.store.review("admin", "example", "K", "set", "changed",
                                      saved["revision"])
        original = secrets.rt.write_json
        def interrupt(path, record, **kwargs):
            if Path(path).name == "index.json":
                raise OSError("injected interrupted ciphertext commit")
            return original(path, record, **kwargs)
        with patch.object(secrets.rt, "write_json", side_effect=interrupt):
            with self.assertRaisesRegex(OSError, "injected"):
                self.store.publish("admin", candidate["reviewId"], acknowledge=True)
        self.assertEqual(self.store.inventory()["projects"][0]["indexed"], False)
        recovered = self.store.recover("admin", "example")
        self.assertTrue(recovered["recovered"])
        self.assertEqual(self.store.inventory()["projects"][0]["revision"], saved["revision"])
        self.assertEqual(self.store.inventory()["projects"][0]["keys"], ["K"])
        self.assertFalse((self.store.state / "projects" / "example" / "pending.json").exists())

    def test_review_token_expiry_is_enforced(self):
        self.enrolled()
        review = self.store.review("admin", "example", "A", "set", "v", None)
        path = self.store.state / "reviews" / review["reviewId"] / "review.json"
        entry = json.loads(path.read_text())
        entry["expires"] = 0
        secrets.rt.write_json(path, entry)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.store.publish("admin", review["reviewId"], acknowledge=True)

    def test_plaintext_never_retained_in_review_files(self):
        self.enrolled()
        value = "sensitive-multiline-quote:'\"\\nnextline"
        self.store.review("admin", "example", "PASSWORD", "set", value, None)
        for file in self.store.state.rglob("*"):
            if file.is_file():
                self.assertNotIn(value.encode(), file.read_bytes())


if __name__ == "__main__":
    unittest.main()
