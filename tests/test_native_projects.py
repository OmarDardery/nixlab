"""Root-owned project workspace overview must never execute or leak source."""
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "admin"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import native_projects


class NativeProjectOverviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "projects"
        self.root.mkdir(mode=0o700)
        (self.root / "flake.nix").write_text('{ outputs = { self }: {}; }')
        (self.root / "flake.lock").write_text(json.dumps({
            "version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}
        }))
        one = self.root / "alpha"
        one.mkdir(mode=0o700)
        (one / "default.nix").write_text('{ ... }: { }')
        (one / "secrets.enc.yaml").write_text('sops: ENCRYPTED-BLOB-NOT-EXPOSED')
        self.root.chmod(0o700)

    def test_names_and_metadata_only_never_expose_encrypted_source(self):
        value = native_projects.overview(self.root)
        self.assertTrue(value["available"])
        self.assertEqual(value["projects"], [
            {"name": "alpha", "entrypoint": "alpha/default.nix", "moduleCount": 0}
        ])
        self.assertRegex(value["sourceHash"], r"^[a-f0-9]{64}$")
        self.assertNotIn("ENCRYPTED-BLOB-NOT-EXPOSED", json.dumps(value))
        self.assertNotIn(str(self.root), json.dumps(value))

    def test_missing_workspace_is_explicit_not_false_empty_success(self):
        result = native_projects.overview(self.root / "missing")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "not-configured")

    def test_symlink_and_unsafe_ownership_refused(self):
        link = self.root.parent / "link"
        link.symlink_to(self.root, target_is_directory=True)
        self.assertEqual(native_projects.overview(link)["reason"], "invalid-workspace")
        self.root.chmod(0o777)
        self.assertEqual(native_projects.overview(self.root)["reason"], "invalid-workspace")

    def test_plaintext_secret_rejected_without_leaking_name_or_content(self):
        (self.root / "alpha" / ".env").write_text("TOKEN=never-publish")
        result = native_projects.overview(self.root)
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "invalid-workspace")
        self.assertNotIn("TOKEN=", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
