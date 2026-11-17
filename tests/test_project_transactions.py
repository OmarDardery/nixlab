"""Project-only source transaction contract: no NixOS rebuild or Incus calls."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "admin"))
sys.path.insert(0, str(ROOT / "scripts"))

import runtime as rt
import project_transactions as tx
import project_plans


class ProjectSourceTransactionsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.host = self.base / "lab"
        self.host.mkdir(mode=0o700)
        self.workspace = self.host / "projects"
        self.workspace.mkdir(mode=0o700)
        self.state = self.base / "state"
        self.state.mkdir(mode=0o700)
        (self.workspace / "flake.nix").write_text('{ outputs = { self }: {}; }')
        (self.workspace / "flake.lock").write_text(json.dumps({
            "version": 7, "root": "root", "nodes": {"root": {"inputs": {}}}
        }))
        project = self.workspace / "alpha"
        (project / "modules").mkdir(parents=True, mode=0o700)
        (project / "default.nix").write_text('{ ... }: { nixlab.project.name = "alpha"; }')
        (project / "modules" / "network.nix").write_text("{ ... }: { nixlab.project.enable = true; }")
        (project / "secrets.enc.yaml").write_text("sops: ciphertext-test-fixture")
        self.before = tx.validate_workspace(self.workspace)["sourceHash"]
        self.state_patch = patch.object(rt, "STATE", self.state)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.parse_patch = patch.object(tx, "_parse_nix", return_value=None)
        self.parse_patch.start()
        self.addCleanup(self.parse_patch.stop)
        self.inventory = {"schemaVersion": 1, "projects": {
            "alpha": {"name": "alpha", "source": "alpha", "enable": True,
                      "backend": "incus-native", "incusProject": "alpha",
                      "resources": {"cpus": 2, "memory": "2GiB"},
                      "secrets": {"file": None}, "backup": {"enable": True, "consistency": "stop"},
                      "claims": [], "modules": ["modules/network.nix"]}}}
        self.eval_patch = patch.object(tx.project_plans, "evaluate", return_value=self.inventory)
        self.eval_patch.start()
        self.addCleanup(self.eval_patch.stop)
        self.manager = tx.ProjectTransactions(workspace=self.workspace, state=self.state)

    def edit(self, **patches):
        return self.manager.review(patches, principal="admin")

    def review(self):
        return self.edit(**{"alpha/modules/network.nix": "{ ... }: { nixlab.project.enable = false; }"})

    def test_review_does_not_touch_live_source_or_execute_host_commands(self):
        with patch.object(tx.subprocess, "run", side_effect=AssertionError("No host commands during review")):
            reviewed = self.review()
        self.assertEqual(reviewed["status"], "ready")
        self.assertEqual(reviewed["deployment"], "not-started")
        self.assertTrue(reviewed["sourceOnly"])
        self.assertEqual(tx.validate_workspace(self.workspace)["sourceHash"], self.before)
        self.assertEqual(self.manager.status()["id"], reviewed["id"])
        self.assertFalse((self.state / "active.json").exists())
        self.assertFalse((self.state / "pending.json").exists())

    def test_publish_changes_source_only_then_exact_rollback(self):
        reviewed = self.review()
        with self.assertRaisesRegex(ValueError, "exact reviewed"):
            self.manager.publish(reviewed["id"], "0" * 64)
        actual = self.manager.publish(reviewed["id"], reviewed["planHash"])
        self.assertEqual(actual["status"], "published-source")
        self.assertEqual(actual["candidateHash"], tx.validate_workspace(self.workspace)["sourceHash"])
        self.assertEqual((self.workspace / "alpha" / "secrets.enc.yaml").read_text(), "sops: ciphertext-test-fixture")
        self.assertIn("false", (self.workspace / "alpha/modules/network.nix").read_text())
        rolled = self.manager.rollback(reviewed["id"], reviewed["planHash"])
        self.assertEqual(rolled["status"], "rolled-back")
        self.assertEqual(tx.validate_workspace(self.workspace)["sourceHash"], self.before)
        self.assertEqual((self.workspace / "alpha" / "secrets.enc.yaml").read_text(), "sops: ciphertext-test-fixture")

    def test_rollback_waits_for_newer_pending_project_review(self):
        published = self.review()
        self.manager.publish(published["id"], published["planHash"])
        pending = self.edit(**{"alpha/modules/extra.nix": "{ ... }: { }"})
        with self.assertRaisesRegex(ValueError, "pending project review"):
            self.manager.rollback(published["id"], published["planHash"])
        self.manager.cancel(pending["id"])
        self.assertEqual(self.manager.rollback(published["id"], published["planHash"])["status"], "rolled-back")

    def test_creation_and_deletion_require_reviews_and_preserve_entrypoints(self):
        proposed = self.edit(**{"beta/default.nix": '{ ... }: { nixlab.project.name = "beta"; }'})
        self.manager.publish(proposed["id"], proposed["planHash"])
        self.assertTrue((self.workspace / "beta/default.nix").exists())
        with self.assertRaisesRegex(ValueError, "entrypoints"):
            self.edit(**{"beta/default.nix": None})
        created = self.edit(**{"alpha/modules/extra.nix": "{ ... }: { }"})
        self.manager.publish(created["id"], created["planHash"])
        deleted = self.edit(**{"alpha/modules/extra.nix": None})
        self.manager.publish(deleted["id"], deleted["planHash"])
        self.assertFalse((self.workspace / "alpha/modules/extra.nix").exists())

    def test_conflicting_editor_source_change_refuses_publish(self):
        reviewed = self.review()
        (self.workspace / "alpha/default.nix").write_text('{ ... }: { nixlab.project.name = "changed"; }')
        with self.assertRaisesRegex(ValueError, "changed"):
            self.manager.publish(reviewed["id"], reviewed["planHash"])
        self.assertTrue((self.workspace / "alpha/modules/network.nix").read_text().endswith("true; }"))

    def test_tampered_candidate_is_rejected(self):
        reviewed = self.review()
        path = self.state / "project-transactions" / reviewed["id"] / "source" / "alpha" / "modules" / "network.nix"
        path.chmod(0o600)
        path.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.manager.publish(reviewed["id"], reviewed["planHash"])
        self.assertEqual(tx.validate_workspace(self.workspace)["sourceHash"], self.before)

    def test_review_expiration_blocks_publish(self):
        reviewed = self.review()
        path = self.state / "project-transactions" / reviewed["id"] / "job.json"
        job = rt.read_job_record(path)
        job["expires"] = time.time() - 1
        rt.write_json(path, job)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.manager.publish(reviewed["id"], reviewed["planHash"])

    def test_unreviewed_or_concurrent_projects_are_blocked(self):
        first = self.review()
        with self.assertRaisesRegex(ValueError, "Finish or recover"):
            self.edit(**{"alpha/modules/another.nix": "{ ... }: { }"})
        self.manager.cancel(first["id"])
        again = self.review()
        self.manager.publish(again["id"], again["planHash"])
        next_job = self.edit(**{"alpha/modules/another.nix": "{ ... }: { }"})
        self.assertEqual(next_job["status"], "ready")

    def test_recovery_after_exchange_failure_keeps_live_source_accessible(self):
        reviewed = self.review()
        with patch.object(tx, "_exchange", side_effect=OSError("simulated swap failure")):
            with self.assertRaises(OSError):
                self.manager.publish(reviewed["id"], reviewed["planHash"])
        # A failed atomic swap never leaves the workspace absent.
        self.assertTrue(self.workspace.is_dir())
        recovered = self.manager.recover(reviewed["id"])
        self.assertEqual(recovered["status"], "recovered")
        self.assertEqual(tx.validate_workspace(self.workspace)["sourceHash"], self.before)

    def test_recovery_finalizes_swapped_source_after_archive_failure(self):
        reviewed = self.review()
        with patch.object(tx.os, "rename", side_effect=OSError("simulated archive failure")):
            with self.assertRaises(OSError):
                self.manager.publish(reviewed["id"], reviewed["planHash"])
        self.assertTrue(self.workspace.is_dir())
        recovered = self.manager.recover(reviewed["id"])
        self.assertEqual(recovered["status"], "published-source")
        self.assertEqual(tx.validate_workspace(self.workspace)["sourceHash"], reviewed["candidateHash"])

    def test_recovery_finalizes_completed_rename_after_journal_write_failure(self):
        reviewed = self.review()
        real_write = self.manager._write
        def fail_final(identity, record):
            if record["status"] == "published-source":
                raise OSError("simulated crash before commit journal")
            return real_write(identity, record)
        with patch.object(self.manager, "_write", side_effect=fail_final):
            with self.assertRaises(OSError):
                self.manager.publish(reviewed["id"], reviewed["planHash"])
        actual = self.manager.recover(reviewed["id"])
        self.assertEqual(actual["status"], "published-source")
        self.assertEqual(tx.validate_workspace(self.workspace)["sourceHash"], reviewed["candidateHash"])

    def test_unexpected_drift_during_recovery_is_never_deleted(self):
        reviewed = self.review()
        with patch.object(tx.os, "rename", side_effect=OSError("never renamed")):
            with self.assertRaises(OSError):
                self.manager.publish(reviewed["id"], reviewed["planHash"])
        (self.workspace / "alpha/default.nix").write_text("external")
        with self.assertRaisesRegex(ValueError, "manual recovery"):
            self.manager.recover(reviewed["id"])
        self.assertEqual((self.workspace / "alpha/default.nix").read_text(), "external")

    def test_host_activation_and_guest_deployment_are_exclusive(self):
        rt.write_json(self.state / "pending.json", {"id": "f" * 32})
        with self.assertRaisesRegex(ValueError, "host activation"):
            self.review()
        (self.state / "pending.json").unlink()
        rt.write_json(self.state / "deployment-active.json", {"id": "a" * 32})
        with self.assertRaisesRegex(ValueError, "guest deployment"):
            self.review()

    def test_unsafe_paths_plaintext_and_missing_lock_fail_closed(self):
        for filename in ("../evil.nix", "alpha/source/Dockerfile", "alpha/../default.nix",
                         "alpha/secrets.enc.yaml", "alpha/modules/_disabled.nix"):
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    self.edit(**{filename: "nix expression"})
        (self.workspace / "alpha" / ".env").write_text("TORBOX_API_KEY=NOPE")
        with self.assertRaisesRegex(ValueError, "plaintext"):
            self.review()
        (self.workspace / "alpha" / ".env").unlink()
        (self.workspace / "flake.lock").unlink()
        with self.assertRaisesRegex(ValueError, "flake.lock"):
            self.review()

    def test_symlink_and_hardlink_in_workspace_fail_closed(self):
        p = self.workspace / "alpha" / "modules" / "outside.nix"
        with tempfile.TemporaryDirectory() as temp:
            p.symlink_to(Path(temp) / "outside")
            with self.assertRaisesRegex(ValueError, "Symlink"):
                self.review()
            p.unlink()
        os.link(self.workspace / "flake.nix", self.workspace / "alias.nix")
        with self.assertRaisesRegex(ValueError, "Hardlinked"):
            self.review()

    def test_noop_deploy_uses_same_broker_and_never_touches_guest(self):
        review = self.review()
        self.manager.publish(review["id"], review["planHash"])
        with patch.object(tx.subprocess, "run", return_value=None) as spawned:
            queued = self.manager.deploy(review["id"], review["planHash"])
        self.assertEqual(queued["deployment"], "queued")
        self.assertTrue(any("project-deploy-job" in str(c) for c in spawned.call_args_list))
        finished = self.manager.run_deployment(review["id"])
        self.assertEqual(finished["deployment"], "verified")
        self.assertEqual(finished["deploymentResources"], {})
        self.assertEqual(self.manager.deploy(review["id"], review["planHash"])["deployment"], "verified")

    def test_project_deployment_blocks_host_transaction_until_verified(self):
        review = self.review()
        self.manager.publish(review["id"], review["planHash"])
        with patch.object(tx.subprocess, "run", return_value=None):
            self.manager.deploy(review["id"], review["planHash"])
        with self.assertRaisesRegex(ValueError, "active project deployment"):
            self.edit(**{"alpha/modules/extra.nix": "{ ... }: {}"})
        self.manager.run_deployment(review["id"])
        self.assertEqual(self.edit(**{"alpha/modules/extra.nix": "{ ... }: {}"})["status"], "ready")

    def test_native_deployment_failure_requires_explicit_resume(self):
        owned = dict(self.inventory["projects"]["alpha"])
        owned["claims"] = ["instance/printer"]
        self.inventory["projects"]["alpha"] = owned
        watched = {
            "project": "alpha", "name": "printer", "action": "update",
            "etag": "abc", "fingerprint": "abcd", "desiredHash": "def"
        }
        with patch.object(project_plans, "_observation", return_value=watched):
            planned = self.review()
            self.manager.publish(planned["id"], planned["planHash"])
        with patch.object(tx.subprocess, "run", return_value=None), patch.object(project_plans, "_observation", return_value=watched):
            self.manager.deploy(planned["id"], planned["planHash"])
        with patch.object(project_plans, "recheck_and_apply", side_effect=ValueError("transient denied")):
            failed = self.manager.run_deployment(planned["id"])
        self.assertEqual(failed["deployment"], "partial")
        with patch.object(tx.subprocess, "run", return_value=None):
            self.manager.deploy(planned["id"], planned["planHash"], resume=True)
        with patch.object(project_plans, "recheck_and_apply", return_value={"status": "verified", "newEtag": "new"}):
            done = self.manager.run_deployment(planned["id"])
        self.assertEqual(done["deployment"], "verified")
        self.assertEqual(done["deploymentResources"]["alpha/printer"]["phase"], "verified")

    def test_git_history_is_preserved_by_rejecting_unsafe_directory_replacement(self):
        git = self.workspace / ".git"
        git.mkdir()
        (git / "config").write_text("private local git history; never delete")
        with self.assertRaisesRegex(ValueError, "Git metadata outside"):
            self.review()
        self.assertEqual((git / "config").read_text(), "private local git history; never delete")

    def test_status_contains_only_hashes_and_paths_not_source_bytes(self):
        reviewed = self.review()
        data = json.dumps(self.manager.status(reviewed["id"]))
        self.assertNotIn("sops: ciphertext-test-fixture", data)
        self.assertNotIn("nixlab.project.enable = false", data)
        self.assertNotIn("currentPassword", data)
        self.assertTrue(reviewed["planHash"])

if __name__ == "__main__":
    unittest.main()
