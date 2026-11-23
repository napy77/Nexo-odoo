"""Cliente HTTP para las APIs del ecosistema Nexo.

Python puro, sin dependencias de Odoo, para poder probarlo sin base ni red.
Nunca escribe en el log cabeceras, cuerpos ni tokens: solo método, ruta,
estado, duración e id de solicitud.
"""
import base64
import json
import logging
import time
import uuid
from urllib.parse import urlsplit

import requests

_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}


class NexoError(Exception):
    """Error de una llamada a Nexo, ya traducido para el usuario.

    `kind` permite a la interfaz distinguir qué pasó sin parsear el mensaje:
    bad_url, network, timeout, auth, forbidden, bad_key, not_found, server,
    invalid_response, client.
    """

    MESSAGES = {
        'bad_url': "La URL de NexoB2B no es válida. Tiene que empezar con https://.",
        'network': "No se pudo conectar con NexoB2B. Revisá la conexión a internet o la URL.",
        'timeout': "NexoB2B tardó demasiado en responder. Probá de nuevo en un rato.",
        'auth': "Credenciales inválidas o sesión vencida. Volvé a iniciar sesión.",
        'forbidden': "El usuario no tiene permiso para esta operación en NexoB2B.",
        'bad_key': "La clave publicable de NexoB2B no es válida.",
        'not_found': "NexoB2B no reconoce esta dirección. Revisá la URL configurada.",
        'server': "NexoB2B tuvo un error interno. Probá de nuevo en un rato.",
        'invalid_response': "La respuesta no parece venir de NexoB2B. Revisá la URL configurada.",
        'client': "NexoB2B rechazó la solicitud.",
    }

    def __init__(self, kind, message=None, status=None, request_id=None, detail=None):
        self.kind = kind
        self.status = status
        self.request_id = request_id
        # Mensaje que devolvió el servidor (ya viene pensado para el usuario
        # en NexoB2B, por ejemplo «Tu usuario está desactivado…»).
        self.detail = detail
        super().__init__(message or self.MESSAGES.get(kind, self.MESSAGES['client']))

    def user_message(self):
        msg = str(self)
        if self.detail and self.detail not in msg:
            msg = f"{msg} ({self.detail})"
        if self.request_id:
            msg = f"{msg} — Solicitud {self.request_id}"
        return msg


def normalize_base_url(url):
    """Devuelve la URL sin barra final, o lanza bad_url.

    Solo HTTPS. HTTP se acepta únicamente contra la máquina local, para
    desarrollo.
    """
    url = (url or '').strip().rstrip('/')
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise NexoError('bad_url')
    if parts.scheme == 'http' and parts.hostname in LOCAL_HOSTS:
        return url
    if parts.scheme != 'https':
        raise NexoError('bad_url')
    return url


def jwt_expiration(token):
    """Lee `exp` del JWT sin verificar la firma (eso lo hace el servidor).

    Devuelve un timestamp UNIX o None si no se puede leer.
    """
    try:
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data['exp'])
    except (IndexError, KeyError, ValueError, TypeError):
        return None


class NexoClient:
    """Una sesión contra una API de Nexo.

    `on_call` recibe un dict por cada llamada (sin secretos) para que Odoo lo
    guarde en el registro de diagnóstico.
    """

    def __init__(self, base_url, publishable_key=None, token=None, connector='odoo',
                 timeout=DEFAULT_TIMEOUT, on_call=None):
        self.base_url = normalize_base_url(base_url)
        self.publishable_key = publishable_key
        self.token = token
        self.connector = connector
        self.timeout = timeout
        self.on_call = on_call
        self.session = requests.Session()

    def _headers(self, request_id, auth):
        headers = {
            'Accept': 'application/json',
            'X-Nexo-Connector': self.connector,
            'X-Request-Id': request_id,
        }
        if self.publishable_key:
            headers['x-publishable-api-key'] = self.publishable_key
        if auth and self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        return headers

    def _send(self, method, url, **kwargs):
        # Punto único de salida a la red: los tests lo reemplazan.
        return self.session.request(method, url, **kwargs)

    def request(self, method, path, params=None, json_body=None, auth=True):
        request_id = uuid.uuid4().hex
        url = f'{self.base_url}{path}'
        started = time.monotonic()
        entry = {'method': method, 'path': path, 'request_id': request_id,
                 'status': None, 'error_kind': None, 'duration_ms': None}
        try:
            try:
                response = self._send(
                    method, url, params=params, json=json_body,
                    headers=self._headers(request_id, auth),
                    timeout=self.timeout, verify=True, allow_redirects=False,
                )
            except requests.exceptions.Timeout as e:
                raise NexoError('timeout', request_id=request_id) from e
            except requests.exceptions.InvalidURL as e:
                raise NexoError('bad_url', request_id=request_id) from e
            except requests.exceptions.RequestException as e:
                # Incluye errores de certificado: no se reintenta sin verificar.
                raise NexoError('network', request_id=request_id) from e
            entry['status'] = response.status_code
            return self._parse(response, request_id)
        except NexoError as e:
            entry['error_kind'] = e.kind
            raise
        finally:
            entry['duration_ms'] = int((time.monotonic() - started) * 1000)
            _logger.info("Nexo %s %s -> %s %s (%sms) [%s]", method, path, entry['status'],
                         entry['error_kind'] or 'ok', entry['duration_ms'], request_id)
            if self.on_call:
                try:
                    self.on_call(entry)
                except Exception:  # el registro nunca puede romper la llamada
                    _logger.exception("No se pudo guardar el registro de la llamada a Nexo")

    @staticmethod
    def _parse(response, request_id):
        status = response.status_code
        try:
            data = response.json()
        except ValueError:
            data = None
        detail = data.get('error') if isinstance(data, dict) and isinstance(data.get('error'), str) else None
        if 200 <= status < 300:
            if data is None:
                raise NexoError('invalid_response', status=status, request_id=request_id)
            return data
        if 300 <= status < 400:
            # Una redirección suele ser http→https o una URL de otro sitio.
            raise NexoError('not_found', status=status, request_id=request_id)
        if status == 401:
            raise NexoError('auth', status=status, request_id=request_id, detail=detail)
        if status == 403:
            raise NexoError('forbidden', message=detail, status=status, request_id=request_id)
        if status == 404:
            raise NexoError('not_found', status=status, request_id=request_id)
        if status == 400 and detail and 'publishable' in detail.lower():
            raise NexoError('bad_key', status=status, request_id=request_id)
        if status >= 500:
            raise NexoError('server', status=status, request_id=request_id)
        raise NexoError('client', message=detail, status=status, request_id=request_id)
