from unittest.mock import MagicMock, patch

import requests

from odoo.tests import BaseCase, tagged

from odoo.addons.nexo_core.lib.client import NexoClient, NexoError, jwt_expiration, normalize_base_url


def fake_response(status, data=None, text=None):
    response = MagicMock()
    response.status_code = status
    if data is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = data
    return response


@tagged('post_install', '-at_install', 'nexo')
class TestNexoClient(BaseCase):

    def _client(self, **kw):
        return NexoClient('https://api.nexo.test', 'pk_test', token='tok', connector='odoo/20.0.1.0.0', **kw)

    def test_https_only(self):
        with self.assertRaises(NexoError) as ctx:
            normalize_base_url('http://api.nexo.test')
        self.assertEqual(ctx.exception.kind, 'bad_url')
        self.assertEqual(normalize_base_url('http://localhost:9000/'), 'http://localhost:9000')
        self.assertEqual(normalize_base_url(' https://api.nexo.test/ '), 'https://api.nexo.test')

    def test_headers_and_log_entry(self):
        calls = []
        client = self._client(on_call=calls.append)
        with patch.object(NexoClient, '_send', return_value=fake_response(200, {'ok': 1})) as send:
            self.assertEqual(client.request('GET', '/store/productos', params={'q': 'x'}), {'ok': 1})
        headers = send.call_args.kwargs['headers']
        self.assertEqual(headers['X-Nexo-Connector'], 'odoo/20.0.1.0.0')
        self.assertEqual(headers['x-publishable-api-key'], 'pk_test')
        self.assertEqual(headers['Authorization'], 'Bearer tok')
        self.assertTrue(headers['X-Request-Id'])
        self.assertTrue(send.call_args.kwargs['verify'])
        # El registro no lleva secretos.
        entry = calls[0]
        self.assertEqual(entry['request_id'], headers['X-Request-Id'])
        self.assertNotIn('tok', repr(entry))
        self.assertNotIn('pk_test', repr(entry))

    def test_no_auth_header_when_not_needed(self):
        with patch.object(NexoClient, '_send', return_value=fake_response(200, {})) as send:
            self._client().request('POST', '/store/comercios/auth', json_body={'email': 'a', 'password': 'b'}, auth=False)
        self.assertNotIn('Authorization', send.call_args.kwargs['headers'])

    def test_error_mapping(self):
        cases = [
            (fake_response(401, {'error': 'Credenciales incorrectas'}), 'auth'),
            (fake_response(403, {'error': 'Tu usuario está desactivado.'}), 'forbidden'),
            (fake_response(404, {'error': 'x'}), 'not_found'),
            (fake_response(200), 'invalid_response'),
            (fake_response(301), 'not_found'),
            (fake_response(400, {'error': 'Publishable API key required'}), 'bad_key'),
            (fake_response(502), 'server'),
        ]
        for response, kind in cases:
            with self.subTest(kind=kind), patch.object(NexoClient, '_send', return_value=response):
                with self.assertRaises(NexoError) as ctx:
                    self._client().request('GET', '/x')
                self.assertEqual(ctx.exception.kind, kind)
                self.assertTrue(ctx.exception.request_id)

    def test_forbidden_keeps_server_message(self):
        with patch.object(NexoClient, '_send', return_value=fake_response(403, {'error': 'Tu usuario está desactivado.'})):
            with self.assertRaises(NexoError) as ctx:
                self._client().request('GET', '/x')
        self.assertIn('desactivado', str(ctx.exception))

    def test_network_errors(self):
        for exc, kind in [(requests.exceptions.Timeout(), 'timeout'),
                          (requests.exceptions.ConnectionError(), 'network'),
                          (requests.exceptions.SSLError(), 'network')]:
            with self.subTest(kind=kind), patch.object(NexoClient, '_send', side_effect=exc):
                with self.assertRaises(NexoError) as ctx:
                    self._client().request('GET', '/x')
                self.assertEqual(ctx.exception.kind, kind)

    def test_jwt_expiration(self):
        # {"comercio_id":"c1","exp":2000000000}
        token = 'eyJhbGciOiJIUzI1NiJ9.eyJjb21lcmNpb19pZCI6ImMxIiwiZXhwIjoyMDAwMDAwMDAwfQ.firma'
        self.assertEqual(jwt_expiration(token), 2000000000)
        self.assertIsNone(jwt_expiration('basura'))
