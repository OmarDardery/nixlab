"""Validate explicit inventory before Incus can change its bridge or pools."""
import ipaddress
import json
from pathlib import Path
import re
import sys


def validate(site):
    m, g = site['management'], site['guests']
    interface = m['interface']
    if not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,15}', interface):
        raise ValueError('Invalid or missing management interface')
    management = ipaddress.ip_interface(f"{m['address']}/{m['prefixLength']}")
    subnet = ipaddress.ip_network(g['cidr'], strict=True)
    bridge = ipaddress.ip_interface(g['gatewayCIDR'])
    app = ipaddress.ip_address(g['appsAddress'])
    if management.version != 4 or subnet.version != 4 or bridge.version != 4:
        raise ValueError('The default topology requires explicit IPv4 inventory')
    if management.network.overlaps(subnet):
        raise ValueError('Guest subnet overlaps the host management subnet')
    if bridge.network != subnet or bridge.ip in (subnet.network_address, subnet.broadcast_address):
        raise ValueError('Bridge must have a usable address inside the guest subnet')
    if app not in subnet or app in (bridge.ip, subnet.network_address, subnet.broadcast_address):
        raise ValueError('Apps IP must be usable and distinct from the bridge')
    low, high = map(ipaddress.ip_address, g['dhcpRange'].split('-'))
    if low not in subnet or high not in subnet or int(low) > int(high):
        raise ValueError('Invalid DHCP range')
    if any(int(low) <= int(ip) <= int(high) for ip in (bridge.ip, app, subnet.network_address, subnet.broadcast_address)):
        raise ValueError('DHCP range overlaps a reserved address')
    reservations = {bridge.ip, app}
    identities = set()
    for instance in site.get('instances', []):
        identity = (instance['project'], instance['name'])
        if identity in identities or (site.get('apps', {}).get('enable', False) and identity == ('lab', 'apps')):
            raise ValueError('Duplicate declared instance name/project')
        identities.add(identity)
        for device in instance.get('devices', {}).values():
            if device.get('network') != 'incusbr0' or not device.get('ipv4.address'):
                continue
            address = ipaddress.ip_address(device['ipv4.address'])
            if (address not in subnet or address in reservations or
                address in (subnet.network_address, subnet.broadcast_address) or
                int(low) <= int(address) <= int(high)):
                raise ValueError('Guest reservation conflicts with another address or DHCP range')
            reservations.add(address)
    for cidr in m['trustedCIDRs']:
        ipaddress.ip_network(cidr, strict=True)
    if m['configure']:
        if ipaddress.ip_address(m['gateway']) not in management.network:
            raise ValueError('Management gateway is outside the configured subnet')
        if not m['dns']:
            raise ValueError('Explicit management DNS servers are required')
    storage = site['storage']
    if storage['driver'] == 'zfs':
        if not storage['confirmedExistingZfsDataset'] or not re.fullmatch(r'[0-9a-fA-F]{8}', storage['hostId']):
            raise ValueError('ZFS needs explicit confirmation and a unique 8-digit hostId')
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.:-]*(/[a-zA-Z0-9_.:-]+)+', storage['source']):
            raise ValueError('ZFS source must name an existing child dataset, never a disk or pool root')
    elif storage['driver'] == 'dir':
        primary_sources = [m['path'] + '/incus' for m in site.get('mounts', []) if m.get('role') == 'guests']
        if not storage['source'].startswith('/var/lib/incus/storage-pools/') and storage['source'] not in primary_sources:
            raise ValueError('Directory storage must live in the dedicated Incus storage-pools tree')
    else:
        raise ValueError('Unsupported storage driver')

if __name__ == '__main__':
    validate(json.loads(Path(sys.argv[1]).read_text()))
