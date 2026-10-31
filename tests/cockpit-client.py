"""Actual PAM login, managed TLS and host shell channel in a disposable NixOS VM."""
import base64
import json
import ssl
import urllib.error
import urllib.request
import websocket

origin = 'https://127.0.0.1:9090'
ca = '/var/lib/nixlab-admin/ca.crt'
context = ssl.create_default_context(cafile=ca)
token = base64.b64encode(b'operator:vm-cockpit-password').decode()
request = urllib.request.Request(origin + '/cockpit/login', headers={'Authorization': 'Basic ' + token, 'Origin': origin})
with urllib.request.urlopen(request, context=context, timeout=30) as response:
    identity = json.load(response)
    assert identity['user'] == 'operator', identity
    cookies = '; '.join(value.split(';', 1)[0] for value in response.headers.get_all('Set-Cookie', []))
assert cookies
ws = websocket.create_connection('wss://127.0.0.1:9090/cockpit/socket', origin=origin,
                                 cookie=cookies, subprotocols=['cockpit1'], sslopt={'ca_certs': ca}, timeout=30)
try:
    initial = ws.recv()
    assert json.loads(initial.split('\n', 1)[1])['command'] == 'init', initial
    ws.send('\n' + json.dumps({'command': 'init', 'version': 1}))
    ws.send('\n' + json.dumps({'command': 'open', 'channel': 'terminal-test', 'payload': 'stream',
                              'spawn': ['/run/current-system/sw/bin/sh', '-c', 'printf NIXLAB_COCKPIT_TERMINAL_OK'],
                              'pty': True}))
    output = ''
    for _ in range(100):
        message = ws.recv()
        if isinstance(message, bytes): message = message.decode()
        channel, content = message.split('\n', 1)
        if channel == 'terminal-test': output += content
        elif not channel:
            control = json.loads(content)
            if control.get('command') == 'close' and control.get('channel') == 'terminal-test':
                assert not control.get('problem'), control
                assert control.get('exit-status', 0) == 0, control
                break
    assert 'NIXLAB_COCKPIT_TERMINAL_OK' in output, output
finally:
    ws.close()
print('Managed Cockpit TLS, local-account login and host terminal channel passed.')
