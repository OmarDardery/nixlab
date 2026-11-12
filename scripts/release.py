"""Fail-closed source-only beta release evidence, using existing CI/report formats."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'admin'))
import commissioning

WORKFLOWS = {'validate': 'validate.yml', 'installer': 'installer.yml', 'portal': 'portal-entry.yml',
             'apps': 'guest-deployments.yml', 'commissioning': 'commissioning.yml', 'website': 'pages.yml'}


def version(tag, version_file):
    value = version_file.read_text().strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+-beta\.[0-9]+', value) or tag != 'v' + value:
        raise ValueError('A beta tag must exactly match VERSION; stable/RC publication is not qualified by this workflow')
    return value


def physical_reports(paths, revision, release_version, inputs, now=None):
    reports = [commissioning.load(path) for path in paths]
    hardware = set(); modes = set()
    for report in reports:
        summary = commissioning.summarize(report, revision=revision, now=now)
        context = report['context']
        if (summary['qualification'] != 'ready-for-review' or summary['profile'] != 'apps'
                or context['version'] != release_version or context['inputsDigest'] != inputs):
            raise ValueError('Require current physical apps-profile fresh/upgrade evidence for this exact source, version and inputs')
        hardware.add(context['hardwareDigest']); modes.add(context['mode'])
    if len(hardware) != 1 or modes != {'fresh', 'upgrade'}:
        raise ValueError('Require fresh and upgrade reports on the same reference hardware')
    return {'hardwareDigest': next(iter(hardware)), 'modes': sorted(modes),
            'evidence': 'operator reports with automated probes and manual attestations; not independent certification'}


def ci_run(key, run, jobs, revision, repository):
    if (run.get('head_sha') != revision or run.get('conclusion') != 'success'
            or run.get('status') != 'completed' or run.get('path') != '.github/workflows/' + WORKFLOWS[key]
            or run.get('event') not in ('push', 'workflow_dispatch')
            or run.get('repository', {}).get('full_name') != repository):
        raise ValueError('Required CI run is incomplete, failed, wrong-scope or on another revision: ' + key)
    if not jobs: raise ValueError('Required CI run has no jobs: ' + key)
    observed = []
    for job in jobs:
        if job.get('status') != 'completed' or job.get('conclusion') != 'success':
            raise ValueError('A required CI job did not pass: ' + key)
        if not job.get('steps'): raise ValueError('Required CI job has no step evidence: ' + key)
        for step in job.get('steps', []):
            # This condition is tested independently by version() for a release.
            if key == 'validate' and step['name'] == 'Check release tag version' and step.get('conclusion') == 'skipped': continue
            if step.get('status') != 'completed' or step.get('conclusion') != 'success':
                raise ValueError('A required CI step failed or was skipped: ' + key)
            observed.append(step['name'])
    if key == 'apps' and 'Three independent real apps VM update and recovery trials' not in observed:
        raise ValueError('Require the three-trial dedicated KVM lane, not an older apps fixture')
    if key == 'website' and 'Deploy to GitHub Pages' not in observed:
        raise ValueError('Website validation alone is not successful Pages publication')
    return run['html_url']


def gh_json(*args):
    return json.loads(subprocess.check_output(['gh', 'api', *args], text=True, timeout=60))


def recovery_evidence(restored, revision, inputs, now=None):
    now = time.time() if now is None else now
    if (restored.get('format') != 1 or restored.get('evidenceType') != 'operator-attestation'
            or restored.get('phase') != 'complete' or restored.get('restore') != 'operator-attested'
            or restored.get('coverage', {}).get('complete') is not True
            or restored.get('sourceRevision') != revision or restored.get('sourceInputsDigest') != inputs
            or type(restored.get('verifiedAt')) not in (int, float) or not 0 <= now - restored['verifiedAt'] <= commissioning.MAX_AGE
            or type(restored.get('backupCreated')) not in (int, float) or not 0 <= restored['backupCreated'] <= restored['verifiedAt']
            or type(restored.get('durationSeconds')) not in (int, float) or not 0 <= restored['durationSeconds'] < float('inf')):
        raise ValueError('Require current full-coverage recovery evidence for this source/inputs with recorded duration')
    return restored['durationSeconds']


def prepare(args):
    release_version = version(args.tag, Path('VERSION'))
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    tagged = subprocess.check_output(['git', 'rev-parse', args.tag + '^{commit}'], text=True).strip()
    if revision != tagged: raise ValueError('Checked-out source does not match the release tag')
    inputs = commissioning.digest(Path('flake.lock').read_text().strip())
    evidence = physical_reports([args.evidence / 'fresh.json', args.evidence / 'upgrade.json'], revision, release_version, inputs)
    restored = json.loads((args.evidence / 'recovery.json').read_text())
    duration = recovery_evidence(restored, revision, inputs)
    runs = json.loads(args.runs)
    if set(runs) != set(WORKFLOWS) or any(type(value) is not int or value <= 0 for value in runs.values()):
        raise ValueError('Supply one positive run ID for each required workflow')
    links = {}
    for key, run_id in runs.items():
        run = gh_json(f'repos/{args.repository}/actions/runs/{run_id}')
        pages = gh_json('--paginate', '--slurp', f'repos/{args.repository}/actions/runs/{run_id}/jobs?per_page=100')
        jobs = [job for page in pages for job in page['jobs']]
        links[key] = ci_run(key, run, jobs, revision, args.repository)
    output = args.output
    if output.exists(): raise ValueError('Release output must not already exist')
    output.mkdir(mode=0o700, parents=True)
    archive = output / ('nixlab-' + release_version + '.tar.gz')
    subprocess.run(['git', 'archive', '--format=tar.gz', '--prefix=nixlab-' + release_version + '/',
                    '--output=' + str(archive), revision], check=True)
    manifest = {'format': 1, 'version': release_version, 'revision': revision, 'inputsDigest': inputs,
                'created': int(time.time()), 'qualification': evidence, 'ci': links,
                'recoveryDurationSeconds': duration,
                'limits': 'Single-host beta, x86_64; two-reboot minimum. Ten reboots/fourteen days remain reported targets. No clustering/HA or universal hardware/application coverage.'}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (output / 'qualification.md').write_text(commissioning.matrix([args.evidence / 'fresh.json', args.evidence / 'upgrade.json'], revision))
    limitations = args.evidence / 'limitations.md'
    if not limitations.is_file() or limitations.stat().st_size > 64 * 1024 or not limitations.read_text().strip():
        raise ValueError('Supply reviewed compatibility, backup/restore limitations, measured reboot count and operation duration')
    (output / 'LIMITATIONS.md').write_bytes(limitations.read_bytes())
    (output / 'SHA256SUMS').write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n'
                                               for path in sorted(output.iterdir()) if path.is_file()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--runs', required=True)
    parser.add_argument('--repository', default='OmarDardery/nixlab')
    parser.add_argument('--output', type=Path, required=True)
    try:
        prepare(parser.parse_args())
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit('Release blocked: ' + str(error)) from None
