"""Byte-for-byte frozen Compose and SOPS file staging, never live deployment."""
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "admin"))

import native_imports as native
import native_compose_workspace as workspace
import native_secrets_delivery as delivery
import runtime as rt


class FakeSecrets:
    def __init__(self, root):
        self.root = root
        self.raw = b"opaque-encrypted-project-sops"
        self.revision = hashlib.sha256(self.raw).hexdigest()
        self.values = {"API_TOKEN": "synthetic-secret-for-api",
                       "DB_PASSWORD": "synthetic-secret-for-db"}
        self.decrypts = 0

    def _project(self, project):
        if project != "alpha":
            raise ValueError("Bad project")
        return self.root

    def _identity(self):
        return "age1" + "q" * 58

    def _check_policy(self, root, host):
        return [host]

    def _cipher(self, root):
        return self.raw

    def _metadata(self, project):
        return {"keys": sorted(self.values), "revision": self.revision}

    def _decode(self, raw):
        self.decrypts += 1
        return dict(self.values)


class FrozenComposeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.manager = FakeSecrets(self.root)
        self.delivery = delivery.ScopedSecrets(self.manager, staging=self.root)
        self.publisher = native.PublishedSources(self.root / "published")
        self.agent = workspace.FrozenComposeWorkspace(self.delivery, self.publisher)
        self.inventory = {
            "name": "alpha", "incusProject": "alpha",
            "secrets": {"file": "secrets.enc.yaml", "bindings": {
                "API_TOKEN": {
                    "key": "API_TOKEN", "instance": "api", "backend": "incus-file",
                    "destination": "/run/secrets/API_TOKEN", "restartUnits": [],
                },
                "DB_PASS": {
                    "key": "DB_PASSWORD", "instance": "db", "backend": "incus-file",
                    "destination": "/run/secrets/DB_PASS", "restartUnits": [],
                },
            }},
        }
        self.raw = (
            b"services:\n"
            b"  api:\n    image: alpine\n    secrets: [API_TOKEN]\n"
            b"  db:\n    image: postgres\n    secrets: [DB_PASS]\n"
            b"secrets:\n"
            b"  API_TOKEN:\n    file: ./secrets/api_token\n"
            b"  DB_PASS:\n    file: ./secrets/db_pass\n"
        )
        self.audit = patch.object(rt, "audit")
        self.audit.start()
        self.addCleanup(self.audit.stop)

    def publish(self, raw=None, *, additional=None):
        files = {"compose.yaml": self.raw if raw is None else raw}
        files.update(additional or {})
        entries = []
        published_root = self.publisher.root
        published_root.mkdir(mode=0o700, exist_ok=True)
        project = published_root / "alpha"
        project.mkdir(mode=0o700, exist_ok=True)
        for name, value in sorted(files.items()):
            entries.append({"path": name, "size": len(value),
                            "sha256": hashlib.sha256(value).hexdigest()})
        revision = native.sha(entries)
        folder = project / "revisions" / revision
        source = folder / "source"
        source.mkdir(parents=True, mode=0o700)
        for name, value in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(value)
            path.chmod(0o400)
        rt.write_json(folder / "manifest.json", {
            "schemaVersion": 1, "project": "alpha",
            "sourceHash": revision, "files": entries})
        rt.write_json(project / "current.json", {
            "project": "alpha", "revision": revision})
        self.assertEqual(self.publisher.attest("alpha", revision)["revision"], revision)
        return revision

    def test_scoped_paths_and_byte_exact_compose_cleanup(self):
        source_revision = self.publish()
        with self.agent.stage("alpha", source_revision, self.manager.revision,
                              self.inventory) as scoped:
            self.assertFalse(scoped["runtimeQualified"])
            self.assertEqual(scoped["compose"].read_bytes(), self.raw)
            self.assertEqual(scoped["paths"]["API_TOKEN"].read_text(),
                             "synthetic-secret-for-api")
            self.assertEqual(scoped["paths"]["DB_PASS"].read_text(),
                             "synthetic-secret-for-db")
            self.assertEqual(stat.S_IMODE(scoped["paths"]["API_TOKEN"].stat().st_mode), 0o400)
            self.assertNotIn("synthetic-secret-for-api", str(scoped))
            self.assertNotIn("synthetic-secret-for-db", str(scoped))
            temp = scoped["directory"].parent
        self.assertFalse(temp.exists())
        self.assertEqual(list(self.root.glob(".nixlab-scoped-*")), [])
        self.assertEqual(list(self.root.glob(".nixlab-compose-*")), [])
        self.assertEqual(self.manager.decrypts, 2)

    def test_env_file_must_block_before_decrypt(self):
        raw = self.raw.replace(b"image: alpine\n", b"image: alpine\n    env_file: .env\n")
        revision = self.publish(raw)
        with self.assertRaisesRegex(ValueError, "unqualified"):
            with self.agent.stage("alpha", revision, self.manager.revision, self.inventory):
                self.fail("Unsafe Compose environment was staged")
        self.assertEqual(self.manager.decrypts, 0)

    def test_collision_with_uploaded_original_denied(self):
        revision = self.publish(additional={"secrets/api_token": b"public-placeholder"})
        with self.assertRaisesRegex(ValueError, "collides"):
            with self.agent.stage("alpha", revision, self.manager.revision, self.inventory):
                self.fail("Original source file overwritten")
        self.assertEqual(self.manager.decrypts, 0)

    def test_hardlink_failure_removes_scoped_staging(self):
        revision = self.publish()
        with patch.object(workspace.os, "link", side_effect=OSError("simulated staging failure")):
            with self.assertRaisesRegex(OSError, "simulated"):
                with self.agent.stage("alpha", revision, self.manager.revision, self.inventory):
                    self.fail("Staging should have aborted")
        self.assertEqual(list(self.root.glob(".nixlab-scoped-*")), [])
        self.assertEqual(list(self.root.glob(".nixlab-compose-*")), [])

    def test_cross_project_and_wrong_cipher_revision_denied(self):
        revision = self.publish()
        for project, secret_rev in (("beta", self.manager.revision),
                                    ("alpha", "0" * 64)):
            with self.subTest(project=project):
                with self.assertRaises(ValueError):
                    with self.agent.stage(project, revision, secret_rev, self.inventory):
                        pass


if __name__ == "__main__":
    unittest.main()
