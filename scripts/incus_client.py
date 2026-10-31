"""Shared local Incus command transport for backup and compatibility operations."""
import subprocess


def run(arguments, timeout, error_type, fallback, limit=None):
    result = subprocess.run(['incus', '--force-local', *arguments], text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        message = result.stderr.strip()
        if limit is not None: message = message[-limit:]
        raise error_type(message or fallback.format(code=result.returncode))
    return result.stdout
