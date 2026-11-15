"""Exercise safe boundaries; this is not an all-clear for known delivery defects."""
import base64
import ast
import contextlib
import io
import importlib.util
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
import recovery
import runtime as rt
import worker
from test_direct_install import initial

spec = importlib.util.spec_from_file_location('credential_installer', BASE / 'installer/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class CredentialContractTests(unittest.TestCase):
    def test_public_errors_are_constructed_only_from_fixed_literals(self):
        for directory in ('admin', 'installer', 'scripts'):
            for path in (BASE / directory).rglob('*.py'):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'PublicError':
                        self.assertEqual(len(node.args), 1, str(path.relative_to(BASE)))
                        self.assertIsInstance(node.args[0], ast.Constant, str(path.relative_to(BASE)))
                        self.assertIsInstance(node.args[0].value, str, str(path.relative_to(BASE)))

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

    def test_first_run_and_legacy_install_summary_never_print_credentials(self):
        settings = initial()
        settings['ready'] = settings['apps']['enable'] = settings['cockpit']['enable'] = True
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), \
                patch.object(installer, 'run', return_value=Mock(stdout='SHA256 Fingerprint=public')), \
                patch('diagnostics.management_address', return_value='192.0.2.20'), \
                patch.object(portainer, 'credentials', return_value={
                    'username': SECRETS['operator'], 'password': SECRETS['portainer']}):
            for enrollment in (True, False):
                if enrollment:
                    rt.atomic(rt.STATE / 'web-enrollment', '{}')
                else:
                    (rt.STATE / 'web-enrollment').unlink()
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    installer.summarize(settings, SECRETS['setup'])
                assert_public_clean(self, output.getvalue())
                self.assertIn('https://192.0.2.20:', output.getvalue())

    def test_installer_command_failure_does_not_replay_child_diagnostics(self):
        output = io.StringIO()
        failure = subprocess.CalledProcessError(1, ['tool', SECRETS['staged']],
                                               output=SECRETS['operator'], stderr=SECRETS['setup'])
        with patch.object(installer.subprocess, 'run', side_effect=failure), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(ValueError) as error:
                installer.run(['tool'], capture_output=True)
        assert_public_clean(self, [output.getvalue(), str(error.exception)])
        self.assertTrue(error.exception.__suppress_context__)

    def test_installer_failure_report_and_stderr_are_clean(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                try:
                    raise ValueError(SECRETS['setup'])
                except ValueError:
                    installer.report_failure()
            assert_public_clean(self, [output.getvalue(), rt.read_json(rt.STATE / 'installation-failure.json')])

    def test_portainer_cli_prints_status_and_private_delivery_path_only(self):
        result = {'url': 'https://192.0.2.20:9443', 'backup': '/private/backup',
                  'username': SECRETS['operator'], 'password': SECRETS['portainer']}
        output = io.StringIO()
        with patch.object(rt, 'locked'), patch.object(portainer, 'reset', return_value=result), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            main.reset_portainer(initial())
        assert_public_clean(self, output.getvalue())
        self.assertIn(str(rt.SECRET_PATHS['portainerPassword']), output.getvalue())

    def test_portainer_success_has_status_only_and_protected_private_delivery(self):
        settings = initial(); settings['apps']['enable'] = True
        helper = ('Password successfully updated for user: ' + SECRETS['operator'] +
                  '\nUse the following password to login: ' + SECRETS['portainer'] + '\n')
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), \
                patch.dict(rt.SECRET_PATHS, portainerPassword=Path(folder) / 'password'), \
                patch.object(portainer, 'guest', side_effect=lambda *a, **kw: helper if a[:2] == ('docker', 'run') else ''), \
                patch.object(portainer, 'incus'), patch('diagnostics.wait_ready'), \
                patch('diagnostics.management_address', return_value='192.0.2.20'), \
                patch.object(portainer, 'credentials', return_value={'username': SECRETS['operator'], 'password': SECRETS['portainer']}):
            result = portainer.reset(settings)
            assert_public_clean(self, result)
            self.assertEqual(set(result), {'url', 'backup'})
            private = rt.SECRET_PATHS['portainerPassword']
            self.assertEqual(private.read_text().strip(), SECRETS['portainer'])
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)

    def test_portainer_failure_and_restart_error_do_not_copy_exception_text(self):
        settings = initial(); settings['apps']['enable'] = True
        for restart_fails in (False, True):
            def guest(*args, **kwargs):
                if args[0] == 'tar' or (restart_fails and args[:2] == ('systemctl', 'start')):
                    raise ValueError(SECRETS['portainer'])
                return ''
            with self.subTest(restart_fails=restart_fails), patch.object(portainer, 'guest', side_effect=guest):
                with self.assertRaises(ValueError) as error:
                    portainer.reset(settings)
                assert_public_clean(self, str(error.exception))
                self.assertTrue(error.exception.__suppress_context__)

    def test_worker_failure_records_and_journal_exception_are_clean(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), patch.object(rt, 'audit'):
            directory = rt.STATE / 'jobs' / ('a' * 32)
            rt.write_json(directory / 'job.json', {'status': 'building'})
            with patch.object(worker, 'build', side_effect=ValueError(SECRETS['staged'])):
                with self.assertRaises(RuntimeError) as error:
                    worker.job('build', directory.name)
            assert_public_clean(self, [rt.read_json(directory / 'job.json'),
                                      (directory / 'build.log').read_text(), str(error.exception)])
            self.assertTrue(error.exception.__suppress_context__)

    def test_worker_command_keeps_child_output_and_arguments_out_of_log(self):
        output = io.StringIO()
        process = Mock(); process.wait.return_value = 1
        with patch.object(worker.subprocess, 'Popen', return_value=process) as start:
            with self.assertRaises(ValueError) as error:
                worker.run(['tool', SECRETS['staged']], output)
        self.assertEqual(start.call_args.kwargs['stdout'], subprocess.DEVNULL)
        self.assertEqual(start.call_args.kwargs['stderr'], subprocess.DEVNULL)
        assert_public_clean(self, [output.getvalue(), str(error.exception)])

    def test_legacy_transaction_log_and_errors_are_not_replayed(self):
        from controller import Manager
        with tempfile.TemporaryDirectory() as folder:
            manager = Manager(Path(folder), Path(folder) / 'state')
            directory = manager.state / 'jobs' / ('a' * 32)
            rt.write_json(directory / 'job.json', {'id': directory.name, 'status': 'failed',
                                                  'error': SECRETS['staged'], 'rollbackError': SECRETS['operator']})
            rt.atomic(directory / 'build.log', SECRETS['portainer'] + '\nHost command started\n')
            result = manager.job(directory.name)
            self.assertEqual(result['log'], 'Host command started\n')
            assert_public_clean(self, result)

    def test_frontend_errors_hide_hostile_domain_messages_without_authentication(self):
        with patch.object(main, 'BoundedHttpsServer') as server, patch.object(main.ssl, 'SSLContext'), \
                patch.object(main, 'reply') as response:
            main.web()
            handler = object.__new__(server.call_args.args[1])
            handler.access = Mock(return_value='localhost:9440')
            handler.headers = {'Origin': 'https://localhost:9440'}
            handler.path = '/api/enrollment'
            for method in (handler.do_GET, handler.do_POST):
                for exception in (ValueError, PermissionError, OSError):
                    with self.subTest(method=method.__name__, exception=exception), \
                            patch.object(main, 'json_body', return_value={}), \
                            patch.object(main, 'broker_request', side_effect=exception(SECRETS['setup'])):
                        method()
                        self.assertGreaterEqual(response.call_args.args[1], 400)
                        assert_public_clean(self, response.call_args.args[2])

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
                (handler.do_GET, '/status', manager.return_value.status, ValueError(SECRETS['setup']), 400),
                (handler.do_POST, '/apply', manager.return_value.apply, ValueError(SECRETS['portainer']), 400),
            ):
                with self.subTest(path=path, exception=type(error)):
                    handler.path = path
                    action.side_effect = error
                    method()
                    self.assertEqual(response.call_args.args[1], status)
                    assert_public_clean(self, response.call_args.args[2])

    def test_configuration_download_does_not_package_runtime_or_job_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            root, state = Path(folder) / 'source', Path(folder) / 'state'
            rt.write_json(root / '.nixlab-files.json', ['flake.nix'])
            rt.atomic(root / 'flake.nix', '{}')
            rt.atomic(state / 'ca.key', SECRETS['private-key'])
            rt.atomic(state / 'setup-password', SECRETS['setup'])
            rt.write_json(state / 'jobs' / ('a' * 32) / 'secrets.json', {'portainerPassword': SECRETS['staged']})
            with patch.object(rt, 'ROOT', root), patch.object(rt, 'STATE', state):
                response = recovery.download()
            self.assertEqual(set(response['files']), {'nixlab-config.tar.gz'})
            archive = Path(folder) / 'download.tar.gz'
            archive.write_bytes(base64.b64decode(response['files']['nixlab-config.tar.gz']))
            manifest, entries = recovery.inspect(archive)
            self.assertEqual(set(entries), {'config/flake.nix'})
            assert_public_clean(self, [response, manifest, entries])

    def test_failed_recovery_encryption_suppresses_child_output_and_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root, target = Path(folder), Path(folder) / 'kit'
            rt.write_json(root / '.nixlab-files.json', ['flake.nix'])
            rt.atomic(root / 'flake.nix', '{}')
            secret = root / 'runtime-secret'
            rt.atomic(secret, SECRETS['staged'])
            encryptor = Mock(stdin=io.BytesIO(), stderr=io.BytesIO(SECRETS['staged'].encode()))
            encryptor.wait.return_value = encryptor.poll.return_value = 1
            def start(args, **kwargs):
                self.assertEqual(kwargs['stderr'], subprocess.DEVNULL)
                Path(args[-1]).write_bytes(b'partial ciphertext')
                return encryptor

            with patch.object(rt, 'ROOT', root), patch.object(recovery, 'secret_files', return_value={'runtime/key': secret}), \
                    patch.object(recovery.upgrades, 'metadata', return_value={}), patch.object(recovery.subprocess, 'Popen', side_effect=start):
                with self.assertRaisesRegex(ValueError, 'Secret encryption failed') as error:
                    recovery.export(target, 'age1' + 'a' * 58, True)
            assert_public_clean(self, str(error.exception))
            self.assertFalse((target / 'nixlab-secrets.tar.gz.age').exists())

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
