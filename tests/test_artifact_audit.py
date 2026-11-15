"""Advisory reachability must preserve uncertainty and never mutate inputs."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('artifact_audit', Path(__file__).resolve().parents[1] / 'scripts/artifact_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def test_refs_orphans_and_dynamic_unknown_are_advisory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = {'unit.nix': 'ExecStart = "python helper.py";',
                     'helper.py': 'pass', 'orphan.py': 'pass',
                     'fixture.json': '{}', 'dynamic.py': 'pass'}
            for name, text in files.items():
                (root / name).write_text(text)
            report = audit.audit(root, files)
            rows = {row['path']: row for row in report['artifacts']}
            self.assertEqual(rows['helper.py']['observedReferences'], ['unit.nix'])
            self.assertEqual(rows['orphan.py']['action'], 'investigate')
            self.assertEqual(rows['fixture.json']['confidence'], 'unknown')
            self.assertEqual(rows['dynamic.py']['confidence'], 'unknown')
            self.assertTrue(report['advisory'])
            self.assertEqual({name: (root / name).read_text() for name in files}, files)
            (root / 'unit.nix').write_text('{}')
            rows = {row['path']: row for row in audit.audit(root, files)['artifacts']}
            self.assertEqual(rows['helper.py']['confidence'], 'unknown')

    def test_manifest_cannot_escape_root(self):
        with self.assertRaises(ValueError):
            audit.audit(Path('/tmp'), ['../secret'])


if __name__ == '__main__':
    unittest.main()
