"""Read-only backup coverage and staging estimates; no application adapters."""
import math


def coverage(guests, volumes, selected_guests, selected_volumes, namespaces):
    selected = {(g['project'], g['name']) for g in selected_guests}
    exported = {(v['project'], v['pool'], v['name']) for v in selected_volumes}
    rows = []
    for guest in guests:
        identity = guest['project'] + '/' + guest['name']
        included = (guest['project'], guest['name']) in selected
        rows.append({'kind': 'instance', 'identity': identity,
                     'status': 'included' if included else 'excluded'})
        if not included:
            continue
        for name, device in guest.get('expanded_devices', guest.get('devices', {})).items():
            if device.get('type') != 'disk' or device.get('path') == '/':
                continue
            source = device.get('source')
            if not source:
                continue
            if device.get('pool'):
                key = (namespaces.get(guest['project'], guest['project']), device['pool'], source.removeprefix('custom/'))
                status = 'included' if key in exported else 'missing'
            else:
                # Host bind mounts, raw disks and external shares are not in an
                # Incus instance archive, even when the guest sees them read-only.
                status = 'external'
            rows.append({'kind': 'attached-disk', 'identity': identity + '/' + name, 'status': status})
    for volume in volumes:
        key = (volume['project'], volume['pool'], volume['name'])
        rows.append({'kind': 'custom-volume', 'identity': '/'.join(key),
                     'status': 'included' if key in exported else 'excluded'})
    return {'format': 1, 'items': rows, 'complete': all(row['status'] == 'included' for row in rows),
            'applicationConsistency': 'cold exports; external writers and application recovery require independent verification',
            'restore': 'unverified'}


def capacity(objects, free, reserve, recovery_bytes=0):
    """Estimate a new generation in addition to all already retained files.

    Inputs contain observed usage and/or the previous compressed archive size.
    These are estimates, never a promise about compression or future growth.
    """
    items = []
    for identity, usage, previous in objects:
        sizes = [value for value in (usage, previous) if type(value) is int and value >= 0]
        estimate = math.ceil(max(sizes) * 1.25) + 16 * 1024**2 if sizes else None
        items.append({'identity': identity, 'estimatedBytes': estimate,
                      'basis': 'observed-usage' if type(usage) is int and usage >= 0 else 'previous-archive' if sizes else 'unknown'})
    predicted = recovery_bytes + sum(item['estimatedBytes'] or 0 for item in items)
    return {'format': 1, 'availableBytes': free, 'reserveBytes': reserve,
            'estimatedNewBytes': predicted, 'requiredBytes': reserve + predicted,
            'unknown': [item['identity'] for item in items if item['basis'] != 'observed-usage'],
            'items': items, 'sufficientForEstimate': free >= reserve + predicted}
