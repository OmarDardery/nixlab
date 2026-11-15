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


    def test_public_entrypoints_and_unreferenced_test_fixture(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = {
                'flake.nix': '{ outputs = _: {}; }',
                'installer/install.py': 'pass',
                'tests/fixtures/unreferenced.json': '{}',
                'tests/test_script.py': 'pass',
            }
            for name, body in files.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body)
            report = audit.audit(root, files)
            rows = {row['path']: row for row in report['artifacts']}
            for name in ('flake.nix', 'installer/install.py'):
                self.assertEqual(rows[name]['action'], 'keep')
                self.assertEqual(rows[name]['classification'], 'public entrypoint')
                self.assertEqual(rows[name]['confidence'], 'unknown')
                self.assertEqual(rows[name]['publicCompatibility'], 'protected known entrypoint')
            orphan = rows['tests/fixtures/unreferenced.json']
            self.assertEqual(orphan['classification'], 'test fixture')
            self.assertEqual(orphan['action'], 'investigate')
            self.assertEqual(orphan['confidence'], 'unknown')
            self.assertNotIn('safe to delete', orphan['action'])

    def test_report_is_deterministic_and_records_real_line_count_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = {
                'admin/main.py': 'one\ntwo\n',
                'tests/test_sample.py': 'test\n',
                'tests/fixtures/architecture-contract.json': (
                    '{"cliBinaries":{"nixlab-admin":"admin/package.nix"},'
                    '"flakeAttributes":["apps.x86_64-linux.install"],'
                    '"machinePaths":{"modern":{"settings":"machine/settings.json"}},'
                    '"runtimeDefaults":{"NIXLAB_STATE":"/var/lib/nixlab-admin"},'
                    '"systemdUnits":{"modules/admin-ui.nix":["nixlab-ui"]}}'
                ),
            }
            for name, body in files.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body)
            first, second = audit.audit(root, files), audit.audit(root, reversed(list(files)))
            self.assertEqual(first, second)
            baseline = first['baseline']
            self.assertEqual(baseline['trackedFiles'], 3)
            self.assertEqual(baseline['sourceFiles'], 1)
            self.assertEqual(baseline['testFiles'], 2)
            self.assertEqual(baseline['sourceLinesInspected'], 2)
            self.assertEqual(baseline['testLinesInspected'], 1)
            self.assertEqual(baseline['cliBinaries'], ['nixlab-admin'])
            self.assertEqual(baseline['publicFlakeAttributesFromContract'],
                             ['apps.x86_64-linux.install'])
            self.assertEqual(baseline['systemdUnitsFromContract']['modules/admin-ui.nix'], ['nixlab-ui'])

    def test_inspection_limits_are_explicit_and_symlinks_are_not_traversed(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as elsewhere:
            root = Path(folder)
            (root / 'font.woff2').write_bytes(b'\0\xff')
            (root / 'giant.md').write_text('x' * (audit.MAX_TEXT_BYTES + 1))
            (root / 'outside').symlink_to(Path(elsewhere), target_is_directory=True)
            (Path(elsewhere) / 'private.py').write_text('NEVER_READ_THIS')
            files = ['font.woff2', 'giant.md', 'outside/private.py', 'missing.py']
            report = audit.audit(root, files)
            omitted = report['inspectionOmissions']
            self.assertIn('binary', omitted['font.woff2'])
            self.assertIn('larger than', omitted['giant.md'])
            self.assertIn('symlink', omitted['outside/private.py'])
            self.assertIn('missing', omitted['missing.py'])
            self.assertEqual(report['textCount'], 0)
            self.assertEqual({row['inspection'] for row in report['artifacts']}, {'not-inspected'})
            self.assertTrue(all(row['confidence'] == 'unknown' for row in report['artifacts']))

    def test_generated_artifact_is_not_confused_with_source_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'untracked.pyc').write_bytes(b'\0\xff')
            row = audit.audit(root, ['untracked.pyc'])['artifacts'][0]
            self.assertEqual(row['classification'], 'generated (tracked)')
            self.assertEqual(row['inspection'], 'not-inspected')
            self.assertEqual(row['action'], 'investigate')

    def test_known_referenced_asset_has_a_named_consumer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'web.py').write_text("serve('theme.css')\n")
            (root / 'theme.css').write_text('a { color: inherit; }')
            rows = {row['path']: row for row in
                    audit.audit(root, ['theme.css', 'web.py'])['artifacts']}
            self.assertEqual(rows['theme.css']['observedReferences'], ['web.py'])
            self.assertEqual(rows['theme.css']['confidence'], 'text-reference-only')
            self.assertTrue(rows['theme.css']['action'] == 'keep')

    def test_nul_and_absolute_paths_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('../secret', '/etc/shadow', 'invalid\0name'):
                with self.subTest(name=repr(name)), self.assertRaisesRegex(ValueError, 'Invalid tracked path'):
                    audit.audit(Path(folder), [name])



if __name__ == '__main__':
    unittest.main()
