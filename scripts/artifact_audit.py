"""Offline advisory inventory. Text references cannot prove dynamic/external reachability."""
import argparse
import json
from collections import Counter
from pathlib import Path, PurePosixPath
import subprocess

# These files are known public entrypoints or recovery contracts even when no
# other tracked file mentions their path. This allowlist is deliberately small:
# extending it requires a compatibility reason, not a desire to silence reports.
PUBLIC_ENTRYPOINTS = frozenset({
    'flake.nix', 'flake.lock', 'configuration.nix',
    'configuration-bootstrap.nix', 'site.nix', 'site-defaults.nix',
    'lib/platform.nix', 'admin/package.nix', 'installer/package.nix',
    'admin/main.py', 'installer/install.py', 'installer/update.py',
    'admin/static/index.html', 'scripts/release.py',
    'scripts/artifact_audit.py',
})

TEXT_EXTENSIONS = frozenset({'.py', '.js', '.nix', '.css', '.html', '.md', '.json', '.yml', '.yaml', '.sh'})
SOURCE_EXTENSIONS = frozenset({'.py', '.js', '.nix', '.css', '.html'})
MAX_TEXT_BYTES = 2 * 1024 * 1024


def classify(name):
    if name.endswith(('.pyc', '.pyo', '.class')) or '/__pycache__/' in name:
        return 'generated (tracked)'
    if name in PUBLIC_ENTRYPOINTS:
        return 'public entrypoint'
    if name.startswith('tests/fixtures/') or name.startswith('tests/module-tree/'):
        return 'test fixture'
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
    omitted = {}
    for name in names:
        path = PurePosixPath(name)
        if not name or '\0' in name or path.is_absolute() or '..' in path.parts:
            raise ValueError('Invalid tracked path')
        target = root / name
        # Check every parent too: a tracked path under a symlinked directory
        # must not cause this read-only audit to inspect files outside root.
        if any((root / PurePosixPath(*path.parts[:i])).is_symlink()
               for i in range(1, len(path.parts) + 1)):
            omitted[name] = 'symlink: target not followed'
            continue
        if not target.is_file():
            omitted[name] = 'missing or non-file tracked entry'
            continue
        # Advisory analysis does not read binary assets or huge generated files.
        if target.stat().st_size > MAX_TEXT_BYTES:
            omitted[name] = 'larger than text inspection limit'
            continue
        if target.suffix not in TEXT_EXTENSIONS and target.name not in ('VERSION', '.gitignore'):
            omitted[name] = 'binary or unsupported text format'
            continue
        try:
            contents[name] = target.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            omitted[name] = 'unreadable text or invalid UTF-8'
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
        protected = name in PUBLIC_ENTRYPOINTS
        artifacts.append({'path': name, 'classification': classify(name),
                          'observedReferences': refs,
                          'inspection': 'text' if name in contents else 'not-inspected',
                          'confidence': 'text-reference-only' if refs else 'unknown',
                          'action': 'keep' if refs or protected else 'investigate',
                          'publicCompatibility': 'protected known entrypoint' if protected else 'unknown; review entrypoints and external callers'})
    # A *tracked* compatibility manifest is advisory input, not proof that an
    # externally published Nix attribute or CLI alias has no consumers.
    try:
        contract = json.loads(contents.get('tests/fixtures/architecture-contract.json', '{}'))
        if not isinstance(contract, dict):
            contract = {}
    except (ValueError, TypeError):
        contract = {}
    lines = {name: len(text.splitlines()) for name, text in contents.items()}
    category_counts = dict(sorted(Counter(classify(name) for name in names).items()))
    baseline = {
        'trackedFiles': len(names),
        'sourceFiles': sum(name.startswith(('admin/', 'scripts/', 'installer/', 'lib/', 'modules/', 'guests/', 'helpers/'))
                           and PurePosixPath(name).suffix in SOURCE_EXTENSIONS for name in names),
        'testFiles': sum(name.startswith('tests/') for name in names),
        'sourceLinesInspected': sum(lines[name] for name in contents if not name.startswith('tests/')
                                    and PurePosixPath(name).suffix in SOURCE_EXTENSIONS),
        'testLinesInspected': sum(lines[name] for name in contents if name.startswith('tests/')
                                  and PurePosixPath(name).suffix in SOURCE_EXTENSIONS),
        'classifications': category_counts,
        'cliBinaries': sorted(contract.get('cliBinaries', {})),
        'publicFlakeAttributesFromContract': contract.get('flakeAttributes', []),
        'machinePathsFromContract': contract.get('machinePaths', {}),
        'runtimePathsFromContract': contract.get('runtimeDefaults', {}),
        'systemdUnitsFromContract': contract.get('systemdUnits', {}),
        'unresolvedPolicyDuplicationToReview': [
            'settings migration and override precedence (#49)',
            'installer/portal activation and access confirmation (#50)',
            'management bind and firewall policy (#51)',
            'transaction, backup and deployment job utilities (#52)',
            'flake validation configurations and fixtures (#54)',
        ],
    }
    return {'version': 1, 'advisory': True,
            'limitations': ['Text matching includes false positives.',
                           'Dynamic imports, generated units and external callers require review.',
                           'Partial/sparse checkouts and binary files are not source-inspected.',
                           'No artifact is proven safe to delete; no host state was inspected.'],
            'trackedCount': len(names), 'textCount': len(contents),
            'inspectionOmissions': dict(sorted(omitted.items())),
            'baseline': baseline, 'artifacts': artifacts}


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
