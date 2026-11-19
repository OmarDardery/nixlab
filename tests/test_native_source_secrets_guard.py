"""Source-only publication must not accidentally commit obvious OCI credentials."""
import base64
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "admin"))
import native_imports as imports
import native_source_secrets_guard as guard


class SourceSecretGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        audit = patch.object(imports.rt, "audit")
        audit.start()
        self.addCleanup(audit.stop)
        self.staging = imports.ImportStaging(self.root / "private")
        self.publisher = imports.PublishedSources(self.root / "sources")

    def stage(self, name, content):
        data = content.encode() if isinstance(content, str) else content
        files = [{"path": name, "size": len(data),
                  "sha256": hashlib.sha256(data).hexdigest()}]
        prepared = self.staging.prepare("admin", "sample", files)
        if data:
            for offset in range(0, len(data), 4096):
                self.staging.chunk(prepared["id"], "admin", name, offset,
                                   base64.b64encode(data[offset:offset + 4096]).decode())
        self.staging.finish(prepared["id"], "admin", prepared["manifestHash"])
        return prepared

    def test_dockerfile_credential_values_and_build_args_blocked(self):
        for content in (
            "FROM alpine\nENV API_TOKEN=abc123\n",
            "FROM alpine\nENV DATABASE_PASSWORD \${DATABASE_PASSWORD}\n",
            "FROM alpine\nARG SECRET_KEY\n",
            "FROM alpine\nARG AUTH_TOKEN=\${AUTH_TOKEN}\n",
        ):
            with self.subTest(content=content):
                entry = self.stage("Dockerfile", content)
                with self.assertRaisesRegex(ValueError, "Credential-bearing"):
                    self.publisher.review(self.staging, entry["id"], "admin", entry["manifestHash"])

    def test_compose_credentials_blocked_in_map_and_list(self):
        for content in (
            "services:\n  app:\n    environment:\n      PASSWORD: topsecret\n",
            "services:\n  app:\n    environment:\n      - API_TOKEN=\${API_TOKEN}\n",
            "services:\n  app:\n    build:\n      args:\n        SECRET_KEY: \${SECRET_KEY}\n",
        ):
            with self.subTest(content=content):
                entry = self.stage("compose.yaml", content)
                with self.assertRaisesRegex(ValueError, "Credential-bearing"):
                    self.publisher.review(self.staging, entry["id"], "admin", entry["manifestHash"])

    def test_nonsecret_source_can_be_published_byte_exact(self):
        content = b"FROM alpine\nENV APP_PORT=8080\n"
        entry = self.stage("Dockerfile", content)
        reviewed = self.publisher.review(self.staging, entry["id"], "admin", entry["manifestHash"])
        published = self.publisher.publish(self.staging, entry["id"], "admin",
                                           reviewed["reviewId"], acknowledge=True)
        path = self.publisher.root / "sample" / "revisions" / published["revision"] / "source" / "Dockerfile"
        self.assertEqual(path.read_bytes(), content)

    def test_sealed_source_tamper_between_review_and_publish_fails(self):
        entry = self.stage("Dockerfile", "FROM alpine\n")
        reviewed = self.publisher.review(self.staging, entry["id"], "admin", entry["manifestHash"])
        file = self.staging.root / entry["id"] / "source" / "Dockerfile"
        file.chmod(0o600)
        file.write_text("ENV API_TOKEN=leak\n")
        file.chmod(0o400)
        with self.assertRaises(ValueError):
            self.publisher.publish(self.staging, entry["id"], "admin",
                                   reviewed["reviewId"], acknowledge=True)
        self.assertFalse((self.publisher.root / "sample" / "current.json").exists())

    def test_plaintext_env_file_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "plaintext"):
            imports.check_path("some/path/.env")


if __name__ == "__main__":
    unittest.main()
