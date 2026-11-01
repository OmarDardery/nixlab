"""Independent systemd transaction worker and persistent rollback guard."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
import runtime as rt


def save_operator_password(recovery):
    account = subprocess.run(['getent', 'shadow', 'operator'], capture_output=True, text=True, timeout=10)
    if account.returncode:
        exists = subprocess.run(['getent', 'passwd', 'operator'], capture_output=True, text=True, timeout=10)
        if exists.returncode == 0 or account.returncode != 2:
            raise ValueError('Cannot preserve the local operator credential')
        value = '!'
    else:
        fields = account.stdout.strip().split(':')
        if len(fields) != 9 or fields[0] != 'operator':
            raise ValueError('Cannot preserve the local operator credential')
        value = fields[1]
    rt.atomic(recovery / 'operator-password', value + '\n')


def restore_operator_password(recovery):
    password = recovery / 'operator-password'
    if not password.exists(): return
    account = subprocess.run(['getent', 'passwd', 'operator'], capture_output=True, text=True, timeout=10)
    if account.returncode == 2: return
    if account.returncode:
        raise ValueError('Cannot inspect the local operator account for recovery')
    restored = subprocess.run(['chpasswd', '--encrypted'], input='operator:' + password.read_text().strip() + '\n',
                              text=True, capture_output=True, timeout=30)
    if restored.returncode:
        raise ValueError('Could not restore the local operator credential; use the host console')


def run(args, log, timeout=7200, check=True):
    log.write('\n$ ' + ' '.join(args) + '\n'); log.flush()
    process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait()
        raise
    if check and code:
        raise subprocess.CalledProcessError(code, args)
    return subprocess.CompletedProcess(args, code)


def update(directory, **fields):
    data = rt.read_json(directory / 'job.json')
    data.update(fields)
    rt.write_json(directory / 'job.json', data)
    return data


def validate_effective_transition(effective, disks, active_settings, previous_disks, initialized):
    for name, previous in previous_disks.items():
        if name not in disks or any(disks[name][key] != previous[key] for key in ('uuid', 'path', 'fsType', 'role')):
            raise ValueError('Migrate active disk before removing/remapping: ' + name)
    if initialized and active_settings:
        if effective['storage'] != active_settings['storage'] or effective['guests'] != active_settings['guests']:
            raise ValueError('Initialized Incus storage/network requires an explicit migration')
        desired_mounts = {mount['name']: mount for mount in effective['mounts']}
        for mount in active_settings['mounts']:
            if mount['role'] in ('guests', 'pool') and desired_mounts.get(mount['name']) != mount:
                raise ValueError('Migrate initialized Incus mount before removing/remapping: ' + mount['name'])


def build(directory):
    source = directory / 'source'
    job = rt.read_json(directory / 'job.json')
    old_inputs = rt.read_json(source / 'flake.lock', {}).get('nodes', {})
    with (directory / 'build.log').open('a', buffering=1) as log:
        if job.get('platformRevision'):
            import upgrades
            changes = upgrades.prepare(source, job['platformRevision'], log)
            update(directory, platformChanges=changes)
        if job['updateInputs']:
            run(['nix', '--extra-experimental-features', 'nix-command flakes', 'flake', 'update', 'nixpkgs', '--flake', 'path:' + str(source)], log)
        run(['nix', '--extra-experimental-features', 'nix-command flakes', 'build', 'path:' + str(source) + '#nixosConfigurations.hypervisor.config.system.build.toplevel',
             '--out-link', str(directory / 'system')], log)
        run(['nixos-rebuild', 'dry-activate', '--flake', 'path:' + str(source) + '#hypervisor'], log)
    result = subprocess.run(['nix', '--extra-experimental-features', 'nix-command flakes', 'eval', '--json',
        'path:' + str(source) + '#nixosConfigurations.hypervisor.config', '--apply',
        'c: { settings = c.nixlab.settings; disks = c.nixlab.storage.disks; }'],
        check=True, capture_output=True, text=True, timeout=120)
    projection = json.loads(result.stdout)
    effective, disks = projection['settings'], projection['disks']
    if 'cockpitPassword' in rt.read_json(directory / 'secrets.json', {}) and not (effective['cockpit']['enable'] and effective['cockpit']['managePassword']):
        raise ValueError('Cockpit password enrollment conflicts with file-owned effective settings')
    from model import inventory, validate_settings
    validation = json.loads(json.dumps(effective))
    validation['mounts'] += list(disks.values())
    validate_settings(validation, validation, inventory())
    validate_effective_transition(effective, disks, rt.read_json(Path('/etc/nixlab/effective-settings.json'), {}),
        rt.read_json(Path('/etc/nixlab/effective-disks.json'), {}), Path('/var/lib/incus/database').exists())
    new_inputs = rt.read_json(source / 'flake.lock', {}).get('nodes', {})
    changed_inputs = [{'input': key, 'before': old_inputs.get(key, {}).get('locked', {}).get('rev'),
                       'after': value.get('locked', {}).get('rev')} for key, value in new_inputs.items()
                      if value.get('locked') != old_inputs.get(key, {}).get('locked')]
    booted = Path('/run/booted-system/kernel')
    kernel = directory / 'system/kernel'
    reboot = booted.exists() and kernel.exists() and booted.resolve() != kernel.resolve()
    update(directory, status='ready', builtAt=time.time(), effectiveSettings=effective, candidateDigest=rt.digest_source(source),
           inputChanges=changed_inputs, rebootRequired=bool(reboot))
    rt.audit('build-ready', job=directory.name)


def save_recovery(directory):
    recovery = directory / 'recovery'
    recovery.mkdir(mode=0o700, exist_ok=True)
    record = rt.read_json(directory / 'job.json', {})
    candidate = record.get('effectiveSettings', record.get('settings', {}))
    if candidate.get('cockpit', {}).get('managePassword'):
        save_operator_password(recovery)
    platform = bool(rt.read_json(directory / 'job.json', {}).get('platformRevision'))
    if platform:
        rt.snapshot(recovery / 'platform', rt.ROOT)
    modules = rt.module_files(rt.ROOT)
    module_names = [str(path.relative_to(rt.ROOT)) for path in modules]
    for path, name in zip(modules, module_names):
        rt.atomic(recovery / 'modules' / name, path.read_bytes(), mode=path.stat().st_mode & 0o777)
    files = [(rt.machine_path(rt.ROOT, 'settings'), 'settings.json'), (rt.ROOT / 'flake.lock', 'flake.lock')]
    files += [(path, 'secret-' + key) for key, path in rt.SECRET_PATHS.items()]
    files += [(rt.STATE / name, name) for name in ('tls.crt', 'tls.key')]
    manifest = []
    for path, name in files:
        manifest.append({'path': str(path), 'name': name, 'existed': path.exists(), 'mode': path.stat().st_mode & 0o777 if path.exists() else 0o600})
        if path.exists():
            rt.atomic(recovery / name, path.read_bytes())
    nm = Path('/run/NetworkManager/system-connections')
    for path in nm.glob('nixlab-*.nmconnection') if nm.exists() else []:
        rt.atomic(recovery / path.name, path.read_bytes())
    try:
        active = subprocess.run(['nmcli', '--get-values', 'GENERAL.CON-UUID', 'device', 'show'], capture_output=True, text=True, timeout=10)
        uuids = [s.strip() for s in active.stdout.splitlines() if s.strip() and s.strip() != '--'] if active.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        uuids = []
    try:
        radios = json.loads(subprocess.run(['rfkill', '--json'], capture_output=True, text=True, check=True).stdout).get('rfkilldevices', [])
    except (OSError, ValueError, subprocess.CalledProcessError):
        radios = []
    rt.write_json(recovery / 'manifest.json', {'files': manifest, 'nmConnections': uuids, 'radios': radios, 'moduleFiles': module_names, 'platformSnapshot': platform})


def restore_files(directory):
    recovery = directory / 'recovery'
    manifest = rt.read_json(recovery / 'manifest.json')
    if not manifest:
        raise ValueError('Recovery manifest is missing')
    if manifest.get('platformSnapshot'):
        import upgrades
        upgrades.publish(recovery / 'platform', rt.ROOT)
    if 'moduleFiles' in manifest:
        # Validate the complete live tree before removing or restoring any module.
        live_modules = rt.module_files(rt.ROOT)
        previous = set(manifest['moduleFiles'])
        for name in previous:
            if not name.startswith(('site-modules/', 'machine/modules/')) or '..' in Path(name).parts:
                raise ValueError('Invalid module recovery path')
        for path in live_modules:
            if str(path.relative_to(rt.ROOT)) not in previous:
                rt.durable_unlink(path)
        for name in previous:
            source = recovery / 'modules' / name
            rt.atomic(rt.ROOT / name, source.read_bytes(), mode=source.stat().st_mode & 0o777)
    for item in manifest['files']:
        target = Path(item['path'])
        if item['existed']:
            rt.atomic(target, (recovery / item['name']).read_bytes(), mode=item['mode'])
        else:
            target.unlink(missing_ok=True)
    nm = Path('/run/NetworkManager/system-connections')
    for path in nm.glob('nixlab-*.nmconnection') if nm.exists() else []:
        path.unlink()
    for path in recovery.glob('nixlab-*.nmconnection'):
        rt.atomic(nm / path.name, path.read_bytes())
    return manifest


def rollback(directory, reason='requested'):
    job = update(directory, status='rolling-back', rollbackReason=reason)
    previous = job.get('previousSystem')
    if not previous or not Path(previous, 'bin/switch-to-configuration').is_file():
        raise ValueError('Previous OS generation is unavailable; use the bootloader recovery menu')
    with (directory / 'build.log').open('a', buffering=1) as log:
        manifest = restore_files(directory)
        run(['nix-env', '--profile', '/nix/var/nix/profiles/system', '--set', previous], log, timeout=120)
        run([previous + '/bin/switch-to-configuration', 'switch'], log, timeout=900)
        restore_operator_password(directory / 'recovery')
        if shutil.which('rfkill'):
            for radio in manifest.get('radios', []):
                if str(radio.get('id', '')).isdigit():
                    run(['rfkill', 'block' if radio.get('soft') in ('blocked', True) else 'unblock', str(radio['id'])], log, timeout=10, check=False)
        if shutil.which('nmcli'):
            run(['nmcli', 'connection', 'reload'], log, timeout=30, check=False)
            for uuid in manifest.get('nmConnections', []):
                # Values come from nmcli before activation, never from a browser command string.
                run(['nmcli', '--wait', '30', 'connection', 'up', 'uuid', uuid], log, timeout=40, check=False)
        run(['systemctl', 'try-restart', 'nixlab-admin-backend.service', 'nixlab-ui.service', 'nginx.service', 'cockpit.service'], log, timeout=45, check=False)
    update(directory, status='rolled-back', rolledBackAt=time.time())
    pending = rt.read_json(rt.STATE / 'pending.json')
    if pending and pending['id'] == directory.name:
        rt.durable_unlink(rt.STATE / 'pending.json')
    (directory / 'secrets.json').unlink(missing_ok=True)
    rt.durable_unlink(directory / 'system')
    rt.durable_unlink(directory / 'previous-system')
    rt.audit('rolled-back', job=directory.name, reason=reason)


def apply(directory):
    job = rt.read_json(directory / 'job.json')
    if job['baseDigest'] != rt.digest_source(rt.ROOT) or job['candidateDigest'] != rt.digest_source(directory / 'source'):
        raise ValueError('Source changed since review')
    old_settings = rt.read_json(Path('/etc/nixlab/effective-settings.json'), rt.read_json(rt.machine_path(rt.ROOT, 'settings')))
    effective = job.get('effectiveSettings', job['settings'])
    previous = str(Path('/run/current-system').resolve())
    save_recovery(directory)
    subprocess.run(['nix-store', '--add-root', str(directory / 'previous-system'), '--indirect', '--realise', previous], check=True, stdout=subprocess.DEVNULL)
    job = update(directory, previousSystem=previous)
    deadline = time.time() + 960 # activation itself has a 900-second deadline
    rt.write_json(rt.STATE / 'pending.json', {'id': directory.name, 'previousSystem': previous, 'deadline': deadline})
    try:
        # Live secrets are separate from the immutable, reviewed Nix snapshot.
        for key, data in rt.read_json(directory / 'secrets.json', {}).items():
            rt.atomic(rt.SECRET_PATHS[key], data)
        if job.get('platformRevision'):
            import upgrades
            upgrades.publish(directory / 'source', rt.ROOT)
        rt.install_modules(directory / 'source', rt.ROOT)
        rt.write_json(rt.machine_path(rt.ROOT, 'settings'), job['settings'])
        if job['updateInputs']:
            rt.atomic(rt.ROOT / 'flake.lock', (directory / 'source/flake.lock').read_bytes(), mode=0o644)
        before, after = old_settings['management'], effective['management']
        if before['address'] != after['address'] or old_settings['hostName'] != effective['hostName']:
            rt.issue_tls(after['address'], effective['hostName'])
        with (directory / 'build.log').open('a', buffering=1) as log:
            # Activate the exact reviewed closure; never rebuild from changed inputs here.
            system = str((directory / 'system').resolve())
            run(['nix-env', '--profile', '/nix/var/nix/profiles/system', '--set', system], log, timeout=120)
            run([system + '/bin/switch-to-configuration', 'switch'], log, timeout=900)
            rt.prepare_state_access()
            run(['systemctl', 'is-active', 'incus.service', 'nixlab-instances.service'], log, timeout=30)
            if effective['cockpit']['enable']:
                run(['systemctl', 'is-active', 'cockpit.socket'], log, timeout=30)
                run(['systemctl', 'try-restart', 'cockpit.service'], log, timeout=45)
            if effective['apps']['enable']:
                run(['systemctl', 'is-active', 'nixlab-apps-onboarding.service', 'nginx.service'], log, timeout=30)
            if before['address'] != after['address'] or old_settings['hostName'] != effective['hostName']:
                run(['systemctl', 'try-restart', 'incus.service'], log, timeout=120)
            # Load rotated TLS material/new port. This may require signing in again.
            run(['systemctl', 'try-restart', 'nixlab-admin-backend.service', 'nixlab-ui.service'], log, timeout=45)
            if effective['apps']['enable']:
                run(['systemctl', 'try-restart', 'nginx.service'], log, timeout=45)
        deadline = time.time() + effective['setup']['confirmationSeconds']
        rt.write_json(rt.STATE / 'pending.json', {'id': directory.name, 'previousSystem': previous, 'deadline': deadline})
        update(directory, status='awaiting-confirmation', deadline=deadline)
        rt.audit('awaiting-confirmation', job=directory.name)
    except Exception:
        rollback(directory, 'activation failed')
        raise


def job(phase, job_id):
    if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('Invalid transaction ID')
    directory = rt.STATE / 'jobs' / job_id
    if not directory.is_dir():
        raise ValueError('Unknown transaction')
    try:
        if phase == 'build':
            build(directory)
        elif phase == 'apply':
            # Guard and confirmation serialize against activation. The guard also
            # checks the deadline on a reboot or after a killed apply process.
            with rt.locked():
                apply(directory)
        elif phase == 'rollback':
            with rt.locked():
                rollback(directory)
        else:
            raise ValueError('Unsupported transaction action')
    except Exception as exc:
        current = rt.read_json(directory / 'job.json')
        if current.get('status') != 'rolled-back':
            update(directory, status='failed', error=str(exc))
        with (directory / 'build.log').open('a') as log:
            log.write('\nFAILED: ' + str(exc) + '\n')
        rt.audit('transaction-failed', job=job_id, phase=phase)
        raise


def guard():
    pending = rt.read_json(rt.STATE / 'pending.json')
    if pending and time.time() >= pending['deadline']:
        with rt.locked():
            pending = rt.read_json(rt.STATE / 'pending.json')
            if pending and rt.read_json(rt.STATE / 'jobs' / pending['id'] / 'job.json', {}).get('status') == 'confirmed':
                rt.durable_unlink(rt.STATE / 'pending.json')
            elif pending and time.time() >= pending['deadline']:
                rollback(rt.STATE / 'jobs' / pending['id'], 'confirmation timed out')
    from operations import Operations
    Operations().recover_interrupted()
