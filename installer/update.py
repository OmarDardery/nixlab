"""One-command platform updates through the existing durable transaction engine."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'admin'))
import direct
import diagnostics
import runtime as rt
from controller import Manager

PRINCIPAL = 'root-update-command'
ACTIVE = {'building', 'ready', 'applying', 'awaiting-confirmation', 'rolling-back'}


def main_revision():
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'ls-remote', '--refs',
                             'https://github.com/OmarDardery/nixlab.git', 'refs/heads/main'],
                            capture_output=True, text=True, timeout=30, check=True)
    fields = result.stdout.split()
    if len(fields) != 2 or not re.fullmatch(r'[0-9a-f]{40}', fields[0]) or fields[1] != 'refs/heads/main':
        raise ValueError('Could not resolve NixLab main to one exact revision')
    return fields[0]


def ask(message):
    return input(message + ' [y/N] ').strip().lower() in ('y', 'yes')


def run(manager):
    state = manager.status()
    record = state.get('job') or {}
    if record.get('status') in ACTIVE:
        if record.get('principal') != PRINCIPAL or not re.fullmatch(r'[0-9a-f]{40}', record.get('platformRevision') or ''):
            raise ValueError('Another configuration transaction is active; finish it in the host console first')
        print('Resuming update to ' + record['platformRevision'], flush=True)
    else:
        if state.get('pending'):
            raise ValueError('Activation recovery is pending; resolve it in the host console first')
        if not state.get('setupComplete'):
            raise ValueError('Finish initial NixLab setup before running #update')
        revision = main_revision()
        print('NixLab main: ' + revision, flush=True)
        if state.get('release', {}).get('revision') == revision:
            print('Platform source is already current.')
            if state.get('rebootRequired'): print('A kernel reboot is pending; reboot separately when ready.')
            return
        record = manager.review({}, revision=revision, principal=PRINCIPAL)
        print('Building update; transaction ' + record['id'], flush=True)
        record = manager.job(record['id'])
    job_id = record['id']
    if record['status'] == 'rolling-back':
        direct.wait_for(manager, job_id, {'rolled-back'})
        raise ValueError('Previous update rolled back; rerun #update to try again')
    if record['status'] == 'building':
        record = direct.wait_for(manager, job_id, {'ready'})
    if record['status'] == 'ready':
        plan = record.get('impactPlan')
        if not isinstance(plan, dict) or plan.get('blocked') is not False:
            raise ValueError('Update has an unavailable or blocked impact plan; inspect Changes in the host console')
        print('Proposed effects:', flush=True)
        for change in plan.get('changes', []):
            print('  ' + str(change.get('message', 'Review change')), flush=True)
        print('Platform files changed: ' + str(len(record.get('platformChanges', []))) +
              '. Full review and dry-activation output: host console → Changes.', flush=True)
        print('Source code, pinned packages and services may change. Machine settings/modules are retained.', flush=True)
        if not ask('Activate this built update?'):
            manager.cancel(job_id)
            raise ValueError('Update cancelled before activation')
        manager.apply(job_id)
        record = direct.wait_for(manager, job_id, {'awaiting-confirmation'})
    elif record['status'] == 'applying':
        record = direct.wait_for(manager, job_id, {'awaiting-confirmation'})
    if record['status'] != 'awaiting-confirmation':
        raise ValueError('Unexpected update state; inspect the host console')
    try:
        diagnostics.wait_ready(record.get('effectiveSettings') or manager.effective_settings())
        print('Health checks passed. Open a second SSH connection and check the web console and workloads.', flush=True)
        deadline = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(record['deadline']))
        print('Confirmation deadline: ' + deadline, flush=True)
        if not ask('Access and workloads verified; keep this update?'):
            raise ValueError('Update was not confirmed')
        manager.confirm(job_id)
    except (ValueError, OSError, subprocess.SubprocessError):
        # Roll back promptly on failed health/user rejection. Disconnection and
        # Ctrl-C still leave the existing independent rollback guard in charge.
        try:
            if manager.job(job_id)['status'] == 'awaiting-confirmation':
                manager.rollback(job_id)
                direct.wait_for(manager, job_id, {'rolled-back'})
        except (ValueError, OSError, subprocess.SubprocessError):
            print('Rollback needs attention; inspect the host console before retrying.', file=sys.stderr)
        raise
    print('NixLab update confirmed.', flush=True)
    if manager.status().get('rebootRequired'):
        print('A kernel reboot is pending; reboot separately when ready.', flush=True)


def main():
    argparse.ArgumentParser(description='Update installed NixLab from main using guarded build/activation.').parse_args()
    if os.geteuid() != 0 or not Path('/etc/NIXOS').is_file():
        raise ValueError('Run on the installed NixOS host with administrator privileges')
    if not (rt.ROOT / 'flake.nix').is_file() or not rt.machine_path(rt.ROOT, 'settings').is_file():
        raise ValueError('No installed NixLab configuration found; use #install for initial installation')
    if not sys.stdin.isatty():
        raise ValueError('#update requires an interactive terminal for activation and access confirmation')
    run(Manager())


if __name__ == '__main__':
    os.umask(0o027)
    try:
        main()
    except KeyboardInterrupt:
        sys.exit('Disconnected/interrupted: rerun the same #update command to resume. Unconfirmed activation retains automatic rollback.')
    except (ValueError, OSError, EOFError, subprocess.SubprocessError) as error:
        sys.exit('Update stopped: ' + str(error))
