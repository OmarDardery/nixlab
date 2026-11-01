"""Direct installed-NixOS conversion through the existing build/activation transaction."""
import copy
import ipaddress
from pathlib import Path
import secrets
import time

import runtime as rt


def guest_network(routes, requested=None):
    occupied = [ipaddress.ip_network(r['dst'], strict=False) for r in routes
                if r.get('dst', 'default') != 'default']
    choices = [requested] if requested else [
        '10.90.90.0/24', '10.77.0.0/24', '10.78.0.0/24', '172.30.77.0/24', '192.168.240.0/24']
    for choice in choices:
        net = ipaddress.ip_network(choice, strict=True)
        private = [ipaddress.ip_network(value) for value in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]
        if net.version != 4 or net.prefixlen != 24 or not any(net.subnet_of(block) for block in private):
            raise ValueError('Guest network must be a private IPv4 /24')
        if any(net.overlaps(existing) for existing in occupied):
            continue
        prefix = str(net.network_address).rsplit('.', 1)[0]
        return {'cidr': str(net), 'gatewayCIDR': prefix + '.1/24',
                'appsAddress': prefix + '.10', 'dhcpRange': prefix + '.100-' + prefix + '.199'}
    raise ValueError('No non-overlapping guest subnet; select an unused --guest-subnet')


def prepare(settings, overview, host, key_files=(), subnet=None, apps=True, previous_guests=None,
            remote=False, password_login=False):
    candidate = copy.deepcopy(settings)
    keys = list(candidate['adminKeys']) + overview.get('adminKeys', [])
    for path in key_files:
        keys += [line.strip() for line in Path(path).read_text().splitlines()
                 if line.strip() and not line.startswith('#')]
    candidate['adminKeys'] = sorted(set(keys))
    candidate.setdefault('access', {})['sshPasswordLogin'] = password_login
    if remote and not keys and not password_login:
        raise ValueError('Remote installation needs your client SSH public key (--ssh-key laptop.pub), '
                         'or explicitly retain password login with --ssh-password-login. Local console installation needs neither.')
    if overview.get('wpaProfiles') and candidate['radios']['wifi']['enable']:
        raise ValueError('Direct switch cannot migrate legacy wpa_supplicant credentials; use Ethernet or the setup wizard')
    if previous_guests:
        if subnet and subnet != previous_guests['cidr']:
            raise ValueError('A partially initialized Incus network cannot change on resume')
        candidate['guests'].update(copy.deepcopy(previous_guests))
    else:
        candidate['guests'].update(guest_network(host['routes'], subnet))
    if not settings['ready']:
        candidate['cockpit']['enable'] = True
        candidate['apps'].update(enable=apps, portainerPort=9443)
        candidate['maintenance']['laptopMode'] = True
    # Preserve current addressing, accounts and mounts. No disk/bridge migration.
    if not settings['ready']:
        candidate['management']['configure'] = False
    from model import validate_settings
    return validate_settings(candidate, settings, host)


def wait_for(manager, job_id, statuses, timeout=10800):
    deadline = time.monotonic() + timeout
    position = 0
    log_path = manager.job_path(job_id) / 'build.log'
    while time.monotonic() < deadline:
        if log_path.exists():
            with log_path.open() as log:
                log.seek(position)
                output = log.read()
                position = log.tell()
            if output:
                print(output, end='', flush=True)
        manager.status()  # Detect stopped durable workers, including before first activation.
        record = manager.job(job_id)
        if record['status'] in statuses:
            return record
        if record['status'] in ('failed', 'rolled-back', 'cancelled'):
            raise ValueError('Direct conversion failed: ' + record.get('error', record['status']) +
                             ('; rollback also failed: ' + record['rollbackError'] if record.get('rollbackError') else '') +
                             '; log: ' + str(log_path))
        time.sleep(1)
    raise ValueError('Conversion timed out; inspect ' + str(log_path) + ' and the transaction status before retrying')


def activate(candidate, manager=None, effective=None):
    from controller import Manager
    manager = manager or Manager()
    effective = effective or candidate
    if effective['cockpit']['enable'] and effective['cockpit']['managePassword']:
        import local_access
        local_access.ensure_operator_password()
    initial = None
    if effective['apps']['enable'] and not rt.SECRET_PATHS['portainerPassword'].exists():
        initial = secrets.token_urlsafe(24)
        # Created once outside the rollback manifest: failed activation must not
        # rotate the credential after Portainer has initialized its database.
        rt.atomic(rt.SECRET_PATHS['portainerPassword'], initial + '\n')
    job_id = manager.review(candidate, secret_values={}, principal='root-direct-install')['id']
    print('Building hypervisor; transaction ' + job_id, flush=True)
    wait_for(manager, job_id, {'ready'})
    print('Switching to the built hypervisor generation.', flush=True)
    manager.apply(job_id)
    record = wait_for(manager, job_id, {'awaiting-confirmation'})
    # This explicit CLI mode confirms locally after the shared activation health
    # checks. Browser confirmation remains the default in the ordinary wizard.
    manager.confirm(job_id)
    return initial, record


def resume(manager, record):
    """Reconnect to an existing durable worker without starting a competing job."""
    job_id = record['id']
    if record['status'] == 'rolling-back':
        return wait_for(manager, job_id, {'rolled-back'})
    if record['status'] == 'building':
        record = wait_for(manager, job_id, {'ready'})
    if record['status'] == 'ready':
        manager.apply(job_id)
        record = wait_for(manager, job_id, {'awaiting-confirmation'})
    elif record['status'] == 'applying':
        record = wait_for(manager, job_id, {'awaiting-confirmation'})
    if record['status'] == 'awaiting-confirmation':
        import diagnostics
        diagnostics.wait_ready(record.get('effectiveSettings', record['settings']))
        manager.confirm(job_id)
    return record

