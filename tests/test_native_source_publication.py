"""Verified source publication is inert, immutable, revision-guarded and crash-safe."""
import base64
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "admin"))
import native_imports as native
from oci_source_plan import source_plan


def manifest(path, value):
    return {"path": path, "size": len(value),
            "sha256": hashlib.sha256(value).hexdigest()}


class SourcePublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        audit = patch.object(native.rt, "audit")
        audit.start()
        self.addCleanup(audit.stop)
        self.staging = native.ImportStaging(self.root / "private-state")
        self.publisher = native.PublishedSources(self.root / "project-sources")

    def stage(self, files, project="sample", actor="owner"):
        entries = [manifest(name, content) for name, content in files.items()]
        prepared = self.staging.prepare(actor, project, entries)
        for name, content in files.items():
            for at in range(0, len(content), 4096):
                self.staging.chunk(prepared["id"], actor, name, at,
                                   base64.b64encode(content[at:at + 4096]).decode())
        ready = self.staging.finish(prepared["id"], actor, prepared["manifestHash"])
        return ready

    def review(self, ready, actor="owner"):
        return self.publisher.review(self.staging, ready["id"], actor, ready["manifestHash"])

    def publish(self, ready, reviewed, actor="owner", acknowledged=True):
        return self.publisher.publish(self.staging, ready["id"], actor,
                                      reviewed["reviewId"], acknowledge=acknowledged)

    def current(self):
        return native.rt.read_json(self.publisher.root / "sample" / "current.json", None)

    def test_original_bytes_preserved_and_no_execution_or_nix_store(self):
        values = {"Dockerfile": b"FROM scratch\n",
                  "compose.yaml": b"services:\n  app:\n    build: .\n",
                  "build/binary.bin": bytes(range(256)) * 15,
                  ".dockerignore": b""}
        ready = self.stage(values)
        review = self.review(ready)
        self.assertEqual(review["baseRevision"], None)
        self.assertEqual(sorted(review["changes"]["added"]), sorted(values))
        self.assertEqual(review["changes"]["modified"], [])
        self.assertEqual(review["changes"]["removed"], [])
        self.assertEqual(review["fileCount"], 4)
        publication = self.publish(ready, review)
        self.assertEqual(publication["revision"], ready["manifestHash"])
        self.assertFalse(publication["noChange"])
        revision = self.publisher.root / "sample" / "revisions" / publication["revision"]
        for path, value in values.items():
            self.assertEqual((revision / "source" / path).read_bytes(), value)
            self.assertEqual((revision / "source" / path).stat().st_mode & 0o222, 0)
        self.assertEqual(self.current()["revision"], ready["manifestHash"])
        self.assertEqual(self.publisher.inventory()["projects"][0]["project"], "sample")
        self.assertFalse((self.publisher.root / "sample" / "flake.nix").exists())
        self.assertFalse((self.publisher.root / "sample" / "default.nix").exists())

    def test_revisions_are_cas_guarded_and_previous_bytes_survive(self):
        a = self.stage({"Dockerfile": b"FROM scratch\n", "notes.txt": b"old"})
        b = self.stage({"Dockerfile": b"FROM busybox\n", "new.txt": b"new"})
        ar, stale = self.review(a), self.review(b)
        self.publish(a, ar)
        with self.assertRaisesRegex(ValueError, "changed after review"):
            self.publish(b, stale)
        self.assertEqual(self.current()["revision"], a["manifestHash"])
        br = self.review(b)
        self.assertEqual(br["baseRevision"], a["manifestHash"])
        self.assertEqual(br["changes"], {"added": ["new.txt"],
                                          "removed": ["notes.txt"],
                                          "modified": ["Dockerfile"]})
        self.publish(b, br)
        self.assertEqual(self.current()["revision"], b["manifestHash"])
        self.assertEqual(self.current()["previous"], a["manifestHash"])
        self.assertEqual((self.publisher.root / "sample" / "revisions" / a["manifestHash"] /
                          "source" / "Dockerfile").read_bytes(), b"FROM scratch\n")

    def test_review_is_explicit_short_lived_and_actor_bound(self):
        ready = self.stage({"Dockerfile": b"FROM scratch\n"})
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            self.publisher.publish(self.staging, ready["id"], "owner", "x" * 32)
        review = self.review(ready)
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            self.publish(ready, review, acknowledged=False)
        with self.assertRaisesRegex(ValueError, "owner"):
            self.publish(ready, review, actor="other")
        with self.assertRaisesRegex(ValueError, "Review"):
            self.publisher.publish(self.staging, ready["id"], "owner", "f" * 32,
                                   acknowledge=True)
        path = self.staging.root / ready["id"] / "publish-review.json"
        entry = native.rt.read_json(path)
        native.rt.write_json(path, dict(entry, expires=0))
        with self.assertRaisesRegex(ValueError, "expired"):
            self.publish(ready, review)
        self.assertIsNone(self.current())

    def test_sealed_source_drift_refuses_publication(self):
        ready = self.stage({"Dockerfile": b"FROM scratch\n"})
        review = self.review(ready)
        source = self.staging.root / ready["id"] / "source" / "Dockerfile"
        source.chmod(0o600)
        source.write_bytes(b"FROM corrupt\n")
        source.chmod(0o400)
        with self.assertRaisesRegex(ValueError, "source|checksum|changed"):
            self.publish(ready, review)
        self.assertIsNone(self.current())

    def test_symlink_alias_or_untrusted_permissions_fail_closed(self):
        ready = self.stage({"Dockerfile": b"FROM scratch\n"})
        self.publisher.root.symlink_to(self.root / "private-state")
        with self.assertRaises(ValueError):
            self.review(ready)
        self.publisher.root.unlink()
        self.publisher.root.mkdir(mode=0o777)
        self.publisher.root.chmod(0o777)
        with self.assertRaises(ValueError):
            self.publisher.inventory()
        self.publisher.root.chmod(0o700)
        self.publisher.root.rmdir()

    def test_failure_after_revision_rename_does_not_change_current(self):
        ready = self.stage({"Dockerfile": b"FROM scratch\n"})
        review = self.review(ready)
        original = native.rt.write_json

        def interrupted(path, value, **kwargs):
            if Path(path).name == "current.json":
                raise OSError("injected failure between revision and ref update")
            return original(path, value, **kwargs)

        with patch.object(native.rt, "write_json", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "injected"):
                self.publish(ready, review)
        self.assertIsNone(self.current())
        rev_dir = self.publisher.root / "sample" / "revisions" / ready["manifestHash"]
        self.assertTrue(rev_dir.is_dir(), "Orphaned verified revision should remain recoverable")
        # A new review safely reuses the immutable revision without overwriting it.
        retry = self.review(ready)
        published = self.publish(ready, retry)
        self.assertEqual(published["status"], "published")
        self.assertEqual(self.current()["revision"], ready["manifestHash"])

    def test_identical_second_import_is_non_destructive_noop(self):
        values = {"compose.yaml": b"services: {}\n"}
        first = self.stage(values)
        self.publish(first, self.review(first))
        second = self.stage(values)
        review = self.review(second)
        self.assertTrue(review["alreadyPublished"])
        result = self.publish(second, review)
        self.assertTrue(result["noChange"])
        self.assertEqual(self.current()["revision"], first["manifestHash"])

    def test_invalid_published_reference_is_not_trusted(self):
        first = self.stage({"Dockerfile": b"FROM scratch\n"})
        self.publish(first, self.review(first))
        meta = self.publisher.root / "sample" / "current.json"
        meta.unlink()
        meta.symlink_to(self.root / "private-state")
        with self.assertRaises(ValueError):
            self.publisher.inventory()


    def test_compose_secret_leak_risks_visible_without_mutating_original(self):
        original = (
            b"services:\n"
            b"  api:\n    image: alpine\n    env_file: .env\n"
            b"    environment:\n      API_TOKEN: \u0024{API_TOKEN}\n"
            b"    secrets: [API_TOKEN]\n"
            b"secrets:\n  API_TOKEN:\n    file: ./runtime/token\n"
        )
        ready = self.stage({"compose.yaml": original})
        published = self.publish(ready, self.review(ready))
        plan = source_plan("sample", published["revision"], publisher=self.publisher)
        self.assertEqual(plan["mode"], "compose")
        self.assertFalse(plan["canDeploy"])
        self.assertIn("compose-env-file-may-persist-in-incus-config", plan["blocking"])
        self.assertIn("compose-secret-environment-may-persist-in-incus-config", plan["blocking"])
        self.assertIn("compose-secret-grant-not-bound-to-service", plan["blocking"])
        self.assertNotIn("literal-private-value", str(plan))
        frozen = (self.publisher.root / "sample" / "revisions" / published["revision"]
                  / "source" / "compose.yaml")
        self.assertEqual(frozen.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
