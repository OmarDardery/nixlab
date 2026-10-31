"""Immutable platform-code updates, with machine state preserved independently."""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import urllib.request
import runtime as rt

UPSTREAM = 'https://github.com/OmarDardery/nixlab.git'
PROTECTED = {'machine', 'local', 'installed-system', 'site-modules', 'secrets', '.git'}


def version(root):
    path = Path(root) / 'VERSION'
    if path.is_file():
        value = path.read_text().strip()
        if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-.][A-Za-z0-9.-]+)?', value): return value
    return 'unknown'


def releases():
    request = urllib.request.Request('https://api.github.com/repos/OmarDardery/nixlab/releases?per_page=30',
                                     headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'nixlab'})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.load(response)
    result = subprocess.run(['git', 'ls-remote', '--tags', UPSTREAM], capture_output=True, text=True, check=True, timeout=30)
    tags, peeled = {}, {}
    for line in result.stdout.splitlines():
        sha, ref = line.split('\t', 1)
        if not re.fullmatch(r'[0-9a-f]{40}', sha) or not ref.startswith('refs/tags/'): continue
        tag = ref.removeprefix('refs/tags/')
        if tag.endswith('^{}'): peeled[tag[:-3]] = sha
        else: tags[tag] = sha
    return [{'name': str(item.get('name') or item['tag_name'])[:120], 'tag': item['tag_name'],
             'revision': peeled.get(item['tag_name'], tags[item['tag_name']])}
            for item in data if not item.get('draft') and isinstance(item.get('tag_name'), str)
            and re.fullmatch(r'v?[0-9]+\.[0-9]+\.[0-9]+(?:[-.][A-Za-z0-9.-]+)?', item['tag_name'])
            and item['tag_name'] in tags]


def platform_path(name):
    path = PurePosixPath(name)
    return (bool(path.parts) and not path.is_absolute() and '..' not in path.parts
            and path.parts[0] not in PROTECTED
            and name not in {'hardware-configuration.nix', '.nixlab-files.json'})


def metadata(root):
    root = Path(root)
    explicit = rt.read_json(root / 'platform-release.json', {})
    if explicit.get('revision') and re.fullmatch(r'[0-9a-f]{40}', explicit['revision']):
        record = explicit
    else:
        result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True)
        revision = result.stdout.strip() if result.returncode == 0 else 'unversioned'
        record = {'version': version(root), 'revision': revision, 'repository': UPSTREAM}
    record['helperSources'] = rt.read_json(root / 'helpers/sources.json', rt.read_json(rt.SOURCE / 'helper-sources.json', {}))
    return record


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
    rt.write_json(source / 'platform-release.json', {'version': version(source), 'revision': revision, 'repository': UPSTREAM})
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
