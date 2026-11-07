"""Qualification must fail closed across stale, missing and simulated evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
import commissioning as c

NOW = 1800000000


def context():
    return dict(revision='a' * 40, version='0.1.0-rc1', sourceDigest='b' * 64,
                inputsDigest='c' * 64, settingsDigest='d' * 64, system='/nix/store/fixture-system',
                kernel='7.0', incus='e' * 64, hardwareDigest='f' * 64, scope='physical-host',
                architecture='x86_64', apps=True, hardwareDevices=False, mode='fresh', environment='installed-host')


def report(complete=False):
    ctx = context()
    result = {'format': 1, 'context': ctx, 'evidence': []}
    if complete:
        for check in c.CHECKS:
            c.record(result, ctx, check, 'passed', 'automated-probe' if check in c.AUTOMATIC else 'manual-attestation', NOW, '1' * 64)
        for boot in ('2' * 64, '3' * 64):
            c.record(result, ctx, 'management', 'passed', 'automated-probe', NOW, boot)
    return result


class CommissioningTests(unittest.TestCase):
    def summary(self, result, **kwargs):
        return c.summarize(result, now=NOW, **kwargs)

    def test_missing_evidence_never_qualifies(self):
        value = self.summary(report())
        self.assertEqual(value['qualification'], 'unqualified')
        self.assertTrue(all(r['status'] != 'passed' for r in value['checks']))

    def test_complete_physical_attestations_require_review_not_certification(self):
        self.assertEqual(self.summary(report(True))['qualification'], 'ready-for-review')
        self.assertEqual(self.summary(report(True), revision='0' * 40)['qualification'], 'unqualified')

    def test_changes_invalidate_prior_evidence(self):
        result = report(True)
        for key, value in [('kernel', '7.1'), ('incus', '1' * 64), ('sourceDigest', '1' * 64),
                           ('inputsDigest', '1' * 64), ('settingsDigest', '1' * 64),
                           ('system', '/nix/store/new-system'), ('hardwareDigest', '1' * 64)]:
            with self.subTest(key=key):
                current = dict(result['context'], **{key: value})
                summary = self.summary(result, current=current)
                self.assertEqual(summary['qualification'], 'unqualified')
                self.assertTrue(all(r['status'] == 'stale' for r in summary['checks']))

    def test_expired_and_future_evidence_do_not_pass(self):
        for stamp in (NOW - c.MAX_AGE - 1, NOW + 10):
            result = report(True)
            for item in result['evidence']: item['time'] = stamp
            self.assertEqual(self.summary(result)['qualification'], 'unqualified')

    def test_latest_failure_or_unknown_overrides_prior_pass(self):
        for status in ('failed', 'unknown', 'skipped', 'unsupported'):
            result = report(True)
            c.record(result, result['context'], 'kvm', status, 'automated-probe', NOW)
            self.assertEqual(self.summary(result)['qualification'], 'unqualified')

    def test_fixtures_nested_and_unknown_scope_never_qualify_hardware(self):
        for field, values in [('scope', ['virtual-machine', 'container', 'unknown']),
                              ('environment', ['automated-fixture', 'nested-integration']),
                              ('revision', ['unknown']), ('system', ['unknown']), ('incus', ['unknown'])]:
            for value in values:
                result = report(True)
                result['context'][field] = value
                for item in result['evidence']: item['contextDigest'] = c.digest(result['context'])
                self.assertEqual(self.summary(result)['qualification'], 'unqualified')

    def test_reboot_requires_three_distinct_healthy_boot_observations(self):
        result = report(True)
        for item in result['evidence']: item['boot'] = '1' * 64
        self.assertEqual(self.summary(result)['qualification'], 'unqualified')
        c.record(result, result['context'], 'management', 'passed', 'automated-probe', NOW, '2' * 64)
        self.assertEqual(self.summary(result)['qualification'], 'unqualified')
        c.record(result, result['context'], 'management', 'passed', 'automated-probe', NOW, '3' * 64)
        self.assertEqual(self.summary(result)['qualification'], 'ready-for-review')

    def test_old_context_boots_cannot_complete_new_reboot_check(self):
        result = report(True)
        result['context']['kernel'] = '7.1'
        for check in c.CHECKS:
            c.record(result, result['context'], check, 'passed', 'automated-probe' if check in c.AUTOMATIC else 'manual-attestation', NOW, '1' * 64)
        self.assertEqual(self.summary(result)['qualification'], 'unqualified')

    def test_disabled_apps_are_optional_but_enabled_apps_require_evidence(self):
        result = report(True)
        result['context']['apps'] = False
        for item in result['evidence']:
            item['contextDigest'] = c.digest(result['context'])
            if item['check'].startswith('apps-'): item['status'] = 'skipped'
        self.assertEqual(self.summary(result)['qualification'], 'ready-for-review')
        self.assertEqual(self.summary(result)['profile'], 'core')
        result['context']['apps'] = True
        for item in result['evidence']: item['contextDigest'] = c.digest(result['context'])
        self.assertEqual(self.summary(result)['qualification'], 'unqualified')

    def test_configured_hardware_needs_reconnect_evidence(self):
        result = report(True)
        result['context']['hardwareDevices'] = True
        result['evidence'] = [e for e in result['evidence'] if e['check'] != 'hardware-reconnect']
        for item in result['evidence']: item['contextDigest'] = c.digest(result['context'])
        self.assertEqual(self.summary(result)['qualification'], 'unqualified')

    def test_reports_reject_unknown_fields_and_unsafe_values(self):
        for mutate in (lambda r: r.update(secret='private'),
                       lambda r: r['context'].update(password='private'),
                       lambda r: r['context'].update(version='injected | table\nrow'),
                       lambda r: r['evidence'][0].update(output='private')):
            result = report(True); mutate(result)
            with self.assertRaises(ValueError): c.validate(result)

    def test_manual_procedures_cannot_be_relabeled_as_automated(self):
        result = report(True)
        item = next(e for e in result['evidence'] if e['check'] == 'offhost-restore')
        item['type'] = 'automated-probe'
        with self.assertRaises(ValueError): c.validate(result)

    def test_matrix_requires_exact_revision_and_does_not_export_raw_identifiers(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.json'
            path.write_text(json.dumps(report(True)))
            with patch.object(c.time, 'time', return_value=NOW):
                self.assertIn('ready-for-review', c.matrix([path], 'a' * 40))
                self.assertNotIn('ready-for-review |', c.matrix([path], 'b' * 40))
            self.assertIn('unqualified', c.matrix([], 'a' * 40))
            with self.assertRaises(ValueError): c.matrix([path], 'main')

    def test_record_requires_acknowledgment_before_any_collection(self):
        with patch.object(c.os, 'geteuid', return_value=0), patch.object(c, 'context') as get_context:
            with self.assertRaisesRegex(ValueError, 'procedure first'):
                c.cli(['record', '--mode', 'fresh', '--check', 'offhost-restore', '--status', 'passed'])
            get_context.assert_not_called()

    def test_capture_contains_no_response_bodies_credentials_or_host_addresses(self):
        result = report()
        settings = {'apps': {'enable': False}}
        ctx = dict(context(), apps=False)
        with patch('diagnostics.probe', return_value={'ok': True, 'address': 'private-address'}), \
             patch.object(c, 'command', side_effect=lambda args: '\n'.join(['active'] * (len(args) - 2)) if args[0] == 'systemctl' else '[]'), \
             patch('builtins.open', side_effect=FileNotFoundError('SECRET-PATH')), \
             patch.object(c, 'boot_id', return_value='1' * 64):
            c.collect(result, ctx, settings)
        exported = json.dumps(result)
        self.assertNotIn('SECRET-PATH', exported)
        self.assertNotIn('private-address', exported)
        states = {e['check']: e['status'] for e in result['evidence']}
        self.assertEqual(states['kvm'], 'unsupported')
        self.assertEqual(states['apps-auth'], 'skipped')
        self.assertEqual(states['container'], 'failed')

    def test_read_only_capture_checks_existing_apps_without_resetting_credentials(self):
        result = report()
        def probe(args):
            if 'is-active' in args: return 'active'
            if 'list' in args: return '[{"type":"container","status":"Running"}]'
            return '{"type":"virtual-machine","status":"Running"}'
        with patch('diagnostics.probe', return_value={'ok': True}), patch.object(c, 'command', side_effect=probe) as run, \
             patch('builtins.open', side_effect=OSError('unavailable')), \
             patch('portainer.credentials', return_value={'password': 'SECRET'}), patch('portainer.reset') as reset:
            c.collect(result, context(), {'apps': {'enable': True}})
        reset.assert_not_called()
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertTrue(all(not set(args[0]) & {'stop', 'start', 'restart', 'reboot', 'delete', 'import'} for args, _ in run.call_args_list))


if __name__ == '__main__': unittest.main()
