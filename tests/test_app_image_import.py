import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('app_image', Path(__file__).resolve().parents[1] / 'scripts/import-app-image.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ImageImportTests(unittest.TestCase):
    def test_split_fingerprint_hashes_metadata_then_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ('metadata', 'disk')]
            paths[0].write_bytes(b'meta')
            paths[1].write_bytes(b'disk')
            self.assertEqual(module.fingerprint(paths), module.hashlib.sha256(b'metadisk').hexdigest())

    def ensure(self, images, aliases):
        def fake(*args):
            if args[:2] == ('image', 'list'): return json.dumps(images)
            if args[:3] == ('image', 'alias', 'list'): return json.dumps(aliases)
            return ''
        with patch.object(module, 'fingerprint', return_value='abc'), patch.object(module, 'run', side_effect=fake) as run:
            module.ensure_image('meta', 'disk')
        return [call.args for call in run.call_args_list]

    def test_retry_keeps_existing_image_and_alias(self):
        calls = self.ensure([{'fingerprint': 'abc'}], [{'name': 'nixlab-apps', 'target': 'abc'}])
        self.assertFalse(any('import' in call or 'delete' in call or 'create' in call for call in calls))

    def test_partial_import_repairs_missing_alias(self):
        calls = self.ensure([{'fingerprint': 'abc'}], [])
        self.assertNotIn(('image', 'import', 'meta', 'disk'), calls)
        self.assertIn(('image', 'alias', 'create', 'nixlab-apps', 'abc'), calls)

    def test_new_image_updates_alias_without_deleting_images(self):
        calls = self.ensure([], [{'name': 'nixlab-apps', 'target': 'old'}])
        self.assertIn(('image', 'import', 'meta', 'disk'), calls)
        self.assertIn(('image', 'alias', 'delete', 'nixlab-apps'), calls)
        self.assertFalse(any(call[:2] == ('image', 'delete') for call in calls))

    def test_import_failure_is_not_swallowed(self):
        with patch.object(module, 'fingerprint', return_value='abc'), patch.object(module, 'run', side_effect=['[]', subprocess.CalledProcessError(1, 'incus')]) as run:
            with self.assertRaises(subprocess.CalledProcessError): module.ensure_image('meta', 'disk')
        self.assertEqual(run.call_count, 2)
