"""Crash cleanup must not dispose of pending recovery or live credentials."""
from pathlib import Path
import json
import errno
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import runtime as rt
from credential_fixtures import SECRETS, assert_public_clean


class CleanupTests(unittest.TestCase):
    def test_doctor_reports_a_failed_cleanup_guard_as_unhealthy(self):
        import diagnostics
        def unit(args, **kwargs):
            status = ('Id=nixlab-rollback-guard.service\nLoadState=loaded\nActiveState=failed\n'
                      if 'nixlab-rollback-guard.service' in args else 'LoadState=not-found\n')
            return subprocess.CompletedProcess(args, 0, stdout=status)
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), \
                patch.object(diagnostics, 'probe', return_value={'ok': True}), \
                patch.object(diagnostics, 'clock_status', return_value={}), \
                patch.object(diagnostics.subprocess, 'run', side_effect=unit):
            report = diagnostics.doctor({})
        self.assertFalse(report['ok'])
        self.assertEqual(report['services'][0]['Id'], 'nixlab-rollback-guard.service')
        assert_public_clean(self, report)

    def test_directory_sync_is_retried_after_unlink_succeeded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); directory = root / 'jobs' / ('a' * 32)
            rt.write_json(directory / 'job.json', {'status': 'confirmed'})
            rt.stage_secrets(directory, {'arcanePassword': SECRETS['staged']})
            with patch.object(rt.os, 'fsync', side_effect=OSError(SECRETS['staged'])):
                with self.assertRaisesRegex(ValueError, 'cleanup failed') as caught:
                    rt.remove_staged_secrets(directory)
            self.assertFalse((directory / 'secrets.json').exists())
            assert_public_clean(self, str(caught.exception))
            with patch.object(rt.os, 'fsync', wraps=rt.os.fsync) as sync:
                rt.cleanup_terminal_secrets(root)
            sync.assert_called_once()

    def test_expired_credentials_are_rejected_by_both_activation_entrypoints(self):
        from controller import Manager
        import worker
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); manager = Manager(root / 'source', root / 'state')
            directory = manager.state / 'jobs' / ('a' * 32)
            rt.write_json(directory / 'job.json', {'status': 'ready', 'created': time.time() - rt.STAGED_SECRET_LIFETIME,
                                                  'secretChanges': ['arcanePassword']})
            rt.stage_secrets(directory, {'arcanePassword': SECRETS['staged']})
            rt.write_json(manager.state / 'active.json', {'id': directory.name})
            with patch.object(rt, 'STATE', manager.state), patch.object(manager, 'spawn') as spawn, \
                    patch.object(worker, 'save_recovery') as recovery, patch.object(rt, 'atomic') as publish:
                with self.assertRaisesRegex(ValueError, 'expired'): manager.apply(directory.name)
                with self.assertRaisesRegex(ValueError, 'expired'): worker.apply(directory)
            spawn.assert_not_called(); recovery.assert_not_called(); publish.assert_not_called()
            self.assertFalse((manager.state / 'pending.json').exists())

    def test_cancel_cannot_discard_failed_pending_recovery(self):
        from controller import Manager
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); manager = Manager(root / 'source', root / 'state')
            directory = manager.state / 'jobs' / ('a' * 32)
            rt.write_json(directory / 'job.json', {'status': 'failed'})
            rt.write_json(manager.state / 'pending.json', {'id': directory.name})
            rt.stage_secrets(directory, {'arcanePassword': SECRETS['staged']})
            with patch.object(rt, 'STATE', manager.state), self.assertRaisesRegex(ValueError, 'pending activation'):
                manager.cancel(directory.name)
            self.assertTrue((directory / 'secrets.json').exists())
            self.assertEqual(rt.read_job_record(directory / 'job.json')['status'], 'failed')

    def test_normal_confirmation_and_rollback_scrub_staging_but_keep_evidence(self):
        from controller import Manager
        import worker
        for action in ('confirm', 'rollback'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); manager = Manager(root / 'source', root / 'state')
                directory = manager.state / 'jobs' / ('a' * 32)
                previous = root / 'previous'
                rt.atomic(previous / 'bin' / 'switch-to-configuration', '')
                rt.write_json(directory / 'job.json', {'status': 'awaiting-confirmation', 'previousSystem': str(previous)})
                rt.atomic(directory / 'recovery' / 'secret-arcanePassword', SECRETS['arcane'])
                rt.write_json(manager.state / 'pending.json', {'id': directory.name, 'deadline': time.time() + 60})
                rt.stage_secrets(directory, {'arcanePassword': SECRETS['staged']})
                (directory / 'system').symlink_to(Path('/run/current-system').resolve())
                with patch.object(rt, 'STATE', manager.state), patch.object(worker, 'restore_files', return_value={}), \
                        patch.object(worker, 'restore_operator_password'), patch.object(worker, 'run'), \
                        patch.object(worker, 'restart_loaded'), patch.object(worker.shutil, 'which', return_value=None):
                    if action == 'confirm': manager.confirm(directory.name)
                    else: worker.rollback(directory)
                self.assertFalse((directory / 'secrets.json').exists())
                self.assertFalse((manager.state / 'pending.json').exists())
                self.assertEqual((directory / 'recovery' / 'secret-arcanePassword').read_text(), SECRETS['arcane'])
                assert_public_clean(self, [(directory / 'job.json').read_text(),
                                          (manager.state / 'audit.jsonl').read_text()])

    def test_failed_candidate_commit_is_retried_before_any_unlink(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); directory = root / 'jobs' / ('a' * 32)
            rt.write_json(directory / 'job.json', {'status': 'ready', 'created': time.time() - rt.STAGED_SECRET_LIFETIME})
            rt.stage_secrets(directory, {'arcanePassword': SECRETS['staged']})
            with patch.object(rt, 'write_json', side_effect=OSError(SECRETS['staged'])):
                with self.assertRaises(ValueError) as caught: rt.cleanup_terminal_secrets(root)
            assert_public_clean(self, str(caught.exception))
            self.assertTrue((directory / 'secrets.json').exists())
            self.assertEqual(rt.read_job_record(directory / 'job.json')['status'], 'ready')
            rt.cleanup_terminal_secrets(root)
            self.assertFalse((directory / 'secrets.json').exists())

    def test_full_or_readonly_storage_does_not_publish_partial_secrets(self):
        for error in (errno.ENOSPC, errno.EROFS):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as folder:
                directory = Path(folder)
                with patch.object(rt.os, 'link', side_effect=OSError(error, SECRETS['staged'])):
                    with self.assertRaisesRegex(ValueError, 'publication failed') as caught:
                        rt.stage_secrets(directory, {'arcanePassword': SECRETS['staged']})
                assert_public_clean(self, str(caught.exception))
                self.assertFalse((directory / 'secrets.json').exists())
                self.assertFalse(list(directory.glob('.secrets.json*')))

    def test_insecure_files_and_unknown_age_or_recovery_require_a_health_finding(self):
        for defect in ('mode', 'unknown-age', 'unresolved-recovery', 'pending-record'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); directory = root / 'jobs' / ('a' * 32)
                job = {'status': 'ready', 'created': time.time()}
                if defect == 'unknown-age': job.pop('created')
                if defect == 'unresolved-recovery':
                    job.update(status='rolling-back', created=time.time() - rt.STAGED_SECRET_LIFETIME)
                rt.write_json(directory / 'job.json', job)
                rt.stage_secrets(directory, {'arcanePassword': SECRETS['staged']})
                if defect == 'mode': (directory / 'secrets.json').chmod(0o644)
                if defect == 'pending-record': rt.write_json(root / 'pending.json', {'id': SECRETS['staged']})
                with self.assertRaises(ValueError) as caught: rt.cleanup_terminal_secrets(root)
                assert_public_clean(self, str(caught.exception))
                self.assertTrue((directory / 'secrets.json').exists())

    def test_guard_reports_cleanup_failure_without_blocking_guest_recovery(self):
        import worker
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, 'STATE', Path(folder)), \
                patch.object(rt, 'cleanup_terminal_secrets', side_effect=ValueError('Staged credential cleanup failed')), \
                patch('operations.Operations') as operations:
            with self.assertRaisesRegex(ValueError, 'cleanup failed'): worker.guard()
            operations.return_value.recover_interrupted.assert_called_once()

    def test_only_proven_terminal_candidate_secrets_are_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            terminal = {'confirmed', 'rolled-back', 'cancelled', 'superseded', 'expired'}
            states = sorted(terminal) + ['reviewed', 'failed', 'building', 'ready', 'applying', 'awaiting-confirmation', 'rolling-back']
            for i, status in enumerate(states):
                job = root / 'jobs' / f'{i:032x}'
                rt.write_json(job / 'job.json', {'status': status, 'created': time.time()})
                rt.atomic(job / 'secrets.json', 'sensitive')
                rt.atomic(job / 'recovery/manifest.json', 'retained')
            rt.cleanup_terminal_secrets(root)
            for i, status in enumerate(states):
                job = root / 'jobs' / f'{i:032x}'
                self.assertEqual((job / 'secrets.json').exists(), status not in terminal, status)
                self.assertEqual((job / 'recovery/manifest.json').read_text(), 'retained')
            rt.cleanup_terminal_secrets(root)  # Idempotent after a partial/complete sweep.

    def test_expired_review_is_cancelled_before_its_secret_is_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / 'jobs' / ('a' * 32)
            job = directory / 'job.json'
            path = directory / 'secrets.json'
            rt.write_json(job, {'status': 'ready', 'created': time.time() - rt.STAGED_SECRET_LIFETIME - 1})
            rt.write_json(path, {'cockpitPassword': SECRETS['staged']})
            rt.cleanup_terminal_secrets(root)
            self.assertFalse(path.exists())
            self.assertEqual(rt.read_job_record(job)['status'], 'cancelled')
            assert_public_clean(self, job.read_bytes())

    def test_expired_interrupted_build_cleaned_only_after_worker_is_proven_stopped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / 'jobs' / ('b' * 32)
            record = directory / 'job.json'
            stage = directory / 'secrets.json'
            rt.write_json(record, {'status': 'building', 'created': time.time() - rt.STAGED_SECRET_LIFETIME - 1})
            rt.write_json(stage, {'arcanePassword': SECRETS['staged']})
            with patch.object(rt, 'worker_active', return_value=True):
                rt.cleanup_terminal_secrets(root)
            self.assertTrue(stage.exists())
            with patch.object(rt, 'worker_active', side_effect=ValueError('Unknown unit status')):
                with self.assertRaisesRegex(ValueError, 'Staged credential cleanup failed'):
                    rt.cleanup_terminal_secrets(root)
            self.assertTrue(stage.exists())
            with patch.object(rt, 'worker_active', return_value=False) as worker:
                rt.cleanup_terminal_secrets(root)
            worker.assert_called_once_with('nixlab-build-' + directory.name)
            self.assertFalse(stage.exists())
            self.assertEqual(rt.read_job_record(record)['status'], 'cancelled')
            assert_public_clean(self, record.read_bytes())

    def test_failed_cleanup_reports_fixed_error_and_guard_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / 'jobs' / ('c' * 32)
            rt.write_json(directory / 'job.json', {'status': 'confirmed'})
            staged = directory / 'secrets.json'
            rt.write_json(staged, {'arcanePassword': SECRETS['staged']})
            with patch.object(rt, 'unlink_staged', side_effect=OSError(SECRETS['staged'])):
                with self.assertRaisesRegex(ValueError, 'Staged credential cleanup failed') as failure:
                    rt.cleanup_terminal_secrets(root)
            assert_public_clean(self, str(failure.exception))
            self.assertTrue(staged.exists())
            rt.cleanup_terminal_secrets(root)
            self.assertFalse(staged.exists())

    def test_staged_secret_reader_rejects_missing_aliased_and_nonprivate_files(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / 'secrets.json'
            expected = ['cockpitPassword']
            with self.assertRaisesRegex(ValueError, 'Staged credentials are missing'):
                rt.read_staged_secrets(directory, expected)
            rt.write_json(path, {'cockpitPassword': SECRETS['staged']})
            self.assertEqual(rt.read_staged_secrets(directory, expected)['cockpitPassword'], SECRETS['staged'])
            path.chmod(0o640)
            with self.assertRaisesRegex(ValueError, 'Staged credentials are invalid') as caught:
                rt.read_staged_secrets(directory, expected)
            assert_public_clean(self, str(caught.exception))
            other = path.with_name('private.json')
            path.rename(other)
            path.symlink_to(other)
            with self.assertRaisesRegex(ValueError, 'Staged credentials are invalid'):
                rt.read_staged_secrets(directory, expected)

    def test_pending_pointer_and_unknown_records_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for i in range(3):
                job = root / 'jobs' / f'{i:032x}'
                rt.write_json(job / 'job.json', {'status': 'confirmed'})
                rt.atomic(job / 'secrets.json', 'sensitive')
            rt.write_json(root / 'pending.json', {'id': f'{0:032x}'})
            (root / 'jobs' / f'{1:032x}' / 'job.json').write_text('corrupt')
            outside = root / 'outside'
            rt.write_json(outside / 'job.json', {'status': 'confirmed'})
            rt.atomic(outside / 'secrets.json', 'retained')
            (root / 'jobs' / f'{3:032x}').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'Staged credential cleanup failed'):
                rt.cleanup_terminal_secrets(root)
            self.assertTrue((root / 'jobs' / f'{0:032x}' / 'secrets.json').exists())
            self.assertTrue((root / 'jobs' / f'{1:032x}' / 'secrets.json').exists())
            self.assertFalse((root / 'jobs' / f'{2:032x}' / 'secrets.json').exists())
            self.assertEqual((outside / 'secrets.json').read_text(), 'retained')

    def test_deadline_is_fixed_and_invalid_age_fails_closed(self):
        created = 1000
        self.assertFalse(rt.staged_secrets_expired({'created': created}, created + rt.STAGED_SECRET_LIFETIME - 1))
        self.assertTrue(rt.staged_secrets_expired({'created': created}, created + rt.STAGED_SECRET_LIFETIME))
        for invalid in (None, True, 0, -1, float('nan'), float('inf'), '1000'):
            with self.subTest(created=invalid), self.assertRaisesRegex(ValueError, 'age is unknown'):
                rt.staged_secrets_expired({'created': invalid}, 2000)
        with self.assertRaisesRegex(ValueError, 'age is unknown'):
            rt.staged_secrets_expired({'created': created}, created - 1)
        rt.require_fresh_staged_secrets({'secretChanges': []})

    def test_publish_once_rejects_aliases_hardlinks_and_writable_ancestors(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / 'private'
            directory.mkdir(mode=0o700)
            values = {'arcanePassword': SECRETS['staged']}
            rt.stage_secrets(directory, values)
            secret = directory / 'secrets.json'
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
            self.assertEqual(rt.read_staged_secrets(directory, list(values)), values)
            with self.assertRaisesRegex(ValueError, 'publication failed'):
                rt.stage_secrets(directory, {'arcanePassword': SECRETS['arcane']})
            self.assertEqual(rt.read_staged_secrets(directory, list(values)), values)
            self.assertFalse(list(directory.glob('.secrets.json*')))
            os.link(secret, directory / 'alias')
            with self.assertRaisesRegex(ValueError, 'invalid or unreadable'):
                rt.read_staged_secrets(directory, list(values))
            (directory / 'alias').unlink()
            alias = Path(folder) / 'alias'
            alias.symlink_to(directory, target_is_directory=True)
            for path in (alias,):
                with self.assertRaises(ValueError): rt.read_staged_secrets(path, list(values))
                with self.assertRaises(ValueError): rt.remove_staged_secrets(path)
                with self.assertRaises(ValueError): rt.stage_secrets(path, values)
            Path(folder).chmod(0o777)
            try:
                with self.assertRaises(ValueError): rt.read_staged_secrets(directory, list(values))
                with self.assertRaises(ValueError): rt.remove_staged_secrets(directory)
                with self.assertRaises(ValueError): rt.stage_secrets(directory, values)
            finally:
                Path(folder).chmod(0o700)
            self.assertEqual(secret.read_text().count(SECRETS['staged']), 1)

    def test_expired_failed_work_checks_all_workers_and_preserves_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); directory = root / 'jobs' / ('d' * 32)
            rt.write_json(directory / 'job.json', {'status': 'failed', 'created': time.time() - rt.STAGED_SECRET_LIFETIME})
            rt.stage_secrets(directory, {'arcanePassword': SECRETS['staged']})
            rt.atomic(directory / 'recovery' / 'secret-arcanePassword', SECRETS['arcane'])
            with patch.object(rt, 'worker_active', side_effect=[False, True]):
                rt.cleanup_terminal_secrets(root)
            self.assertTrue((directory / 'secrets.json').exists())
            with patch.object(rt, 'worker_active', return_value=False) as workers:
                rt.cleanup_terminal_secrets(root)
            self.assertEqual(workers.call_count, 3)
            self.assertFalse((directory / 'secrets.json').exists())
            self.assertEqual((directory / 'recovery' / 'secret-arcanePassword').read_text(), SECRETS['arcane'])

    def test_legacy_partial_files_are_removed_even_when_recovery_is_pending(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); directory = root / 'jobs' / ('e' * 32)
            rt.write_json(directory / 'job.json', {'status': 'applying', 'created': time.time()})
            rt.write_json(root / 'pending.json', {'id': directory.name})
            rt.stage_secrets(directory, {'arcanePassword': SECRETS['staged']})
            rt.atomic(directory / '.secrets.jsonabcdefgh', SECRETS['staged'])
            rt.atomic(directory / '.secrets.json-keep', 'unrelated')
            rt.cleanup_terminal_secrets(root)
            self.assertFalse((directory / '.secrets.jsonabcdefgh').exists())
            self.assertTrue((directory / '.secrets.json-keep').exists())
            self.assertEqual(rt.read_staged_secrets(directory, ['arcanePassword'])['arcanePassword'], SECRETS['staged'])

    def test_sigkill_at_publication_boundaries_eventually_scrubs_only_staging(self):
        code = '''
import json, os, signal, sys
sys.path[:0] = [sys.argv[1] + '/admin', sys.argv[1] + '/scripts']
import runtime as rt
point = sys.argv[3]
link, sync, unlink = rt.os.link, rt.os.fsync, rt.unlink_staged
def die(): os.kill(os.getpid(), signal.SIGKILL)
def publish(*a, **kw):
    if point == 'before-link': die()
    link(*a, **kw)
    if point == 'after-link': die()
def fsync(fd):
    sync(fd)
    if point == 'file-sync': die()
def clean(*a):
    unlink(*a)
    if point == 'after-unlink': die()
rt.os.link, rt.os.fsync, rt.unlink_staged = publish, fsync, clean
rt.stage_secrets(sys.argv[2], json.load(sys.stdin))
'''
        for point in ('file-sync', 'before-link', 'after-link', 'after-unlink'):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as folder:
                root = Path(folder); directory = root / 'jobs' / ('f' * 32)
                rt.write_json(directory / 'job.json', {'status': 'building', 'created': time.time() - rt.STAGED_SECRET_LIFETIME})
                rt.atomic(directory / 'recovery' / 'manifest.json', 'rollback evidence')
                child = subprocess.run([sys.executable, '-c', code, str(BASE), str(directory), point],
                                       input=json.dumps({'arcanePassword': SECRETS['staged']}),
                                       capture_output=True, text=True, timeout=10)
                self.assertEqual(child.returncode, -9)
                assert_public_clean(self, [child.stdout, child.stderr])
                with patch.object(rt, 'worker_active', return_value=False):
                    rt.cleanup_terminal_secrets(root)
                self.assertFalse((directory / 'secrets.json').exists())
                self.assertFalse(list(directory.glob('.secrets.json*')))
                self.assertEqual((directory / 'recovery' / 'manifest.json').read_text(), 'rollback evidence')
                assert_public_clean(self, (directory / 'job.json').read_text())


if __name__ == '__main__':
    unittest.main()
