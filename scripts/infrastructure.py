"""Refuse to overwrite native Incus edits to source-owned infrastructure.

Only objects named in preseed are tracked. Other networks, pools, profiles and
guests remain entirely Incus-owned; their runtime state is never reconciled here.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import quote

STATE = Path('/var/lib/nixlab/infrastructure.json')


def query(path, missing_ok=False):
    result = subprocess.run(['incus', '--force-local', 'query', path], capture_output=True, text=True, timeout=30)
    if result.returncode:
        if missing_ok and 'not found' in result.stderr.lower(): return None
        raise ValueError('Cannot inspect Incus infrastructure: ' + result.stderr.strip()[-2000:])
    return json.loads(result.stdout)


def objects(preseed):
    for key, api in (('storage_pools', 'storage-pools'), ('networks', 'networks'), ('projects', 'projects'), ('profiles', 'profiles')):
        for item in preseed.get(key, []):
            project = item.get('project', 'default')
            path = '/1.0/' + api + '/' + quote(item['name'], safe='')
            if key == 'profiles': path += '?project=' + quote(project, safe='')
            yield key + ':' + project + '/' + item['name'], path, stable(item)


def stable(value):
    return {key: ({k: v for k, v in item.items() if not k.startswith('volatile.')} if key == 'config' else item)
            for key, item in value.items() if key in ('config', 'devices', 'driver', 'type', 'description')}


def check(preseed, state=STATE):
    previous = json.loads(state.read_text()) if state.is_file() else {'objects': {}, 'declarations': {}}
    for identity, path, declaration in objects(preseed):
        current = query(path, missing_ok=True)
        if identity not in previous['objects']:
            if current is None: continue
            actual = stable(current)
            matches = all(all(actual.get(key, {}).get(k) == v for k, v in wanted.items()) if isinstance(wanted, dict)
                          else actual.get(key) == wanted for key, wanted in declaration.items())
            if matches: continue
        else:
            actual = stable(current or {})
            old = previous['objects'][identity]
            if actual == old: continue
            # An explicitly reviewed source edit may adopt exactly that edit;
            # unrelated native changes still refuse instead of being reset.
            expected = json.loads(json.dumps(old))
            for key, wanted in declaration.items():
                if isinstance(wanted, dict):
                    target = expected.setdefault(key, {})
                    for removed in previous['declarations'][identity].get(key, {}).keys() - wanted.keys(): target.pop(removed, None)
                    target.update(wanted)
                else: expected[key] = wanted
            if actual == expected: continue
        if current is not None or identity in previous['objects']:
            raise ValueError('Incus drift in source-owned infrastructure: ' + identity
                             + '. Restore the declared object in Incus or review its Nix declaration; refusing to reset native edits.')


def record(preseed, state=STATE):
    values = {'objects': {}, 'declarations': {}}
    for identity, path, declaration in objects(preseed):
        values['objects'][identity] = stable(query(path))
        values['declarations'][identity] = declaration
    state.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=state.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(values, stream, indent=2); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, state)
    finally: Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        preseed = json.loads(Path(sys.argv[2]).read_text())
        (check if sys.argv[1] == 'check' else record)(preseed)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc: sys.exit(str(exc))
