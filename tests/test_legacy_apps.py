"""Read-only legacy apps VM inventory: no deletion, password or lifecycle commands."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import legacy_apps


class LegacyAppsInventoryTests(unittest.TestCase):
    def test_absent_vm_is_not_interpreted_as_permission_to_delete(self):
        with patch.object(legacy_apps, "run", return_value="[]") as run:
            report = legacy_apps.inventory()
        self.assertFalse(report["present"])
        self.assertFalse(report["safeToDelete"])
        self.assertEqual(run.call_count, 1)

    def test_running_vm_reports_only_whitelisted_metadata(self):
        credentials = "SECRET_API_KEY_NEVER_PRINT"
        outputs = {
            "list": json.dumps([{"name": "apps", "status": "Running"}]),
            "query": json.dumps({
                "type": "virtual-machine",
                "config": {"user.nixlab.managed": "true", "user.token": credentials},
                "expanded_devices": {
                    "root": {"type": "disk", "pool": "guests", "path": "/", "size": "64GiB",
                             "raw": credentials},
                    "eth0": {"type": "nic", "network": "incusbr0"},
                },
            }),
            "ps": json.dumps({"Names": "aiostreams", "Image": "image:stable",
                              "State": "running", "Labels": credentials}) + "\n",
            "volume": json.dumps({"Name": "aiostreams_data", "Driver": "local",
                                  "Secrets": credentials}) + "\n",
            "compose": json.dumps([{"Name": "aiostreams", "Status": "running",
                                   "ConfigFiles": credentials}]),
        }

        def fake(*args, timeout=30):
            if "list" == args[0]: return outputs["list"]
            if args[0] == "query": return outputs["query"]
            if args[-4:-3] == ("ps",) or "ps" in args: return outputs["ps"]
            if "volume" in args: return outputs["volume"]
            if "compose" in args: return outputs["compose"]
            raise AssertionError(args)

        with patch.object(legacy_apps, "run", side_effect=fake) as run:
            report = legacy_apps.inventory()
        self.assertTrue(report["present"])
        self.assertFalse(report["safeToDelete"])
        self.assertEqual(report["docker"]["status"], "inspected")
        self.assertEqual(report["docker"]["volumes"][0]["name"], "aiostreams_data")
        self.assertEqual(report["instance"]["disks"][0]["pool"], "guests")
        self.assertEqual(report["instance"]["nixlabManaged"], True)
        self.assertNotIn(credentials, json.dumps(report))
        calls = [c.args for c in run.call_args_list]
        self.assertFalse(any("stop" in call or "start" in call or "delete" in call for call in calls))
        self.assertFalse(any("inspect" in call or "env" in call for call in calls))

    def test_stopped_vm_is_never_started_for_inspection(self):
        def fake(*args, **kwargs):
            if args[0] == "list": return '[{"name":"apps","status":"Stopped"}]'
            if args[0] == "query": return '{"type":"virtual-machine","config":{}}'
            raise AssertionError("Guest must not be started: " + str(args))
        with patch.object(legacy_apps, "run", side_effect=fake) as run:
            report = legacy_apps.inventory()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(report["docker"]["status"], "vm-not-running")
        self.assertIsNone(report["docker"]["containers"])

    def test_failed_docker_inventory_cannot_appear_empty(self):
        def fake(*args, **kwargs):
            if args[0] == "list": return '[{"name":"apps","status":"Running"}]'
            if args[0] == "query": return '{"type":"virtual-machine","config":{}}'
            raise RuntimeError("unavailable")
        with patch.object(legacy_apps, "run", side_effect=fake):
            report = legacy_apps.inventory()
        self.assertEqual(report["docker"]["status"], "unavailable-or-partial")
        self.assertIsNone(report["docker"]["containers"])
        self.assertFalse(report["safeToDelete"])

    def test_inc us_failure(self):
        pass

    def test_reject_conflicting_instance_type(self):
        def fake(*args, **kwargs):
            return ('[{"name":"apps","status":"Running"}]' if args[0] == "list"
                    else '{"type":"container","config":{}}')
        with patch.object(legacy_apps, "run", side_effect=fake):
            with self.assertRaisesRegex(ValueError, "not the expected"):
                legacy_apps.inventory()

    def test_upstream_errors_are_not_echoed(self):
        token = "PRIVATE_TOKEN"
        completed = subprocess.CompletedProcess(["incus"], 1, "", "permission denied " + token)
        with patch.object(legacy_apps.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "inventory query failed") as err:
                legacy_apps.run("list", "--project", "lab")
        self.assertNotIn(token, str(err.exception))


if __name__ == "__main__":
    unittest.main()
