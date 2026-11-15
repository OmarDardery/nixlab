"""Small, local console observations; never a health or restore qualification."""
import ipaddress
import json
from pathlib import Path
import subprocess


def command(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=1).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def clean(value):
    return ''.join(c for c in str(value) if c.isprintable())[:160]


def usable_ipv4(value):
    try:
        address = ipaddress.ip_address(value)
        return address.version == 4 and not any((address.is_loopback, address.is_link_local,
                                               address.is_unspecified, address.is_multicast))
    except ValueError:
        return False


def services(names):
    result = {}
    output = command(['systemctl', 'show', '--no-pager', '--property=Id,LoadState,ActiveState,Result,ExecMainExitTimestamp', *names])
    for block in output.strip().split('\n\n'):
        fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        if fields.get('Id'):
            result[fields['Id']] = fields
    return result


def lines(settings, links, units, current=None, default=None, kernel=None, booted=None):
    management = settings.get('management', {})
    interface = management.get('interface', '')
    link = next((link for link in links if link.get('ifname') == interface), {})
    addresses = [item.get('local', '') for item in link.get('addr_info', [])
                 if item.get('family') == 'inet' and item.get('scope') == 'global']
    addresses = [value for value in addresses if usable_ipv4(value)]
    address = management.get('address') if management.get('address') in addresses else None
    if address is None and not management.get('configure') and len(addresses) == 1:
        address = addresses[0]
    if address:
        yield 'Management: ' + clean(interface) + '  ' + address
        for section, label, port in (
            ('setup', 'NixLab', 'port'), ('cockpit', 'Cockpit', 'port'), ('apps', 'Portainer', 'portainerPort')):
            option = settings.get(section, {})
            if option.get('enable') and type(option.get(port)) is int:
                yield f'{label}: https://{address}:{option[port]}'
        if settings.get('ready'):
            yield f'Incus: https://{address}:8443'
    else:
        yield 'Management: ' + clean(interface or 'not configured') + ' — no unambiguous configured IPv4 address'
        if link.get('master'):
            yield 'Address belongs on bridge ' + clean(link['master']) + '; review management interface'
    labels = [('incus.service', 'Incus'), ('nixlab-ui.service', 'Web'), ('nginx.service', 'Proxy')]
    if settings.get('apps', {}).get('enable'):
        labels.append(('nixlab-apps-onboarding.service', 'Apps setup'))
    states = []
    for name, label in labels:
        unit = units.get(name, {})
        state = unit.get('ActiveState', 'unknown') if unit.get('LoadState') == 'loaded' else 'unavailable'
        states.append(label + '=' + clean(state))
    yield 'Services: ' + '  '.join(states)
    if settings.get('backup', {}).get('enable'):
        backup = units.get('restic-backups-nixlab.service', {})
        timestamp = backup.get('ExecMainExitTimestamp')
        if timestamp and backup.get('LoadState') == 'loaded':
            yield 'Backup last run: ' + clean(timestamp) + '  result=' + clean(backup.get('Result', 'unknown'))
        else:
            yield 'Backup: configured; no recorded run available'
        yield 'Restore verification: not checked by this summary'
    else:
        yield 'Backups: disabled'
    if current and default:
        yield ('Boot: current configuration is the boot default' if current == default
               else 'Boot: current configuration differs from boot default (test/unconfirmed)')
    else:
        yield 'Boot: unknown'
    if kernel and booted and kernel != booted:
        yield 'Reboot: a different kernel is running'
    yield 'Check endpoints: sudo nixlab doctor | Update: nixlab-update'


def target(path):
    path = Path(path)
    return str(path.resolve()) if path.exists() else None


def main():
    try:
        settings = json.loads(Path('/etc/nixlab/effective-settings.json').read_text())
        if not isinstance(settings, dict): raise ValueError('Invalid settings')
    except (OSError, ValueError):
        print('NixLab: configuration unavailable; run sudo nixlab doctor')
        return
    try:
        links = json.loads(command(['ip', '-j', 'address', 'show']))
        if not isinstance(links, list): links = []
    except ValueError:
        links = []
    names = ['incus.service', 'nixlab-ui.service', 'nginx.service', 'nixlab-apps-onboarding.service']
    if settings.get('backup', {}).get('enable'):
        names.append('restic-backups-nixlab.service')
    for line in lines(settings, links, services(names),
                      target('/run/current-system'), target('/nix/var/nix/profiles/system'),
                      target('/run/current-system/kernel'), target('/run/booted-system/kernel')):
        print(line)


if __name__ == '__main__':
    main()
