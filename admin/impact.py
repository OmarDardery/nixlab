"""Read-only impact planning using the same ownership decisions as activation.

Raw Incus configuration stays in root-only transaction files. Public plans expose
identities and changed field names, never runtime configuration values.
"""
import hashlib
import http.client
import json
from pathlib import Path
import time
from urllib.parse import quote

import infrastructure
import reconcile
import runtime as rt

VERSION = 1
LIMIT = 256
MAX_BYTES = 2 * 1024 * 1024
BLOCKING = {'migration-required', 'ownership-conflict'}
RECOVERY_BOUNDARIES = [
    'OS rollback restores host code/settings, not guest disks, databases or Incus schema.',
    'Incus owns native guests, consoles, networks and volumes; Portainer or declared source owns stacks.',
    'Router routes, external DNS, Cloudflare accounts and physical switches require separate review.',
    'Unit changes may reload or restart services; exact activation behavior remains in dry-activation output.',
    'The plan covers modeled declarations and observed consumers; arbitrary Nix code can have additional effects.',
]


class ReviewRequired(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def changed_fields(before, after, prefix=''):
    if isinstance(before, dict) and isinstance(after, dict):
        fields = []
        for key in sorted(before.keys() | after.keys()):
            name = prefix + ('.' if prefix else '') + key
            if key not in before or key not in after:
                value = after.get(key, before.get(key))
                fields += changed_fields({}, value, name) if isinstance(value, dict) and value else [name]
            else: fields += changed_fields(before[key], after[key], name)
        return fields
    return [prefix] if before != after else []


def declarations(preseed):
    return {identity: value for identity, _, value in infrastructure.objects(preseed)}


def baseline(settings):
    owned = rt.read_json(infrastructure.STATE, {'objects': {}, 'declarations': {}})
    preseed = rt.read_json(Path('/etc/nixlab/owned-infrastructure.json'))
    if preseed is None:
        # Upgrade from versions that wrote the guard ledger but not the projection.
        preseed = {}
        for identity, value in owned.get('declarations', {}).items():
            kind, rest = identity.split(':', 1)
            project, name = rest.split('/', 1)
            item = dict(value, name=name)
            if kind == 'profiles': item['project'] = project
            preseed.setdefault(kind, []).append(item)
    return {'settings': settings, 'hostSystem': str(Path('/run/current-system').resolve()),
            'hostSettingsDigest': digest(rt.read_json(Path('/etc/nixlab/effective-settings.json'), {})),
            'disks': rt.read_json(Path('/etc/nixlab/effective-disks.json'), {}),
            'instances': rt.read_json(Path('/etc/nixlab/desired-instances.json'), []), 'preseed': preseed}


def api(path, deadline, missing=False):
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise ValueError('Inventory deadline exceeded')
    connection = reconcile.LocalConnection('localhost', timeout=min(5, remaining))
    try:
        connection.request('GET', path)
        response = connection.getresponse()
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES: raise ValueError('Inventory exceeds size limit')
        if response.status == 404 and missing: return None, None
        if response.status != 200: raise ValueError('Incus inventory unavailable')
        payload = json.loads(data)
        if not isinstance(payload, dict) or 'metadata' not in payload: raise ValueError('Invalid Incus response')
        if payload.get('type') == 'error': raise ValueError('Incus inventory unavailable')
        return payload['metadata'], response.getheader('ETag')
    finally:
        connection.close()


def capture(candidate):
    snapshot = {'state': 'absent', 'system': str(Path('/run/current-system').resolve()),
                'settingsDigest': digest(rt.read_json(Path('/etc/nixlab/effective-settings.json'), {})),
                'owned': rt.read_json(infrastructure.STATE, {'objects': {}, 'declarations': {}}),
                'objects': {}, 'guests': [], 'resources': {}, 'projects': {}, 'server': {}}
    if not Path('/var/lib/incus/unix.socket').exists():
        if Path('/var/lib/incus/database').exists(): snapshot['state'] = 'unavailable'
        return snapshot
    deadline = time.monotonic() + 30
    try:
        def read(path, missing=False): return api(path, deadline, missing)
        def listing(path):
            values, _ = read(path)
            if not isinstance(values, list) or len(values) > LIMIT:
                raise ValueError('Inventory limit exceeded')
            if any(not isinstance(v, dict) or not isinstance(v.get('name'), str) for v in values):
                raise ValueError('Invalid inventory shape')
            if any(not isinstance(v.get(key, {}), dict) for v in values for key in ('config', 'devices')):
                raise ValueError('Invalid configuration shape')
            return values
        server, _ = read('/1.0')
        if not isinstance(server, dict): raise ValueError('Invalid server metadata')
        snapshot['server'] = server.get('config', {})
        if not isinstance(snapshot['server'], dict): raise ValueError('Invalid server configuration')
        snapshot['guests'] = listing('/1.0/instances?all-projects=true&recursion=1')
        projects = listing('/1.0/projects?recursion=1')
        if len(projects) > 16: raise ValueError('Project inventory limit exceeded')
        snapshot['projects'] = {item['name']: item.get('config', {}) for item in projects}
        for kind, endpoint in (('storage_pools', 'storage-pools'), ('networks', 'networks'), ('projects', 'projects')):
            values = projects if kind == 'projects' else listing('/1.0/' + endpoint + '?recursion=1')
            for item in values:
                snapshot['resources'][kind + ':default/' + item['name']] = item
        for project in projects:
            name = project['name']
            if name != 'default' and project.get('config', {}).get('features.networks') == 'true':
                for item in listing('/1.0/networks?recursion=1&project=' + quote(name, safe='')):
                    snapshot['resources']['networks:' + name + '/' + item['name']] = item
            # Projects without private profiles inherit the default namespace.
            if name != 'default' and project.get('config', {}).get('features.profiles') == 'false': continue
            for item in listing('/1.0/profiles?recursion=1&project=' + quote(name, safe='')):
                snapshot['resources']['profiles:' + name + '/' + item['name']] = item
        for identity, path, _ in infrastructure.objects(candidate['preseed']):
            value, etag = read(path, missing=True)
            snapshot['objects'][identity] = {'value': value, 'etag': etag}
        for guest in snapshot['guests']:
            identity = guest.get('project', 'default') + '/' + guest['name']
            if guest.get('config', {}).get(reconcile.OWNER) == 'true':
                value, etag = read('/1.0/instances/' + quote(guest['name'], safe='') + '?project=' + quote(guest.get('project', 'default'), safe=''))
                guest.update(value)
                guest['_etag'] = etag
        if len(snapshot['resources']) + len(snapshot['objects']) > LIMIT:
            raise ValueError('Resource inventory limit exceeded')
        snapshot['state'] = 'observed'
    except (ValueError, KeyError, TypeError, OSError, http.client.HTTPException):
        # Never promote partial/failed observation to an empty, conflict-free lab.
        snapshot['state'] = 'unavailable'
    return snapshot


def fingerprint(snapshot):
    guests = []
    for item in snapshot['guests']:
        value = {key: item.get(key) for key in ('name', 'project', 'type', 'profiles', 'devices', 'expanded_devices')}
        if item.get('config', {}).get(reconcile.OWNER) == 'true':
            value['config'] = {k: v for k, v in item.get('config', {}).items() if not k.startswith('volatile.')}
        guests.append(value)
    return digest({'state': snapshot['state'], 'system': snapshot['system'], 'settingsDigest': snapshot['settingsDigest'],
                   'owned': snapshot['owned'], 'projects': snapshot['projects'], 'server': snapshot.get('server', {}),
                   'objects': {k: {'value': infrastructure.stable(v['value'] or {}), 'etag': v['etag']}
                               for k, v in snapshot['objects'].items()},
                   'resources': {k: infrastructure.stable(v) for k, v in snapshot['resources'].items()},
                   'guests': sorted(guests, key=lambda g: (g.get('project') or 'default', g['name']))})


def units(system):
    root = Path(system) / 'etc/systemd/system'
    result = {}
    if not root.is_dir(): return result
    for path in sorted(root.iterdir()):
        if path.suffix not in {'.service', '.socket', '.timer', '.target', '.mount'}: continue
        if len(result) >= 1024: raise ValueError('Unit inventory limit exceeded')
        content = []
        for file in [path, *sorted(root.glob(path.name + '.d/*.conf'))]:
            if file.is_file():
                if file.stat().st_size > MAX_BYTES: raise ValueError('Unit exceeds size limit')
                content.append(hashlib.sha256(file.read_bytes()).hexdigest())
            else: content.append(str(file.resolve()))
        result[path.name] = digest(content)
    return result


def consumers(identity, snapshot):
    kind, rest = identity.split(':', 1)
    project, name = rest.split('/', 1)
    if kind == 'profiles' and snapshot['projects'].get(project, {}).get('features.profiles') == 'false': project = 'default'
    result = []
    for guest in snapshot['guests']:
        namespace = guest.get('project', 'default')
        config = snapshot['projects'].get(namespace, {})
        profile_namespace = 'default' if config.get('features.profiles') == 'false' else namespace
        network_namespace = namespace if config.get('features.networks') == 'true' else 'default'
        devices = guest.get('expanded_devices', guest.get('devices', {})).values()
        used = (kind == 'profiles' and project == profile_namespace and name in guest.get('profiles', []))
        used |= kind == 'storage_pools' and any(d.get('pool') == name for d in devices)
        used |= kind == 'networks' and project == network_namespace and any(d.get('network') == name or d.get('parent') == name for d in devices)
        used |= kind == 'projects' and namespace == name
        if used: result.append(namespace + '/' + guest['name'])
    return sorted(result)


def make_plan(before, after, snapshot, unit_changes=None, opaque=False, transition_error=False, original_host=None):
    changes, ownership = [], []
    def add(scope, identity, kind, message, affected=None, fields=None, blocked=False, resolution=None):
        changes.append({'scope': scope, 'identity': identity, 'kind': kind, 'message': message,
                        'affected': affected or [], 'fields': fields or [], 'blocked': blocked or kind in BLOCKING,
                        'resolution': resolution})
    if snapshot['state'] == 'unavailable':
        add('runtime', 'incus', 'unknown', 'Runtime inventory is incomplete or unavailable.', blocked=True,
            resolution='Restore local Incus access and build a new review; an empty inventory is not assumed.')
    if original_host and any(snapshot[k] != original_host[k] for k in ('system', 'settingsDigest', 'state')):
        add('host', 'active-generation', 'unknown', 'The active host generation, settings or Incus availability changed.', blocked=True,
            resolution='Build a new candidate against the active host.')
    server_before, server_after = before.get('preseed', {}).get('config', {}), after['preseed'].get('config', {})
    if server_after:
        ownership.append({'identity': 'incus-server', 'owner': 'Nix declared keys; other keys remain Incus', 'fields': sorted(server_after), 'consumers': []})
    if changed_fields(server_before, server_after):
        add('infrastructure', 'incus-server', 'restart-required', 'Source-owned server settings change.', fields=changed_fields(server_before, server_after))
    if snapshot['state'] == 'observed' and any(snapshot.get('server', {}).get(key) not in (server_before.get(key), value) for key, value in server_after.items()):
        add('infrastructure', 'incus-server', 'ownership-conflict', 'Runtime server settings differ from their source-owned values.',
            resolution='Inspect the declared server keys locally; restore their declared values or explicitly adopt them in source before reviewing another change.')
    old, new = declarations(before.get('preseed', {})), declarations(after['preseed'])
    observed = {k: v['value'] for k, v in snapshot['objects'].items()}
    conflicts = infrastructure.conflicts(after['preseed'], snapshot['owned'], observed) if snapshot['state'] == 'observed' else []
    for identity in sorted(old.keys() | new.keys()):
        fields = changed_fields(old.get(identity, {}), new.get(identity, {}))
        affected = consumers(identity, snapshot)
        if identity in new:
            ownership.append({'identity': identity, 'owner': 'NixLab / Nix', 'fields': changed_fields({}, new[identity]), 'consumers': affected})
        if identity in conflicts:
            add('infrastructure', identity, 'ownership-conflict', 'Native edits conflict with source-owned infrastructure.', affected,
                resolution='Inspect this object in Incus. Restore its declared fields or explicitly review the corresponding Nix declaration; do not delete the ownership ledger.')
        if not fields: continue
        if identity not in new:
            add('infrastructure', identity, 'configuration', 'Declaration removed; native resource is retained, never deleted.', affected, fields)
            continue
        kind = 'restart-required' if identity in old else 'configuration'
        immutable = identity.startswith('storage_pools:') and any(f in ('driver', 'config.source') for f in fields)
        immutable |= identity.startswith('networks:') and any(f in ('type', 'config.ipv4.address', 'config.ipv6.address') for f in fields)
        immutable |= identity.startswith('profiles:') and any(f.startswith('devices.') and f.rsplit('.', 1)[-1] in {'type', 'pool', 'source', 'path', 'network', 'parent', 'nictype'} for f in fields)
        exists = observed.get(identity) is not None or identity in snapshot['resources']
        if exists and immutable and (affected or identity.startswith(('storage_pools:', 'networks:'))): kind = 'migration-required'
        add('infrastructure', identity, kind, 'Changed source-owned fields may affect every listed consumer.', affected, fields,
            resolution='Migrate consumers and backing resources explicitly, then rebuild the review.' if kind == 'migration-required' else None)
    for identity in sorted(snapshot['resources'].keys() - new.keys()):
        ownership.append({'identity': identity, 'owner': 'Incus', 'fields': [], 'consumers': consumers(identity, snapshot)})
    guests = {g.get('project', 'default') + '/' + g['name']: g for g in snapshot['guests']}
    desired = {g['project'] + '/' + g['name']: g for g in after['instances']}
    prior = {g['project'] + '/' + g['name']: g for g in before.get('instances', [])}
    handled = set()
    for identity, spec in desired.items():
        current = guests.get(identity)
        if spec.get('manageExistingOnly') and (current is None or current.get('config', {}).get(reconcile.OWNER) != 'true'): continue
        handled.add(identity)
        owner = 'Incus; declaration conflicts' if current and current.get('config', {}).get(reconcile.OWNER) != 'true' else 'Nix declared fields; other fields remain Incus'
        ownership.append({'identity': 'instance:' + identity, 'owner': owner,
                          'fields': [f for f in changed_fields({}, reconcile.desired_state(spec)) if f != 'version'], 'consumers': [identity]})
        if current is None:
            add('guest', identity, 'configuration', 'New guest initialized once; future image changes do not replace its disk.', [identity])
            continue
        try:
            updated = reconcile.plan(current, spec)
            if updated is not None:
                fields = changed_fields({k: current.get(k) for k in updated}, updated)
                fields = [f for f in fields if f != 'config.' + reconcile.LAST_APPLIED]
                add('guest', identity, 'restart-required', 'Declared guest fields change; inspect live/hotplug and restart requirements in Incus.', [identity], fields)
            if spec.get('stop') or spec.get('start'):
                add('guest', identity, 'restart-required', 'The declaration explicitly requests a guest start or stop.', [identity])
        except ValueError as exc:
            kind = 'migration-required' if any(word in str(exc).lower() for word in ('migration', 'immutable', 'backing')) else 'ownership-conflict'
            add('guest', identity, kind, 'Guest reconciliation refuses these fields: ' + str(exc), [identity],
                resolution='Inspect this guest in Incus and its Nix declaration. Resolve the named conflict or perform an explicit migration; no automatic adoption or deletion occurs.')
    for identity in sorted(prior.keys() - desired.keys()):
        add('guest', identity, 'configuration', 'Declaration removed; guest and persistent disks remain untouched.', [identity])
    for identity in sorted(guests.keys() - handled):
        ownership.append({'identity': 'instance:' + identity, 'owner': 'Incus; not adopted by this review', 'fields': [], 'consumers': []})
    old_mounts = {m['name']: m for m in before['settings'].get('mounts', [])}
    old_mounts.update(before.get('disks', {}))
    new_mounts = {m['name']: m for m in after['settings'].get('mounts', [])}
    new_mounts.update(after.get('disks', {}))
    for name in sorted(old_mounts.keys() | new_mounts.keys()):
        a, b = old_mounts.get(name, {}), new_mounts.get(name, {})
        fields = changed_fields(a, b)
        if not fields: continue
        path = a.get('path', '')
        pools = [key for key, value in {**snapshot['resources'], **{k: v['value'] or {} for k, v in snapshot['objects'].items()}}.items()
                 if key.startswith('storage_pools:') and path and value.get('config', {}).get('source', '').startswith(path.rstrip('/') + '/')]
        affected = set(pools)
        for pool in pools: affected.update(consumers(pool, snapshot))
        for identity, guest in guests.items():
            if path and any(d.get('source', '').startswith(path.rstrip('/') + '/') or d.get('source') == path for d in guest.get('expanded_devices', guest.get('devices', {})).values()):
                affected.add(identity)
        critical = bool(a and (affected or a.get('role') in ('guests', 'pool') or name in before.get('disks', {})))
        critical &= any(f in ('uuid', 'path', 'fsType', 'role') for f in fields)
        add('mount', name, 'migration-required' if critical else 'restart-required',
            'Backing filesystem changes require reviewing dependent pools, guests and backup services.', sorted(affected), fields,
            resolution='Move dependent data/consumers before remapping or removing this filesystem.' if critical else None)
    if transition_error:
        add('storage/network', 'initialized-state', 'migration-required', 'An existing disk, pool or guest subnet cannot be remapped by this activation.',
            resolution='Complete an explicit storage/network migration, then build a new review.')
    settings_fields = changed_fields(before['settings'], after['settings'])
    if settings_fields:
        add('host', 'effective-settings', 'restart-required', 'Host service, access or maintenance settings change.', fields=settings_fields)
    for unit in unit_changes or []:
        add('service', unit, 'restart-required', 'Unit definition added, changed or removed; activation may reload/restart it.')
    if after['settings'].get('apps', {}).get('enable') and set(unit_changes or []) & {'nixlab-apps-onboarding.service', 'nixlab-instances.service'}:
        add('guest', 'lab/apps', 'restart-required', 'Application VM initialization or OS deployment may run; existing disks are retained and application migrations need separate recovery.', ['lab/apps'])
    if opaque:
        add('source', 'Nix-modules/platform', 'unknown', 'Arbitrary Nix code and package effects are not completely modeled. Review code and dry-activation output.')
    ownership += [
        {'identity': 'applications', 'owner': 'Portainer or declared Compose source', 'fields': [], 'consumers': []},
        {'identity': 'external-network-and-data', 'owner': 'Router, DNS, Cloudflare and application administrators', 'fields': [], 'consumers': []},
    ]
    return {'format': VERSION, 'generatedAt': int(time.time()), 'runtimeDigest': fingerprint(snapshot),
            'blocked': any(c['blocked'] for c in changes), 'changes': changes, 'ownership': ownership,
            'boundaries': RECOVERY_BOUNDARIES, 'runtimeState': snapshot['state'],
            'preconditionCount': len(snapshot['objects']) + len(snapshot['guests'])}


def save(directory, bundle, plan):
    rt.write_json(directory / 'impact.json', bundle)
    job = rt.read_json(directory / 'job.json')
    job.update(impactPlan=plan, impactDigest=digest(bundle))
    rt.write_json(directory / 'job.json', job)


def prepare(directory, before, after, opaque=False, transition_error=False):
    snapshot = capture(after)
    live_units, target_units = units(snapshot['system']), units(directory / 'system')
    changed = sorted(k for k in live_units.keys() | target_units.keys() if live_units.get(k) != target_units.get(k))
    original = {'system': before.get('hostSystem', snapshot['system']),
                'settingsDigest': before.get('hostSettingsDigest', snapshot['settingsDigest']), 'state': snapshot['state']}
    bundle = {'format': VERSION, 'before': before, 'after': after, 'snapshot': snapshot,
              'originalHost': original,
              'units': changed, 'opaque': opaque, 'transitionError': transition_error}
    plan = make_plan(before, after, snapshot, changed, opaque, transition_error, original)
    save(directory, bundle, plan)
    return plan


def recheck(directory):
    job = rt.read_json(directory / 'job.json')
    path = directory / 'impact.json'
    if not path.is_file(): raise ValueError('Candidate has no impact plan; build a new review')
    if path.stat().st_size > 8 * MAX_BYTES: raise ValueError('Impact plan exceeds size limit')
    bundle = rt.read_json(path)
    if bundle.get('format') != VERSION or job.get('impactDigest') != digest(bundle):
        raise ValueError('Impact evidence changed; build a new review')
    fresh = capture(bundle['after'])
    plan = make_plan(bundle['before'], bundle['after'], fresh, bundle['units'], bundle['opaque'],
                     bundle['transitionError'], bundle['originalHost'])
    if fingerprint(fresh) != fingerprint(bundle['snapshot']):
        bundle['snapshot'] = fresh
        plan['refreshedAfterDrift'] = True
        save(directory, bundle, plan)
        raise ReviewRequired('Runtime changed since review. The impact plan was refreshed; inspect it before applying again.')
    if plan['blocked']:
        save(directory, bundle, plan)
        raise ValueError('Impact plan contains migration, ownership or observation blockers; resolve them and rebuild the review')
    save(directory, bundle, plan)
    return plan
