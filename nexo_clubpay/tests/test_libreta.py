"""Libreta de ClubPay con ClubPay simulado.

Las respuestas siguen el contrato de NexoPOS con ClubPay
(`nexopos/docs/CLUBPAY-CUENTA-CORRIENTE.md`). `GET /pos/account/payments` es la
ruta que le pedimos a ClubPay: todavía no existe y el fake la contesta con 404
salvo que el test cargue pagos.
"""
import copy

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.nexo_core.lib.client import NexoError

from .test_nexo_clubpay import KEY, ClubPayCase, FakeClubPay


class FakeLibreta(FakeClubPay):

    def __init__(self):
        super().__init__()
        self.customers = {}
        self.movements = {}
        self.app_payments = None  # None = la ruta no existe todavía

    def accept(self, external_id):
        self.customers[external_id]['status'] = 'vinculada'
        self.customers[external_id]['account_id'] = 'acc_77'

    def __call__(self, client, method, path, params=None, json_body=None, auth=True):
        if not (path.startswith('/pos/customers') or path.startswith('/pos/account/')):
            return super().__call__(client, method, path, params=params, json_body=json_body, auth=auth)
        self.calls.append((method, path, copy.deepcopy(json_body), client.auth_header))
        if client.token != KEY:
            raise NexoError('auth', service='ClubPay', status=401)
        if self.fail:
            raise NexoError(self.fail, service='ClubPay')
        if method == 'POST' and path == '/pos/customers':
            if json_body['dni'].endswith('0'):
                return {'encontrado': False, 'status': 'sin_cuenta', 'mensaje': 'El DNI no tiene cuenta en ClubPay'}
            c = self.customers.setdefault(json_body['external_id'], {
                'encontrado': True, 'status': 'propuesta', 'persona': 'Germán Yovan', 'dni': json_body['dni']})
            return {k: v for k, v in c.items() if k != 'dni'}
        if method == 'GET' and path.startswith('/pos/customers/'):
            c = self.customers.get(path.split('/')[3])
            if not c:
                raise NexoError('not_found', service='ClubPay', status=404)
            return {k: v for k, v in c.items() if k != 'dni'}
        if method == 'POST' and path == '/pos/account/movements':
            self.movements[json_body['movement_id']] = json_body
            return {'ok': True}
        if method == 'GET' and path.startswith('/pos/account/payments'):
            if self.app_payments is None:
                raise NexoError('not_found', service='ClubPay', status=404)
            return {'pagos': self.app_payments, 'hasta': '2099-01-01T00:00:00Z', 'hay_mas': False}
        raise AssertionError(f"Ruta no simulada: {method} {path}")


@tagged('post_install', '-at_install')
class TestLibreta(ClubPayCase):
    fake_class = FakeLibreta

    def setUp(self):
        super().setUp()
        self.Charge.set_key(KEY)
        self.Line = self.env['nexo.clubpay.libreta.line']
        self.env['res.config.settings'].create({'nexo_clubpay_libreta': True}).execute()
        self.partner = self.env['res.partner'].create({'name': 'German Yovan', 'nexo_clubpay_dni': '26098535'})
        if self.env.ref('l10n_ar.par_cfa', raise_if_not_found=False):
            # Con la localización argentina la factura pide la condición frente
            # al IVA: consumidor final, como en la caja.
            self.partner.l10n_ar_afip_responsibility_type_id = self.env.ref('l10n_ar.res_CF')
            # En Odoo 20 el DNI va en los identificadores adicionales.
            self.partner.additional_identifiers = {'AR_DNI': '26098535'}
        self.iva = self.env['account.tax'].search([('type_tax_use', '=', 'sale'), ('amount', '=', 21),
                                                   ('company_id', '=', self.env.company.id)], limit=1)
        self.ext = f"ODOO-{self.partner.id}"

    def _invoice(self, amount, post=True, move_type='out_invoice'):
        move = self.env['account.move'].create({
            'move_type': move_type, 'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({'name': 'Mercadería', 'quantity': 1, 'price_unit': amount,
                                                 'tax_ids': [Command.set(self.iva.ids)]})],
        })
        if post:
            move.action_post()
        return move

    def _link(self):
        self.partner.action_nexo_clubpay_invite()
        self.fake.accept(self.ext)
        self.partner.action_nexo_clubpay_check()

    def _posted(self, path):
        return [c for c in self.fake.calls if c[1] == path]

    def test_needs_clubpay_key(self):
        self.env['nexo.token']._clear('clubpay', self.env.company)
        settings = self.env['res.config.settings'].create({'nexo_clubpay_libreta': False})
        settings.execute()
        with self.assertRaises(UserError):
            self.env['res.config.settings'].create({'nexo_clubpay_libreta': True}).execute()

    def test_invitation_sends_dni_once(self):
        self.partner.action_nexo_clubpay_invite()
        self.assertEqual(self.partner.nexo_clubpay_status, 'propuesta')
        self.assertEqual(self.partner.nexo_clubpay_persona, 'Germán Yovan')
        sent = self._posted('/pos/customers')
        self.assertEqual(sent[0][2], {'dni': '26098535', 'external_id': self.ext})
        self.assertEqual(sent[0][3], 'X-API-Key')
        # Volver a tocar consulta sin el DNI.
        self.partner.action_nexo_clubpay_invite()
        self.assertEqual(len(self._posted('/pos/customers')), 1, "El DNI viaja una sola vez")
        self.assertEqual(self.fake.calls[-1][:3], ('GET', f'/pos/customers/{self.ext}', None))

    def test_external_id_is_not_nexopos(self):
        self.partner.action_nexo_clubpay_invite()
        self.assertFalse(self._posted('/pos/customers')[0][2]['external_id'].startswith('CLI-'),
                         "CLI- es el cliente de NexoPOS: un pago iría a la cuenta de otra persona")

    def test_dni_without_clubpay_account(self):
        self.partner.nexo_clubpay_dni = '26098530'
        self.partner.action_nexo_clubpay_invite()
        self.assertEqual(self.partner.nexo_clubpay_status, 'sin_cuenta')

    def test_dni_is_validated(self):
        with self.assertRaises(UserError):
            self.partner.nexo_clubpay_dni = '26.098'

    def test_nothing_is_sent_before_accepting(self):
        self.partner.action_nexo_clubpay_invite()
        self._invoice(1000.0)
        self.Line._cron_libreta()
        self.assertFalse(self.fake.movements, "Hasta que acepta, ClubPay no le muestra nada")

    def test_accepting_sends_balance_then_each_movement(self):
        before = self._invoice(1000.0)
        self.partner.action_nexo_clubpay_invite()
        self.fake.customers[self.ext]['status'] = 'aceptada'  # la palabra de su documentación
        self.Line._cron_libreta()
        self.assertEqual(self.partner.nexo_clubpay_status, 'vinculada')
        opening = [m for m in self.fake.movements.values() if '-saldo-' in m['movement_id']]
        self.assertEqual(len(opening), 1)
        self.assertEqual(opening[0]['kind'], 'ajuste')
        self.assertEqual(opening[0]['amount_cents'], round(before.amount_total * 100))
        self.assertEqual(opening[0]['external_id'], self.ext)
        self.assertEqual(len(self.fake.movements), 1, "La factura vieja va en el saldo, no suelta")

        invoice = self._invoice(500.0)
        line = self.Line.search([('move_id', '=', invoice.id)])
        self.assertEqual(line.state, 'pending', "Entra a la cola al publicar")
        self.Line._cron_libreta()
        self.assertEqual(line.state, 'sent')
        sent = self.fake.movements[line.movement_id]
        self.assertEqual(sent['kind'], 'compra')
        self.assertEqual(sent['amount_cents'], round(invoice.amount_total * 100))

        refund = self._invoice(100.0, move_type='out_refund')
        self.Line._cron_libreta()
        sent = self.fake.movements[self.Line.search([('move_id', '=', refund.id)]).movement_id]
        self.assertEqual(sent['kind'], 'devolucion')
        self.assertLess(sent['amount_cents'], 0, "Baja la deuda: va con signo negativo")

        # El saldo inicial no tiene apunte propio y no por eso está borrado.
        self.Line._cron_libreta()
        self.Line._cron_libreta()
        self.assertFalse([m for m in self.fake.movements if m.endswith('-anulado')])

    def test_network_failure_retries(self):
        self._link()
        invoice = self._invoice(500.0)
        self.fake.fail = 'network'
        self.Line._cron_libreta()
        line = self.Line.search([('move_id', '=', invoice.id)])
        self.assertEqual((line.state, line.attempts), ('pending', 1))
        self.assertGreater(line.next_try, fields.Datetime.now())
        self.fake.fail = None
        line.next_try = fields.Datetime.now()
        self.Line._cron_libreta()
        self.assertEqual(line.state, 'sent')

    def test_reset_to_draft_is_reversed(self):
        self._link()
        invoice = self._invoice(500.0)
        self.Line._cron_libreta()
        line = self.Line.search([('move_id', '=', invoice.id)])
        invoice.button_draft()
        self.Line._cron_libreta()
        self.assertTrue(line.reversed)
        reversal = self.fake.movements[f"{line.movement_id}-anulado"]
        self.assertEqual(reversal['amount_cents'], -round(invoice.amount_total * 100))
        self.Line._cron_libreta()
        self.assertEqual(len([m for m in self.fake.movements if m.endswith('-anulado')]), 1,
                         "Ni la anulación ni el saldo inicial se anulan solos")

    def test_payment_from_app(self):
        self._link()
        old = self._invoice(1000.0)
        new = self._invoice(1000.0)
        self.Line._cron_libreta()
        sent_before = len(self.fake.movements)
        self.fake.app_payments = [
            {'clubpay_payment_id': 'pay_12', 'external_id': self.ext, 'amount_cents': 100000,
             'paid_at': '2099-01-01T00:00:00Z'},
            {'clubpay_payment_id': 'pay_nexopos', 'external_id': 'CLI-12', 'amount_cents': 100000,
             'paid_at': '2099-01-01T00:00:00Z'},
        ]
        self.Line._cron_libreta()
        pay = self.env['nexo.clubpay.libreta.payment'].search([('name', '=', 'pay_12')])
        self.assertEqual(pay.amount, 1000.0)
        self.assertFalse(self.env['nexo.clubpay.libreta.payment'].search([('name', '=', 'pay_nexopos')]),
                         "Los de NexoPOS no son de Odoo")
        self.assertEqual(old.amount_residual, old.amount_total - 1000.0, "Primero la factura más vieja")
        self.assertEqual(new.amount_residual, new.amount_total)
        self.assertEqual(pay.account_payment_ids.journal_id.code, 'CPMP')
        self.assertEqual(len(self.fake.movements), sent_before, "El pago vino de ClubPay: no se le devuelve")
        # Repetido: no registra dos veces.
        self.Line._cron_libreta()
        self.env['nexo.clubpay.libreta.payment']._record_app_payment(self.fake.app_payments[0], self.env.company)
        self.assertEqual(self.env['nexo.clubpay.libreta.payment'].search_count([('name', '=', 'pay_12')]), 1)
        self.assertEqual(old.amount_residual, old.amount_total - 1000.0)

    def test_overpayment_stays_in_favour(self):
        self._link()
        invoice = self._invoice(100.0)
        pay = self.env['nexo.clubpay.libreta.payment']._record_app_payment(
            {'clubpay_payment_id': 'pay_13', 'external_id': self.ext,
             'amount_cents': round((invoice.amount_total + 50) * 100), 'paid_at': '2099-01-01T00:00:00Z'},
            self.env.company)
        self.assertEqual(invoice.amount_residual, 0.0)
        self.assertIn(invoice.payment_state, ('paid', 'in_payment'))
        self.assertAlmostEqual(sum(pay.account_payment_ids.mapped('amount')), invoice.amount_total + 50)

    def test_missing_payments_route_does_not_break(self):
        self._link()
        self._invoice(500.0)
        self.Line._cron_libreta()  # GET /pos/account/payments da 404
        self.assertTrue(self.Line.search([('state', '=', 'sent')]))

    def test_simulator(self):
        self.Charge._set_simulator(True)
        calls = len(self.fake.calls)
        self.partner.action_nexo_clubpay_invite()
        self.assertEqual(self.partner.nexo_clubpay_status, 'propuesta')
        self.partner.action_nexo_clubpay_sim_accept()
        invoice = self._invoice(1000.0)
        action = self.partner.action_nexo_clubpay_sim_payment()
        pay = self.env['nexo.clubpay.libreta.payment'].browse(action['res_id'])
        self.assertTrue(pay.simulated)
        self.assertEqual(invoice.amount_residual, 0.0)
        self.assertEqual(len(self.fake.calls), calls, "El simulador no llama a ClubPay")
