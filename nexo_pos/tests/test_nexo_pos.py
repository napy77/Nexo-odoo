"""NexoPOS simulado: se reemplaza NexoClient.request, como en nexo_b2b.

La forma de las respuestas sale de backend/src/modules/erp.ts de NexoPOS.
"""
import copy
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nexo_core.lib.client import NexoClient, NexoError

KEY = 'npos_test_no_es_real'


class FakeNexoPos:

    def __init__(self):
        self.productos = [
            {'id': '10', 'ean': '7790001000011', 'nombre': 'Yerba 1 kg', 'stock': 5, 'precio_centavos': None},
            {'id': '11', 'ean': '7790001000028', 'nombre': 'Azúcar 1 kg', 'stock': 0, 'precio_centavos': 120000},
            {'id': '12', 'ean': '7790009999999', 'nombre': 'Repetido A', 'stock': 1},
            {'id': '13', 'ean': '7790009999999', 'nombre': 'Repetido B', 'stock': 1},
            {'id': '14', 'ean': None, 'nombre': 'Tarta de verdura', 'unidad': 'unidad', 'stock': 4,
             'precio_centavos': 550000, 'costo_centavos': 300000},
            {'id': '15', 'ean': '7790002000010', 'nombre': 'Harina 000 — unidad', 'unidad': 'unidad', 'stock': 0,
             'precio_centavos': 121000, 'costo_centavos': 70000, 'es_insumo': True},
        ]
        self.calls = []
        self.fail = None

    def _find(self, ref):
        # Como `resolver` de NexoPOS: por id, o por EAN si no hay id.
        if ref.get('id'):
            return [p for p in self.productos if p['id'] == ref['id']]
        return [p for p in self.productos if p['ean'] == ref.get('ean')]

    def __call__(self, client, method, path, params=None, json_body=None, auth=True):
        self.calls.append((method, path, copy.deepcopy(params), copy.deepcopy(json_body)))
        if client.token != KEY:
            raise NexoError('auth', service='NexoPOS', status=401, detail='Clave de API inválida')
        if self.fail:
            raise NexoError(self.fail, service='NexoPOS')
        if path == '/productos' and method == 'GET':
            page = (params or {}).get('page', 1)
            return {'productos': copy.deepcopy(self.productos) if page == 1 else [],
                    'page': page, 'pageSize': 500, 'total': len(self.productos)}
        if path == '/productos' and method == 'POST':
            return {'importacion_id': '41', 'estado': 'analizando'}
        if path == '/precios':
            faltan = [{'id': l.get('id'), 'ean': l.get('ean')} for l in json_body['precios'] if len(self._find(l)) != 1]
            return {'aplicados': len(json_body['precios']) - len(faltan), 'no_encontrados': faltan}
        if path == '/stock':
            faltan = []
            for l in json_body['stock']:
                match = self._find(l)
                if len(match) != 1:
                    faltan.append({'id': l.get('id'), 'ean': l.get('ean')})
                    continue
                match[0]['stock'] = l['cantidad'] if json_body['modo'] == 'absoluto' else match[0]['stock'] + l['cantidad']
            return {'modo': json_body['modo'], 'aplicados': len(json_body['stock']) - len(faltan), 'no_encontrados': faltan}
        raise AssertionError(f"Ruta no simulada: {method} {path}")


@tagged('post_install', '-at_install')
class TestNexoPos(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service = cls.env['nexo.pos.service']
        cls.warehouse = cls.env['stock.warehouse'].search([('company_id', '=', cls.env.company.id)], limit=1)
        cls.stock = cls.warehouse.lot_stock_id
        cls.tax = cls.env['account.tax'].create({
            'name': 'IVA 21 % prueba NexoPOS', 'amount': 21, 'amount_type': 'percent', 'type_tax_use': 'sale',
            'price_include_override': 'tax_excluded', 'company_id': cls.env.company.id,
        })
        Product = cls.env['product.product']
        cls.yerba = Product.create({'name': 'Yerba', 'barcode': '7790001000011', 'is_storable': True,
                                    'list_price': 1000.0, 'taxes_id': [(6, 0, cls.tax.ids)]})
        cls.azucar = Product.create({'name': 'Azúcar', 'barcode': '7790001000028', 'is_storable': True,
                                     'list_price': 1200.0, 'taxes_id': [(6, 0, [])]})
        cls.repetido = Product.create({'name': 'Repetido', 'barcode': '7790009999999', 'is_storable': True})
        cls.solo_odoo = Product.create({'name': 'Solo en Odoo', 'barcode': '7790005555555', 'is_storable': True,
                                        'list_price': 500.0, 'standard_price': 300.0, 'taxes_id': [(6, 0, [])]})

    def setUp(self):
        super().setUp()
        self.env['nexo.token'].sudo().search([('service', '=', 'nexopos')]).unlink()
        self.service._set_push(False)
        self.fake = FakeNexoPos()
        patcher = patch.object(NexoClient, 'request', autospec=True, side_effect=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _connect(self):
        self.service.set_key(KEY)
        self.service._set_push(True)
        self.service.link_products()
        self.fake.calls.clear()

    def _inventory(self, product, qty, **ctx):
        quant = self.env['stock.quant'].with_context(inventory_mode=True).create({
            'product_id': product.id, 'location_id': self.stock.id, 'inventory_quantity': qty})
        quant.with_context(**ctx).action_apply_inventory()

    def _deliver(self, product, qty):
        move = self.env['stock.move'].create({
            'product_id': product.id, 'product_uom_qty': qty, 'uom_id': product.uom_id.id,
            'location_id': self.stock.id, 'location_dest_id': self.env.ref('stock.stock_location_customers').id,
        })
        move._action_confirm()
        move._action_assign()
        move.quantity = qty
        move.picked = True
        move._action_done()
        return move

    def _queue(self, product):
        return self.env['nexo.pos.stock.queue'].search([('product_id', '=', product.id)])

    # -- Clave -----------------------------------------------------------

    def test_key_is_tested_before_storing(self):
        with self.assertRaises(UserError):
            self.service.set_key('npos_equivocada')
        self.assertFalse(self.env['nexo.token']._get('nexopos'))
        self.assertEqual(self.service.set_key(f'  {KEY}  '), 6)
        self.assertEqual(self.env['nexo.token']._valid_token('nexopos'), KEY)
        self.assertNotIn(KEY, str(self.env['nexo.log'].search([]).read()), "La clave no va al registro")

    def test_errors_name_nexopos(self):
        self.service.set_key(KEY)
        self.fake.fail = 'timeout'
        self.assertIn('NexoPOS tardó demasiado', self.service.test_connection()['message'])

    # -- Productos ---------------------------------------------------------

    def test_link_by_barcode(self):
        self.service.set_key(KEY)
        result = self.service.link_products()
        self.assertEqual(self.yerba.nexo_pos_id, '10')
        self.assertEqual(self.azucar.nexo_pos_id, '11')
        self.assertFalse(self.repetido.nexo_pos_id, "Un EAN repetido en NexoPOS no se adivina")
        self.assertEqual(result['ean_repetido'], ['7790009999999'])
        self.assertGreaterEqual(result['solo_odoo'], 1)
        self.assertTrue(self.yerba.product_tmpl_id.nexo_pos_linked)
        self.assertTrue(all(m == 'GET' for m, *_ in self.fake.calls), "Vincular solo lee en NexoPOS")

        self.fake.productos = [p for p in self.fake.productos if p['id'] != '11']
        result = self.service.link_products()
        self.assertFalse(self.azucar.nexo_pos_id)
        self.assertEqual(result['desvinculados'], 1)

    def test_create_in_nexopos_only_sends_unlinked(self):
        self._connect()
        self._inventory(self.solo_odoo, 4)
        self.service.create_in_nexopos(self.yerba | self.solo_odoo)
        body = self.fake.calls[-1][3]
        self.assertEqual([f['ean'] for f in body['productos']], ['7790005555555'])
        self.assertEqual(body['productos'][0]['stock'], 4)
        self.assertEqual(body['productos'][0]['precio_centavos'], 50000)
        self.assertEqual(body['productos'][0]['costo_centavos'], 30000)

    # -- Precios -----------------------------------------------------------

    def test_prices_include_tax_and_only_changes(self):
        self._connect()
        self.assertTrue(self.yerba.nexo_pos_price_dirty, "Al vincular se manda el precio de Odoo")
        self.service.push_prices()
        precios = {l['id']: l['precio_centavos'] for l in self.fake.calls[-1][3]['precios']}
        self.assertEqual(precios['10'], 121000, "1000 + 21 % de IVA, en centavos")
        self.assertEqual(precios['11'], 120000)
        self.assertFalse(self.yerba.nexo_pos_price_dirty)

        self.fake.calls.clear()
        self.service.push_prices()
        self.assertFalse(self.fake.calls, "Sin cambios no se manda nada")

        self.yerba.product_tmpl_id.list_price = 1100.0
        self.assertTrue(self.yerba.nexo_pos_price_dirty)
        self.service.push_prices()
        self.assertEqual(self.fake.calls[-1][3]['precios'], [{'id': '10', 'precio_centavos': 133100}])

    # -- Stock -------------------------------------------------------------

    def test_stock_moves_go_as_adjustments(self):
        self._connect()
        self._inventory(self.yerba, 10)
        self._deliver(self.yerba, 3)
        self.assertEqual(sorted(self._queue(self.yerba).mapped('cantidad')), [-3.0, 10.0])
        self.env['nexo.pos.stock.queue']._send_pending()
        body = self.fake.calls[-1][3]
        self.assertEqual(body, {'modo': 'ajuste', 'stock': [{'id': '10', 'cantidad': 7.0}]})
        self.assertEqual(set(self._queue(self.yerba).mapped('state')), {'sent'})
        self.assertEqual(self.fake.productos[0]['stock'], 12)

    def test_unlinked_and_skipped_moves_are_not_sent(self):
        self._connect()
        self._inventory(self.solo_odoo, 5)
        self._inventory(self.yerba, 2, nexo_pos_skip=True)
        self.assertFalse(self._queue(self.solo_odoo))
        self.assertFalse(self._queue(self.yerba), "Lo que NexoPOS ya descontó no se vuelve a mandar")

    def test_no_key_no_queue(self):
        self._connect()
        self.env['nexo.token']._clear('nexopos')
        self._inventory(self.yerba, 3)
        self.assertFalse(self._queue(self.yerba))

    def test_timeout_is_not_retried(self):
        self._connect()
        self._inventory(self.yerba, 4)
        self.fake.fail = 'timeout'
        self.env['nexo.pos.stock.queue']._send_pending()
        row = self._queue(self.yerba)
        self.assertEqual(row.state, 'uncertain')
        self.fake.fail = None
        self.fake.calls.clear()
        self.env['nexo.pos.stock.queue']._send_pending()
        self.assertFalse(self.fake.calls, "Sin respuesta no se reintenta solo: se sumaría dos veces")

    def test_server_error_stays_pending(self):
        self._connect()
        self._inventory(self.yerba, 4)
        self.fake.fail = 'server'
        self.service._cron_push()
        self.assertEqual(self._queue(self.yerba).state, 'pending')

    # -- Comparación -------------------------------------------------------

    def test_compare_and_resolve(self):
        self._connect()
        self._inventory(self.yerba, 7)          # Odoo 7; NexoPOS 5 + 7 = 12 al mandarlo
        self._inventory(self.azucar, 2)
        self.env['nexo.pos.stock.queue']._send_pending()   # NexoPOS: yerba 12, azúcar 2
        self.fake.productos[0]['stock'] = 10    # un pedido de NexoTienda aceptado allá
        Diff = self.env['nexo.pos.stock.diff']
        self.assertEqual(Diff._compare(), 1)
        diff = Diff.search([])
        self.assertEqual((diff.product_id, diff.stock_odoo, diff.stock_nexopos, diff.diferencia),
                         (self.yerba, 7.0, 10.0, -3.0))

        diff.action_use_nexopos()
        self.assertEqual(self.yerba.with_context(warehouse_id=self.warehouse.id).qty_available, 10.0)
        self.assertFalse(self._queue(self.yerba).filtered(lambda r: r.state == 'pending'),
                         "El ajuste en Odoo no se vuelve a mandar")
        self.assertEqual(Diff._compare(), 0)

        self.fake.productos[1]['stock'] = 0
        Diff._compare()
        Diff.search([]).action_use_odoo()
        self.env['nexo.pos.stock.queue']._send_pending()
        self.assertEqual(self.fake.productos[1]['stock'], 2)

    def test_read_only_by_default(self):
        self.service.set_key(KEY)
        self.service.link_products()
        self._inventory(self.yerba, 3)
        self.assertFalse(self._queue(self.yerba), "En «solo lee» no se encola stock")
        self.service._cron_push()
        self.assertFalse([c for c in self.fake.calls if c[0] in ('PUT', 'POST')], "Ni precios ni stock")
        self.env['nexo.pos.stock.diff']._compare()
        with self.assertRaises(UserError):
            self.env['nexo.pos.stock.diff'].search([]).action_use_odoo()

    def test_compare_button_from_list(self):
        """Como lo llama el botón de la lista: con registros (o ninguno)."""
        self._connect()
        Diff = self.env['nexo.pos.stock.diff']
        action = Diff.browse().action_compare_now()
        self.assertEqual(action['res_model'], 'nexo.pos.stock.diff')
        Diff.search([]).action_compare_now()

    def test_import_from_nexopos(self):
        self.env.company.account_sale_tax_id = self.tax
        self._connect()
        result = self.service.import_from_nexopos()
        self.assertEqual(result['creados'], 2)
        self.assertEqual(result['ya_estaban'], 2)
        self.assertEqual(result['ean_repetido'], ['7790009999999'])
        Product = self.env['product.product']
        tarta = Product.search([('nexo_pos_id', '=', '14')])
        harina = Product.search([('nexo_pos_id', '=', '15')])
        self.assertFalse(tarta.barcode, "Sin EAN se vincula igual, por id")
        self.assertEqual(harina.name, 'Harina 000', "La presentación no va en el nombre")
        self.assertEqual(harina.barcode, '7790002000010')
        self.assertAlmostEqual(harina.list_price, 1000.0, 2, "El precio de NexoPOS lleva IVA")
        self.assertEqual(self.service._sale_price(harina), 1210.0, "Ida y vuelta da el mismo precio")
        self.assertEqual(harina.standard_price, 700.0)
        self.assertFalse(harina.sale_ok, "Un insumo no se vende")
        self.assertEqual(tarta.with_context(warehouse_id=self.warehouse.id).qty_available, 4.0)
        self.assertFalse(self._queue(tarta), "El stock inicial ya está en NexoPOS: no se manda")
        self.assertFalse((tarta | harina).filtered('nexo_pos_price_dirty'))
        self.assertFalse([c for c in self.fake.calls if c[0] != 'GET'], "Traer solo lee en NexoPOS")
        self.assertEqual(self.service.import_from_nexopos()['creados'], 0, "Repetir no duplica")
        self.env['nexo.pos.stock.diff']._compare()
        diffs = self.env['nexo.pos.stock.diff'].search([]).product_id
        self.assertFalse(diffs & (tarta | harina), "Lo traído coincide con NexoPOS")
        self.assertIn(self.yerba, diffs, "La yerba ya estaba en Odoo con otro stock: eso sí se ve")
