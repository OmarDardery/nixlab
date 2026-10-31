"""Validate host prerequisites shared by the portal and activation preflight."""
import ipaddress
import re


def interface(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', value) is not None and value != 'lo'


def records(values, fields, label):
    if not isinstance(values, list) or len(values) > 64:
        raise ValueError(label + ' must contain at most 64 records')
    for item in values:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError('Invalid ' + label + ' fields')
    return values


def validate(site, host=None):
    c = site.get('connectivity', {})
    bridges = records(c.get('bridges', []), {'name', 'interfaces'}, 'Ethernet bridges')
    vlans = records(c.get('vlans', []), {'name', 'interface', 'id'}, 'VLANs')
    names, ports = set(), set()
    physical = {i['name']: i for i in (host or {}).get('interfaces', [])}
    for item in bridges + vlans:
        if not interface(item['name']) or item['name'] in names or item['name'].startswith(('incus', 'veth', 'tap')):
            raise ValueError('Host interface names must be unique and cannot use Incus-reserved names')
        names.add(item['name'])
    vlan_names = {v['name'] for v in vlans}
    for vlan in vlans:
        if not interface(vlan['interface']) or vlan['interface'] in names:
            raise ValueError('VLAN parents must be physical Ethernet interfaces')
        if type(vlan['id']) is not int or not 1 <= vlan['id'] <= 4094:
            raise ValueError('VLAN ID must be between 1 and 4094')
        if host is not None and (vlan['interface'] not in physical or physical[vlan['interface']].get('wireless')
                                 or physical[vlan['interface']].get('kind', 'ethernet') != 'ethernet'):
            raise ValueError('Select a discovered Ethernet VLAN parent')
    if len({(v['interface'], v['id']) for v in vlans}) != len(vlans):
        raise ValueError('Duplicate VLAN parent/tag')
    for bridge in bridges:
        if not isinstance(bridge['interfaces'], list) or not bridge['interfaces']:
            raise ValueError('A LAN bridge requires at least one Ethernet/VLAN port')
        for port in bridge['interfaces']:
            if not interface(port) or port in ports or port in names - vlan_names:
                raise ValueError('An interface can belong to only one bridge; bridge nesting is unsupported')
            if host is not None and port not in vlan_names and (port not in physical or physical[port].get('wireless')
                                                               or physical[port].get('kind', 'ethernet') != 'ethernet'):
                raise ValueError('Only discovered Ethernet interfaces or declared VLANs can be bridged')
            ports.add(port)
    m = site['management']
    if m['interface'] in ports:
        raise ValueError('Move management addressing onto the bridge before enslaving its interface')
    if names and m['interface'] in names and not m['configure']:
        raise ValueError('A declared management bridge/VLAN needs explicit addressing')
    if host is not None:
        for name in names & set(physical):
            actual = physical[name].get('kind')
            expected = 'vlan' if name in vlan_names else 'bridge'
            if actual and actual != expected:
                raise ValueError('Declared host interface conflicts with discovered hardware: ' + name)
        active_ports = {port for port in ports if physical.get(port, {}).get('addresses')}
        if active_ports and (m['interface'] not in {b['name'] for b in bridges} or not m['configure']):
            raise ValueError('Review management addressing before moving an addressed Ethernet port into a bridge')
    guests = c.get('guestInterfaces', ['incusbr0'])
    if (not isinstance(guests, list) or len(guests) > 64 or any(not interface(name) for name in guests)
            or len(set(guests)) != len(guests) or 'incusbr0' not in guests):
        raise ValueError('Register distinct private guest bridges, including incusbr0')
    if any(not interface(name) or name == m['interface'] or name in names or name in ports for name in guests):
        raise ValueError('Guest firewall interfaces must be separate from host Ethernet/LAN interfaces')
    if host is not None and any(physical.get(name, {}).get('kind') not in (None, 'bridge') for name in guests):
        raise ValueError('Guest firewall integration accepts bridges only')
    for rule in records(c.get('privateAccess', []), {'cidr', 'protocol', 'ports'}, 'private access rules'):
        network = ipaddress.ip_network(rule['cidr'], strict=True)
        if network.version != 4 or rule['protocol'] not in ('tcp', 'udp'):
            raise ValueError('Private access rules require IPv4 and TCP/UDP')
        if not isinstance(rule['ports'], list) or not rule['ports'] or any(type(p) is not int or not 1 <= p <= 65535 for p in rule['ports']):
            raise ValueError('Private access ports must be between 1 and 65535')
    aliases = records(site.get('hardware', {}).get('serialAliases', []), {'name', 'vendorId', 'productId', 'serial'}, 'serial aliases')
    identities, alias_names = set(), set()
    for alias in aliases:
        if (any(not isinstance(value, str) for value in alias.values())
                or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', alias['name'])
                or any(not re.fullmatch(r'[0-9a-fA-F]{4}', alias[k]) for k in ('vendorId', 'productId'))
                or not re.fullmatch(r'[A-Za-z0-9_.:+-]{1,128}', alias['serial'])):
            raise ValueError('Select an exact USB identity and a safe stable alias name')
        identity = (alias['vendorId'].lower(), alias['productId'].lower(), alias['serial'])
        if identity in identities or alias['name'] in alias_names: raise ValueError('Duplicate serial alias identity/name')
        identities.add(identity); alias_names.add(alias['name'])
    backup = site.get('backup', {})
    for group in backup.get('groups', []):
        if not isinstance(group, list) or not group: raise ValueError('Backup consistency groups must be nonempty lists')
        for member in group: guest_identity(member)
    for member in backup.get('exclude', []): guest_identity(member)
    if type(backup.get('reserveMiB', 1024)) is not int or not 100 <= backup.get('reserveMiB', 1024) <= 1048576:
        raise ValueError('Backup staging reserve must be 100–1048576 MiB')
    schedule = backup.get('schedule', '*-*-* 03:00:00')
    if not isinstance(schedule, str) or not schedule or len(schedule) > 128 or any(ch in schedule for ch in '\r\n\0'):
        raise ValueError('Use a single-line systemd calendar schedule')
    return names


def guest_identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}/[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', value):
        raise ValueError('Use project/guest identities for backup selection')
