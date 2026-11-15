"""Offline real-restic check: successful reads, corrupted packs and bad credentials."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase

import backup_verification as verification
from credential_fixtures import SECRETS, assert_public_clean


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    password = root / 'repository-password'
    password.write_text(SECRETS['staged'])
    password.chmod(0o600)
    os.environ.update(RESTIC_REPOSITORY=str(root / 'repository'), RESTIC_PASSWORD_FILE=str(password))
    subprocess.run(['restic', 'init'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    data = root / 'payload'
    data.write_bytes(os.urandom(1024 * 1024))
    subprocess.run(['restic', '--no-cache', 'backup', str(data)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = root / 'verification.json'
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        assert verification.check('metadata', state) == 0
        assert json.loads(state.read_text())['lastSuccess']['mode'] == 'metadata'
        assert verification.check('full', state) == 0
        successful = json.loads(state.read_text())['lastSuccess']
        pack = max((root / 'repository/data').glob('*/*'), key=lambda path: path.stat().st_size)
        # Restic makes packs read-only. Deliberately inject corruption as the
        # owning test user, then restore normal permissions before verification.
        original_mode = pack.stat().st_mode & 0o777
        pack.chmod(0o600)
        with pack.open('r+b') as stream:
            stream.seek(pack.stat().st_size // 2)
            byte = stream.read(1)
            stream.seek(-1, 1)
            stream.write(bytes([byte[0] ^ 0xff]))
        pack.chmod(original_mode)
        assert verification.check('full', state) == 1
        damaged = json.loads(state.read_text())
        assert damaged['phase'] == 'failed' and damaged['lastSuccess'] == successful
        password.write_text(SECRETS['portainer'])
        assert verification.check('metadata', state) == 1
        assert json.loads(state.read_text())['phase'] == 'failed'
    assert_public_clean(TestCase(), {'output': output.getvalue(), 'record': state.read_text()})
    assert state.stat().st_mode & 0o777 == 0o600
print('Offline restic metadata/full checks passed; damaged packs and bad credentials rejected. No restore claim.')
