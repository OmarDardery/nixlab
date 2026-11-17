"""Non-mutating project workspace safety and discovery regression tests."""
from pathlib import Path
from unittest.mock import patch
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from project_workspace import validate_workspace, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "projects"
        self.root.mkdir()
        (self.root / "flake.nix").write_text("{ outputs = { self }: {}; }")
        (self.root / "flake.lock").write_text(json.dumps({
            "version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}
        }))
        p = self.root / "aiostreams"
        (p / "modules" / "nested").mkdir(parents=True)
        (p / "modules" / "_disabled").mkdir()
        (p / "files").mkdir()
        (p / "default.nix").write_text("{ ... }: { nixlab.project.name = \"aiostreams\"; }")
        (p / "modules" / "network.nix").write_text("{ ... }: { }")
        (p / "modules" / "nested" / "resources.nix").write_text("{ ... }: { }")
        (p / "modules" / "_disabled" / "never.nix").write_text('throw "never"')
        (p / "files" / "not-automatically-imported.nix").write_text('throw "never"')
        (p / "secrets.enc.yaml").write_text("sops: encrypted-ciphertext-fixture")
        self.p = p

    def check(self):
        return validate_workspace(self.root)

    def test_deterministic_module_discovery_ignores_resources_and_disabled_tree(self):
        a = self.check()
        b = self.check()
        self.assertEqual(a, b)
        self.assertEqual(a["schemaVersion"], 1)
        self.assertEqual(a["projects"]["aiostreams"]["modules"], [
            "aiostreams/modules/nested/resources.nix",
            "aiostreams/modules/network.nix",
        ])
        self.assertIn("aiostreams/files/not-automatically-imported.nix", a["files"])
        self.assertNotIn("aiostreams/files/not-automatically-imported.nix",
                         a["projects"]["aiostreams"]["modules"])
        self.assertNotIn("aiostreams/modules/_disabled/never.nix",
                         a["projects"]["aiostreams"]["modules"])
        self.assertIn("aiostreams/secrets.enc.yaml", a["files"])

    def test_new_project_is_discovered_without_git_commit_or_host_changes(self):
        before = self.check()
        target = self.root / "klipper"
        target.mkdir()
        (target / "default.nix").write_text("{ ... }: { }")
        after = self.check()
        self.assertEqual(sorted(after["projects"]), ["aiostreams", "klipper"])
        self.assertNotEqual(after["sourceHash"], before["sourceHash"])

    def test_revision_hash_detects_content_changes_and_no_secret_output(self):
        result = self.check()
        (self.p / "modules" / "network.nix").write_text("{ ... }: { nixlab.project.enable = false; }")
        self.assertNotEqual(self.check()["sourceHash"], result["sourceHash"])
        self.assertNotIn("encrypted-ciphertext-fixture", json.dumps(self.check()))

    def test_pin_required_and_corrupt_lock_rejected(self):
        (self.root / "flake.lock").unlink()
        with self.assertRaisesRegex(WorkspaceError, "flake.lock"):
            self.check()
        self.assertIn("aiostreams", validate_workspace(self.root, require_lock=False)["projects"])
        (self.root / "flake.lock").write_text('{"version":8,"root":"missing","nodes":{}}')
        with self.assertRaisesRegex(WorkspaceError, "Invalid pinned"):
            self.check()

    def test_symlink_escape_forbidden(self):
        with tempfile.TemporaryDirectory() as external:
            (self.p / "external").symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(WorkspaceError, "Symlink"):
                self.check()

    def test_symlinked_root_forbidden(self):
        link = self.root.parent / "projects-link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(WorkspaceError, "real directory"):
            validate_workspace(link)

    def test_reject_symlinked_module_and_default(self):
        with tempfile.TemporaryDirectory() as external:
            source = Path(external) / "evil.nix"
            source.write_text('throw "evil"')
            (self.p / "modules" / "link.nix").symlink_to(source)
            with self.assertRaisesRegex(WorkspaceError, "Symlink"):
                self.check()
            (self.p / "modules" / "link.nix").unlink()
            (self.p / "default.nix").unlink()
            (self.p / "default.nix").symlink_to(source)
            with self.assertRaisesRegex(WorkspaceError, "Symlink"):
                self.check()

    def test_hardlinks_and_special_files_refused(self):
        os.link(self.p / "default.nix", self.p / "files" / "hardlink.nix")
        with self.assertRaisesRegex(WorkspaceError, "Hardlinked"):
            self.check()
        (self.p / "files" / "hardlink.nix").unlink()
        os.mkfifo(self.p / "files" / "fifo")
        with self.assertRaisesRegex(WorkspaceError, "Unsupported filesystem"):
            self.check()

    def test_explicitly_reject_plaintext_secrets_and_host_data_dirs(self):
        for filename in (".env", ".env.production", "id_ed25519", "projects.agekey", "secrets.yaml"):
            target = self.p / filename
            target.write_text("DO_NOT_EXPORT")
            with self.subTest(filename=filename), self.assertRaisesRegex(WorkspaceError, "plaintext"):
                self.check()
            target.unlink()
        (self.root / "state").mkdir()
        with self.assertRaisesRegex(WorkspaceError, "outside"):
            self.check()

    def test_large_file_limit_and_module_depth(self):
        target = self.p / "files" / "too-large.dat"
        target.write_bytes(b"x" * (1024 * 1024 + 1))
        with self.assertRaisesRegex(WorkspaceError, "1 MiB"):
            self.check()
        target.unlink()
        directory = self.p / "modules"
        for i in range(10):
            directory /= f"layer{i}"
            directory.mkdir()
        (directory / "deep.nix").write_text("{ ... }: {}")
        with self.assertRaisesRegex(WorkspaceError, "eight"):
            self.check()

    def test_invalid_project_name_and_nonmodule_directory(self):
        invalid = self.root / "Invalid_ABC"
        invalid.mkdir()
        (invalid / "default.nix").write_text("{ ... }: { }")
        with self.assertRaisesRegex(WorkspaceError, "Invalid project"):
            self.check()
        (invalid / "default.nix").unlink()
        self.assertEqual(list(self.check()["projects"]), ["aiostreams"])

    def test_unexpected_file_count_fails_closed(self):
        with patch("project_workspace.MAX_FILES", 3):
            with self.assertRaisesRegex(WorkspaceError, "file limit"):
                self.check()


if __name__ == "__main__":
    unittest.main()
