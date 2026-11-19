"""Root broker staged imports preserve exact bytes and require immutable manifests."""
import base64
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "admin"))
import native_imports as imp


def descriptor(path, data):
    return {"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class NativeImportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.audit = patch.object(imp.rt, "audit")
        self.audit.start()
        self.addCleanup(self.audit.stop)
        self.store = imp.ImportStaging(self.root)

    def test_dockerfile_compose_binary_and_empty_original_bytes_preserved(self):
        sources = {
            "Dockerfile": b"FROM scratch\nCOPY \x00\xff /data\n",
            "compose.yaml": b"services:\n  app:\n    build: .\n",
            "context/files/payload.bin": bytes(range(256)) * 5,
            ".dockerignore": b"",
        }
        manifest = [descriptor(name, data) for name, data in sources.items()]
        session = self.store.prepare("owner", "web-app", manifest)
        for entry in manifest:
            data = sources[entry["path"]]
            for at in range(0, len(data), 40):
                result = self.store.chunk(session["id"], "owner", entry["path"],
                                          at, base64.b64encode(data[at:at + 40]).decode())
                self.assertEqual(result["offset"], min(at + 40, len(data)))
        ready = self.store.finish(session["id"], "owner", session["manifestHash"])
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["project"], "web-app")
        self.assertEqual(ready["manifestHash"], imp.sha(imp.check_files(manifest)))
        for name, data in sources.items():
            stored = self.store.root / session["id"] / "source" / name
            self.assertEqual(stored.read_bytes(), data)
            self.assertEqual(stored.stat().st_mode & 0o222, 0)
        with self.assertRaisesRegex(ValueError, "sealed"):
            self.store.chunk(session["id"], "owner", "Dockerfile", 0, "QQ==")
        self.assertNotIn("COPY", json.dumps(ready))

    def test_unapproved_actor_and_manifest_never_receive_uploaded_bytes(self):
        data = b"FROM scratch\n"
        prepared = self.store.prepare("owner", "test-app", [descriptor("Dockerfile", data)])
        with self.assertRaisesRegex(ValueError, "owner"):
            self.store.chunk(prepared["id"], "other", "Dockerfile", 0, base64.b64encode(data).decode())
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.store.finish(prepared["id"], "owner", "a" * 64)
        self.assertFalse((self.store.root / prepared["id"] / "source" / "Dockerfile").exists())

    def test_overwrite_offset_or_digest_mismatch_never_seals(self):
        data = b"test"
        prepared = self.store.prepare("owner", "test-app", [descriptor("Dockerfile", data)])
        self.store.chunk(prepared["id"], "owner", "Dockerfile", 0, base64.b64encode(b"bad!").decode())
        with self.assertRaisesRegex(ValueError, "offset"):
            self.store.chunk(prepared["id"], "owner", "Dockerfile", 0, base64.b64encode(data).decode())
        with self.assertRaisesRegex(ValueError, "SHA|checksum|match"):
            self.store.finish(prepared["id"], "owner", prepared["manifestHash"])
        self.assertEqual(imp.rt.read_json(self.store.root / prepared["id"] / "manifest.json")["status"], "uploading")

    def test_sensitive_and_escaping_names_fail_closed(self):
        for name in ("../Dockerfile", "/etc/passwd", ".env", ".env.local", ".git/config", "x/../y",
                     "keys/id_ed25519", "root.key", "private.agekey", "folder\\name", "foo//bar"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                imp.check_path(name)
        self.assertEqual(imp.check_path("context/.dockerignore"), "context/.dockerignore")
        self.assertEqual(imp.check_path("config/.env.template"), "config/.env.template")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            imp.check_files([descriptor("Dockerfile", b"1"), descriptor("Dockerfile", b"2")])
        with self.assertRaisesRegex(ValueError, "64 MiB"):
            imp.check_files([{"path": "x", "size": 65 * 1024 * 1024, "sha256": "0" * 64}])
        with self.assertRaisesRegex(ValueError, "128 MiB"):
            imp.check_files([
                {"path": "a", "size": 64 * 1024 * 1024, "sha256": "0" * 64},
                {"path": "b", "size": 64 * 1024 * 1024, "sha256": "0" * 64},
                {"path": "c", "size": 1, "sha256": "0" * 64}
            ])

    def test_unreviewed_file_is_not_accepted(self):
        data = b"hello"
        session = self.store.prepare("owner", "hello-world", [descriptor("Dockerfile", data)])
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.store.chunk(session["id"], "owner", "other/Dockerfile", 0, base64.b64encode(data).decode())
        with self.assertRaises(ValueError):
            self.store.chunk(session["id"], "owner", "Dockerfile", 0, "@@@")
        with self.assertRaises(ValueError):
            self.store.chunk(session["id"], "owner", "Dockerfile", 1, base64.b64encode(data).decode())


if __name__ == "__main__":
    unittest.main()
