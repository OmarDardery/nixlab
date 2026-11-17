"""Open root operation locks without following links or truncating existing files."""
import os
import stat


def open_lock(path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise ValueError('Operation lock must be a singly linked regular file owned by the current privileged user')
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, 'r+')
    except Exception:
        os.close(descriptor)
        raise
