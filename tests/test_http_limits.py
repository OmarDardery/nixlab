"""Reject ambiguous framing before reading bodies or invoking privileged actions."""
from email.message import Message
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE / 'admin'), str(BASE / 'scripts')]
from main import HeaderReader, MAX_BODY, MAX_HEADERS, json_body
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


if __name__ == '__main__':
    unittest.main()
