"""ClubPay simulado: se reemplaza NexoClient.request, como en nexo_pos.

La forma de las respuestas sale del contrato de ClubPay
(`clubapp/docs/nexopos-cobro-qr.md`): 20 % con tope de $8.000 sobre $50.000
da $8.000 de descuento con `recorte: por_compra`.
"""
import copy
from unittest.mock import patch
from uuid import uuid4

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.nexo_core.lib.client import NexoClient, NexoError

KEY = 'pos_test_no_es_real'


class FakeClubPay:

    def __init__(self):
        self.charges = {}
        self.calls = []
        self.fail = None

    def scan(self, charge_id, discount_cents=800000, recorte='por_compra'):
        """El socio escaneó y confirmó en su teléfono."""
        ch = self.charges[charge_id]
        ch.update(status='applied', transaction_id=812, discount_cents=discount_cents,
                  neto_cents=ch['ticket_total_cents'] - discount_cents, recorte=recorte,
                  member={'membership_id': 3, 'name': 'Juan Pérez', 'member_number': 'CU-00133',
                          'club_id': 1, 'club_name': 'Club Unión Morrison'},
                  offer={'id': 4, 'description': '20% con tope de $8.000', 'discount_percent': 20})

    def __call__(self, client, method, path, params=None, json_body=None, auth=True):
        self.calls.append((method, path, copy.deepcopy(json_body), client.auth_header))
        if client.token != KEY:
            raise NexoError('auth', service='ClubPay', status=401, detail='API key inválida')
        if self.fail:
            raise NexoError(self.fail, service='ClubPay')
        if method == 'POST' and path == '/pos/charges':
            for ch in self.charges.values():
                if ch['external_reference'] == json_body['external_reference']:
                    return copy.deepcopy(ch)
            cid = f"chg_{uuid4().hex[:10]}"
            self.charges[cid] = {
                'charge_id': cid, 'qr_payload': f'https://api.clubpay.com.ar/c/{cid}',
                'expires_at': '2099-01-01T00:03:00.000Z', 'status': 'pending',
                'ticket_total_cents': json_body['ticket_total_cents'],
                'external_reference': json_body['external_reference'],
            }
            return copy.deepcopy(self.charges[cid])
        if path.startswith('/pos/charges/'):
            cid = path.split('/')[3]
            if cid not in self.charges:
                raise NexoError('not_found', service='ClubPay', status=404)
            ch = self.charges[cid]
            if path.endswith('/cancel'):
                if ch['status'] != 'pending':
                    raise NexoError('client', service='ClubPay', status=409, detail='El cobro ya está resuelto')
                ch['status'] = 'cancelled'
            return copy.deepcopy(ch)
        raise AssertionError(f"Ruta no simulada: {method} {path}")


class ClubPayCase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.fake = FakeClubPay()
        patcher = patch.object(NexoClient, 'request', autospec=True, side_effect=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.Charge = self.env['nexo.clubpay.charge']
        # La base de prueba puede tener el simulador prendido para mirar la caja.
        self.Charge._set_simulator(False)


@tagged('post_install', '-at_install')
class TestClubPay(ClubPayCase):

    def test_key_is_tested_before_storing(self):
        with self.assertRaises(UserError):
            self.Charge.set_key('pos_equivocada')
        self.assertFalse(self.env['nexo.token']._get('clubpay'))
        self.Charge.set_key(f'  {KEY}  ')
        self.assertEqual(self.env['nexo.token']._valid_token('clubpay'), KEY)
        self.assertEqual(self.fake.calls[-1][3], 'X-API-Key', "ClubPay pide la clave en X-API-Key")
        self.assertNotIn(KEY, str(self.env['nexo.log'].search([]).read()), "La clave no va al registro")

    def test_discount_comes_from_clubpay(self):
        self.Charge.set_key(KEY)
        charge = self.Charge._create_charge(50000.0, 'pos')
        self.assertEqual(self.fake.calls[-1][2]['ticket_total_cents'], 5000000, "En centavos")
        self.assertEqual(charge.state, 'pending')
        self.assertEqual(charge.qr_payload, f'https://api.clubpay.com.ar/c/{charge.name}', "El QR va tal cual")
        self.assertTrue(charge._qr_image().startswith('data:image/png;base64,'))
        charge._refresh()
        self.assertEqual(charge.state, 'pending')
        self.fake.scan(charge.name)
        charge._refresh()
        self.assertEqual(charge.state, 'applied')
        # El 20 % daría $10.000: el tope lo dice ClubPay, Odoo no calcula.
        self.assertEqual(charge.discount, 8000.0)
        self.assertEqual(charge.neto, 42000.0)
        self.assertEqual(charge.member_name, 'Juan Pérez')
        self.assertEqual(charge.recorte_texto, "Se alcanzó el tope por compra")

    def test_each_attempt_is_a_new_charge(self):
        self.Charge.set_key(KEY)
        a = self.Charge._create_charge(1000.0, 'pos')
        b = self.Charge._create_charge(1000.0, 'pos')
        self.assertNotEqual(a.external_reference, b.external_reference)
        self.assertNotEqual(a.name, b.name)

    def test_cancel_after_scan_keeps_clubpay_state(self):
        self.Charge.set_key(KEY)
        charge = self.Charge._create_charge(1000.0, 'pos')
        self.fake.scan(charge.name, discount_cents=20000, recorte='')
        charge._cancel()  # ClubPay contesta 409: manda lo que diga ClubPay
        self.assertEqual(charge.state, 'applied')
        self.assertEqual(charge.discount, 200.0)

    def test_clubpay_message_is_shown_as_is(self):
        self.Charge.set_key(KEY)
        self.fake.fail = 'timeout'
        with self.assertRaises(UserError) as err:
            self.Charge._create_charge(1000.0, 'pos')
        self.assertIn('ClubPay', str(err.exception))

    def test_simulator_does_not_call_clubpay(self):
        self.Charge._set_simulator(True)
        self.env['ir.config_parameter'].sudo().set_str('nexo_clubpay.simulador_segundos', '0')
        charge = self.Charge._create_charge(50000.0, 'pos')
        charge._refresh()
        self.assertFalse(self.fake.calls)
        self.assertTrue(charge.simulated)
        self.assertEqual(charge.state, 'applied')
        self.assertEqual(charge.discount, 8000.0)


@tagged('post_install', '-at_install')
class TestClubPayPos(ClubPayCase):

    def setUp(self):
        super().setUp()
        self.Charge.set_key(KEY)
        self.config = self.env['pos.config'].create({'name': 'Caja ClubPay prueba'})
        self.env['res.config.settings'].create({}).action_nexo_clubpay_setup()
        self.method = self.env['pos.payment.method'].search([
            ('company_id', '=', self.env.company.id), ('payment_provider', '=', 'clubpay')])
        self.config.open_ui()
        self.product = self.env['product.product'].create({
            'name': 'Vino de prueba', 'list_price': 50000.0, 'taxes_id': [Command.clear()],
            'available_in_pos': True})

    def _order(self):
        uid = str(uuid4())
        self.env['pos.order'].sync_from_ui([{
            'uuid': uid, 'session_id': self.config.current_session_id.id, 'state': 'draft',
            'date_order': fields.Datetime.to_string(fields.Datetime.now()),
            'amount_tax': 0.0, 'amount_total': 50000.0, 'amount_paid': 0.0, 'amount_return': 0.0,
            'lines': [Command.create({
                'product_id': self.product.id, 'qty': 1, 'price_unit': 50000.0, 'discount': 0,
                'tax_ids': [], 'price_subtotal': 50000.0, 'price_subtotal_incl': 50000.0,
                'uuid': str(uuid4())})],
        }])
        return self.env['pos.order'].search([('uuid', '=', uid)])

    def test_setup_adds_coupon_to_closed_pos(self):
        self.assertEqual(self.method.payment_method_type, 'terminal')
        self.assertIn(self.method, self.config.payment_method_ids)
        journal = self.env['res.config.settings']._clubpay_journal(self.env.company)
        self.assertEqual(self.method.journal_id, journal)
        self.env['res.config.settings'].create({}).action_nexo_clubpay_setup()
        self.assertEqual(len(self.env['pos.payment.method'].search([('payment_provider', '=', 'clubpay')])), 1,
                         "Tocar de nuevo no duplica")

    def test_pos_flow(self):
        data = self.method.clubpay_create({'amount': 50000.0, 'order_uuid': 'x'})
        self.assertEqual(data['state'], 'pending')
        self.assertTrue(data['qr_image'])
        self.fake.scan(data['charge_id'])
        data = self.method.clubpay_status(data['charge_id'])
        self.assertEqual((data['state'], data['discount'], data['member_number']), ('applied', 8000.0, 'CU-00133'))

        order = self._order()
        with self.assertRaises(UserError, msg="La caja no puede poner otro importe"):
            self.env['pos.payment'].create({'pos_order_id': order.id, 'payment_method_id': self.method.id,
                                            'amount': 10000.0, 'transaction_id': data['charge_id']})
        payment = self.env['pos.payment'].create({'pos_order_id': order.id, 'payment_method_id': self.method.id,
                                                  'amount': 8000.0, 'transaction_id': data['charge_id']})
        charge = self.Charge._find(data['charge_id'])
        self.assertEqual(charge.pos_payment_id, payment)
        self.assertEqual(payment.cardholder_name, 'Juan Pérez')
        with self.assertRaises(UserError, msg="Un cupón se usa una sola vez"):
            self.env['pos.payment'].create({'pos_order_id': self._order().id, 'payment_method_id': self.method.id,
                                            'amount': 8000.0, 'transaction_id': data['charge_id']})

    def test_unconfirmed_coupon_is_refused(self):
        data = self.method.clubpay_create({'amount': 50000.0})
        with self.assertRaises(UserError):
            self.env['pos.payment'].create({'pos_order_id': self._order().id, 'payment_method_id': self.method.id,
                                            'amount': 8000.0, 'transaction_id': data['charge_id']})

    def test_cancel_from_pos(self):
        data = self.method.clubpay_create({'amount': 50000.0})
        self.assertEqual(self.method.clubpay_cancel(data['charge_id'])['state'], 'cancelled')


@tagged('post_install', '-at_install')
class TestClubPaySale(ClubPayCase):

    def setUp(self):
        super().setUp()
        self.Charge.set_key(KEY)
        self.env['res.config.settings'].create({}).action_nexo_clubpay_setup()
        # Con la localización argentina la factura necesita la condición frente
        # al IVA del cliente: se usa el consumidor final, como en la caja.
        self.partner = self.env.ref('l10n_ar.par_cfa', raise_if_not_found=False) or \
            self.env['res.partner'].create({'name': 'Cliente ClubPay'})
        # La localización argentina pide un IVA por línea al facturar.
        iva = self.env['account.tax'].search([('type_tax_use', '=', 'sale'), ('amount', '=', 21),
                                              ('company_id', '=', self.env.company.id)], limit=1)
        product = self.env['product.product'].create({
            'name': 'Servicio de prueba', 'type': 'service', 'list_price': 50000.0,
            'taxes_id': [Command.set(iva.ids)], 'invoice_policy': 'order'})
        self.order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [Command.create({'product_id': product.id, 'product_uom_qty': 1, 'price_unit': 50000.0,
                                           'tax_ids': [Command.set(iva.ids)]})],
        })
        self.total = self.order.amount_total

    def test_discount_goes_to_invoice_as_coupon(self):
        self.assertTrue(self.order.clubpay_available)
        action = self.order.action_clubpay_qr()
        wizard = self.env['nexo.clubpay.sale.qr'].browse(action['res_id'])
        self.assertTrue(wizard.qr_image)
        self.assertEqual(self.order.action_clubpay_qr()['res_id'] and self.order.clubpay_charge_ids.filtered(
            lambda c: c.state == 'pending'), wizard.charge_id, "Volver a tocar muestra el mismo QR")
        self.fake.scan(wizard.charge_id.name)
        wizard.action_refresh()
        self.assertEqual(self.order.clubpay_discount, 8000.0)
        self.assertEqual(self.order.clubpay_neto, self.total - 8000.0)
        self.assertFalse(self.order.clubpay_total_changed)
        with self.assertRaises(UserError):
            self.order._clubpay_start()

        self.order.action_confirm()
        invoice = self.order._create_invoices()
        invoice.action_post()
        self.assertEqual(invoice.amount_total, self.total, "La venta sigue siendo por el total")
        self.assertEqual(invoice.amount_residual, self.total - 8000.0, "El cupón ya pagó su parte")
        self.assertTrue(wizard.charge_id.invoice_payment_id)

    def test_expired_qr_gives_another(self):
        action = self.order.action_clubpay_qr()
        wizard = self.env['nexo.clubpay.sale.qr'].browse(action['res_id'])
        first = wizard.charge_id
        self.fake.charges[first.name]['status'] = 'expired'
        wizard.action_refresh()
        self.assertEqual(first.state, 'expired')
        wizard.action_new()
        self.assertNotEqual(wizard.charge_id, first)
        self.assertEqual(wizard.charge_id.state, 'pending')

    def test_new_qr_cancels_the_pending_one(self):
        first = self.order._clubpay_start()
        second = self.order._clubpay_start()
        self.assertEqual(first.state, 'cancelled')
        self.assertEqual(second.state, 'pending')
