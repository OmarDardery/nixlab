"""Console observations must not invent addresses, health or restore evidence."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('console_status', Path(__file__).resolve().parents[1] / 'scripts/console_status.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)


def settings():
    return {'ready': True, 'management': {'interface': 'br0', 'address': '192.0.2.10', 'configure': False},
            'setup': {'enable': True, 'port': 9440}, 'apps': {'enable': False}, 'backup': {'enable': False}}


def link(address='192.0.2.20'):
    return {'ifname': 'br0', 'addr_info': [{'family': 'inet', 'scope': 'global', 'local': address}]}


class ConsoleTests(unittest.TestCase):
    def test_uses_observed_address_and_only_enabled_endpoints(self):
        output = '\n'.join(c.lines(settings(), [link()], {}))
        self.assertIn('https://192.0.2.20:9440', output)
        self.assertIn('https://192.0.2.20:8443', output)
        self.assertNotIn('192.0.2.10:', output)
        self.assertNotIn('Arcane:', output)
        self.assertIn('Web=unavailable', output)
        self.assertIn('Backups: disabled', output)
        self.assertNotIn('healthy', output)

    def test_wrong_port_reports_bridge_without_guessing_a_url(self):
        cfg = settings(); cfg['management']['interface'] = 'eth0'
        output = '\n'.join(c.lines(cfg, [{'ifname': 'eth0', 'master': 'br0'}, link()], {}))
        self.assertIn('Address belongs on bridge br0', output)
        self.assertNotIn('https://', output)

    def test_missing_ambiguous_and_wrong_static_addresses_are_explicit(self):
        for links in ([], [link('169.254.1.2')], [dict(link(), addr_info=link()['addr_info'] + link('192.0.2.30')['addr_info'])]):
            with self.subTest(links=links):
                self.assertNotIn('https://', '\n'.join(c.lines(settings(), links, {})))
        cfg = settings(); cfg['management']['configure'] = True
        self.assertNotIn('https://', '\n'.join(c.lines(cfg, [link()], {})))

    def test_backup_default_success_is_not_a_successful_run_or_restore(self):
        cfg = settings(); cfg['backup']['enable'] = True
        units = {'restic-backups-nixlab.service': {'LoadState': 'loaded', 'Result': 'success'}}
        output = '\n'.join(c.lines(cfg, [link()], units))
        self.assertIn('no recorded run available', output)
        self.assertIn('Restore verification: not checked', output)
        units['restic-backups-nixlab.service'].update(ExecMainExitTimestamp='2026-01-01', Result='exit-code')
        self.assertIn('result=exit-code', '\n'.join(c.lines(cfg, [link()], units)))

    def test_test_activation_and_pending_kernel_are_visible(self):
        output = '\n'.join(c.lines(settings(), [link()], {}, 'new', 'old', 'new-kernel', 'old-kernel'))
        self.assertIn('differs from boot default', output)
        self.assertIn('different kernel is running', output)
        self.assertNotIn('boot default', '\n'.join(c.lines(settings(), [], {})))

    def test_timeout_is_bounded_and_terminal_controls_are_removed(self):
        with patch.object(c.subprocess, 'run', side_effect=subprocess.TimeoutExpired('systemctl', 1)) as run:
            self.assertEqual(c.command(['systemctl']), '')
        self.assertEqual(run.call_args.kwargs['timeout'], 1)
        self.assertNotIn('\x1b', c.clean('name\x1b[31m\n'))

    def test_partial_service_query_keeps_failed_state(self):
        with patch.object(c, 'command', return_value='Id=nginx.service\nLoadState=loaded\nActiveState=failed\n\nId=incus.service\nLoadState=not-found\n'):
            units = c.services(['nginx.service', 'incus.service'])
        output = '\n'.join(c.lines(settings(), [link()], units))
        self.assertIn('Proxy=failed', output)
        self.assertIn('Incus=unavailable', output)
