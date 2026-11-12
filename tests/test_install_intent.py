"""Pure installation intent acceptance scenarios; never mutates real hardware."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "installer"))
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
    def test_homelab_dns_printer_and_aiostreams(self):
        answers = intent.normalize(dict(workloads="both", access="private",
                                        adguard=True, klipper=True,
                                        aiostreams=True, laptop=True))
        proposal = intent.plan(answers, host(), settings())
        self.assertTrue(proposal["apps"])
        self.assertTrue(proposal["laptop"])
        self.assertTrue(proposal["monitoring"])
        text = "\n".join(proposal["pending"])
        for feature in ("AdGuard", "Klipper", "AIOStreams", "Tailscale",
                        "restic", "router DHCP DNS", "serial", "53"):
            self.assertIn(feature, text)
        self.assertTrue(any("NAT alone" in reason for reason in proposal["reasons"]))

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
        p = intent.plan({"workloads": "containers", "network": "lan", "adguard": True},
                        host(), settings())
        self.assertFalse(p["apps"])
        self.assertTrue(p["networkRequiresReview"])
        self.assertTrue(any("no host NIC is modified" in x for x in p["pending"]))

    def test_public_apps_never_automatically_expose_management(self):
        p = intent.plan({"access": "public", "aiostreams": True}, host(), settings())
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
                    {"workloads": "containers", "aiostreams": True},
                    {"guestSubnet": "8.8.8.0/24"},
                    {"guestSubnet": "10.90.0.0/16"}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                intent.normalize(raw)
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "answers.json"
            p.write_text(json.dumps({"version": 1, "workloads": "containers"}))
            self.assertEqual(intent.load(p)["workloads"], "containers")
            p.write_text('{"SECRET_KEY":"private"}')
            with self.assertRaises(ValueError): intent.load(p)

    def test_interview_defaults_and_explicit_choices_produce_equal_plans(self):
        replies = iter(["both", "private", "nat", "y", "y", "y", "n", "y"])
        values = intent.interview(input_fn=lambda _: next(replies), output=lambda _: None)
        self.assertEqual(values, intent.normalize(dict(
            workloads="both", access="private", network="nat",
            adguard=True, klipper=True, aiostreams=True, laptop=False,
            monitoring=True)))
        self.assertEqual(intent.plan(values, host(), settings()),
                         intent.plan(json.loads(json.dumps(values)), host(), settings()))

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
