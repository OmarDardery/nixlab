"""Shared storage mechanics preserve domain-specific state and reject path aliases."""
from pathlib import Path
import sys
import tempfile
import unittest

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

    def test_log_read_is_bounded_and_handles_missing_or_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / 'build.log'
            self.assertEqual(rt.tail_log(log), '')
            log.write_bytes(b'x' * 100000 + b'\xffend')
            self.assertEqual(rt.tail_log(log, 4), '\ufffdend')
            self.assertEqual(len(rt.tail_log(log)), 65536)


if __name__ == '__main__':
    unittest.main()
