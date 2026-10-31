"""Packaged upstream Incus wizard. No browser execution or Proxmox emulation."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

BASE = Path(os.environ.get('NIXLAB_HELPER_METADATA', Path(__file__).parent))
ENGINE = Path(os.environ.get('NIXLAB_HELPER_ENGINE', '/nonexistent'))
SCRIPTS = Path(os.environ.get('NIXLAB_HELPER_SCRIPTS', '/nonexistent'))
STATE = Path('/var/lib/nixlab/helpers')
NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}')


def pins(): return json.loads((BASE / 'sources.json').read_text())


def catalog():
    known = json.loads((BASE / 'compatibility.json').read_text())
    return [{'id': p.stem, 'title': known.get(p.stem, {}).get('title', p.stem),
             'status': known.get(p.stem, {}).get('status', 'upstream-supported'),
             'notes': known.get(p.stem, {}).get('notes', 'Not qualified on Nixlab.'),
             'verifiedOnNixlab': known.get(p.stem, {}).get('status') == 'verified'}
            for p in sorted((SCRIPTS / 'ct').glob('*.sh')) if not p.is_symlink() and NAME.fullmatch(p.stem)]


def command(args, env):
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=30)
    if result.returncode: raise ValueError(result.stderr.strip()[-2000:] or 'Command failed: ' + args[0])
    return result.stdout


def environment(project, directory):
    if not NAME.fullmatch(project): raise ValueError('Use a local Incus project name')
    config = directory / 'incus'; config.mkdir(mode=0o700)
    (config / 'config.yml').write_text('default-remote: local\nremotes:\n  local:\n    addr: unix://\n    project: ' + project
                                     + '\n  images:\n    addr: https://images.linuxcontainers.org\n    protocol: simplestreams\n    public: true\n')
    source = pins()
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('COMMUNITY_SCRIPTS_', '_CS_', '_INCUS_', 'LXC_PLATFORM', 'INCUS_')): env.pop(key)
    env.update(INCUS_CONF=str(config), INCUS_PROJECT=project, LXC_PLATFORM='incus',
               COMMUNITY_SCRIPTS_CORE_DIR=str(ENGINE), COMMUNITY_SCRIPTS_ROOT=str(SCRIPTS),
               COMMUNITY_SCRIPTS_CORE_URL='https://raw.githubusercontent.com/' + source['core']['repository'] + '/' + source['core']['revision'],
               COMMUNITY_SCRIPTS_URL='https://raw.githubusercontent.com/' + source['scripts']['repository'] + '/' + source['scripts']['revision'],
               COMMUNITY_SCRIPTS_STATE_DIR=str(STATE), LC_ALL='C')
    return env


def doctor(project, directory):
    if not (ENGINE / 'core/build.func').is_file() or not (SCRIPTS / 'ct').is_dir():
        raise ValueError('Use the Nix-packaged runner: pinned engine/scripts are missing')
    missing = [n for n in ('bash', 'incus', 'curl', 'jq', 'whiptail', 'awk', 'sed', 'tar', 'xz', 'hostname') if not shutil.which(n)]
    if missing: raise ValueError('Missing packaged dependencies: ' + ', '.join(missing))
    env = environment(project, directory)
    info = json.loads(command(['incus', '--force-local', 'query', '/1.0'], env))
    command(['incus', '--force-local', 'project', 'show', project], env)
    pools = json.loads(command(['incus', '--force-local', 'storage', 'list', '--format=json'], env))
    if not pools: raise ValueError('Create/select an Incus storage pool before provisioning')
    networks = json.loads(command(['incus', '--force-local', 'network', 'list', '--format=json'], env))
    candidates = [n for n in networks if n.get('type') in ('bridge', 'ovn', 'macvlan')]
    if not candidates: raise ValueError('Prepare a guest network in Incus or a host Ethernet bridge first')
    command(['incus', 'remote', 'get-url', 'images'], env)
    command(['incus', 'image', 'info', 'images:debian/13'], env)
    for pin in pins().values():
        command(['curl', '--fail', '--silent', '--show-error', '--head', '--max-time', '15',
                 'https://raw.githubusercontent.com/' + pin['repository'] + '/' + pin['revision'] + '/README.md'], env)
    capacity = []
    for pool in pools:
        response = json.loads(command(['incus', '--force-local', 'query', '/1.0/storage-pools/' + pool['name'] + '/resources'], env))
        space = response.get('space', {})
        capacity.append({'name': pool['name'], 'freeBytes': space.get('total', 0) - space.get('used', 0)})
    if not any(p['freeBytes'] > 1024**3 for p in capacity):
        raise ValueError('No pool reports at least 1 GiB free; inspect Incus pool capacity')
    return env, {'project': project, 'serverVersion': info.get('environment', {}).get('server_version'),
                 'pools': capacity, 'networks': [n['name'] for n in candidates], 'sources': pins()}


def secure_state():
    if STATE.resolve() != STATE: raise ValueError('Helper state cannot traverse symlinks')
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    if STATE.stat().st_uid != os.geteuid() or STATE.stat().st_mode & 0o077:
        raise ValueError('Helper state must be root-owned with mode 0700')


def instances(env):
    return {item['name']: item for item in json.loads(command(['incus', '--force-local', 'list', '--format=json'], env))}


def observed_versions(guests, env):
    result = {}
    checks = {'adguard': ['/opt/AdGuardHome/AdGuardHome', '--version'],
              'vaultwarden': ['/opt/vaultwarden/bin/vaultwarden', '--version']}
    for name, guest in guests.items():
        entry = {'imageOS': guest.get('config', {}).get('image.os'), 'imageRelease': guest.get('config', {}).get('image.release')}
        for application, args in checks.items():
            if name.startswith(application):
                try: entry['applicationVersion'] = command(['incus', '--force-local', 'exec', name, '--', *args], env).strip()[:500]
                except (ValueError, subprocess.TimeoutExpired): pass
        result[name] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'doctor', 'run'])
    parser.add_argument('script', nargs='?')
    parser.add_argument('--project', default='lab')
    args = parser.parse_args()
    if args.action == 'list': print(json.dumps({'sources': pins(), 'scripts': catalog()}, indent=2)); return 0
    if os.geteuid() != 0: parser.error('Run doctor/run with sudo on the Incus host')
    if args.action == 'run' and (not args.script or not NAME.fullmatch(args.script)
                                or args.script not in {item['id'] for item in catalog()}):
        parser.error('Choose a packaged ct script ID from nixlab-helpers list; PVE host/VM scripts are unsupported')
    if args.action == 'run' and not sys.stdin.isatty(): parser.error('The upstream wizard requires an interactive terminal')
    secure_state()
    admin_state = Path('/var/lib/nixlab-admin')
    admin_state.mkdir(mode=0o700, exist_ok=True)
    from locking import open_lock
    import fcntl
    with open_lock('/var/lib/nixlab-admin/mutation.lock') as config_lock, open_lock('/run/lock/nixlab-incus.lock') as incus_lock:
        fcntl.flock(config_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(incus_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        active = json.loads((admin_state / 'active.json').read_text()) if (admin_state / 'active.json').is_file() else {}
        job_path = admin_state / 'jobs' / active.get('id', '-') / 'job.json'
        job = json.loads(job_path.read_text()) if job_path.is_file() else {}
        if (admin_state / 'pending.json').exists() or job.get('status') in ('building', 'applying', 'awaiting-confirmation', 'rolling-back'):
            raise ValueError('Finish the host configuration transaction before running helper wizards')
        with tempfile.TemporaryDirectory(dir=STATE, prefix='client-') as folder:
            env, report = doctor(args.project, Path(folder))
            if args.action == 'doctor': print(json.dumps(report, indent=2)); return 0
            record = {'script': args.script, 'started': datetime.datetime.now(datetime.timezone.utc).isoformat(), **report}
            before = instances(env)
            record['hostSystem'] = str(Path('/run/current-system').resolve())
            path = STATE / (str(os.getpid()) + '-' + args.script + '.json')
            path.write_text(json.dumps(record, indent=2)); path.chmod(0o600)
            try:
                result = subprocess.run(['bash', str(SCRIPTS / 'ct' / (args.script + '.sh'))], env=env)
                record['exitStatus'] = result.returncode
                return result.returncode
            finally:
                record['finished'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                record.setdefault('exitStatus', 'interrupted')
                try:
                    new = {name: item for name, item in instances(env).items() if name not in before}
                    record['observedNewInstances'] = observed_versions(new, env)
                except (ValueError, OSError, subprocess.TimeoutExpired): record['metadataIncomplete'] = True
                path.write_text(json.dumps(record, indent=2))


if __name__ == '__main__':
    os.umask(0o077)
    try: sys.exit(main())
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc: sys.exit(str(exc))
