"""Real VM activation/reboot recovery of a prebuilt bootstrap closure.

The Nix evaluation test separately qualifies the actual bootstrap composition.
Here the candidate is the VM's current prebuilt closure so no registry/network
fetch, nested guest or physical hardware assumption enters the recovery test.
"""
import grp
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from controller import Manager
import impact
import runtime as rt
import worker

JOB = 'b' * 32


def initialize():
    if (rt.ROOT / '.nixlab-files.json').exists():
        return  # A reboot must not overwrite the pending transaction's state.
    source = Path(os.environ['NIXLAB_TEST_SITE_SOURCE'])
    names = ['site.nix', 'site-defaults.nix', 'lib/machine.nix', 'lib/settings-migrations.nix']
    for name in names:
        rt.atomic(rt.ROOT / name, (source / name).read_bytes())
    settings = json.loads(Path('/etc/nixlab-test-settings.json').read_text())
    rt.write_json(rt.ROOT / 'machine/format.json', {'version': 1})
    rt.write_json(rt.ROOT / 'machine/settings.json', settings)
    rt.write_json(rt.ROOT / 'machine/installation.json', {
        'interface': settings['management']['interface'],
        'trustedCIDRs': settings['management']['trustedCIDRs']})
    rt.write_json(rt.ROOT / '.nixlab-files.json', names + [
        'machine/format.json', 'machine/settings.json', 'machine/installation.json'])
    rt.STATE.mkdir(parents=True, mode=0o710, exist_ok=True)
    os.chown(rt.STATE, 0, grp.getgrnam('nixlab-ui').gr_gid)
    rt.STATE.chmod(0o710)
    rt.write_json(rt.STATE / 'auth.json', rt.password_record('vm-bootstrap-test-password'),
                  mode=0o640, group='nixlab-ui')
    rt.issue_tls(settings['management']['address'], settings['hostName'])


def activate():
    settings = rt.read_json(Path('/etc/nixlab/effective-settings.json'))
    assert not settings['ready'] and settings['guests']['cidr'] == ''
    directory = rt.STATE / 'jobs' / JOB
    directory.mkdir(parents=True, mode=0o700)
    rt.snapshot(directory / 'source')
    system = Path('/run/current-system').resolve()
    (directory / 'system').symlink_to(system)
    rt.write_json(directory / 'job.json', {
        'id': JOB, 'hostRole': 'bootstrap', 'principal': 'root-wizard-install',
        'status': 'applying', 'created': time.time(), 'appliedAt': time.time(),
        'settings': settings, 'effectiveSettings': settings, 'updateInputs': False,
        'baseDigest': rt.digest_source(), 'candidateDigest': rt.digest_source(directory / 'source')})
    rt.write_json(directory / 'secrets.json', {})
    rt.write_json(rt.STATE / 'active.json', {'id': JOB})
    with rt.locked():
        impact.prepare(directory, impact.baseline(settings), {
            'settings': settings, 'disks': {}, 'preseed': {}, 'instances': [], 'deployments': {}})
        worker.apply(directory)
    assert rt.read_json(directory / 'job.json')['status'] == 'awaiting-confirmation'
    assert rt.read_json(rt.STATE / 'pending.json')['id'] == JOB
    assert (directory / 'recovery/manifest.json').is_file()
    # Force recovery to happen on the next boot, not a lucky pre-reboot tick.
    subprocess.run(['systemctl', 'stop', 'nixlab-rollback-guard.timer', 'nixlab-rollback-guard.service'],
                   check=True, timeout=30)
    # Deliberately leave access unconfirmed, then simulate an interrupted intent
    # publication. Recovery must restore this from its existing durable manifest.
    with rt.locked():
        rt.write_json(rt.machine_path(rt.ROOT, 'settings'), dict(settings, hostName='unconfirmed-intent'))
        pending = rt.read_json(rt.STATE / 'pending.json')
        pending['deadline'] = 0
        rt.write_json(rt.STATE / 'pending.json', pending)


def recovered():
    directory = rt.STATE / 'jobs' / JOB
    assert rt.read_json(directory / 'job.json')['status'] == 'rolled-back'
    assert not (rt.STATE / 'pending.json').exists()
    assert not (directory / 'secrets.json').exists()
    assert rt.read_json(rt.machine_path(rt.ROOT, 'settings'))['hostName'] == 'bootstrap-test'
    assert not rt.read_json(rt.machine_path(rt.ROOT, 'settings'))['ready']
    assert Manager().settings()['guests']['cidr'] == ''


{'initialize': initialize, 'activate': activate, 'recovered': recovered}[sys.argv[1]]()
