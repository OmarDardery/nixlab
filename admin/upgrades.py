"""Immutable platform-code updates, with machine state preserved independently."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import runtime as rt

UPSTREAM = 'https://github.com/OmarDardery/nixlab.git'
PROTECTED = {'machine', 'local', 'installed-system', 'site-modules', 'secrets', '.git'}


def platform_path(name):
    path = PurePosixPath(name)
    return (bool(path.parts) and not path.is_absolute() and '..' not in path.parts
            and path.parts[0] not in PROTECTED
            and name not in {'hardware-configuration.nix', '.nixlab-files.json'})


def metadata(root):
    root = Path(root)
    explicit = rt.read_json(root / 'platform-release.json', {})
    if explicit.get('revision') and re.fullmatch(r'[0-9a-f]{40}', explicit['revision']):
        return explicit
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    revision = result.stdout.strip() if result.returncode == 0 else 'unversioned'
    return {'version': '0.1.0-rc1', 'revision': revision, 'repository': UPSTREAM}


def prepare(source, revision, log):
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('Platform updates require a reviewed full 40-character Git commit SHA')
    before = {n: p.read_bytes() for n, p in rt.source_files(source) if platform_path(n)}
    with tempfile.TemporaryDirectory(dir=source.parent, prefix='upstream-') as folder:
        def git(*args):
            return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', folder, *args],
                                  check=True, stdout=subprocess.PIPE, stderr=log, timeout=300).stdout
        git('init', '-q')
        git('fetch', '--depth=1', '--no-tags', UPSTREAM, revision)
        if git('rev-parse', 'FETCH_HEAD').decode().strip() != revision:
            raise ValueError('Fetched revision did not match the reviewed SHA')
        archive = git('archive', '--format=tar', 'FETCH_HEAD')
        if len(archive) > 100 * 1024**2:
            raise ValueError('Platform archive exceeds the 100 MiB review limit')
        incoming = {}
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            for member in stream:
                name = member.name
                if member.isdir(): continue
                if not platform_path(name): continue
                if not member.isfile() or member.size > 16 * 1024**2 or len(incoming) >= 10000:
                    raise ValueError('Platform source must contain bounded regular files only')
                incoming[name] = (stream.extractfile(member).read(), 0o755 if member.mode & 0o111 else 0o644)
        if not {'flake.nix', 'flake.lock', 'admin/main.py'}.issubset(incoming):
            raise ValueError('Selected revision does not contain a Nixlab platform')
    # Machine settings/modules were already staged by Manager.review and remain
    # untouched. Git is transport only; no reset, checkout or merge changes them.
    for name in before.keys() - incoming.keys():
        (source / name).unlink()
    for name, (data, mode) in incoming.items():
        rt.atomic(source / name, data, mode=mode)
    rt.write_json(source / 'platform-release.json', {'version': '0.1.0-rc1', 'revision': revision, 'repository': UPSTREAM})
    incoming['platform-release.json'] = ((source / 'platform-release.json').read_bytes(), 0o644)
    names = {n for n, _ in rt.source_files(source) if not platform_path(n)} | set(incoming)
    rt.write_json(source / '.nixlab-files.json', sorted(names - {'.nixlab-files.json'}))
    changes = []
    for name in sorted(before.keys() | incoming.keys()):
        old, new = before.get(name), incoming.get(name, (None,))[0]
        if old != new:
            changes.append({'path': name, 'before': hashlib.sha256(old).hexdigest() if old is not None else None,
                            'after': hashlib.sha256(new).hexdigest() if new is not None else None})
    return changes


def publish(source, root):
    """Publish only reviewed platform files, retaining all local machine state."""
    desired = {n: p for n, p in rt.source_files(source) if platform_path(n)}
    current = {n: p for n, p in rt.source_files(root) if platform_path(n)}
    local = {n for n, _ in rt.source_files(root) if not platform_path(n)}
    # All source paths are validated before the first write, including live paths.
    for name, path in desired.items():
        target = root / name
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError('Platform destination cannot traverse symlinks')
    # The union records new files before any publication. After a power loss,
    # recovery can remove partially published additions as well as restore edits.
    rt.write_json(root / '.nixlab-files.json', sorted((local | set(current) | set(desired)) - {'.nixlab-files.json'}))
    for name, path in desired.items():
        rt.atomic(root / name, path.read_bytes(), mode=path.stat().st_mode & 0o777)
    for name in current.keys() - desired.keys():
        rt.durable_unlink(root / name)
    rt.write_json(root / '.nixlab-files.json', sorted((local | set(desired)) - {'.nixlab-files.json'}))
