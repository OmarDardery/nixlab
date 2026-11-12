"""Beta publishing rejects stale, skipped, wrong-revision and simulated gates."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts'), str(BASE / 'tests')]
import commissioning as c
import runtime as rt
from test_commissioning import NOW, report

spec = importlib.util.spec_from_file_location('beta_release', BASE / 'scripts/release.py')
release = importlib.util.module_from_spec(spec); spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_restore_requires_full_recorded_data_recovery_on_the_candidate(self):
        record = {'phase': 'complete', 'restore': 'operator-attested', 'coverage': {'complete': True},
                  'sourceRevision': 'a' * 40, 'sourceInputsDigest': 'b' * 64, 'durationSeconds': 42.5}
        self.assertEqual(release.recovery_evidence(record, 'a' * 40, 'b' * 64), 42.5)
        for change in ({'phase': 'isolated'}, {'restore': 'unverified'}, {'restore': 'operator-attested-selected'},
                       {'coverage': {'complete': False}}, {'sourceRevision': 'c' * 40}, {'durationSeconds': True},
                       {'durationSeconds': float('nan')}, {'durationSeconds': float('inf')}):
            with self.assertRaises(ValueError): release.recovery_evidence({**record, **change}, 'a' * 40, 'b' * 64)

    def test_tag_must_match_beta_version_without_accepting_stable_or_rc(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'VERSION'
            for value, tag in [('0.1.0', 'v0.1.0'), ('0.1.0-rc1', 'v0.1.0-rc1'), ('0.1.0-beta.1', 'v0.1.0-beta.2')]:
                path.write_text(value)
                with self.assertRaises(ValueError): release.version(tag, path)
            path.write_text('0.1.0-beta.1')
            self.assertEqual(release.version('v0.1.0-beta.1', path), '0.1.0-beta.1')

    def physical(self, folder, mode):
        result = report(True); result['context'].update(version='0.1.0-beta.1', mode=mode)
        for item in result['evidence']: item['contextDigest'] = c.digest(result['context'])
        path = Path(folder) / (mode + '.json'); rt.write_json(path, result)
        return path

    def test_requires_current_fresh_and_upgrade_physical_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [self.physical(folder, mode) for mode in ('fresh', 'upgrade')]
            args = (paths, 'a' * 40, '0.1.0-beta.1', 'c' * 64)
            self.assertEqual(release.physical_reports(*args, now=NOW)['modes'], ['fresh', 'upgrade'])
            for change in ({'scope': 'virtual-machine'}, {'environment': 'nested-integration'}, {'revision': 'b' * 40},
                           {'inputsDigest': 'e' * 64}, {'apps': False}, {'hardwareDigest': '1' * 64}):
                with self.subTest(change=change):
                    path = self.physical(folder, 'fresh'); result = c.load(path); result['context'].update(change)
                    for item in result['evidence']: item['contextDigest'] = c.digest(result['context'])
                    rt.write_json(path, result)
                    with self.assertRaises(ValueError): release.physical_reports(*args, now=NOW)
            self.physical(folder, 'fresh')
            with self.assertRaises(ValueError): release.physical_reports(*args, now=NOW + c.MAX_AGE + 1)

    def run_evidence(self, key):
        run = {'head_sha': 'a' * 40, 'status': 'completed', 'conclusion': 'success',
               'path': '.github/workflows/' + release.WORKFLOWS[key], 'event': 'push',
               'repository': {'full_name': 'OmarDardery/nixlab'}, 'html_url': 'https://github.com/example/run'}
        name = 'Three independent real apps VM update and recovery trials' if key == 'apps' else 'Deploy to GitHub Pages' if key == 'website' else 'Required test'
        jobs = [{'status': 'completed', 'conclusion': 'success', 'steps': [{'name': name, 'status': 'completed', 'conclusion': 'success'}]}]
        return run, jobs

    def test_every_required_workflow_rejects_failed_skipped_or_wrong_revision(self):
        for key in release.WORKFLOWS:
            run, jobs = self.run_evidence(key)
            release.ci_run(key, run, jobs, 'a' * 40, 'OmarDardery/nixlab')
            for mutate in (lambda r, j: r.update(head_sha='b' * 40), lambda r, j: r.update(conclusion='failure'),
                           lambda r, j: r.update(event='pull_request'), lambda r, j: j[0].update(conclusion='skipped'),
                           lambda r, j: j[0]['steps'][0].update(conclusion='skipped')):
                candidate, checks = copy.deepcopy(run), copy.deepcopy(jobs); mutate(candidate, checks)
                with self.assertRaises(ValueError): release.ci_run(key, candidate, checks, 'a' * 40, 'OmarDardery/nixlab')

    def test_old_apps_lane_and_build_only_website_are_not_release_qualification(self):
        for key in ('apps', 'website'):
            run, jobs = self.run_evidence(key); jobs[0]['steps'][0]['name'] = 'Earlier fixture'
            with self.assertRaises(ValueError): release.ci_run(key, run, jobs, 'a' * 40, 'OmarDardery/nixlab')

    def test_only_ineligible_tag_check_can_be_skipped(self):
        run, jobs = self.run_evidence('validate')
        jobs[0]['steps'].append({'name': 'Check release tag version', 'status': 'completed', 'conclusion': 'skipped'})
        release.ci_run('validate', run, jobs, 'a' * 40, 'OmarDardery/nixlab')
        jobs[0]['steps'][-1]['name'] = 'Guest integration'
        with self.assertRaises(ValueError): release.ci_run('validate', run, jobs, 'a' * 40, 'OmarDardery/nixlab')


if __name__ == '__main__':
    unittest.main()
