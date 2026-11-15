"""Disposable VM-only staged credentials and reboot assertions; no real secrets."""
from pathlib import Path
import sys
import time

import runtime as rt

SECRET = 'synthetic-staged-vm-credential'
EVIDENCE = 'synthetic-rollback-vm-credential'
STATES = ('confirmed', 'rolled-back', 'cancelled', 'superseded', 'ready', 'failed',
          'building', 'awaiting-confirmation')


def directory(index):
    return rt.STATE / 'jobs' / f'{index + 100:032x}'


def prepare():
    with rt.locked():
        for index, status in enumerate(STATES):
            path = directory(index)
            rt.write_json(path / 'job.json', {'id': path.name, 'status': status,
                                             'created': time.time() - rt.STAGED_SECRET_LIFETIME - 60})
            rt.stage_secrets(path, {'portainerPassword': SECRET})
            rt.atomic(path / 'recovery' / 'secret-portainerPassword', EVIDENCE)
            # Reproduce a crash after atomic link and before temporary unlink.
            (path / '.secrets.jsonabcdefgh').hardlink_to(path / 'secrets.json')
        rt.write_json(rt.STATE / 'pending.json', {'id': directory(7).name, 'deadline': time.time() + 86400})
        # An unexpired candidate remains resumable after reboot.
        path = directory(8)
        rt.write_json(path / 'job.json', {'id': path.name, 'status': 'ready', 'created': time.time()})
        rt.stage_secrets(path, {'portainerPassword': SECRET})


def verify():
    for index in range(9):
        path = directory(index)
        assert not list(path.glob('.secrets.json*'))
        if index < 7:
            assert not (path / 'secrets.json').exists(), 'Unexpected retained staging in fixture state: ' + str(index)
        else:
            assert rt.read_staged_secrets(path, ['portainerPassword']) == {'portainerPassword': SECRET}
        if index < 8:
            assert (path / 'recovery' / 'secret-portainerPassword').read_text() == EVIDENCE
        if index in (4, 5, 6):
            assert rt.read_job_record(path / 'job.json')['status'] == 'cancelled'
    assert rt.read_json(rt.STATE / 'pending.json')['id'] == directory(7).name
    print('Staged-secret reboot cleanup and recovery retention verified')


if __name__ == '__main__':
    {'prepare': prepare, 'verify': verify}[sys.argv[1]]()
