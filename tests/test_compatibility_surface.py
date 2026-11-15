"""Freeze public entrypoints and durable layout before architecture simplification.

This is a *characterization* fixture, not a promise to preserve insecure defaults.
Behavioral guards for transactions, worker loss and Incus ownership live in the
related suites listed by the manifest and in the NixOS integration fixtures.
"""
import ast
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / "admin"), str(BASE / "scripts")]
import runtime as rt

CONTRACT = json.loads((BASE / "tests/fixtures/architecture-contract.json").read_text())


def module(path):
    return ast.parse((BASE / path).read_text(), filename=path)


def named_class(tree, name):
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef) and node.name == name)


def named_method(klass, name):
    return next(node for node in klass.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def literal_strings(node):
    return {value.value for value in ast.walk(node)
            if isinstance(value, ast.Constant) and isinstance(value.value, str)}


class PublicCompatibilityTests(unittest.TestCase):
    def test_manifest_version_and_regression_owners(self):
        self.assertEqual(CONTRACT["version"], 1)
        for path in CONTRACT["regressionSuites"]:
            with self.subTest(path=path):
                self.assertTrue((BASE / path).is_file(), path)
        self.assertEqual(len(CONTRACT["regressionSuites"]),
                         len(set(CONTRACT["regressionSuites"])))

    def test_published_flake_attribute_names_have_not_disappeared(self):
        # Source-level guard; the Nix evaluations in validate.yml verify semantics.
        source = (BASE / "flake.nix").read_text()
        self.assertIn("nixosConfigurations =", source)
        self.assertIn("checks.x86_64-linux =", source)
        for attribute in CONTRACT["flakeAttributes"]:
            leaf = attribute.split(".")[-1]
            with self.subTest(attribute=attribute):
                assignment = re.search(r"\b" + re.escape(leaf) + r"\s*=", source)
                inherited = re.search(r"\binherit\s+[^;]*\b" + re.escape(leaf) + r"\b\s*;", source)
                self.assertTrue(assignment or inherited, attribute)

    def test_public_cli_binaries_still_have_packaged_entrypoints(self):
        for executable, path in CONTRACT["cliBinaries"].items():
            with self.subTest(executable=executable):
                source = (BASE / path).read_text()
                self.assertIn('name = "' + executable + '"', source)
                self.assertIn('exec python3 ', source)
        admin = (BASE / "admin/main.py").read_text()
        for command in ("config", "guests", "recovery", "doctor", "guard"):
            with self.subTest(command=command):
                self.assertIn("'" + command + "'", admin)

    def test_frontend_routes_remain_present_in_executable_methods(self):
        # Parse AST rather than finding strings in docs/comments.
        frontend = named_class(module("admin/main.py"), "Frontend")
        for method, key in (("do_GET", "get"), ("do_POST", "post")):
            present = literal_strings(named_method(frontend, method))
            for route in CONTRACT["portalRoutes"][key]:
                with self.subTest(method=method, route=route):
                    self.assertIn(route, present)

    def test_status_response_keeps_previous_public_keys(self):
        manager = named_class(module("admin/controller.py"), "Manager")
        status = named_method(manager, "status")
        returned = [node.value for node in ast.walk(status)
                    if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)]
        self.assertTrue(returned)
        keys = {key.value for response in returned for key in response.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)}
        self.assertTrue(set(CONTRACT["statusFields"]) <= keys)

    def test_runtime_default_paths_remain_explicit(self):
        source = (BASE / "admin/runtime.py").read_text()
        for env, default in CONTRACT["runtimeDefaults"].items():
            with self.subTest(env=env):
                self.assertIn("os.environ.get('" + env + "', '" + default + "')", source)

    def test_old_and_current_machine_paths_are_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "source"
            root.mkdir()
            for name, expected in CONTRACT["machinePaths"]["legacy"].items():
                with self.subTest(layout="legacy", key=name):
                    self.assertEqual(rt.machine_path(root, name), root / expected)
            rt.write_json(root / "machine/format.json", {"version": 1})
            for name, expected in CONTRACT["machinePaths"]["modern"].items():
                with self.subTest(layout="modern", key=name):
                    self.assertEqual(rt.machine_path(root, name), root / expected)
            rt.write_json(root / "machine/format.json", {"version": 99})
            with self.assertRaisesRegex(ValueError, "Unsupported machine directory version"):
                rt.machine_path(root, "settings")

    def test_snapshot_preserves_legacy_operator_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "source"
            root.mkdir()
            rt.write_json(root / ".nixlab-files.json", ["flake.nix"])
            rt.atomic(root / "flake.nix", "{ }")
            rt.write_json(root / "local/settings.json", {"management": {"interface": "eth0"}})
            rt.atomic(root / "site-modules/operator.nix", "{ ... }: { }")
            rt.atomic(root / "installed-system/configuration.nix", "{ ... }: { }")
            target = Path(folder) / "snapshot"
            rt.snapshot(target, root)
            for name in ("flake.nix", "local/settings.json",
                         "site-modules/operator.nix", "installed-system/configuration.nix"):
                with self.subTest(name=name):
                    self.assertEqual((root / name).read_bytes(), (target / name).read_bytes())
                    self.assertIn(name, rt.read_json(target / ".nixlab-files.json"))

    def test_job_state_rejects_path_traversal_and_symlink_aliases(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "jobs"
            root.mkdir()
            valid = "a" * 32
            (root / valid).mkdir()
            self.assertEqual(rt.job_directory(root, valid), root / valid)
            for candidate in ("../etc", "b" * 31, "B" * 32, valid + "/job.json"):
                with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                    rt.job_directory(root, candidate)
            (root / ("b" * 32)).symlink_to(root / valid, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "Unknown transaction"):
                rt.job_directory(root, "b" * 32)

    def test_identity_and_recovery_systemd_names_are_traceable(self):
        for path, units in CONTRACT["systemdUnits"].items():
            source = (BASE / path).read_text()
            for unit in units:
                with self.subTest(path=path, unit=unit):
                    self.assertRegex(source, r"\b" + re.escape(unit) + r"\s*=")
        for record in CONTRACT["rootStateRecords"]:
            with self.subTest(record=record):
                self.assertTrue(record and not Path(record).is_absolute())
                self.assertEqual(Path(record).parts, (record,))


if __name__ == "__main__":
    unittest.main()
