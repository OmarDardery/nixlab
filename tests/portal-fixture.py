"""Disposable NixOS VM credentials; never executed by installer outputs."""
import grp
import os
from pathlib import Path
import runtime as rt

root = rt.ROOT
root.mkdir(parents=True, mode=0o700, exist_ok=True)
rt.write_json(root / 'machine/format.json', {'version': 1})
import json
settings = json.loads(Path('/etc/nixlab-test-settings.json').read_text())
rt.write_json(root / 'machine/settings.json', settings)
rt.write_json(root / 'machine/installation.json', {'interface': 'eth0', 'trustedCIDRs': ['127.0.0.0/8']})
rt.write_json(root / '.nixlab-files.json', ['machine/format.json', 'machine/settings.json', 'machine/installation.json'])
rt.STATE.mkdir(parents=True, mode=0o710, exist_ok=True)
os.chown(rt.STATE, 0, grp.getgrnam('nixlab-ui').gr_gid)
rt.STATE.chmod(0o710)
rt.write_json(rt.STATE / 'auth.json', rt.password_record('vm-integration-password'), mode=0o640, group='nixlab-ui')
rt.issue_tls('127.0.0.1', 'nixlab-test')
