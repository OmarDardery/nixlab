"""One-command platform updates through the existing durable transaction engine."""
import argparse
import ipaddress
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
from model import inventory
from upgrades import main_revision

PRINCIPAL = 'root-update-command'
ACTIVE = {'building', 'ready', 'applying', 'awaiting-confirmation', 'rolling-back'}


def ask(message):
    return input(message + ' [y/N] ').strip().lower() in ('y', 'yes')


def already_current(manager, state, revision):
    if state.get('release', {}).get('revision') != revision:
        return False
    job_id = (state.get('lastConfirmed') or {}).get('id')
    if not job_id or not state.get('currentSystem'):
        return False
    confirmed = manager.job(job_id)
    return (confirmed['status'] == 'confirmed'
            and confirmed.get('candidateDigest') == rt.digest_source(manager.root)
            and (manager.job_path(job_id) / 'system').resolve() == Path(state['currentSystem']))


def management_change(settings, interface, saved=None):
    """Select an existing address owner; never move addresses or expand trust."""
    current = settings['management']
    link = next((link for link in inventory()['interfaces'] if link['name'] == interface), None)
    addresses = [ipaddress.ip_interface(value) for value in (link or {}).get('addresses', [])]
    addresses = [value for value in addresses if value.version == 4
                 and not value.ip.is_loopback and not value.ip.is_link_local
                 and not value.ip.is_unspecified and not value.ip.is_multicast]
    selected = next((value for value in addresses if str(value.ip) == current['address']), None)
    if selected is None:
        if len(addresses) != 1:
            raise ValueError('Selected interface needs one unambiguous usable IPv4 address')
        selected = addresses[0]
    if not any(selected.ip in ipaddress.ip_network(cidr) for cidr in current['trustedCIDRs']):
        raise ValueError('Selected address is outside existing trusted management networks; review access policy first')
    values = {'interface': interface, 'address': str(selected.ip), 'prefixLength': selected.network.prefixlen}
    matches = all(current[key] == value for key, value in values.items())
    persisted = saved is None or all(saved['management'][key] == value for key, value in values.items())
    return {} if matches and persisted else {'management': values}


def run(manager, interface=None, repair=None):
    state = manager.status()
    record = state.get('job') or {}
    if record.get('status') in ACTIVE:
        if record.get('principal') != PRINCIPAL or not re.fullmatch(r'[0-9a-f]{40}', record.get('platformRevision') or ''):
            raise ValueError('Another configuration transaction is active; finish it in the host console first')
        if interface is not None and record.get('settings', {}).get('management', {}).get('interface') != interface:
            raise ValueError('Requested interface differs from the existing reviewed update; finish or cancel that update first')
        print('Resuming update to ' + record['platformRevision'], flush=True)
    else:
        if state.get('pending'):
            raise ValueError('Activation recovery is pending; resolve it in the host console first')
        if not state.get('setupComplete'):
            raise ValueError('Finish initial NixLab setup before running #update')
        changes = management_change(manager.effective_settings(), interface, manager.settings()) if interface is not None else {}
        revision = main_revision()
        print('NixLab main: ' + revision, flush=True)
        if not changes and repair is None and already_current(manager, state, revision):
            print('Platform source and active system are already current.')
            if state.get('rebootRequired'): print('A kernel reboot is pending; reboot separately when ready.')
            return
        if changes:
            print('Management selection: ' + str(changes['management']), flush=True)
        kwargs = {'modules': repair.record['modules']} if repair else {}
        record = manager.review(changes, revision=revision, principal=PRINCIPAL, **kwargs)
        if repair: repair.reviewed(record)
        print('Building update; transaction ' + record['id'], flush=True)
        record = manager.job(record['id'])
    job_id = record['id']
    if record['status'] == 'rolling-back':
        direct.wait_for(manager, job_id, {'rolled-back'})
        raise ValueError('Previous update rolled back; rerun #update to try again')
    if record['status'] == 'building':
        record = direct.wait_for(manager, job_id, {'ready'})
    if record['status'] == 'ready':
        if interface is not None:
            expected = record.get('settings', {}).get('management', {})
            effective = record.get('effectiveSettings', {}).get('management', {})
            if expected.get('interface') != interface or any(
                    effective.get(key) != expected.get(key) for key in ('interface', 'address', 'prefixLength')):
                raise ValueError('Built management selection differs from the request; review source-owned Nix overrides before activation')
        if repair: repair.verify(record)
        plan = record.get('impactPlan')
        if not isinstance(plan, dict) or plan.get('blocked') is not False:
            raise ValueError('Update has an unavailable or blocked impact plan; inspect Changes in the host console')
        print('Proposed effects:', flush=True)
        migration = record.get('settingsMigration')
        if migration and migration['from'] != migration['to']:
            print('  Settings schema migration: ' + str(migration['from']) + ' → ' + str(migration['to']) +
                  '; previous settings/source remain available for rollback.', flush=True)
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
        if repair: repair.native_identity()
        report = diagnostics.wait_ready(record.get('effectiveSettings') or manager.effective_settings())
        for check in report['checks']:
            if check['name'] in ('Nixlab', 'Incus UI', 'Cockpit', 'Portainer') and check['ok']:
                print(check['name'] + ': ' + check['url'], flush=True)
        # The initiating SSH connection may survive while new logins and
        # portal access are broken. It cannot prove independent reachability.
        # Leave the existing rollback timer armed until a NEW session confirms.
        rt.pending_access(job_id, record)
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
    print('NixLab update activated but NOT confirmed; a new management session must confirm before the rollback deadline.', flush=True)
    if manager.status().get('rebootRequired'):
        print('A kernel reboot is pending; reboot separately when ready.', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Update installed NixLab from main using guarded build/activation.')
    parser.add_argument('--interface', help='Use this already-addressed management interface through the reviewed update')
    parser.add_argument('--repair-network', action='store_true',
                        help='At the local console, recover existing Ethernet/bridge connections before the guarded update')
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path('/etc/NIXOS').is_file():
        raise ValueError('Run on the installed NixOS host with administrator privileges')
    if not (rt.ROOT / 'flake.nix').is_file() or not rt.machine_path(rt.ROOT, 'settings').is_file():
        raise ValueError('No installed NixLab configuration found; use #install for initial installation')
    if not sys.stdin.isatty():
        raise ValueError('#update requires an interactive terminal for activation and access confirmation')
    manager = Manager()
    if args.repair_network:
        from network_repair import Repair
        repair = Repair(manager)
        success = False
        try:
            interface = repair.prepare(ask, management_change, args.interface)
            run(manager, interface=interface, repair=repair)
            success = True
        finally:
            repair.finish(success)
    else:
        run(manager, interface=args.interface)


if __name__ == '__main__':
    os.umask(0o027)
    try:
        main()
    except KeyboardInterrupt:
        sys.exit('Disconnected/interrupted: rerun the same #update command to resume. Unconfirmed activation retains automatic rollback.')
    except (ValueError, OSError, EOFError, subprocess.SubprocessError) as error:
        sys.exit('Update stopped; inspect host status before retrying. Unconfirmed activation retains automatic rollback.')
