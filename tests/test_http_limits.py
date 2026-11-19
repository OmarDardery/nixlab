"""Reject ambiguous framing before reading bodies or invoking privileged actions."""
from email.message import Message
from io import BytesIO
from pathlib import Path
import sys
import unittest
import socket
import ssl
import subprocess
import shutil
import tempfile
import threading
import time
import warnings
from contextlib import contextmanager
from unittest.mock import Mock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
from main import (HeaderReader, MAX_BODY, MAX_HEADERS, MAX_ACTIVE_CLIENTS, ReadDeadline,
                  BoundedHttpsServer, SecuredHttpHandler, checked_length, json_body, reply, tls_context)
import http.client


class FramingTests(unittest.TestCase):
    def handler(self, headers, raw=b'{}'):
        message = Message()
        for name, value in headers:
            message[name] = value
        return Mock(headers=message, rfile=BytesIO(raw), close_connection=False)

    def test_ambiguous_or_unsupported_framing_never_reads_body(self):
        cases = [[], [('Content-Length', '2'), ('Content-Length', '2')],
                 [('Content-Length', '2'), ('Content-Length', '5')],
                 [('Content-Length', '2'), ('Transfer-Encoding', 'chunked')],
                 [('Transfer-Encoding', '')], [('Content-Length', '-1')],
                 [('Content-Length', '+2')], [('Content-Length', '0')],
                 [('Content-Length', str(MAX_BODY + 1))], [('Content-Length', 'not-a-number')]]
        for headers in cases:
            with self.subTest(headers=headers):
                handler = self.handler(headers)
                with self.assertRaises(ValueError):
                    json_body(handler)
                self.assertEqual(handler.rfile.tell(), 0)
                self.assertTrue(handler.close_connection)

    def test_valid_object_consumes_exactly_declared_bytes(self):
        handler = self.handler([('Content-Length', ' 2 ')], b'{}NEXT')
        self.assertEqual(json_body(handler), {})
        self.assertEqual(handler.rfile.read(), b'NEXT')
        self.assertFalse(handler.close_connection)

    def test_incomplete_invalid_and_nonobject_bodies_close_connection(self):
        for raw, length in [(b'{', '2'), (b'\xff', '1'), (b'{bad', '4'), (b'[]', '2')]:
            with self.subTest(raw=raw):
                handler = self.handler([('Content-Length', length)], raw)
                with self.assertRaises(ValueError):
                    json_body(handler)
                self.assertTrue(handler.close_connection)

    def test_io_errors_cannot_disclose_transport_details(self):
        handler = self.handler([('Content-Length', '2')])
        handler.rfile = Mock()
        handler.rfile.read.side_effect = TimeoutError('SENTINEL-private-transport-detail')
        with self.assertRaises(ValueError) as caught:
            json_body(handler)
        self.assertNotIn('SENTINEL', str(caught.exception))
        self.assertTrue(handler.close_connection)

    def test_header_budget_is_aggregate_and_reads_only_budget_plus_one(self):
        stream = BytesIO(b'x' * (MAX_HEADERS + 1000))
        reader = HeaderReader(stream)
        with self.assertRaises(http.client.LineTooLong):
            reader.readline()
        self.assertEqual(stream.tell(), MAX_HEADERS + 1)
        stream = BytesIO(b'A: b\r\nC: d\r\n\r\n')
        reader = HeaderReader(stream)
        self.assertEqual(reader.readline(65537), b'A: b\r\n')
        self.assertEqual(reader.readline(65537), b'C: d\r\n')
        self.assertEqual(reader.remaining, MAX_HEADERS - 12)



class EchoHandler(SecuredHttpHandler):
    """No privileged API: exercise the production parser over real TCP sockets."""
    def do_GET(self):
        if self.path == '/slow':
            time.sleep(0.22)
        reply(self, 200, {'ok': True})

    def do_POST(self):
        reply(self, 200, json_body(self))


class NoTlsContext:
    def wrap_socket(self, sock, server_side=False):
        return sock


@contextmanager
def serving(context=None):
    server = BoundedHttpsServer(('127.0.0.1', 0), EchoHandler)
    server.context = context or NoTlsContext()
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def raw_request(server, packet):
    with socket.create_connection(server.server_address, timeout=2) as client:
        client.settimeout(2)
        client.sendall(packet)
        client.shutdown(socket.SHUT_WR)
        chunks = []
        try:
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        except (ConnectionResetError, BrokenPipeError):
            pass
        return b''.join(chunks)


class LiveHttpBoundaryTests(unittest.TestCase):
    def test_get_rejects_all_request_bodies_and_smuggling(self):
        with serving() as server:
            for headers in (b'Content-Length: 0\r\n', b'Content-Length: 5\r\n',
                            b'Transfer-Encoding: chunked\r\n',
                            b'Content-Length: 2\r\nContent-Length: 2\r\n'):
                with self.subTest(headers=headers):
                    packet = b'GET / HTTP/1.1\r\nHost: localhost\r\n' + headers + (
                        b'Connection: close\r\n\r\nABCDEGET / HTTP/1.1\r\n\r\n'
                    )
                    data = raw_request(server, packet)
                    self.assertIn(b'400', data.split(b'\r\n', 1)[0])
                    self.assertNotIn(b'200 OK', data)
                    self.assertNotIn(b'ABCDE', data)

    def test_post_rejects_ambiguous_framing_before_handler(self):
        with serving() as server:
            cases = (b'Content-Length: 2\r\nContent-Length: 2\r\n',
                     b'Content-Length: 2\r\nTransfer-Encoding: chunked\r\n',
                     b'Content-Length: 999999\r\n', b'')
            for headers in cases:
                with self.subTest(headers=headers):
                    response = raw_request(server, b'POST / HTTP/1.1\r\nHost: localhost\r\n'
                                           + headers + b'Connection: close\r\n\r\n{}')
                    self.assertIn(b'400', response.split(b'\r\n', 1)[0])
            good = raw_request(server, b'POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n'
                               b'Connection: close\r\n\r\n{}')
            self.assertIn(b'200 OK', good)
            self.assertTrue(good.endswith(b'{}'))

    def test_request_line_header_overflow_and_expect_cannot_leak_input(self):
        with serving() as server:
            secret = b'SENTINEL-private-header-must-not-be-in-error'
            cases = (
                (b'GET /' + b'x' * 5000 + b' HTTP/1.1\r\nHost: localhost\r\n\r\n', b'414'),
                (b'GET / HTTP/1.1\r\nHost: localhost\r\nX-Test: ' + secret +
                 b'x' * MAX_HEADERS + b'\r\n\r\n', b'431'),
                (b'POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n'
                 b'Expect: 100-continue\r\n\r\n{}', b'417'),
            )
            for packet, status in cases:
                with self.subTest(status=status):
                    response = raw_request(server, packet)
                    self.assertIn(status, response.split(b'\r\n', 1)[0])
                    self.assertNotIn(b'200 OK', response)
                    self.assertNotIn(secret, response)

    def test_absolute_header_budget_defeats_trickling_client(self):
        with patch('main.MAX_HEADER_SECONDS', .15), serving() as server:
            with socket.create_connection(server.server_address, timeout=2) as client:
                client.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\nX-Hang: ')
                for _ in range(4):
                    try:
                        client.sendall(b'x')
                    except OSError:
                        break
                    time.sleep(.06)
                client.settimeout(1)
                try:
                    response = client.recv(1024)
                except (ConnectionResetError, BrokenPipeError):
                    response = b''
                self.assertNotIn(b'200 OK', response)

    def test_absolute_body_budget_defeats_trickling_client(self):
        with patch('main.MAX_BODY_SECONDS', .15), serving() as server:
            with socket.create_connection(server.server_address, timeout=2) as client:
                client.sendall(b'POST / HTTP/1.1\r\nHost: localhost\r\n'
                               b'Content-Length: 80\r\n\r\n{')
                for _ in range(4):
                    try:
                        client.sendall(b' ')
                    except OSError:
                        break
                    time.sleep(.06)
                client.settimeout(1)
                try:
                    response = client.recv(1024)
                except (ConnectionResetError, BrokenPipeError):
                    response = b''
                self.assertNotIn(b'200 OK', response)

    def test_header_timer_disarms_before_slow_backend_operation(self):
        with patch('main.MAX_HEADER_SECONDS', .05), serving() as server:
            response = raw_request(server, b'GET /slow HTTP/1.1\r\nHost: localhost\r\n'
                                   b'Connection: close\r\n\r\n')
            self.assertIn(b'200 OK', response)

    def test_overloaded_listener_rejects_connections_without_new_threads(self):
        with serving() as server:
            for _ in range(MAX_ACTIVE_CLIENTS):
                self.assertTrue(server.slots.acquire(blocking=False))
            try:
                for _ in range(128):
                    client, peer = socket.socketpair()
                    try:
                        server.process_request(client, ('127.0.0.1', 12345))
                        self.assertEqual(peer.recv(1), b'')
                    finally:
                        client.close()
                        peer.close()
                self.assertFalse(server.slots.acquire(blocking=False))
            finally:
                for _ in range(MAX_ACTIVE_CLIENTS):
                    server.slots.release()

    @unittest.skipUnless(shutil.which('openssl'), 'Requires OpenSSL to make ephemeral test-only TLS identity')
    def test_tls_rejects_legacy_protocols_and_accepts_modern_client(self):
        with tempfile.TemporaryDirectory() as folder:
            cert, key = Path(folder) / 'cert.pem', Path(folder) / 'key.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048',
                            '-nodes', '-keyout', str(key), '-out', str(cert),
                            '-days', '1', '-subj', '/CN=localhost'], capture_output=True, check=True)
            tls = tls_context(cert, key)
            self.assertGreaterEqual(tls.minimum_version, ssl.TLSVersion.TLSv1_2)
            self.assertTrue(tls.options & ssl.OP_NO_COMPRESSION)
            with serving(tls) as server:
                client_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                client_tls.check_hostname = False
                client_tls.verify_mode = ssl.CERT_NONE
                client_tls.minimum_version = ssl.TLSVersion.TLSv1_2
                with socket.create_connection(server.server_address, timeout=3) as raw:
                    with client_tls.wrap_socket(raw, server_hostname='localhost') as client:
                        self.assertIn(client.version(), ('TLSv1.2', 'TLSv1.3'))
                        client.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                        self.assertIn(b'200 OK', client.recv(4096))
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', DeprecationWarning)
                    old = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    old.check_hostname = False
                    old.verify_mode = ssl.CERT_NONE
                    old.maximum_version = ssl.TLSVersion.TLSv1_1
                    with self.assertRaises((ssl.SSLError, OSError)):
                        with socket.create_connection(server.server_address, timeout=3) as raw:
                            old.wrap_socket(raw, server_hostname='localhost')


if __name__ == '__main__':
    unittest.main()
