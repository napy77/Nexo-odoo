"""Cobro con Mercado Pago a través de ClubPay.

El comercio conectó su Mercado Pago una sola vez, en ClubPay. Odoo nunca ve
esas credenciales: pide el cobro a ClubPay con la misma clave `pos_…` del
descuento, y ClubPay lo crea a nombre del comercio. La plata va directo del
cliente a la cuenta del comercio. Es lo mismo que hace NexoPOS
(`backend/src/integrations/clubpay-cobros.ts`).

Contrato: `clubapp/docs/respuesta-pedido-cobros-mercadopago.md` y
`clubapp/docs/API_CONTRACTS.md` («Cobros con Mercado Pago»). Importes en
centavos.

- GET  /pos/payments/status → {mercadopago, enabled_for_pos, mostrador_listo}
- POST /pos/payments {amount_cents, external_reference, channel, description?,
       return_url?} → el cobro. 201 nuevo, 200 si ya existía.
- GET  /pos/payments/:id → el cobro.
- POST /pos/payments/:id/cancel → cancelled; 409 con el cobro en `paid` si se
       pagó.
- POST /pos/payments/:id/refunds {amount_cents?, external_reference} → el cobro.

Lo que no se negocia, porque es plata del cliente:
- `paid` nunca retrocede.
- `rejected` no es final: con el mismo QR se puede probar otra tarjeta.
- Un `cancelled` o un `expired` puede terminar en `paid`. El aviso de ClubPay va
  a NexoPOS, no a Odoo, así que Odoo pregunta: cada 2 s mientras el QR está en
  pantalla, y con un cron después.
"""
import base64
import logging
from datetime import timedelta, timezone
from uuid import uuid4

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.nexo_core.lib.client import NexoError

from .nexo_clubpay_charge import _parse_dt, centavos, pesos

_logger = logging.getLogger(__name__)

STATES = [
    ('pending', 'Esperando el pago'),
    ('paid', 'Pagado'),
    ('rejected', 'Rechazado'),
    ('expired', 'Vencido'),
    ('cancelled', 'Cancelado'),
    ('refunded', 'Devuelto'),
]
# Vigencia que fija ClubPay; el simulador la imita.
MINUTOS = {'mostrador': 5, 'tienda': 30}
SIM_SEGUNDOS = 8
# Un cobro pagado que no quedó en ninguna venta pasado este tiempo es plata
# del cliente sin venta: se avisa para devolverlo.
MINUTOS_SIN_VENTA = 30


class NexoClubpayPayment(models.Model):
    _name = 'nexo.clubpay.payment'
    _description = 'Cobro con Mercado Pago vía ClubPay'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char('Cobro', required=True, readonly=True, index=True, help="payment_id de ClubPay")
    company_id = fields.Many2one('res.company', required=True, readonly=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    origin = fields.Selection([('pos', 'Punto de Venta'), ('sale', 'Ventas')], required=True, readonly=True)
    channel = fields.Selection([('mostrador', 'QR de mostrador'), ('tienda', 'Link de pago')],
                               required=True, readonly=True)
    external_reference = fields.Char(required=True, readonly=True, index=True)
    amount = fields.Monetary('Importe', readonly=True)
    fee = fields.Monetary('Comisión de ClubPay', readonly=True)
    refunded = fields.Monetary('Devuelto', readonly=True)
    state = fields.Selection(STATES, default='pending', required=True, readonly=True, tracking=True)
    paid_at = fields.Datetime('Pagado el', readonly=True)
    mp_payment_id = fields.Char('Pago en Mercado Pago', readonly=True)
    qr_data = fields.Char('Contenido del QR', readonly=True)
    checkout_url = fields.Char('Link de pago', readonly=True)
    expires_at = fields.Datetime('Vence', readonly=True)
    error = fields.Char(readonly=True)
    simulated = fields.Boolean('Simulador', readonly=True)
    sim_return_url = fields.Char(readonly=True, help="Simulador: adónde vuelve el link de pago")
    pos_order_uuid = fields.Char(readonly=True, help="Orden de la caja que pidió el cobro")
    pos_payment_id = fields.Many2one('pos.payment', readonly=True, ondelete='set null')
    transaction_id = fields.Many2one('payment.transaction', 'Transacción', readonly=True, ondelete='set null',
                                     index='btree_not_null')
    sale_order_id = fields.Many2one('sale.order', readonly=True, ondelete='set null')
    has_sale = fields.Boolean('Quedó en una venta', compute='_compute_has_sale')
    to_refund = fields.Monetary('Por devolver', compute='_compute_has_sale')

    @api.depends('pos_payment_id', 'transaction_id.state', 'state', 'amount', 'refunded')
    def _compute_has_sale(self):
        for payment in self:
            payment.has_sale = bool(payment.pos_payment_id or payment.transaction_id.state == 'done')
            payment.to_refund = payment.amount - payment.refunded if payment.state in ('paid', 'refunded') else 0.0

    # ------------------------------------------------------------------
    # Conexión: la de ClubPay, con su clave
    # ------------------------------------------------------------------

    @api.model
    def _charges(self):
        return self.env['nexo.clubpay.charge']

    @api.model
    def _simulator(self, company=None):
        return self._charges()._simulator(company)

    @api.model
    def _mp_enabled(self, company=None):
        """El tilde «Cobrar con Mercado Pago a través de ClubPay» de Ajustes."""
        company = company or self.env.company
        return self.env['ir.config_parameter'].sudo().get_str(f'nexo_clubpay.mp.{company.id}') == '1'

    @api.model
    def _set_mp_enabled(self, enabled, company=None):
        company = company or self.env.company
        self.env['ir.config_parameter'].sudo().set_str(f'nexo_clubpay.mp.{company.id}', '1' if enabled else '0')

    @api.model
    def _call(self, method, path, json_body=None, company=None):
        return self._charges()._call(method, path, json_body=json_body, company=company)

    @api.model
    def _message(self, error):
        """ClubPay manda el texto en `error` y `message`, también con 502
        («Mercado Pago no contestó…»): ese va tal cual."""
        data = error.data if isinstance(error.data, dict) else {}
        text = next((data[k] for k in ('message', 'error') if isinstance(data.get(k), str)), None)
        if error.kind == 'auth':
            return self._charges()._message(error)
        return text or self._charges()._message(error)

    @api.model
    def _status(self, company=None):
        """Si el comercio puede cobrar: Mercado Pago conectado en ClubPay, el
        interruptor «Cobrar también desde NexoPOS y NexoTienda» prendido y, para
        el QR de mostrador, la dirección del local cargada."""
        company = company or self.env.company
        if self._simulator(company):
            return {'mercadopago': 'connected', 'enabled_for_pos': True, 'mostrador_listo': True}
        try:
            return self._call('GET', '/pos/payments/status', company=company)
        except NexoError as e:
            raise UserError(self._message(e)) from e

    @api.model
    def _check_ready(self, channel, company=None):
        status = self._status(company)
        if status.get('mercadopago') != 'connected':
            raise UserError(_("El comercio no tiene Mercado Pago conectado en ClubPay. Conectalo en ClubPay › Cobros."))
        if not status.get('enabled_for_pos'):
            raise UserError(_("Prendé «Cobrar también desde NexoPOS y NexoTienda» en ClubPay › Cobros."))
        if channel == 'mostrador' and not status.get('mostrador_listo'):
            raise UserError(_("Para cobrar con QR en el mostrador falta cargar la dirección del local en "
                              "ClubPay › Cobros."))
        return status

    # ------------------------------------------------------------------
    # Cobro
    # ------------------------------------------------------------------

    @api.model
    def _reference(self, origin, suffix=None):
        db = self.env['ir.config_parameter'].sudo().get_str('database.uuid') or ''
        return f"odoo-{db[:8]}-{origin}-{suffix or uuid4().hex[:16]}"

    @api.model
    def _create_payment(self, amount, channel, origin, company=None, reference=None, description=None,
                        return_url=None, **links):
        """Pide el cobro a ClubPay. `links`: pos_order_uuid, transaction_id,
        sale_order_id. Con la misma referencia ClubPay devuelve el mismo cobro."""
        company = company or self.env.company
        if amount <= 0:
            raise UserError(_("El importe tiene que ser mayor que cero para cobrar con Mercado Pago."))
        reference = reference or self._reference(origin)
        existing = self.sudo().search([('external_reference', '=', reference), ('company_id', '=', company.id)],
                                      limit=1)
        if existing:
            return existing
        vals = {'company_id': company.id, 'origin': origin, 'channel': channel, 'external_reference': reference,
                'amount': amount, **{k: v for k, v in links.items() if v}}
        if self._simulator(company):
            payment_id = f"pay_sim_{uuid4().hex[:12]}"
            vals.update(name=payment_id, simulated=True,
                        expires_at=fields.Datetime.now() + timedelta(minutes=MINUTOS[channel]))
            if channel == 'mostrador':
                vals['qr_data'] = f"SIMULADOR-CLUBPAY-MP-{payment_id}"
            else:
                # Relativo: el simulador corre en el mismo Odoo que lo pidió.
                vals['checkout_url'] = f"/nexo_clubpay/simulador/pago/{payment_id}"
                vals['sim_return_url'] = return_url or False
            return self.sudo().create(vals)
        body = {'amount_cents': centavos(amount), 'external_reference': reference, 'channel': channel}
        if description:
            body['description'] = description[:200]
        if return_url:
            body['return_url'] = return_url
        try:
            data = self._call('POST', '/pos/payments', body, company=company)
        except NexoError as e:
            if e.status == 409 and isinstance(e.data, dict) and e.data.get('payment_id'):
                raise UserError(_("ClubPay ya tiene otro cobro con esa referencia y otro importe. Generá uno nuevo.")) \
                    from e
            raise UserError(self._message(e)) from e
        if not data.get('payment_id'):
            raise UserError(NexoError('invalid_response', service='ClubPay').user_message())
        if channel == 'mostrador' and not data.get('qr_data') or channel == 'tienda' and not data.get('checkout_url'):
            raise UserError(data.get('error') or _("ClubPay no devolvió el QR ni el link de pago."))
        payment = self.sudo().create(dict(vals, name=data['payment_id']))
        payment._apply(data)
        return payment

    def _refresh(self):
        """Relee el cobro en ClubPay. Se relee también un vencido, cancelado o
        rechazado: todavía puede terminar pagado."""
        for payment in self:
            if payment.simulated:
                payment._simulate()
                continue
            try:
                data = self._call('GET', f'/pos/payments/{payment.name}', company=payment.company_id)
            except NexoError as e:
                payment.error = self._message(e)
                continue
            payment._apply(data)
        return True

    def _apply(self, data):
        self.ensure_one()
        status = data.get('status') or 'pending'
        if status not in dict(STATES):
            status = 'pending'
        if self.state in ('paid', 'refunded') and status not in ('paid', 'refunded'):
            # Un pago acreditado no vuelve atrás, aunque llegue un estado viejo.
            status = self.state
        vals = {
            'state': status,
            'error': data.get('error') or False,
        }
        for key, field in (('amount_cents', 'amount'), ('fee_cents', 'fee'), ('refunded_cents', 'refunded')):
            if data.get(key) is not None:
                vals[field] = pesos(data[key])
        for key in ('qr_data', 'checkout_url', 'mp_payment_id'):
            if data.get(key):
                vals[key] = str(data[key])
        for key in ('paid_at', 'expires_at'):
            if data.get(key):
                vals[key] = _parse_dt(data[key])
        changed = vals['state'] != self.state
        self.write(vals)
        if changed and self.transaction_id:
            self.transaction_id._clubpay_record(self)

    def _simulate(self):
        """Mostrador: se paga solo a los 8 segundos, como el simulador de
        NexoPOS. Link de pago: lo decide la página del simulador."""
        self.ensure_one()
        now = fields.Datetime.now()
        if self.state != 'pending':
            return
        if self.expires_at and now > self.expires_at:
            self._apply({'status': 'expired'})
        elif self.channel == 'mostrador':
            wait = int(self.env['ir.config_parameter'].sudo().get_str('nexo_clubpay.simulador_segundos')
                       or SIM_SEGUNDOS)
            if wait <= 0 or (now - self.create_date).total_seconds() >= wait:
                self._sim_pay()

    def _sim_pay(self):
        self._apply({'status': 'paid', 'paid_at': fields.Datetime.now().replace(tzinfo=timezone.utc).isoformat(),
                     'mp_payment_id': f"sim-{self.id}", 'fee_cents': 0})

    def _cancel(self):
        """Cancela antes de cobrar con otro medio, como pide ClubPay: si el QR
        quedara vivo el cliente podría pagar dos veces. Si ClubPay contesta que
        ya se pagó, el cobro queda pagado y la venta se cierra con ese pago."""
        for payment in self.filtered(lambda p: p.state in ('pending', 'rejected', 'expired')):
            if payment.simulated:
                payment._apply({'status': 'cancelled'})
                continue
            try:
                payment._apply(self._call('POST', f'/pos/payments/{payment.name}/cancel', {},
                                          company=payment.company_id))
            except NexoError as e:
                if e.status == 409 and isinstance(e.data, dict) and e.data.get('status'):
                    payment._apply(e.data)
                else:
                    raise UserError(self._message(e)) from e
        return True

    def _refund(self, amount=None, reference=None):
        """Devuelve al que pagó. Sin importe, todo lo que falta devolver."""
        self.ensure_one()
        if self.state not in ('paid', 'refunded'):
            raise UserError(_("Solo se puede devolver un cobro pagado."))
        amount = amount or self.amount - self.refunded
        reference = reference or f"{self.external_reference}-dev-{uuid4().hex[:8]}"
        if self.simulated:
            refunded = self.refunded + amount
            if self.currency_id.compare_amounts(refunded, self.amount) > 0:
                raise UserError(_("Se pueden devolver hasta %s.", self.amount - self.refunded))
            self._apply({'status': 'refunded', 'refunded_cents': centavos(refunded)})
            return True
        try:
            self._apply(self._call('POST', f'/pos/payments/{self.name}/refunds', {
                'amount_cents': centavos(amount), 'external_reference': reference[:120]}, company=self.company_id))
        except NexoError as e:
            raise UserError(self._message(e)) from e
        return True

    def action_refresh(self):
        self.sudo()._refresh()
        return True

    def action_refund(self):
        for payment in self:
            payment.sudo()._refund()
            payment.message_post(body=_("Devuelto al cliente por Mercado Pago: %s.", payment.refunded))
        return True

    def _qr_image(self, size=300):
        self.ensure_one()
        png = self.env['ir.actions.report'].barcode('QR', self.qr_data, width=size, height=size, quiet=1)
        return 'data:image/png;base64,' + base64.b64encode(png).decode()

    def _to_ui(self):
        self.ensure_one()
        return {
            'payment_id': self.name,
            'state': self.state,
            'qr_image': self._qr_image() if self.state in ('pending', 'rejected') and self.qr_data else False,
            'expires_at': self.expires_at and self.expires_at.replace(tzinfo=timezone.utc).isoformat(),
            'amount': self.amount,
            'mp_payment_id': self.mp_payment_id,
            'error': self.error,
            'simulated': self.simulated,
        }

    @api.model
    def _find(self, payment_id, company=None):
        company = company or self.env.company
        payment = self.sudo().search([('name', '=', payment_id), ('company_id', '=', company.id)], limit=1)
        if not payment:
            raise UserError(_("No se encontró el cobro de Mercado Pago %s.", payment_id))
        return payment

    # ------------------------------------------------------------------
    # Cron: lo que se paga cuando nadie está mirando
    # ------------------------------------------------------------------

    @api.model
    def _cron_check(self):
        """Relee los cobros de las últimas 48 horas que todavía pueden cambiar.
        Un pago acreditado que no quedó en ninguna venta se avisa para
        devolverlo: no se devuelve solo, porque la venta de la caja puede
        llegar más tarde (sin conexión)."""
        since = fields.Datetime.now() - timedelta(hours=48)
        payments = self.sudo().search([('create_date', '>=', since),
                                       ('state', 'in', ('pending', 'rejected', 'expired', 'cancelled'))])
        for payment in payments:
            try:
                with self.env.cr.savepoint():
                    payment._refresh()
            except Exception:  # noqa: BLE001 — un cobro no frena a los demás
                _logger.exception("ClubPay: no se pudo releer el cobro %s", payment.name)
        late = fields.Datetime.now() - timedelta(minutes=MINUTOS_SIN_VENTA)
        orphans = self.sudo().search([('create_date', '>=', since), ('create_date', '<=', late),
                                      ('state', '=', 'paid')]).filtered(lambda p: not p.has_sale)
        activity_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
        for payment in orphans:
            if payment.activity_ids:
                continue
            user = self.env['res.users'].sudo().search([
                ('all_group_ids', 'in', self.env.ref('base.group_system').id),
                ('company_ids', 'in', payment.company_id.id), ('share', '=', False)], limit=1)
            payment.activity_schedule(
                activity_type_id=activity_type.id if activity_type else False,
                summary=_("Pago de Mercado Pago sin venta"),
                note=_("El cliente pagó %s con Mercado Pago y no quedó en ninguna venta. Revisalo y, si "
                       "corresponde, tocá «Devolver».", payment.amount),
                user_id=user.id or self.env.uid,
            )
