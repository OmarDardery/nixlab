"""Inspect local login readiness without exposing shadow hashes in output."""
import grp
import pwd
import secrets
import subprocess

import runtime as rt


def shadow(username):
    result = subprocess.run(['getent', 'shadow', username], capture_output=True, text=True, timeout=10)
    if result.returncode:
        return None
    fields = result.stdout.strip().split(':')
    return fields[1] if len(fields) == 9 and fields[0] == username else None


def cockpit_accounts():
    try:
        wheel = grp.getgrnam('wheel')
    except KeyError:
        return []
    accounts = []
    for account in pwd.getpwall():
        if account.pw_uid == 0 or account.pw_shell.endswith(('/nologin', '/false')):
            continue
        if account.pw_name not in wheel.gr_mem and account.pw_gid != wheel.gr_gid:
            continue
        encoded = shadow(account.pw_name)
        if encoded and not encoded.startswith(('!', '*')):
            accounts.append(account.pw_name)
    return accounts


def ensure_operator_password():
    plain = rt.STATE / 'operator-password'
    encoded = rt.SECRET_PATHS['cockpitPassword']
    if not encoded.exists():
        if not plain.exists():
            rt.atomic(plain, secrets.token_urlsafe(24) + '\n')
        password = plain.read_text().strip()
        result = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=password + '\n',
                                capture_output=True, text=True, check=True, timeout=30)
        rt.atomic(encoded, result.stdout)
