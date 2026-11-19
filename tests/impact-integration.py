"""Real local Incus API/ETag test, only inside the disposable foundation VM."""
import copy
import json
from pathlib import Path
import subprocess

import impact
import runtime as rt

before = json.loads(Path('/etc/nixlab-impact-fixture.json').read_text())
after = copy.deepcopy(before)
profile = next(p for p in after['preseed']['profiles'] if p['name'] == 'base' and p.get('project') == 'lab')
profile['config']['limits.cpu'] = '3'
directory = Path('/tmp/nixlab-impact-test')
directory.mkdir(mode=0o700)
(directory / 'system').symlink_to('/run/current-system')
rt.write_json(directory / 'job.json', {'status': 'ready'})
plan = impact.prepare(directory, before, after)
assert plan['runtimeState'] == 'observed', plan
assert not plan['blocked'], plan
change = next(c for c in plan['changes'] if c['identity'] == 'profiles:lab/base')
assert {'lab/native', 'lab/impact-peer'}.issubset(change['affected']), change
assert rt.read_json(directory / 'impact.json')['snapshot']['objects']['profiles:lab/base']['etag']

def set_cpu(value):
    subprocess.run(['incus', '--force-local', 'profile', 'set', 'base', 'limits.cpu=' + value, '--project', 'lab'], check=True)

try:
    set_cpu('4')
    try:
        impact.recheck(directory)
        raise AssertionError('An intervening native edit must invalidate review')
    except impact.ReviewRequired:
        pass
    assert rt.read_json(directory / 'job.json')['impactPlan']['blocked']
    try:
        impact.recheck(directory)
        raise AssertionError('A conflicting native edit must not pass on retry')
    except impact.ReviewRequired:
        raise
    except ValueError:
        pass
finally:
    set_cpu('2')

try:
    impact.recheck(directory)
    raise AssertionError('Restoring the object still requires a fresh review')
except impact.ReviewRequired:
    pass
assert not impact.recheck(directory)['blocked']
print('Real Incus impact planning passed: shared consumers, ETags, drift refusal and recovery.')
