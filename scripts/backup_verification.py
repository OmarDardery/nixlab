"""Run the existing restic check without publishing repository/credential output."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time

import backup_health
import diagnostics
import runtime as rt

POLICY = Path('/etc/nixlab/backup-verification.json')
STATE = Path('/var/lib/nixlab/backup-verification.json')
MODES = ('metadata', 'full')


def repository_fingerprint(repository):
    # Root-only comparison evidence, never a public field or a Nix-store value.
    return hashlib.sha256(repository.encode()).hexdigest() if isinstance(repository, str) and repository else None


def timestamp(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None
    except OverflowError:
        return None


def clock_observation():
    observed = diagnostics.clock_status()
    return {'state': observed['state'], 'evidenceWarning': observed['evidenceWarning']}


def last_success(record, latest):
    item = record.get('lastSuccess')
    if (isinstance(item, dict) and (at := timestamp(item.get('at'))) is not None
            and at <= latest and item.get('mode') in MODES and isinstance(item.get('clock'), dict)):
        clock = item['clock']
        if clock.get('state') in ('synchronized', 'plausible-but-unsynchronized', 'invalid-clock') and type(clock.get('evidenceWarning')) is bool:
            return {'at': at, 'mode': item['mode'],
                    'clock': {'state': clock['state'],
                              'evidenceWarning': clock['evidenceWarning'] or clock['state'] != 'synchronized'}}
    return None


def read_document(path):
    try:
        with Path(path).open() as stream:
            text = stream.read(16385)
        if len(text) > 16384:
            return None
        document = json.loads(text)
        return document if isinstance(document, dict) else None
    except (OSError, ValueError):
        return None


def read_record(path):
    record = read_document(path)
    return record if record and type(record.get('version')) is int and record['version'] == 1 else None


def check(mode, state=None):
    if mode not in MODES:
        raise ValueError('Unsupported repository verification mode')
    state = Path(state or STATE)
    repository = repository_fingerprint(os.environ.get('RESTIC_REPOSITORY'))
    if repository is None:
        raise ValueError('Repository target is unavailable')
    previous = read_record(state) or {}
    if previous.get('repositoryFingerprint') != repository:
        previous = {}
    started = time.time()
    record = {'version': 1, 'phase': 'running', 'mode': mode, 'startedAt': started,
              'finishedAt': None, 'lastSuccess': last_success(previous, started), 'repositoryFingerprint': repository}
    rt.write_json(state, record)
    args = ['restic', '--no-cache', 'check'] + (['--read-data'] if mode == 'full' else [])
    try:
        # Repository errors can include URLs, server responses or credentials.
        # Publish only a result code; never copy restic output into host logs.
        result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=43140)
        code, failure = result.returncode, None if result.returncode == 0 else 'restic-failed'
    except subprocess.TimeoutExpired:
        code, failure = None, 'timeout'
    except OSError:
        code, failure = None, 'unavailable'
    record.update(phase='successful' if failure is None else 'failed', finishedAt=time.time(),
                  exitCode=code, failure=failure, clock=clock_observation())
    if failure is None:
        record['lastSuccess'] = {'at': record['finishedAt'], 'mode': mode, 'clock': record['clock']}
    rt.write_json(state, record)
    return 0 if failure is None else 1


def status(backup_enabled, policy=None, state=None, *, repository=None):
    """Single read-only projection for portal/recovery; malformed evidence is unknown."""
    if not backup_enabled:
        return {'state': 'disabled'}
    try:
        policy = read_document(policy or POLICY)
        if (not isinstance(policy, dict) or type(policy.get('version')) is not int or policy['version'] != 1
                or type(policy.get('enable')) is not bool or policy.get('mode') not in MODES
                or not isinstance(policy.get('schedule'), str)):
            return {'state': 'unknown'}
    except (OSError, ValueError):
        return {'state': 'unknown'}
    result = {'state': 'unknown', 'scheduled': policy['enable'],
              'mode': policy['mode'], 'schedule': policy['schedule']}
    unit = backup_health.properties('nixlab-backup-check.service', ['LoadState', 'ActiveState', 'Result'])
    if (unit.get('LoadState') != 'loaded' or not isinstance(unit.get('Result'), str)
            or unit.get('ActiveState') not in ('active', 'activating', 'deactivating', 'inactive', 'failed')):
        return result
    record = read_record(state or STATE)
    now = time.time()
    if record is not None and (repository_fingerprint(repository) is None
                              or record.get('repositoryFingerprint') != repository_fingerprint(repository)):
        return dict(result, reason='repository-unverified')
    if unit['ActiveState'] in ('active', 'activating', 'deactivating'):
        result['state'] = 'running'
    elif record is None:
        try:
            Path(state or STATE).lstat()
        except FileNotFoundError:
            result['state'] = 'never-run' if policy['enable'] else 'disabled'
        except OSError:
            pass
    elif record.get('phase') == 'running':
        result['state'] = 'interrupted'
    elif record.get('phase') in ('successful', 'failed') and record.get('mode') in MODES:
        started = timestamp(record.get('startedAt'))
        finished = timestamp(record.get('finishedAt'))
        if started is None or finished is None or not started <= finished <= now:
            return result
        success = last_success(record, finished if record['phase'] == 'successful' else started)
        if record['phase'] == 'successful' and (not success or success['at'] != finished or success['mode'] != record['mode']):
            return result
        result.update(state=record['phase'], lastMode=record['mode'], finishedAt=finished, lastSuccess=success)
        if result['lastSuccess']:
            result['timeWarning'] = result['lastSuccess']['clock']['evidenceWarning']
    if unit.get('Result') not in ('', 'success') and result['state'] not in ('running', 'interrupted'):
        result['state'] = 'failed'
    if policy['enable']:
        timer = backup_health.properties('nixlab-backup-check.timer', ['LoadState', 'ActiveState', 'LastTriggerUSec'])
        if timer.get('LoadState') != 'loaded' or timer.get('ActiveState') != 'active' or 'LastTriggerUSec' not in timer:
            if result['state'] not in ('running', 'failed', 'interrupted'):
                result['state'] = 'unknown'
        elif result['state'] in ('successful', 'never-run'):
            try:
                trigger = float(timer['LastTriggerUSec'].lstrip('@')) if timer['LastTriggerUSec'] not in ('', 'n/a') else 0
            except (ValueError, AttributeError):
                result['state'] = 'unknown'
            else:
                if not math.isfinite(trigger) or not 0 <= trigger <= now:
                    result['state'] = 'unknown'
                elif trigger > (timestamp((record or {}).get('finishedAt')) or 0):
                    result['state'] = 'overdue' if now >= trigger + 7200 else 'queued'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=sorted(MODES), required=True)
    args = parser.parse_args(argv)
    try:
        result = check(args.mode)
    except (OSError, ValueError):
        print('Repository verification evidence could not be recorded; integrity remains unverified.')
        return 1
    if result:
        print('Repository verification failed; check connectivity, credentials, repository locks and repository integrity from the host console.')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
