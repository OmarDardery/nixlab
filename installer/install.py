"""Install a verified Nixlab stack on conventional NixOS, preserving the installed machine."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'admin'))
sys.path.append(str(Path(__file__).resolve().parents[1] / 'scripts'))
import runtime as rt

REPOSITORY = 'https://github.com/OmarDardery/nixlab.git'
TARGET = Path('/var/lib/nixlab/config')
ORIGINAL = Path('/etc/nixos')


def run(args, **kwargs):
    try:
        return subprocess.run(args, check=True, **kwargs)
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            detail = exc.stderr.decode(errors='replace') if isinstance(exc.stderr, bytes) else exc.stderr
            print(detail[-6000:], file=sys.stderr, flush=True)
        raise


def choose_network(interface=None, address=None):
    routes = json.loads(run(['ip', '-j', '-4', 'route', 'show', 'default'], capture_output=True, text=True).stdout)
    if not interface:
        routes.sort(key=lambda route: route.get('metric', 0))
        if not routes:
            raise ValueError('No default IPv4 route; pass --interface for the management interface')
        interface = routes[0]['dev']
    links = json.loads(run(['ip', '-j', '-4', 'address', 'show', 'dev', interface], capture_output=True, text=True).stdout)
    assigned = [a for link in links for a in link.get('addr_info', []) if a.get('scope') == 'global']
    if address:
        assigned = [a for a in assigned if a['local'] == address]
    if not assigned:
        raise ValueError('The selected management interface must already have a usable IPv4 address')
    selected = assigned[0]
    gateway = next((r.get('gateway', '') for r in routes if r.get('dev') == interface), '')
    return interface, selected['local'], selected['prefixlen'], gateway


def copy_nix_modules(source, target):
    paths = list(source.rglob('*.nix'))
    if not (source / 'configuration.nix').is_file() or not (source / 'hardware-configuration.nix').is_file():
        raise ValueError('/etc/nixos must contain configuration.nix and hardware-configuration.nix')
    # Secret files, private keys and unrelated assets never enter the flake source.
    for path in paths:
        if path.is_symlink():
            raise ValueError('Resolve configuration symlinks before conversion: ' + str(path))
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def copy_embedded_checkout(source, target, repository, revision=None):
    """Accept either the packaged source tree or a launcher's Git checkout."""
    if not (source / 'flake.nix').is_file() or not (source / 'flake.lock').is_file():
        raise ValueError('Bundled checkout needs flake.nix and flake.lock')
    if (source / '.git').exists():
        run(['git', 'clone', '--no-hardlinks', str(source), str(target)])
        run(['git', '-C', str(target), 'remote', 'set-url', 'origin', repository])
        return
    shutil.copytree(source, target, symlinks=False)
    if revision and len(revision) == 40 and all(c in '0123456789abcdef' for c in revision):
        import upgrades
        rt.write_json(target / 'platform-release.json', {'version': upgrades.version(source), 'revision': revision, 'repository': repository})
    run(['git', '-C', str(target), 'init', '-q', '-b', 'nixlab-iso'])
    run(['git', '-C', str(target), 'add', '.'])
    run(['git', '-C', str(target), '-c', 'user.name=Nixlab ISO', '-c',
         'user.email=installer@nixlab.invalid', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-q', '-m', 'Initialize bundled Nixlab source'])
    run(['git', '-C', str(target), 'remote', 'add', 'origin', repository])


def parser_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, help='Reviewed source supplied by the Nix launcher')
    parser.add_argument('--repo', default=REPOSITORY)
    parser.add_argument('--ref', default='main')
    parser.add_argument('--interface')
    parser.add_argument('--address', help='An IPv4 address already assigned to the selected interface')
    parser.add_argument('--trusted-cidr', action='append')
    parser.add_argument('--resume', action='store_true', help='Compatibility flag; retries resume automatically')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--switch', dest='switch', action='store_true', help='Install the full stack (default)')
    mode.add_argument('--wizard', dest='switch', action='store_false', help='Only bootstrap the optional web wizard')
    # Retain the existing firstboot appliance route; plain installed hosts use
    # the full local CLI workflow. ISO composition and boot code are unchanged.
    parser.set_defaults(switch=not Path('/etc/systemd/system/nixlab-firstboot.service').exists())
    parser.add_argument('--ssh-key', action='append', default=[], type=Path, help='Client public key file, never a private key')
    parser.add_argument('--ssh-password-login', action='store_true', help='Explicitly retain SSH password login for existing non-root accounts')
    parser.add_argument('--guest-subnet', help='Unused private IPv4 /24')
    parser.add_argument('--no-apps', action='store_true', help='Skip the Docker/Portainer VM')
    parser.add_argument('--adopt-incus', action='store_true', help='Back up existing Incus guests and add an isolated Nixlab namespace')
    parser.add_argument('--no-update', action='store_true', help='Keep the current platform source on retry')
    return parser, parser.parse_args()


def evaluate(root, attribute):
    return json.loads(run(['nix', '--extra-experimental-features', 'nix-command flakes', 'eval', '--json',
                           'path:' + str(root) + '#' + attribute], capture_output=True, text=True).stdout)


def validate_keys(keys):
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w') as key:
        for value in keys:
            key.seek(0); key.truncate(); key.write(value + '\n'); key.flush()
            run(['ssh-keygen', '-l', '-f', key.name], stdout=subprocess.DEVNULL)


def validate_kvm(vendor):
    run(['modprobe', 'kvm_amd' if vendor == 'amd' else 'kvm_intel'])
    import fcntl
    with open('/dev/kvm', 'rb+', buffering=0) as kvm:
        if fcntl.ioctl(kvm, 0xAE00, 0) != 12:
            raise ValueError('Hardware virtualization is unavailable; enable it in firmware')


def setup_password():
    path = rt.STATE / 'setup-password'
    auth = rt.STATE / 'auth.json'
    if not auth.exists():
        if not path.exists():
            rt.atomic(path, secrets.token_urlsafe(24) + '\n')
        rt.write_json(auth, rt.password_record(path.read_text().strip()), mode=0o640)
    if path.is_file() and rt.password_matches(path.read_text().strip(), rt.read_json(auth)):
        return path.read_text().strip()
    return None


def publish_etc():
    backup = Path('/etc/nixos.pre-nixlab')
    if ORIGINAL.is_symlink() and ORIGINAL.resolve() == TARGET.resolve():
        return
    if ORIGINAL.exists():
        if backup.exists():
            raise ValueError('Both /etc/nixos and /etc/nixos.pre-nixlab exist; inspect them before linking the managed config')
        ORIGINAL.rename(backup)
        backup.chmod(0o700)
    ORIGINAL.symlink_to(TARGET)


def summarize(settings, password=None, record=None):
    import diagnostics
    address = diagnostics.management_address(settings)
    if not settings['management']['configure']:
        print('Preserving current addressing: ' + address + '. DHCP is retained; no static address was assigned.')
    print('Nixlab: https://' + address + ':' + str(settings['setup']['port']) + ' (username: admin)')
    if password:
        print('Setup password: ' + password)
    else:
        print('Use your existing setup password; recover with sudo nixlab-admin reset-password.')
    if settings['ready']:
        print('Incus: https://' + address + ':8443 (enroll your browser certificate in the Incus UI)')
        if settings['cockpit']['enable']:
            print('Cockpit: https://' + address + ':' + str(settings['cockpit']['port']) + ' (existing non-root Linux account/password)')
            import local_access
            operator_password = local_access.operator_password()
            if operator_password:
                print('Verified Cockpit username: operator; password: ' + operator_password)
        if settings['apps']['enable']:
            import portainer
            print('Portainer: https://' + address + ':' + str(settings['apps']['portainerPort']))
            credential = portainer.credentials(settings)
            if credential:
                print('Verified Portainer username: ' + credential['username'] + '; password: ' + credential['password'])
            else:
                print('Portainer has an existing account. The bootstrap file is not a verified current password.')
                print('Recover access: sudo nixlab portainer reset-password')
        print('All enabled management endpoints passed health checks. No disks were formatted.')
        if (record or {}).get('rebootRequired'):
            print('Reboot to use the new kernel; then run sudo nixlab doctor.')
    fingerprint = run(['openssl', 'x509', '-in', str(rt.STATE / 'ca.crt'), '-noout', '-fingerprint', '-sha256'], capture_output=True, text=True).stdout.strip()
    print('Local management CA: ' + fingerprint)
    if not settings['adminKeys']:
        print('No administrator key enrolled. Local console access remains available.')
        print('Enroll your laptop PUBLIC key later: sudo nixlab enroll-key /path/to/laptop.pub')
    print('SSH password login: ' + ('enabled for existing non-root accounts' if settings.get('access', {}).get('sshPasswordLogin') else 'disabled'))
    print('Later rebuilds: sudo nixos-rebuild switch --flake path:/etc/nixos#hypervisor')
    print('Diagnostics: sudo nixlab doctor')


def main():
    import tempfile
    import direct
    import workflow
    from model import inventory, merge
    from controller import Manager
    parser, args = parser_args()
    if os.geteuid() != 0 or not Path('/etc/NIXOS').exists():
        parser.error('Run as root on an installed NixOS system')
    if Path('/etc/nixlab-iso-revision').exists():
        parser.error('This is live installation media. Use nixlab-stage-install on the mounted target, then nixos-install')
    if os.uname().machine != 'x86_64':
        parser.error('This release supports Intel/AMD x86_64 NixOS hosts')
    remote = workflow.remote_session()
    if TARGET.exists() and not rt.machine_path(TARGET, 'installation').is_file():
        parser.error('Existing checkout has no intact installation inventory; inspect it before proceeding (no configuration changed)')
    if TARGET.exists():
        manager = Manager()
        current = manager.status().get('job') or {}
        if current.get('status') in ('building', 'ready', 'applying', 'awaiting-confirmation', 'rolling-back'):
            if not args.switch:
                parser.error('A full installation transaction is active; rerun without --wizard to resume it')
            print('Resuming transaction ' + current['id'], flush=True)
            direct.resume(manager, current)
            settings = manager.settings()
            publish_etc()
            summarize(settings, setup_password(), current)
            return
    interface, address, prefix, gateway = choose_network(args.interface, args.address)
    trusted = args.trusted_cidr or [str(ipaddress.ip_interface(f'{address}/{prefix}').network)]
    if os.environ.get('SSH_CONNECTION'):
        client = ipaddress.ip_address(os.environ['SSH_CONNECTION'].split()[0])
        if client.version != 4:
            parser.error('Use the local console for an IPv6-only SSH session')
        trusted = sorted(set(trusted + [str(client) + '/32']))
    for network in trusted:
        if ipaddress.ip_network(network, strict=True).version != 4:
            parser.error('Use trusted IPv4 CIDRs for this release')
    if not any(ipaddress.ip_address(address) in ipaddress.ip_network(network) for network in trusted):
        trusted.append(address + '/32')  # local endpoint probes also need access
    host = inventory()
    if host['cpuVendor'] not in ('intel', 'amd'):
        parser.error('CPU vendor could not be identified safely')
    existing = TARGET.exists()
    adoption = None
    if not existing and Path('/var/lib/incus/database').exists():
        adoption = workflow.incus_inventory()
        print('Existing Incus: ' + str(len(adoption['instances'])) + ' guests. Adoption creates separate resources and backs up existing guests.')
        if not args.adopt_incus:
            if not sys.stdin.isatty() or input('Back up and adopt this Incus host? [y/N] ').strip().lower() not in ('y', 'yes'):
                parser.error('Adoption was not selected; no changes made. Use --adopt-incus after reviewing existing guests.')
    # All source changes and evaluations happen in a disposable planning tree.
    # Publish only after key/network/Wi-Fi/KVM/adoption validation succeeds.
    with tempfile.TemporaryDirectory(prefix='nixlab-plan-') as temporary:
        stage = Path(temporary) / 'config'
        incoming = args.checkout
        changed = False
        if existing:
            source_digest = rt.digest_source(TARGET)
            changed, edits = workflow.stage_update(TARGET, None if args.no_update else incoming, stage,
                                                    rt.STATE / 'platform-baseline.json')
            if edits:
                print('Preserving local platform edits; source update skipped: ' + ', '.join(edits))
            default = merge(evaluate(stage, 'nixlabDefaults'), rt.read_json(rt.machine_path(stage, 'settings')))
        else:
            if not ORIGINAL.is_dir() or not (ORIGINAL / 'configuration.nix').is_file() or not (ORIGINAL / 'hardware-configuration.nix').is_file():
                parser.error('A conventional /etc/nixos installation with both configuration files is required')
            if Path('/etc/nixos.pre-nixlab').exists():
                parser.error('/etc/nixos.pre-nixlab already exists; inspect it before conversion')
            if incoming:
                copy_embedded_checkout(incoming.resolve(), stage, args.repo, args.ref)
            else:
                run(['git', 'clone', '--single-branch', '--branch', args.ref, args.repo, str(stage)])
            rt.write_json(stage / 'machine/format.json', {'version': 1})
            copy_nix_modules(ORIGINAL.resolve(), rt.machine_path(stage, 'installed'))
            default = evaluate(stage, 'nixlabDefaults')
            default.update(system='x86_64-linux', cpuVendor=host['cpuVendor'], hostName=socket.gethostname().split('.')[0])
            default['setup']['enable'] = True
            wireless = Path('/sys/class/net', interface, 'wireless').exists()
            default['radios']['wifi'].update(enable=wireless, interface=interface if wireless else '')
            zone = str(Path('/etc/localtime').resolve())
            if '/zoneinfo/' in zone:
                default['maintenance']['timeZone'] = zone.split('/zoneinfo/', 1)[1]
            memory_kib = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))
            default['apps']['cpu'] = str(max(1, min(2, (os.cpu_count() or 2) // 2)))
            default['apps']['memory'] = str(max(1, min(2, memory_kib // 1024 // 1024 // 2))) + 'GiB'
        if not default['management']['configure']:
            default['management'].update(interface=interface, address=address, prefixLength=prefix, gateway=gateway, trustedCIDRs=trusted)
        if adoption:
            workflow.adoption_settings(default, adoption)
        rt.write_json(rt.machine_path(stage, 'settings'), default)
        overview = evaluate(stage, 'nixlabOverview')
        # Reuse client keys already authorized for the installer account. Server
        # host keys and arbitrary public-key files are never enrollment sources.
        for home in (Path('/root'), Path('/home') / os.environ.get('SUDO_USER', '')):
            path = home / '.ssh/authorized_keys'
            if path.is_file():
                overview['adminKeys'] = overview.get('adminKeys', []) + [line.strip()
                    for line in path.read_text().splitlines() if line.startswith(('ssh-ed25519 ', 'ssh-rsa ', 'ecdsa-sha2-'))]
        installation = rt.read_json(rt.machine_path(stage, 'installation'), {})
        prior = {}
        if existing:
            active = rt.read_json(rt.STATE / 'active.json', {})
            prior = rt.read_json(rt.STATE / 'jobs' / active.get('id', '-') / 'job.json', {})
        previous_guests = default['guests'] if default['ready'] else prior.get('settings', {}).get('guests') if prior.get('previousSystem') else None
        candidate = None
        if args.switch:
            candidate = direct.prepare(default, overview, host, args.ssh_key, args.guest_subnet,
                not args.no_apps if not default['ready'] else default['apps']['enable'], previous_guests=previous_guests,
                remote=remote, password_login=args.ssh_password_login or default.get('access', {}).get('sshPasswordLogin', False))
            validate_keys(candidate['adminKeys'])
            validate_kvm(candidate['cpuVendor'])
            import local_access
            if not local_access.cockpit_accounts():
                candidate['cockpit']['managePassword'] = True
                print('No usable local administrator password was found; a stable operator login will be provisioned for Cockpit.')
            print('SSH policy after activation: ' + ('password login retained for existing non-root accounts' if candidate['access']['sshPasswordLogin'] else 'password login disabled; keys/console remain available'))
        installation.update(interface=interface, trustedCIDRs=trusted, legacyWpaProfiles=overview['wpaProfiles'])
        if not existing:
            installation.update(installedAt=time.time(), originalSystem=str(Path('/run/current-system').resolve()), repository=args.repo, ref=args.ref)
        rt.write_json(rt.machine_path(stage, 'installation'), installation)
        # Backups complete before source publication or activation.
        if adoption:
            destination = rt.STATE / 'incus-adoption' / str(time.time_ns())
            workflow.backup_incus(adoption, destination)
            print('Incus backups: ' + str(destination))
        rt.STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not existing:
            original_backup = rt.STATE / 'original-config'
            if not original_backup.exists():
                shutil.copytree(ORIGINAL.resolve(), original_backup, symlinks=True)
                original_backup.chmod(0o700)
            TARGET.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Stage on the same filesystem, then rename: interruption cannot leave a partial checkout.
            publishing = TARGET.with_name('.config-installing')
            if publishing.exists():
                shutil.rmtree(publishing)
            shutil.copytree(stage, publishing)
            publishing.chmod(0o700)
            publishing.rename(TARGET)
            run(['nix-store', '--add-root', str(rt.STATE / 'original-system'), '--indirect', '--realise', installation['originalSystem']])
            rt.write_json(rt.STATE / 'platform-baseline.json', workflow.hashes(TARGET))
        else:
            with rt.locked(blocking=False):
                active = rt.read_json(rt.STATE / 'active.json', {})
                job = rt.read_json(rt.STATE / 'jobs' / active.get('id', '-') / 'job.json', {})
                if job.get('status') in ('building', 'ready', 'applying', 'awaiting-confirmation', 'rolling-back'):
                    raise ValueError('A configuration transaction started during planning; retry after it finishes')
                if rt.digest_source(TARGET) != source_digest:
                    raise ValueError('Source changed during planning; retry to preserve the latest local changes')
                checkpoint = rt.STATE / ('source-before-install-' + str(time.time_ns()))
                rt.snapshot(checkpoint, TARGET)
                if changed:
                    import upgrades
                    upgrades.publish(stage, TARGET)
                    rt.write_json(rt.STATE / 'platform-baseline.json', workflow.hashes(TARGET))
                rt.write_json(rt.machine_path(TARGET, 'installation'), installation)
                rt.write_json(rt.machine_path(TARGET, 'settings'), default)
    password = setup_password()
    pin = rt.STATE / 'original-system'
    if not pin.exists() and installation.get('originalSystem'):
        run(['nix-store', '--add-root', str(pin), '--indirect', '--realise', installation['originalSystem']])
    if args.switch and existing and prior.get('status') == 'confirmed' and candidate == Manager().settings() \
            and rt.digest_source(TARGET) == prior.get('candidateDigest'):
        import diagnostics
        diagnostics.wait_ready(candidate)
        publish_etc()
        summarize(candidate, password, prior)
        return
    if not (rt.STATE / 'tls.key').exists():
        rt.issue_tls(address, default['hostName'])
    previous = str(Path('/run/current-system').resolve())
    try:
        record = None
        if args.switch:
            _, record = direct.activate(candidate)
            settings = rt.read_json(rt.machine_path(TARGET, 'settings'))
        else:
            run(['nixos-rebuild', 'build', '--flake', 'path:' + str(TARGET) + '#bootstrap'])
            run(['nixos-rebuild', 'switch', '--flake', 'path:' + str(TARGET) + '#bootstrap'])
            settings = default
    except Exception as original:
        if not args.switch:
            try:
                run(['nix-env', '--profile', '/nix/var/nix/profiles/system', '--set', previous])
                run([previous + '/bin/switch-to-configuration', 'switch'])
            except Exception as recovery:
                print('Rollback also failed: ' + str(recovery), file=sys.stderr)
        raise original
    rt.prepare_state_access()
    publish_etc()
    summarize(settings, password, record)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        import fcntl
        from locking import open_lock
        with open_lock(Path('/run/lock/nixlab-install.lock')) as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another Nixlab installer is already running')
            main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print('\nINSTALLATION FAILED: ' + str(exc), file=sys.stderr, flush=True)
        try:
            report = {'error': str(exc), 'time': time.time()}
            rt.write_json(rt.STATE / 'installation-failure.json', report)
            print('Failure report: ' + str(rt.STATE / 'installation-failure.json'), file=sys.stderr)
            print('Retry the same install command; completed work is detected automatically.', file=sys.stderr)
            print('Diagnostics: sudo nixlab doctor (once host tools are installed).', file=sys.stderr)
        except OSError:
            pass
        sys.exit(1)
