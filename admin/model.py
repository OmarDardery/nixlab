"""Typed settings and read-only host inventory. No evaluation of user supplied code."""
import copy
import ipaddress
import json
import re
import subprocess
from pathlib import Path
from preflight import validate as validate_network

FILESYSTEMS = {'ext4', 'xfs', 'btrfs', 'vfat', 'exfat', 'ntfs3'}
ROLES = {'data', 'guests', 'pool', 'backup'}


def command(args, timeout=20):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise ValueError(result.stderr.strip() or 'Command failed: ' + args[0])
    return result.stdout


def inventory():
    def read(args, fallback):
        try:
            return json.loads(command(args))
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return fallback
    links = read(['ip', '-j', 'address', 'show'], [])
    interfaces = []
    for link in links:
        if link['ifname'] == 'lo':
            continue
        interfaces.append({'name': link['ifname'], 'state': link.get('operstate', ''),
            'wireless': Path('/sys/class/net', link['ifname'], 'wireless').exists(),
            'addresses': [f"{a['local']}/{a['prefixlen']}" for a in link.get('addr_info', []) if a['family'] == 'inet']})
    disks = read(['lsblk', '--json', '--bytes', '--output', 'NAME,PATH,TYPE,SIZE,FSTYPE,UUID,LABEL,MOUNTPOINTS,ROTA,TRAN'], {'blockdevices': []})
    cpu = Path('/proc/cpuinfo').read_text() if Path('/proc/cpuinfo').exists() else ''
    return {'interfaces': interfaces, 'routes': read(['ip', '-j', '-4', 'route', 'show'], []),
            'disks': disks['blockdevices'], 'cpuVendor': 'amd' if 'AuthenticAMD' in cpu else 'intel' if 'GenuineIntel' in cpu else '',
            'architecture': command(['uname', '-m']).strip(), 'kvm': Path('/dev/kvm').exists(),
            'uefi': Path('/sys/firmware/efi').exists()}


def flatten_disks(devices):
    for device in devices:
        yield device
        yield from flatten_disks(device.get('children', []))


def merge(a, b):
    result = copy.deepcopy(a)
    for key, value in b.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def check_shape(value, template, location='settings'):
    if location == 'settings.tunnel.ingress':
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k,v in value.items()):
            raise ValueError('Ingress must map hostnames to endpoint strings')
        return
    if isinstance(template, dict):
        if not isinstance(value, dict):
            raise ValueError(location + ' must be an object')
        for key, child in value.items():
            if key not in template:
                raise ValueError('Unknown setting: ' + location + '.' + key)
            check_shape(child, template[key], location + '.' + key)
    elif isinstance(template, list):
        if not isinstance(value, list):
            raise ValueError(location + ' must be a list')
    elif type(value) is not type(template):
        raise ValueError(location + ' has an invalid type')


def validate_settings(candidate, current, host, require_devices=True):
    check_shape(candidate, current)
    # The browser edits these sections; instance declarations and secret file paths remain code-owned.
    for field in ('instances', 'secrets', 'system'):
        if candidate[field] != current[field]:
            raise ValueError(field + ' is managed in the source repository')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]{0,62}', candidate['hostName']):
        raise ValueError('Use a valid host name')
    if candidate['system'] != host['architecture'] + '-linux':
        raise ValueError('This installer supports the detected Linux architecture only')
    if candidate['cpuVendor'] != host['cpuVendor'] or candidate['cpuVendor'] not in ('intel', 'amd'):
        raise ValueError('CPU vendor must match the detected Intel/AMD host')
    keys = candidate['adminKeys']
    if not keys or any(not isinstance(k, str) or '\n' in k or not re.fullmatch(r'(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh.com) [A-Za-z0-9+/=]+(?: [^\r\n]*)?', k) for k in keys):
        raise ValueError('Supply at least one valid administrator SSH public key')
    interfaces = {i['name']: i for i in host['interfaces']}
    m, w = candidate['management'], candidate['radios']['wifi']
    if require_devices and m['interface'] not in interfaces:
        raise ValueError('Management interface was not discovered on this host')
    if not isinstance(m['prefixLength'], int) or isinstance(m['prefixLength'], bool) or not 1 <= m['prefixLength'] <= 30:
        raise ValueError('Management IPv4 prefix must be between 1 and 30')
    for value in m['dns']:
        ipaddress.ip_address(value)
    if not m['trustedCIDRs'] or any(ipaddress.ip_network(v).version != 4 for v in m['trustedCIDRs']):
        raise ValueError('Configure trusted IPv4 management source networks')
    if require_devices and not m['configure'] and f"{m['address']}/{m['prefixLength']}" not in interfaces[m['interface']]['addresses']:
        raise ValueError('Preserve mode must use an address actually assigned to that interface')
    if interfaces.get(m['interface'], {}).get('wireless') and not w['enable']:
        raise ValueError('Wi-Fi cannot be disabled while it is the management interface')
    if w['enable']:
        if require_devices and not interfaces.get(w['interface'], {}).get('wireless'):
            raise ValueError('Select a detected Wi-Fi interface')
        if w['manageProfile'] and (not w['ssid'] or len(w['ssid'].encode()) > 32 or any(c in w['ssid'] for c in '\r\n\x00')):
            raise ValueError('A managed Wi-Fi profile needs a valid SSID')
        if m['configure'] and m['interface'] == w['interface'] and not w['manageProfile']:
            raise ValueError('Static Wi-Fi addressing requires a managed Wi-Fi profile')
    for field, lower, upper in (('retentionDays', 1, 90), ('scrapeSeconds', 15, 300)):
        value = candidate['monitoring'][field]
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError('Monitoring ' + field + ' must be between ' + str(lower) + ' and ' + str(upper))
    if not re.fullmatch(r'[A-Za-z0-9_+.-]+(?:/[A-Za-z0-9_+.-]+)*', candidate['maintenance']['timeZone']):
        raise ValueError('Invalid time zone')
    zone = Path('/etc/zoneinfo', candidate['maintenance']['timeZone'])
    if require_devices and not zone.exists() and not Path('/usr/share/zoneinfo', candidate['maintenance']['timeZone']).exists():
        raise ValueError('Time zone is not installed on this host')
    if not 1024 <= candidate['setup']['port'] <= 65535 or candidate['setup']['port'] in (8080, 8443, 9090, 9100, 9441):
        raise ValueError('Choose an unused setup port (default 9440)')
    if not 300 <= candidate['setup']['confirmationSeconds'] <= 1800:
        raise ValueError('Activation confirmation must allow 5–30 minutes')
    if not candidate['setup']['enable']:
        raise ValueError('The setup service cannot disable its own recovery/access path')
    if not re.fullmatch(r'[1-9][0-9]*', candidate['apps']['cpu']):
        raise ValueError('VM CPU allocation must be a positive count')
    for k in ('memory', 'disk'):
        if not re.fullmatch(r'[1-9][0-9]*(?:MiB|GiB)', candidate['apps'][k]):
            raise ValueError('VM ' + k + ' must use MiB/GiB units')
    if not re.fullmatch(r'[A-Za-z0-9./_:@-]+', candidate['apps']['portainerImage']):
        raise ValueError('Invalid Portainer image reference')
    mounts = candidate['mounts']
    uuids, paths, names = set(), set(), set()
    available = {}
    for device in flatten_disks(host.get('disks', [])):
        if device.get('uuid'):
            available.setdefault(device['uuid'], []).append(device)
    for mount in mounts:
        if set(mount) != {'name', 'uuid', 'path', 'fsType', 'role', 'required', 'readOnly'}:
            raise ValueError('Each mount needs name, UUID, path, filesystem, role and boot/read-only flags')
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', mount['name']):
            raise ValueError('Mount names use lowercase letters, numbers and hyphens')
        if not re.fullmatch(r'[A-Za-z0-9-]{4,80}', mount['uuid']):
            raise ValueError('Select a stable filesystem UUID')
        if mount['path'] != '/srv/nixlab/' + mount['name']:
            raise ValueError('Managed disks mount only at /srv/nixlab/<name>')
        if mount['fsType'] not in FILESYSTEMS or mount['role'] not in ROLES:
            raise ValueError('Unsupported filesystem or storage role')
        if type(mount['required']) is not bool or type(mount['readOnly']) is not bool:
            raise ValueError('Mount flags must be boolean')
        if mount['role'] != 'data' and (not mount['required'] or mount['readOnly'] or mount['fsType'] not in {'ext4', 'xfs', 'btrfs'}):
            raise ValueError('Incus/backup storage requires a writable, required Linux filesystem')
        if mount['uuid'] in uuids or mount['path'] in paths or mount['name'] in names:
            raise ValueError('Duplicate filesystem, mount point or pool name')
        uuids.add(mount['uuid']); paths.add(mount['path']); names.add(mount['name'])
        if require_devices:
            matches = available.get(mount['uuid'], [])
            if len(matches) != 1:
                raise ValueError('Filesystem UUID must resolve to exactly one detected device')
            disk = matches[0]
            if ('ntfs3' if disk.get('fstype') == 'ntfs' else disk.get('fstype')) != mount['fsType']:
                raise ValueError('Filesystem type differs from the discovered device')
            existing = [s for s in disk.get('mountpoints', []) if s]
            if any(s != mount['path'] for s in existing):
                raise ValueError('Device is already mounted elsewhere; root/boot/system disks cannot be repurposed')
            if any(other.get('uuid') != mount['uuid'] and mount['path'] in (other.get('mountpoints') or []) for other in flatten_disks(host.get('disks', []))):
                raise ValueError('Mount point is already backed by a different filesystem')
            target = Path(mount['path'])
            if target.is_symlink():
                raise ValueError('Mount point cannot be a symlink')
            if target.exists() and not target.is_mount() and any(target.iterdir()):
                raise ValueError('Mount point contains files that would be hidden')
    primary = [m for m in mounts if m['role'] == 'guests']
    stages = [m for m in mounts if m['role'] == 'backup']
    if len(primary) > 1 or len(stages) > 1:
        raise ValueError('Choose at most one primary Incus disk and one backup staging disk')
    if primary:
        if candidate['storage']['driver'] != 'dir':
            raise ValueError('Filesystem mounting uses a directory pool; ZFS datasets are configured separately')
        candidate['storage']['source'] = primary[0]['path'] + '/incus'
    elif candidate['storage']['driver'] == 'dir':
        candidate['storage']['source'] = '/var/lib/incus/storage-pools/guests'
    candidate['backup']['stagingDirectory'] = stages[0]['path'] + '/exports' if stages else '/var/lib/nixlab/exports'
    if candidate['backup']['enable']:
        if not candidate['backup']['repository'] or '\n' in candidate['backup']['repository']:
            raise ValueError('Provide an independent restic repository')
    if candidate['tunnel']['enable']:
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', candidate['tunnel']['uuid']) or not candidate['tunnel']['ingress']:
            raise ValueError('Provide the existing tunnel UUID and application hostnames')
        for hostname, target in candidate['tunnel']['ingress'].items():
            if not re.fullmatch(r'[A-Za-z0-9*.-]+', hostname) or not isinstance(target, str) or not target.startswith(('http://', 'https://')):
                raise ValueError('Tunnel ingress must map hostnames to HTTP application endpoints')
            if re.search(r':(?:22|8443|9090|9100|9440|9441|'+str(candidate['setup']['port'])+r')(?:/|$)', target):
                raise ValueError('Management endpoints cannot be public tunnel origins')
    candidate['ready'] = True
    validate_network(candidate)
    return candidate


def differences(old, new, prefix=''):
    changes = []
    for key in sorted(set(old) | set(new)):
        path = prefix + key
        before, after = old.get(key), new.get(key)
        if isinstance(before, dict) and isinstance(after, dict):
            changes += differences(before, after, path + '.')
        elif before != after:
            changes.append({'setting': path, 'before': before, 'after': after})
    return changes
