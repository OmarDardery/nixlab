"""Nixlab local configuration broker and authenticated HTTPS interface."""
import collections
import base64
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
MAX_HEADERS = 16384
MAX_REQUEST_LINE = 4096
MAX_HEADER_SECONDS = 15
MAX_BODY_SECONDS = 30
MAX_ACTIVE_CLIENTS = 32


def checked_length(method, headers):
    """Reject ambiguous HTTP framing on ALL routes, including read-only ones."""
    if headers.get_all('Transfer-Encoding'):
        raise rt.PublicError('Transfer encoding is not accepted')
    lengths = headers.get_all('Content-Length', [])
    if method != 'POST':
        if lengths:
            raise rt.PublicError('Request body is not accepted for this method')
        return None
    if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0].strip()):
        raise rt.PublicError('Exactly one valid request length is required')
    length = int(lengths[0].strip())
    if not 0 < length <= MAX_BODY:
        raise rt.PublicError('Request body is missing or too large')
    return length


class ReadDeadline:
    """A wall-clock read budget, not an idle timeout that trickle traffic resets."""
    def __init__(self, connection, seconds):
        self.connection = connection
        self.seconds = seconds
        self.lock = threading.Lock()
        self.active = False
        self.timer = None

    def __enter__(self):
        with self.lock:
            self.active = True
        self.timer = threading.Timer(self.seconds, self.expire)
        self.timer.daemon = True
        self.timer.start()
        return self

    def expire(self):
        with self.lock:
            if self.active:
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.active = False

    def close(self):
        with self.lock:
            self.active = False
        if self.timer is not None:
            self.timer.cancel()

    def __exit__(self, *_):
        self.close()


def body_read_deadline(handler):
    # Broker requests arrive over a private Unix socket; its caller already
    # enforces a timeout. Only the externally reachable HTTPS reader needs this.
    if getattr(type(handler), 'enforce_body_deadline', False):
        return ReadDeadline(handler.connection, MAX_BODY_SECONDS)
    from contextlib import nullcontext
    return nullcontext()



class HeaderReader:
    """Bound aggregate header bytes during the stdlib parser's line reads."""
    def __init__(self, stream):
        self.stream = stream
        self.remaining = MAX_HEADERS

    def readline(self, size=-1):
        limit = self.remaining + 1
        line = self.stream.readline(min(size, limit) if size >= 0 else limit)
        self.remaining -= len(line)
        if self.remaining < 0:
            raise http.client.LineTooLong('request header block')
        return line


def json_body(handler):
    try:
        length = checked_length('POST', handler.headers)
        with body_read_deadline(handler):
            raw = handler.rfile.read(length)
        if len(raw) != length:
            raise rt.PublicError('Incomplete request body')
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise rt.PublicError('Invalid JSON request body') from None
        if not isinstance(body, dict):
            raise rt.PublicError('Request body must be an object')
        return body
    except OSError:
        handler.close_connection = True
        raise rt.PublicError('Request body could not be read') from None
    except ValueError:
        # A rejected body must never become a second, smuggled request.
        handler.close_connection = True
        raise



def reply(handler, status, data, content_type='application/json', extra=None):
    body = json.dumps(data).encode() if content_type == 'application/json' else data
    handler.send_response(status)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Referrer-Policy', 'no-referrer')
    handler.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
    for key, value in (extra.items() if isinstance(extra, dict) else (extra or [])):
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


class UnixHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    address_family = socket.AF_UNIX
    daemon_threads = True
    def handle_error(self, request, client_address):
        # socketserver's default traceback can contain secret-bearing exceptions.
        pass
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
                if self.path == '/enrollment':
                    import accounts
                    result = accounts.enrollment_status()
                elif self.path == '/status':
                    result = manager.status()
                elif self.path == '/inventory':
                    result = manager.host_inventory()
                elif self.path == '/releases':
                    import upgrades
                    result = {'releases': upgrades.releases()}
                elif self.path == '/main-revision':
                    import upgrades
                    result = {'revision': upgrades.main_revision()}
                elif self.path == '/guest-network':
                    import guest_network
                    result = guest_network.inventory() if manager.effective_settings()['ready'] else {'available': False, 'guests': [], 'conflicts': []}
                elif self.path == '/operations':
                    import backup
                    import backup_verification
                    settings = manager.effective_settings()
                    unit = subprocess.run(['systemctl', 'show', 'restic-backups-nixlab.service',
                                           '--property=ActiveState,SubState,Result'], capture_output=True, text=True, timeout=10)
                    result = {'jobs': operations.status(), 'archives': operations.archives(),
                              'generations': backup.generations(Path(settings['backup']['stagingDirectory'])),
                              'backupService': dict(line.split('=', 1) for line in unit.stdout.splitlines() if '=' in line),
                              'backupStatus': rt.read_json(Path('/var/lib/nixlab/backup-status.json'), {}),
                              'backupVerification': backup_verification.status(settings['backup']['enable'], repository=settings['backup']['repository'])}
                elif self.path == '/guests':
                    result = {'guests': guests() if manager.settings()['ready'] else []}
                elif self.path == '/project/status':
                    from project_transactions import ProjectTransactions
                    result = ProjectTransactions().status()
                elif self.path == '/project-sources':
                    import native_projects
                    result = native_projects.overview()
                elif self.path == '/project-imports':
                    import native_imports
                    result = native_imports.PublishedSources().inventory()
                elif self.path.startswith('/deployment/'):
                    from deployments import Deployments
                    result = Deployments().status(self.path.removeprefix('/deployment/'))
                elif self.path == '/users':
                    import accounts
                    result = {'users': accounts.list_users()}
                elif self.path == '/browser-certificates':
                    result = manager.browser_certificates()
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
            except ValueError as exc:
                reply(self, 400, {'error': rt.public_error(exc)})
            except Exception:
                reply(self, 500, {'error': 'Could not retrieve management status; inspect the host services'})
        def do_POST(self):
            try:
                body = json_body(self)
                actor = body.pop('_actor', 'root')
                if not isinstance(actor, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', actor):
                    raise rt.PublicError('Invalid audit identity')
                rt.audit('broker-request', principal=actor, operation=self.path)
                if self.path in ('/bootstrap-start', '/bootstrap-verify', '/bootstrap-claim'):
                    import browser_bootstrap as bootstrap
                    shared = (body.get('token'), body.get('csrf'), body.get('address'), body.get('origin'))
                    if self.path == '/bootstrap-start':
                        result = bootstrap.start(body.get('token'), body.get('address'), body.get('origin'))
                    elif self.path == '/bootstrap-verify':
                        result = bootstrap.verify(*shared, body.get('hostUsername'), body.get('hostPassword'))
                    else:
                        result = bootstrap.claim(*shared, body.get('username'), body.get('password'))
                elif self.path == '/enrollment':
                    # Local root compatibility only. The HTTPS gateway never
                    # forwards an unbound browser claim to this legacy route.
                    import accounts
                    result = accounts.claim_enrollment(body.get('code'), body.get('password'))
                elif self.path in ('/project-import/prepare', '/project-import/chunk', '/project-import/finish',
                                   '/project-import/review', '/project-import/publish'):
                    import native_imports
                    with rt.locked(blocking=False):
                        staging = native_imports.ImportStaging()
                        if self.path == '/project-import/prepare':
                            result = staging.prepare(actor, body.get('project'), body.get('files'))
                        elif self.path == '/project-import/chunk':
                            result = staging.chunk(body.get('id'), actor, body.get('path'),
                                                   body.get('offset'), body.get('data'))
                        elif self.path == '/project-import/finish':
                            result = staging.finish(body.get('id'), actor, body.get('manifestHash'))
                        elif self.path == '/project-import/review':
                            result = native_imports.PublishedSources().review(
                                staging, body.get('id'), actor, body.get('manifestHash'))
                        else:
                            result = native_imports.PublishedSources().publish(
                                staging, body.get('id'), actor, body.get('reviewId'),
                                acknowledge=body.get('acknowledge') is True)
                elif self.path == '/review':
                    if 'reuseArcanePassword' in body or 'arcaneCurrent' in body:
                        raise rt.PublicError('Legacy application-manager credentials are unsupported by Incus-native')
                    secret_values = body.get('secrets', {})
                    result = manager.review(body['settings'], secret_values, body.get('updateInputs', False), body.get('modules', {}), body.get('revision'), principal=actor)
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
                elif self.path in ('/project/review', '/project/publish', '/project/rollback',
                                   '/project/cancel', '/project/recover', '/project/deploy'):
                    from project_transactions import ProjectTransactions
                    projects = ProjectTransactions()
                    if self.path == '/project/review':
                        result = projects.review(body.get('changes'), principal=actor,
                                                 source_revisions=body.get('sourceRevisions'))
                    elif self.path == '/project/publish':
                        result = projects.publish(body.get('id'), body.get('planHash'))
                    elif self.path == '/project/rollback':
                        result = projects.rollback(body.get('id'), body.get('planHash'))
                    elif self.path == '/project/cancel':
                        result = projects.cancel(body.get('id'))
                    elif self.path == '/project/deploy':
                        result = projects.deploy(body.get('id'), body.get('planHash'), resume=body.get('resume', False))
                    else:
                        result = projects.recover(body.get('id'))
                elif self.path == '/deploy-guests':
                    from deployments import Deployments
                    deploy = Deployments()
                    result = deploy.abandon(body['id']) if body.get('abandon') is True else deploy.submit(body['id'], resume=body.get('resume', False), principal=actor)
                elif self.path == '/cancel-backup':
                    subprocess.run(['systemctl', 'stop', '--no-block', 'restic-backups-nixlab.service'], check=True, timeout=10)
                    result = {'ok': True, 'message': 'Cancellation requested; restart recovery remains active'}
                elif self.path == '/recovery-export':
                    import recovery
                    result = recovery.download(body.get('recipient'))
                elif self.path == '/browser-certificate':
                    result = manager.browser_certificate(body.get('device'), body.get('passphrase'), actor,
                                                         body.get('fullAdmin', False), body.get('renew'))
                elif self.path == '/revoke-browser-certificate':
                    result = manager.revoke_browser_certificate(body.get('fingerprint'), actor)
                else:
                    return reply(self, 404, {'error': 'Unknown action'})
                reply(self, 200, result)
            except ValueError as exc:
                reply(self, 400, {'error': rt.public_error(exc)})
            except KeyError:
                reply(self, 400, {'error': 'A required request field or configuration value is missing'})
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
        self.sock.settimeout(self.timeout)
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
                raise rt.PublicError('Too many sign-in attempts; wait five minutes')
            if not isinstance(username, str) or not accounts.matches(username, password):
                attempts.append(now)
                raise rt.PublicError('Invalid administrator password')
            self.failures.pop(address, None)
            if len(self.sessions) > 128:
                self.sessions.clear()
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            item = accounts.record(username)
            self.sessions[token] = {'csrf': csrf, 'created': now, 'used': now, 'username': username,
                                    'role': item['role'], 'identity': accounts.fingerprint(item),
                                    'authenticatedAt': time.time()}
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
            for holder in self.sessions.values():
                pending = holder.get('certificateDownload')
                if pending and now >= pending['deadline']:
                    holder.pop('certificateDownload', None)
            if not item or now - item['used'] > 1800 or now - item['created'] > 28800:
                self.sessions.pop(token, None)
                return None
            current = accounts.record(item['username'])
            if not current or accounts.fingerprint(current) != item['identity']:
                self.sessions.pop(token, None)
                return None
            item['used'] = now
            return item

    def begin_certificate(self, session):
        with self.lock:
            pending = session.get('certificateDownload')
            if pending and time.monotonic() >= pending['deadline']:
                session.pop('certificateDownload', None)
            if session.get('certificateIssuing') or session.get('certificateDownload'):
                raise rt.PublicError('Claim the pending certificate download before issuing another identity')
            session['certificateIssuing'] = True

    def stage_certificate(self, session, result):
        with self.lock:
            if not any(item is session for item in self.sessions.values()):
                raise rt.PublicError('Session ended; inspect the identity list before reissuing')
            data = base64.b64decode(result['pkcs12'], validate=True)
            if len(data) > 65536 or not re.fullmatch(r'nixlab-browser-[A-Za-z0-9_-]{1,64}', result['name']):
                raise rt.PublicError('Invalid private certificate download; inspect the identity list')
            session['certificateDownload'] = {'data': data, 'name': result['name'],
                                              'fingerprint': result['fingerprint'],
                                              'deadline': time.monotonic() + 120}
            fields = ('name', 'device', 'fingerprint', 'owner', 'issuedAt', 'expiresAt',
                      'restricted', 'projects', 'status', 'validDays')
            return {key: result[key] for key in fields} | {'downloadSeconds': 120}

    def take_certificate(self, session):
        with self.lock:
            pending = session.pop('certificateDownload', None)
            if not pending or time.monotonic() >= pending['deadline']:
                raise rt.PublicError('Certificate download expired or was already claimed; inspect the identity list to revoke or reissue')
            return pending

    def discard_certificate(self, identity):
        with self.lock:
            for session in self.sessions.values():
                if session.get('certificateDownload', {}).get('fingerprint') == identity:
                    session.pop('certificateDownload', None)


class SecuredHttpHandler(BaseHTTPRequestHandler):
    """Shared bounded HTTP parsing for the web portal (no privileged routes)."""
    protocol_version = 'HTTP/1.1'
    enforce_body_deadline = True

    def handle_one_request(self):
        # BaseHTTPRequestHandler reads the request line before parse_request.
        # Arm the budget before that read and cancel it as soon as framing is
        # validated, so long-running root broker actions are not interrupted.
        self._header_deadline = ReadDeadline(self.connection, MAX_HEADER_SECONDS)
        self._header_deadline.__enter__()
        try:
            return super().handle_one_request()
        finally:
            self._header_deadline.close()

    def parse_request(self):
        try:
            original = self.rfile
            self.rfile = HeaderReader(original)
            try:
                parsed = super().parse_request()
            finally:
                self.rfile = original
            if not parsed:
                return False
            # Initialize command/version via stdlib before sending a 414:
            # send_error needs both fields to write an actual HTTP response.
            if len(self.raw_requestline) > MAX_REQUEST_LINE:
                self.close_connection = True
                self.send_error(414)
                return False
            # HTTP/1.1 keep-alive must never preserve stray request-body bytes.
            if not self.path.startswith('/') or self.path.startswith('//'):
                self.close_connection = True
                self.send_error(400)
                return False
            try:
                checked_length(self.command, self.headers)
            except ValueError:
                self.close_connection = True
                self.send_error(400)
                return False
            return True
        finally:
            self._header_deadline.close()

    def handle_expect_100(self):
        # Do not acknowledge a potentially large upload before validating its
        # framing or authenticating the request; browser JSON doesn't need it.
        self.close_connection = True
        self.send_error(417)
        return False

    def send_error(self, code, message=None, explain=None):
        # Stdlib error rendering must never echo untrusted methods or headers.
        super().send_error(code)

    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def log_message(self, *_):
        pass


class BoundedHttpsServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # Do not let the stdlib print request exceptions into the journal.
        pass

    daemon_threads = True
    request_queue_size = MAX_ACTIVE_CLIENTS
    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(MAX_ACTIVE_CLIENTS)
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



def require_fresh_web_confirmation(session, job, address):
    """A post-activation remote login demonstrates the NEW HTTPS path works.

    Host-health probes from loopback and cookies issued before activation are
    not evidence of remotely reachable management access. The root CLI has a
    separate, operator-controlled recovery path when LAN access is unavailable.
    """
    if not isinstance(job, dict) or job.get('status') != 'awaiting-confirmation':
        raise rt.PublicError('No active transaction is awaiting access confirmation')
    try:
        remote = not ipaddress.ip_address(address).is_loopback
    except ValueError:
        remote = False
    if not remote:
        raise PermissionError('Loopback access cannot confirm remote management; use a fresh LAN login or local console')
    ready = job.get('awaitingAt', job.get('appliedAt'))
    issued = session.get('authenticatedAt')
    if not isinstance(ready, (int, float)) or not isinstance(issued, (int, float)) or issued <= ready:
        raise PermissionError('Sign out, reconnect to the new management address, and sign in again before confirming')
    return True


def bootstrap_cookie(header):
    values = [part.strip().split('=', 1)[1] for part in header.split(';')
              if part.strip().startswith('__Host-nixlab_bootstrap=')]
    return values[0] if len(values) == 1 else None


def tls_context(certfile, keyfile):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.options |= ssl.OP_NO_COMPRESSION
    context.load_cert_chain(certfile, keyfile)
    return context


def web():
    auth = Auth()
    class Frontend(SecuredHttpHandler):
        def access(self):
            settings = broker_request('GET', '/access')
            address = ipaddress.ip_address(self.client_address[0])
            if not address.is_loopback and not any(address in ipaddress.ip_network(n) for n in settings['networks']):
                raise PermissionError('Management source network is not trusted')
            hosts = self.headers.get_all('Host', [])
            if len(hosts) != 1:
                raise PermissionError('Exactly one management hostname is required')
            host = hosts[0]
            try:
                parsed = urlsplit('https://' + host)
                valid = (parsed.hostname in settings['hosts'] and not parsed.username
                         and not parsed.password and parsed.path == '' and not parsed.query
                         and not parsed.fragment and parsed.port in (None, self.server.server_port))
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
                          '/projects.js': ('projects.js', 'text/javascript; charset=utf-8'),
                          '/incus-projects.js': ('incus-projects.js', 'text/javascript; charset=utf-8'),
                          '/api.js': ('api.js', 'text/javascript; charset=utf-8'),
                          '/events.js': ('events.js', 'text/javascript; charset=utf-8'),
                          '/operations.js': ('operations.js', 'text/javascript; charset=utf-8'),
                          '/commands.js': ('commands.js', 'text/javascript; charset=utf-8'),
                          '/foundation.js': ('foundation.js', 'text/javascript; charset=utf-8'),
                          '/style.css': ('style.css', 'text/css; charset=utf-8'),
                          '/brand.css': ('brand.css', 'text/css; charset=utf-8'),
                          '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
                          '/fonts/dm-sans-latin.woff2': ('fonts/dm-sans-latin.woff2', 'font/woff2'),
                          '/fonts/space-grotesk-latin.woff2': ('fonts/space-grotesk-latin.woff2', 'font/woff2')}
                if path in assets:
                    filename, mime = assets[path]
                    return reply(self, 200, (rt.SOURCE / 'static' / filename).read_bytes(), mime)
                if path == '/ca.crt':
                    return reply(self, 200, broker_request('GET', '/ca')['certificate'].encode(), 'application/x-x509-ca-cert', {'Content-Disposition': 'attachment; filename=nixlab-ca.crt'})
                if path == '/api/enrollment':
                    return reply(self, 200, broker_request('GET', '/enrollment'))
                if path == '/api/bootstrap':
                    host = self.access()
                    result = broker_request('POST', '/bootstrap-start', {
                        'token': bootstrap_cookie(self.headers.get('Cookie', '')),
                        'address': self.client_address[0], 'origin': 'https://' + host})
                    token = result.pop('token')
                    return reply(self, 200, result, extra={'Set-Cookie': '__Host-nixlab_bootstrap=' + token + '; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=600'})
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
                            '<meta name="theme-color" content="#111310"><title>Nixlab · Offline guide</title>'
                            '<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/brand.css"><link rel="stylesheet" href="/style.css">'
                            '<body><main class="page offline-guide"><header class="guide-bar"><a class="brand" href="/"><span class="brand-mark" aria-hidden="true">[n]</span><span>nixlab<span class="brand-cursor" aria-hidden="true">_</span></span></a><a href="/">Back to host console →</a></header><article class="card">' + content + '</article></main></body></html>')
                    return reply(self, 200, page.encode(), 'text/html; charset=utf-8')
                if path == '/api/session':
                    return reply(self, 200, {key: session[key] for key in ('csrf', 'username', 'role')})
                if path == '/api/users':
                    if session['role'] != 'admin': raise access_accounts.AuthorizationError('Administrator account required')
                    return reply(self, 200, broker_request('GET', '/users'))
                if path == '/api/browser-certificates':
                    if session['role'] != 'admin': raise access_accounts.AuthorizationError('Administrator account required')
                    return reply(self, 200, broker_request('GET', '/browser-certificates'))
                if path in ('/api/releases', '/api/main-revision', '/api/project-imports', '/api/project/status'):
                    if session['role'] != 'admin': raise access_accounts.AuthorizationError('Administrator account required')
                    return reply(self, 200, broker_request('GET', path[4:]))
                if path.startswith('/api/deployment/'):
                    return reply(self, 200, broker_request('GET', path[4:]))
                if path in ('/api/status', '/api/inventory', '/api/ca', '/api/operations', '/api/guests', '/api/guest-network', '/api/project-sources', '/api/health', '/api/recipes'):
                    result = broker_request('GET', path[4:])
                    if path == '/api/status' and session['role'] != 'admin' and result.get('job'):
                        result['job'] = {k: v for k, v in result['job'].items() if k not in ('log', 'moduleChanges', 'platformChanges')}
                    return reply(self, 200, result)
                reply(self, 404, {'error': 'Unknown endpoint'})
            except access_accounts.AuthorizationError as exc:
                reply(self, 403, {'error': rt.public_error(exc)})
            except PermissionError as exc:
                reply(self, 401, {'error': rt.public_error(exc)})
            except (ValueError, OSError) as exc:
                reply(self, 400, {'error': rt.public_error(exc)})
        def do_POST(self):
            try:
                host = self.access()
                if self.headers.get('Origin') != 'https://' + host:
                    raise PermissionError('Same-origin request required')
                if self.path in ('/api/enrollment', '/api/bootstrap/verify', '/api/bootstrap/claim'):
                    body = json_body(self)
                    token = bootstrap_cookie(self.headers.get('Cookie', ''))
                    csrf = self.headers.get('X-Nixlab-CSRF', '')
                    if not token or not csrf:
                        raise PermissionError('Bootstrap browser session and request token required')
                    payload = {'token': token, 'csrf': csrf, 'address': self.client_address[0], 'origin': 'https://' + host}
                    if self.path == '/api/bootstrap/verify':
                        payload.update(hostUsername=body.get('hostUsername'), hostPassword=body.get('hostPassword'))
                        return reply(self, 200, broker_request('POST', '/bootstrap-verify', payload))
                    payload.update(username=body.get('username'), password=body.get('password'))
                    result = broker_request('POST', '/bootstrap-claim', payload)
                    username = body.get('username')
                    # A successfully committed host-approved account must not be
                    # stranded by pre-enrollment anonymous login failures. Normal
                    # verifier matching still creates the ordinary session.
                    with auth.lock:
                        auth.failures.pop(self.client_address[0], None)
                    ordinary, session_csrf = auth.login(body.get('password'), self.client_address[0], username)
                    principal = auth.get('nixlab_session=' + ordinary)
                    return reply(self, 200, {'csrf': session_csrf, 'username': username, 'role': principal['role']}, extra=[
                        ('Set-Cookie', 'nixlab_session=' + ordinary + '; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=28800'),
                        ('Set-Cookie', '__Host-nixlab_bootstrap=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0')])
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
                if self.path in ('/api/project-import/prepare', '/api/project-import/publish'):
                    # Import and publication use separate, fresh password checks.
                    # Review alone never writes persistent project source.
                    if not accounts.matches(session['username'], body.pop('current', None)):
                        raise rt.PublicError('Reauthenticate before changing project source')
                if self.path == '/api/browser-certificate/download':
                    pending = auth.take_certificate(session)
                    # Consume before writing the response. A disconnect cannot
                    # replay the key; its owned trust entry stays revocable.
                    return reply(self, 200, pending['data'], 'application/x-pkcs12', {
                        'Content-Disposition': 'attachment; filename="' + pending['name'] + '.p12"'})
                if self.path in ('/api/browser-certificate', '/api/revoke-browser-certificate'):
                    if not accounts.matches(session['username'], body.pop('current', None)):
                        raise rt.PublicError('Reauthenticate before issuing or revoking browser identities')
                    if self.path == '/api/revoke-browser-certificate':
                        result = broker_request('POST', '/revoke-browser-certificate', {
                            'fingerprint': body.get('fingerprint'), '_actor': session['username']})
                        auth.discard_certificate(body.get('fingerprint'))
                        return reply(self, 200, result)
                    if body.get('fullAdmin', False) is not False and body.pop('acknowledgeFullAdmin', False) is not True:
                        raise rt.PublicError('Explicitly acknowledge that unrestricted Incus access grants host administration')
                    auth.begin_certificate(session)
                    try:
                        body['_actor'] = session['username']
                        result = broker_request('POST', '/browser-certificate', body)
                        if not auth.get(self.headers.get('Cookie', '')):
                            raise rt.PublicError('Session ended; inspect the identity list before reissuing')
                        metadata = auth.stage_certificate(session, result)
                        return reply(self, 200, metadata)
                    finally:
                        with auth.lock:
                            session.pop('certificateIssuing', None)
                if self.path == '/api/logout':
                    session.pop('certificateDownload', None)
                    session['created'] = -1000000000
                    return reply(self, 200, {'ok': True}, extra={'Set-Cookie': 'nixlab_session=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0'})
                if self.path == '/api/password':
                    if not accounts.matches(session['username'], body.get('current')):
                        raise rt.PublicError('Current password is incorrect')
                    result = broker_request('POST', '/password', {'username': session['username'], 'password': body['password'], '_actor': session['username']})
                    with auth.lock:
                        auth.sessions = {key: item for key, item in auth.sessions.items() if item['username'] != session['username']}
                    return reply(self, 200, result)
                if self.path == '/api/migrate-layout' and not accounts.matches(session['username'], body.get('current')):
                    raise rt.PublicError('Reauthenticate to migrate the machine layout')
                if self.path == '/api/review':
                    password = body.pop('adminPassword', None)
                    if body.get('modules') or body.get('revision'):
                        if body.get('acknowledgePrivilegedModules') is not True:
                            raise rt.PublicError('Explicitly approve the privileged platform revision or Nix modules before building')
                        if not accounts.matches(session['username'], password):
                            raise rt.PublicError('Reauthenticate with your current portal password before building privileged code')
                    del password
                if self.path == '/api/confirm':
                    state = broker_request('GET', '/status')
                    active = state.get('job') or {}
                    if active.get('id') != body.get('id'):
                        raise rt.PublicError('Confirm the current reviewed transaction only')
                    require_fresh_web_confirmation(session, active, self.client_address[0])
                if self.path.startswith('/api/project/'):
                    if body.get('acknowledgePrivilegedNix') is not True:
                        raise rt.PublicError('Acknowledge that project Nix files are privileged code')
                    if not accounts.matches(session['username'], body.pop('current', None)):
                        raise rt.PublicError('Reauthenticate before changing declarative project source')
                    body.pop('acknowledgePrivilegedNix', None)
                if self.path in ('/api/recovery-export', '/api/users'):
                    if not accounts.matches(session['username'], body.pop('current', None)):
                        raise rt.PublicError('Reauthenticate before exporting recovery material or changing accounts')
                actions = {'/api/project-import/prepare', '/api/project-import/chunk', '/api/project-import/finish', '/api/project-import/review', '/api/project-import/publish', '/api/review', '/api/apply', '/api/confirm', '/api/rollback', '/api/cancel', '/api/migrate-layout', '/api/operation', '/api/users', '/api/recovery-export', '/api/cancel-backup', '/api/deploy-guests', '/api/project/review', '/api/project/publish', '/api/project/rollback', '/api/project/cancel', '/api/project/recover', '/api/project/deploy'}
                if self.path not in actions:
                    return reply(self, 404, {'error': 'Unknown action'})
                body['_actor'] = session['username']
                reply(self, 200, broker_request('POST', self.path[4:], body))
            except access_accounts.AuthorizationError as exc:
                self.close_connection = True
                reply(self, 403, {'error': rt.public_error(exc)})
            except PermissionError as exc:
                self.close_connection = True
                reply(self, 401, {'error': rt.public_error(exc)})
            except (ValueError, KeyError, OSError) as exc:
                self.close_connection = True
                reply(self, 400, {'error': rt.public_error(exc)})
    server = BoundedHttpsServer(('0.0.0.0', int(os.environ.get('NIXLAB_PORT', '9440'))), Frontend)
    server.daemon_threads = True
    credentials = Path(os.environ.get('CREDENTIALS_DIRECTORY', str(rt.STATE)))
    context = tls_context(credentials / 'tls.crt', credentials / 'tls.key')
    server.context = context
    server.serve_forever()



def ssh_login_epoch(proc_root=Path('/proc'), pid=None):
    """Creation time of the nearest SSH/Cockpit terminal session ancestor."""
    pid = os.getpid() if pid is None else pid
    try:
        uptime = (proc_root / 'stat').read_text().splitlines()
        boot_line = next(line for line in uptime if line.startswith('btime '))
        boot = int(boot_line.split()[1])
        ticks = os.sysconf('SC_CLK_TCK')
        for _ in range(64):
            process = proc_root / str(pid)
            name = (process / 'comm').read_text().strip()
            if name.startswith(('sshd', 'cockpit-')):
                raw = (process / 'stat').read_text()
                start_ticks = int(raw.rsplit(')', 1)[1].split()[19])
                return boot + start_ticks / ticks
            status = (process / 'status').read_text().splitlines()
            parent = int(next(line.split()[1] for line in status if line.startswith('PPid:')))
            if parent <= 1 or parent == pid:
                break
            pid = parent
    except (OSError, ValueError, StopIteration, IndexError, ZeroDivisionError):
        return None
    return None


def require_fresh_cli_confirmation(job, *, tty=None, ssh_started_at=None):
    """Root confirmation must come from a new remote login or physical VT."""
    if not isinstance(job, dict) or job.get('status') != 'awaiting-confirmation':
        raise rt.PublicError('No transaction is awaiting access confirmation')
    ready = job.get('awaitingAt', job.get('appliedAt'))
    if not isinstance(ready, (int, float)):
        raise rt.PublicError('Cannot establish activation time; inspect recovery at the host console')
    if tty is None:
        if not sys.stdin.isatty():
            raise rt.PublicError('Use an interactive NEW SSH login or the physical host console to confirm access')
        try:
            tty = os.ttyname(sys.stdin.fileno())
        except OSError:
            raise rt.PublicError('Cannot verify operator terminal; use physical host console') from None
    if re.fullmatch(r'/dev/(?:tty[0-9]+|ttyS[0-9]+|hvc[0-9]+)', tty):
        return True  # Physical operator can observe local access after activation.
    if not tty.startswith('/dev/pts/'):
        raise rt.PublicError('Unrecognized terminal; use a new SSH login or the physical host console')
    if ssh_started_at is None:
        ssh_started_at = ssh_login_epoch()
    if ssh_started_at is None or ssh_started_at <= ready:
        raise rt.PublicError('This terminal predates activation; open a NEW SSH/Cockpit terminal and confirm there')
    return True


def config_cli(argv):
    """Local root CLI using the same transaction API as the browser."""
    import argparse
    parser = argparse.ArgumentParser(prog='nixlab-admin config')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    commands.add_parser('plan')
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
    elif args.command in ('status', 'plan'):
        result = broker_request('GET', '/status')
        if args.command == 'plan':
            result = (result.get('job') or {}).get('impactPlan')
            if result is None: raise rt.PublicError('Build a new review to generate an impact plan')
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
        if args.command == 'confirm':
            state = broker_request('GET', '/status')
            job = state.get('job') or {}
            if job.get('id') != args.id:
                raise rt.PublicError('Confirm the active transaction only')
            require_fresh_cli_confirmation(job)
        result = broker_request('POST', '/' + args.command, {'id': args.id})
    print(json.dumps(result, indent=2))


def project_cli(argv):
    """Root-only review and recovery; identical state to the browser broker."""
    import argparse
    from project_transactions import ProjectTransactions
    parser = argparse.ArgumentParser(prog='sudo nixlab-admin projects')
    parser.add_argument('command', choices=['status', 'review', 'publish', 'rollback', 'cancel', 'recover', 'deploy'])
    parser.add_argument('--changes', type=Path, help='UTF-8 JSON map of relative Nix file edits')
    parser.add_argument('--source-revisions', type=Path, help='Optional JSON map of published project to exact source revision SHA-256')
    parser.add_argument('--id')
    parser.add_argument('--plan-hash')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    projects = ProjectTransactions()
    try:
        if args.command == 'status': result = projects.status(args.id)
        elif args.command == 'review':
            if not args.changes or args.changes.is_symlink() or args.changes.stat().st_size > 131072:
                raise ValueError('Provide a bounded local JSON edit file')
            refs = None
            if args.source_revisions:
                if args.source_revisions.is_symlink() or args.source_revisions.stat().st_size > 4096:
                    raise ValueError('Provide a bounded local source revision map')
                refs = json.loads(args.source_revisions.read_text())
            result = projects.review(json.loads(args.changes.read_text()), principal='root-console',
                                     source_revisions=refs)
        elif args.command == 'publish': result = projects.publish(args.id, args.plan_hash)
        elif args.command == 'rollback': result = projects.rollback(args.id, args.plan_hash)
        elif args.command == 'cancel': result = projects.cancel(args.id)
        elif args.command == 'deploy': result = projects.deploy(args.id, args.plan_hash, resume=args.resume)
        else: result = projects.recover(args.id)
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError):
        raise SystemExit('Project transaction failed; inspect source integrity, approvals and recovery state') from None


def deploy_cli(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='nixlab-admin deploy')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status').add_argument('id', nargs='?')
    for action in ('start', 'resume', 'abandon'): commands.add_parser(action).add_argument('id')
    args = parser.parse_args(argv)
    if args.command == 'status':
        if args.id: result = broker_request('GET', '/deployment/' + args.id)
        else:
            job = broker_request('GET', '/status').get('job') or {}
            result = {'plan': job.get('guestPlan'), 'deployment': job.get('guestDeployment')}
    else:
        result = broker_request('POST', '/deploy-guests', {'id': args.id, 'resume': args.command == 'resume', 'abandon': args.command == 'abandon'})
    print(json.dumps(result, indent=2))


def renew():
    settings = rt.read_json(Path('/etc/nixlab/effective-settings.json'), rt.read_json(rt.machine_path(rt.ROOT, 'settings')))
    import subprocess
    checks = [subprocess.run(['openssl', 'x509', '-checkend', '2592000', '-noout', '-in', str(rt.STATE / name)], capture_output=True).returncode
              for name in ('tls.crt', 'apps-tls.crt')]
    if any(checks):
        if checks[1]:
            (rt.STATE / 'apps-tls.key').unlink(missing_ok=True)
        import diagnostics
        rt.issue_tls(diagnostics.management_address(settings), settings['hostName'])
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


def enroll_key(manager, public_key_file, settings):
    """Stage a public key without claiming that local probes prove client access."""
    import diagnostics
    keys = [line.strip() for line in Path(public_key_file).read_text().splitlines()
            if line.strip() and not line.lstrip().startswith('#')]
    if not keys: raise rt.PublicError('The public key file is empty')
    candidate = manager.settings()
    candidate['adminKeys'] = sorted(set(candidate['adminKeys'] + keys))
    # Use the same reviewed transaction and health checks as setup.
    job_id = manager.review(candidate, principal='root-key-enrollment')['id']
    print('Building key enrollment transaction: ' + job_id, flush=True)
    # Poll locally; workers survive this CLI being disconnected.
    while True:
        manager.status()  # Reconcile a stopped worker before reading this exact job.
        record = manager.job(job_id)
        if record['status'] != 'building': break
        time.sleep(1)
    if record['status'] != 'ready': raise ValueError(record.get('error', record['status']))
    manager.apply(job_id)
    while True:
        record = manager.job(job_id)
        if record['status'] != 'applying': break
        time.sleep(1)
    if record['status'] != 'awaiting-confirmation': raise ValueError(record.get('error', record['status']))
    diagnostics.wait_ready(record.get('effectiveSettings', candidate))
    print('Test the new key in a fresh session: ssh operator@' + diagnostics.management_address(settings), flush=True)
    rt.pending_access(job_id, record)
    return record


def approve_browser_console():
    import getpass
    import warnings
    if os.geteuid() != 0 or not sys.stdin.isatty():
        raise SystemExit('Run sudo nixlab-admin approve-enrollment from a private host terminal')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            code = getpass.getpass('Code displayed in your browser: ')
        access_accounts.approve_enrollment(code)
    except (getpass.GetPassWarning, EOFError, ValueError, OSError):
        raise SystemExit('Browser approval did not complete; check enrollment state from a private host terminal') from None
    print('Browser approved for up to ten minutes. Return to that browser to create its independent portal account.')


def reset_portal_password():
    """Supplied-password console recovery; never accept an echoing fallback."""
    import getpass
    import warnings
    if os.geteuid() != 0 or not sys.stdin.isatty():
        raise SystemExit('Run sudo nixlab-admin reset-password from a private host terminal')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            value = getpass.getpass('New NixLab administrator password (16–256 characters): ')
        access_accounts.recover_password(value)
    except (getpass.GetPassWarning, EOFError, ValueError, OSError):
        raise SystemExit('Password change did not complete; check host account state from a private terminal before retrying') from None
    print('Password changed for the recovery administrator. Its old sessions will be rejected; sign in as admin.')


if __name__ == '__main__':
    os.umask(0o027)
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    if action in ('doctor', 'identity', 'enroll-key') and os.geteuid() == 0:
        from controller import Manager
        import diagnostics
        try:
            manager = Manager()
            settings = manager.effective_settings()
            if action == 'doctor':
                if sys.argv[2:] == ['--wait']:
                    diagnostics.wait_ready(settings)
                result = diagnostics.doctor(settings)
                print(json.dumps(result, indent=2))
                sys.exit(0 if result['ok'] else 1)
            elif action == 'identity':
                # network-online may be reached before a DHCP lease is usable.
                deadline = time.monotonic() + 90
                while True:
                    try:
                        diagnostics.identity(settings)
                        break
                    except (ValueError, OSError, subprocess.SubprocessError):
                        if time.monotonic() >= deadline: raise
                        time.sleep(2)
            elif action == 'enroll-key':
                if len(sys.argv) != 3: raise rt.PublicError('Usage: sudo nixlab enroll-key laptop.pub')
                enroll_key(manager, sys.argv[2], settings)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            sys.exit('Command failed; check inputs and host status before retrying')
    elif action == 'backend':
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
    elif action == 'deployment-job' and os.geteuid() == 0:
        from deployments import Deployments
        Deployments().run(sys.argv[2])
    elif action == 'deploy' and os.geteuid() == 0:
        try:
            deploy_cli(sys.argv[2:])
        except (ValueError, OSError) as exc:
            sys.exit('Command failed; check inputs and host status before retrying')
    elif action == 'projects' and os.geteuid() == 0:
        project_cli(sys.argv[2:])
    elif action == 'project-deploy-job' and os.geteuid() == 0:
        if len(sys.argv) != 3:
            sys.exit('Expected one project deployment ID')
        from project_transactions import ProjectTransactions
        ProjectTransactions().run_deployment(sys.argv[2])
    elif action == 'recovery' and os.geteuid() == 0:
        import recovery
        recovery.cli(sys.argv[2:])
    elif action == 'legacy-apps' and os.geteuid() == 0:
        if sys.argv[2:] != ['inventory']:
            sys.exit('Usage: sudo nixlab-admin legacy-apps inventory')
        import legacy_apps
        try:
            print(json.dumps(legacy_apps.inventory(), indent=2))
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
            sys.exit('Legacy apps inventory unavailable or incomplete; inspect Incus and the guest locally')
    elif action == 'install-plan' and os.geteuid() == 0:
        installation = rt.read_json(rt.machine_path(rt.ROOT, 'installation'), {})
        print(json.dumps({'intent': installation.get('intent'),
                          'pendingTasks': installation.get('pendingTasks', []),
                          'note': 'Pending tasks are not proof of physical completion; use commissioning evidence.'},
                         indent=2))
    elif action == 'commissioning':
        import commissioning
        try:
            commissioning.cli(sys.argv[2:])
        except (ValueError, OSError, subprocess.SubprocessError):
            sys.exit('Commissioning report unavailable or invalid; see docs/commissioning.md for prerequisites and procedures')
    elif action == 'guests' and os.geteuid() == 0:
        guests_cli(sys.argv[2:])
    elif action == 'disk' and os.geteuid() == 0 and sys.argv[2:] == ['prepare']:
        from disks import prepare
        try:
            prepare()
        except (ValueError, OSError, StopIteration, EOFError, subprocess.CalledProcessError) as exc:
            sys.exit('Command failed; check inputs and host status before retrying')
    elif action == 'recovery-backup' and os.geteuid() == 0:
        import recovery
        recovery.backup(Path(sys.argv[2]))
    elif action == 'recovery-coverage' and os.geteuid() == 0:
        import recovery
        print(json.dumps(recovery.coverage()))
    elif action == 'config' and os.geteuid() == 0:
        try:
            config_cli(sys.argv[2:])
        except (ValueError, OSError) as exc:
            sys.exit('Command failed; check inputs and host status before retrying')
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
    elif action == 'approve-enrollment' and os.geteuid() == 0:
        if len(sys.argv) != 2:
            sys.exit('Run sudo nixlab-admin approve-enrollment from a private host terminal')
        approve_browser_console()
    elif action == 'reset-password' and os.geteuid() == 0:
        reset_portal_password()
    else:
        sys.exit('Usage: nixlab-admin {doctor|identity|enroll-key|legacy-apps inventory|backend|web|guard|renew|approve-enrollment|reset-password|config|projects|deploy|guests|recovery|commissioning|install-plan|prune-jobs|disk prepare}')

