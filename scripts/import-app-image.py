"""Import a split Incus image once and point the managed alias at its fingerprint."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def fingerprint(paths):
    digest = hashlib.sha256()
    for path in paths:
        with open(path, 'rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


def run(*args):
    return subprocess.check_output(['incus', '--force-local', *args], text=True)


def ensure_image(metadata, disk):
    target = fingerprint([metadata, disk])
    images = json.loads(run('image', 'list', '--format=json'))
    if not any(image['fingerprint'] == target for image in images):
        run('image', 'import', str(metadata), str(disk))
    # Verify presence even after import; do not hide genuine import failures.
    run('image', 'info', target)
    aliases = json.loads(run('image', 'alias', 'list', '--format=json'))
    current = next((alias for alias in aliases if alias['name'] == 'nixlab-apps'), None)
    if current and current['target'] == target:
        return
    if current:
        run('image', 'alias', 'delete', 'nixlab-apps')
    run('image', 'alias', 'create', 'nixlab-apps', target)


if __name__ == '__main__':
    ensure_image(Path(sys.argv[1]), Path(sys.argv[2]))
