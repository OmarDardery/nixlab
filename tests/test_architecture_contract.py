"""Characterize configuration preservation without freezing insecure defaults."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import model
import runtime


class ConfigurationContractTests(unittest.TestCase):
    def test_nested_overlay_preserves_unrelated_settings_and_replaces_lists(self):
        defaults = {'management': {'interface': 'eth0', 'trustedCIDRs': ['192.0.2.0/24']},
                    'adminKeys': ['old'], 'other': {'enabled': True}}
        overlay = {'management': {'trustedCIDRs': ['198.51.100.4/32']}, 'adminKeys': ['new']}
        original_defaults, original_overlay = copy.deepcopy(defaults), copy.deepcopy(overlay)
        result = model.merge(defaults, overlay)
        self.assertEqual(result['management'], {'interface': 'eth0', 'trustedCIDRs': ['198.51.100.4/32']})
        self.assertEqual(result['adminKeys'], ['new'])
        self.assertEqual(result['other'], {'enabled': True})
        result['other']['enabled'] = False
        self.assertEqual(defaults, original_defaults)
        self.assertEqual(overlay, original_overlay)

    def test_snapshot_preserves_machine_settings_and_installed_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'source'
            root.mkdir()
            runtime.write_json(root / '.nixlab-files.json', ['flake.nix'])
            runtime.atomic(root / 'flake.nix', '{ }')
            runtime.write_json(root / 'machine/format.json', {'version': 1})
            settings = {'management': {'interface': 'enp1s0'}}
            runtime.write_json(root / 'machine/settings.json', settings)
            runtime.atomic(root / 'machine/modules/custom.nix', '{ ... }: { }')
            runtime.atomic(root / 'machine/installed-system/configuration.nix', '{ ... }: { }')
            destination = Path(folder) / 'snapshot'
            runtime.snapshot(destination, root)
            self.assertEqual(json.loads((destination / 'machine/settings.json').read_text()), settings)
            self.assertTrue((destination / 'machine/modules/custom.nix').is_file())
            self.assertTrue((destination / 'machine/installed-system/configuration.nix').is_file())
            self.assertEqual((destination / 'flake.nix').read_text(), (root / 'flake.nix').read_text())
            manifest = json.loads((destination / '.nixlab-files.json').read_text())
            self.assertIn('machine/settings.json', manifest)
            self.assertIn('machine/installed-system/configuration.nix', manifest)


if __name__ == '__main__':
    unittest.main()
