"""Arcane private authentication and upstream interactive account recovery."""
import http.client
import json
import subprocess
import sys
import diagnostics
import runtime as rt


def incus(*args, timeout=120):
    result = subprocess.run(['incus', '--force-local', *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ValueError('Arcane command failed')
    return result.stdout + result.stderr


def guest(*args, timeout=120):
    return incus('exec', 'apps', '--project', 'lab', '--', *args, timeout=timeout)


def authentication_ready(settings):
    path = rt.SECRET_PATHS['arcanePassword']
    if not path.is_file(): return False
    try:
        code, data = diagnostics.request(diagnostics.management_address(settings), settings['apps']['arcanePort'],
            '/api/auth/login', {'username': 'admin', 'password': path.read_text().strip()})
        response = json.loads(data)
        return code == 200 and isinstance(response, dict) and isinstance(response.get('data'), dict) and isinstance(response['data'].get('token'), str) and bool(response['data']['token'])
    except (OSError, ValueError, AttributeError, http.client.HTTPException):
        return False


def reset(settings):
    if not settings['apps']['enable'] or not sys.stdin.isatty():
        raise ValueError('Use a private host terminal with the applications VM enabled')
    try:
        result = subprocess.run(['incus', '--force-local', 'exec', 'apps', '--project', 'lab', '--mode', 'interactive', '--',
            'docker', 'exec', '-it', '-e', 'ARCANE_ALLOW_CLI_PASSWORD_RESET=true', 'arcane',
            '/app/arcane', 'admin', 'reset-password', '--username', 'admin'], check=False)
    except OSError:
        raise ValueError('Arcane account recovery failed') from None
    if result.returncode: raise ValueError('Arcane account recovery failed')
    return {'url': 'https://' + diagnostics.management_address(settings) + ':' + str(settings['apps']['arcanePort'])}
