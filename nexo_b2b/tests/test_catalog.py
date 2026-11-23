"""Pruebas sin red: NexoB2B se simula con respuestas armadas según el código
real de apps/backend/src/api/store (ver docs/API_CONTRACTS.md)."""
import base64
import copy
from unittest.mock import patch

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.nexo_core.lib.client import NexoClient, NexoError

# exp = 2000000000 (año 2033)
TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJjb21lcmNpb19pZCI6ImNvbV8xIiwiZXhwIjoyMDAwMDAwMDAwfQ.firma'

PRODUCTO = {
    'id': 'pm_1',
    'ean': '7791293042312',
    'nombre': 'Vino Tinto Malbec 750ml',
    'descripcion': None,
    'marca': 'Bodega Test',
    'unidad_base': 'unidad',
    'alicuota_iva': '21.00',
    'imagen_url': '/uploads/fotos/pm_1.jpg',
    'imagenes': [],
    'pasillo_id': 'pas_1', 'rubro_id': 'rub_1', 'subrubro_id': 'sub_1',
    'pasillo_nombre': 'Bebidas', 'rubro_nombre': 'Vinos', 'subrubro_nombre': 'Tintos',
    'mayoristas': [{
        'listing_id': 'pml_1',
        'mayorista_id': 'may_1',
        'mayorista_nombre': 'Mayorista Test',
        'muestra_precio': 'neto',
        'tiene_alta': True,
        'presentaciones': [
            {'id': 'pmp_u', 'presentacion_id': 'pp_u', 'nombre': 'Unidad', 'factor': 1, 'ean_propio': None,
             'precio': 1000.0, 'precio_mostrar': 1000.0, 'stock': 50},
            {'id': 'pmp_c', 'presentacion_id': 'pp_c', 'nombre': 'Caja x12', 'factor': 12,
             'ean_propio': '17791293042319', 'precio': 11400.0, 'precio_mostrar': 11400.0, 'stock': 4},
        ],
    }],
}


# Como GET /store/mayoristas/lista (apps/backend/src/api/store/mayoristas/lista/route.ts).
MAYORISTA_1 = {
    'id': 'may_1', 'nombre': 'Mayorista Test', 'email': 'ventas@may1.test', 'telefono': '351000',
    'ciudad': 'Córdoba', 'provincia': 'Córdoba', 'rubros': ['Alimentos y bebidas'], 'descripcion': 'Distribuidora',
    'logo_url': '/uploads/logos/may_1.png', 'portada_url': None, 'distancia_km': 3.2, 'productos_count': 120,
    'muestras': [{'nombre': 'Vino', 'imagen_url': '/uploads/fotos/pm_1.jpg'}],
    'contacto': {'nombre': 'Mayorista Test', 'celular': '351000', 'email': 'ventas@may1.test', 'es_vendedor': False},
}
MAYORISTA_2 = {
    'id': 'may_2', 'nombre': 'Electro Sur', 'ciudad': 'Rosario', 'provincia': 'Santa Fe', 'rubros': ['Electrónica'],
    'distancia_km': None, 'productos_count': 40, 'muestras': [],
    'contacto': {'nombre': 'Ana Vendedora', 'celular': '341000', 'email': 'ana@sur.test', 'es_vendedor': True},
}


class FakeNexoB2B:
    """Responde como NexoB2B según el método y la ruta."""

    def __init__(self):
        self.producto = copy.deepcopy(PRODUCTO)
        self.calls = []
        self.bodies = []
        # mayorista_id → estado de la solicitud de este comercio.
        self.solicitudes = {'may_2': 'aceptado'}

    def __call__(self, client, method, path, params=None, json_body=None, auth=True):
        self.calls.append((method, path, params, auth))
        if path == '/store/comercios/auth':
            if json_body['password'] != 'buena':
                raise NexoError('auth', status=401)
            usuario = None
            if json_body['email'] == 'sinpermiso@test':
                usuario = {'nombre': 'Empleado', 'permisos': ['pedidos']}
            elif json_body['email'] == 'equipo@test':
                usuario = {'nombre': 'Compras', 'permisos': ['comprar']}
            return {'token': TOKEN, 'comercio': {'id': 'com_1', 'nombre': 'Comercio Test'}, 'usuario': usuario}
        if path == '/store/comercios/me':
            return {'comercio': {'id': 'com_1', 'nombre': 'Comercio Test', 'lat': -31.4, 'lng': -64.2,
                                 'rubros': ['Almacén']}, 'usuario': None}
        if path == '/store/tipos-comercio':
            return {'tipos': ['Almacén', 'Kiosco'], 'equivalencias': {
                'Almacén': ['Alimentos y bebidas', 'Limpieza y hogar'], 'Kiosco': ['Alimentos y bebidas', 'Otros']}}
        if path == '/store/mayoristas/lista':
            mayoristas = [MAYORISTA_1, MAYORISTA_2]
            rubros = (params or {}).get('rubros') or []
            if rubros:
                mayoristas = [m for m in mayoristas if set(m['rubros']) & set(rubros)]
            result = []
            for m in mayoristas:
                m = copy.deepcopy(m)
                estado = self.solicitudes.get(m['id'])
                m['solicitud'] = {'id': 'sol_' + m['id'], 'estado': estado, 'mayorista_id': m['id']} if estado else None
                result.append(m)
            return {'mayoristas': result, 'meta': {'total': len(result)}}
        if path == '/store/solicitudes' and method == 'POST':
            self.bodies.append(json_body)
            if json_body['mayorista_id'] in self.solicitudes:
                raise NexoError('client', message='Ya existe una solicitud con este mayorista', status=409)
            self.solicitudes[json_body['mayorista_id']] = 'pendiente'
            return {'solicitud': {'id': 'sol_new', 'estado': 'pendiente', 'mayorista_id': json_body['mayorista_id']}}
        if path == '/store/taxonomia':
            return {
                'pasillos': [{'id': 'pas_1', 'nombre': 'Bebidas'}],
                'rubros': [{'id': 'rub_1', 'nombre': 'Vinos', 'pasillo_id': 'pas_1'}],
                'subrubros': [{'id': 'sub_1', 'nombre': 'Tintos', 'rubro_id': 'rub_1'}],
                'alicuotas': [{'id': 'a1', 'nombre': 'IVA 21', 'porcentaje': 21}],
            }
        if path == '/store/productos':
            return {'productos': [self.producto], 'total': 1, 'page': 1, 'pageSize': 20, 'totalPages': 1}
        raise NexoError('not_found', status=404)


# PNG de 1×1 px.
PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')


@tagged('post_install', '-at_install', 'nexo')
class TestNexoB2bCatalog(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_str('nexo_b2b.url', 'https://api.nexob2b.test')
        ICP.set_str('nexo_b2b.publishable_key', 'pk_test')
        cls.service = cls.env['nexo.b2b.service']

    def setUp(self):
        super().setUp()
        # La base puede tener una sesión real (alguien inició sesión a mano):
        # los tests arrancan sin ninguna. Se deshace al terminar cada test.
        self.env['nexo.token'].sudo().search([]).unlink()
        # Lo mismo con los mapeos que alguien haya cargado a mano.
        self.env['nexo.b2b.tax.map'].search([]).unlink()
        self.env['nexo.b2b.category.map'].search([]).unlink()
        self.fake = FakeNexoB2B()
        patcher = patch.object(NexoClient, 'request', autospec=True, side_effect=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.real_download_image = type(self.service)._download_image
        image = patch.object(type(self.service), '_download_image', return_value=None)
        image.start()
        self.addCleanup(image.stop)

    def _login(self, email='equipo@test'):
        return self.service.login(email, 'buena')

    # -- Sesión -------------------------------------------------------

    def test_login_stores_token_not_password(self):
        res = self._login()
        self.assertTrue(res['ok'], res)
        record = self.env['nexo.token']._get('nexob2b')
        self.assertEqual(record.token, TOKEN)
        self.assertEqual(record.account_name, 'Comercio Test')
        self.assertTrue(record.expires_at)
        stored = repr(record.read()[0])
        self.assertNotIn('buena', stored)

    def test_login_wrong_password(self):
        res = self.service.login('equipo@test', 'mala')
        self.assertFalse(res['ok'])
        self.assertIn('incorrectos', res['message'])
        self.assertFalse(self.env['nexo.token']._get('nexob2b'))

    def test_login_requires_comprar(self):
        res = self._login('sinpermiso@test')
        self.assertFalse(res['ok'])
        self.assertIn('comprar', res['message'])
        self.assertFalse(self.env['nexo.token']._get('nexob2b'))

    def test_test_connection(self):
        self.assertFalse(self.service.test_connection()['ok'])
        self._login()
        self.assertTrue(self.service.test_connection()['ok'])

    # -- Búsqueda -----------------------------------------------------

    def test_search_params_and_image(self):
        self._login()
        res = self.service.search_catalog('malbec', 2, True, {'pasillo_id': 'pas_1', 'rubro_id': 'rub_1', 'otro': 'x'})
        self.assertTrue(res['ok'])
        _method, path, params, auth = self.fake.calls[-1]
        self.assertEqual(path, '/store/productos')
        # comercio_id sale del token (nunca sin token) y los filtros desconocidos no pasan.
        self.assertEqual(params, {'page': 2, 'pageSize': 50, 'q': 'malbec', 'con_stock': '1',
                                  'pasillo_id': 'pas_1', 'rubro_id': 'rub_1', 'comercio_id': 'com_1'})
        self.assertTrue(auth)
        producto = res['productos'][0]
        self.assertEqual(producto['imagen_url'], 'https://api.nexob2b.test/uploads/fotos/pm_1.jpg')
        self.assertFalse(producto['mayoristas'][0]['agregado'])
        self.assertTrue(producto['mayoristas'][0]['tiene_alta'])

        self.service.search_catalog('', 1, False)
        self.assertNotIn('con_stock', self.fake.calls[-1][2])
        self.assertNotIn('q', self.fake.calls[-1][2])

    def test_taxonomy(self):
        res = self.service.get_taxonomy()
        self.assertTrue(res['ok'])
        self.assertEqual(res['rubros'], [{'id': 'rub_1', 'nombre': 'Vinos', 'pasillo_id': 'pas_1'}])
        self.assertEqual(res['subrubros'][0]['rubro_id'], 'rub_1')

    # -- Mayoristas ---------------------------------------------------

    def test_mayoristas_context(self):
        self.assertFalse(self.service.get_mayoristas_context()['ok'])
        self._login()
        res = self.service.get_mayoristas_context()
        self.assertTrue(res['ok'])
        self.assertEqual(res['ubicacion'], {'lat': -31.4, 'lng': -64.2})
        # Los rubros de mayorista a los que le compra un «Almacén», como el portal.
        self.assertEqual(res['rubros_sugeridos'], ['Alimentos y bebidas', 'Limpieza y hogar'])
        self.assertEqual(res['rubros'], ['Alimentos y bebidas', 'Limpieza y hogar', 'Otros'])
        self.assertTrue(res['puede_pedir_alta'])

    def test_search_mayoristas(self):
        self._login()
        partner = self.env['res.partner'].create({'name': 'Mayorista Test', 'nexo_mayorista_id': 'may_1'})
        res = self.service.search_mayoristas(' sur ', 100, ['Alimentos y bebidas'], {'lat': -31.4, 'lng': -64.2})
        self.assertTrue(res['ok'])
        method, path, params, auth = self.fake.calls[-1]
        self.assertEqual((method, path, auth), ('GET', '/store/mayoristas/lista', True))
        self.assertEqual(params, {'radio_km': 100, 'lat': -31.4, 'lng': -64.2, 'busqueda': 'sur',
                                  'rubros': ['Alimentos y bebidas']})
        self.assertFalse(res['sin_de_sus_rubros'])
        self.assertEqual(len(res['mayoristas']), 1)
        m = res['mayoristas'][0]
        self.assertEqual(m['logo_url'], 'https://api.nexob2b.test/uploads/logos/may_1.png')
        self.assertEqual(m['muestras'][0]['imagen_url'], 'https://api.nexob2b.test/uploads/fotos/pm_1.jpg')
        self.assertIsNone(m['estado'])
        self.assertEqual(m['partner_id'], partner.id)

    def test_search_mayoristas_falls_back_to_all(self):
        # Con sus rubros no hay nadie: se muestran todos los de la zona, con aviso.
        self._login()
        res = self.service.search_mayoristas('', 12345, ['Automotor'])
        self.assertTrue(res['sin_de_sus_rubros'])
        self.assertEqual([m['id'] for m in res['mayoristas']], ['may_1', 'may_2'])
        self.assertEqual(res['mayoristas'][1]['estado'], 'aceptado')
        # Radio desconocido → 50, y sin ubicación no se mandan coordenadas.
        self.assertEqual(self.fake.calls[-1][2], {'radio_km': 50})

    def test_request_alta(self):
        self._login()
        res = self.service.request_alta('may_1', '  Somos un almacén  ')
        self.assertEqual(res, {'ok': True, 'estado': 'pendiente'})
        self.assertEqual(self.fake.bodies[-1], {'mayorista_id': 'may_1', 'mensaje': 'Somos un almacén'})
        # NexoB2B no deja repetirla (409).
        again = self.service.request_alta('may_1')
        self.assertFalse(again['ok'])
        self.assertIn('Ya hay una solicitud', again['message'])
        self.assertEqual(self.fake.bodies[-1], {'mayorista_id': 'may_1'})

    def test_request_alta_needs_purchase_manager(self):
        self._login()
        user = new_test_user(self.env, login='nexo_compras', groups='purchase.group_purchase_user')
        service = self.service.with_user(user)
        self.assertFalse(service.get_mayoristas_context()['puede_pedir_alta'])
        with self.assertRaises(AccessError):
            service.request_alta('may_1')
        self.assertFalse(self.fake.bodies)

    def test_catalog_by_mayorista(self):
        self._login()
        self.service.search_catalog('', 1, True, {'mayorista_id': 'may_1'})
        self.assertEqual(self.fake.calls[-1][2]['mayorista_id'], 'may_1')

    # -- Agregar ------------------------------------------------------

    def _choices(self):
        return {'tax': {'without_tax': True}, 'category': {'mode': 'create'}}

    def test_add_asks_for_tax_and_category(self):
        self._login()
        res = self.service.add_product('pm_1', PRODUCTO['ean'], 'may_1')
        self.assertEqual(res['status'], 'needs_input')
        self.assertEqual(res['tax']['alicuota'], 21.0)
        self.assertEqual(res['category']['ruta'], 'NexoB2B / Bebidas / Vinos / Tintos')
        self.assertFalse(self.env['product.template'].search([('barcode', '=', PRODUCTO['ean'])]))

    def test_add_creates_product_vendor_and_lines(self):
        self._login()
        res = self.service.add_product('pm_1', PRODUCTO['ean'], 'may_1', self._choices())
        self.assertEqual(res['status'], 'ok', res)
        template = self.env['product.template'].browse(res['product_id'])
        self.assertEqual(template.barcode, PRODUCTO['ean'])
        self.assertEqual(template.nexo_b2b_id, 'pm_1')
        self.assertEqual(template.nexo_marca, 'Bodega Test')
        self.assertEqual(template.uom_id, self.env.ref('uom.product_uom_unit'))
        self.assertEqual(template.categ_id.complete_name, 'NexoB2B / Bebidas / Vinos / Tintos')

        partner = self.env['res.partner'].search([('nexo_mayorista_id', '=', 'may_1')])
        self.assertEqual(partner.name, 'Mayorista Test')

        lines = template.seller_ids.sorted('nexo_factor')
        self.assertEqual(lines.mapped('nexo_presentacion_id'), ['pmp_u', 'pmp_c'])
        unit_line, box_line = lines
        self.assertEqual(unit_line.uom_id, template.uom_id)
        self.assertEqual(unit_line.price, 1000.0)
        # La caja va en su unidad de compra y con su precio tal cual (DEC-005, DEC-008).
        self.assertEqual(box_line.uom_id.relative_uom_id, template.uom_id)
        self.assertEqual(box_line.uom_id.relative_factor, 12.0)
        self.assertEqual(box_line.price, 11400.0)
        self.assertEqual(box_line.partner_id, partner)
        barcode = self.env['product.uom'].search([('barcode', '=', '17791293042319')])
        self.assertEqual(barcode.uom_id, box_line.uom_id)
        self.assertEqual(barcode.product_id, template.product_variant_id)

    def test_add_twice_does_not_duplicate(self):
        self._login()
        first = self.service.add_product('pm_1', PRODUCTO['ean'], 'may_1', self._choices())
        self.fake.producto['mayoristas'][0]['presentaciones'][1]['precio'] = 12000.0
        second = self.service.add_product('pm_1', PRODUCTO['ean'], 'may_1')
        self.assertEqual(second['status'], 'ok', second)
        self.assertEqual(first['product_id'], second['product_id'])
        self.assertFalse(second['product_created'])
        self.assertEqual((second['lines_created'], second['lines_updated']), (0, 2))
        self.assertEqual(self.env['product.template'].search_count([('barcode', '=', PRODUCTO['ean'])]), 1)
        self.assertEqual(self.env['res.partner'].search_count([('nexo_mayorista_id', '=', 'may_1')]), 1)
        template = self.env['product.template'].browse(second['product_id'])
        self.assertEqual(len(template.seller_ids), 2)
        self.assertEqual(template.seller_ids.filtered(lambda s: s.nexo_factor == 12).price, 12000.0)
        # La misma unidad «x12» se reusa.
        self.assertEqual(self.env['uom.uom'].search_count([
            ('relative_uom_id', '=', template.uom_id.id), ('relative_factor', '=', 12.0)]), 1)

    def test_add_existing_product_by_ean_keeps_its_data(self):
        self._login()
        own = self.env['product.template'].create({'name': 'Mi Malbec', 'barcode': PRODUCTO['ean']})
        res = self.service.add_product('pm_1', PRODUCTO['ean'], 'may_1')
        self.assertEqual(res['status'], 'ok', res)
        self.assertEqual(res['product_id'], own.id)
        self.assertEqual(own.name, 'Mi Malbec')
        self.assertEqual(own.nexo_b2b_id, 'pm_1')
        self.assertEqual(len(own.seller_ids), 2)

    def test_category_map_is_remembered(self):
        self._login()
        existing = self.env['product.category'].create({'name': 'Vinos del comercio'})
        res = self.service.add_product('pm_1', PRODUCTO['ean'], 'may_1', {
            'tax': {'without_tax': True}, 'category': {'mode': 'existing', 'categ_id': existing.id}})
        self.assertEqual(self.env['product.template'].browse(res['product_id']).categ_id, existing)
        mapping = self.env['nexo.b2b.category.map'].search([('nexo_key', '=', 'sub_1')])
        self.assertEqual(mapping.categ_id, existing)

    def test_tax_map_is_remembered(self):
        self._login()
        self.service.add_product('pm_1', PRODUCTO['ean'], 'may_1', self._choices())
        tax_map = self.env['nexo.b2b.tax.map']._for(21)
        self.assertTrue(tax_map.confirmed)

    def test_image_is_saved_on_product(self):
        # Odoo 20 rechaza bytes en los campos binarios: hace falta BinaryValue.
        response = type('Resp', (), {
            'status_code': 200,
            'headers': {'Content-Type': 'image/png'},
            'raw': type('Raw', (), {'read': lambda self, n, decode_content=True: PNG})(),
        })()
        with patch('odoo.addons.nexo_b2b.models.nexo_b2b_service.requests.get', return_value=response):
            image = self.real_download_image(self.service, 'https://api.nexob2b.test/x.png')
        self._login()
        with patch.object(type(self.service), '_download_image', return_value=image):
            res = self.service.add_product('pm_1', PRODUCTO['ean'], 'may_1', self._choices())
        self.assertEqual(res['status'], 'ok', res)
        template = self.env['product.template'].browse(res['product_id'])
        self.assertTrue(template.image_1920)

    def test_deleted_tax_asks_again(self):
        # Cambiar la localización fiscal borra impuestos: el mapeo no lo
        # bloquea, queda sin impuesto y se vuelve a preguntar.
        tax = self.env['account.tax'].create({'name': 'IVA 21 prueba', 'amount': 21, 'type_tax_use': 'purchase'})
        tax_map = self.env['nexo.b2b.tax.map'].create({
            'alicuota': float(PRODUCTO['alicuota_iva']), 'purchase_tax_id': tax.id, 'confirmed': True})
        self.assertTrue(tax_map._is_usable())
        tax.unlink()
        self.assertFalse(tax_map.purchase_tax_id)
        self.assertFalse(tax_map._is_usable())
        self._login()
        res = self.service.add_product('pm_1', PRODUCTO['ean'], 'may_1', {'category': {'mode': 'create'}})
        self.assertEqual(res['status'], 'needs_input')
        self.assertTrue(res['tax'])

    def test_tax_of_wrong_type(self):
        sale_tax = self.env['account.tax'].create({'name': 'IVA 21 venta prueba', 'amount': 21, 'type_tax_use': 'sale'})
        TaxMap = self.env['nexo.b2b.tax.map']
        with self.assertRaises(ValidationError):
            TaxMap.create({'alicuota': 21.0, 'purchase_tax_id': sale_tax.id, 'confirmed': True})
        # Una fila vieja con el tipo equivocado no se usa: se vuelve a preguntar.
        tax_map = TaxMap.create({'alicuota': 21.0, 'confirmed': True, 'without_tax': True})
        self.env.cr.execute("UPDATE nexo_b2b_tax_map SET purchase_tax_id = %s WHERE id = %s", (sale_tax.id, tax_map.id))
        tax_map.invalidate_recordset()
        self.assertFalse(tax_map._is_usable())
        self._login()
        res = self.service.add_product('pm_1', PRODUCTO['ean'], 'may_1', {'category': {'mode': 'create'}})
        self.assertEqual(res['status'], 'needs_input')
        self.assertNotEqual(res['tax']['purchase_tax_id'], sale_tax.id)

    def test_expired_session_asks_login(self):
        res = self.service.search_catalog('malbec')
        self.assertFalse(res['ok'])
        self.assertEqual(res['kind'], 'auth')
