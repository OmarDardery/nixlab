"""Exercise a portal-issued certificate against the actual Incus HTTPS API."""
import base64
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import time
from unittest.mock import patch

import runtime as rt
import browser_certificates
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
        manager = Manager(root=root, state=root)
        previous = subprocess.run(['incus', '--force-local', 'query', '/1.0/certificates?recursion=1'],
                                  check=True, capture_output=True, timeout=10)
        existing = {item['fingerprint'] for item in json.loads(previous.stdout)}
        bundle = manager.browser_certificate('fixture', passphrase)
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
            context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
            # The fixture pins the local daemon certificate, which may have
            # no loopback name in its subject alternative names.
            context.check_hostname = False
            context.load_cert_chain(pem)
            path = '/1.0/instances/native?project=lab'
            status, data = request(context, 'GET', path)
            assert status == 200 and data['metadata']['name'] == 'native', (status, data)
            status, data = request(context, 'PATCH', path, {'config': {'user.browser-certificate-fixture': 'permitted'}})
            assert status in (200, 202), (status, data)
            if status == 202:
                status, finished = request(context, 'GET', data['operation'].split('?', 1)[0] + '/wait?timeout=10&project=lab')
                assert status == 200 and finished['metadata']['status_code'] == 200, (status, finished)
            status, updated = request(context, 'GET', path)
            assert status == 200 and updated['metadata']['config']['user.browser-certificate-fixture'] == 'permitted', (status, updated)
            private = '/1.0/instances/private-fixture?project=default'
            local = subprocess.run(['incus', '--force-local', 'query', private],
                                   check=True, capture_output=True, timeout=10)
            assert json.loads(local.stdout)['name'] == 'private-fixture'
            status, data = request(context, 'GET', '/1.0/instances?project=default')
            assert status == 200 and data['metadata'] == [], (status, data)
            status, data = request(context, 'GET', private)
            assert status in (403, 404), (status, data)
            status, data = request(context, 'PATCH', private, {'config': {'user.browser-certificate-fixture': 'forbidden'}})
            assert status in (403, 404), (status, data)
            status, data = request(context, 'PATCH', '/1.0', {'config': {}})
            assert status == 403, (status, data)
            owned = manager.browser_certificates()['certificates']
            assert any(item['fingerprint'] == fingerprint and item['restricted'] and item['projects'] == ['lab'] for item in owned)
        finally:
            manager.revoke_browser_certificate(fingerprint)
        status, data = request(context, 'GET', path)
        assert status == 403, (status, data)
        # Renewal leaves the previous identity trusted until explicitly revoked;
        # elevated scope is possible only through an explicit root/admin action.
        elevated = manager.browser_certificate('fixture-admin', passphrase, full_admin=True)
        try:
            archive.write_bytes(base64.b64decode(elevated['pkcs12']))
            extracted = subprocess.run(['openssl', 'pkcs12', '-in', str(archive), '-nodes', '-passin', 'stdin'],
                                       input=(passphrase + '\n').encode(), check=True, capture_output=True).stdout
            rt.atomic(pem, extracted)
            elevated_context = ssl.create_default_context(cafile=str(state_dir / 'server.crt'))
            elevated_context.check_hostname = False
            elevated_context.minimum_version = elevated_context.maximum_version = ssl.TLSVersion.TLSv1_3
            elevated_context.load_cert_chain(pem)
            status, data = request(elevated_context, 'GET', private)
            assert status == 200, (status, data)
            status, data = request(elevated_context, 'PATCH', '/1.0', {'config': {}})
            assert status == 200, (status, data)
            future = time.time() + 91 * 86400
            with patch.object(browser_certificates.time, 'time', return_value=future), rt.locked():
                browser_certificates.expire_owned(root)
            status, data = request(elevated_context, 'GET', private)
            assert status == 403, (status, data)
        finally:
            manager.revoke_browser_certificate(elevated['fingerprint'])
        after = subprocess.run(['incus', '--force-local', 'query', '/1.0/certificates?recursion=1'],
                               check=True, capture_output=True, timeout=10)
        assert existing <= {item['fingerprint'] for item in json.loads(after.stdout)}


if __name__ == '__main__':
    main()
