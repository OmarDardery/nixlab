"""Publish a persistent Arcane encryption key once, without secret output."""
import os
import json
from pathlib import Path
import re
import secrets
import sys
import tempfile


def ensure(path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'r') as stream:
            if not re.fullmatch(r'[0-9a-f]{64}', stream.read(66)):
                raise ValueError('Arcane encryption key is invalid; restore the protected key backup')
        if path.stat().st_mode & 0o077:
            raise ValueError('Arcane encryption key permissions must be root-only')
        return
    marker = path.parent / '.nixlab-bootstrap.json'
    if any(path.parent.glob('arcane.db*')) or marker.exists() or marker.is_symlink():
        raise ValueError('Existing Arcane database requires its protected encryption key backup')
    fd, temporary = tempfile.mkstemp(prefix='.arcane-key-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(secrets.token_hex(32)); stream.flush(); os.fsync(stream.fileno())
        try: os.link(temporary, path)
        except FileExistsError: pass
        else:
            # Only first key publication may attest that this data directory is
            # new. Missing DB + an older key must never reopen enrollment.
            marker_fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(marker_fd, 'w') as stream:
                stream.write(json.dumps({'format':1,'state':'fresh'})); stream.flush(); os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)
    ensure(path)


if __name__ == '__main__':
    try: ensure(sys.argv[1])
    except (OSError, ValueError, IndexError):
        raise SystemExit('Arcane encryption key is unavailable; inspect protected storage or restore its backup') from None
