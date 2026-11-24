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
        'bad_url': "La URL de {svc} no es válida. Tiene que empezar con https://.",
        'network': "No se pudo conectar con {svc}. Revisá la conexión a internet o la URL.",
        'timeout': "{svc} tardó demasiado en responder. Probá de nuevo en un rato.",
        'auth': "Credenciales inválidas o sesión vencida. Volvé a iniciar sesión.",
        'forbidden': "El usuario no tiene permiso para esta operación en {svc}.",
        'bad_key': "La clave publicable de {svc} no es válida.",
        'not_found': "{svc} no reconoce esta dirección. Revisá la URL configurada.",
        'server': "{svc} tuvo un error interno. Probá de nuevo en un rato.",
        'invalid_response': "La respuesta no parece venir de {svc}. Revisá la URL configurada.",
        'client': "{svc} rechazó la solicitud.",
    }

    def __init__(self, kind, message=None, status=None, request_id=None, detail=None, service='NexoB2B',
                 code=None, data=None):
        self.kind = kind
        self.status = status
        # `code` es el identificador estable que manda el servidor al lado del
        # mensaje (NexoPOS: `paso_invalido`, `pedido_inexistente`…), y `data`
        # el cuerpo del error. Sirven para decidir sin leer el texto.
        self.code = code
        self.data = data
        self.request_id = request_id
        # Mensaje que devolvió el servidor (ya viene pensado para el usuario
        # en NexoB2B, por ejemplo «Tu usuario está desactivado…»).
        self.detail = detail
        self.service = service
        super().__init__(message or self.MESSAGES.get(kind, self.MESSAGES['client']).format(svc=service))

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
                 timeout=DEFAULT_TIMEOUT, on_call=None, service='NexoB2B', auth_header=None):
        # Nombre del servicio para los mensajes al usuario.
        self.service = service
        # Cabecera donde va la clave cuando no es `Authorization: Bearer`
        # (ClubPay la pide en `X-API-Key`).
        self.auth_header = auth_header
        try:
            self.base_url = normalize_base_url(base_url)
        except NexoError as e:
            raise NexoError('bad_url', service=service) from e
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
            if self.auth_header:
                headers[self.auth_header] = self.token
            else:
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
                raise NexoError('timeout', request_id=request_id, service=self.service) from e
            except requests.exceptions.InvalidURL as e:
                raise NexoError('bad_url', request_id=request_id, service=self.service) from e
            except requests.exceptions.RequestException as e:
                # Incluye errores de certificado: no se reintenta sin verificar.
                raise NexoError('network', request_id=request_id, service=self.service) from e
            entry['status'] = response.status_code
            return self._parse(response, request_id, self.service)
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
    def _parse(response, request_id, service='NexoB2B'):
        status = response.status_code
        try:
            data = response.json()
        except ValueError:
            data = None
        detail = None
        if isinstance(data, dict):
            # NexoB2B y NexoPOS mandan `error`; ClubPay a veces `message`.
            detail = next((data[k] for k in ('error', 'message') if isinstance(data.get(k), str)), None)
        if 200 <= status < 300:
            if data is None:
                raise NexoError('invalid_response', service=service, status=status, request_id=request_id)
            return data
        extra = {'code': data.get('code') if isinstance(data.get('code'), str) else None, 'data': data} \
            if isinstance(data, dict) else {}
        if 300 <= status < 400:
            # Una redirección suele ser http→https o una URL de otro sitio.
            raise NexoError('not_found', service=service, status=status, request_id=request_id, **extra)
        if status == 401:
            raise NexoError('auth', service=service, status=status, request_id=request_id, detail=detail, **extra)
        if status == 403:
            raise NexoError('forbidden', service=service, message=detail, status=status, request_id=request_id, **extra)
        if status == 404:
            raise NexoError('not_found', service=service, status=status, request_id=request_id, **extra)
        if status == 400 and detail and 'publishable' in detail.lower():
            raise NexoError('bad_key', service=service, status=status, request_id=request_id, **extra)
        if status >= 500:
            raise NexoError('server', service=service, status=status, request_id=request_id, **extra)
        raise NexoError('client', service=service, message=detail, status=status, request_id=request_id, **extra)
