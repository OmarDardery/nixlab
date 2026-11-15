"""Read normalized desired settings from the selected source's pure Nix entrypoint."""
import json
from pathlib import Path
import subprocess


def read_settings(root):
    # site.nix imports only local defaults/layout/migrations. No flake fetch,
    # host build or second Python migration interpretation is needed here.
    try:
        result = subprocess.run(['nix-instantiate', '--eval', '--strict', '--json',
                                 str(Path(root) / 'site.nix')],
                                capture_output=True, text=True, timeout=10)
        if result.returncode:
            if 'Unsupported NixLab settings schema version' in result.stderr:
                raise ValueError('Unsupported NixLab settings schema version; use compatible platform source')
            raise ValueError('Nix settings evaluation failed; inspect the source from the host console')
        settings = json.loads(result.stdout)
    except (OSError, UnicodeError, subprocess.SubprocessError):
        raise ValueError('Nix settings evaluation is unavailable; inspect the host console') from None
    except json.JSONDecodeError:
        raise ValueError('Nix settings evaluation returned invalid JSON') from None
    if not isinstance(settings, dict):
        raise ValueError('Nix settings evaluation must return an object')
    return settings
