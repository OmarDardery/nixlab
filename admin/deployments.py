"""Reviewed guest rollout journal. Host access confirmation never certifies guest health."""
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import runtime as rt
from locking import open_lock
from guest_deploy import DeploymentError, Transport, NAME, store_path

VERSION = 1
INCUS_LOCK = Path('/run/lock/nixlab-incus.lock')
ACTIVE = {'queued', 'running'}
BOUNDARY = 'OS generations only: guest disks, databases, application schemas and external systems are not rolled back.'


def active_system():
    return str(Path('/run/current-system').resolve())


def digest(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def order(targets):
    if not isinstance(targets, dict) or len(targets) > 16: raise ValueError('Declare at most 16 deployment targets')
    result, visiting = [], set()
    def visit(key):
        if key in visiting: raise ValueError('Guest deployment dependency cycle')
        if key in result: return
        if key not in targets: raise ValueError('Guest deployment dependency is not declared')
        visiting.add(key)
        for dependency in targets[key]['dependsOn']: visit(dependency)
        visiting.remove(key); result.append(key)
    for key in sorted(targets): visit(key)
    return result


def validate(manifest):
    if not isinstance(manifest, dict) or len(manifest) > 16: raise ValueError('Declare at most 16 deployment targets')
    targets = copy.deepcopy(manifest)
    identities = set()
    for key, spec in targets.items():
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', key): raise ValueError('Invalid deployment target key')
        if any(not isinstance(spec.get(k), str) or not NAME.fullmatch(spec[k]) for k in ('project', 'name')): raise ValueError('Invalid deployment guest identity')
        identity = spec['project'] + '/' + spec['name']
        if identity in identities: raise ValueError('Duplicate deployment guest identity')
        identities.add(identity)
        for field in ('system', 'probe'): store_path(spec[field])
        if spec.get('restartPolicy') not in ('defer', 'reboot'): raise ValueError('Unsupported guest restart policy')
        if not isinstance(spec.get('dependsOn'), list) or any(not isinstance(d, str) for d in spec['dependsOn']): raise ValueError('Invalid deployment dependencies')
        for field, maximum in (('maxAttempts', 5), ('healthTimeout', 300)):
            if type(spec.get(field)) is not int or not 1 <= spec[field] <= maximum: raise ValueError('Invalid deployment retry/health bound')
        if type(spec.get('rollbackOnFailure')) is not bool: raise ValueError('Invalid deployment rollback policy')
    order(targets)
    return targets


def observation(spec):
    try:
        return Transport(spec['project'], spec['name']).inspect(), None
    except DeploymentError as exc:
        return None, str(exc)


def public_targets(targets, observations):
    return [{'id': key, 'project': spec['project'], 'name': spec['name'], 'targetSystem': spec['system'],
             'currentSystem': (observations[key].get('current') or {}).get('profile'),
             'dependsOn': spec['dependsOn'], 'restartPolicy': spec['restartPolicy'],
             'maxAttempts': spec['maxAttempts'], 'healthTimeout': spec['healthTimeout'],
             'healthUnits': spec.get('healthUnits', []), 'httpChecks': spec.get('httpChecks', 0),
             'observation': observations[key].get('error') or 'Observed source-owned NixOS guest'}
            for key, spec in targets.items()]


def prepare(directory, manifest):
    targets = validate(manifest)
    if not targets: return
    observations = {key: dict(zip(('current', 'error'), observation(spec))) for key, spec in targets.items()}
    bundle = {'format': VERSION, 'targets': targets, 'observations': observations}
    rt.write_json(directory / 'deployment-plan.json', bundle)
    job = rt.read_json(directory / 'job.json')
    job.update(deploymentDigest=digest(bundle), guestPlan={'format': VERSION, 'targets': public_targets(targets, observations),
                                                        'order': order(targets), 'boundary': BOUNDARY})
    rt.write_json(directory / 'job.json', job)


def read_plan(directory):
    path = directory / 'deployment-plan.json'
    if not path.is_file() or path.stat().st_size > 1024 * 1024: raise ValueError('Build a new review with guest deployment targets')
    bundle = rt.read_json(path)
    if bundle.get('format') != VERSION or digest(bundle) != rt.read_json(directory / 'job.json').get('deploymentDigest'):
        raise ValueError('Guest deployment evidence changed; rebuild the review')
    validate(bundle['targets'])
    return bundle


def check_idle(state):
    active = rt.read_json(state / 'deployment-active.json')
    if active: raise ValueError('Finish or resume the active guest deployment before changing host configuration')


class Deployments:
    def __init__(self, root=None, state=None):
        self.root, self.state = Path(root or rt.ROOT), Path(state or rt.STATE)

    def directory(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id): raise ValueError('Invalid deployment transaction ID')
        directory = self.state / 'jobs' / job_id
        if not directory.is_dir(): raise ValueError('Unknown deployment transaction')
        return directory

    def host(self, directory):
        job = rt.read_json(directory / 'job.json')
        if job['status'] != 'confirmed' or rt.read_json(self.state / 'pending.json'):
            raise ValueError('Confirm host access before deploying guests')
        if not (directory / 'system').exists() or active_system() != str((directory / 'system').resolve()):
            raise ValueError('The reviewed host generation is no longer active; build a new deployment')
        if job.get('candidateDigest') != rt.digest_source(directory / 'source') or job.get('baseDigest') is None:
            raise ValueError('The reviewed deployment source changed')
        return job

    def status(self, job_id, private=False):
        directory = self.directory(job_id)
        record = rt.read_json(directory / 'deployment.json')
        if record and record['status'] in ACTIVE and time.time() - record['updatedAt'] > 30:
            unit = subprocess.run(['systemctl', 'is-active', '--quiet', 'nixlab-deploy-' + job_id], capture_output=True, timeout=10)
            if unit.returncode:
                record['status'] = 'interrupted'
                record['error'] = 'Worker stopped. Resume rechecks the guest identity and generations before continuing.'
        if record and not private:
            for state in record['targets'].values(): state.pop('precondition', None)
        return record

    def submit(self, job_id, resume=False, principal='root'):
        from recovery_guard import require_normal
        require_normal()
        if type(resume) is not bool: raise ValueError('Resume must be a boolean')
        with rt.locked(blocking=False):
            directory = self.directory(job_id)
            self.host(directory)
            from operations import check_config_idle
            check_config_idle(self.state)
            bundle = read_plan(directory)
            record = self.status(job_id, private=True)
            active = rt.read_json(self.state / 'deployment-active.json')
            if active and active['id'] != job_id: raise ValueError('Another guest deployment is active')
            if record and record['status'] in ACTIVE: raise ValueError('Guest deployment is already running')
            if record and record['status'] == 'abandoned': raise ValueError('Build a new review after abandoning a deployment')
            if record and not resume: raise ValueError('Resume the existing deployment instead of replacing its journal')
            if not record:
                if resume: raise ValueError('Start the reviewed guest deployment first')
                observations = {}
                for key, spec in bundle['targets'].items():
                    current, error = observation(spec)
                    reviewed = bundle['observations'][key]['current']
                    if current and (not reviewed
                            or any(current[k] != reviewed[k] for k in ('uuid', 'fingerprint'))
                            or current['profile'] not in (reviewed['profile'], spec['system'])
                            or current['active'] not in (reviewed['active'], spec['system'])):
                        error = 'Guest identity, configuration or OS generation differs from review; build a new review'; current = None
                    observations[key] = {'current': current, 'error': error}
                record = {'format': VERSION, 'id': job_id, 'hostSystem': str((directory / 'system').resolve()),
                          'planDigest': digest(bundle), 'createdAt': time.time(), 'principal': principal,
                          'boundary': BOUNDARY, 'targets': {key: {'phase': 'pending', 'attempts': 0,
                              'precondition': observations[key]['current'], 'error': observations[key]['error']}
                              for key in bundle['targets']}}
            record.update(status='queued', updatedAt=time.time(), error=None)
            rt.write_json(directory / 'deployment.json', record)
            rt.write_json(self.state / 'deployment-active.json', {'id': job_id})
            try:
                self.spawn(job_id)
            except (OSError, subprocess.SubprocessError):
                record.update(status='interrupted', error='Could not start deployment worker', updatedAt=time.time())
                rt.write_json(directory / 'deployment.json', record)
                rt.durable_unlink(self.state / 'deployment-active.json')
                raise ValueError('Could not start deployment worker; resume this journal') from None
            rt.audit('guest-deployment-requested', job=job_id, principal=principal, resume=resume)
            return {'id': job_id}

    def spawn(self, job_id):
        subprocess.run(['systemd-run', '--quiet', '--collect', '--no-block', '--unit=nixlab-deploy-' + job_id,
                    '--property=RuntimeMaxSec=10800', '--setenv=NIXLAB_ROOT=' + str(self.root),
                    '--setenv=NIXLAB_STATE=' + str(self.state), '--setenv=PATH=' + os.environ['PATH'],
                    sys.executable, str(rt.SOURCE / 'main.py'), 'deployment-job', job_id], check=True, capture_output=True, timeout=20)

    def abandon(self, job_id):
        with rt.locked(blocking=False):
            directory = self.directory(job_id)
            record = self.status(job_id, private=True)
            if not record or record['status'] in ACTIVE: raise ValueError('Only a stopped deployment can be abandoned')
            record.update(status='abandoned', error='Retries abandoned; guest generations and data were not changed')
            self.save(directory, record)
            if rt.read_json(self.state / 'deployment-active.json', {}).get('id') == job_id:
                rt.durable_unlink(self.state / 'deployment-active.json')
            rt.audit('guest-deployment-abandoned', job=job_id)
            return {'id': job_id}

    def save(self, directory, record):
        record['updatedAt'] = time.time()
        rt.write_json(directory / 'deployment.json', record)

    def phase(self, directory, record, key, phase, **values):
        record['targets'][key].update(phase=phase, **values)
        self.save(directory, record)

    def verify(self, transport, state, spec):
        current = transport.inspect()
        expected = state['precondition']
        if not expected: raise DeploymentError(state.get('error') or 'Guest was not eligible at deployment start; rebuild the review')
        if any(current[k] != expected[k] for k in ('uuid', 'fingerprint')):
            raise DeploymentError('Guest identity or configuration changed during deployment; inspect it and rebuild the review')
        allowed = {state.get('previousSystem', expected['profile']), spec['system']}
        if current['profile'] not in allowed or current['active'] not in allowed:
            raise DeploymentError('A native guest OS generation conflicts with the deployment; no automatic adoption')
        return current

    def health(self, transport, spec):
        deadline = time.monotonic() + spec['healthTimeout']
        while True:
            try:
                if transport.healthy(spec['probe'], timeout=max(1, min(10, deadline - time.monotonic()))): return
            except DeploymentError:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise DeploymentError('Guest health checks failed within the configured deadline')
            time.sleep(min(2, remaining))

    def rollback_target(self, directory, record, key, spec, transport):
        state = record['targets'][key]
        self.verify(transport, state, spec)
        self.phase(directory, record, key, 'rolling-back')
        transport.activate(state['previousSystem'])
        current = transport.inspect()
        if current['profile'] != state['previousSystem'] or current['active'] != state['previousSystem']:
            raise DeploymentError('Previous OS generation could not be restored')
        self.phase(directory, record, key, 'rolled-back', currentSystem=state['previousSystem'], rollbackRebootRequired=transport.reboot_required(current))

    def target(self, directory, record, key, spec):
        state = record['targets'][key]
        transport = Transport(spec['project'], spec['name'])
        current = self.verify(transport, state, spec)
        # A killed rollback must finish before another forward deployment attempt.
        if state['phase'] in ('rolling-back', 'rollback-failed'):
            self.rollback_target(directory, record, key, spec, transport)
            current = self.verify(transport, state, spec)
        # A deferred reboot or stopped worker must not lose this rollout's
        # rollback boundary when the next run already observes the target OS.
        mutated = state['phase'] in ('activating', 'activated', 'checking', 'rebooting') or (
            state.get('previousSystem') not in (None, spec['system'])
            and spec['system'] in (current['profile'], current['active']))
        if current['profile'] != spec['system'] or current['active'] != spec['system']:
            if state['attempts'] >= spec['maxAttempts']:
                if mutated and spec['rollbackOnFailure'] and state.get('previousSystem'):
                    try: self.rollback_target(directory, record, key, spec, transport)
                    except DeploymentError: self.phase(directory, record, key, 'rollback-failed')
                raise DeploymentError('Retry budget exhausted; inspect the guest and build a new reviewed deployment')
            previous = state.get('previousSystem', current['profile'])
            self.phase(directory, record, key, 'transferring', previousSystem=previous, targetSystem=spec['system'],
                       attempts=state['attempts'] + 1, error=None)
            transport.transfer(spec['system'], spec['probe'])
            self.phase(directory, record, key, 'transferred')
            self.verify(transport, state, spec)
            transport.retain(directory.name + '-' + key, previous, spec['system'])
            self.phase(directory, record, key, 'activating')
            mutated = True
            try:
                transport.activate(spec['system'])
            except DeploymentError:
                if spec['rollbackOnFailure']:
                    try: self.rollback_target(directory, record, key, spec, transport)
                    except DeploymentError: self.phase(directory, record, key, 'rollback-failed')
                raise
            self.phase(directory, record, key, 'activated')
        else:
            # An already-current guest still needs the reviewed probe imported.
            if state['phase'] in ('pending', 'failed', 'blocked', 'transferring-probe'):
                if state['attempts'] >= spec['maxAttempts']: raise DeploymentError('Retry budget exhausted while importing the guest health probe')
                self.phase(directory, record, key, 'transferring-probe', attempts=state['attempts'] + 1,
                           previousSystem=state.get('previousSystem', current['profile']), targetSystem=spec['system'])
                transport.transfer(spec['probe'])
                self.phase(directory, record, key, 'transferred')
        try:
            current = self.verify(transport, state, spec)
            if current['active'] != spec['system']: raise DeploymentError('Guest activation did not reach its target generation')
            if transport.reboot_required(current):
                if spec['restartPolicy'] == 'defer':
                    self.phase(directory, record, key, 'reboot-pending', targetSystem=spec['system'], currentSystem=spec['system'], error='Reboot this guest in Incus, then resume to run health checks')
                    return
                self.phase(directory, record, key, 'rebooting')
                transport.reboot()
                deadline = time.monotonic() + 120
                while True:
                    try:
                        current = self.verify(transport, state, spec)
                        if not transport.reboot_required(current): break
                    except DeploymentError:
                        pass
                    if time.monotonic() >= deadline: raise DeploymentError('Guest did not return on the target boot configuration within the reboot deadline')
                    time.sleep(2)
            self.phase(directory, record, key, 'checking', targetSystem=spec['system'])
            self.health(transport, spec)
            self.phase(directory, record, key, 'healthy', currentSystem=spec['system'], checkedAt=time.time(), error=None)
        except DeploymentError:
            if mutated and spec['rollbackOnFailure']:
                try: self.rollback_target(directory, record, key, spec, transport)
                except DeploymentError:
                    self.phase(directory, record, key, 'rollback-failed')
            raise

    def run(self, job_id):
        from recovery_guard import require_normal
        require_normal()
        directory = self.directory(job_id)
        with rt.locked(), open_lock(INCUS_LOCK) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            record = rt.read_json(directory / 'deployment.json')
            try:
                self.host(directory)
                bundle = read_plan(directory)
                if record['status'] not in ACTIVE: raise DeploymentError('Deployment was not queued')
                if record['planDigest'] != digest(bundle): raise DeploymentError('Deployment plan changed')
                record['status'] = 'running'; self.save(directory, record)
                for key in order(bundle['targets']):
                    spec = bundle['targets'][key]
                    if any(record['targets'][d]['phase'] != 'healthy' for d in spec['dependsOn']):
                        self.phase(directory, record, key, 'blocked', error='A dependency is not healthy; resume after resolving it')
                        continue
                    try: self.target(directory, record, key, spec)
                    except DeploymentError as exc:
                        phase = record['targets'][key]['phase']
                        self.phase(directory, record, key, phase if phase in ('rolled-back', 'rollback-failed') else 'failed', error=str(exc))
                record['status'] = 'healthy' if all(t['phase'] == 'healthy' for t in record['targets'].values()) else 'partial'
                record['finishedAt'] = time.time(); self.save(directory, record)
            except (ValueError, OSError, subprocess.SubprocessError):
                record.update(status='interrupted', error='Deployment interrupted or preconditions changed; inspect host/guest state before resuming')
                self.save(directory, record)
                raise
            finally:
                active = rt.read_json(self.state / 'deployment-active.json')
                if active and active['id'] == job_id: rt.durable_unlink(self.state / 'deployment-active.json')
            rt.audit('guest-deployment-finished', job=job_id, status=record['status'])
            return record
