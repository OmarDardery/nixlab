"""Interpret scheduled backup status without assuming a daily schedule."""
import json
from pathlib import Path
import subprocess
import sys
import time

STATUS = Path('/var/lib/nixlab/backup-status.json')
SUCCESS = Path('/var/lib/nixlab/last-backup-success')


def evaluate(enabled, phase, active, result, last_trigger, last_success, now):
    if not enabled: return 'disabled'
    if active in ('active', 'activating', 'deactivating'): return 'running'
    if result not in ('', 'success') or phase == 'failed': return 'failed'
    if phase == 'complete-with-exclusions': return 'partial'
    if last_trigger and last_trigger > last_success and now >= last_trigger + 7200: return 'overdue'
    if not last_success: return 'never-run'
    return 'successful'


def properties(unit, fields):
    try:
        result = subprocess.run(['systemctl', 'show', unit, '--timestamp=unix', '--property=' + ','.join(fields)],
                                capture_output=True, text=True, timeout=10)
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    except (OSError, subprocess.TimeoutExpired): return {}


def status(enabled):
    if not enabled: return 'disabled'
    unit = properties('restic-backups-nixlab.service', ['ActiveState', 'Result'])
    timer = properties('restic-backups-nixlab.timer', ['LastTriggerUSec'])
    if not unit.get('ActiveState') or 'LastTriggerUSec' not in timer: return 'unknown'
    trigger = timer.get('LastTriggerUSec', '').lstrip('@')
    try: trigger = float(trigger)
    except ValueError: trigger = 0
    try: last = int(SUCCESS.read_text().strip())
    except (OSError, ValueError): last = 0
    try: phase = json.loads(STATUS.read_text()).get('phase', '')
    except (OSError, ValueError, AttributeError): phase = ''
    return evaluate(enabled, phase, unit.get('ActiveState', ''), unit.get('Result', ''), trigger, last, time.time())


if __name__ == '__main__':
    print('1' if status(True) == 'overdue' else '0')
