"""Pedidos de NexoTienda atendidos desde Odoo.

NexoPOS es el dueño del pedido de tienda: ahí entra (`POST /v1/orders`), ahí
se descuenta el stock al aceptarlo y de ahí sale cada aviso al comprador.
Odoo lo lee, lo cobra en el Punto de Venta y le pide a NexoPOS cada paso.

Contrato leído del código de NexoPOS (backend/src/modules/erp.ts, commit
cd15a2c) y de docs/RESPUESTA-PEDIDOS-ERP-A-NEXO-ODOO.md:
- GET  /pedidos?actualizados_desde=  → {pedidos[], hasta, hay_mas}, de a 200
- GET  /pedidos/:code
- POST /pedidos/:code/{aceptar,listo,en-camino,entregado,cancelar}
Repetir un paso contesta 200 sin hacer nada; un paso que no corresponde es
409 `paso_invalido` con el `status` actual. Importes en centavos.
"""
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.nexo_core.lib.client import NexoError
from odoo.addons.nexo_pos.models.nexo_pos_service import UNCERTAIN

_logger = logging.getLogger(__name__)

STATES = [
    ('recibido', 'Recibido'),
    ('aceptado', 'Aceptado'),
    ('listo', 'Listo'),
    ('en_camino', 'En camino'),
    ('entregado', 'Entregado'),
    ('cancelado', 'Cancelado'),
]
EN_CURSO = ('recibido', 'aceptado', 'listo', 'en_camino')
# Lo que NexoPOS ya descontó y Odoo descuenta recién al entregar.
DESCONTADO_EN_NEXOPOS = ('aceptado', 'listo', 'en_camino')

STEP_PATH = {'aceptar': 'aceptar', 'listo': 'listo', 'en_camino': 'en-camino',
             'entregado': 'entregado', 'cancelar': 'cancelar'}

# Los textos que muestra la pantalla de NexoPOS (CR-0003).
REVISION = {
    'cuenta_nueva_monto_alto': "Es el primer pedido de esta persona y es grande. Llamala antes de armarlo.",
    'identidad_no_disponible': "No pudimos comprobar su cuenta de ClubPay al recibirlo. "
                               "Confirmá con la persona antes de armarlo.",
}
PAGO_MEDIO = {'efectivo_entrega': 'Paga al retirar', 'online': 'Mercado Pago', 'cuenta_corriente': 'A la libreta'}
PAGO_ESTADO = {'no_aplica': '', 'pendiente': 'pendiente', 'pagado': 'pagado',
               'rechazado': 'rechazado', 'reembolsado': 'reembolsado'}

MAX_PAGES = 50


def _parse_iso(value):
    if not value:
        return False
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return dt.astimezone(timezone.utc).replace(tzinfo=None, microsecond=0)


def _pesos(centavos):
    try:
        return int(centavos or 0) / 100.0
    except (TypeError, ValueError):
        return 0.0


class NexoTiendaOrder(models.Model):
    _name = 'nexo.tienda.order'
    _description = 'Pedido de NexoTienda'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'creado desc, id desc'
    _rec_name = 'code'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company,
                                 index=True, readonly=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    code = fields.Char('Pedido', required=True, readonly=True, index=True)
    state = fields.Selection(STATES, 'Estado', required=True, readonly=True, tracking=True)
    creado = fields.Datetime('Hecho', readonly=True)
    actualizado = fields.Char('Último cambio en NexoPOS', readonly=True,
                              help="Con microsegundos, tal cual lo manda NexoPOS.")

    tipo = fields.Char('Entrega', readonly=True, help="retiro o reparto")
    franja = fields.Char('Franja', readonly=True)
    direccion = fields.Char('Dirección', readonly=True)
    contacto_nombre = fields.Char('Nombre', readonly=True)
    contacto_telefono = fields.Char('Teléfono', readonly=True)
    cuenta_id = fields.Char('Cuenta en NexoPOS', readonly=True, help="La libreta del cliente (CLI-<n>).")
    notas = fields.Text('Notas del comprador', readonly=True)
    listo_para = fields.Char('Listo para', readonly=True)
    motivo_cancelacion = fields.Char('Motivo de cancelación', readonly=True)
    cancelado_por = fields.Char('Cancelado por', readonly=True)
    revision = fields.Char('Revisión', readonly=True)
    revision_texto = fields.Char('Antes de aceptarlo', compute='_compute_revision_texto')

    pago_medio = fields.Char('Medio de pago', readonly=True)
    pago_estado = fields.Char('Estado del pago', readonly=True)
    pago_id = fields.Char('Id del pago', readonly=True)
    pago_texto = fields.Char('Pago', compute='_compute_pago_texto')
    no_cobrar = fields.Boolean('No se cobra en la caja', compute='_compute_pago_texto',
                               help="Pagado por Mercado Pago o a la libreta de NexoPOS.")

    subtotal = fields.Monetary('Subtotal', readonly=True)
    cargo = fields.Monetary('Cargo', readonly=True)
    total = fields.Monetary('Total', readonly=True)
    venta_ticket = fields.Char('Nota de venta en NexoPOS', readonly=True)

    line_ids = fields.One2many('nexo.tienda.order.line', 'order_id', 'Productos', readonly=True)
    sale_order_id = fields.Many2one('sale.order', 'Pedido de venta', readonly=True, copy=False, index='btree_not_null')
    partner_id = fields.Many2one(related='sale_order_id.partner_id')
    entregar_pendiente = fields.Boolean(
        'Entregado en Odoo, falta avisar', readonly=True,
        help="Se cobró o se entregó en Odoo y todavía no se le avisó a NexoPOS. Se avisa solo, cada minuto.")
    error = fields.Text('Problema', readonly=True)

    _code_company_uniq = models.Constraint('UNIQUE(company_id, code)', "El pedido ya está en Odoo.")

    @api.depends('revision', 'state')
    def _compute_revision_texto(self):
        for order in self:
            texto = REVISION.get(order.revision, order.revision) if order.revision else False
            order.revision_texto = texto if order.state == 'recibido' else False

    @api.depends('pago_medio', 'pago_estado', 'cuenta_id')
    def _compute_pago_texto(self):
        for order in self:
            medio = PAGO_MEDIO.get(order.pago_medio, order.pago_medio or '')
            estado = PAGO_ESTADO.get(order.pago_estado, order.pago_estado or '')
            order.pago_texto = f"{medio} · {estado}" if estado else medio
            order.no_cobrar = order.pago_estado == 'pagado' or order.pago_medio == 'cuenta_corriente'

    # ------------------------------------------------------------------
    # Configuración
    # ------------------------------------------------------------------

    @api.model
    def _enabled(self, company=None):
        """Apagado, Odoo no trae los pedidos. Prenderlo es decidir que la caja
        de este comercio es Odoo (DEC-016)."""
        company = company or self.env.company
        return self.env['ir.config_parameter'].sudo().get_str(f'nexo_tienda.enabled.{company.id}') == '1'

    @api.model
    def _set_enabled(self, enabled, company=None):
        company = company or self.env.company
        self.env['ir.config_parameter'].sudo().set_str(f'nexo_tienda.enabled.{company.id}', '1' if enabled else '0')

    def _service(self):
        return self.env['nexo.pos.service'].with_company(self.company_id or self.env.company)

    # ------------------------------------------------------------------
    # Traer pedidos
    # ------------------------------------------------------------------

    @api.model
    def _sync(self):
        """Trae lo que cambió desde la última vez, en la compañía actual.

        El `hasta` de NexoPOS queda dos minutos atrás a propósito, así que un
        pedido puede volver repetido: se actualiza por `code`. Se guarda tal
        cual, con microsegundos.
        """
        company = self.env.company
        ICP = self.env['ir.config_parameter'].sudo()
        key = f'nexo_tienda.cursor.{company.id}'
        cursor = ICP.get_str(key) or None
        service = self.env['nexo.pos.service']
        n = 0
        for _page in range(MAX_PAGES):
            data = service._call('GET', '/pedidos', params={'actualizados_desde': cursor} if cursor else None)
            if not isinstance(data, dict) or not isinstance(data.get('pedidos'), list):
                raise NexoError('invalid_response', service='NexoPOS')
            for pedido in data['pedidos']:
                if self._upsert(pedido):
                    n += 1
            cursor = data.get('hasta') or cursor
            if cursor:
                ICP.set_str(key, cursor)
            if not data.get('hay_mas'):
                break
        return n

    @api.model
    def _upsert(self, p):
        """Crea o actualiza el pedido por `code` y lleva el pedido de venta al
        mismo estado. Devuelve el registro, o vacío si no se pudo leer."""
        code = (p.get('code') or '').strip().upper()
        status = p.get('status')
        if not code or status not in dict(STATES):
            _logger.warning("NexoTienda: pedido sin código o con estado desconocido (%s, %s)", code, status)
            return self.browse()
        company = self.env.company
        retiro = p.get('retiro') or {}
        pago = p.get('pago') or {}
        contacto = p.get('contacto') or {}
        venta = p.get('venta') or {}
        vals = {
            'state': status,
            'actualizado': p.get('actualizado'),
            'tipo': retiro.get('tipo'),
            'franja': retiro.get('franja'),
            'direccion': retiro.get('direccion'),
            'pago_medio': pago.get('medio'),
            'pago_estado': pago.get('estado'),
            'pago_id': pago.get('id'),
            'contacto_nombre': contacto.get('nombre'),
            'contacto_telefono': contacto.get('telefono'),
            'cuenta_id': p.get('cuenta_id'),
            'notas': p.get('notas'),
            'listo_para': p.get('listo_para'),
            'motivo_cancelacion': p.get('motivo_cancelacion'),
            'cancelado_por': p.get('cancelado_por'),
            'revision': (p.get('revision') or {}).get('motivo'),
            'subtotal': _pesos(p.get('subtotal_centavos')),
            'cargo': _pesos(p.get('cargo_centavos')),
            'total': _pesos(p.get('total_centavos')),
            'venta_ticket': venta.get('ticket') and str(venta['ticket']),
        }
        lineas = [{
            'producto_id': str(l.get('producto_id') or ''),
            'ean': l.get('ean'),
            'nombre': l.get('nombre') or '',
            'unidad': l.get('unidad'),
            'cantidad': float(l.get('cantidad') or 0),
            'precio': _pesos(l.get('precio_centavos')),
        } for l in (p.get('lineas') or [])]

        order = self.sudo().search([('company_id', '=', company.id), ('code', '=', code)], limit=1)
        if order:
            old_state = order.state
            order.write(vals)
            if order._lines_key() != [(l['producto_id'], l['cantidad'], l['precio']) for l in lineas]:
                order.line_ids.unlink()
                order.write({'line_ids': [fields.Command.create(l) for l in lineas]})
                order._on_lines_changed()
        else:
            if status == 'cancelado':
                # Un pedido que Odoo nunca vio y ya está cancelado no tiene nada
                # que atender.
                return self.browse()
            old_state = False
            order = self.sudo().create({**vals, 'company_id': company.id, 'code': code,
                                        'creado': _parse_iso(p.get('creado')),
                                        'line_ids': [fields.Command.create(l) for l in lineas]})
        order._link_products()
        if not order.sale_order_id and order.state != 'cancelado':
            order._create_sale_order()
        if old_state != order.state:
            order._apply_state(old_state)
        return order

    def _lines_key(self):
        return [(l.producto_id, l.cantidad, l.precio) for l in self.line_ids.sorted('id')]

    def _on_lines_changed(self):
        so = self.sale_order_id
        if so and so.state in ('draft', 'sent'):
            so.order_line.unlink()
            self._fill_sale_order(so)
            self.message_post(body=_("NexoPOS cambió los productos del pedido: se rearmó el pedido de venta."))
        elif so:
            self.activity_schedule('mail.mail_activity_data_todo', summary=_("Cambiaron los productos en NexoPOS"),
                                   note=_("El pedido de venta ya estaba confirmado y no se tocó. Revisalo."))

    def _link_products(self):
        """Por el `producto_id` de NexoPOS, que es el que vincula `nexo_pos`.
        Si el producto todavía no está vinculado, por código de barras, solo si
        hay uno en Odoo."""
        Product = self.env['product.product'].sudo()
        for line in self.line_ids.filtered(lambda l: not l.product_id):
            company_dom = [('company_id', 'in', [False, line.order_id.company_id.id])]
            product = Product.search([('nexo_pos_id', '=', line.producto_id)] + company_dom, limit=2) \
                if line.producto_id else Product
            if len(product) != 1 and line.ean:
                product = Product.search([('barcode', '=', line.ean)] + company_dom, limit=2)
            if len(product) == 1:
                line.product_id = product

    # ------------------------------------------------------------------
    # Pedido de venta
    # ------------------------------------------------------------------

    def _find_partner(self):
        Partner = self.env['res.partner'].sudo()
        phone = (self.contacto_telefono or '').strip()
        if phone:
            partner = Partner.search([('phone', '=', phone), ('company_id', 'in', [False, self.company_id.id])],
                                     limit=1)
            return partner or Partner.create({'name': self.contacto_nombre or phone, 'phone': phone,
                                              'comment': _("Cliente de NexoTienda")})
        return self.env.ref('nexo_tienda.partner_nexotienda')

    def _create_sale_order(self):
        """Un pedido de venta del equipo NexoTienda, que la caja abre y cobra
        con «Cobrar un pedido». Hasta que todos los productos estén
        vinculados no se arma: un pedido a medias se cobraría mal."""
        self.ensure_one()
        faltan = self.line_ids.filtered(lambda l: not l.product_id)
        if faltan:
            self.error = _("Productos de NexoPOS que no están vinculados en Odoo: %s. Vinculalos (Ajustes → "
                           "NexoPOS → Vincular productos) y tocá «Actualizar».",
                           ", ".join(f"{l.nombre} (id {l.producto_id})" for l in faltan))
            return
        service = self._service()
        so = self.env['sale.order'].sudo().with_company(self.company_id).create({
            'partner_id': self._find_partner().id,
            'company_id': self.company_id.id,
            'team_id': self.env.ref('nexo_tienda.crm_team_nexotienda').id,
            'client_order_ref': self.code,
            'origin': f"NexoTienda {self.code}",
            'warehouse_id': service._warehouse(self.company_id).id,
            'nexo_tienda_order_id': self.id,
        })
        self.sale_order_id = so
        self.error = False
        self._fill_sale_order(so)

    def _fill_sale_order(self, so):
        """Precio de NexoPOS, que lleva IVA (DEC-020), pasado al precio sin IVA
        de la línea según sus impuestos."""
        service = self._service()
        avisos = []
        SaleLine = self.env['sale.order.line'].sudo()
        for line in self.line_ids:
            sol = SaleLine.create({'order_id': so.id, 'product_id': line.product_id.id,
                                   'product_uom_qty': line.cantidad, 'name': line.nombre})
            price, ok = service._price_without_tax(line.precio, sol.tax_ids)
            if not ok:
                avisos.append(line.nombre)
            sol.price_unit = price
        if self.cargo:
            SaleLine.create({'order_id': so.id, 'product_id': self.env.ref('nexo_tienda.product_cargo').id,
                             'product_uom_qty': 1, 'price_unit': self.cargo, 'tax_ids': [fields.Command.clear()]})
        if avisos:
            so.message_post(body=_("Estos productos tienen un impuesto que no es un porcentaje y quedaron con el "
                                   "precio de NexoPOS tal cual: %s", ", ".join(avisos)))
        if so.currency_id.compare_amounts(so.amount_total, self.total):
            so.message_post(body=_("El total en Odoo (%(odoo)s) no es igual al de NexoPOS (%(nexopos)s). "
                                   "Revisá los impuestos de los productos. Al comprador se le cobra el de NexoPOS.",
                                   odoo=so.amount_total, nexopos=self.total))

    def _apply_state(self, old_state):
        """Lleva el pedido de venta al estado que dice NexoPOS."""
        self.ensure_one()
        if old_state:
            self.message_post(body=_("NexoPOS: %(old)s → %(new)s", old=dict(STATES).get(old_state),
                                     new=dict(STATES).get(self.state)))
        so = self.sale_order_id.sudo()
        if not so:
            return
        so = so.with_context(nexo_tienda_sync=True)
        if self.state in DESCONTADO_EN_NEXOPOS + ('entregado',) and so.state in ('draft', 'sent'):
            so.action_confirm()
        if self.state == 'entregado':
            self.entregar_pendiente = False
            self._deliver_in_odoo()
        elif self.state == 'cancelado':
            self.entregar_pendiente = False
            if self._delivered_in_odoo():
                self.activity_schedule(
                    'mail.mail_activity_data_todo', summary=_("Cancelado en NexoPOS, entregado en Odoo"),
                    note=_("NexoPOS canceló el pedido (%s) cuando en Odoo ya se había entregado o cobrado. "
                           "Revisá la plata y el stock.", self.motivo_cancelacion or ''))
            elif so.state != 'cancel':
                so._action_cancel()

    def _delivered_in_odoo(self):
        so = self.sale_order_id.sudo()
        return bool(so.pos_order_line_ids) or any(
            p.state == 'done' and p.picking_type_code == 'outgoing' for p in so.picking_ids)

    def _deliver_in_odoo(self):
        """NexoPOS dice entregado (lo entregó su pantalla, o Odoo se lo avisó):
        la entrega de Odoo se valida. El stock ya lo descontó NexoPOS al
        aceptar, así que no se vuelve a mandar (ver stock.move)."""
        so = self.sale_order_id.sudo()
        if not so or so.pos_order_line_ids:
            return  # Lo cobró la caja: el stock lo movió el Punto de Venta.
        pickings = so.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel')
                                           and p.picking_type_code == 'outgoing')
        if not pickings:
            return
        try:
            with self.env.cr.savepoint():
                for move in pickings.move_ids.filtered(lambda m: m.state not in ('done', 'cancel')):
                    move.quantity = move.product_uom_qty
                    move.picked = True
                pickings.with_context(nexo_tienda_sync=True, cancel_backorder=True)._action_done()
        except Exception as e:  # noqa: BLE001 — se avisa en el pedido, no se corta la sincronización
            _logger.exception("NexoTienda %s: no se pudo validar la entrega", self.code)
            self.activity_schedule('mail.mail_activity_data_todo', summary=_("Validá la entrega"),
                                   note=_("NexoPOS dice entregado y Odoo no pudo validar la entrega sola: %s", e))

    def _mark_delivered(self):
        """Se cobró o se entregó en Odoo: hay que avisarle a NexoPOS."""
        for order in self:
            if order.state in EN_CURSO:
                order.entregar_pendiente = True
            elif order.state == 'cancelado':
                order.activity_schedule(
                    'mail.mail_activity_data_todo', summary=_("Entregado en Odoo, cancelado en NexoPOS"),
                    note=_("El pedido está cancelado en NexoPOS. Revisá la plata y el stock."))

    # ------------------------------------------------------------------
    # Pasos hacia NexoPOS
    # ------------------------------------------------------------------

    def _post_step(self, step, body=None):
        """Pide el paso a NexoPOS y guarda el pedido que devuelve."""
        self.ensure_one()
        data = self._service()._call('POST', f"/pedidos/{quote(self.code, safe='')}/{STEP_PATH[step]}",
                                     json_body=body or {})
        self.with_company(self.company_id)._upsert(data)

    def _refresh(self):
        self.ensure_one()
        data = self._service()._call('GET', f"/pedidos/{quote(self.code, safe='')}")
        self.with_company(self.company_id)._upsert(data)

    def _step_message(self, e):
        if e.kind in UNCERTAIN:
            return _("NexoPOS no respondió y no se sabe si tomó el paso. Tocá de nuevo: repetirlo no lo duplica.")
        if e.status == 409 and isinstance(e.data, dict) and e.data.get('status'):
            return _("%(msg)s El pedido está %(state)s en NexoPOS.", msg=str(e),
                     state=dict(STATES).get(e.data['status'], e.data['status']))
        return e.user_message()

    def _step_from_ui(self, step, body=None):
        """Botones del pedido. No se usa UserError: deshace lo que se guardó,
        y después de un 409 hay que quedarse con el estado que dio NexoPOS."""
        self.ensure_one()
        self.env['nexo.tienda.order'].check_access('write')
        try:
            self._post_step(step, body)
        except NexoError as e:
            if e.kind == 'auth':
                raise UserError(e.user_message()) from e
            if e.status == 409:
                try:
                    self._refresh()
                except NexoError:
                    pass
            self.error = self._step_message(e)
            return self._notify(self.error, 'danger')
        self.error = False
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def _notify(self, message, kind='info'):
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _("NexoTienda"), 'message': message, 'type': kind, 'sticky': kind == 'danger',
            'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def _open_step(self, step):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'nexo.tienda.step', 'view_mode': 'form',
                'target': 'new', 'name': _("Aceptar pedido") if step == 'aceptar' else _("Cancelar pedido"),
                'context': {'default_order_id': self.id, 'default_step': step}}

    def action_accept(self):
        return self._open_step('aceptar')

    def action_cancel(self):
        return self._open_step('cancelar')

    def action_ready(self):
        return self._step_from_ui('listo')

    def action_on_the_way(self):
        return self._step_from_ui('en_camino')

    def action_deliver(self):
        return self._step_from_ui('entregado')

    def action_refresh(self):
        self.ensure_one()
        try:
            self._refresh()
        except NexoError as e:
            if e.kind == 'auth':
                raise UserError(e.user_message()) from e
            return self._notify(e.user_message(), 'danger')
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}

    def action_open_sale_order(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'sale.order', 'res_id': self.sale_order_id.id,
                'view_mode': 'form'}

    def action_sync_now(self):
        """Botón de la lista. Con display="always" llega con los registros
        elegidos (o ninguno), no como método de modelo: por eso no es
        @api.model (el mismo error que tuvo «Comparar ahora»)."""
        self = self.browse()
        self.check_access('write')
        if not self._enabled():
            raise UserError(_("Los pedidos de NexoTienda están apagados. Prendelos en Ajustes → NexoPOS."))
        try:
            self._send_pending()
            n = self._sync()
        except NexoError as e:
            if e.kind == 'not_found' and not e.code:
                raise UserError(_("NexoPOS todavía no tiene los pedidos para ERP (GET /pedidos da 404). "
                                  "Salen con su próximo deploy.")) from e
            raise UserError(e.user_message()) from e
        return self._notify(_("%s pedidos nuevos o con cambios.", n) if n else _("No hay pedidos nuevos."),
                            'success')

    # ------------------------------------------------------------------
    # Avisar lo que se entregó en Odoo
    # ------------------------------------------------------------------

    @api.model
    def _send_pending(self):
        """Lo que se cobró o entregó en Odoo pasa a entregado en NexoPOS, paso
        por paso, porque NexoPOS no deja saltear: recibido → aceptado → listo →
        entregado. Aceptar ahí es lo que descuenta el stock de NexoPOS.

        Repetir no duplica, así que un corte de red se reintenta en la próxima
        vuelta.
        """
        orders = self.search([('company_id', '=', self.env.company.id), ('entregar_pendiente', '=', True)])
        for order in orders:
            try:
                with self.env.cr.savepoint():
                    for _i in range(4):
                        if order.state == 'recibido':
                            order._post_step('aceptar', {'listo_para': _("Entregado en el local")})
                        elif order.state == 'aceptado':
                            order._post_step('listo')
                        elif order.state in ('listo', 'en_camino'):
                            order._post_step('entregado')
                        else:
                            break
                    order.entregar_pendiente = order.state in EN_CURSO
                    order.error = False
            except NexoError as e:
                if e.kind == 'auth':
                    raise
                order.error = order._step_message(e)
                if e.status == 409:
                    try:
                        order._refresh()
                    except NexoError:
                        pass

    @api.model
    def _cron_sync(self):
        service = self.env['nexo.pos.service']
        for company in service._companies_with_key():
            if not self._enabled(company):
                continue
            Order = self.with_company(company)
            try:
                with self.env.cr.savepoint():
                    Order._send_pending()
                    Order._sync()
            except NexoError as e:
                _logger.warning("NexoTienda (%s): no se pudieron traer los pedidos: %s", company.name, e.kind)


class NexoTiendaOrderLine(models.Model):
    _name = 'nexo.tienda.order.line'
    _description = 'Producto de un pedido de NexoTienda'
    _order = 'id'

    order_id = fields.Many2one('nexo.tienda.order', required=True, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='order_id.currency_id')
    producto_id = fields.Char('Id en NexoPOS', readonly=True)
    ean = fields.Char('EAN', readonly=True)
    nombre = fields.Char('Producto', readonly=True)
    unidad = fields.Char('Unidad', readonly=True)
    cantidad = fields.Float('Cantidad', digits='Product Unit', readonly=True)
    precio = fields.Monetary('Precio con IVA', readonly=True)
    product_id = fields.Many2one('product.product', 'Producto en Odoo', readonly=True)
