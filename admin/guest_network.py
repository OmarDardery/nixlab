"""Sanitized, read-only Incus address inventory; Incus remains the IPAM owner."""
import ipaddress
import json
import subprocess
import time
import incus_client


def query(path):
    raw = incus_client.run(['query', path], 10, ValueError, 'Incus inventory unavailable', limit=1024)
    if len(raw) > 8 * 1024**2:
        raise ValueError('Incus inventory exceeds the console limit')
    result = json.loads(raw)
    if not isinstance(result, list) or len(result) > 1000:
        raise ValueError('Incus inventory exceeds the console limit')
    return result


def address(value):
    if not isinstance(value, str):
        return None
    try:
        ip = ipaddress.ip_address(value)
        return str(ip) if not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified) else None
    except (ValueError, TypeError):
        return None


def addresses(network):
    return sorted({ip for entry in network.get('addresses', [])
                   if entry.get('scope') == 'global' and (ip := address(entry.get('address')))})


def summarize(instances, projects, now=None):
    namespaces = {p['name']: p['name'] if p.get('config', {}).get('features.networks') == 'true' else 'default'
                  for p in projects}
    guests, owners = [], {}
    for guest in instances:
        project = guest.get('project', 'default')
        running = guest.get('status') == 'Running'
        networks = ((guest.get('state') or {}).get('network') or {}) if running else {}
        config = guest.get('expanded_config', guest.get('config', {}))
        nics = []
        for key, device in sorted(guest.get('expanded_devices', guest.get('devices', {})).items()):
            if device.get('type') != 'nic':
                continue
            observed = networks.get(device.get('name', key))
            mac = device.get('hwaddr') or config.get('volatile.' + key + '.hwaddr')
            if observed is None and mac:
                observed = next((n for n in networks.values() if (n.get('hwaddr') or '').lower() == mac.lower()), None)
            reported = addresses(observed or {})
            reserved = sorted({ip for field in ('ipv4.address', 'ipv6.address') if (ip := address(device.get(field)))})
            network = device.get('network')
            nics.append({'name': key, 'network': network or device.get('parent') or 'Unmanaged',
                         'observed': reported, 'reserved': reserved,
                         'unobservedReservation': bool(reported and set(reserved) - set(reported))})
            # Only managed network namespaces establish a known shared address
            # domain. Identical addresses on isolated networks are legitimate.
            if network and project in namespaces:
                for ip in set(reported + reserved):
                    identity = (namespaces.get(project, 'default'), network, ip)
                    owners.setdefault(identity, set()).add((project, guest['name'], key))
        guests.append({'project': project, 'name': guest['name'], 'type': guest.get('type', 'unknown'),
                       'status': guest.get('status', 'Unknown'), 'nics': nics,
                       'observed': sorted({ip for nic in nics for ip in nic['observed']})})
    conflicts = [{'project': project, 'network': network, 'address': ip,
                  'owners': ['/'.join(owner) for owner in sorted(members)]}
                 for (project, network, ip), members in sorted(owners.items()) if len(members) > 1]
    return {'available': True, 'checkedAt': int(time.time() if now is None else now),
            'guests': sorted(guests, key=lambda g: (g['project'], g['name'])), 'conflicts': conflicts}


def inventory():
    try:
        return summarize(query('/1.0/instances?recursion=2&all-projects=true'), query('/1.0/projects?recursion=1'))
    except (ValueError, KeyError, TypeError, AttributeError, OSError, subprocess.SubprocessError):
        # Never turn failed observations into a healthy, empty inventory or
        # expose complete instance config/user-data through an error response.
        raise ValueError('Guest network inventory unavailable; check Incus and refresh.') from None
