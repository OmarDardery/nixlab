"""The Python reader invokes Nix; it never substitutes another migration policy."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import settings_reader


class ReaderTests(unittest.TestCase):
    def test_selected_source_is_evaluated_once_without_a_flake_fetch(self):
        desired = {'schemaVersion': 1, 'monitoring': {'collection': False}}
        with patch.object(settings_reader.subprocess, 'run', return_value=Mock(returncode=0, stdout=json.dumps(desired))) as run:
            self.assertEqual(settings_reader.read_settings(Path('/fixture/source')), desired)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ['nix-instantiate', '--eval', '--strict', '--json', '/fixture/source/site.nix'])
        self.assertEqual(run.call_args.kwargs['timeout'], 10)

    def test_failed_evaluation_does_not_leak_nix_stderr_or_fall_back(self):
        with patch.object(settings_reader.subprocess, 'run', return_value=Mock(returncode=1, stderr='SENTINEL-private-config', stdout='')):
            with self.assertRaises(ValueError) as caught:
                settings_reader.read_settings('/fixture')
            self.assertNotIn('SENTINEL', str(caught.exception))

    def test_missing_evaluator_timeout_and_invalid_output_fail_closed(self):
        for error in (OSError('SENTINEL'), subprocess.TimeoutExpired('nix', 10)):
            with self.subTest(error=type(error)), patch.object(settings_reader.subprocess, 'run', side_effect=error):
                with self.assertRaisesRegex(ValueError, 'unavailable'):
                    settings_reader.read_settings('/fixture')
        for output in ('[]', 'null', 'SENTINEL-not-json'):
            with self.subTest(output=output), patch.object(settings_reader.subprocess, 'run', return_value=Mock(returncode=0, stdout=output)):
                with self.assertRaises(ValueError) as caught:
                    settings_reader.read_settings('/fixture')
                self.assertNotIn('SENTINEL', str(caught.exception))

    def test_future_schema_has_a_safe_actionable_error(self):
        with patch.object(settings_reader.subprocess, 'run', return_value=Mock(returncode=1,
                   stderr='Unsupported NixLab settings schema version\nSENTINEL-config-context')):
            with self.assertRaisesRegex(ValueError, 'Unsupported NixLab settings schema version') as caught:
                settings_reader.read_settings('/fixture')
            self.assertNotIn('SENTINEL', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
