"""Pure installation intent acceptance scenarios; never mutates real hardware."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ("installer", "admin", "scripts")]
import intent


def settings():
    value = json.loads((BASE / "tests/fixtures/defaults.json").read_text())
    value["management"].update(interface="enp8s0", address="192.168.1.240",
                               prefixLength=24)
    value["apps"]["memory"] = "2GiB"
    return value


def host(wireless=False, memory=16 * 1024 * 1024):
    return dict(memoryKiB=memory, interfaces=[
        dict(name="enp8s0", wireless=wireless, addresses=["192.168.1.240/24"])])


class IntentTests(unittest.TestCase):
    def test_generic_guests_compose_and_device_workloads(self):
        answers = intent.normalize(dict(workloads="both", access="private",
                                        laptop=True, backup=True))
        proposal = intent.plan(answers, host(), settings())
        self.assertTrue(proposal["apps"])
        self.assertTrue(proposal["laptop"])
        self.assertTrue(proposal["monitoring"])
        description = "\n".join(proposal["pending"])
        for feature in ("Incus", "Compose", "Tailscale", "restic",
                        "USB/serial", "LAN", "restore"):
            self.assertIn(feature, description)
        self.assertTrue(any("NAT guests" in reason for reason in proposal["reasons"]))
        for deprecated in ("klipper", "aiostreams", "adguard"):
            self.assertNotIn(deprecated, repr(proposal).lower())

    def test_compose_away_from_home_wifi_stays_private(self):
        s = settings()
        s["management"]["interface"] = "wlan0"
        h = dict(memoryKiB=8 * 1024 * 1024, interfaces=[
            dict(name="wlan0", wireless=True, addresses=["192.168.1.240/24"])])
        p = intent.plan({"workloads": "compose", "access": "private"}, h, s, remote=True)
        self.assertTrue(p["apps"])
        self.assertFalse(p["networkRequiresReview"])
        self.assertTrue(any("Tailscale" in x for x in p["pending"]))
        for kind in ("lan", "vlan"):
            with self.assertRaisesRegex(ValueError, "Wi-Fi"):
                intent.plan({"network": kind}, h, s)

    def test_lan_dns_bridge_needs_manual_review_not_auto_mutation(self):
        p = intent.plan({"workloads": "containers", "network": "lan"},
                        host(), settings())
        self.assertFalse(p["apps"])
        self.assertTrue(p["networkRequiresReview"])
        self.assertTrue(any("no host NIC is modified" in x for x in p["pending"]))

    def test_public_apps_never_automatically_expose_management(self):
        p = intent.plan({"access": "public"}, host(), settings())
        self.assertTrue(any("never SSH/Incus/Cockpit/NixLab" in x for x in p["pending"]))

    def test_insufficient_ram_and_network_collision_fail_before_apply(self):
        with self.assertRaisesRegex(ValueError, "RAM"):
            intent.plan({"workloads": "both"}, host(memory=2 * 1024 * 1024), settings())
        with self.assertRaisesRegex(ValueError, "overlaps"):
            intent.plan({"guestSubnet": "192.168.1.0/24"}, host(), settings())
        with self.assertRaisesRegex(ValueError, "local-console"):
            intent.plan({"network": "lan"}, host(), settings(), remote=True)

    def test_no_secrets_unknown_keys_invalid_values_or_unsafe_subnets(self):
        for raw in ({"password": "secret"}, {"version": 3}, {"adguard": "yes"},
                    {"guestSubnet": "8.8.8.0/24"},
                    {"guestSubnet": "10.90.0.0/16"}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                intent.normalize(raw)
        # Old v1 answer files are readable, but their obsolete app flags
        # are discarded rather than becoming installed workloads.
        old = intent.normalize({"version": 1, "klipper": True,
                                "aiostreams": True, "adguard": True})
        self.assertEqual(set(old), set(intent.defaults()))
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "answers.json"
            p.write_text(json.dumps({"version": 1, "workloads": "containers"}))
            self.assertEqual(intent.load(p)["workloads"], "containers")
            p.write_text('{"SECRET_KEY":"private"}')
            with self.assertRaises(ValueError): intent.load(p)

    def test_interview_defaults_and_explicit_choices_produce_equal_plans(self):
        replies = iter(["both", "private", "nat", "y", "n", "y"])
        values = intent.interview(input_fn=lambda _: next(replies), output=lambda _: None)
        self.assertEqual(values, intent.normalize(dict(
            workloads="both", access="private", network="nat",
            backup=True, laptop=False, monitoring=True)))
        self.assertEqual(intent.plan(values, host(), settings()),
                         intent.plan(json.loads(json.dumps(values)), host(), settings()))

    def test_interrupted_interview_resumes_last_nonsecret_answer(self):
        snapshots = []
        attempts = iter(["containers", "private"])
        def interrupted(_):
            try:
                return next(attempts)
            except StopIteration:
                raise EOFError("terminal disconnected")
        with self.assertRaises(EOFError):
            intent.interview(input_fn=interrupted, output=lambda _: None,
                             on_answer=lambda answer: snapshots.append(answer))
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[-1]["workloads"], "containers")
        self.assertEqual(snapshots[-1]["access"], "private")
        resumed = intent.interview(input_fn=lambda _: "",
                                   output=lambda _: None, existing=snapshots[-1])
        self.assertEqual(resumed["workloads"], "containers")
        self.assertEqual(resumed["access"], "private")

    def test_apply_is_pure_and_only_touches_typed_owned_settings(self):
        original = settings()
        candidate = copy.deepcopy(original)
        proposal = intent.plan({"workloads": "both", "laptop": True,
                                "monitoring": False}, host(), original)
        modified = intent.apply(candidate, proposal)
        self.assertEqual(original, candidate)
        self.assertTrue(modified["maintenance"]["laptopMode"])
        self.assertFalse(modified["monitoring"]["collection"])
        for key in ("management", "guests", "mounts", "instances", "tunnel", "backup", "access"):
            self.assertEqual(original[key], modified[key])


if __name__ == "__main__":
    unittest.main()
