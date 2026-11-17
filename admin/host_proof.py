"""Authenticate a local host administrator without retaining credentials."""
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import threading

import runtime as rt

PAM_SERVICE = 'nixlab-enrollment'
ROOT_SHELL = '/run/current-system/sw/bin/bash'
SUDO = '/run/wrappers/bin/sudo'
SLOTS = threading.BoundedSemaphore(2)


def verify(username, password):
    if (not isinstance(username, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', username)
            or not isinstance(password, str) or not 1 <= len(password) <= 1024
            or any(c in password for c in '\x00\r\n')):
        raise rt.PublicError('Host verification failed; use a valid local sudo administrator or console approval')
    if not SLOTS.acquire(blocking=False):
        raise rt.PublicError('Host verification is busy; wait and retry')
    try:
        # Only local interactive identities qualify; NSS-only accounts are outside
        # this bootstrap path. PAM performs both password AND account checks.
        local = any(line.split(':', 1)[0] == username for line in Path('/etc/passwd').read_text().splitlines())
        user = pwd.getpwnam(username)
        helper = shutil.which('pamtester')
        if (not local or (user.pw_uid != 0 and user.pw_uid < 1000)
                or Path(user.pw_shell).name in {'false', 'nologin'} or not helper
                or not Path('/etc/pam.d/' + PAM_SERVICE).is_file()):
            raise ValueError()
        result = subprocess.run([helper, PAM_SERVICE, username,
                'authenticate(PAM_SILENT|PAM_DISALLOW_NULL_AUTHTOK)', 'acct_mgmt'],
            input=(password + '\n').encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        if result.returncode:
            raise ValueError()
        # Listing a specific argument-free root shell consults the real sudoers
        # policy without executing it. Limited sudo commands and wheel membership
        # alone are insufficient. Never feed the password to sudo.
        result = subprocess.run([SUDO, '-n', '-l', '-U', username, '-u', 'root', '--', ROOT_SHELL],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            env={'PATH': '/run/current-system/sw/bin:/run/wrappers/bin', 'LC_ALL': 'C'})
        if result.returncode:
            raise ValueError()
        return username
    except (OSError, KeyError, ValueError, subprocess.SubprocessError):
        raise rt.PublicError('Host verification failed; use a valid local sudo administrator or console approval') from None
    finally:
        SLOTS.release()
