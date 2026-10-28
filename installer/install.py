"""Convert a conventional fresh NixOS install into an authenticated setup appliance.
The first switch preserves the installed configuration; the web wizard performs conversion.
"""
import argparse
import datetime
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
import runtime as rt

REPOSITORY = 'https://github.com/OmarDardery/nixlab.git'
TARGET = Path('/var/lib/nixlab/config')
ORIGINAL = Path('/etc/nixos')


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


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
    """The ISO carries a filtered source tree, not a Git working copy."""
    if not (source / 'flake.nix').is_file() or not (source / 'flake.lock').is_file():
        raise ValueError('Bundled checkout needs flake.nix and flake.lock')
    shutil.copytree(source, target, symlinks=False)
    if revision and len(revision) == 40 and all(c in '0123456789abcdef' for c in revision):
        rt.write_json(target / 'platform-release.json', {'version': '0.1.0-rc1', 'revision': revision, 'repository': repository})
    run(['git', '-C', str(target), 'init', '-q', '-b', 'nixlab-iso'])
    run(['git', '-C', str(target), 'add', '.'])
    run(['git', '-C', str(target), '-c', 'user.name=Nixlab ISO', '-c',
         'user.email=installer@nixlab.invalid', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-q', '-m', 'Initialize bundled Nixlab source'])
    run(['git', '-C', str(target), 'remote', 'add', 'origin', repository])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', type=Path, help='Reviewed checkout supplied by the standalone launcher')
    parser.add_argument('--repo', default=REPOSITORY)
    parser.add_argument('--ref', default='main')
    parser.add_argument('--interface')
    parser.add_argument('--address', help='An address already assigned to the selected interface')
    parser.add_argument('--trusted-cidr', action='append')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path('/etc/NIXOS').exists():
        parser.error('Run as root on an installed NixOS system')
    if Path('/etc/nixlab-iso-revision').exists():
        parser.error('This is live installation media. Use nixlab-stage-install on the mounted target, then nixos-install')
    if os.uname().machine != 'x86_64':
        parser.error('This release supports Intel/AMD x86_64 NixOS hosts')
    if TARGET.exists() and not args.resume:
        parser.error('Nixlab already has a checkout. Use --resume after a failed bootstrap; existing settings are never overwritten')
    if not TARGET.exists() and Path('/var/lib/incus/database').exists():
        parser.error('An existing Incus database needs an explicit migration; this installer targets fresh NixOS hosts')
    interface, address, prefix, gateway = choose_network(args.interface, args.address)
    trusted = args.trusted_cidr or [str(ipaddress.ip_interface(f'{address}/{prefix}').network)]
    for network in trusted:
        if ipaddress.ip_network(network).version != 4:
            parser.error('Use trusted IPv4 CIDRs for this release')
    cpu = Path('/proc/cpuinfo').read_text()
    vendor = 'amd' if 'AuthenticAMD' in cpu else 'intel' if 'GenuineIntel' in cpu else ''
    if not vendor:
        parser.error('CPU vendor could not be identified safely')
    if not args.resume:
        # Validate source shape before making any configuration changes.
        if not ORIGINAL.is_dir() or not (ORIGINAL / 'configuration.nix').is_file() or not (ORIGINAL / 'hardware-configuration.nix').is_file():
            parser.error('A conventional /etc/nixos installation with both configuration files is required')
        for path in ORIGINAL.rglob('*.nix'):
            if path.is_symlink():
                parser.error('Configuration symlinks need an explicit import plan; no changes made')
        if Path('/etc/nixos.pre-nixlab').exists():
            parser.error('/etc/nixos.pre-nixlab already exists; inspect it before another conversion')
        rt.STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copytree(ORIGINAL.resolve(), rt.STATE / 'original-config', symlinks=True)
        (rt.STATE / 'original-config').chmod(0o700)
        TARGET.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if args.checkout:
            if (args.checkout / '.git').exists():
                run(['git', 'clone', '--no-hardlinks', str(args.checkout.resolve()), str(TARGET)])
                run(['git', '-C', str(TARGET), 'remote', 'set-url', 'origin', args.repo])
            else:
                copy_embedded_checkout(args.checkout.resolve(), TARGET, args.repo, args.ref)
        else:
            run(['git', 'clone', '--single-branch', '--branch', args.ref, args.repo, str(TARGET)])
        TARGET.chmod(0o700)
        rt.write_json(TARGET / 'machine/format.json', {'version': 1})
        copy_nix_modules(ORIGINAL.resolve(), rt.machine_path(TARGET, 'installed'))
        default = json.loads(run(['nix', '--extra-experimental-features', 'nix-command flakes', 'eval', '--json',
                                 'path:' + str(TARGET) + '#nixlabDefaults'], capture_output=True, text=True).stdout)
        default['setup']['enable'] = True
        default['system'] = 'x86_64-linux'; default['cpuVendor'] = vendor
        default['hostName'] = socket.gethostname().split('.')[0]
        default['management'].update(interface=interface, address=address, prefixLength=prefix, gateway=gateway, trustedCIDRs=trusted)
        # Keep the current Wi-Fi uplink active; user makes its final choice in the wizard.
        wireless = Path('/sys/class/net', interface, 'wireless').exists()
        default['radios']['wifi'].update(enable=wireless, interface=interface if wireless else '')
        zone = Path('/etc/localtime').resolve()
        if '/zoneinfo/' in str(zone):
            default['maintenance']['timeZone'] = str(zone).split('/zoneinfo/', 1)[1]
        keys = []
        for home in (Path('/root'), Path('/home') / os.environ.get('SUDO_USER', '')):
            path = home / '.ssh/authorized_keys'
            if path.is_file():
                keys.extend(line.strip() for line in path.read_text().splitlines() if line.startswith(('ssh-ed25519 ', 'ssh-rsa ', 'ecdsa-sha2-')))
        default['adminKeys'] = sorted(set(keys))
        memory_kib = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))
        default['apps']['cpu'] = str(max(1, min(2, (os.cpu_count() or 2) // 2)))
        default['apps']['memory'] = str(max(1, min(2, memory_kib // 1024 // 1024 // 2))) + 'GiB'
        rt.write_json(rt.machine_path(TARGET, 'settings'), default)
        rt.write_json(rt.machine_path(TARGET, 'installation'), {'interface': interface, 'trustedCIDRs': trusted, 'installedAt': time.time(),
                       'originalSystem': str(Path('/run/current-system').resolve()), 'repository': args.repo, 'ref': args.ref})
        # Pin the original generation against garbage collection for console recovery.
        run(['nix-store', '--add-root', str(rt.STATE / 'original-system'), '--indirect', '--realise', str(Path('/run/current-system').resolve())])
    elif not (rt.machine_path(TARGET, 'installation')).is_file():
        parser.error('Resume requires an intact installation inventory')
    settings = rt.read_json(rt.machine_path(TARGET, 'settings'))
    if settings['ready']:
        parser.error('Initial setup is already complete; use the web interface for changes')
    password = None
    if not (rt.STATE / 'auth.json').exists():
        password = secrets.token_urlsafe(24)
        rt.write_json(rt.STATE / 'auth.json', rt.password_record(password), mode=0o640)
    if not (rt.STATE / 'tls.key').exists():
        rt.issue_tls(settings['management']['address'], settings['hostName'])
    previous = str(Path('/run/current-system').resolve())
    try:
        # Bootstrap preserves the installed bootloader/users/network and adds setup only.
        run(['nixos-rebuild', 'build', '--flake', 'path:' + str(TARGET) + '#bootstrap'])
        overview = json.loads(run(['nix', '--extra-experimental-features', 'nix-command flakes', 'eval', '--json',
                                   'path:' + str(TARGET) + '#nixlabOverview'], capture_output=True, text=True).stdout)
        installation = rt.read_json(rt.machine_path(TARGET, 'installation'))
        installation['legacyWpaProfiles'] = overview['wpaProfiles']
        rt.write_json(rt.machine_path(TARGET, 'installation'), installation)
        run(['nixos-rebuild', 'switch', '--flake', 'path:' + str(TARGET) + '#bootstrap'])
    except Exception:
        if password:
            print('Initial setup password for a retry (shown once): ' + password, file=sys.stderr, flush=True)
        run(['nix-env', '--profile', '/nix/var/nix/profiles/system', '--set', previous])
        run([previous + '/bin/switch-to-configuration', 'switch'])
        raise
    import grp
    gid = grp.getgrnam('nixlab-ui').gr_gid
    os.chown(rt.STATE, -1, gid); rt.STATE.chmod(0o710)
    os.chown(rt.STATE / 'auth.json', -1, gid)
    (rt.STATE / 'auth.json').chmod(0o640)
    # Keep a directly restorable original /etc/nixos; standard rebuilds now see managed code.
    if not ORIGINAL.is_symlink():
        ORIGINAL.rename('/etc/nixos.pre-nixlab')
        Path('/etc/nixos.pre-nixlab').chmod(0o700)
        ORIGINAL.symlink_to(TARGET)
    run(['systemctl', 'restart', 'nixlab-admin-backend.service', 'nixlab-ui.service'])
    fingerprint = run(['openssl', 'x509', '-in', str(rt.STATE / 'ca.crt'), '-noout', '-fingerprint', '-sha256'], capture_output=True, text=True).stdout.strip()
    print('\nNixlab setup is ready: https://' + settings['management']['address'] + ':' + str(settings['setup']['port']))
    print('Username: admin')
    if password:
        print('Initial password (shown once): ' + password)
    else:
        print('Use your existing setup password. Root can reset it with nixlab-admin reset-password.')
    print('Verify the local CA fingerprint before trusting its certificate: ' + fingerprint)
    print('Finish the required wizard, build/review, activate, then confirm access. No disks have been formatted.')


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
