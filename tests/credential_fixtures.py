"""Distinct synthetic credentials and a reusable public-artifact assertion.

Only pass public artifacts here. Protected credential files, Set-Cookie delivery
and explicitly authorized private downloads need separate access/lifetime tests.
"""
import base64
from collections.abc import Mapping
from urllib.parse import quote, quote_plus

SECRETS = {
    'setup': 'sentinel-setup-password-01+/=',
    'operator': 'sentinel-linux-password-02+/=',
    'portainer': 'sentinel-portainer-password-03+/=',
    'p12': 'sentinel-p12-passphrase-04+/=',
    'cookie': 'sentinel-session-cookie-05+/=',
    'staged': 'sentinel-staged-secret-06+/=',
    'private-key': 'sentinel-private-key-07+/=',
}


def credential_exposures(artifact):
    """Return categories only, without echoing a credential or submitted field."""
    if isinstance(artifact, Mapping):
        return set().union(set(), *(credential_exposures(part)
                                   for pair in artifact.items() for part in pair))
    if isinstance(artifact, (list, tuple, set)):
        return set().union(set(), *(credential_exposures(part) for part in artifact))
    if isinstance(artifact, bytes):
        artifact = artifact.decode('utf-8', errors='replace')
    if not isinstance(artifact, str):
        return set()
    exposed = set()
    for category, secret in SECRETS.items():
        forms = (secret, quote(secret, safe=''), quote_plus(secret),
                 base64.b64encode(secret.encode()).decode(), secret.encode().hex())
        if any(value in artifact for value in forms):
            exposed.add(category)
    return exposed


def assert_public_clean(test, artifacts):
    exposed = credential_exposures(artifacts)
    test.assertFalse(exposed, 'Credential categories exposed in public artifacts: ' + ', '.join(sorted(exposed)))
