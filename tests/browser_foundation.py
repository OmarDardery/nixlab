"""Browser QA against simulated inventory/API; never performs host operations."""
import argparse
import copy
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import shutil
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('/tmp/nixlab-browser'))
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    settings = json.loads((BASE / 'tests/fixtures/defaults.json').read_text())
    settings.update(ready=True, cpuVendor='intel', adminKeys=['ssh-ed25519 EXAMPLE'])
    settings['management'].update(interface='eth0', address='192.0.2.10', trustedCIDRs=['192.0.2.0/24'])
    status = {'settings': settings, 'effectiveSettings': copy.deepcopy(settings), 'setupComplete': True,
              'codeOverrides': [], 'job': None, 'pending': None, 'layoutVersion': 1, 'lastConfirmed': None}
    host = {'architecture': 'x86_64', 'cpuVendor': 'intel', 'uefi': True, 'kvm': True, 'disks': [], 'secretPresent': {},
            'interfaces': [{'name': 'eth0', 'kind': 'ethernet', 'addresses': ['192.0.2.10/24']}],
            'serialDevices': [{'path': '/dev/ttyUSB0', 'vendorId': '1234', 'productId': '5678', 'serial': 'EXAMPLE',
                               'product': 'Simulated USB serial device', 'driver': 'example', 'stablePaths': ['/dev/serial/by-id/example']}],
            'helpers': {'sources': json.loads((BASE / 'helpers/sources.json').read_text()),
                        'applications': list(json.loads((BASE / 'helpers/compatibility.json').read_text()).values())}}
    submitted = []; errors = []; authenticated = [False]; requests = []
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(BASE / 'admin/static')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            executable = shutil.which('chromium') or shutil.which('google-chrome')
            browser = p.chromium.launch(executable_path=executable, args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url))
            def api(route):
                endpoint = route.request.url.split('/api/', 1)[1]
                if endpoint == 'session' and not authenticated[0]:
                    route.fulfill(status=401, json={'error': 'Sign in to continue'}); return
                if route.request.method == 'POST':
                    body = route.request.post_data_json; submitted.append((endpoint, body))
                    if endpoint == 'login': authenticated[0] = True
                    if endpoint == 'review':
                        status['job'] = {'id': 'a'*32, 'status': 'building', 'settings': copy.deepcopy(body['settings']),
                                         'changes': [], 'moduleChanges': [], 'secretChanges': [], 'log': ''}
                    response = {'csrf': 'example', 'username': 'admin', 'role': 'admin'} if endpoint == 'login' else {'id': 'a'*32}
                else:
                    response = {'session': {'csrf': 'example', 'username': 'admin', 'role': 'admin'},
                                'status': status, 'inventory': host,
                                'operations': {'jobs': [], 'archives': [], 'generations': [], 'backupStatus': {}},
                                'health': {'services': [], 'certificates': {}, 'guestsAwaitingRestart': 0}, 'users': {'users': []}}.get(endpoint, {})
                route.fulfill(json=response)
            page.route('**/api/**', api)
            page.goto('http://127.0.0.1:' + str(server.server_port))
            page.get_by_role('heading', name='Sign in to your host.', exact=True).wait_for()
            assert page.get_by_text('HOST CONSOLE', exact=True).count() == 1
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(args.output / 'login-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.locator('input[name=password]').fill('disposable-ui-fixture-password')
            page.get_by_role('button', name='Sign in →', exact=True).click()
            page.get_by_role('heading', name='Your homelab.', exact=True).wait_for()
            page.evaluate('document.fonts.ready')
            assert page.evaluate("document.fonts.check('16px DM Sans')")
            assert page.evaluate("document.fonts.check('16px Space Grotesk')")
            page.screenshot(path=str(args.output / 'overview.png'), full_page=True)
            assert page.locator('[data-guest-action]').count() == 0
            page.locator('nav [data-go="commands"]').click()
            page.get_by_role('heading', name='Commands & docs', exact=True).wait_for()
            commands = page.locator('[data-copy-command]').evaluate_all("nodes => nodes.map(n => n.dataset.copyCommand)")
            assert len(commands) == len(set(commands))
            assert {'doctor', 'commissioning', 'helper-debian', 'compose-list'}.issubset(commands)
            page.get_by_text('Health, logs & SSH', exact=True).click()
            assert page.locator('#command-commissioning').text_content() == 'sudo nixlab commissioning collect --mode fresh'
            page.locator('[data-copy-command="commissioning"]').click()
            page.wait_for_function("['Copied', 'Selected'].includes(document.querySelector('[data-copy-command=commissioning]').textContent)")
            assert page.locator('[data-action=build]').count() == 0
            assert page.locator('a[href="https://github.com/community-scripts/Incus"]').count() == 1
            page.locator('nav [data-go="network"]').click()
            page.locator('[data-record-add="connectivity.bridges"]').click()
            page.locator('[data-record="connectivity.bridges"][data-key="interfaces"]').fill('eth0')
            page.locator('[data-path="management.interface"]').fill('br-lan')
            page.locator('[data-path="management.configure"]').check()
            page.locator('[data-go="hardware"]').click()
            page.get_by_role('button', name='Declare stable alias').click()
            assert page.locator('[data-record="hardware.serialAliases"][data-key="serial"]').input_value() == 'EXAMPLE'
            page.locator('nav [data-go="integrations"]').click()
            page.locator('[data-action="management-stack"]').click()
            assert page.locator('[data-path="cockpit.enable"]').is_checked()
            assert page.locator('[data-path="apps.portainerPort"]').input_value() == '9443'
            page.locator('[data-secret="cockpitPassword"]').fill('test-linux-account-password')
            page.locator('[data-secret="portainerPassword"]').fill('test-portainer-password')
            # Active links must not advertise an unapplied draft.
            assert page.locator('a[href="https://192.0.2.10:9443"]').count() == 0
            assert page.get_by_text('Upstream-supported; unverified here', exact=True).count() == 3
            page.locator('nav [data-go="operations"]').click()
            page.get_by_role('heading', name='Scheduled off-host backup').wait_for()
            assert page.locator('[data-guest-action]').count() == 0
            page.locator('nav [data-go="review"]').click()
            page.get_by_role('button', name='Build & review configuration', exact=True).click()
            page.wait_for_function("state.status.job?.status === 'building'")
            request = next(body for action, body in submitted if action == 'review')
            assert request['settings']['cockpit']['enable']
            assert request['settings']['maintenance']['laptopMode']
            assert request['secrets']['cockpitPassword'] == 'test-linux-account-password'
            assert request['secrets']['portainerPassword'] == 'test-portainer-password'
            assert request['settings']['connectivity']['bridges'][0]['interfaces'] == ['eth0']
            assert request['settings']['hardware']['serialAliases'][0]['serial'] == 'EXAMPLE'
            page.evaluate("state.principal.role='viewer';state.page='hardware';shell()")
            assert page.locator('[data-serial-pick]').is_disabled()
            assert page.locator('[data-record-add="hardware.serialAliases"]').is_disabled()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(args.output / 'hardware-mobile.png'), full_page=True)
            page.evaluate("state.principal.role='admin';state.page='integrations';shell()")
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.screenshot(path=str(args.output / 'integrations.png'), full_page=True)
            assert not errors, errors
            assert not [url for url in requests if not url.startswith('http://127.0.0.1:')], 'Private console assets must stay local'
            assert all(action != 'operation' for action, _ in submitted)
            browser.close()
    finally: server.shutdown(); server.server_close()
    print('Browser QA passed: host forms, review payload, role restrictions, mobile layout and no guest/script execution controls.')


if __name__ == '__main__': main()
