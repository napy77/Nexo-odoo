"""Pedidos de NexoTienda con NexoPOS simulado.

Las reglas del simulado salen de backend/src/modules/pedidos.ts y erp.ts de
NexoPOS (commit cd15a2c): pasos posibles, repetir contesta 200 sin hacer
nada, un paso que no corresponde es 409 `paso_invalido` con el `status`, y el
stock se descuenta al aceptar y vuelve al cancelar.
"""
import copy
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.nexo_core.lib.client import NexoClient, NexoError
from odoo.addons.nexo_pos.tests.test_nexo_pos import KEY, FakeNexoPos

PASOS = {
    'recibido': ['aceptado', 'cancelado'],
    'aceptado': ['listo', 'cancelado'],
    'listo': ['en_camino', 'entregado', 'cancelado'],
    'en_camino': ['entregado', 'cancelado'],
    'entregado': [],
    'cancelado': [],
}
STEP_STATE = {'aceptar': 'aceptado', 'listo': 'listo', 'en-camino': 'en_camino',
              'entregado': 'entregado', 'cancelar': 'cancelado'}


class FakeNexoPosPedidos(FakeNexoPos):

    def __init__(self):
        super().__init__()
        self.pedidos = {}
        self.clock = 0

    def _tick(self):
        self.clock += 1
        return f"2026-11-23T20:{self.clock // 60:02d}:{self.clock % 60:02d}.000001Z"

    def add(self, code, lineas, status='recibido', **extra):
        p = {
            'code': code, 'status': status, 'creado': '2026-11-23T20:41:00.123Z',
            'retiro': {'tipo': 'retiro', 'franja': 'Hoy 18 a 20 h', 'direccion': None},
            'pago': {'medio': 'efectivo_entrega', 'estado': 'no_aplica', 'id': None},
            'contacto': {'nombre': 'Comprador de prueba', 'telefono': '3510000000'},
            'cuenta_id': None, 'notas': None, 'listo_para': None, 'motivo_cancelacion': None,
            'cancelado_por': None, 'revision': None, 'venta': None,
            'lineas': [{'producto_id': pid, 'ean': None, 'sku': None, 'nombre': f'Producto {pid}',
                        'unidad': 'unidad', 'cantidad': qty, 'precio_centavos': precio}
                       for pid, qty, precio in lineas],
            'cargo_centavos': 0,
        }
        p['subtotal_centavos'] = sum(round(l['cantidad'] * l['precio_centavos']) for l in p['lineas'])
        p.update(extra)
        p['total_centavos'] = p['subtotal_centavos'] + p['cargo_centavos']
        p['actualizado'] = self._tick()
        self.pedidos[code] = p
        return p

    def _stock(self, pedido, sign):
        for l in pedido['lineas']:
            for prod in self.productos:
                if prod['id'] == l['producto_id']:
                    prod['stock'] += sign * l['cantidad']

    def set_status(self, code, status):
        p = self.pedidos[code]
        if status == 'aceptado':
            self._stock(p, -1)
        if status == 'cancelado' and p['status'] in ('aceptado', 'listo', 'en_camino'):
            self._stock(p, +1)
        p['status'] = status
        p['actualizado'] = self._tick()

    def __call__(self, client, method, path, params=None, json_body=None, auth=True):
        if not path.startswith('/pedidos'):
            return super().__call__(client, method, path, params, json_body, auth)
        self.calls.append((method, path, copy.deepcopy(params), copy.deepcopy(json_body)))
        if client.token != KEY:
            raise NexoError('auth', service='NexoPOS', status=401)
        if self.fail:
            raise NexoError(self.fail, service='NexoPOS')
        parts = path.strip('/').split('/')
        if method == 'GET' and len(parts) == 1:
            desde = (params or {}).get('actualizados_desde')
            rows = sorted((p for p in self.pedidos.values()
                           if (p['actualizado'] > desde if desde else p['status'] not in ('entregado', 'cancelado'))),
                          key=lambda p: p['actualizado'])
            hasta = rows[-1]['actualizado'] if rows else desde
            return {'pedidos': copy.deepcopy(rows), 'hasta': hasta, 'hay_mas': False}
        code = parts[1].upper()
        if code not in self.pedidos:
            raise NexoError('not_found', service='NexoPOS', status=404, code='pedido_inexistente',
                            data={'error': 'No existe ese pedido en este comercio', 'code': 'pedido_inexistente'})
        p = self.pedidos[code]
        if method == 'GET':
            return copy.deepcopy(p)
        step = parts[2]
        target = STEP_STATE[step]
        if step == 'aceptar' and not (json_body or {}).get('listo_para'):
            raise NexoError('client', service='NexoPOS', status=400, code='falta_listo_para')
        if p['status'] != target:
            if target not in PASOS[p['status']]:
                raise NexoError('client', service='NexoPOS', status=409, code='paso_invalido',
                                message=f"Un pedido {p['status']} no puede pasar a {target}.",
                                data={'error': '…', 'code': 'paso_invalido', 'status': p['status']})
            if step == 'aceptar':
                p['listo_para'] = json_body['listo_para']
            if step == 'cancelar':
                p['motivo_cancelacion'] = json_body['motivo']
                p['cancelado_por'] = 'comercio'
            self.set_status(code, target)
        return copy.deepcopy(p)

    def steps(self):
        return [(m, path) for m, path, _p, _b in self.calls if m == 'POST' and path.startswith('/pedidos')]


@tagged('post_install', '-at_install')
class TestNexoTienda(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.service = cls.env['nexo.pos.service']
        cls.Order = cls.env['nexo.tienda.order']
        cls.warehouse = cls.service._warehouse()
        cls.stock = cls.warehouse.lot_stock_id
        cls.tax = cls.env['account.tax'].create({
            'name': 'IVA 21 % prueba NexoTienda', 'amount': 21, 'amount_type': 'percent', 'type_tax_use': 'sale',
            'price_include_override': 'tax_excluded', 'company_id': cls.env.company.id,
        })
        Product = cls.env['product.product']
        cls.yerba = Product.create({'name': 'Yerba', 'barcode': '7790001000011', 'is_storable': True,
                                    'list_price': 1000.0, 'taxes_id': [(6, 0, cls.tax.ids)]})
        cls.azucar = Product.create({'name': 'Azúcar', 'barcode': '7790001000028', 'is_storable': True,
                                     'list_price': 1200.0, 'taxes_id': [(6, 0, [])]})

    def setUp(self):
        super().setUp()
        self.env['nexo.token'].sudo().search([('service', '=', 'nexopos')]).unlink()
        self.env['ir.config_parameter'].sudo().set_str(f'nexo_tienda.cursor.{self.env.company.id}', '')
        self.fake = FakeNexoPosPedidos()
        patcher = patch.object(NexoClient, 'request', autospec=True, side_effect=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service.set_key(KEY)
        self.service._set_push(True)
        self.service.link_products()  # yerba ↔ 10, azúcar ↔ 11
        self.Order._set_enabled(True)
        for product, qty in ((self.yerba, 5), (self.azucar, 0)):
            quant = self.env['stock.quant'].with_context(inventory_mode=True).create({
                'product_id': product.id, 'location_id': self.stock.id, 'inventory_quantity': qty})
            quant.with_context(nexo_pos_skip=True).action_apply_inventory()
        self.fake.calls.clear()

    def _queue(self, product):
        return self.env['nexo.pos.stock.queue'].search([('product_id', '=', product.id)])

    def _order(self, code='P-0001', lineas=(('10', 2, 121000), ('11', 1, 120000)), **kw):
        self.fake.add(code, list(lineas), **kw)
        self.Order._sync()
        return self.Order.search([('code', '=', code)])

    def _accept(self, order, listo_para='30 minutos'):
        wizard = self.env['nexo.tienda.step'].create({'order_id': order.id, 'step': 'aceptar',
                                                      'listo_para': listo_para})
        return wizard.action_confirm()

    def _deliver_picking(self, so):
        for picking in so.picking_ids:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            picking.button_validate()

    def test_sync_creates_sale_order(self):
        order = self._order()
        self.assertEqual(order.state, 'recibido')
        self.assertEqual(order.total, 3620.0)
        so = order.sale_order_id
        self.assertEqual(so.state, 'draft')
        self.assertEqual(so.team_id, self.env.ref('nexo_tienda.crm_team_nexotienda'))
        self.assertEqual(so.client_order_ref, 'P-0001')
        self.assertEqual(so.partner_id.phone, '3510000000')
        self.assertEqual(so.warehouse_id, self.warehouse)
        yerba = so.order_line.filtered(lambda l: l.product_id == self.yerba)
        # $1.210 con IVA en NexoPOS → $1.000 + 21 % en Odoo.
        self.assertAlmostEqual(yerba.price_unit, 1000.0)
        self.assertAlmostEqual(so.amount_total, 3620.0)
        self.assertEqual(self.env['ir.config_parameter'].sudo().get_str(f'nexo_tienda.cursor.{self.env.company.id}'),
                         self.fake.pedidos['P-0001']['actualizado'])
        # Vuelve repetido (el cursor queda atrás): se actualiza, no se duplica.
        self.env['ir.config_parameter'].sudo().set_str(f'nexo_tienda.cursor.{self.env.company.id}', '')
        self.Order._sync()
        self.assertEqual(self.Order.search_count([('code', '=', 'P-0001')]), 1)
        self.assertEqual(self.env['sale.order'].search_count([('client_order_ref', '=', 'P-0001')]), 1)

    def test_unlinked_product_waits(self):
        order = self._order(lineas=(('99', 1, 50000),))
        self.assertFalse(order.sale_order_id)
        self.assertIn('id 99', order.error)
        self.env['product.product'].create({'name': 'Tarta', 'is_storable': True, 'nexo_pos_id': '99',
                                            'taxes_id': [(6, 0, [])]})
        order.action_refresh()
        self.assertTrue(order.sale_order_id)
        self.assertFalse(order.error)

    def test_accept_from_odoo(self):
        order = self._order()
        self._accept(order)
        self.assertEqual(self.fake.steps(), [('POST', '/pedidos/P-0001/aceptar')])
        self.assertEqual(self.fake.calls[-1][3], {'listo_para': '30 minutos'})
        self.assertEqual(order.state, 'aceptado')
        self.assertEqual(order.listo_para, '30 minutos')
        self.assertEqual(order.sale_order_id.state, 'sale')
        # NexoPOS descontó; Odoo no manda nada y la comparación lo sabe.
        self.assertEqual(self.fake.productos[0]['stock'], 3)
        self.assertFalse(self._queue(self.yerba))
        self.env['nexo.pos.stock.diff']._compare()
        self.assertFalse(self.env['nexo.pos.stock.diff'].search([('product_id', '=', self.yerba.id)]))

    def test_step_rejected_keeps_nexopos_state(self):
        order = self._order()
        self.fake.set_status('P-0001', 'cancelado')
        action = self._accept(order)
        self.assertEqual(action['params']['type'], 'danger')
        self.assertEqual(order.state, 'cancelado')
        self.assertIn('cancelado', order.error.lower())
        self.assertEqual(order.sale_order_id.state, 'cancel')

    def test_no_response_can_be_repeated(self):
        order = self._order()
        self._accept(order)
        self.fake.fail = 'timeout'
        action = order.action_ready()
        self.assertEqual(action['params']['type'], 'danger')
        self.assertIn('Tocá de nuevo', order.error)
        self.fake.fail = None
        order.action_ready()
        self.assertEqual(order.state, 'listo')
        self.assertFalse(order.error)

    def test_delivered_in_odoo_is_told_to_nexopos(self):
        order = self._order()
        self._accept(order)
        self._deliver_picking(order.sale_order_id)
        self.assertTrue(order.entregar_pendiente)
        self.assertFalse(self._queue(self.yerba), "NexoPOS ya lo descontó al aceptar")
        self.fake.calls.clear()
        self.Order._send_pending()
        self.assertEqual(self.fake.steps(), [('POST', '/pedidos/P-0001/listo'),
                                             ('POST', '/pedidos/P-0001/entregado')])
        self.assertEqual(order.state, 'entregado')
        self.assertFalse(order.entregar_pendiente)

    def test_delivered_before_accepting_goes_step_by_step(self):
        order = self._order()
        order._mark_delivered()
        self.Order._send_pending()
        self.assertEqual([p for _m, p in self.fake.steps()],
                         ['/pedidos/P-0001/aceptar', '/pedidos/P-0001/listo', '/pedidos/P-0001/entregado'])
        self.assertEqual(order.state, 'entregado')
        self.assertEqual(self.fake.productos[0]['stock'], 3, "Aceptar en NexoPOS es lo que descuenta")

    def test_delivered_in_nexopos_validates_odoo_delivery(self):
        order = self._order()
        for status in ('aceptado', 'listo', 'entregado'):
            self.fake.set_status('P-0001', status)
        self.Order._sync()
        self.assertEqual(order.state, 'entregado')
        so = order.sale_order_id
        self.assertEqual(so.state, 'sale')
        self.assertTrue(so.picking_ids)
        self.assertTrue(all(p.state == 'done' for p in so.picking_ids))
        self.assertEqual(self.yerba.with_context(warehouse_id=self.warehouse.id).qty_available, 3)
        self.assertFalse(self._queue(self.yerba))
        self.assertFalse(order.entregar_pendiente)

    def test_cancelled_in_nexopos_cancels_sale_order(self):
        order = self._order()
        self.fake.set_status('P-0001', 'cancelado')
        self.Order._sync()
        self.assertEqual(order.state, 'cancelado')
        self.assertEqual(order.sale_order_id.state, 'cancel')

    def test_cancel_from_odoo_sends_reason(self):
        order = self._order()
        wizard = self.env['nexo.tienda.step'].create({'order_id': order.id, 'step': 'cancelar',
                                                      'motivo': 'No me queda yerba'})
        wizard.action_confirm()
        self.assertEqual(self.fake.calls[-1][3], {'motivo': 'No me queda yerba'})
        self.assertEqual(order.state, 'cancelado')
        self.assertEqual(order.motivo_cancelacion, 'No me queda yerba')

    def test_paid_online_is_not_charged(self):
        order = self._order(pago={'medio': 'online', 'estado': 'pagado', 'id': 'mp-1'},
                            revision={'motivo': 'cuenta_nueva_monto_alto'})
        self.assertTrue(order.no_cobrar)
        self.assertTrue(order.sale_order_id.nexo_tienda_no_cobrar)
        self.assertIn('primer pedido', order.revision_texto)

    def test_disabled_does_not_sync(self):
        self.Order._set_enabled(False)
        self.fake.add('P-0002', [('10', 1, 121000)])
        self.Order._cron_sync()
        self.assertFalse(self.Order.search([('code', '=', 'P-0002')]))
        self.assertFalse(self.fake.calls)
