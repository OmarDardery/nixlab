"""Real process races and SIGKILL boundaries for the root enrollment authority."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / name) for name in ('admin', 'scripts')]
import accounts
import enrollment
from main import Auth
import runtime as rt
from credential_fixtures import SECRETS, assert_public_clean

CODE = 'a' * 32
CHILD = '''
import json, os, signal, sys
from pathlib import Path
import accounts, enrollment, runtime as rt
request=json.load(sys.stdin)
stage=sys.argv[1]
def kill(): os.kill(os.getpid(), signal.SIGKILL)
replace=rt.os.replace
fsync=rt.os.fsync
fdopen=rt.os.fdopen
class PartialWrite:
 def __init__(self, stream): self.stream=stream
 def __enter__(self): return self
 def __exit__(self,*args): return self.stream.__exit__(*args)
 def write(self,data):
  self.stream.write(data[:len(data)//2]); self.stream.flush(); kill()
def opening(fd,*args,**kwargs):
 stream=fdopen(fd,*args,**kwargs)
 if stage=='partial-write' and Path(os.readlink('/proc/self/fd/'+str(fd))).name.startswith(('.auth.json','.users.json')):
  return PartialWrite(stream)
 return stream
def syncing(fd):
 name=Path(os.readlink('/proc/self/fd/'+str(fd))).name
 if stage=='file-fsync' and name.startswith(('.auth.json','.users.json')): kill()
 return fsync(fd)
def replacing(source,target):
 target=Path(target)
 if target.name=='bootstrap-state.json':
  phase=json.loads(Path(source).read_text())['phase']
  if stage=='intent-rename' and phase=='committing': kill()
  if stage=='receipt-rename' and phase=='account-committed': kill()
 if stage=='auth-rename' and target.name in ('auth.json','users.json'): kill()
 result=replace(source,target)
 if stage=='after-auth-rename' and target.name in ('auth.json','users.json'): kill()
 if stage=='after-receipt-rename' and target.name=='bootstrap-state.json' and phase=='account-committed': kill()
 return result
rt.os.fdopen=opening; rt.os.fsync=syncing; rt.os.replace=replacing
try:
 result=enrollment.claim(request['code'],request['password'],request.get('username','admin'))
 print(json.dumps(result))
except (ValueError,OSError):
 print(json.dumps({'rejected':True}))
'''


class AtomicEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.patch = patch.object(rt, 'STATE', self.state)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.env = os.environ | {'NIXLAB_STATE': str(self.state), 'NIXLAB_TEST': '1',
            'PYTHONPATH': os.pathsep.join(str(BASE / name) for name in ('admin', 'scripts'))}
        self.environment = patch.dict(os.environ, {'NIXLAB_TEST': '1'})
        self.environment.start(); self.addCleanup(self.environment.stop)

    def start_child(self, stage='claim'):
        return subprocess.Popen([sys.executable, '-c', CHILD, stage], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)

    def finish(self, child, password=SECRETS['setup'], username='admin'):
        out, err = child.communicate(json.dumps({'code': CODE, 'password': password, 'username': username}).encode(), timeout=20)
        assert_public_clean(self, [out, err])
        return child.returncode, json.loads(out) if out else None

    def prepare(self):
        accounts.enable_enrollment()
        accounts.approve_enrollment(CODE)

    def test_independent_processes_commit_exactly_one_verifier(self):
        self.prepare()
        first, second = self.start_child(), self.start_child()
        # Start both before feeding their private pipes; no secret is in argv/env.
        first.stdin.write(json.dumps({'code': CODE, 'password': SECRETS['setup']}).encode()); first.stdin.close(); first.stdin = None
        second.stdin.write(json.dumps({'code': CODE, 'password': SECRETS['operator']}).encode()); second.stdin.close(); second.stdin = None
        results = []
        for child in (first, second):
            out, err = child.communicate(timeout=20)
            assert_public_clean(self, [out, err])
            self.assertEqual(child.returncode, 0)
            results.append(json.loads(out))
        self.assertEqual(sum(item == {'ok': True} for item in results), 1)
        self.assertEqual(sum(accounts.matches('admin', pw) for pw in (SECRETS['setup'], SECRETS['operator'])), 1)
        self.assertEqual(accounts.enrollment_status(), {'available': False, 'recoveryRequired': False})
        self.assertEqual((self.state / enrollment.STATE_FILE).stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.state / 'auth.json').stat().st_mode & 0o777, 0o640)

    @unittest.skipUnless(Path('/proc/self/fd').exists(), 'Linux process/write boundaries required')
    def test_sigkill_leaves_complete_verifier_or_closed_recoverable_intent(self):
        for stage in ('intent-rename', 'partial-write', 'file-fsync', 'auth-rename',
                      'after-auth-rename', 'receipt-rename', 'after-receipt-rename'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)):
                previous_env = self.env
                self.env = self.env | {'NIXLAB_STATE': folder}
                try:
                    self.prepare()
                    code, result = self.finish(self.start_child(stage))
                    self.assertEqual(code, -signal.SIGKILL)
                    self.assertIsNone(result)
                    self.assertIsInstance(rt.read_json(rt.STATE / enrollment.STATE_FILE), dict)
                    if (rt.STATE / 'auth.json').exists():
                        self.assertTrue(enrollment.auth_valid(), 'Killed publication left an invalid verifier')
                        self.assertTrue(accounts.matches('admin', SECRETS['setup']), 'Published verifier differs from the supplied credential')
                        self.assertFalse(accounts.enrollment_open())
                        self.assertEqual(accounts.claim_enrollment(CODE, SECRETS['operator']), {'ok': True, 'alreadyComplete': True})
                        self.assertTrue(accounts.matches('admin', SECRETS['setup']), 'Idempotent retry changed the verifier')
                    else:
                        if stage != 'intent-rename':
                            self.assertEqual(accounts.enrollment_status(), {'available': False, 'recoveryRequired': True})
                            with self.assertRaises(ValueError): accounts.claim_enrollment(CODE, SECRETS['setup'])
                        # A fresh ROOT approval is required to resume an interrupted intent.
                        accounts.approve_enrollment(CODE)
                        self.assertEqual(self.finish(self.start_child())[1], {'ok': True})
                    self.assertFalse((rt.STATE / 'web-enrollment').exists())
                    assert_public_clean(self, [(rt.STATE / enrollment.STATE_FILE).read_bytes(), (rt.STATE / 'auth.json').read_bytes()])
                finally:
                    self.env = previous_env

    def test_approval_survives_process_restart_but_not_expiry_or_boot_change(self):
        self.prepare()
        for mutation in ({'expires': time.monotonic() - 1}, {'boot': 'previous-boot'}):
            value = enrollment.load(); value['proof'].update(mutation)
            rt.write_json(self.state / enrollment.STATE_FILE, value)
            self.assertEqual(self.finish(self.start_child())[1], {'rejected': True})
            self.assertFalse((self.state / 'auth.json').exists())
            accounts.approve_enrollment(CODE)
        self.assertEqual(self.finish(self.start_child())[1], {'ok': True})

    def test_legacy_migration_preserves_jobs_and_invalidates_old_sessions_after_commit(self):
        rt.write_json(self.state / 'auth.json', rt.password_record(SECRETS['setup']))
        rt.atomic(self.state / 'setup-password', SECRETS['setup'])
        rt.write_json(self.state / 'jobs' / ('f' * 32) / 'job.json', {'status': 'confirmed'})
        before = (self.state / 'jobs' / ('f' * 32) / 'job.json').read_bytes()
        auth = Auth(); token, _ = auth.login(SECRETS['setup'], '127.0.0.1')
        with self.assertRaises(ValueError): accounts.enable_enrollment()
        accounts.change_password('admin', SECRETS['operator'])
        self.assertEqual(enrollment.load()['phase'], 'migrated')
        self.assertFalse((self.state / 'setup-password').exists())
        self.assertIsNone(auth.get('nixlab_session=' + token))
        self.assertTrue(accounts.matches('admin', SECRETS['operator']))
        self.assertEqual((self.state / 'jobs' / ('f' * 32) / 'job.json').read_bytes(), before)
        self.assertFalse(accounts.enrollment_open())

    def test_missing_corrupt_and_future_state_never_reopen_claims(self):
        self.prepare(); accounts.claim_enrollment(CODE, SECRETS['setup'])
        original = (self.state / 'auth.json').read_bytes()
        for malformed in (None, '{', 'null', '{}', '[]', '{"version":2}'):
            path = self.state / 'auth.json'
            if malformed is None: path.unlink(missing_ok=True)
            else: rt.atomic(path, malformed)
            rt.atomic(self.state / 'web-enrollment', '1\n')
            self.assertEqual(accounts.enrollment_status(), {'available': False, 'recoveryRequired': True})
            self.assertFalse(accounts.matches('admin', SECRETS['setup']))
            with self.assertRaises(ValueError): accounts.approve_enrollment(CODE)
            with self.assertRaises(ValueError): accounts.claim_enrollment(CODE, SECRETS['operator'])
        rt.atomic(self.state / 'auth.json', original)
        for malformed in ('{', 'null', '[]', '{"version":2,"phase":"unclaimed"}', '{"version":1,"phase":"unknown"}'):
            rt.atomic(self.state / enrollment.STATE_FILE, malformed)
            self.assertEqual(accounts.enrollment_status(), {'available': False, 'recoveryRequired': True})
            self.assertTrue(accounts.matches('admin', SECRETS['setup']), 'Unsupported enrollment state must preserve existing login')
            with self.assertRaises(ValueError): accounts.enable_enrollment()
            with self.assertRaises(ValueError): accounts.claim_enrollment(CODE, SECRETS['operator'])
            with self.assertRaises(ValueError): accounts.change_password('admin', SECRETS['operator'])
            self.assertTrue((self.state / 'auth.json').read_bytes() == original, 'Unsupported state changed the legacy verifier')

    def test_legacy_marker_needs_new_root_approval_and_local_recovery_cannot_reopen_claim(self):
        rt.atomic(self.state / 'web-enrollment', '1\n')
        rt.write_json(self.state / 'web-enrollment-approval.json', {'digest': enrollment.proof_digest(CODE), 'boot': enrollment.boot_id(), 'expires': time.monotonic() + 600})
        self.assertFalse(accounts.enrollment_open())
        with self.assertRaises(ValueError): accounts.claim_enrollment(CODE, SECRETS['setup'])
        accounts.approve_enrollment(CODE)
        accounts.claim_enrollment(CODE, SECRETS['setup'])
        (self.state / 'auth.json').unlink()
        with patch.object(accounts, 'local_root', return_value=False), self.assertRaises(PermissionError):
            accounts.recover_password(SECRETS['operator'])
        with patch.object(accounts, 'local_root', return_value=True):
            accounts.recover_password(SECRETS['operator'])
        self.assertFalse(accounts.enrollment_open())
        self.assertTrue(accounts.matches('admin', SECRETS['operator']))
        self.assertEqual(enrollment.load()['phase'], 'migrated')

    def test_configuration_recovery_preserves_accounts_and_claim_authority(self):
        import worker
        self.prepare(); accounts.claim_enrollment(CODE, SECRETS['setup'])
        root = self.state / 'config'; root.mkdir()
        rt.write_json(root / 'machine/format.json', {'version': 1})
        rt.write_json(root / 'machine/settings.json', {'hostName': 'before'})
        directory = self.state / 'jobs' / ('e' * 32); directory.mkdir(parents=True)
        rt.write_json(directory / 'job.json', {'settings': {}})
        with patch.object(rt, 'ROOT', root), patch.object(rt, 'SECRET_PATHS', {}), \
                patch.object(worker.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='{}')):
            worker.save_recovery(directory)
            rt.write_json(root / 'machine/settings.json', {'hostName': 'after'})
            accounts.change_password('admin', SECRETS['operator'])
            preserved = {name: (self.state / name).read_bytes() for name in ('auth.json', enrollment.STATE_FILE)}
            worker.restore_files(directory)
        self.assertEqual(rt.read_json(root / 'machine/settings.json'), {'hostName': 'before'})
        self.assertTrue(all((self.state / name).read_bytes() == data for name, data in preserved.items()),
                        'Source rollback replaced portal identities or claim authority')
        self.assertTrue(accounts.matches('admin', SECRETS['operator']))
        self.assertFalse(accounts.enrollment_open())

    def test_missing_legacy_auth_on_a_host_with_jobs_requires_recovery(self):
        rt.write_json(self.state / 'jobs' / ('f' * 32) / 'job.json', {'status': 'confirmed'})
        with self.assertRaises(ValueError): enrollment.ensure_legacy_account()
        self.assertEqual(accounts.enrollment_status(), {'available': False, 'recoveryRequired': True})
        self.assertFalse((self.state / 'auth.json').exists())
        self.assertFalse(accounts.enrollment_open())

    def test_new_root_approval_replaces_old_browser_authority(self):
        self.prepare()
        accounts.approve_enrollment('b' * 32)
        with self.assertRaises(ValueError): accounts.claim_enrollment(CODE, SECRETS['setup'])
        accounts.claim_enrollment('b' * 32, SECRETS['operator'])
        self.assertTrue(accounts.matches('admin', SECRETS['operator']))
        self.assertFalse(accounts.enrollment_open())

    def test_broken_authority_errors_never_echo_private_file_contents(self):
        rt.atomic(self.state / enrollment.STATE_FILE, '{' + SECRETS['setup'])
        artifacts = [accounts.enrollment_status()]
        for operation in (accounts.enable_enrollment, lambda: accounts.approve_enrollment(CODE),
                          lambda: accounts.claim_enrollment(CODE, SECRETS['operator'])):
            with self.assertRaises(ValueError) as stopped: operation()
            artifacts.append(str(stopped.exception))
        assert_public_clean(self, artifacts)

    def test_named_first_admin_users_publication_survives_sigkill(self):
        for stage in ('partial-write', 'file-fsync', 'after-auth-rename', 'receipt-rename'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)):
                previous_env = self.env; self.env = self.env | {'NIXLAB_STATE': folder}
                try:
                    self.prepare()
                    code, _ = self.finish(self.start_child(stage), username='namedowner')
                    self.assertEqual(code, -signal.SIGKILL)
                    if enrollment.account_valid():
                        self.assertFalse(accounts.enrollment_open())
                        self.assertTrue(accounts.matches('namedowner', SECRETS['setup']))
                    else:
                        self.assertEqual(accounts.enrollment_status(), {'available': False, 'recoveryRequired': True})
                        accounts.approve_enrollment(CODE)
                        self.assertEqual(self.finish(self.start_child(), username='namedowner')[1], {'ok': True})
                    self.assertFalse((rt.STATE / 'auth.json').exists())
                    self.assertEqual(accounts.list_users(), [{'username': 'namedowner', 'role': 'admin'}])
                finally:
                    self.env = previous_env
