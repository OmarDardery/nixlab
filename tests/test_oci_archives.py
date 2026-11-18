"""Data-only #139 import hardening and deterministic OCI source plan tests."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "admin"))
import native_imports as imp
from oci_source_plan import source_plan


def descriptor(path, payload):
    return {"path": path, "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def zip_bytes(mapping):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, value in mapping.items():
            z.writestr(name, value)
    return buf.getvalue()


def tar_bytes(files, *, mode="w:gz"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as tar:
        for name, value in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(value)
            tar.addfile(entry, io.BytesIO(value))
    return buf.getvalue()


class OCIArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        with patch.object(imp.rt, "audit"):
            self.staging = imp.ImportStaging(self.root / "state")
        self.publisher = imp.PublishedSources(root=self.root / "published")
        self.audit = patch.object(imp.rt, "audit")
        self.audit.start()
        self.addCleanup(self.audit.stop)

    def stage(self, name, payload, *, archive=True):
        entry = descriptor(name, payload)
        prepared = self.staging.prepare("owner", "sample", [entry], archive=archive)
        for offset in range(0, len(payload), 8192):
            part = payload[offset:offset + 8192]
            self.staging.chunk(prepared["id"], "owner", name, offset,
                               base64.b64encode(part).decode())
        return self.staging.finish(prepared["id"], "owner", prepared["manifestHash"])

    def publish(self, staged):
        approved = self.publisher.review(self.staging, staged["id"],
                                         "owner", staged["manifestHash"])
        return self.publisher.publish(self.staging, staged["id"], "owner",
                                      approved["reviewId"], acknowledge=True)

    def test_nested_zip_compose_source_is_byte_exact_after_private_publication(self):
        files = {
            "project/compose.yaml": b"services:\n  web:\n    build:\n      context: ./web\n",
            "project/web/Dockerfile": b"FROM scratch\nCOPY files/payload /data\n",
            "project/web/files/payload": bytes(range(256)) * 7,
            "project/web/.dockerignore": b"*.log\n",
        }
        staged = self.stage("project.zip", zip_bytes(files))
        self.assertTrue(staged["expandedFromArchive"])
        self.assertEqual(staged["files"], len(files))
        self.assertEqual({e["path"]: e["sha256"] for e in staged["entries"]},
                         {path: hashlib.sha256(data).hexdigest() for path, data in files.items()})
        published = self.publish(staged)
        self.assertEqual(published["revision"], staged["manifestHash"])
        folder = self.publisher.root / "sample" / "revisions" / published["revision"] / "source"
        for path, data in files.items():
            self.assertEqual((folder / path).read_bytes(), data)
        self.assertFalse((folder / "project.zip").exists())
        plan = source_plan("sample", published["revision"], self.publisher)
        self.assertEqual(plan["mode"], "compose")
        self.assertEqual(plan["entrypoint"], "project/compose.yaml")
        self.assertFalse(plan["canDeploy"])
        self.assertFalse(plan["canBuild"])
        self.assertIn("sandboxed-incus-compose-compatibility-check-required", plan["blocking"])
        self.assertEqual(plan["sourceFiles"], staged["entries"])

    def test_tar_dockerfile_only_creates_separate_manifest_with_source_qualified_tag(self):
        staged = self.stage("backup.tar.gz", tar_bytes({
            "app/Dockerfile": b"FROM scratch\nLABEL version=1\n",
            "app/assets/icon.bin": b"\x00\xfe\xff",
        }))
        published = self.publish(staged)
        plan = source_plan("sample", published["revision"], self.publisher)
        self.assertEqual(plan["mode"], "dockerfile")
        self.assertIn('context: "./app"', plan["generatedCompose"])
        self.assertIn('dockerfile: "Dockerfile"', plan["generatedCompose"])
        self.assertIn(published["revision"][:32], plan["proposedImage"])
        assert not (self.publisher.root / "sample" / "revisions" /
                    published["revision"] / "source" / "compose.yaml").exists()
        newer = self.stage("backup.tar.gz", tar_bytes({
            "app/Dockerfile": b"FROM scratch\nLABEL version=2\n",
            "app/assets/icon.bin": b"\x00\xfe\xff",
        }))
        again = self.publish(newer)
        changed = source_plan("sample", again["revision"], self.publisher)
        self.assertNotEqual(plan["buildIdentity"], changed["buildIdentity"])
        self.assertNotEqual(plan["proposedImage"], changed["proposedImage"])
        self.assertNotEqual(plan["revision"], changed["revision"])

    def test_standard_tar_dot_prefix_is_canonicalized_without_changing_bytes(self):
        payload = tar_bytes({"./Dockerfile": b"FROM scratch\\n", "./assets/data.bin": b"abc"})
        staged = self.stage("source.tgz", payload)
        self.assertEqual(sorted(item["path"] for item in staged["entries"]),
                         ["Dockerfile", "assets/data.bin"])
        published = self.publish(staged)
        root = self.publisher.root / "sample" / "revisions" / published["revision"] / "source"
        self.assertEqual((root / "Dockerfile").read_bytes(), b"FROM scratch\\n")

    def test_archive_import_is_explicit_not_automatic(self):
        payload = zip_bytes({"Dockerfile": b"FROM scratch\n"})
        staged = self.stage("archive.zip", payload, archive=False)
        self.assertFalse(staged["expandedFromArchive"])
        self.assertEqual(staged["entries"], [descriptor("archive.zip", payload)])

    def test_zip_symlink_and_tar_hardlink_are_refused_without_publication(self):
        link = zipfile.ZipInfo("linked/Dockerfile")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(link, "../outside")
        with self.assertRaisesRegex(ValueError, "links|special"):
            self.stage("source.zip", buf.getvalue())
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            item = tarfile.TarInfo("Dockerfile")
            item.type = tarfile.LNKTYPE
            item.linkname = "/etc/shadow"
            tar.addfile(item)
        with self.assertRaisesRegex(ValueError, "links|special"):
            self.stage("source.tar", buf.getvalue())
        self.assertFalse(self.publisher.root.exists())

    def test_archive_traversal_secret_files_and_duplicate_paths_refused(self):
        for name in ("../outside", "/etc/shadow", "config/.env", ".ssh/id_ed25519",
                     "dir\\evil", "dir/../../breakout"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.stage("source.zip", zip_bytes({name: b"secret"}))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Dockerfile", b"FROM scratch")
            z.writestr("Dockerfile", b"FROM busybox")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.stage("source.zip", buf.getvalue())
        self.assertFalse(self.publisher.root.exists())

    def test_decompression_budget_and_archive_permissions(self):
        payload = zip_bytes({"Dockerfile": b"FROM scratch\n", "payload.bin": b"A" * 4096})
        with patch.object(imp, "MAX_TOTAL", 1024):
            with self.assertRaisesRegex(ValueError, "quota"):
                self.stage("source.zip", payload)
        with self.assertRaisesRegex(ValueError, "bounded|one"):
            self.staging.prepare("owner", "sample", [descriptor("source.zip", b"x"),
                                                     descriptor("other.zip", b"y")], archive=True)
        with self.assertRaisesRegex(ValueError, "ZIP/TAR"):
            self.staging.prepare("owner", "sample", [descriptor("README", b"x")], archive=True)

    def test_buildkit_unsupported_is_visible_not_silently_translated(self):
        original = b"# syntax=docker/dockerfile:1.7\nFROM scratch\nRUN --mount=type=secret,id=x echo no\n"
        staged = self.stage("source.zip", zip_bytes({"Dockerfile": original}))
        published = self.publish(staged)
        plan = source_plan("sample", published["revision"], self.publisher)
        self.assertIn("unsupported-buildkit-feature-requires-explicit-policy", plan["blocking"])
        self.assertEqual((self.publisher.root / "sample" / "revisions" / published["revision"] /
                          "source" / "Dockerfile").read_bytes(), original)
        self.assertFalse(plan["canBuild"])


if __name__ == "__main__":
    unittest.main()
