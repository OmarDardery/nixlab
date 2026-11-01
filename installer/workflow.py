"""Read-only planning and recoverable source publication for installed NixOS."""
import hashlib
import json
from pathlib import Path
import subprocess
import time

import runtime as rt
import upgrades


def remote_session():
    import os
    if os.environ.get('SSH_CONNECTION') or os.environ.get('SSH_TTY'):
        return True
    # sudo commonly removes SSH_* variables. Inspect process names, never their
    # environment or argument vectors, to recognize an sshd ancestor anyway.
    pid = os.getpid()
    for _ in range(64):
        try:
            proc = Path('/proc') / str(pid)
            if (proc / 'comm').read_text().strip().startswith('sshd'):
                return True
            status = (proc / 'status').read_text().splitlines()
            parent = int(next(line.split()[1] for line in status if line.startswith('PPid:')))
            if parent <= 1 or parent == pid:
                break
            pid = parent
        except (OSError, StopIteration, ValueError):
            break
    return False


def hashes(root):
    return {name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in rt.source_files(root) if upgrades.platform_path(name)}


def stage_update(root, incoming, stage, baseline):
    """Never overwrite platform edits; machine files are protected separately."""
    rt.snapshot(stage, root)
    if incoming is None:
        return False, []
    current = hashes(root)
    known = rt.read_json(baseline, {})
    if not known:
        names = subprocess.run(['git', '-C', str(root), 'ls-tree', '-r', '--name-only', 'HEAD'],
                               capture_output=True, text=True, timeout=30)
        for name in names.stdout.splitlines() if names.returncode == 0 else []:
            if not upgrades.platform_path(name):
                continue
            data = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:' + name],
                                  capture_output=True, check=True, timeout=30).stdout
            known[name] = hashlib.sha256(data).hexdigest()
    if not known:
        raise ValueError('Cannot establish the platform baseline; retry with --no-update and inspect the managed source')
    edits = sorted(name for name in set(known) | set(current) if current.get(name) != known.get(name))
    desired = {name: path for name, path in rt.source_files(incoming) if upgrades.platform_path(name)}
    edits += sorted(name for name in desired if name not in current and (root / name).exists())
    if edits:
        return False, sorted(set(edits))
    for name in current.keys() - desired.keys():
        (stage / name).unlink(missing_ok=True)
    for name, path in desired.items():
        rt.atomic(stage / name, path.read_bytes(), mode=path.stat().st_mode & 0o777)
    preserved = {name for name, _ in rt.source_files(stage) if not upgrades.platform_path(name)}
    rt.write_json(stage / '.nixlab-files.json', sorted((preserved | set(desired)) - {'.nixlab-files.json'}))
    return True, []


def incus_inventory():
    from urllib.parse import quote
    def query(path):
        result = subprocess.run(['incus', '--force-local', 'query', path],
                                capture_output=True, text=True, check=True, timeout=30)
        return json.loads(result.stdout)
    server = query('/1.0')
    if server.get('environment', {}).get('server_clustered'):
        raise ValueError('Clustered Incus needs a separate migration plan; no changes made')
    data = {'server': server, 'projects': query('/1.0/projects?recursion=1'),
            'networks': query('/1.0/networks?recursion=1'),
            'pools': query('/1.0/storage-pools?recursion=1'),
            'instances': query('/1.0/instances?recursion=1&all-projects=true')}
    data['profiles'] = {p['name']: query('/1.0/profiles?recursion=1&project=' + quote(p['name'], safe=''))
                        for p in data['projects']}
    data['volumes'] = {p['name']: query('/1.0/storage-pools/' + quote(p['name'], safe='') +
                                      '/volumes?recursion=1&all-projects=true') for p in data['pools']}
    return data


def adoption_settings(settings, data):
    """Use a separate namespace so native guests and their dependencies stay native."""
    if any(p['name'] == 'lab' for p in data['projects']):
        raise ValueError('Existing project lab conflicts with the Nixlab namespace. '
                         'Use the existing Nixlab checkout to resume, or migrate that project explicitly; no changes made')
    occupied = {item['name'] for item in data['networks']} | {item['name'] for item in data['pools']}
    def available(prefix):
        return next(name for name in [prefix] + [prefix + str(i) for i in range(1, 100)] if name not in occupied)
    settings['guests']['bridge'] = available('nixlabbr')
    settings['storage']['pool'] = available('nixlab-guests')
    settings['storage']['source'] = '/var/lib/incus/storage-pools/' + settings['storage']['pool']
    settings['connectivity']['guestInterfaces'] = [settings['guests']['bridge']]
    settings['connectivity']['preservedIncusBridges'] = [n['name'] for n in data['networks']
        if n.get('managed') and n.get('type') == 'bridge']
    return settings


def backup_incus(data, directory):
    """Native exports include guest configuration and snapshots; nothing is stopped/deleted."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    rt.write_json(directory / 'inventory.json', data)
    for instance in data['instances']:
        project, name = instance.get('project', 'default'), instance['name']
        # API names must never become arbitrary filesystem paths.
        import re
        if not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', v) for v in (project, name)):
            raise ValueError('Unsafe Incus identity in adoption inventory')
        output = directory / project / (name + '.tar.gz')
        output.parent.mkdir(mode=0o700, exist_ok=True)
        # Every adoption attempt makes a fresh export; native guests can have
        # changed since a previous failed installation.
        temporary = output.with_suffix('.partial')
        temporary.unlink(missing_ok=True)
        subprocess.run(['incus', '--force-local', 'export', name, str(temporary), '--project', project,
                        '--compression', 'gzip'], check=True, timeout=7200)
        if not temporary.is_file() or not temporary.stat().st_size:
            raise ValueError('Incus export produced no backup for ' + project + '/' + name)
        temporary.chmod(0o600)
        temporary.rename(output)
        rt.atomic(output.parent / (output.name + '.complete'), str(time.time()))
    for pool, volumes in data.get('volumes', {}).items():
        for volume in volumes:
            if volume.get('type') != 'custom': continue
            project, name = volume.get('project', 'default'), volume['name']
            import re
            if not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value) for value in (pool, project, name)):
                raise ValueError('Unsafe custom volume identity')
            output = directory / 'volumes' / pool / project / (name + '.tar.gz')
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = output.with_suffix('.partial'); temporary.unlink(missing_ok=True)
            subprocess.run(['incus', '--force-local', 'storage', 'volume', 'export', pool, name, str(temporary),
                            '--project', project, '--compression', 'gzip'], check=True, timeout=7200)
            if not temporary.is_file() or not temporary.stat().st_size:
                raise ValueError('Custom volume export produced no backup: ' + pool + '/' + name)
            temporary.chmod(0o600); temporary.rename(output)
    rt.write_json(directory / 'complete.json', {'completedAt': time.time(), 'guests': len(data['instances'])})
