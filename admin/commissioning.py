"""Bounded commissioning probes and revision-bound evidence, never a test executor.

Routine collection is read-only apart from its own root-only report. Disruptive
procedures are performed separately and recorded as operator attestations.
"""
import argparse
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time

import runtime as rt
import upgrades
from locking import open_lock

VERSION = 1
MAX_AGE = 30 * 86400
MAX_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 400
STATUSES = {'passed', 'failed', 'skipped', 'unsupported', 'unknown'}
SCOPES = {'physical-host', 'virtual-machine', 'container', 'unknown'}
TYPES = {'automated-probe', 'manual-attestation'}
CHECKS = {
    'management': 'Local management endpoints and service health',
    'kvm': 'Usable KVM API',
    'container': 'Running unprivileged Incus container',
    'apps-vm': 'Real applications VM with agent and Docker',
    'apps-auth': 'Authenticated Portainer access',
    'install-resume': 'Installation or upgrade and interrupted retry',
    'management-auth': 'Authenticated remote SSH, Cockpit and Incus access',
    'reboots': 'Healthy management across two deliberate reboots',
    'network-recovery': 'Actual network loss and confirmation rollback',
    'offhost-restore': 'Independent off-host restore with verified application data',
    'hardware-reconnect': 'Configured physical device disconnect/reconnect',
}
AUTOMATIC = {'management', 'kvm', 'container', 'apps-vm', 'apps-auth'}
MANUAL = set(CHECKS) - AUTOMATIC
CONTEXT_FIELDS = {'revision', 'version', 'sourceDigest', 'inputsDigest', 'settingsDigest',
                  'system', 'kernel', 'incus', 'hardwareDigest', 'scope', 'architecture',
                  'apps', 'hardwareDevices', 'mode', 'environment'}
ENVIRONMENTS = {'installed-host', 'automated-fixture', 'nested-integration'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_text(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return ''


def command(args):
    """Fixed argv only. Output/errors are never included in exported evidence."""
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode or len(result.stdout) > MAX_BYTES:
        raise ValueError('Probe unavailable')
    return result.stdout.strip()


def scope():
    try:
        if command(['systemd-detect-virt', '--container', '--quiet']) == '':
            return 'container'
    except (ValueError, OSError, subprocess.SubprocessError):
        pass
    try:
        result = subprocess.run(['systemd-detect-virt'], capture_output=True, text=True, timeout=5)
        if result.returncode == 1 and result.stdout.strip() == 'none':
            return 'physical-host'
        if result.returncode == 0 and result.stdout.strip():
            return 'virtual-machine'
    except (OSError, subprocess.SubprocessError):
        pass
    return 'unknown'


def context(settings, mode, environment='installed-host'):
    release = upgrades.metadata(rt.ROOT)
    # Hash identifiers/configuration locally. Export no serials, addresses,
    # machine-id, device paths, usernames, settings or command output.
    hardware = [read_text('/etc/machine-id'), platform.machine(),
                read_text('/sys/class/dmi/id/sys_vendor'), read_text('/sys/class/dmi/id/product_name')]
    for directory in ('/sys/class/net', '/sys/class/block'):
        for device in sorted(Path(directory).glob('*')):
            # Exclude transient guest veths, bridges, loop devices and partitions;
            # otherwise an ordinary reboot would invalidate its own evidence.
            if not (device / 'device').exists(): continue
            hardware.append([device.name, read_text(device / 'device/vendor'),
                             read_text(device / 'device/device'), read_text(device / 'size')])
    try:
        versions = re.findall(r'(?:Client|Server) version:\s*([0-9][a-zA-Z0-9.+-]*)',
                              command(['incus', '--force-local', 'version']))
        incus = '_'.join(versions) if len(versions) == 2 else 'unknown'
    except (ValueError, OSError, subprocess.SubprocessError):
        incus = 'unknown'
    revision = release.get('revision', '')
    return {
        'revision': revision if re.fullmatch(r'[0-9a-f]{40}', revision) else 'unknown',
        'version': release.get('version', 'unknown'),
        'sourceDigest': rt.digest_source(rt.ROOT),
        'inputsDigest': digest(read_text(rt.ROOT / 'flake.lock')),
        'settingsDigest': digest(settings),
        'system': str(Path('/run/current-system').resolve()) if Path('/run/current-system').exists() else 'unknown',
        'kernel': platform.release(), 'incus': incus,
        'hardwareDigest': digest(hardware), 'scope': scope(), 'architecture': platform.machine(),
        'apps': bool(settings.get('apps', {}).get('enable')),
        'hardwareDevices': bool(settings.get('hardware', {}).get('serialAliases')),
        'mode': mode, 'environment': environment,
    }


def boot_id():
    value = read_text('/proc/sys/kernel/random/boot_id')
    return digest(value) if value else 'unknown'


def validate(report):
    """Strict schema: a downloaded report cannot smuggle extra fields into exports."""
    if not isinstance(report, dict) or set(report) != {'format', 'context', 'evidence'} or type(report['format']) is not int or report['format'] != VERSION:
        raise ValueError('Unsupported commissioning report')
    ctx = report['context']
    if not isinstance(ctx, dict) or set(ctx) != CONTEXT_FIELDS:
        raise ValueError('Invalid commissioning context')
    for key in ('apps', 'hardwareDevices'):
        if type(ctx[key]) is not bool: raise ValueError('Invalid capability')
    for key in CONTEXT_FIELDS - {'apps', 'hardwareDevices'}:
        if not isinstance(ctx[key], str) or not re.fullmatch(r'[a-zA-Z0-9/_.:+-]{1,256}', ctx[key]):
            raise ValueError('Invalid context value')
    if ctx['scope'] not in SCOPES or ctx['mode'] not in ('fresh', 'upgrade') or ctx['environment'] not in ENVIRONMENTS:
        raise ValueError('Invalid commissioning scope or mode')
    for key in ('sourceDigest', 'inputsDigest', 'settingsDigest', 'hardwareDigest'):
        if not re.fullmatch(r'[0-9a-f]{64}', ctx[key]): raise ValueError('Invalid context digest')
    if ctx['revision'] != 'unknown' and not re.fullmatch(r'[0-9a-f]{40}', ctx['revision']):
        raise ValueError('Invalid source revision')
    if not isinstance(report['evidence'], list) or len(report['evidence']) > MAX_RECORDS:
        raise ValueError('Too many evidence records; start a new report')
    for item in report['evidence']:
        if not isinstance(item, dict) or set(item) != {'check', 'status', 'type', 'contextDigest', 'time', 'boot', 'procedure'}:
            raise ValueError('Invalid evidence record')
        if any(not isinstance(item[key], str) for key in set(item) - {'time'}):
            raise ValueError('Invalid evidence value')
        if item['check'] not in CHECKS or item['status'] not in STATUSES or item['type'] not in TYPES:
            raise ValueError('Invalid check, status or evidence type')
        if item['type'] == 'automated-probe' and item['check'] not in AUTOMATIC:
            raise ValueError('Manual procedure cannot be an automated probe')
        if item['type'] == 'manual-attestation' and item['check'] not in MANUAL:
            raise ValueError('Probe results must be collected, not attested')
        if type(item['time']) is not int or item['time'] < 0:
            raise ValueError('Invalid evidence timestamp')
        for key in ('contextDigest', 'boot'):
            if not re.fullmatch(r'[0-9a-f]{64}|unknown', item[key]): raise ValueError('Invalid evidence fingerprint')
        if item['procedure'] != ('commissioning-v1' if item['type'] == 'manual-attestation' else 'probe-v1'):
            raise ValueError('Unsupported evidence procedure')
    return report


def record(report, ctx, check, status, evidence_type, now=None, boot=None):
    report['context'] = ctx
    report['evidence'].append({'check': check, 'status': status, 'type': evidence_type,
                               'contextDigest': digest(ctx), 'time': int(time.time() if now is None else now),
                               'boot': boot or boot_id(),
                               'procedure': 'commissioning-v1' if evidence_type == 'manual-attestation' else 'probe-v1'})
    validate(report)


def collect(report, ctx, settings):
    import diagnostics
    def management():
        result = diagnostics.probe(settings)  # unlike doctor(), does not save diagnostics
        if not result['ok']: return False
        units = ['incus.service', 'nixlab-instances.service', 'nixlab-management-health.service']
        if ctx['apps']: units.append('nixlab-apps-onboarding.service')
        return command(['systemctl', 'is-active', *units]).splitlines() == ['active'] * len(units)

    def kvm():
        with open('/dev/kvm', 'r+b', buffering=0) as device:
            return fcntl.ioctl(device, 0xAE00, 0) == 12

    def container():
        guests = json.loads(command(['incus', '--force-local', 'list', '--all-projects', '--format=json']))
        return any(g.get('type') == 'container' and g.get('status') == 'Running'
                   and g.get('expanded_config', g.get('config', {})).get('security.privileged', 'false') != 'true'
                   for g in guests)

    def apps_vm():
        guest = json.loads(command(['incus', '--force-local', 'query', '/1.0/instances/apps?project=lab']))
        if guest.get('type') != 'virtual-machine' or guest.get('status') != 'Running': return False
        return command(['incus', '--force-local', 'exec', 'apps', '--project', 'lab', '--',
                        'systemctl', 'is-active', 'docker.service']) == 'active'

    def apps_auth():
        import portainer
        # Use existing credentials only; never reset/enroll anything during collection.
        return portainer.credentials(settings) is not None

    probes = {'management': management, 'kvm': kvm, 'container': container,
              'apps-vm': apps_vm, 'apps-auth': apps_auth}
    for check, probe in probes.items():
        if check.startswith('apps-') and not ctx['apps']:
            status = 'skipped'
        else:
            try:
                status = 'passed' if probe() else 'failed'
            except FileNotFoundError:
                status = 'unsupported'
            except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, http.client.HTTPException):
                status = 'unknown'
        record(report, ctx, check, status, 'automated-probe')
    return report


def summarize(report, current=None, now=None, revision=None):
    validate(report)
    now = int(time.time() if now is None else now)
    ctx = current or report['context']
    fingerprint = digest(ctx)
    rows = []
    for check, title in CHECKS.items():
        required = not (check.startswith('apps-') and not ctx['apps']) and not (check == 'hardware-reconnect' and not ctx['hardwareDevices'])
        items = [e for e in report['evidence'] if e['check'] == check]
        # The latest observation wins, including failed/unknown observations.
        latest = items[-1] if items else None
        stale = bool(latest and (latest['contextDigest'] != fingerprint or not 0 <= now - latest['time'] <= MAX_AGE))
        status = 'stale' if stale else latest['status'] if latest else 'unknown'
        if not required and latest is None: status = 'skipped'
        if check == 'reboots' and status == 'passed':
            # Three independently observed healthy boots = initial boot + two reboots.
            boots = {e['boot'] for e in report['evidence'] if e['check'] == 'management'
                     and e['status'] == 'passed' and e['contextDigest'] == fingerprint
                     and e['boot'] != 'unknown' and 0 <= now - e['time'] <= MAX_AGE}
            if len(boots) < 3: status = 'unknown'
        rows.append({'check': check, 'title': title, 'required': required, 'status': status,
                     'evidenceType': latest['type'] if latest else None,
                     'observedAt': latest['time'] if latest else None})
    eligible = (ctx['scope'] == 'physical-host' and ctx['environment'] == 'installed-host' and ctx['architecture'] == 'x86_64'
                and ctx['revision'] != 'unknown' and ctx['system'] != 'unknown'
                and ctx['incus'] != 'unknown' and (revision is None or ctx['revision'] == revision)
                and all(r['status'] == 'passed' for r in rows if r['required']))
    return {'format': VERSION, 'revision': ctx['revision'], 'mode': ctx['mode'],
            'scope': ctx['scope'], 'environment': ctx['environment'], 'profile': 'apps' if ctx['apps'] else 'core',
            'qualification': 'ready-for-review' if eligible else 'unqualified', 'checks': rows}


def load(path):
    if path.stat().st_size > MAX_BYTES: raise ValueError('Oversized report')
    return validate(json.loads(path.read_text()))


def matrix(paths, revision):
    if not re.fullmatch(r'[0-9a-f]{40}', revision): raise ValueError('Use a full release revision')
    lines = ['# Single-host qualification evidence', '', 'Source revision: `' + revision + '`.', '',
             'Reports are operator evidence, not a security certification. Ready-for-review still requires release review.', '',
             '| Target fingerprint | Mode | Profile | Scope | Environment | Evidence |', '|---|---|---|---|---|---|']
    for path in paths:
        report = load(path)
        summary = summarize(report, revision=revision)
        lines.append('| ' + ' | '.join([report['context']['hardwareDigest'][:12], summary['mode'],
                     summary['profile'], summary['scope'], summary['environment'], summary['qualification']]) + ' |')
    if not paths: lines.append('| No submitted evidence | — | — | — | — | unqualified |')
    return '\n'.join(lines) + '\n'


def cli(argv):
    parser = argparse.ArgumentParser(prog='nixlab-admin commissioning')
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('collect', 'record', 'status', 'export'):
        cmd = sub.add_parser(action)
        cmd.add_argument('--mode', choices=['fresh', 'upgrade'], required=True)
        cmd.add_argument('--environment', choices=sorted(ENVIRONMENTS), default='installed-host')
        if action == 'record':
            cmd.add_argument('--check', choices=sorted(MANUAL), required=True)
            cmd.add_argument('--status', choices=sorted(STATUSES), required=True)
            cmd.add_argument('--acknowledge', action='store_true', help='attest that the documented procedure was actually performed')
    cmd = sub.add_parser('matrix')
    cmd.add_argument('--revision', required=True)
    cmd.add_argument('reports', nargs='*', type=Path)
    args = parser.parse_args(argv)
    if args.action == 'matrix':
        print(matrix(args.reports, args.revision), end='')
        return
    if os.geteuid() != 0: raise ValueError('Use sudo for local commissioning evidence')
    if args.action == 'record' and not args.acknowledge:
        raise ValueError('Perform the commissioning procedure first, then use --acknowledge to record an operator attestation')
    settings = rt.read_json(Path('/etc/nixlab/effective-settings.json'))
    if not isinstance(settings, dict): raise ValueError('Install and activate NixLab before commissioning')
    ctx = context(settings, args.mode, args.environment)
    directory = rt.STATE / 'commissioning'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != 0 or directory.stat().st_mode & 0o077:
        raise ValueError('Commissioning directory must be root-owned, mode 0700')
    with open_lock(directory / 'report.lock') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / (args.environment + '-' + args.mode + '.json')
        if path.is_symlink(): raise ValueError('Report cannot be a symlink')
        report = load(path) if path.exists() else {'format': VERSION, 'context': ctx, 'evidence': []}
        if args.action == 'collect': collect(report, ctx, settings)
        elif args.action == 'record': record(report, ctx, args.check, args.status, 'manual-attestation')
        if args.action in ('collect', 'record'): rt.write_json(path, report)
        if args.action == 'export':
            # Refresh context for export so a changed live system cannot inherit a green report.
            report['context'] = ctx
            validate(report)
            print(json.dumps(report, indent=2))
        else:
            print(json.dumps(summarize(report, current=ctx), indent=2))


if __name__ == '__main__':
    try:
        cli(__import__('sys').argv[1:])
    except (ValueError, OSError, subprocess.SubprocessError):
        raise SystemExit('Commissioning report unavailable or invalid; check documented prerequisites and local file permissions')
