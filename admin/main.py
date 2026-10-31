"""Nixlab local configuration broker and authenticated HTTPS interface."""
import collections
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import ipaddress
import json
import os
import re
import subprocess
from pathlib import Path
import secrets
import socket
import socketserver
import ssl
import sys
import threading
import time
from urllib.parse import urlsplit
import runtime as rt
import accounts as access_accounts

SOCKET = Path(os.environ.get('NIXLAB_SOCKET', '/run/nixlab-admin/backend.sock'))
MAX_BODY = 262144


def json_body(handler):
    if handler.headers.get('Transfer-Encoding'):
        raise ValueError('Chunked requests are not accepted')
    try:
        length = int(handler.headers.get('Content-Length', '0'))
    except ValueError:
        raise ValueError('Invalid request length')
    if not 0 < length <= MAX_BODY:
        raise ValueError('Request body is missing or too large')
    body = json.loads(handler.rfile.read(length))
    if not isinstance(body, dict): raise ValueError('Request body must be an object')
    return body


def reply(handler, status, data, content_type='application/json', extra=None):
    body = json.dumps(data).encode() if content_type == 'application/json' else data
    handler.send_response(status)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Referrer-Policy', 'no-referrer')
    handler.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
    for key, value in (extra or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


class UnixHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    address_family = socket.AF_UNIX
    daemon_threads = True
    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = 'localhost'; self.server_port = 0


def backend():
    from controller import Manager
    from operations import Operations, guests
    manager = Manager()
    operations = Operations()
    class Broker(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            try:
                if self.path == '/status':
                    result = manager.status()
                elif self.path == '/inventory':
                    result = manager.host_inventory()
                elif self.path == '/releases':
                    import upgrades
                    result = {'releases': upgrades.releases()}
                elif self.path == '/operations':
                    import backup
                    settings = manager.effective_settings()
                    unit = subprocess.run(['systemctl', 'show', 'restic-backups-nixlab.service',
                                           '--property=ActiveState,SubState,Result'], capture_output=True, text=True, timeout=10)
                    result = {'jobs': operations.status(), 'archives': operations.archives(),
                              'generations': backup.generations(Path(settings['backup']['stagingDirectory'])),
                              'backupService': dict(line.split('=', 1) for line in unit.stdout.splitlines() if '=' in line),
                              'backupStatus': rt.read_json(Path('/var/lib/nixlab/backup-status.json'), {})}
                elif self.path == '/guests':
                    result = {'guests': guests() if manager.settings()['ready'] else []}
                elif self.path == '/users':
                    import accounts
                    result = {'users': accounts.list_users()}
                elif self.path == '/health':
                    import recovery
                    result = recovery.health()
                elif self.path == '/recipes':
                    import catalog
                    result = {'recipes': catalog.recipes()}
                elif self.path == '/access':
                    settings = manager.effective_settings()
                    hosts = [settings['management']['address'], settings['hostName'], 'localhost', '127.0.0.1']
                    from model import command
                    try:
                        links = json.loads(command(['ip', '-j', '-4', 'address', 'show', 'dev', settings['management']['interface']], timeout=5))
                        hosts += [a['local'] for link in links for a in link.get('addr_info', []) if a.get('family') == 'inet']
                    except (ValueError, OSError):
                        pass
                    result = {'networks': settings['management']['trustedCIDRs'], 'hosts': hosts}
                elif self.path == '/ca':
                    result = {'certificate': (rt.STATE / 'ca.crt').read_text()}
                else:
                    return reply(self, 404, {'error': 'Unknown endpoint'})
                reply(self, 200, result)
            except Exception as exc:
                reply(self, 400, {'error': str(exc)})
        def do_POST(self):
            try:
                body = json_body(self)
                actor = body.pop('_actor', 'root')
                if not isinstance(actor, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', actor):
                    raise ValueError('Invalid audit identity')
                rt.audit('broker-request', principal=actor, operation=self.path)
                if self.path == '/review':
                    result = manager.review(body['settings'], body.get('secrets', {}), body.get('updateInputs', False), body.get('modules', {}), body.get('revision'), principal=actor)
                elif self.path == '/apply':
                    result = manager.apply(body['id'])
                elif self.path == '/cancel':
                    result = manager.cancel(body['id'])
                elif self.path == '/confirm':
                    result = manager.confirm(body['id'])
                elif self.path == '/rollback':
                    result = manager.rollback(body['id'])
                elif self.path == '/password':
                    import accounts
                    result = accounts.change_password(body.get('username', 'admin'), body['password'])
                elif self.path == '/users':
                    import accounts
                    result = accounts.update(body.get('username'), body.get('role'), body.get('password'), body.get('delete', False))
                elif self.path == '/migrate-layout':
                    result = manager.migrate_layout()
                elif self.path == '/operation':
                    result = operations.submit(body, principal=actor)
                elif self.path == '/cancel-backup':
                    subprocess.run(['systemctl', 'stop', '--no-block', 'restic-backups-nixlab.service'], check=True, timeout=10)
                    result = {'ok': True, 'message': 'Cancellation requested; restart recovery remains active'}
                elif self.path == '/recovery-export':
                    import recovery
                    result = recovery.download(body.get('recipient'))
                elif self.path == '/browser-certificate':
                    result = manager.browser_certificate()
                else:
                    return reply(self, 404, {'error': 'Unknown action'})
                reply(self, 200, result)
            except (ValueError, KeyError, OSError) as exc:
                reply(self, 400, {'error': str(exc)})
            except Exception:
                reply(self, 500, {'error': 'Operation failed; inspect the root broker journal'})
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    if SOCKET.exists():
        SOCKET.unlink()
    server = UnixHTTPServer(str(SOCKET), Broker)
    os.chmod(SOCKET, 0o660)
    # Backend runs with the nixlab-ui group; socket is never globally writable.
    server.serve_forever()


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(150)
        self.sock.connect(str(SOCKET))


def broker_request(method, path, value=None):
    connection = UnixConnection('localhost', timeout=150)
    try:
        body = json.dumps(value).encode() if value is not None else None
        connection.request(method, path, body=body, headers={'Content-Type': 'application/json'})
        result = connection.getresponse()
        payload = json.loads(result.read())
        if result.status != 200:
            raise ValueError(payload.get('error', 'Broker request failed'))
        return payload
    finally:
        connection.close()


class Auth:
    def __init__(self):
        self.sessions = {}
        self.failures = collections.defaultdict(list)
        self.lock = threading.RLock()
    def login(self, password, address, username='admin'):
        import accounts
        with self.lock:
            now = time.monotonic()
            attempts = [t for t in self.failures[address] if now - t < 300]
            self.failures[address] = attempts
            if len(attempts) >= 5:
                raise ValueError('Too many sign-in attempts; wait five minutes')
            if not isinstance(username, str) or not accounts.matches(username, password):
                attempts.append(now)
                raise ValueError('Invalid administrator password')
            self.failures.pop(address, None)
            if len(self.sessions) > 128:
                self.sessions.clear()
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            item = accounts.record(username)
            self.sessions[token] = {'csrf': csrf, 'created': now, 'used': now, 'username': username,
                                    'role': item['role'], 'identity': accounts.fingerprint(item)}
            return token, csrf
    def get(self, cookie):
        import accounts
        token = ''
        for part in cookie.split(';'):
            if part.strip().startswith('nixlab_session='):
                token = part.strip().split('=', 1)[1]
        with self.lock:
            item = self.sessions.get(token)
            now = time.monotonic()
            if not item or now - item['used'] > 1800 or now - item['created'] > 28800:
                self.sessions.pop(token, None)
                return None
            current = accounts.record(item['username'])
            if not current or accounts.fingerprint(current) != item['identity']:
                self.sessions.pop(token, None)
                return None
            item['used'] = now
            return item


class BoundedHttpsServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, handler)
    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise
    def process_request_thread(self, request, address):
        try:
            request.settimeout(10)
            wrapped = self.context.wrap_socket(request, server_side=True)
            super().process_request_thread(wrapped, address)
        except (ssl.SSLError, OSError):
            request.close()
        finally:
            self.slots.release()


def web():
    auth = Auth()
    class Frontend(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def setup(self):
            super().setup()
            self.connection.settimeout(20)
        def log_message(self, *_):
            pass
        def access(self):
            settings = broker_request('GET', '/access')
            address = ipaddress.ip_address(self.client_address[0])
            if not address.is_loopback and not any(address in ipaddress.ip_network(n) for n in settings['networks']):
                raise PermissionError('Management source network is not trusted')
            host = self.headers.get('Host', '')
            try:
                parsed = urlsplit('https://' + host)
                valid = parsed.hostname in settings['hosts'] and not parsed.username and not parsed.password and parsed.path == ''
            except ValueError:
                valid = False
            if not valid:
                raise PermissionError('Invalid management hostname')
            return host
        def session(self, mutation=False):
            host = self.access()
            if mutation and self.headers.get('Origin') != 'https://' + host:
                raise PermissionError('Same-origin request required')
            session = auth.get(self.headers.get('Cookie', ''))
            if not session:
                raise PermissionError('Sign in to continue')
            if mutation and not secrets.compare_digest(self.headers.get('X-Nixlab-CSRF', ''), session['csrf']):
                raise PermissionError('Invalid request token')
            return session
        def do_GET(self):
            try:
                self.access()
                path = urlsplit(self.path).path
                assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                          '/api.js': ('api.js', 'text/javascript; charset=utf-8'),
                          '/events.js': ('events.js', 'text/javascript; charset=utf-8'),
                          '/operations.js': ('operations.js', 'text/javascript; charset=utf-8'),
                          '/foundation.js': ('foundation.js', 'text/javascript; charset=utf-8'),
                          '/style.css': ('style.css', 'text/css; charset=utf-8')}
                if path in assets:
                    filename, mime = assets[path]
                    return reply(self, 200, (rt.SOURCE / 'static' / filename).read_bytes(), mime)
                if path == '/ca.crt':
                    return reply(self, 200, broker_request('GET', '/ca')['certificate'].encode(), 'application/x-x509-ca-cert', {'Content-Disposition': 'attachment; filename=nixlab-ca.crt'})
                session = self.session()
                if path.startswith('/docs/'):
                    import html
                    import re
                    name = path[len('/docs/'):]
                    if not re.fullmatch(r'[a-z0-9-]+\.md', name):
                        return reply(self, 404, {'error': 'Choose a packaged Markdown guide'})
                    directory = rt.SOURCE / 'docs'
                    if not directory.is_dir(): directory = rt.SOURCE.parent / 'docs'
                    source = directory / name
                    if not source.is_file(): return reply(self, 404, {'error': 'Guide not found'})
                    text = source.read_text()
                    try:
                        import markdown
                        content = markdown.markdown(text, extensions=['fenced_code', 'tables'])
                    except ImportError:
                        content = '<pre>' + html.escape(text) + '</pre>'
                    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                            '<title>Nixlab guide</title><link rel="stylesheet" href="/style.css">'
                            '<main class="page"><article class="card"><a href="/">Back to Nixlab</a>' + content + '</article></main></html>')
                    return reply(self, 200, page.encode(), 'text/html; charset=utf-8')
                if path == '/api/session':
                    return reply(self, 200, {key: session[key] for key in ('csrf', 'username', 'role')})
                if path == '/api/users':
                    if session['role'] != 'admin': raise access_accounts.AuthorizationError('Administrator account required')
                    return reply(self, 200, broker_request('GET', '/users'))
                if path == '/api/releases':
                    if session['role'] != 'admin': raise access_accounts.AuthorizationError('Administrator account required')
                    return reply(self, 200, broker_request('GET', '/releases'))
                if path in ('/api/status', '/api/inventory', '/api/ca', '/api/operations', '/api/guests', '/api/health', '/api/recipes'):
                    result = broker_request('GET', path[4:])
                    if path == '/api/status' and session['role'] != 'admin' and result.get('job'):
                        result['job'] = {k: v for k, v in result['job'].items() if k not in ('log', 'moduleChanges', 'platformChanges')}
                    return reply(self, 200, result)
                reply(self, 404, {'error': 'Unknown endpoint'})
            except access_accounts.AuthorizationError as exc:
                reply(self, 403, {'error': str(exc)})
            except PermissionError as exc:
                reply(self, 401, {'error': str(exc)})
            except (ValueError, OSError) as exc:
                reply(self, 400, {'error': str(exc)})
        def do_POST(self):
            try:
                host = self.access()
                if self.headers.get('Origin') != 'https://' + host:
                    raise PermissionError('Same-origin request required')
                if self.path == '/api/login':
                    body = json_body(self)
                    username = body.get('username', 'admin')
                    token, csrf = auth.login(body.get('password'), self.client_address[0], username)
                    return reply(self, 200, {'csrf': csrf, 'username': username, 'role': auth.get('nixlab_session=' + token)['role']}, extra={
                        'Set-Cookie': 'nixlab_session=' + token + '; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=28800'})
                session = self.session(mutation=True)
                body = json_body(self)
                import accounts
                accounts.authorize(session, self.path, body)
                if self.path == '/api/logout':
                    session['created'] = -1000000000
                    return reply(self, 200, {'ok': True}, extra={'Set-Cookie': 'nixlab_session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0'})
                if self.path == '/api/password':
                    if not accounts.matches(session['username'], body.get('current')):
                        raise ValueError('Current password is incorrect')
                    result = broker_request('POST', '/password', {'username': session['username'], 'password': body['password'], '_actor': session['username']})
                    with auth.lock:
                        auth.sessions = {key: item for key, item in auth.sessions.items() if item['username'] != session['username']}
                    return reply(self, 200, result)
                if self.path == '/api/migrate-layout' and not accounts.matches(session['username'], body.get('current')):
                    raise ValueError('Reauthenticate to migrate the machine layout')
                if self.path == '/api/review' and (body.get('modules') or body.get('revision')):
                    if body.get('acknowledgePrivilegedModules') is not True or not accounts.matches(session['username'], body.get('adminPassword')):
                        raise ValueError('Reauthenticate and acknowledge privileged Nix modules')
                    body.pop('adminPassword', None)
                if self.path in ('/api/recovery-export', '/api/users'):
                    if not accounts.matches(session['username'], body.pop('current', None)):
                        raise ValueError('Reauthenticate before exporting recovery material or changing accounts')
                actions = {'/api/review', '/api/apply', '/api/confirm', '/api/rollback', '/api/cancel', '/api/browser-certificate', '/api/migrate-layout', '/api/operation', '/api/users', '/api/recovery-export', '/api/cancel-backup'}
                if self.path not in actions:
                    return reply(self, 404, {'error': 'Unknown action'})
                body['_actor'] = session['username']
                reply(self, 200, broker_request('POST', self.path[4:], body))
            except access_accounts.AuthorizationError as exc:
                reply(self, 403, {'error': str(exc)})
            except PermissionError as exc:
                reply(self, 401, {'error': str(exc)})
            except (ValueError, KeyError, OSError) as exc:
                reply(self, 400, {'error': str(exc)})
    server = BoundedHttpsServer(('0.0.0.0', int(os.environ.get('NIXLAB_PORT', '9440'))), Frontend)
    server.daemon_threads = True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    credentials = Path(os.environ.get('CREDENTIALS_DIRECTORY', str(rt.STATE)))
    context.load_cert_chain(credentials / 'tls.crt', credentials / 'tls.key')
    server.context = context
    server.serve_forever()


def config_cli(argv):
    """Local root CLI using the same transaction API as the browser."""
    import argparse
    parser = argparse.ArgumentParser(prog='nixlab-admin config')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    commands.add_parser('migrate-layout')
    review = commands.add_parser('review')
    review.add_argument('--module', action='append', default=[], metavar='NAME=FILE')
    review.add_argument('--settings', type=Path)
    review.add_argument('--update-inputs', action='store_true')
    review.add_argument('--revision', help='Full reviewed upstream platform commit SHA')
    for action in ('apply', 'confirm', 'cancel', 'rollback'):
        commands.add_parser(action).add_argument('id')
    args = parser.parse_args(argv)
    if args.command == 'migrate-layout':
        result = rt.migrate_idle_layout(rt.ROOT, rt.STATE)
    elif args.command == 'status':
        result = broker_request('GET', '/status')
    elif args.command == 'review':
        modules = {}
        for item in args.module:
            name, separator, filename = item.partition('=')
            if not separator or name in modules:
                parser.error('Use distinct NAME=FILE module arguments')
            modules[name] = Path(filename).read_text()
        rt.validate_modules(modules)
        settings = json.loads(args.settings.read_text()) if args.settings else {}
        request = {'settings': settings, 'modules': modules, 'updateInputs': args.update_inputs}
        if args.revision: request['revision'] = args.revision
        result = broker_request('POST', '/review', request)
    else:
        result = broker_request('POST', '/' + args.command, {'id': args.id})
    print(json.dumps(result, indent=2))


def renew():
    settings = rt.read_json(Path('/etc/nixlab/effective-settings.json'), rt.read_json(rt.machine_path(rt.ROOT, 'settings')))
    import subprocess
    checks = [subprocess.run(['openssl', 'x509', '-checkend', '2592000', '-noout', '-in', str(rt.STATE / name)], capture_output=True).returncode
              for name in ('tls.crt', 'apps-tls.crt')]
    if any(checks):
        if checks[1]:
            (rt.STATE / 'apps-tls.key').unlink(missing_ok=True)
        rt.issue_tls(settings['management']['address'], settings['hostName'])
        services = ['nixlab-ui.service']
        if settings['ready']:
            services += ['incus.service']
            if settings.get('cockpit', {}).get('enable'):
                services += ['cockpit.service']
            if settings['apps']['enable']:
                services += ['nginx.service', 'nixlab-apps-onboarding.service']
        subprocess.run(['systemctl', 'try-restart', *services], check=True)


def guests_cli(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='nixlab-admin guests')
    parser.add_argument('action', choices=['list', 'status', 'register-export', 'start', 'stop', 'restart', 'snapshot', 'snapshot-copy', 'export', 'restore', 'backup', 'backup-check', 'release-restore'])
    parser.add_argument('--file', type=Path)
    parser.add_argument('--name'); parser.add_argument('--project', default='lab')
    parser.add_argument('--snapshot'); parser.add_argument('--target'); parser.add_argument('--archive')
    parser.add_argument('--acknowledge', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'register-export':
        from operations import Operations
        if args.file is None: parser.error('Use --file with the recovered local Incus export')
        result = Operations().register_archive(args.file, args.project, args.name)
    elif args.action in ('list', 'status'):
        result = broker_request('GET', '/guests' if args.action == 'list' else '/operations')
    else:
        request = {k: v for k, v in vars(args).items() if v is not None and k != 'file'}
        result = broker_request('POST', '/operation', request)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    os.umask(0o027)
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    if action == 'backend':
        backend()
    elif action == 'web':
        web()
    elif action == 'guard':
        from worker import guard
        guard()
    elif action == 'job':
        from worker import job
        job(sys.argv[2], sys.argv[3])
    elif action == 'operation-job' and os.geteuid() == 0:
        from operations import Operations
        Operations().run(sys.argv[2])
    elif action == 'recovery' and os.geteuid() == 0:
        import recovery
        recovery.cli(sys.argv[2:])
    elif action == 'guests' and os.geteuid() == 0:
        guests_cli(sys.argv[2:])
    elif action == 'disk' and os.geteuid() == 0 and sys.argv[2:] == ['prepare']:
        from disks import prepare
        try:
            prepare()
        except (ValueError, OSError, StopIteration, EOFError, subprocess.CalledProcessError) as exc:
            sys.exit(str(exc))
    elif action == 'recovery-backup' and os.geteuid() == 0:
        import recovery
        recovery.backup(Path(sys.argv[2]))
    elif action == 'config' and os.geteuid() == 0:
        config_cli(sys.argv[2:])
    elif action == 'prune-jobs' and os.geteuid() == 0:
        import argparse
        from controller import Manager
        parser = argparse.ArgumentParser(prog='nixlab-admin prune-jobs')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--keep', type=int, default=20)
        args = parser.parse_args(sys.argv[2:])
        print(json.dumps(Manager().prune_jobs(args.apply, args.keep), indent=2))
    elif action == 'renew':
        renew()
    elif action == 'reset-password' and os.geteuid() == 0:
        import getpass
        value = getpass.getpass('New Nixlab administrator password: ')
        rt.write_json(rt.STATE / 'auth.json', rt.password_record(value), mode=0o640, group='nixlab-ui')
        print('Password changed. Restart nixlab-ui to revoke existing sessions.')
    else:
        sys.exit('Usage: nixlab-admin {backend|web|guard|renew|reset-password|config|guests|recovery|prune-jobs|disk prepare}')
