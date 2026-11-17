"""Browser QA against simulated inventory/API; never performs host operations."""
import argparse
import copy
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import shutil
from pathlib import Path
import threading
import time
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
              'codeOverrides': [], 'job': None, 'pending': None, 'layoutVersion': 1, 'lastConfirmed': None, 'release': {'version': (BASE / 'VERSION').read_text().strip(), 'revision': 'a' * 40}}
    host = {'architecture': 'x86_64', 'cpuVendor': 'intel', 'uefi': True, 'kvm': True, 'disks': [], 'secretPresent': {},
            'interfaces': [{'name': 'eth0', 'kind': 'ethernet', 'addresses': ['192.0.2.10/24']}],
            'serialDevices': [{'path': '/dev/ttyUSB0', 'vendorId': '1234', 'productId': '5678', 'serial': 'EXAMPLE',
                               'product': 'Simulated USB serial device', 'driver': 'example', 'stablePaths': ['/dev/serial/by-id/example']}],
            'helpers': {'sources': json.loads((BASE / 'helpers/sources.json').read_text()),
                        'applications': list(json.loads((BASE / 'helpers/compatibility.json').read_text()).values())}}
    submitted = []; errors = []; authenticated = [False]; requests = []
    certificate_records = []; certificate_pending = [False]; bootstrap_approved = [False]
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
                    if endpoint == 'browser-certificate':
                        record = {'name': 'nixlab-browser-ui-000000000000', 'device': body['device'],
                                  'fingerprint': 'a' * 64, 'restricted': not body.get('fullAdmin', False),
                                  'projects': ['lab'], 'expiresAt': 'fixture-expiry', 'status': 'trusted'}
                        certificate_records.append(record); certificate_pending[0] = True
                        route.fulfill(json=record); return
                    if endpoint == 'browser-certificate/download':
                        if not certificate_pending[0]:
                            route.fulfill(status=400, json={'error': 'Already claimed'}); return
                        certificate_pending[0] = False
                        route.fulfill(content_type='application/x-pkcs12', body=b'encrypted-browser-ui-fixture'); return
                    if endpoint == 'revoke-browser-certificate':
                        certificate_records[0]['status'] = 'revoked'
                        route.fulfill(json={'ok': True}); return
                    if endpoint == 'bootstrap/verify':
                        bootstrap_approved[0] = True
                        route.fulfill(json={'approved': True}); return
                    if endpoint == 'bootstrap/claim':
                        authenticated[0] = True
                        route.fulfill(json={'csrf': 'example', 'username': body['username'], 'role': 'admin'}); return
                    if endpoint == 'login': authenticated[0] = True
                    if endpoint == 'review':
                        status['job'] = {'id': 'a'*32, 'status': 'building', 'settings': copy.deepcopy(body['settings']),
                                         'changes': [], 'moduleChanges': [], 'secretChanges': [], 'log': ''}
                    if endpoint == 'deploy-guests':
                        status['job']['guestDeployment'] = {'status': 'queued', 'targets': {}}
                    if endpoint == 'apply':
                        status['job']['impactPlan']['blocked'] = True
                        status['job']['impactPlan']['refreshedAfterDrift'] = True
                        route.fulfill(status=400, json={'error': 'Runtime changed since review; inspect the refreshed plan'})
                        return
                    response = {'csrf': 'example', 'username': 'admin', 'role': 'admin'} if endpoint == 'login' else {'id': 'a'*32}
                else:
                    response = {'session': {'csrf': 'example', 'username': 'admin', 'role': 'admin'},
                                'status': status, 'inventory': host,
                                'bootstrap': {'csrf': 'bootstrap-fixture-csrf', 'approvalCode': 'f' * 32, 'approved': bootstrap_approved[0], 'expiresIn': 600},
                                'guest-network': {'available': True, 'checkedAt': int(time.time()), 'conflicts': [], 'guests': [
                                    {'name': 'web', 'project': 'lab', 'type': 'container', 'status': 'Running', 'observed': ['10.77.0.20'],
                                     'nics': [{'name': 'eth0', 'network': 'incusbr0', 'observed': ['10.77.0.20'], 'reserved': ['10.77.0.20']}]}]},
                                'main-revision': {'revision': 'b' * 40},
                                'browser-certificates': {'certificates': certificate_records},
                                'operations': {'jobs': [], 'archives': [], 'generations': [], 'backupStatus': {}},
                                'health': {'checkedAt': int(time.time()), 'services': [], 'certificates': {}, 'guestsAwaitingRestart': 0}, 'users': {'users': []}}.get(endpoint, {})
                route.fulfill(json=response)
            page.route('**/api/**', api)
            page.goto('http://127.0.0.1:' + str(server.server_port))
            page.get_by_role('heading', name='Sign in to your host.', exact=True).wait_for()
            assert page.get_by_text('sudo nixlab-admin reset-password', exact=True).is_visible()
            # The host credential is transient; a verified browser creates an
            # independent named portal identity and receives an ordinary session.
            page.evaluate('showEnrollment()')
            page.get_by_role('heading', name='Verify this machine', exact=True).wait_for()
            assert page.locator('#enrollment-form button').is_disabled()
            page.locator('#host-proof-form [name="hostUsername"]').fill('operator')
            page.locator('#host-proof-form [name="hostPassword"]').fill('browser-host-proof-password')
            page.get_by_role('button', name='Verify host administrator', exact=True).click()
            page.wait_for_function('document.querySelector("#host-proof-form [name=hostPassword]").value === ""')
            assert page.locator('#enrollment-form button').is_enabled()
            page.locator('#enrollment-form [name="username"]').fill('portalowner')
            page.locator('#enrollment-form [name="password"]').fill('browser-independent-portal-password')
            assert 'browser-host-proof-password' not in page.content()
            assert 'browser-host-proof-password' not in page.evaluate('JSON.stringify(state)')
            page.get_by_role('button', name='Create administrator', exact=True).click()
            page.wait_for_function('state.principal?.username === "portalowner" && state.csrf === "example"')
            page.get_by_role('heading', name='Overview', exact=True).wait_for()
            claim = next(body for endpoint, body in submitted if endpoint == 'bootstrap/claim')
            assert claim['username'] == 'portalowner' and 'hostPassword' not in claim
            authenticated[0] = False
            page.reload()
            page.get_by_role('heading', name='Sign in to your host.', exact=True).wait_for()

            assert 'setup password printed' not in page.locator('.login-card').text_content()
            assert page.get_by_text('HOST CONSOLE', exact=True).count() == 1
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(args.output / 'login-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.locator('input[name=password]').fill('disposable-ui-fixture-password')
            page.get_by_role('button', name='Sign in →', exact=True).click()
            page.get_by_role('heading', name='Overview', exact=True).wait_for()
            page.evaluate('document.fonts.ready')
            assert page.evaluate("document.fonts.check('16px DM Sans')")
            assert page.evaluate("document.fonts.check('16px Space Grotesk')")
            assert page.get_by_role('heading', name='Guests & addresses', exact=True).is_visible()
            assert page.get_by_text('10.77.0.20', exact=True).count() == 2
            assert page.locator('.nav > button').count() == 14
            assert page.locator('.nav [aria-current="page"]').count() == 1
            page.screenshot(path=str(args.output / 'overview.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(args.output / 'overview-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            assert page.locator('[data-guest-action]').count() == 0
            page.evaluate("state.page='security';shell()")
            assert not page.locator('#certificate-full-admin').is_checked()
            page.locator('#certificate-device').fill('browser-qa')
            page.locator('#certificate-passphrase').fill('synthetic-browser-bundle-passphrase')
            page.locator('#certificate-current').fill('disposable-ui-fixture-password')
            with page.expect_download() as transfer:
                page.get_by_role('button', name='Create an Incus browser identity', exact=True).click()
            assert transfer.value.suggested_filename.endswith('.p12')
            assert page.locator('#certificate-passphrase').input_value() == ''
            assert page.locator('#certificate-current').input_value() == ''
            issued = next(body for endpoint, body in submitted if endpoint == 'browser-certificate')
            assert issued['fullAdmin'] is False and issued['acknowledgeFullAdmin'] is False
            page.get_by_role('button', name='Refresh browser identities', exact=True).click()
            page.get_by_role('button', name='Renew', exact=True).click()
            assert page.locator('#certificate-renew').input_value() == 'a' * 64
            assert page.locator('#certificate-device').input_value() == 'browser-qa'
            page.locator('#certificate-current').fill('disposable-ui-fixture-password')
            page.get_by_role('button', name='Revoke', exact=True).click()
            page.wait_for_function("document.querySelector('#certificate-list').textContent.includes('revoked')")
            assert page.get_by_role('button', name='Revoke', exact=True).is_disabled()
            page.locator('.sidebar nav [data-go="commands"]').click()
            page.get_by_role('heading', name='Help', exact=True).wait_for()
            commands = page.locator('[data-copy-command]').evaluate_all("nodes => nodes.map(n => n.dataset.copyCommand)")
            assert len(commands) == len(set(commands))
            assert {'doctor', 'commissioning', 'helper-debian', 'compose-list'}.issubset(commands)
            page.get_by_text('Health, logs & SSH', exact=True).click()
            assert page.locator('#command-commissioning').text_content() == 'sudo nixlab commissioning collect --mode fresh'
            page.locator('[data-copy-command="commissioning"]').click()
            page.wait_for_function("['Copied', 'Selected'].includes(document.querySelector('[data-copy-command=commissioning]').textContent)")
            assert page.locator('[data-action=build]').count() == 0
            assert page.locator('a[href="https://github.com/community-scripts/Incus"]').count() == 1
            page.locator('.sidebar nav [data-go="network"]').click()
            page.locator('[data-record-add="connectivity.bridges"]').click()
            page.locator('[data-record="connectivity.bridges"][data-key="interfaces"]').fill('eth0')
            page.locator('[data-path="management.interface"]').fill('br-lan')
            page.locator('[data-path="management.configure"]').check()
            page.locator('.sidebar [data-go="hardware"]').click()
            page.get_by_role('button', name='Declare stable alias').click()
            assert page.locator('[data-record="hardware.serialAliases"][data-key="serial"]').input_value() == 'EXAMPLE'
            page.locator('.sidebar nav [data-go="integrations"]').click()
            page.locator('[data-path="cockpit.enable"]').check()
            # Fresh installations do not offer Arcane provisioning.
            assert page.get_by_text('Applications through Incus', exact=True).count() == 1
            assert page.locator('[data-path="apps.enable"]').count() == 0
            assert page.locator('[data-secret="arcanePassword"]').count() == 0
            # Exercise legacy-only controls independently: an already-enabled
            # Arcane host retains its migration/reset UI and existing data.
            status['settings']['apps']['enable'] = True
            status['effectiveSettings']['apps']['enable'] = True
            page.evaluate('(value) => { state.status=value; shell(); }', status)
            page.locator('[data-path="apps.enable"]').check()
            page.locator('[data-path="apps.arcanePort"]').fill('9443')
            assert page.locator('[data-path="cockpit.enable"]').is_checked()
            assert page.locator('[data-path="apps.arcanePort"]').input_value() == '9443'
            page.locator('[data-secret="cockpitPassword"]').fill('test-linux-account-password')
            page.locator('[data-secret="arcanePassword"]').fill('Test-arcane-password-1!')
            # Active links must not advertise an unapplied draft.
            assert page.locator('a[href="https://192.0.2.10:9443"]').count() == 0
            page.get_by_text('Optional provisioning tools', exact=True).click()
            page.get_by_text('Compatibility & pinned revisions', exact=True).click()
            assert page.get_by_text('Upstream-supported; unverified here', exact=True).count() == 3
            page.locator('.sidebar nav [data-go="operations"]').click()
            page.get_by_role('heading', name='Scheduled off-host backup').wait_for()
            assert page.locator('[data-guest-action]').count() == 0
            page.locator('.sidebar nav [data-go="review"]').click()
            # An old confirmed job must not become a preview of a new selection.
            status['job'] = {'id': 'c' * 32, 'status': 'confirmed', 'platformRevision': 'c' * 40,
                             'platformChanges': [{'path': 'historical-only.nix', 'before': None, 'after': '{}'}],
                             'settings': copy.deepcopy(settings), 'changes': [], 'moduleChanges': [], 'secretChanges': [], 'log': ''}
            page.evaluate('poll()')
            assert page.get_by_text('Historical platform files (1)', exact=True).count() == 1
            baseline_count = page.evaluate('stagedCount()')
            page.get_by_text('Advanced: published release or commit', exact=True).click()
            page.locator('#platform-revision').fill('a' * 40)
            page.locator('#platform-revision').blur()
            assert page.evaluate('stagedCount()') == baseline_count
            assert 'rebuild only' in page.locator('#platform-selected').inner_text()
            page.locator('[data-load-main]').click()
            page.wait_for_function("state.platformRevision === 'b'.repeat(40)")
            page.locator('#platform-password').fill('disposable-ui-fixture-password')
            page.locator('#platform-ack').check()
            # A status change forces poll() to rerender the review page.
            status['job']['log'] = 'fixture progress changed'
            page.evaluate('poll()')
            assert page.locator('#platform-password').input_value() == 'disposable-ui-fixture-password', 'Polling must preserve entered reauthentication'
            assert page.locator('#platform-ack').is_checked(), 'Polling must preserve explicit approval'
            page.evaluate('shell()')
            assert page.locator('#platform-ack').is_checked()
            assert 'disposable-ui-fixture-password' not in page.content(), 'Password must not be serialized into markup'
            assert page.evaluate('JSON.stringify(state)').find('disposable-ui-fixture-password') == -1
            # Approval of one revision must not carry to different privileged code.
            page.get_by_text('Advanced: published release or commit', exact=True).click()
            page.locator('#platform-revision').fill('d' * 40)
            page.locator('#platform-revision').blur()
            assert page.locator('#platform-password').input_value() == ''
            assert not page.locator('#platform-ack').is_checked()
            page.locator('[data-load-main]').click()
            page.wait_for_function("state.platformRevision === 'b'.repeat(40)")
            page.locator('#platform-password').fill('disposable-ui-fixture-password')
            page.locator('#platform-ack').check()
            page.get_by_role('button', name='Build a new review', exact=True).click()
            page.wait_for_function("state.status.job?.status === 'building'")
            assert page.locator('#platform-password').input_value() == ''
            assert not page.locator('#platform-ack').is_checked()
            request = next(body for action, body in submitted if action == 'review')
            assert request['revision'] == 'b' * 40
            assert request['adminPassword'] == 'disposable-ui-fixture-password'
            assert request['acknowledgePrivilegedModules'] is True
            assert request['settings']['cockpit']['enable']
            assert request['settings']['maintenance']['laptopMode'] == settings['maintenance']['laptopMode'], 'Enabling services must not silently change power policy'
            assert request['secrets']['cockpitPassword'] == 'test-linux-account-password'
            assert request['secrets']['arcanePassword'] == 'Test-arcane-password-1!'
            assert request['settings']['connectivity']['bridges'][0]['interfaces'] == ['eth0']
            assert request['settings']['hardware']['serialAliases'][0]['serial'] == 'EXAMPLE'
            status['job'].update(status='ready', impactPlan={
                'format': 1, 'blocked': True, 'runtimeState': 'observed', 'preconditionCount': 3,
                'changes': [{'scope': 'profile', 'identity': 'profiles:lab/base', 'kind': 'migration-required',
                             'message': 'Review shared storage', 'fields': ['devices.root.pool'],
                             'affected': ['lab/web', 'lab/dns'], 'blocked': True, 'resolution': 'Migrate consumers first'}],
                'ownership': [{'identity': 'instance:lab/web', 'owner': 'Incus', 'fields': [], 'consumers': []}],
                'boundaries': ['OS rollback does not restore guest data']})
            page.evaluate('(value) => { state.status=value; shell(); }', status)
            page.get_by_role('heading', name='Change impact', exact=True).wait_for()
            assert page.get_by_text('Affects: lab/web, lab/dns', exact=True).is_visible()
            assert page.get_by_role('button', name='Activate reviewed generation', exact=True).is_disabled()
            status['job']['impactPlan']['blocked'] = False
            page.evaluate('(value) => { state.status=value; shell(); }', status)
            page.get_by_role('button', name='Activate reviewed generation', exact=True).click()
            page.get_by_text('Runtime changed since review. This plan was refreshed; review it again before activation.', exact=True).wait_for()
            assert page.get_by_role('button', name='Activate reviewed generation', exact=True).is_disabled()
            page.screenshot(path=str(args.output / 'impact-review.png'), full_page=True)
            status['job'].update(status='confirmed', guestPlan={
                'boundary': 'OS rollback does not restore database schemas', 'targets': [
                    {'id': 'apps', 'name': 'apps', 'project': 'lab', 'targetSystem': '/nix/store/target',
                     'currentSystem': '/nix/store/previous', 'dependsOn': [], 'restartPolicy': 'defer',
                     'maxAttempts': 3, 'healthUnits': ['docker-arcane.service'], 'httpChecks': 0}]})
            page.evaluate('(value) => { state.status=value; shell(); }', status)
            page.get_by_role('button', name='Deploy reviewed guests', exact=True).click()
            page.wait_for_function("state.status.job?.guestDeployment?.status === 'queued'")
            assert page.get_by_role('button', name='Resume / recheck guests', exact=True).is_disabled()
            request = next(body for endpoint, body in submitted if endpoint == 'deploy-guests')
            assert request == {'id': 'a' * 32, 'resume': False, 'abandon': False}
            page.screenshot(path=str(args.output / 'guest-deployment.png'), full_page=True)
            page.evaluate("state.principal.role='viewer';state.page='hardware';shell()")
            assert page.locator('[data-serial-pick]').is_disabled()
            assert page.locator('[data-record-add="hardware.serialAliases"]').is_disabled()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(args.output / 'hardware-mobile.png'), full_page=True)
            page.evaluate("state.principal.role='admin';state.page='integrations';shell()")
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.screenshot(path=str(args.output / 'integrations.png'), full_page=True)
            # Existing service reset is independent from initial reuse and wipes
            # both password fields after the protected request completes.
            page.evaluate("state.host.arcane={state:'initialized'};state.page='integrations';shell()")
            page.locator('#arcane-reset-form [name=current]').fill('browser-current-portal-password')
            page.locator('#arcane-reset-form [name=password]').fill('Browser-arcane-replacement1!')
            page.locator('#arcane-reset-form button').click()
            page.wait_for_function('document.querySelector("#arcane-reset-form [name=password]").value === ""')
            reset = next(body for endpoint, body in submitted if endpoint == 'arcane/reset')
            assert reset == {'current':'browser-current-portal-password','password':'Browser-arcane-replacement1!'}
            assert 'Browser-arcane-replacement1!' not in page.evaluate('JSON.stringify(state)')
            # Explicit initial reuse survives navigation but the current password
            # is entered only on Changes and never becomes persistent UI state.
            page.evaluate("state.host.arcane={state:'fresh'};state.page='integrations';shell()")
            assert not page.locator('#arcane-reuse').is_checked()
            page.locator('#arcane-reuse').check()
            page.evaluate("state.modules={};state.platformRevision='';state.page='review';shell()")
            page.locator('#arcane-current').fill('Browser-current-reuse-password1!')
            page.locator('[data-action=build]').click()
            page.wait_for_function('state.arcaneReuse === false')
            reuse = [body for endpoint, body in submitted if endpoint == 'review'][-1]
            assert reuse['reuseArcanePassword'] is True and reuse['arcaneCurrent'] == 'Browser-current-reuse-password1!'
            assert 'arcanePassword' not in reuse['secrets']
            assert 'Browser-current-reuse-password1!' not in page.evaluate('JSON.stringify(state)')
            assert not errors, errors
            assert not [url for url in requests if not url.startswith(('http://127.0.0.1:', 'blob:http://127.0.0.1:'))], 'Private console assets must stay local'
            assert all(action != 'operation' for action, _ in submitted)
            browser.close()
    finally: server.shutdown(); server.server_close()
    print('Browser QA passed: host forms, review payload, role restrictions, mobile layout and no guest/script execution controls.')


if __name__ == '__main__': main()
