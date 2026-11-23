"""Pedidos a NexoB2B, sin red. Las respuestas siguen el código real de
apps/backend/src/api/store/ordenes (ver docs/API_CONTRACTS.md)."""
import copy
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nexo_core.lib.client import NexoClient, NexoError

from .test_catalog import PRODUCTO, FakeNexoB2B


class FakeOrdenes(FakeNexoB2B):
    """Agrega las rutas de órdenes al NexoB2B simulado."""

    def __init__(self):
        super().__init__()
        self.ordenes = {}
        self.documentos = {}
        self.fail_post = None
        self.created_on_fail = False

    def _items(self, body):
        # La lista del comercio, no la de Odoo. Como en la API real, los
        # numeric de Postgres llegan como texto.
        precios = {'pmp_u': '950', 'pmp_c': '11000'}
        return [{'presentacion_id': i['presentacion_id'], 'cantidad': i['cantidad'],
                 'precio_unitario': precios[i['presentacion_id']], 'alicuota_iva': '21'} for i in body['items']]

    def __call__(self, client, method, path, params=None, json_body=None, auth=True):
        if not path.startswith('/store/ordenes') and '/medios-pago' not in path and '/transportes' not in path:
            return super().__call__(client, method, path, params, json_body, auth)
        self.calls.append((method, path, params, auth))
        if path == '/store/ordenes' and method == 'POST':
            self.bodies.append(copy.deepcopy(json_body))
            if json_body['mayorista_id'] != 'may_1':
                raise NexoError('forbidden', message='Necesitás estar dado de alta en Otro para comprarle.', status=403)
            numero = f'ORD-{len(self.ordenes) + 1:05d}'
            orden = {'id': f'ord_{len(self.ordenes) + 1}', 'numero': numero, 'estado': 'cargada',
                     'mayorista_id': json_body['mayorista_id'], 'notas': json_body.get('notas'),
                     'medio_pago_nombre': 'Transferencia' if json_body.get('medio_pago_id') else None,
                     'total': '13310', 'costo_medio_pago': '0.00', 'costo_transporte': '0.00', 'monto_descuento': '0.00',
                     'cantidad_bultos': None, 'peso_kg': None,
                     'items': self._items(json_body)}
            if self.fail_post:
                if self.created_on_fail:
                    self.ordenes[orden['id']] = orden
                raise NexoError(self.fail_post)
            self.ordenes[orden['id']] = orden
            return {'orden': copy.deepcopy(orden)}
        if path == '/store/ordenes':
            return {'ordenes': [copy.deepcopy(o) for o in self.ordenes.values()]}
        if path.endswith('/documentos'):
            return {'documentos': self.documentos.get(path.split('/')[3], [])}
        if path.endswith('/cancelar'):
            orden = self.ordenes[path.split('/')[3]]
            if orden['estado'] not in ('cargada', 'devuelto'):
                raise NexoError('client', message='Solo se puede cancelar un pedido cargado o devuelto', status=400)
            orden['estado'] = 'cancelado'
            return {'orden': copy.deepcopy(orden)}
        if '/medios-pago' in path:
            return {'medios_pago': [{'id': 'mp_1', 'nombre': 'Transferencia', 'porcentaje_costo': '0.00'}]}
        if '/transportes' in path:
            return {'transportes': [{'id': 'tr_1', 'nombre': 'Reparto propio', 'porcentaje_costo': 2}]}
        orden = self.ordenes.get(path.split('/')[3])
        if not orden:
            raise NexoError('not_found', status=404)
        return {'orden': copy.deepcopy(orden)}


@tagged('post_install', '-at_install', 'nexo')
class TestNexoB2bOrders(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ICP = cls.env['ir.config_parameter'].sudo()
        ICP.set_str('nexo_b2b.url', 'https://api.nexob2b.test')
        ICP.set_str('nexo_b2b.publishable_key', 'pk_test')
        cls.service = cls.env['nexo.b2b.service']

    def setUp(self):
        super().setUp()
        self.env['nexo.token'].sudo().search([]).unlink()
        self.env['nexo.b2b.tax.map'].search([]).unlink()
        self.env['nexo.b2b.category.map'].search([]).unlink()
        self.fake = FakeOrdenes()
        patcher = patch.object(NexoClient, 'request', autospec=True, side_effect=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        image = patch.object(type(self.service), '_download_image', return_value=None)
        image.start()
        self.addCleanup(image.stop)
        self.service.login('equipo@test', 'buena')
        self.choices = {'tax': {'without_tax': True}, 'category': {'mode': 'create'}}

    def _order(self, cajas=2, unidades=3):
        res = self.service.add_to_order('pm_1', PRODUCTO['ean'], 'may_1', 'pmp_c', cajas, self.choices)
        self.assertEqual(res['status'], 'ok', res)
        if unidades:
            self.service.add_to_order('pm_1', PRODUCTO['ean'], 'may_1', 'pmp_u', unidades, self.choices)
        return self.env['purchase.order'].browse(res['order_id'])

    def _send(self, order, **kwargs):
        return order._nexo_send(**kwargs)

    def _set_estado(self, order, estado, **extra):
        self.fake.ordenes[order.nexo_orden_id].update(estado=estado, **extra)
        return order._nexo_sync()

    # -- Armar el pedido ---------------------------------------------

    def test_add_to_order_builds_draft(self):
        order = self._order(cajas=2, unidades=0)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.partner_id.nexo_mayorista_id, 'may_1')
        self.assertEqual(len(order.order_line), 1)
        line = order.order_line
        self.assertEqual(line.product_qty, 2)
        self.assertEqual(line._nexo_seller().nexo_presentacion_id, 'pmp_c')
        self.assertEqual(line.price_unit, 11400.0)
        # Sumar de nuevo la misma presentación aumenta la cantidad, en el mismo borrador.
        again = self.service.add_to_order('pm_1', PRODUCTO['ean'], 'may_1', 'pmp_c', 1, self.choices)
        self.assertEqual(again['order_id'], order.id)
        self.assertEqual(line.product_qty, 3)
        self.assertFalse(any(c[1] == '/store/ordenes' for c in self.fake.calls), "Armar no le manda nada a NexoB2B")

    def test_same_uom_presentations_stay_apart(self):
        # En NexoB2B hay «Botella» y «Caja» con factor 1: las dos quedan en
        # Unidades y no se pueden distinguir por la unidad.
        pres = self.fake.producto['mayoristas'][0]['presentaciones']
        pres[1].update(nombre='Caja', factor=1, ean_propio=None)
        self.service.add_to_order('pm_1', PRODUCTO['ean'], 'may_1', 'pmp_c', 1, self.choices)
        res = self.service.add_to_order('pm_1', PRODUCTO['ean'], 'may_1', 'pmp_u', 2, self.choices)
        order = self.env['purchase.order'].browse(res['order_id'])
        self.assertEqual(len(order.order_line), 2, "No se suman en la misma línea")
        self.assertEqual(sorted(order.order_line.mapped(lambda l: (l._nexo_seller().nexo_presentacion_id, l.product_qty))),
                         [('pmp_c', 1), ('pmp_u', 2)])
        order._nexo_send()
        self.assertCountEqual(self.fake.bodies[-1]['items'], [{'presentacion_id': 'pmp_c', 'cantidad': 1},
                                                              {'presentacion_id': 'pmp_u', 'cantidad': 2}])
        # Una línea cargada a mano, sin presentación elegida, es ambigua.
        manual = self.env['purchase.order'].create({'partner_id': order.partner_id.id})
        self.env['purchase.order.line'].create({'order_id': manual.id, 'product_id': order.order_line[0].product_id.id,
                                                'product_qty': 1, 'uom_id': order.order_line[0].uom_id.id})
        with self.assertRaisesRegex(UserError, 'varias presentaciones'):
            manual.action_nexo_send()
        ctx = {'nexo_presentacion_label': True, 'use_simplified_supplier_name': True}  # como la vista
        label = manual.order_line.nexo_seller_id.with_context(**ctx)
        sellers = order.order_line.nexo_seller_id.with_context(**ctx)
        self.assertCountEqual([s.display_name.split(' · ')[0] for s in sellers], ['Unidad', 'Caja'])
        self.assertFalse(label)

    # -- Enviar ------------------------------------------------------

    def test_send_creates_remote_order(self):
        order = self._order()
        wizard_action = order.action_nexo_send()
        wizard = self.env['nexo.b2b.order.send'].browse(wizard_action['res_id'])
        self.assertEqual(wizard.option_ids.filtered(lambda o: o.kind == 'transporte').display_name, 'Reparto propio (+2%)')
        wizard.medio_pago_id = wizard.option_ids.filtered(lambda o: o.kind == 'medio_pago')
        wizard.notas = 'Entregar a la mañana'
        wizard.action_send()
        body = self.fake.bodies[-1]
        self.assertEqual(body['mayorista_id'], 'may_1')
        self.assertCountEqual(body['items'], [{'presentacion_id': 'pmp_c', 'cantidad': 2},
                                              {'presentacion_id': 'pmp_u', 'cantidad': 3}])
        self.assertEqual(body['medio_pago_id'], 'mp_1')
        self.assertNotIn('transporte_id', body)
        self.assertEqual(body['notas'], f'Pedido de compra Odoo {order.name}\nEntregar a la mañana')
        self.assertNotIn('precio_unitario', str(body['items']), "El precio no se manda: lo pone NexoB2B")
        self.assertEqual(order.state, 'sent')
        self.assertEqual(order.nexo_estado, 'cargada')
        self.assertEqual(order.nexo_numero, 'ORD-00001')
        self.assertEqual(order.partner_ref, 'ORD-00001')
        self.assertEqual(order.nexo_medio_pago, 'Transferencia')
        # Odoo toma los precios que fijó NexoB2B.
        self.assertEqual(sorted(order.order_line.mapped('price_unit')), [950.0, 11000.0])
        with self.assertRaises(UserError):
            order.action_nexo_send()

    def test_send_rejects_lines_not_from_nexo(self):
        order = self._order(unidades=0)
        otro = self.env['product.product'].create({'name': 'Producto propio'})
        self.env['purchase.order.line'].create({'order_id': order.id, 'product_id': otro.id, 'product_qty': 1})
        with self.assertRaisesRegex(UserError, 'Producto propio'):
            order.action_nexo_send()
        self.assertFalse(self.fake.bodies)

    def test_send_rejects_fractional_quantity(self):
        order = self._order(unidades=0)
        order.order_line.product_qty = 1.5
        with self.assertRaisesRegex(UserError, 'entero'):
            order.action_nexo_send()

    def test_send_without_alta_shows_nexo_message(self):
        order = self._order(unidades=0)
        order.partner_id.nexo_mayorista_id = 'may_otro'
        with self.assertRaisesRegex(UserError, 'dado de alta'):
            self._send(order)
        self.assertFalse(order.nexo_orden_id)

    def test_send_timeout_then_reconcile(self):
        # Se corta sin respuesta pero la orden se creó: no se duplica.
        order = self._order()
        self.fake.fail_post, self.fake.created_on_fail = 'timeout', True
        result = self._send(order)
        self.assertEqual(result['params']['type'], 'warning')
        self.assertTrue(order.nexo_envio_incierto)
        self.assertFalse(order.nexo_orden_id)
        self.fake.fail_post = None
        order.action_nexo_sync()
        self.assertEqual(order.nexo_numero, 'ORD-00001')
        self.assertFalse(order.nexo_envio_incierto)
        self.assertEqual(len(self.fake.bodies), 1)

    def test_send_timeout_not_created_allows_resend(self):
        order = self._order()
        self.fake.fail_post = 'network'
        self._send(order)
        self.fake.fail_post = None
        # Al reenviar, primero busca; como no está, envía de nuevo.
        self._send(order)
        self.assertEqual(len(self.fake.bodies), 2)
        self.assertEqual(order.nexo_estado, 'cargada')

    # -- Seguimiento -------------------------------------------------

    def test_states_confirm_and_notify(self):
        order = self._order()
        self._send(order)
        self.assertEqual(self._set_estado(order, 'confirmado'), 'cargada')
        self.assertEqual(order.state, 'purchase', "Se confirma cuando confirma el mayorista")
        self.assertIsNone(order._nexo_sync(), "Sin cambios no avisa de nuevo")
        self._set_estado(order, 'listo', cantidad_bultos=3, peso_kg='12.50', costo_transporte='150.00')
        self.assertEqual(order.nexo_peso_kg, 12.5)
        self.assertEqual(order.nexo_recargos, 150.0, "Los importes en texto se suman como números")
        self.assertEqual(order.nexo_total, 13310.0)
        self.assertIn('3 bultos', order.message_ids[0].body)
        self.assertIn(order.user_id.partner_id, order.message_ids[0].partner_ids)
        self.fake.documentos[order.nexo_orden_id] = [
            {'id': 'doc_1', 'nombre': 'Remito 0001', 'tipo': 'remito', 'url': '/uploads/documentos/r1.pdf'}]
        self._set_estado(order, 'en_transporte', numero_guia='AB123')
        self.assertEqual(order.nexo_guia, 'AB123')
        bodies = ' '.join(order.message_ids.mapped('body'))
        self.assertIn('https://api.nexob2b.test/uploads/documentos/r1.pdf', bodies)
        self._set_estado(order, 'entregado')
        self.assertEqual(bodies.count('Remito 0001'), 1)
        self.assertEqual(order.nexo_estado, 'entregado')
        if 'picking_ids' in order._fields and order.picking_ids:
            self.assertTrue(order.activity_ids.filtered(lambda a: 'validá la recepción' in (a.summary or '')))

    def test_returned_order_schedules_activity(self):
        order = self._order(unidades=0)
        self._send(order)
        self._set_estado(order, 'devuelto', mensaje_mayorista='No hay stock de cajas')
        self.assertEqual(order.nexo_mensaje, 'No hay stock de cajas')
        self.assertTrue(order.activity_ids.filtered(lambda a: 'devolvió' in (a.summary or '')))
        self.assertIn(order.state, ('draft', 'sent'))

    def test_remote_cancel_cancels_odoo(self):
        order = self._order(unidades=0)
        self._send(order)
        self._set_estado(order, 'cancelado')
        self.assertEqual(order.state, 'cancel')
        self.assertFalse(any(c[1].endswith('/cancelar') for c in self.fake.calls))

    def test_cancel_from_odoo(self):
        order = self._order(unidades=0)
        self._send(order)
        order.button_cancel()
        self.assertEqual(order.state, 'cancel')
        self.assertEqual(order.nexo_estado, 'cancelado')
        self.assertTrue(any(c[1].endswith('/cancelar') for c in self.fake.calls))

    def test_cancel_blocked_once_processing(self):
        order = self._order(unidades=0)
        self._send(order)
        self._set_estado(order, 'armando')
        with self.assertRaisesRegex(UserError, 'ya está procesando'):
            order.button_cancel()

    def test_cron_syncs_open_orders(self):
        order = self._order(unidades=0)
        self._send(order)
        self.fake.ordenes[order.nexo_orden_id]['estado'] = 'confirmado'
        self.env['purchase.order']._cron_nexo_sync()
        self.assertEqual(order.nexo_estado, 'confirmado')
        # Sin sesión no consulta.
        self.env['nexo.token'].sudo().search([]).unlink()
        self.fake.ordenes[order.nexo_orden_id]['estado'] = 'armando'
        self.env['purchase.order']._cron_nexo_sync()
        self.assertEqual(order.nexo_estado, 'confirmado')

    def test_send_wizard_rereads_prices(self):
        order = self._order(cajas=2, unidades=3)
        caja = order.order_line.filtered(lambda l: l._nexo_seller().nexo_presentacion_id == 'pmp_c')
        wizard = self.env['nexo.b2b.order.send'].browse(order.action_nexo_send()['res_id'])
        self.assertFalse(wizard.precio_cambios, "Sin cambios en NexoB2B no avisa")
        self.assertFalse(wizard.precio_avisos)

        presentaciones = self.fake.producto['mayoristas'][0]['presentaciones']
        presentaciones[1]['precio'] = '12000'  # como la API real, en texto
        wizard = self.env['nexo.b2b.order.send'].browse(order.action_nexo_send()['res_id'])
        self.assertEqual(caja.price_unit, 12000.0, "La línea toma el precio actual de NexoB2B")
        self.assertEqual(caja._nexo_seller().price, 12000.0, "Y la presentación también")
        self.assertIn('Caja x12', wizard.precio_cambios)
        self.assertIn('Precios actualizados desde NexoB2B', order.message_ids[0].body)

        del presentaciones[0]  # la unidad ya no está
        wizard = self.env['nexo.b2b.order.send'].browse(order.action_nexo_send()['res_id'])
        self.assertIn('ya no figura', wizard.precio_avisos)
