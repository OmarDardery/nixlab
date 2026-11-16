"""Read-only migration inventory contract: bounded API, no secret or workload mutations."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "admin"), str(ROOT / "scripts")]
import app_migration as migration

SETTINGS = {"apps": {"enable": True, "portainerPort": 9443}}
STACK = {"Id": 12, "Name": "media", "Type": 2, "Status": 1,
         "GitConfig": {"URL": "https://private.example/repo", "Authentication": {"Password": "secret"}},
         "Env": [{"name": "API_TOKEN", "value": "do-not-persist"},
                 {"name": "TZ", "value": "UTC"}]}
COMPOSE = {"StackFileContent": "services:\n  app:\n    environment:\n      - SECRET=do-not-persist\n"}


class AppMigrationTests(unittest.TestCase):
    def test_only_allows_read_only_stack_paths(self):
        for path in ("/api/stacks/12", "/api/stacks/12/start", "/api/containers",
                     "/api/stacks/-1/file", "/api/stacks/0/file"):
            with self.subTest(path=path):
                with self.assertRaises(migration.InventoryError):
                    migration._request(SETTINGS, path)

    def test_excludes_secret_values_and_compose_file(self):
        requests = []
        def fake(_settings, path, **kwargs):
            requests.append((path, kwargs))
            if path == "/api/stacks":
                return [STACK]
            if path == "/api/stacks/12/file":
                return COMPOSE
            self.fail("Unexpected endpoint")
        with patch.object(migration, "_token", return_value="not-persisted"), \
                patch.object(migration, "_request", side_effect=fake):
            result = migration.inventory(SETTINGS)
        self.assertEqual(result["stackCount"], 1)
        self.assertFalse(result["safeToSwitch"])
        self.assertTrue(result["stacks"][0]["manualReviewRequired"])
        self.assertEqual(result["stacks"][0]["environmentVariableNames"], ["API_TOKEN", "TZ"])
        serialized = json.dumps(result)
        for secret in ("do-not-persist", "private.example", "not-persisted", "StackFileContent", "Authentication"):
            self.assertNotIn(secret, serialized)
        self.assertEqual([path for path, _ in requests],
                         ["/api/stacks", "/api/stacks/12/file"])
        self.assertTrue(all(k["bearer"] == "not-persisted" for _, k in requests))

    def test_fails_closed_on_missing_stack_file(self):
        with patch.object(migration, "_token", return_value="token"), \
                patch.object(migration, "_request", side_effect=[[STACK], {}]):
            with self.assertRaises(migration.InventoryError):
                migration.inventory(SETTINGS)

    def test_refuses_duplicate_ids(self):
        with patch.object(migration, "_token", return_value="token"), \
                patch.object(migration, "_request", side_effect=[[STACK, STACK], COMPOSE]):
            with self.assertRaises(migration.InventoryError):
                migration.inventory(SETTINGS)

    def test_bounds_and_requires_apps(self):
        with self.assertRaises(migration.InventoryError):
            migration.inventory({"apps": {"enable": False}})
        with patch.object(migration, "_token", return_value="token"), \
                patch.object(migration, "_request", return_value=[STACK] * (migration.MAX_STACKS + 1)):
            with self.assertRaises(migration.InventoryError):
                migration.inventory(SETTINGS)

    def test_root_only_report_contains_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(migration.rt, "STATE", Path(folder)), \
                    patch.object(migration, "inventory", return_value={
                        "format": 1, "complete": True, "safeToSwitch": False,
                        "stackCount": 1, "stacks": [{"name": "media"}]
                    }):
                summary = migration.collect(SETTINGS)
                report = Path(summary["report"])
                self.assertEqual(report.name, migration.REPORT)
                self.assertEqual(report.stat().st_mode & 0o777, 0o600)
                self.assertFalse(summary["safeToSwitch"])
                self.assertEqual(json.loads(report.read_text())["stacks"][0]["name"], "media")


if __name__ == "__main__":
    unittest.main()
