"""Portainer bootstrap credentials and an offline, backed-up recovery operation."""
import http.client
import json
from pathlib import Path
import re
import subprocess
import time

import diagnostics
import runtime as rt

HELPER_IMAGE = 'portainer/helper-reset-password:latest'


def incus(*args, timeout=120):
    result = subprocess.run(['incus', '--force-local', *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Helper output may contain a password; never include it in exceptions.
        raise ValueError('Portainer recovery command failed: ' + args[0])
    return result.stdout + result.stderr


def guest(*args, timeout=120):
    return incus('exec', 'apps', '--project', 'lab', '--', *args, timeout=timeout)


def credentials(settings):
    path = rt.SECRET_PATHS['portainerPassword']
    if not path.is_file():
        return None
    saved = rt.read_json(rt.STATE / 'portainer-account.json', {})
    username = saved.get('username', 'admin')
    password = path.read_text().strip()
    try:
        code, data = diagnostics.request(diagnostics.management_address(settings), settings['apps']['portainerPort'],
                                         '/api/auth', {'Username': username, 'Password': password})
        if code == 200 and json.loads(data).get('jwt'):
            return {'username': username, 'password': password}
    except (OSError, ValueError, http.client.HTTPException):
        pass
    return None


def reset(settings, helper=HELPER_IMAGE):
    if not settings['apps']['enable']:
        raise ValueError('The applications VM is not enabled')
    stamp = str(time.time_ns())
    backup = '/var/lib/nixlab/portainer-recovery/' + stamp
    # Download before stopping so an unavailable registry cannot cause downtime.
    guest('docker', 'pull', helper, timeout=600)
    guest('install', '-d', '-m', '0700', backup)
    guest('systemctl', 'stop', 'docker-portainer.service')
    failure = None
    try:
        guest('tar', '-C', '/srv/portainer', '-czf', backup + '/data.tar.gz', '.', timeout=600)
        output = guest('docker', 'run', '--rm', '--network', 'none', '-v', '/srv/portainer:/data', helper,
                       timeout=120)
        # Retain the recovery result privately even if a future helper changes its format.
        rt.atomic(rt.STATE / ('portainer-reset-' + stamp + '.txt'), output)
        user = re.search(r'updated for user:\s*([^\r\n]+)', output, re.I)
        password = re.search(r'Use the following password to login:\s*([^\r\n]+)', output)
        if not user or not password:
            raise ValueError('Helper output format changed; inspect the root-only recovery result in ' + str(rt.STATE))
        rt.atomic(rt.SECRET_PATHS['portainerPassword'], password[1].strip() + '\n')
        incus('file', 'push', str(rt.SECRET_PATHS['portainerPassword']),
              'apps/var/lib/nixlab-secrets/portainer-admin-password', '--project', 'lab', '--mode', '0600')
        rt.write_json(rt.STATE / 'portainer-account.json', {'username': user[1].strip(), 'resetAt': time.time(), 'backup': backup})
    except Exception as exc:
        failure = exc
    finally:
        try:
            guest('systemctl', 'start', 'docker-portainer.service')
        except Exception as restart:
            raise ValueError(str(failure or 'Password reset completed') + '; restart failed: ' + str(restart) +
                             '; backup: ' + backup) from restart
    if failure:
        raise ValueError(str(failure) + '; Portainer restarted; backup: ' + backup) from failure
    diagnostics.wait_ready(settings)
    result = credentials(settings)
    if not result:
        raise ValueError('Portainer restarted but authentication failed; backup: ' + backup)
    return {**result, 'backup': backup, 'url': 'https://' + diagnostics.management_address(settings) + ':' + str(settings['apps']['portainerPort'])}
