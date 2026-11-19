"""Restricted Nix inventory and owned existing-native Incus adapter tests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "admin"))
sys.path.insert(0, str(BASE / "scripts"))
import project_plans as pp
import reconcile


def inventory(*, claims=None, backend="incus-native", cpus=3):
    return {"schemaVersion": 1, "projects": {"printer": {
        "name": "printer", "source": "printer", "enable": True,
        "backend": backend, "incusProject": "printer",
        "resources": {"cpus": cpus, "memory": "2GiB"},
        "secrets": {"file": "secrets.enc.yaml", "bindings": {}},
        "backup": {"enable": True, "consistency": "stop"},
        "claims": [] if claims is None else claims,
        "modules": ["modules/instance.nix"]
    }}}


def current_owned():
    applied = {"version": 1, "config": {
        "limits.cpu": "2", "limits.memory": "2GiB"},
        "devices": {}, "profiles": ["default"]}
    return {"type": "container", "status": "Running",
            "config": {"user.nixlab.managed": "true",
                       "user.nixlab.last-applied": json.dumps(applied, sort_keys=True, separators=(",", ":")),
                       "limits.cpu": "2", "limits.memory": "2GiB"},
            "devices": {}, "profiles": ["default"]}


class ProjectInventoryTests(unittest.TestCase):
    def test_restricted_evaluator_does_not_share_root_or_network(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / "flake.nix").write_text("{ outputs = { self }: {}; }")
            (base / "flake.lock").write_text("{}")
            expected = inventory()
            pinned = "/nix/store/" + ("a" * 32) + "-source"
            observed = []
            def observe(arguments, **kwargs):
                observed.append(arguments)
                if arguments[:2] == ["nix-store", "--add"]:
                    self.assertEqual(arguments[2], str(base))
                    return CompletedProcess(arguments, 0, (pinned + "\n").encode(), b"")
                self.assertEqual(arguments[0], "systemd-run")
                self.assertIn("--property=DynamicUser=yes", arguments)
                self.assertIn("--property=PrivateNetwork=yes", arguments)
                self.assertIn("--property=ProtectSystem=strict", arguments)
                self.assertIn("--property=NoNewPrivileges=yes", arguments)
                self.assertIn("--option", arguments)
                self.assertIn("restrict-eval", arguments)
                self.assertIn("--setenv=NIX_REMOTE=local?read-only=true", arguments)
                self.assertIn("nix-instantiate", " ".join(arguments))
                self.assertIn("--expr", arguments)
                self.assertIn("root = " + pinned, arguments[-1])
                self.assertNotIn("nix eval", " ".join(arguments))
                self.assertNotIn("flake.nix", arguments[-1])
                self.assertFalse(any("nixos-rebuild" in part or "incus" == part or "docker" == part for part in arguments))
                self.assertFalse(any(part.startswith("--property=BindPaths") for part in arguments))
                return CompletedProcess(arguments, 0, json.dumps(expected).encode(), b"")
            configured = {"NIXLAB_PROJECT_EVAL_LIB": "/nix/store/" + "b" * 32 + "-evaluator.nix",
                          "NIXLAB_PROJECT_NIXPKGS_LIB": "/nix/store/" + "c" * 32 + "-nixpkgs/lib"}
            with patch.dict(pp.os.environ, configured), patch.object(pp, "validate_workspace", return_value={"sourceHash": "same"}), patch.object(pp.subprocess, "run", side_effect=observe):
                actual = pp.evaluate(base, "a" * 32)
            self.assertEqual(actual, expected)
            self.assertEqual(len(observed), 2)

    def test_unavailable_evaluator_does_not_fall_back_to_root_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "flake.nix").write_text("{}")
            pinned = "/nix/store/" + ("a" * 32) + "-source"
            def fail_rootless(arguments, **kwargs):
                if arguments[0] == "nix-store":
                    return CompletedProcess(arguments, 0, (pinned + "\n").encode(), b"")
                return CompletedProcess(arguments, 1, b"", b"private data")
            configured = {"NIXLAB_PROJECT_EVAL_LIB": "/nix/store/" + "b" * 32 + "-evaluator.nix",
                          "NIXLAB_PROJECT_NIXPKGS_LIB": "/nix/store/" + "c" * 32 + "-nixpkgs/lib"}
            with patch.dict(pp.os.environ, configured), patch.object(pp, "validate_workspace", return_value={"sourceHash": "same"}), patch.object(pp.subprocess, "run", side_effect=fail_rootless) as called:
                with self.assertRaisesRegex(ValueError, "Restricted project evaluation failed"):
                    pp.evaluate(temp, "b" * 32)
            self.assertEqual(called.call_count, 2)

    def test_modified_store_source_is_rejected_before_untrusted_nix_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            pinned = "/nix/store/" + ("a" * 32) + "-source"
            with patch.object(pp, "validate_workspace", side_effect=[
                {"sourceHash": "candidate"}, {"sourceHash": "tampered"}
            ]), patch.object(pp.subprocess, "run", return_value=CompletedProcess(
                ["nix-store"], 0, (pinned + "\n").encode(), b""
            )) as run:
                with self.assertRaisesRegex(ValueError, "differs from reviewed"):
                    pp.evaluate(temp, "b" * 32)
                self.assertEqual(run.call_count, 1)

    def test_inventory_rejects_unknown_fields_unbounded_values_and_resource_collisions(self):
        good = inventory()
        self.assertEqual(pp.validate_inventory(good), good)
        for mutate in (
            lambda x: x["projects"]["printer"].update(password="plaintext"),
            lambda x: x["projects"]["printer"]["resources"].update(cpus=0),
            lambda x: x["projects"]["printer"]["resources"].update(memory="infinite"),
            lambda x: x["projects"]["printer"].update(backend="docker"),
            lambda x: x["projects"]["printer"].update(claims=["disk/../../host"]),
            lambda x: x["projects"]["printer"].update(claims=["instance/a", "instance/a"]),
        ):
            from copy import deepcopy
            item = deepcopy(good)
            mutate(item)
            with self.assertRaises(ValueError):
                pp.validate_inventory(item)

    def test_unsupported_compose_volume_and_network_claims_do_not_mutate_incus(self):
        for backend, claims in (
            ("incus-compose", ["instance/app"]),
            ("incus-native", ["volume/data"]),
            ("incus-native", ["network/private"]),
        ):
            with self.subTest(backend=backend, claims=claims):
                with patch.object(reconcile, "read_instance", side_effect=AssertionError("should not inspect")):
                    with self.assertRaisesRegex(ValueError, "#133"):
                        pp.plan(pp.validate_inventory(inventory(backend=backend, claims=claims)))


class OwnedNativeIncusTests(unittest.TestCase):
    def setUp(self):
        self.claims_patch = patch.object(pp, "_host_claims", return_value=set())
        self.claims_patch.start()
        self.addCleanup(self.claims_patch.stop)

    def test_existing_owned_cpu_limit_update_etag_rechecked_and_verified(self):
        state = current_owned()
        records = [state]
        def read(project, name):
            self.assertEqual((project, name), ("printer", "toolhead"))
            return records[-1], "etag-" + str(len(records))
        def write(project, name, update, etag):
            self.assertEqual((project, name, etag), ("printer", "toolhead", "etag-1"))
            self.assertEqual(update["config"]["limits.cpu"], "3")
            records.append({**state, "config": update["config"], "devices": update["devices"], "profiles": update["profiles"]})
        with patch.object(reconcile, "read_instance", side_effect=read), patch.object(reconcile, "write_instance", side_effect=write) as written:
            desired = pp.validate_inventory(inventory(claims=["instance/toolhead"]))
            planned = pp.plan(desired)
            self.assertEqual(planned["actions"][0]["action"], "update")
            result = pp.recheck_and_apply(desired["projects"]["printer"], planned["resources"][0])
            self.assertEqual(result["status"], "verified")
            self.assertEqual(written.call_count, 1)
            self.assertEqual(pp.plan(desired)["actions"][0]["action"], "noop")

    def test_unexpected_guest_shutdown_is_partial_not_verified(self):
        state = current_owned()
        records = [state]
        def read(project, name):
            return records[-1], "etag-" + str(len(records))
        def write(project, name, update, etag):
            records.append({**state, "status": "Stopped",
                            "config": update["config"], "devices": update["devices"],
                            "profiles": update["profiles"]})
        desired = pp.validate_inventory(inventory(claims=["instance/toolhead"]))
        with patch.object(reconcile, "read_instance", side_effect=read), patch.object(reconcile, "write_instance", side_effect=write):
            planned = pp.plan(desired)["resources"][0]
            with self.assertRaisesRegex(ValueError, "stopped during configuration"):
                pp.recheck_and_apply(desired["projects"]["printer"], planned)

    def test_owned_instance_identity_and_drift_are_fail_closed(self):
        raw = current_owned()
        bad = dict(raw)
        bad["config"] = dict(raw["config"], **{"user.nixlab.managed": "false"})
        with patch.object(reconcile, "read_instance", return_value=(bad, "etag")):
            with self.assertRaisesRegex(ValueError, "Refusing to adopt"):
                pp.plan(pp.validate_inventory(inventory(claims=["instance/toolhead"])))
        with patch.object(reconcile, "read_instance", return_value=(raw, "etag")):
            desired = pp.validate_inventory(inventory(claims=["instance/toolhead"]))
            reviewed = pp.plan(desired)["resources"][0]
        with patch.object(reconcile, "read_instance", return_value=(raw, "new-etag")), patch.object(reconcile, "write_instance") as write:
            with self.assertRaisesRegex(ValueError, "changed since"):
                pp.recheck_and_apply(desired["projects"]["printer"], reviewed)
            write.assert_not_called()

    def test_host_owned_legacy_lab_cannot_be_adopted(self):
        desired = inventory(claims=["instance/toolhead"])
        desired["projects"]["printer"]["incusProject"] = "lab"
        with patch.object(reconcile, "read_instance") as reader:
            with self.assertRaisesRegex(ValueError, "Legacy lab project"):
                pp.plan(pp.validate_inventory(desired))
            reader.assert_not_called()

    def test_host_owned_nonlegacy_instance_cannot_be_adopted(self):
        desired = pp.validate_inventory(inventory(claims=["instance/toolhead"]))
        with patch.object(pp, "_host_claims", return_value={("printer", "toolhead")}), patch.object(reconcile, "read_instance") as reader:
            with self.assertRaisesRegex(ValueError, "already host-flake owned"):
                pp.plan(desired)
            reader.assert_not_called()

    def test_removed_claim_never_stops_or_deletes_existing_guest(self):
        desired = pp.validate_inventory(inventory(claims=[]))
        with patch.object(reconcile, "read_instance", side_effect=AssertionError("must not touch removed instance")):
            self.assertEqual(pp.plan(desired)["actions"], [])

if __name__ == "__main__":
    unittest.main()
