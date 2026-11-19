"""Verify independent, no-Incus OCI build review and artifact journaling."""
import base64
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "admin"))
sys.path.insert(0, str(ROOT / "tests"))
import oci_builds
import oci_compose
import native_imports as sources
import runtime as rt
import test_oci_artifacts


class OCIBuildReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.patches = [
            patch.object(rt, "audit"),
            patch.object(rt, "locked", return_value=nullcontext()),
            patch.object(oci_builds, "ROOT", self.root / "oci"),
            patch.object(oci_builds, "_account", return_value=(os.geteuid(), os.getegid())),
            patch.object(oci_builds, "_chown_tree"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.publisher = sources.PublishedSources(self.root / "private-source")
        self.staging = sources.ImportStaging(self.root / "state")
        self.manager = oci_builds.OCIBuilds(self.root / "state", publisher=self.publisher)
        self.published = self._source(b"FROM scratch\nCOPY content.dat /content.dat\n", b"binary")

    def _source(self, dockerfile, content):
        values = {"Dockerfile": dockerfile, "content.dat": content}
        files = [{"path": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
                 for name, value in values.items()]
        ready = self.staging.prepare("owner", "sample", files)
        for name, data in values.items():
            self.staging.chunk(ready["id"], "owner", name, 0, base64.b64encode(data).decode())
        done = self.staging.finish(ready["id"], "owner", ready["manifestHash"])
        reviewed = self.publisher.review(self.staging, done["id"], "owner", done["manifestHash"])
        return self.publisher.publish(self.staging, done["id"], "owner",
                                      reviewed["reviewId"], acknowledge=True)

    def fake_sandbox(self, workspace, mode, identity):
        spec = json.loads((workspace / "work" / "intent.json").read_text())
        checked = oci_compose.validate_model(
            {"services": {"app": {"build": {"context": "."}}}},
            project="sample", revision=spec["revision"], compose_path="Dockerfile",
            files=spec["sourceFiles"])
        if mode == "inspect":
            (workspace / "out" / "intent.json").write_text(json.dumps(checked))
            return
        self.assertEqual(mode, "build")
        self.assertEqual(checked["intentHash"], spec["intentHash"])
        content = test_oci_artifacts.archive()
        (workspace / "out" / "app.oci.tar").write_bytes(content)
        (workspace / "out" / "build-result.json").write_text(json.dumps({
            "format": 1, "project": "sample", "revision": spec["revision"],
            "intentHash": checked["intentHash"],
            "artifacts": [{"service": "app", "image": checked["services"][0]["image"],
                           "artifact": "app.oci.tar",
                           "sha256": hashlib.sha256(content).hexdigest(),
                           "bytes": len(content)}]
        }))

    def test_review_is_source_only_and_build_approval_is_exact(self):
        with patch.object(oci_builds, "_run_sandbox", side_effect=self.fake_sandbox):
            reviewed = self.manager.review("sample", self.published["revision"], principal="portal-admin")
        self.assertEqual(reviewed["status"], "ready")
        self.assertEqual(reviewed["services"][0]["built"], True)
        self.assertFalse((self.root / "oci" / "jobs").joinpath(reviewed["id"]).exists(),
                         "Builder workspace cleaned after preview")
        with self.assertRaisesRegex(ValueError, "exact"):
            self.manager.queue(reviewed["id"], "0" * 64)
        self.assertEqual(self.manager.status(reviewed["id"])["status"], "ready")

    def test_failed_builder_keeps_existing_private_source_unchanged(self):
        with patch.object(oci_builds, "_run_sandbox", side_effect=self.fake_sandbox):
            reviewed = self.manager.review("sample", self.published["revision"])
        with patch.object(oci_builds.rt, "pin_worker_runtime"), patch.object(
                oci_builds.subprocess, "run"):
            queued = self.manager.queue(reviewed["id"], reviewed["planHash"])
        self.assertEqual(queued["status"], "queued")
        def reject(*args):
            raise ValueError("buildah failed without output")
        with patch.object(oci_builds, "_run_sandbox", side_effect=reject):
            result = self.manager.run(reviewed["id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errorCode"], "build-or-artifact-verification-failed")
        self.assertEqual(self.publisher.attest("sample", self.published["revision"])["fileCount"], 2)
        self.assertFalse((self.manager._job(reviewed["id"]) / "artifacts" / "app.oci.tar").exists())
        self.assertFalse((self.root / "state" / "oci-build-active.json").exists())

    def test_build_artifact_is_verified_and_no_incus_api_called(self):
        with patch.object(oci_builds, "_run_sandbox", side_effect=self.fake_sandbox):
            reviewed = self.manager.review("sample", self.published["revision"])
        with patch.object(oci_builds.rt, "pin_worker_runtime"), patch.object(
                oci_builds.subprocess, "run"):
            self.manager.queue(reviewed["id"], reviewed["planHash"])
        with patch.object(oci_builds, "_run_sandbox", side_effect=self.fake_sandbox):
            done = self.manager.run(reviewed["id"])
        self.assertEqual(done["status"], "verified-artifact")
        self.assertEqual(len(done["artifacts"]), 1)
        artifact = self.manager._job(reviewed["id"]) / "artifacts" / "app.oci.tar"
        self.assertTrue(artifact.exists())
        self.assertEqual(artifact.stat().st_mode & 0o222, 0)
        self.assertEqual(done["artifacts"][0]["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest())
        self.assertFalse((self.root / "state" / "oci-build-active.json").exists())
        self.assertEqual(self.manager.status(reviewed["id"])["status"], "verified-artifact")

    def test_published_source_rotation_blocks_expired_review(self):
        with patch.object(oci_builds, "_run_sandbox", side_effect=self.fake_sandbox):
            reviewed = self.manager.review("sample", self.published["revision"])
        self._source(b"FROM scratch\n", b"next")
        with self.assertRaisesRegex(ValueError, "revision"):
            self.manager.queue(reviewed["id"], reviewed["planHash"])


if __name__ == "__main__":
    unittest.main()
