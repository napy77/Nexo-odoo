"""Cobro con QR de ClubPay.

Contrato: `clubapp/docs/nexopos-cobro-qr.md` del repo de ClubPay (lo que el
servidor hace hoy), el mismo que usa NexoPOS (`backend/src/integrations/
clubpay.ts`). Base `https://api.clubpay.com.ar`, clave del comercio en
`X-API-Key`. Importes en centavos enteros.

- POST /pos/charges {ticket_total_cents, external_reference}
      → 201 {charge_id, qr_payload, expires_at, status}
      Idempotente por external_reference.
- GET  /pos/charges/:id → status pending | applied | rejected | expired |
      cancelled; con applied vienen member, offer, transaction_id,
      discount_cents, neto_cents, recorte.
- POST /pos/charges/:id/cancel → cancelled; 409 si ya estaba resuelto.

Reglas que no se negocian (las mismas que NexoPOS):
- **ClubPay decide el descuento.** Odoo no calcula ningún porcentaje: los topes
  del acuerdo viven allá y cambian sin que Odoo se entere.
- **El QR es `qr_payload` tal cual.** Es una URL https para que quien no tiene
  la app caiga en una página que le explica qué es.
- **El cobro vive 3 minutos.** Por eso en Ventas no va el QR en el mail: el
  cliente lo genera desde el portal cuando lo va a usar (DEC-023).
- **El descuento no achica la venta:** entra como forma de pago «Cupón ClubPay»
  (DEC-023, igual que NexoPOS).
"""
import base64
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.nexo_core.lib.client import NexoClient, NexoError

_logger = logging.getLogger(__name__)

SERVICE = 'clubpay'
LABEL = 'ClubPay'
DEFAULT_URL = 'https://api.clubpay.com.ar'
STATES = [
    ('pending', 'Esperando que escanee'),
    ('applied', 'Descuento aplicado'),
    ('rejected', 'Rechazado por el socio'),
    ('expired', 'Vencido'),
    ('cancelled', 'Cancelado'),
]
FINAL = ('applied', 'rejected', 'expired', 'cancelled')
RECORTE = {
    'base': "Se aplicó sobre el monto máximo que cubre el beneficio",
    'por_compra': "Se alcanzó el tope por compra",
    'mensual': "Se alcanzó el tope mensual del socio",
}
# Simulador: los mismos tiempos y el mismo beneficio que el de NexoPOS
# (20 % con tope de $8.000), para probar sin la app.
SIM_SEGUNDOS = 6
SIM_MINUTOS = 3


def centavos(value):
    return int(round((value or 0.0) * 100))


def pesos(value):
    return (value or 0) / 100.0


def _parse_dt(value):
    if not value:
        return False
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class NexoClubpayCharge(models.Model):
    _name = 'nexo.clubpay.charge'
    _description = 'Cobro con QR de ClubPay'
    _order = 'id desc'

    name = fields.Char('Cobro', required=True, readonly=True, index=True,
                       help="charge_id de ClubPay")
    company_id = fields.Many2one('res.company', required=True, readonly=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    origin = fields.Selection([('pos', 'Punto de Venta'), ('sale', 'Ventas')], required=True, readonly=True)
    sale_order_id = fields.Many2one('sale.order', readonly=True, index='btree_not_null', ondelete='set null')
    pos_order_uuid = fields.Char(readonly=True, help="Orden de la caja que pidió el cobro")
    pos_payment_id = fields.Many2one('pos.payment', readonly=True, ondelete='set null')
    invoice_payment_id = fields.Many2one('account.payment', 'Pago del cupón', readonly=True, ondelete='set null')
    external_reference = fields.Char(required=True, readonly=True)
    ticket_total = fields.Monetary('Total de la compra', readonly=True)
    qr_payload = fields.Char('Contenido del QR', readonly=True)
    expires_at = fields.Datetime('Vence', readonly=True)
    state = fields.Selection(STATES, default='pending', required=True, readonly=True)
    member_name = fields.Char('Socio', readonly=True)
    member_number = fields.Char('N.º de socio', readonly=True)
    club_name = fields.Char('Club', readonly=True)
    offer_description = fields.Char('Beneficio', readonly=True)
    discount_percent = fields.Float('% del beneficio', readonly=True)
    discount = fields.Monetary('Descuento', readonly=True)
    neto = fields.Monetary('A pagar', readonly=True)
    recorte = fields.Char(readonly=True)
    recorte_texto = fields.Char('Tope', compute='_compute_recorte_texto')
    transaction_id = fields.Char('Transacción en ClubPay', readonly=True)
    error = fields.Char(readonly=True)
    simulated = fields.Boolean('Simulador', readonly=True)

    @api.depends('recorte')
    def _compute_recorte_texto(self):
        for charge in self:
            charge.recorte_texto = RECORTE.get(charge.recorte or '', '')

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------

    @api.model
    def _url(self):
        return self.env['ir.config_parameter'].sudo().get_str('nexo_clubpay.url') or DEFAULT_URL

    @api.model
    def _simulator(self, company=None):
        """Modo de prueba: no habla con ClubPay y el cobro se aplica solo a los
        pocos segundos, como en NexoPOS. Lo prende el administrador a mano y la
        caja y el portal lo dicen en letras grandes."""
        company = company or self.env.company
        return self.env['ir.config_parameter'].sudo().get_str(f'nexo_clubpay.simulador.{company.id}') == '1'

    @api.model
    def _set_simulator(self, enabled, company=None):
        company = company or self.env.company
        self.env['ir.config_parameter'].sudo().set_str(f'nexo_clubpay.simulador.{company.id}', '1' if enabled else '0')

    @api.model
    def _key(self, company=None):
        return self.env['nexo.token']._valid_token(SERVICE, company)

    @api.model
    def _enabled(self, company=None):
        return bool(self._simulator(company) or self._key(company))

    @api.model
    def _connector(self):
        module = self.env['ir.module.module'].sudo().search([('name', '=', 'nexo_clubpay')], limit=1)
        return f"odoo/{module.latest_version or '20.0'}"

    @api.model
    def _client(self, key):
        return NexoClient(self._url(), token=key, auth_header='X-API-Key', connector=self._connector(),
                          service=LABEL, on_call=self.env['nexo.log']._on_call(SERVICE))

    @api.model
    def _call(self, method, path, json_body=None, company=None, key=None):
        key = key or self._key(company)
        if not key:
            raise NexoError('auth', service=LABEL,
                            message=_("No hay una clave de ClubPay cargada. Cargala en Ajustes → ClubPay."))
        return self._client(key).request(method, path, json_body=json_body)

    @api.model
    def _message(self, error):
        """Los mensajes de ClubPay están escritos para leérselos al cliente: van
        tal cual, sin traducir ni acortar."""
        if error.kind == 'auth':
            return _("La clave de ClubPay no es válida, o el comercio todavía no está activo en ClubPay.")
        return error.detail or error.user_message()

    @api.model
    def set_key(self, key):
        """Prueba la clave y, si ClubPay la acepta, la guarda. No hay una ruta
        para preguntar «¿esta clave anda?»: se consulta un cobro que no existe.
        404 es que la clave pasó; 401, que no."""
        key = (key or '').strip()
        if not key:
            raise UserError(_("Pegá la clave de ClubPay del comercio."))
        try:
            self._call('GET', '/pos/charges/chg_prueba_de_odoo', key=key)
        except NexoError as e:
            if e.kind != 'not_found':
                raise UserError(self._message(e)) from e
        self.env['nexo.token']._store(SERVICE, key, account_name=_("ClubPay"))
        return True

    # ------------------------------------------------------------------
    # Cobro
    # ------------------------------------------------------------------

    @api.model
    def _create_charge(self, total, origin, company=None, sale_order=None, pos_order_uuid=None):
        company = company or self.env.company
        if total <= 0:
            raise UserError(_("El total tiene que ser mayor que cero para usar ClubPay."))
        # Única por intento: ClubPay devuelve el mismo cobro para la misma
        # referencia, y uno vencido no se puede volver a mostrar.
        db = self.env['ir.config_parameter'].sudo().get_str('database.uuid') or ''
        reference = f"odoo-{db[:8]}-{origin}-{uuid4().hex[:16]}"
        vals = {
            'company_id': company.id, 'origin': origin, 'external_reference': reference,
            'ticket_total': total, 'sale_order_id': sale_order.id if sale_order else False,
            'pos_order_uuid': pos_order_uuid or False,
        }
        if self._simulator(company):
            charge_id = f"chg_sim_{uuid4().hex[:12]}"
            vals.update(name=charge_id, simulated=True, qr_payload=f"https://api.clubpay.com.ar/c/{charge_id}",
                        expires_at=fields.Datetime.now() + timedelta(minutes=SIM_MINUTOS))
        else:
            try:
                data = self._call('POST', '/pos/charges', {
                    'ticket_total_cents': centavos(total), 'external_reference': reference}, company=company)
            except NexoError as e:
                raise UserError(self._message(e)) from e
            if not data.get('charge_id') or not data.get('qr_payload'):
                raise UserError(NexoError('invalid_response', service=LABEL).user_message())
            vals.update(name=data['charge_id'], qr_payload=data['qr_payload'],
                        expires_at=_parse_dt(data.get('expires_at')), state=data.get('status') or 'pending')
        return self.sudo().create(vals)

    def _refresh(self):
        """Relee el cobro en ClubPay. Un cobro resuelto ya no cambia."""
        for charge in self.filtered(lambda c: c.state == 'pending'):
            if charge.simulated:
                charge._simulate()
                continue
            try:
                data = self._call('GET', f'/pos/charges/{charge.name}', company=charge.company_id)
            except NexoError as e:
                # Sin respuesta se vuelve a preguntar en dos segundos.
                charge.error = self._message(e)
                continue
            charge._apply(data)
        return True

    def _apply(self, data):
        self.ensure_one()
        status = data.get('status') or 'pending'
        vals = {'state': status if status in dict(STATES) else 'pending', 'error': data.get('error') or False}
        if status == 'applied':
            member = data.get('member') or {}
            offer = data.get('offer') or {}
            vals.update(
                member_name=member.get('name'), member_number=member.get('member_number'),
                club_name=member.get('club_name'), offer_description=offer.get('description'),
                discount_percent=offer.get('discount_percent') or 0.0,
                transaction_id=str(data['transaction_id']) if data.get('transaction_id') is not None else False,
                discount=pesos(data.get('discount_cents')), neto=pesos(data.get('neto_cents')),
                recorte=data.get('recorte') or False,
            )
            # El total del cobro es el de ClubPay: si no coincide con el de la
            # compra, el descuento se calculó sobre otro importe.
            if data.get('ticket_total_cents') is not None:
                vals['ticket_total'] = pesos(data['ticket_total_cents'])
        self.write(vals)

    def _simulate(self):
        self.ensure_one()
        seconds = (fields.Datetime.now() - self.create_date).total_seconds()
        wait = int(self.env['ir.config_parameter'].sudo().get_str('nexo_clubpay.simulador_segundos') or SIM_SEGUNDOS)
        if self.expires_at and fields.Datetime.now() > self.expires_at:
            self.state = 'expired'
        elif wait <= 0 or seconds >= wait:
            total = centavos(self.ticket_total)
            bruto = round(total * 0.2)
            discount = min(bruto, 800000)
            self._apply({
                'status': 'applied', 'ticket_total_cents': total, 'transaction_id': f"sim-{self.id}",
                'member': {'name': "Pedro Gómez (simulador)", 'member_number': "CU-00001", 'club_name': "Club Unión"},
                'offer': {'description': "20% con tope de $8.000", 'discount_percent': 20},
                'discount_cents': discount, 'neto_cents': total - discount,
                'recorte': 'por_compra' if discount < bruto else '',
            })

    def _cancel(self):
        for charge in self.filtered(lambda c: c.state == 'pending'):
            if charge.simulated:
                charge.state = 'cancelled'
                continue
            try:
                charge._apply(self._call('POST', f'/pos/charges/{charge.name}/cancel', {}, company=charge.company_id))
            except NexoError as e:
                if e.status == 409:
                    # Ya se resolvió (aplicado o vencido): manda lo que diga ClubPay.
                    charge._refresh()
                else:
                    raise UserError(self._message(e)) from e
        return True

    def _qr_image(self, size=300):
        """El QR como imagen, armado en Odoo con el texto de ClubPay tal cual."""
        self.ensure_one()
        png = self.env['ir.actions.report'].barcode('QR', self.qr_payload, width=size, height=size, quiet=1)
        return 'data:image/png;base64,' + base64.b64encode(png).decode()

    def _to_ui(self):
        self.ensure_one()
        return {
            'charge_id': self.name,
            'state': self.state,
            'qr_image': self._qr_image() if self.state == 'pending' and self.qr_payload else False,
            'qr_payload': self.qr_payload,
            'expires_at': self.expires_at and self.expires_at.replace(tzinfo=timezone.utc).isoformat(),
            'ticket_total': self.ticket_total,
            'member_name': self.member_name,
            'member_number': self.member_number,
            'club_name': self.club_name,
            'offer_description': self.offer_description,
            'discount': self.discount,
            'neto': self.neto,
            'recorte': self.recorte_texto,
            'transaction_id': self.transaction_id,
            'error': self.error,
            'simulated': self.simulated,
        }

    @api.model
    def _find(self, charge_id, company=None):
        company = company or self.env.company
        charge = self.sudo().search([('name', '=', charge_id), ('company_id', '=', company.id)], limit=1)
        if not charge:
            raise UserError(_("No se encontró el cobro de ClubPay %s.", charge_id))
        return charge
