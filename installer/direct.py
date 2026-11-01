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
        if net.version != 4 or net.prefixlen != 24 or not net.is_private:
            raise ValueError('Guest network must be a private IPv4 /24')
        if any(net.overlaps(existing) for existing in occupied):
            continue
        prefix = str(net.network_address).rsplit('.', 1)[0]
        return {'cidr': str(net), 'gatewayCIDR': prefix + '.1/24',
                'appsAddress': prefix + '.10', 'dhcpRange': prefix + '.100-' + prefix + '.199'}
    raise ValueError('No non-overlapping guest subnet; select an unused --guest-subnet')


def prepare(settings, overview, host, key_files=(), subnet=None, apps=True, previous_guests=None):
    candidate = copy.deepcopy(settings)
    keys = list(candidate['adminKeys']) + overview.get('adminKeys', [])
    for path in key_files:
        keys += [line.strip() for line in Path(path).read_text().splitlines()
                 if line.strip() and not line.startswith('#')]
    candidate['adminKeys'] = sorted(set(keys))
    if not keys:
        raise ValueError('Direct switch needs an SSH public key: pass --ssh-key /path/to/key.pub')
    if overview.get('wpaProfiles') and candidate['radios']['wifi']['enable']:
        raise ValueError('Direct switch cannot migrate legacy wpa_supplicant credentials; use Ethernet or the setup wizard')
    if previous_guests:
        if subnet and subnet != previous_guests['cidr']:
            raise ValueError('A partially initialized Incus network cannot change on resume')
        candidate['guests'] = copy.deepcopy(previous_guests)
    else:
        candidate['guests'] = guest_network(host['routes'], subnet)
    candidate['cockpit']['enable'] = True
    candidate['apps'].update(enable=apps, portainerPort=9443)
    candidate['maintenance']['laptopMode'] = True
    # Preserve current addressing, accounts and mounts. No disk/bridge migration.
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
        record = manager.job(job_id)
        if record['status'] in statuses:
            return record
        if record['status'] in ('failed', 'rolled-back', 'cancelled'):
            raise ValueError('Direct conversion failed: ' + record.get('error', record['status']) +
                             '; log: ' + str(log_path))
        time.sleep(1)
    raise ValueError('Conversion timed out; inspect ' + str(log_path) + ' and the transaction status before retrying')


def activate(candidate, manager=None):
    from controller import Manager
    manager = manager or Manager()
    credentials = {}
    initial = None
    if candidate['apps']['enable'] and not rt.SECRET_PATHS['portainerPassword'].exists():
        initial = secrets.token_urlsafe(24)
        credentials['portainerPassword'] = initial
    job_id = manager.review(candidate, secret_values=credentials, principal='root-direct-install')['id']
    print('Building hypervisor; transaction ' + job_id, flush=True)
    wait_for(manager, job_id, {'ready'})
    print('Switching to the built hypervisor generation.', flush=True)
    manager.apply(job_id)
    record = wait_for(manager, job_id, {'awaiting-confirmation'})
    # This explicit CLI mode confirms locally after the shared activation health
    # checks. Browser confirmation remains the default in the ordinary wizard.
    manager.confirm(job_id)
    return initial, record
