"""Exercise a portal-issued certificate against the actual Incus HTTPS API."""
import base64
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile

import runtime as rt
from controller import Manager


def request(context, method, path, payload=None):
    connection = http.client.HTTPSConnection('127.0.0.1', 8443, context=context, timeout=5)
    try:
        body = json.dumps(payload) if payload is not None else None
        headers = {'Content-Type': 'application/json'} if body is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def revoke(fingerprint):
    subprocess.run(['incus', '--force-local', 'config', 'trust', 'remove', fingerprint],
                   check=True, capture_output=True, timeout=10)


def main():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        rt.STATE = root
        passphrase = 'disposable-browser-fixture-passphrase'
        bundle = Manager(root=root, state=root).browser_certificate('fixture', passphrase)
        fingerprint = bundle['fingerprint']
        try:
            archive = root / 'browser.p12'
            rt.atomic(archive, base64.b64decode(bundle['pkcs12']))
            private_identity = subprocess.run(
                ['openssl', 'pkcs12', '-in', str(archive), '-nodes', '-passin', 'stdin'],
                input=(passphrase + '\n').encode(), check=True, capture_output=True, timeout=10,
            ).stdout
            pem = root / 'identity.pem'
            rt.atomic(pem, private_identity)
            state_dir = Path(os.environ.get('INCUS_DIR', '/var/lib/incus'))
            context = ssl.create_default_context(cafile=str(state_dir / 'server.crt'))
            # The fixture pins the local daemon certificate, which may have
            # no loopback name in its subject alternative names.
            context.check_hostname = False
            context.load_cert_chain(pem)
            path = '/1.0/instances/native?project=lab'
            status, data = request(context, 'GET', path)
            assert status == 200 and data['metadata']['name'] == 'native', (status, data)
            private = '/1.0/instances/private-fixture?project=default'
            local = subprocess.run(['incus', '--force-local', 'query', private],
                                   check=True, capture_output=True, timeout=10)
            assert json.loads(local.stdout)['name'] == 'private-fixture'
            status, data = request(context, 'GET', '/1.0/instances?project=default')
            assert status == 200 and data['metadata'] == [], (status, data)
            status, data = request(context, 'GET', private)
            assert status in (403, 404), (status, data)
            status, data = request(context, 'PATCH', '/1.0', {'config': {}})
            assert status == 403, (status, data)
        finally:
            revoke(fingerprint)
        status, data = request(context, 'GET', path)
        assert status == 403, (status, data)


if __name__ == '__main__':
    main()
