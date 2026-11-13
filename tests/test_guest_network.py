"""Address visibility is read-only, sanitized and scoped to actual Incus networks."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ('admin', 'scripts')]
import guest_network as network


def guest(name='web', project='lab', bridge='private', ip='10.77.0.5'):
    return {'name': name, 'project': project, 'type': 'container', 'status': 'Running',
            'config': {'user.secret': 'DO-NOT-EXPORT', 'volatile.eth0.hwaddr': '00:11:22:33:44:55'},
            'expanded_devices': {'eth0': {'type': 'nic', 'network': bridge, 'ipv4.address': ip}},
            'state': {'network': {'eth0': {'hwaddr': '00:11:22:33:44:55',
                                          'addresses': [{'address': ip, 'scope': 'global'}]},
                                  'docker0': {'addresses': [{'address': '172.17.0.1', 'scope': 'global'}]}}}}


class GuestNetworkTests(unittest.TestCase):
    def test_reported_and_reserved_ips_are_separate_and_secrets_are_omitted(self):
        value = guest(); value['expanded_devices']['eth0']['ipv4.address'] = '10.77.0.9'
        result = network.summarize([value], [{'name': 'lab'}], now=123)
        nic = result['guests'][0]['nics'][0]
        self.assertEqual(nic['observed'], ['10.77.0.5']); self.assertEqual(nic['reserved'], ['10.77.0.9'])
        self.assertTrue(nic['unobservedReservation'])
        self.assertNotIn('DO-NOT-EXPORT', json.dumps(result)); self.assertNotIn('hwaddr', json.dumps(result))
        self.assertNotIn('172.17.0.1', json.dumps(result), 'Docker-internal addresses are not guest endpoints')
        self.assertEqual(result['checkedAt'], 123)

    def test_shared_project_networks_detect_duplicate_reservations(self):
        result = network.summarize([guest(), guest('db', 'other')], [{'name': 'lab'}, {'name': 'other'}])
        self.assertEqual(len(result['conflicts']), 1)
        self.assertEqual(result['conflicts'][0]['owners'], ['lab/web/eth0', 'other/db/eth0'])
        value = guest('db', 'other'); value['expanded_devices']['eth0'].pop('ipv4.address')
        result = network.summarize([guest(), value], [{'name': 'lab'}, {'name': 'other'}])
        self.assertEqual(len(result['conflicts']), 1, 'Reported duplicate addresses also matter')

    def test_identical_ips_on_isolated_networks_are_not_conflicts(self):
        for second, projects in ((guest('db', 'other'), [{'name': 'lab'}, {'name': 'other', 'config': {'features.networks': 'true'}}]),
                                 (guest('db', bridge='isolated'), [{'name': 'lab'}]),
                                 (guest('db', project='unknown'), [{'name': 'lab'}])):
            with self.subTest(second=second['project']):
                self.assertEqual(network.summarize([guest(), second], projects)['conflicts'], [])

    def test_vm_interface_names_match_by_mac_and_stopped_state_is_not_current(self):
        value = guest(); value['state']['network']['enp5s0'] = value['state']['network'].pop('eth0')
        self.assertEqual(network.summarize([value], [])['guests'][0]['nics'][0]['observed'], ['10.77.0.5'])
        value['status'] = 'Stopped'
        row = network.summarize([value], [])['guests'][0]
        self.assertEqual(row['observed'], []); self.assertEqual(row['nics'][0]['reserved'], ['10.77.0.5'])

    def test_missing_agent_and_invalid_addresses_remain_unknown(self):
        value = guest(); value['state'] = None
        self.assertEqual(network.summarize([value], [])['guests'][0]['observed'], [])
        for ip in ('none', 'auto', '127.0.0.1', 'fe80::1', '::', '224.0.0.1', '<img>', True, None):
            with self.subTest(ip=ip): self.assertIsNone(network.address(ip))
        self.assertEqual(network.address('fd00::0010'), 'fd00::10')

    def test_inventory_uses_only_bounded_readonly_requests_and_redacts_errors(self):
        with patch.object(network.incus_client, 'run', side_effect=[json.dumps([guest()]), '[{"name":"lab"}]']) as run:
            self.assertTrue(network.inventory()['available'])
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [['query', '/1.0/instances?recursion=2&all-projects=true'], ['query', '/1.0/projects?recursion=1']])
        self.assertTrue(all(call.args[1] == 10 for call in run.call_args_list))
        for error in (ValueError('DO-NOT-EXPORT'), subprocess.TimeoutExpired('incus', 10)):
            with patch.object(network.incus_client, 'run', side_effect=error), self.assertRaisesRegex(ValueError, '^Guest network inventory unavailable'):
                network.inventory()


if __name__ == '__main__': unittest.main()
