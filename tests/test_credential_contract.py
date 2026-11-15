"""Exercise safe boundaries; this is not an all-clear for known delivery defects."""
import base64
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ('admin', 'scripts')]
from credential_fixtures import SECRETS, assert_public_clean, credential_exposures
import accounts
import main
from main import Auth
import portainer
import runtime as rt


class CredentialContractTests(unittest.TestCase):
    def test_scanner_detects_nested_keys_values_and_encodings_without_echo(self):
        self.assertEqual(credential_exposures({'items': list(SECRETS.values())}), set(SECRETS))
        for category, secret in SECRETS.items():
            for value in (secret, secret.encode(), quote(secret, safe=''),
                          base64.b64encode(secret.encode()).decode(), secret.encode().hex()):
                with self.subTest(category=category):
                    self.assertEqual(credential_exposures({value: ['metadata']}), {category})
        self.assertEqual(credential_exposures({'status': 'ready', 'count': 7, 'data': None}), set())
        with self.assertRaises(AssertionError) as error:
            assert_public_clean(self, {'error': SECRETS['private-key']})
        self.assertNotIn(SECRETS['private-key'], str(error.exception))

    def test_failed_portainer_command_does_not_return_helper_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                patch.object(portainer.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    ['incus'], 1, stdout=SECRETS['portainer'], stderr=SECRETS['private-key'])):
            with self.assertRaises(ValueError) as error:
                portainer.incus('exec', 'apps')
        assert_public_clean(self, {'stdout-stderr': output.getvalue(), 'error': str(error.exception)})

    def test_invalid_secret_input_errors_do_not_echo_value(self):
        with self.assertRaises(ValueError) as error:
            rt.encode_secrets({'unsupported': SECRETS['staged']}, {'secrets': {'enable': False}})
        assert_public_clean(self, {'error': str(error.exception)})

    def test_authentication_and_account_metadata_do_not_disclose_passwords(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), \
                patch.object(rt.grp, 'getgrnam', return_value=Mock(gr_gid=0)), patch.object(rt.os, 'chown'):
            rt.write_json(rt.STATE / 'auth.json', rt.password_record(SECRETS['setup']))
            accounts.update('observer', 'viewer', SECRETS['operator'])
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaises(ValueError) as error:
                    Auth().login(SECRETS['portainer'], '192.0.2.44')
            assert_public_clean(self, {
                'accounts': accounts.list_users(), 'authRecord': rt.read_json(rt.STATE / 'auth.json'),
                'namedAccounts': rt.read_json(rt.STATE / 'users.json'),
                'audit': (rt.STATE / 'audit.jsonl').read_text(),
                'error': str(error.exception), 'output': output.getvalue(),
            })

    def test_broker_errors_hide_hostile_system_and_mapping_details(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(main, 'SOCKET', Path(folder) / 'broker.sock'), \
                patch.object(main, 'UnixHTTPServer') as server, \
                patch.object(main.os, 'chmod'), patch('controller.Manager') as manager, \
                patch('operations.Operations'), patch.object(rt, 'audit'), \
                patch.object(main, 'reply') as response, patch.object(main, 'json_body', return_value={'id': 'a' * 32}):
            main.backend()
            handler = object.__new__(server.call_args.args[1])
            for method, path, action, error, status in (
                (handler.do_GET, '/status', manager.return_value.status, OSError(SECRETS['private-key']), 500),
                (handler.do_POST, '/apply', manager.return_value.apply, OSError(SECRETS['staged']), 500),
                (handler.do_POST, '/apply', manager.return_value.apply, KeyError(SECRETS['operator']), 400),
            ):
                with self.subTest(path=path, exception=type(error)):
                    handler.path = path
                    action.side_effect = error
                    method()
                    self.assertEqual(response.call_args.args[1], status)
                    assert_public_clean(self, response.call_args.args[2])

    def test_cancelled_candidate_scrubs_staged_value_without_public_disclosure(self):
        from controller import Manager
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manager = Manager(root, root / 'state')
            directory = manager.state / 'jobs' / ('a' * 32)
            directory.mkdir()
            rt.write_json(directory / 'job.json', {'id': directory.name, 'status': 'ready',
                                                  'secretChanges': ['portainerPassword']})
            rt.write_json(directory / 'secrets.json', {'portainerPassword': SECRETS['staged']})
            with patch.object(rt, 'STATE', manager.state):
                result = manager.cancel(directory.name)
            self.assertFalse((directory / 'secrets.json').exists())
            assert_public_clean(self, {'response': result, 'job': rt.read_json(directory / 'job.json'),
                                      'audit': (manager.state / 'audit.jsonl').read_text()})
