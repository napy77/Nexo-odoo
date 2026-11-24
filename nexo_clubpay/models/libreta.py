"""Libreta de ClubPay: la cuenta corriente del cliente, en su teléfono.

El comercio invita al cliente con su DNI desde el contacto. La persona acepta
en la app de ClubPay, y desde ahí ve lo que debe y lo puede pagar con el
Mercado Pago que el comercio conectó en ClubPay. Es lo mismo que hace NexoPOS
(`backend/src/integrations/clubpay.ts`, `modules/clubpay-outbox.ts`).

Contrato: `nexopos/docs/CLUBPAY-CUENTA-CORRIENTE.md` y `clubapp/docs/
API_CONTRACTS.md` («Cuenta corriente»). Clave `pos_…` del comercio en
`X-API-Key`, importes en centavos.

- POST /pos/customers {dni, external_id} → {encontrado, status, persona,
       mensaje}. Idempotente; no pisa una vinculación aceptada.
- GET  /pos/customers/:external_id → el mismo estado, sin mandar el DNI.
- POST /pos/account/movements {external_id, movement_id, kind, amount_cents,
       occurred_at, description}. Idempotente por movement_id. Contesta 200
       aunque no lo registre.

Lo que no se negocia (DEC-025):
- **Nace como propuesta.** En el mostrador se tipean DNI mal; hasta que la
  persona confirma, ClubPay no le muestra ningún saldo.
- **El DNI viaja una sola vez.** Para saber en qué quedó se consulta sin él.
- **La cuenta corriente es la de Odoo**: el saldo a cobrar del contacto en
  Contabilidad. ClubPay espeja; no decide nada.
- **El aviso no puede perderse ni frenar una venta**: se encola al publicar y
  sale por red después, con reintentos.
- **Un pago que vino de ClubPay no se le devuelve a ClubPay**: lo originó ella.

ClubPay avisa los pagos y las vinculaciones a una sola URL de la plataforma,
la de NexoPOS. Odoo no se entera; pregunta. Pedido en `docs/PARA-CLUBPAY_de-
NEXO-ODOO_libreta-en-odoo.md`, respuesta en `docs/respuesta-libreta-odoo.md`
(24/11/2026):
- `GET /pos/account/payments?desde=` está hecha y sale en el próximo despliegue
  de ClubPay. Hasta entonces el cron la intenta, recibe 404 y sigue.
- A NexoPOS no se le avisa nada de un `ODOO-…`.
- Si la persona ya tiene la libreta de NexoPOS (`CLI-…`) en el mismo comercio,
  `POST /pos/customers` contesta 409 con el motivo, y se muestra tal cual.
"""
import logging
import re
from datetime import timedelta, timezone
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.nexo_core.lib.client import NexoError

from .nexo_clubpay_charge import _parse_dt, centavos, pesos

_logger = logging.getLogger(__name__)

LINK_STATES = [
    ('sin_cuenta', 'Sin cuenta en ClubPay'),
    ('propuesta', 'Invitación enviada'),
    ('vinculada', 'Vinculada'),
    ('rechazada', 'Rechazada por la persona'),
]
KINDS = [('compra', 'Compra'), ('pago', 'Pago'), ('ajuste', 'Ajuste'), ('devolucion', 'Devolución')]
# Los mismos reintentos que NexoPOS: si en dos días no entró, no es la red.
REINTENTOS_MIN = [1, 5, 15, 60]
REINTENTO_LARGO_MIN = 360
MAX_INTENTOS = 12
# Mientras ClubPay no avise a Odoo, se pregunta por las invitaciones pendientes.
DIAS_PREGUNTANDO = 30
# Páginas de pagos desde la app por corrida (200 cada una): lo que sobre, en la próxima.
MAX_PAGES = 10


def _external_id(partner):
    # «ODOO-» y no «CLI-»: la misma clave del comercio puede estar en NexoPOS, y
    # ClubPay empareja la libreta por este número. Un «CLI-12» de Odoo sería el
    # cliente 12 de NexoPOS, y su pago bajaría la deuda de otra persona.
    return f"ODOO-{partner.id}"


def _iso(dt):
    return dt.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    nexo_clubpay_dni = fields.Char('DNI', help="Solo números. Con él ClubPay le propone a la persona ver esta "
                                               "cuenta en su app.")
    nexo_clubpay_status = fields.Selection(LINK_STATES, 'Libreta en ClubPay', readonly=True, copy=False)
    nexo_clubpay_persona = fields.Char('Persona en ClubPay', readonly=True, copy=False)
    nexo_clubpay_account = fields.Char('Cuenta en ClubPay', readonly=True, copy=False)
    nexo_clubpay_message = fields.Char('Respuesta de ClubPay', readonly=True, copy=False)
    nexo_clubpay_invited_at = fields.Datetime('Invitación enviada el', readonly=True, copy=False)
    nexo_clubpay_linked_at = fields.Datetime('Vinculada el', readonly=True, copy=False)
    nexo_clubpay_checked_at = fields.Datetime('Consultado el', readonly=True, copy=False)
    nexo_clubpay_company_id = fields.Many2one('res.company', 'Compañía de la libreta', readonly=True, copy=False)
    nexo_clubpay_libreta_on = fields.Boolean(compute='_compute_nexo_clubpay_libreta_on')
    nexo_clubpay_simulated = fields.Boolean(compute='_compute_nexo_clubpay_libreta_on')
    nexo_clubpay_line_count = fields.Integer(compute='_compute_nexo_clubpay_line_count')

    @api.depends_context('company')
    def _compute_nexo_clubpay_libreta_on(self):
        on = self.env['nexo.clubpay.libreta.line']._enabled(self.env.company)
        sim = self.env['nexo.clubpay.charge']._simulator(self.env.company)
        for partner in self:
            partner.nexo_clubpay_libreta_on = on
            partner.nexo_clubpay_simulated = sim

    def _compute_nexo_clubpay_line_count(self):
        groups = self.env['nexo.clubpay.libreta.line']._read_group(
            [('partner_id', 'in', self.ids)], ['partner_id'], ['__count'])
        counts = {partner.id: count for partner, count in groups}
        for partner in self:
            partner.nexo_clubpay_line_count = counts.get(partner.id, 0)

    @api.constrains('nexo_clubpay_dni')
    def _check_nexo_clubpay_dni(self):
        for partner in self:
            if partner.nexo_clubpay_dni and not re.fullmatch(r'\d{7,8}', partner.nexo_clubpay_dni):
                raise UserError(_("El DNI va solo con números, sin puntos: 7 u 8 dígitos."))

    @api.onchange('nexo_clubpay_dni')
    def _onchange_nexo_clubpay_dni(self):
        # Se escribe como se escribe en el mostrador: con puntos o espacios.
        if self.nexo_clubpay_dni:
            self.nexo_clubpay_dni = re.sub(r'\D', '', self.nexo_clubpay_dni)

    def _clubpay_libreta_partner(self):
        """La libreta es del contacto que tiene la cuenta a cobrar: la empresa,
        no la persona que retira."""
        self.ensure_one()
        return self.commercial_partner_id

    # ------------------------------------------------------------------
    # Botones del contacto
    # ------------------------------------------------------------------

    def action_nexo_clubpay_invite(self):
        self.ensure_one()
        partner = self._clubpay_libreta_partner()
        Line = self.env['nexo.clubpay.libreta.line']
        company = self.env.company
        if not Line._enabled(company):
            raise UserError(_("La libreta de ClubPay está apagada. Prendela en Ajustes → ClubPay."))
        dni = re.sub(r'\D', '', self.nexo_clubpay_dni or partner.nexo_clubpay_dni or '')
        if not re.fullmatch(r'\d{7,8}', dni):
            raise UserError(_("Cargá el DNI del cliente para enviarle la invitación."))
        if partner.nexo_clubpay_status in ('propuesta', 'vinculada'):
            # El DNI ya viajó: no se vuelve a mandar. Se consulta.
            return partner.action_nexo_clubpay_check()
        if Line._simulated(company):
            # Como el simulador de NexoPOS: el último dígito elige el caso.
            result = {'encontrado': False, 'status': 'sin_cuenta', 'mensaje': "El DNI no tiene cuenta en ClubPay"} \
                if dni.endswith('0') else {'encontrado': True, 'status': 'propuesta', 'persona': partner.name,
                                           'mensaje': "Simulador: la invitación quedó enviada"}
        else:
            try:
                result = Line._call('POST', '/pos/customers', {'dni': dni, 'external_id': _external_id(partner)},
                                    company=company)
            except NexoError as e:
                raise UserError(self.env['nexo.clubpay.charge']._message(e)) from e
        partner.sudo().write({'nexo_clubpay_dni': dni, 'nexo_clubpay_company_id': company.id,
                              'nexo_clubpay_invited_at': fields.Datetime.now()})
        if self != partner and self.nexo_clubpay_dni:
            self.sudo().nexo_clubpay_dni = False
        partner._clubpay_apply_link(result)
        return partner._clubpay_link_notification()

    def action_nexo_clubpay_check(self):
        self.ensure_one()
        partner = self._clubpay_libreta_partner()
        partner._clubpay_refresh_link(raise_errors=True)
        return partner._clubpay_link_notification()

    def action_nexo_clubpay_reset(self):
        """Después de un rechazo (casi siempre un DNI mal tipeado): se corrige el
        DNI y se vuelve a invitar."""
        partner = self._clubpay_libreta_partner()
        if partner.nexo_clubpay_status == 'vinculada':
            raise UserError(_("La libreta está vinculada. Si no es de esta persona, que la desvincule desde ClubPay."))
        partner.sudo().write({'nexo_clubpay_status': False, 'nexo_clubpay_persona': False,
                              'nexo_clubpay_message': False, 'nexo_clubpay_invited_at': False})
        return True

    def action_nexo_clubpay_lines(self):
        action = self.env['ir.actions.act_window']._for_xml_id('nexo_clubpay.nexo_clubpay_libreta_line_action')
        action['domain'] = [('partner_id', '=', self._clubpay_libreta_partner().id)]
        return action

    def action_nexo_clubpay_sim_accept(self):
        partner = self._clubpay_libreta_partner()
        if not self.env['nexo.clubpay.libreta.line']._simulated(partner.nexo_clubpay_company_id or self.env.company):
            raise UserError(_("Solo con el simulador de ClubPay prendido."))
        partner._clubpay_apply_link({'status': 'vinculada', 'persona': partner.name,
                                     'account_id': f"acc_sim_{partner.id}"})
        return partner._clubpay_link_notification()

    def action_nexo_clubpay_sim_payment(self):
        """Simula que la persona pagó su libreta desde la app, por todo lo que
        debe o $10.000 si no debe nada."""
        partner = self._clubpay_libreta_partner()
        company = partner.nexo_clubpay_company_id or self.env.company
        if not self.env['nexo.clubpay.libreta.line']._simulated(company):
            raise UserError(_("Solo con el simulador de ClubPay prendido."))
        if partner.nexo_clubpay_status != 'vinculada':
            raise UserError(_("Primero la persona tiene que aceptar la invitación."))
        debt = self.env['nexo.clubpay.libreta.line']._balance(partner, company)
        amount = debt if debt > 0 else 10000.0
        pay = self.env['nexo.clubpay.libreta.payment']._record_app_payment({
            'clubpay_payment_id': f"pay_sim_{fields.Datetime.now().strftime('%Y%m%d%H%M%S')}_{partner.id}",
            'external_id': _external_id(partner), 'amount_cents': centavos(amount),
            'paid_at': _iso(fields.Datetime.now()),
        }, company, simulated=True)
        return {'type': 'ir.actions.act_window', 'res_model': 'nexo.clubpay.libreta.payment',
                'res_id': pay.id, 'view_mode': 'form'}

    # ------------------------------------------------------------------
    # Estado de la vinculación
    # ------------------------------------------------------------------

    def _clubpay_refresh_link(self, raise_errors=False):
        self.ensure_one()
        Line = self.env['nexo.clubpay.libreta.line']
        company = self.nexo_clubpay_company_id or self.env.company
        if not self.nexo_clubpay_status:
            raise UserError(_("Todavía no se envió la invitación."))
        if Line._simulated(company):
            self.sudo().nexo_clubpay_checked_at = fields.Datetime.now()
            return
        try:
            result = Line._call('GET', f'/pos/customers/{_external_id(self)}', company=company)
        except NexoError as e:
            if raise_errors:
                raise UserError(self.env['nexo.clubpay.charge']._message(e)) from e
            _logger.warning("ClubPay: no se pudo consultar la libreta de %s: %s", self.display_name, e)
            return
        self._clubpay_apply_link(result)

    def _clubpay_apply_link(self, result):
        self.ensure_one()
        status = result.get('status')
        # En la base de ClubPay es «vinculada»; en su documentación, «aceptada».
        # Se aceptan las dos (ver vinculacionAceptada en NexoPOS).
        if status == 'aceptada':
            status = 'vinculada'
        if status not in dict(LINK_STATES):
            raise UserError(_("ClubPay contestó un estado que no conocemos: %s", status))
        was_linked = self.nexo_clubpay_status == 'vinculada'
        vals = {'nexo_clubpay_status': status, 'nexo_clubpay_checked_at': fields.Datetime.now(),
                'nexo_clubpay_message': result.get('mensaje') or False}
        if result.get('persona'):
            vals['nexo_clubpay_persona'] = result['persona']
        if result.get('account_id'):
            vals['nexo_clubpay_account'] = result['account_id']
        if status == 'vinculada' and not was_linked:
            vals['nexo_clubpay_linked_at'] = fields.Datetime.now()
        self.sudo().write(vals)
        if status == 'vinculada' and not was_linked:
            self.env['nexo.clubpay.libreta.line']._open_libreta(self)
        if status == 'rechazada' and was_linked is False:
            # Casi siempre es un DNI tipeado mal: la cuenta apuntaba a otra
            # persona. Que alguien lo mire.
            self.sudo().message_post(body=_("La persona de ClubPay con el DNI %s dijo que esta cuenta no es suya. "
                                            "Revisá el DNI y volvé a invitar.", self.nexo_clubpay_dni))

    def _clubpay_link_notification(self):
        self.ensure_one()
        texts = {
            'sin_cuenta': _("Ese DNI no tiene cuenta en ClubPay. La cuenta corriente anda igual; cuando se "
                            "registre, volvé a invitarlo."),
            'propuesta': _("Invitación enviada a %s. La ve en su app y decide si la acepta.",
                           self.nexo_clubpay_persona or self.nexo_clubpay_dni),
            'vinculada': _("%s ya ve su cuenta en ClubPay.", self.nexo_clubpay_persona or self.name),
            'rechazada': _("La persona dijo que esta cuenta no es suya. Revisá el DNI."),
        }
        kind = {'sin_cuenta': 'warning', 'rechazada': 'danger'}.get(self.nexo_clubpay_status, 'success')
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': 'ClubPay', 'type': kind, 'sticky': False,
                           'message': self.nexo_clubpay_message or texts.get(self.nexo_clubpay_status, ''),
                           'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}


class NexoClubpayLibretaLine(models.Model):
    """Un movimiento de la cuenta corriente, tal como se le avisa a ClubPay.

    Es la cola: la fila nace en la misma transacción que la factura o el pago
    (al publicar) o en la corrida siguiente del cron, y el envío pasa después.
    """
    _name = 'nexo.clubpay.libreta.line'
    _description = 'Movimiento de libreta enviado a ClubPay'
    _order = 'occurred_at desc, id desc'

    partner_id = fields.Many2one('res.partner', 'Cliente', required=True, readonly=True, index=True,
                                 ondelete='cascade')
    company_id = fields.Many2one('res.company', required=True, readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    move_line_id = fields.Many2one('account.move.line', 'Apunte', readonly=True, index='btree_not_null',
                                   ondelete='set null')
    move_id = fields.Many2one('account.move', 'Asiento', readonly=True, ondelete='set null')
    movement_id = fields.Char(required=True, readonly=True)
    kind = fields.Selection(KINDS, 'Tipo', required=True, readonly=True)
    amount = fields.Monetary('Importe', readonly=True, help="Positivo aumenta la deuda; negativo la baja")
    occurred_at = fields.Datetime('Fecha', required=True, readonly=True)
    description = fields.Char('Detalle', readonly=True)
    state = fields.Selection([
        ('pending', 'Por enviar'),
        ('sent', 'Enviado'),
        ('included', 'En el saldo inicial'),
        ('from_clubpay', 'Vino de ClubPay'),
        ('failed', 'No se pudo enviar'),
    ], 'Estado', default='pending', required=True, readonly=True, index=True)
    attempts = fields.Integer('Intentos', readonly=True)
    next_try = fields.Datetime('Próximo intento', readonly=True)
    error = fields.Char(readonly=True)
    reversed = fields.Boolean('Anulado y avisado', readonly=True)

    _movement_unique = models.Constraint('UNIQUE(company_id, movement_id)',
                                         "Ese movimiento ya está en la cola de ClubPay.")

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------

    @api.model
    def _enabled(self, company=None):
        company = company or self.env.company
        return self.env['ir.config_parameter'].sudo().get_str(f'nexo_clubpay.libreta.{company.id}') == '1'

    @api.model
    def _set_enabled(self, enabled, company=None):
        company = company or self.env.company
        self.env['ir.config_parameter'].sudo().set_str(f'nexo_clubpay.libreta.{company.id}', '1' if enabled else '0')

    @api.model
    def _simulated(self, company=None):
        return self.env['nexo.clubpay.charge']._simulator(company)

    @api.model
    def _call(self, method, path, json_body=None, company=None):
        return self.env['nexo.clubpay.charge']._call(method, path, json_body=json_body, company=company)

    @api.model
    def _prefix(self):
        # Para que dos bases del mismo comercio no se pisen los movimientos.
        db = self.env['ir.config_parameter'].sudo().get_str('database.uuid') or ''
        return f"odoo-{db[:8]}"

    # ------------------------------------------------------------------
    # Qué es la cuenta corriente en Odoo
    # ------------------------------------------------------------------

    @api.model
    def _receivable_domain(self, partner, company):
        return [('partner_id', '=', partner.id), ('company_id', '=', company.id),
                ('parent_state', '=', 'posted'), ('account_id.account_type', '=', 'asset_receivable')]

    @api.model
    def _balance(self, partner, company):
        """Lo que debe: el saldo a cobrar del contacto en Contabilidad."""
        lines = self.env['account.move.line'].sudo().search(self._receivable_domain(partner, company))
        return sum(lines.mapped('balance'))

    @api.model
    def _kind(self, line):
        move_type = line.move_id.move_type
        if move_type in ('out_invoice', 'out_receipt'):
            return 'compra'
        if move_type == 'out_refund':
            return 'devolucion'
        return 'pago' if line.balance < 0 else 'ajuste'

    @api.model
    def _vals_from_line(self, line):
        move = line.move_id
        detail = move.name or ''
        if move.ref and move.ref != detail:
            detail = f"{detail} · {move.ref}" if detail else move.ref
        return {
            'partner_id': line.partner_id.id, 'company_id': line.company_id.id,
            'move_line_id': line.id, 'move_id': move.id,
            'movement_id': f"{self._prefix()}-aml-{line.id}",
            'kind': self._kind(line), 'amount': line.balance,
            # La fecha real del movimiento: la app los ordena por esto.
            'occurred_at': line.create_date or fields.Datetime.now(),
            'description': detail[:120],
        }

    @api.model
    def _open_libreta(self, partner):
        """La persona aceptó. La app muestra lo que debe desde hoy: un ajuste con
        el saldo a la fecha, y de acá en adelante cada movimiento.

        NexoPOS en cambio recupera los movimientos de los últimos días. Acá no:
        la deuda de un cliente de Odoo puede venir de facturas de hace meses, y
        mandar solo las recientes le mostraría un saldo que no es el suyo.
        """
        company = partner.nexo_clubpay_company_id or self.env.company
        lines = self.env['account.move.line'].sudo().search(self._receivable_domain(partner, company))
        known = set(self.sudo().search([('move_line_id', 'in', lines.ids)]).move_line_id.ids)
        self.sudo().create([dict(self._vals_from_line(line), state='included')
                            for line in lines if line.id not in known])
        balance = self._balance(partner, company)
        if company.currency_id.is_zero(balance):
            return
        self.sudo().create({
            'partner_id': partner.id, 'company_id': company.id,
            'movement_id': f"{self._prefix()}-saldo-{partner.id}-{fields.Datetime.now().strftime('%Y%m%d%H%M%S')}",
            'kind': 'ajuste', 'amount': balance, 'occurred_at': fields.Datetime.now(),
            'description': _("Saldo al vincular la libreta"),
            'next_try': fields.Datetime.now(),
        })

    @api.model
    def _enqueue_moves(self, moves):
        """Al publicar: los apuntes a cobrar de clientes con la libreta
        vinculada entran a la cola en la misma transacción."""
        lines = moves.line_ids.filtered(
            lambda l: l.account_id.account_type == 'asset_receivable' and l.partner_id
            and l.partner_id.nexo_clubpay_status == 'vinculada'
            and l.partner_id.nexo_clubpay_company_id == l.company_id
            and self._enabled(l.company_id))
        if not lines:
            return
        known = set(self.sudo().search([('move_line_id', 'in', lines.ids)]).move_line_id.ids)
        from_clubpay = set(self.env['nexo.clubpay.libreta.payment'].sudo().search(
            [('account_payment_ids.move_id', 'in', moves.ids)]).account_payment_ids.move_id.ids)
        vals = []
        for line in lines:
            if line.id in known:
                continue
            state = 'from_clubpay' if line.move_id.id in from_clubpay else 'pending'
            vals.append(dict(self._vals_from_line(line), state=state, next_try=fields.Datetime.now()))
        if vals:
            self.sudo().create(vals)

    @api.model
    def _enqueue_reversals(self, company):
        """Una factura que se pasó a borrador o se borró después de avisarla:
        se avisa la anulación. El id deriva del original, así que repetirla no
        duplica nada."""
        # Solo los que nacieron de un apunte (`-aml-`): el saldo inicial y las
        # anulaciones no tienen apunte propio, y no por eso están borrados.
        gone = self.sudo().search([('company_id', '=', company.id), ('state', '=', 'sent'), ('reversed', '=', False),
                                   ('move_line_id', '=', False), ('movement_id', 'like', '%-aml-%'),
                                   ('movement_id', 'not like', '%-anulado')])
        gone |= self.sudo().search([('company_id', '=', company.id), ('state', '=', 'sent'), ('reversed', '=', False),
                                    ('move_line_id', '!=', False), ('move_line_id.parent_state', '!=', 'posted')])
        for line in gone:
            self.sudo().create({
                'partner_id': line.partner_id.id, 'company_id': company.id,
                'movement_id': f"{line.movement_id}-anulado", 'kind': 'ajuste', 'amount': -line.amount,
                'occurred_at': fields.Datetime.now(), 'next_try': fields.Datetime.now(),
                'description': _("Anulado: %s", line.description or ''),
            })
            line.reversed = True

    # ------------------------------------------------------------------
    # Envío
    # ------------------------------------------------------------------

    def _payload(self):
        self.ensure_one()
        return {
            'external_id': _external_id(self.partner_id), 'movement_id': self.movement_id, 'kind': self.kind,
            'amount_cents': centavos(self.amount), 'occurred_at': _iso(self.occurred_at),
            'description': self.description or '',
        }

    def _send(self):
        simulated = {}
        for line in self:
            company = line.company_id
            if company.id not in simulated:
                simulated[company.id] = self._simulated(company)
            try:
                if simulated[company.id]:
                    _logger.info("ClubPay (simulador): movimiento %s", line._payload())
                else:
                    with self.env.cr.savepoint():
                        line._call('POST', '/pos/account/movements', line._payload(), company=company)
            except NexoError as e:
                attempts = line.attempts + 1
                wait = REINTENTOS_MIN[attempts - 1] if attempts <= len(REINTENTOS_MIN) else REINTENTO_LARGO_MIN
                line.write({
                    'attempts': attempts, 'error': str(e)[:250],
                    'next_try': fields.Datetime.now() + timedelta(minutes=wait),
                    # Si en dos días no entró, no es un corte de red: queda a la
                    # vista para que alguien lo mire.
                    'state': 'failed' if attempts >= MAX_INTENTOS else 'pending',
                })
                continue
            line.write({'state': 'sent', 'error': False, 'attempts': line.attempts + 1})

    def action_retry(self):
        self.filtered(lambda l: l.state == 'failed').write(
            {'state': 'pending', 'attempts': 0, 'next_try': fields.Datetime.now()})
        return True

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _cron_libreta(self):
        for company in self.env['res.company'].sudo().search([]):
            if not self._enabled(company) or not self.env['nexo.clubpay.charge']._enabled(company):
                continue
            Line = self.with_company(company)
            for step in (Line._check_invitations, Line._enqueue_reversals,
                         Line.env['nexo.clubpay.libreta.payment']._fetch):
                try:
                    with self.env.cr.savepoint():
                        step(company)
                except Exception:  # noqa: BLE001 — un paso no frena a los demás
                    _logger.exception("ClubPay: falló la libreta de %s (%s)", company.name, step.__name__)
            due = Line.sudo().search([('company_id', '=', company.id), ('state', '=', 'pending'),
                                      '|', ('next_try', '=', False), ('next_try', '<=', fields.Datetime.now())],
                                     order='occurred_at, id', limit=200)
            due._send()

    @api.model
    def _check_invitations(self, company):
        """ClubPay avisa la aceptación solo a NexoPOS: Odoo pregunta por las
        invitaciones pendientes de los últimos días."""
        since = fields.Datetime.now() - timedelta(days=DIAS_PREGUNTANDO)
        partners = self.env['res.partner'].sudo().search([
            ('nexo_clubpay_company_id', '=', company.id), ('nexo_clubpay_status', '=', 'propuesta'),
            ('nexo_clubpay_invited_at', '>=', since)])
        for partner in partners:
            partner._clubpay_refresh_link()


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        self.env['nexo.clubpay.libreta.line']._enqueue_moves(posted)
        return posted


class NexoClubpayLibretaPayment(models.Model):
    """Un pago que la persona hizo desde la app de ClubPay contra su libreta.

    La plata fue directo al Mercado Pago del comercio. Entra como cobro al
    cliente en el diario «Mercado Pago (ClubPay)» y se imputa a las facturas
    más viejas primero, como en NexoPOS. Importe libre: si solo se aceptara el
    pago total, la app sería peor que el cuaderno.
    """
    _name = 'nexo.clubpay.libreta.payment'
    _description = 'Pago de libreta desde ClubPay'
    _order = 'paid_at desc, id desc'

    name = fields.Char('Pago en ClubPay', required=True, readonly=True, help="clubpay_payment_id")
    company_id = fields.Many2one('res.company', required=True, readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    partner_id = fields.Many2one('res.partner', 'Cliente', required=True, readonly=True, index=True)
    amount = fields.Monetary('Importe', readonly=True)
    paid_at = fields.Datetime('Pagado el', readonly=True)
    mp_payment_id = fields.Char('Pago en Mercado Pago', readonly=True)
    account_payment_ids = fields.Many2many('account.payment', string='Cobros en Contabilidad', readonly=True)
    simulated = fields.Boolean('Simulador', readonly=True)

    # El índice único y no una consulta previa: dos lecturas a la vez no
    # pueden registrar dos veces el mismo pago.
    _name_unique = models.Constraint('UNIQUE(company_id, name)', "Ese pago de ClubPay ya está registrado.")

    @api.model
    def _record_app_payment(self, data, company, simulated=False):
        existing = self.sudo().search([('company_id', '=', company.id), ('name', '=', data['clubpay_payment_id'])])
        if existing:
            return existing
        match = re.fullmatch(r'ODOO-(\d+)', data.get('external_id') or '', re.IGNORECASE)
        partner = self.env['res.partner'].sudo().browse(int(match.group(1))).exists() if match else None
        if not partner or partner.nexo_clubpay_company_id != company:
            raise UserError(_("El pago %(p)s de ClubPay es de un cliente que no está en esta base (%(c)s).",
                              p=data['clubpay_payment_id'], c=data.get('external_id')))
        journal = self.env['res.config.settings']._clubpay_get_journal(
            company, 'mp_journal_id', _("Mercado Pago (ClubPay)"), 'CPMP')
        amount = pesos(data['amount_cents'])
        pay = self.sudo().create({
            'name': data['clubpay_payment_id'], 'company_id': company.id, 'partner_id': partner.id,
            'amount': amount, 'paid_at': _parse_dt(data.get('paid_at')) or fields.Datetime.now(),
            'mp_payment_id': data.get('mp_payment_id') or False,
            'simulated': simulated,
        })
        pay._post_payments(journal)
        return pay

    def _post_payments(self, journal):
        self.ensure_one()
        currency = self.company_id.currency_id
        memo = _("Pago desde ClubPay %s", self.name)
        date = fields.Date.context_today(self, self.paid_at)
        invoices = self.env['account.move'].sudo().search([
            ('partner_id', 'child_of', self.partner_id.id), ('company_id', '=', self.company_id.id),
            ('move_type', 'in', ('out_invoice', 'out_receipt')), ('state', '=', 'posted'),
            ('payment_state', 'in', ('not_paid', 'partial'))], order='invoice_date_due, invoice_date, id')
        left = self.amount
        payments = self.env['account.payment']
        for invoice in invoices:
            if currency.compare_amounts(left, 0) <= 0:
                break
            part = min(left, invoice.amount_residual)
            if currency.compare_amounts(part, 0) <= 0:
                continue
            wizard = self.env['account.payment.register'].sudo().with_context(
                active_model='account.move', active_ids=invoice.ids).create({
                    'journal_id': journal.id, 'amount': part, 'payment_date': date, 'communication': memo})
            payments |= wizard._create_payments()
            left -= part
        if currency.compare_amounts(left, 0) > 0:
            # Pagó de más o a cuenta: queda a favor del cliente, como en el
            # cuaderno.
            Payment = self.env['account.payment'].sudo()
            # `memo` desde Odoo 18; antes era `ref`. Se mira el campo y no la
            # versión, para no suponer nada de Odoo 20.
            memo_field = 'memo' if 'memo' in Payment._fields else 'ref'
            extra = Payment.create({
                'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': self.partner_id.id,
                'amount': left, 'journal_id': journal.id, 'date': date, memo_field: memo,
                'company_id': self.company_id.id,
            })
            extra.action_post()
            payments |= extra
        self.account_payment_ids = payments
        # Los apuntes de estos cobros no vuelven a ClubPay: los originó ella.
        self.env['nexo.clubpay.libreta.line'].sudo().search(
            [('move_id', 'in', payments.move_id.ids), ('state', '=', 'pending')]).state = 'from_clubpay'

    @api.model
    def _fetch(self, company):
        """`GET /pos/account/payments?desde=`. ClubPay lo confirmó el 24/11/2026
        (`docs/respuesta-libreta-odoo.md`) y sale en su próximo despliegue;
        hasta entonces contesta 404 y se sigue.

        - Repite los pagos de los dos minutos antes de `desde`, para no perder
          uno que se aprobaba justo: el índice único los descarta.
        - Trae las libretas de NexoPOS (`CLI-`) del mismo comercio: se saltean.
        - Hasta 200 por página; con `hay_mas` se vuelve a pedir desde `hasta`.
        """
        if self.env['nexo.clubpay.charge']._simulator(company):
            return  # con el simulador, el pago se simula desde el contacto
        param = f'nexo_clubpay.libreta_pagos_desde.{company.id}'
        ICP = self.env['ir.config_parameter'].sudo()
        since = ICP.get_str(param) or ''
        for _page in range(MAX_PAGES):
            path = '/pos/account/payments' + (f'?desde={quote(since, safe="")}' if since else '')
            try:
                data = self.env['nexo.clubpay.libreta.line']._call('GET', path, company=company)
            except NexoError as e:
                if e.kind == 'not_found':
                    _logger.info("ClubPay todavía no tiene GET /pos/account/payments: los pagos desde la app no "
                                 "llegan a Odoo.")
                    return
                raise
            for item in data.get('pagos') or []:
                if not (item.get('external_id') or '').upper().startswith('ODOO-'):
                    continue  # son de NexoPOS: ClubPay ya se los avisa a NexoPOS
                # Si uno falla, el error sube y `desde` no avanza: la próxima
                # corrida lo vuelve a traer. Un pago no se saltea en silencio.
                self._record_app_payment(item, company)
            until = data.get('hasta')
            if until:
                ICP.set_str(param, until)
            # Sin avance no se pide de nuevo: sería la misma página.
            if not data.get('hay_mas') or not until or until == since:
                return
            since = until
