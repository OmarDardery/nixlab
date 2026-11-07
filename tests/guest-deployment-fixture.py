"""Runs only in a disposable Incus integration VM; exercises the actual transport."""
import copy
import json
from pathlib import Path
import sys

import deployments as d
import runtime as rt
from guest_deploy import Transport

state = rt.STATE
manager = d.Deployments(state / 'source', state)
manifest = rt.read_json(Path('/etc/nixlab-test-deployments.json'))
paths = rt.read_json(Path('/etc/nixlab-test-deployment-paths.json'))
main_id, failed_id = 'b' * 32, 'c' * 32


def create(job_id, targets):
    directory = state / 'jobs' / job_id
    directory.mkdir(parents=True)
    source = directory / 'source'; source.mkdir()
    rt.write_json(source / '.nixlab-files.json', [])
    (directory / 'system').symlink_to('/run/current-system')
    rt.write_json(directory / 'job.json', {'id': job_id, 'status': 'confirmed', 'baseDigest': 'fixture', 'candidateDigest': rt.digest_source(source)})
    rt.write_json(state / 'active.json', {'id': job_id})
    d.prepare(directory, targets)
    manager.spawn = lambda _: None  # The VM harness controls the actual worker process.
    manager.submit(job_id)
    return directory


action = sys.argv[1]
if action == 'failure':
    bad = copy.deepcopy(manifest); bad['primary']['probe'] = paths['failedProbe']; bad['primary']['healthTimeout'] = 1
    directory = create(failed_id, bad)
    result = manager.run(failed_id)
    assert result['targets']['primary']['phase'] == 'rolled-back', result
    assert 'health checks failed' in result['targets']['primary']['error'], result
    assert result['targets']['worker']['phase'] == 'blocked', result
    for name in ('primary', 'worker'):
        assert Transport('lab', name).inspect()['profile'] == paths['previous']
elif action == 'queue':
    create(main_id, manifest)
elif action == 'run':
    manager.run(main_id)
elif action == 'transferring':
    assert manager.status(main_id)['targets']['primary']['phase'] == 'transferring'
    importing = False
    for process in Path('/proc').iterdir():
        if not process.name.isdigit(): continue
        try: args = (process / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
        except (FileNotFoundError, PermissionError, ProcessLookupError): continue
        if args[:4] == [b'incus', b'--force-local', b'exec', b'primary'] and args[-2:] == [b'nix-store', b'--import']:
            importing = True; break
    assert importing, 'Wait for the actual Incus import receiver before killing its worker'
elif action == 'resume':
    manager.spawn = lambda _: None
    manager.submit(main_id, resume=True)
    assert manager.run(main_id)['status'] == 'healthy'
elif action == 'verify':
    report = manager.status(main_id)
    assert report['status'] == 'healthy', report
    assert report['targets']['primary']['attempts'] == 2, report
    for name in ('primary', 'worker'):
        transport = Transport('lab', name)
        assert transport.inspect()['profile'] == manifest[name]['system']
        assert transport.execute('cat', '/etc/nixlab-deploy-version').stdout.strip() == 'v2'
        assert transport.execute('cat', '/var/lib/nixlab/preserved').stdout.strip() == 'original-data'
    print('Real NixOS guests updated in place; health rollback, dependency failure, interrupted transfer and controller reboot verified.')
else:
    raise ValueError('Unknown disposable fixture action')
