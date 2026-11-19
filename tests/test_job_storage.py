"""Shared storage mechanics preserve domain-specific state and reject path aliases."""
from concurrent.futures import ThreadPoolExecutor
import errno
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import runtime as rt


class StorageTests(unittest.TestCase):
    def test_ids_must_be_existing_directories_without_symlink_aliases(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            identity = 'a' * 32
            with self.assertRaisesRegex(ValueError, 'Unknown operation'):
                rt.job_directory(root, identity, 'operation')
            (root / identity).mkdir()
            self.assertEqual(rt.job_directory(root, identity), root / identity)
            for bad in ('../' + identity, '/', 'A' * 32, None, 'a' * 31):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    rt.job_directory(root, bad)
            alias = 'b' * 32
            (root / alias).symlink_to(root / identity)
            with self.assertRaises(ValueError):
                rt.job_directory(root, alias)

    def test_records_reject_aliases_special_files_and_malformed_json(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / ('a' * 32)
            directory.mkdir()
            record = directory / 'job.json'
            self.assertIsNone(rt.read_job_record(record, None))
            with self.assertRaisesRegex(ValueError, 'Job record is missing'):
                rt.read_job_record(record)

            rt.write_json(record, {'status': 'confirmed', 'legacyField': 'retain'})
            self.assertEqual(rt.read_job_record(record)['legacyField'], 'retain')
            alias = root / ('b' * 32)
            alias.symlink_to(directory)
            with self.assertRaisesRegex(ValueError, 'Job record is invalid'):
                rt.read_job_record(alias / record.name)
            other = root / 'job.json'
            for link in (os.symlink, os.link):
                link(record, other)
                try:
                    with self.assertRaisesRegex(ValueError, 'Job record is invalid'):
                        rt.read_job_record(other, {})
                finally:
                    other.unlink()
            os.mkfifo(other)
            with self.assertRaisesRegex(ValueError, 'Job record is invalid'):
                rt.read_job_record(other)
            for raw in (b'{', b'[]', b'\xff', b'X' * 65):
                record.write_bytes(raw)
                with self.subTest(raw=raw), patch.object(rt, '_JOB_RECORD_LIMIT', 64):
                    with self.assertRaisesRegex(ValueError, 'Job record is invalid'):
                        rt.read_job_record(record, {})
            rt.write_json(record, {'status': 'confirmed'})
            record.chmod(0o622)
            with self.assertRaisesRegex(ValueError, 'Job record is invalid'):
                rt.read_job_record(record)

    def test_failed_writes_before_replace_leave_a_readable_previous_record(self):
        with tempfile.TemporaryDirectory() as folder:
            record = Path(folder) / 'job.json'
            rt.write_json(record, {'status': 'building'})
            for operation, code in (('fsync', errno.ENOSPC), ('replace', errno.EROFS)):
                with self.subTest(operation=operation), patch.object(rt.os, operation, side_effect=OSError(code, 'injected')):
                    with self.assertRaises(OSError):
                        rt.write_json(record, {'status': 'ready'})
                self.assertEqual(rt.read_job_record(record), {'status': 'building'})
                self.assertEqual(list(record.parent.iterdir()), [record])

    def test_concurrent_record_replacement_never_returns_a_partial_record(self):
        with tempfile.TemporaryDirectory() as folder:
            record = Path(folder) / 'job.json'
            older = {'status': 'building', 'data': 'a' * 5000}
            newer = {'status': 'ready', 'data': 'b' * 9000}
            rt.write_json(record, older)
            def write_updates():
                for _ in range(16):
                    rt.write_json(record, newer)
                    rt.write_json(record, older)
            with ThreadPoolExecutor(max_workers=1) as pool:
                writer = pool.submit(write_updates)
                for _ in range(64):
                    self.assertIn(rt.read_job_record(record), (older, newer))
                writer.result()

    def test_log_read_is_bounded_and_handles_missing_or_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / 'build.log'
            self.assertEqual(rt.tail_log(log), '')
            log.write_bytes(b'x' * 100000 + b'\xffend')
            self.assertEqual(rt.tail_log(log, 4), '\ufffdend')
            self.assertEqual(len(rt.tail_log(log)), 65536)
            alias = log.with_name('alias.log')
            alias.symlink_to(log)
            with self.assertRaisesRegex(ValueError, 'Job log is invalid'):
                rt.tail_log(alias)


if __name__ == '__main__':
    unittest.main()
