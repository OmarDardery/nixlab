"""Offline advisory inventory. Text references cannot prove dynamic/external reachability."""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess


def classify(name):
    if name.startswith('tests/'):
        return 'test'
    if name.startswith(('docs/', 'website/', 'examples/')) or name.endswith('.md'):
        return 'documentation/example'
    if name.startswith('.github/'):
        return 'automation'
    if name.endswith('.nix'):
        return 'Nix module/entrypoint'
    if name.endswith(('.py', '.js', '.css', '.html')):
        return 'runtime/asset'
    return 'data/configuration'


def audit(root, names):
    names = sorted(set(names))
    contents = {}
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Invalid tracked path')
        target = root / name
        if target.is_symlink() or not target.is_file():
            continue
        # Advisory analysis does not read binary assets or huge generated files.
        if target.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            contents[name] = target.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            pass
    artifacts = []
    for name in names:
        path = PurePosixPath(name)
        # Include basename references for Nix interpolation, shell wrappers and assets;
        # module names cover Python and JS imports. A hit is evidence, not proof.
        tokens = {name, path.name}
        if path.suffix in ('.py', '.js'):
            tokens.add(path.stem)
        refs = [source for source, text in contents.items()
                if source != name and any(token in text for token in tokens)]
        artifacts.append({'path': name, 'classification': classify(name),
                          'observedReferences': refs,
                          'confidence': 'text-reference-only' if refs else 'unknown',
                          'action': 'keep' if refs else 'investigate',
                          'publicCompatibility': 'unknown; review entrypoints and external callers'})
    return {'version': 1, 'advisory': True,
            'limitations': ['Text matching includes false positives.',
                           'Dynamic imports, generated units and external callers require review.',
                           'No artifact is proven safe to delete; no host state was inspected.'],
            'trackedCount': len(names), 'textCount': len(contents),
            'artifacts': artifacts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    result = subprocess.run(['git', '-C', str(args.root), 'ls-files', '-z'],
                            check=True, capture_output=True)
    names = [name.decode('utf-8') for name in result.stdout.split(b'\0') if name]
    print(json.dumps(audit(args.root, names), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
