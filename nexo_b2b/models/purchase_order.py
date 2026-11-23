"""Pedidos de compra enviados a NexoB2B y seguimiento de su estado.

Contrato verificado contra el código de NexoB2B (apps/backend/src/api/store):
- POST /store/ordenes                 → {mayorista_id, items[{presentacion_id, cantidad}], notas,
                                         medio_pago_id, transporte_id}. El precio lo pone NexoB2B,
                                         nunca el que manda el comercio. Exige alta aceptada (403).
- GET  /store/ordenes, /store/ordenes/:id, /store/ordenes/:id/documentos
- PUT  /store/ordenes/:id/cancelar    → solo en «cargada» o «devuelto».
- GET  /store/mayoristas/:id/medios-pago, /store/mayoristas/:id/transportes

NexoB2B no avisa a los comercios por API (solo email y push al celular): Odoo
consulta cada pedido abierto con un cron (DEC-011).
"""
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.nexo_core.lib.client import NexoError

from .nexo_b2b_service import SERVICE


def _num(value):
    """NexoB2B manda los importes como texto (`"0.00"`, `"66550"`) porque son
    numeric de Postgres. Sumarlos sin convertir concatena (`"0.000.00"`)."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

_logger = logging.getLogger(__name__)

# Estados de la orden en NexoB2B (modules/orden/models/orden.ts) y cómo se le
# cuentan al comprador.
ESTADOS = [
    ('cargada', 'Enviado, esperando al mayorista'),
    ('confirmado', 'Confirmado por el mayorista'),
    ('armando', 'En preparación'),
    ('listo', 'Listo para despachar'),
    ('en_transporte', 'En camino'),
    ('entregado', 'Entregado'),
    ('devuelto', 'Devuelto por el mayorista'),
    ('cancelado', 'Cancelado'),
]
FINALES = ('entregado', 'cancelado')
# Desde estos estados el comercio puede cancelar (api/store/ordenes/[id]/cancelar).
CANCELABLES = ('cargada', 'devuelto')
# A partir de acá el mayorista ya aceptó el pedido.
ACEPTADOS = ('confirmado', 'armando', 'listo', 'en_transporte', 'entregado')
# Si el envío se corta sin respuesta, la orden pudo haberse creado igual:
# NexoB2B no tiene clave de idempotencia y se busca por la referencia en las notas.
INCIERTOS = ('timeout', 'network', 'server', 'invalid_response')
REFERENCIA = "Pedido de compra Odoo %s"


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    nexo_mayorista_id = fields.Char(related='partner_id.nexo_mayorista_id')
    nexo_orden_id = fields.Char('Id de la orden en NexoB2B', copy=False, index='btree_not_null', readonly=True)
    nexo_numero = fields.Char('Número NexoB2B', copy=False, readonly=True)
    nexo_estado = fields.Selection(ESTADOS, 'Estado en NexoB2B', copy=False, readonly=True)
    nexo_envio_incierto = fields.Boolean(
        copy=False, readonly=True,
        help="El último envío se cortó sin respuesta: antes de reenviar se busca si la orden llegó igual.")
    nexo_sent_at = fields.Datetime('Enviado a NexoB2B', copy=False, readonly=True)
    nexo_sync_at = fields.Datetime('Última consulta a NexoB2B', copy=False, readonly=True)
    nexo_medio_pago = fields.Char('Medio de pago', copy=False, readonly=True)
    nexo_transporte = fields.Char('Transporte', copy=False, readonly=True)
    nexo_total = fields.Float('Total en NexoB2B', copy=False, readonly=True, digits=(16, 2))
    nexo_recargos = fields.Float('Medio de pago y transporte', copy=False, readonly=True, digits=(16, 2))
    nexo_descuento = fields.Float('Descuento', copy=False, readonly=True, digits=(16, 2))
    nexo_bultos = fields.Integer('Bultos', copy=False, readonly=True)
    nexo_peso_kg = fields.Float('Peso (kg)', copy=False, readonly=True)
    nexo_guia = fields.Char('Número de guía', copy=False, readonly=True)
    nexo_mensaje = fields.Text('Mensaje del mayorista', copy=False, readonly=True)
    nexo_pagada = fields.Boolean('Pagada en NexoB2B', copy=False, readonly=True)
    nexo_facturada = fields.Boolean('Facturada en NexoB2B', copy=False, readonly=True)
    # Ids de documentos ya avisados, separados por coma.
    nexo_documentos_vistos = fields.Char(copy=False, readonly=True)

    # ------------------------------------------------------------------
    # Enviar
    # ------------------------------------------------------------------

    def _nexo_items(self):
        """[{presentacion_id, cantidad}] desde las líneas, o UserError con lo
        que falta. Cada línea tiene que estar en una presentación del mayorista
        (la unidad de la línea es la de su línea de proveedor, DEC-008)."""
        self.ensure_one()
        items, problemas = [], []
        for line in self.order_line.filtered(lambda l: not l.display_type):
            if not line.product_qty:
                continue
            seller = line._nexo_seller()
            ambiguas = not seller and len(line._nexo_candidates().filtered(lambda s: s.uom_id == line.uom_id)) > 1
            if ambiguas:
                problemas.append(_("«%(producto)s»: hay varias presentaciones en %(unidad)s; elegí cuál en la columna "
                                   "«Presentación NexoB2B».", producto=line.product_id.display_name,
                                   unidad=line.uom_id.name))
                continue
            if not seller:
                opciones = self._nexo_presentaciones(line.product_id)
                problemas.append(_("«%(producto)s» en %(unidad)s: %(detalle)s", producto=line.product_id.display_name,
                                   unidad=line.uom_id.name,
                                   detalle=(_("este mayorista lo vende en %s", ", ".join(opciones)) if opciones else
                                            _("no está agregado desde el catálogo de NexoB2B para este mayorista"))))
                continue
            if line.product_qty != int(line.product_qty) or line.product_qty < 0:
                problemas.append(_("«%(producto)s»: la cantidad tiene que ser un número entero de %(unidad)s.",
                                   producto=line.product_id.display_name, unidad=line.uom_id.name))
                continue
            items.append({'presentacion_id': seller.nexo_presentacion_id, 'cantidad': int(line.product_qty)})
        if problemas:
            raise UserError(_("No se puede enviar a NexoB2B:\n%s", "\n".join("• " + p for p in problemas)))
        if not items:
            raise UserError(_("El pedido no tiene líneas para enviar."))
        return items

    def _nexo_presentaciones(self, product):
        sellers = product.product_tmpl_id.seller_ids.filtered(
            lambda s: s.partner_id == self.partner_id and s.nexo_presentacion_id)
        return [f"{s.nexo_presentacion_nombre or s.uom_id.name} ({s.uom_id.name})" for s in sellers]

    def _nexo_check_can_send(self):
        self.ensure_one()
        self.env['nexo.b2b.service']._check_user()
        if not self.nexo_mayorista_id:
            raise UserError(_("El proveedor %s no es un mayorista de NexoB2B.", self.partner_id.display_name))
        if self.nexo_orden_id:
            raise UserError(_("Este pedido ya se envió a NexoB2B (%s).", self.nexo_numero))
        if self.state not in ('draft', 'sent'):
            raise UserError(_("Solo se envía a NexoB2B una solicitud de presupuesto, no un pedido ya confirmado."))

    def action_nexo_send(self):
        """Abre la confirmación del envío (medio de pago, transporte, notas)."""
        self.ensure_one()
        self._nexo_check_can_send()
        if self.nexo_envio_incierto and self._nexo_reconcile():
            return self._nexo_notify(_("El pedido ya había llegado a NexoB2B como %s.", self.nexo_numero))
        self._nexo_items()
        return self.env['nexo.b2b.order.send']._open_for(self)

    def _nexo_send(self, medio_pago_id=None, transporte_id=None, notas=None):
        """Crea la orden en NexoB2B. Es un pedido real al mayorista."""
        self.ensure_one()
        self._nexo_check_can_send()
        if self.nexo_envio_incierto and self._nexo_reconcile():
            return self._nexo_notify(_("El pedido ya había llegado a NexoB2B como %s.", self.nexo_numero))
        body = {
            'mayorista_id': self.nexo_mayorista_id,
            'items': self._nexo_items(),
            # La referencia permite encontrar la orden si el envío se corta.
            'notas': "\n".join(filter(None, [REFERENCIA % self.name, (notas or '').strip()])),
        }
        if medio_pago_id:
            body['medio_pago_id'] = medio_pago_id
        if transporte_id:
            body['transporte_id'] = transporte_id
        service = self.env['nexo.b2b.service']
        try:
            data = service._call('POST', '/store/ordenes', json_body=body)
        except NexoError as e:
            if e.kind in INCIERTOS:
                # No se lanza el error: se perdería la marca al deshacer la transacción.
                self.nexo_envio_incierto = True
                self.message_post(body=_("El envío a NexoB2B no tuvo respuesta (%s). Antes de reenviarlo, "
                                         "Odoo va a buscar si llegó igual.", e.user_message()),
                                  subtype_xmlid='mail.mt_note')
                return self._nexo_notify(_("NexoB2B no respondió. Puede que el pedido haya llegado igual: "
                                           "tocá «Actualizar estado» antes de volver a enviarlo."), 'warning')
            raise UserError(e.user_message()) from e
        orden = data.get('orden') or {}
        if not orden.get('id'):
            raise UserError(NexoError('invalid_response').user_message())
        self.write({'nexo_sent_at': fields.Datetime.now(), 'nexo_envio_incierto': False})
        if self.state == 'draft':
            self.state = 'sent'
        self._nexo_apply(orden, notify=False)
        self.message_post(body=self._nexo_summary(orden, _("Enviado a NexoB2B")), subtype_xmlid='mail.mt_note')
        return self._nexo_notify(_("Pedido %(numero)s enviado a %(mayorista)s.", numero=self.nexo_numero,
                                   mayorista=self.partner_id.name), 'success')

    def _nexo_reconcile(self):
        """Busca en NexoB2B una orden con la referencia de este pedido. True si
        la encontró y la vinculó."""
        self.ensure_one()
        try:
            data = self.env['nexo.b2b.service']._call('GET', '/store/ordenes')
        except NexoError as e:
            raise UserError(_("No se pudo comprobar si el pedido llegó a NexoB2B: %s", e.user_message())) from e
        referencia = REFERENCIA % self.name
        for orden in data.get('ordenes') or []:
            if orden.get('mayorista_id') == self.nexo_mayorista_id and \
                    (orden.get('notas') or '').splitlines()[:1] == [referencia]:
                self.write({'nexo_envio_incierto': False, 'nexo_sent_at': fields.Datetime.now()})
                if self.state == 'draft':
                    self.state = 'sent'
                self._nexo_apply(orden, notify=False)
                self.message_post(body=self._nexo_summary(orden, _("Vinculado a la orden de NexoB2B")),
                                  subtype_xmlid='mail.mt_note')
                return True
        self.nexo_envio_incierto = False
        return False

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def action_nexo_sync(self):
        self.ensure_one()
        if self.nexo_envio_incierto and not self.nexo_orden_id:
            found = self._nexo_reconcile()
            return self._nexo_notify(
                _("El pedido estaba en NexoB2B: %s.", self.nexo_numero) if found else
                _("El pedido no llegó a NexoB2B. Ya lo podés volver a enviar."), 'success' if found else 'warning')
        try:
            changed = self._nexo_sync()
        except NexoError as e:
            raise UserError(e.user_message()) from e
        label = dict(ESTADOS).get(self.nexo_estado, self.nexo_estado)
        return self._nexo_notify(_("Estado en NexoB2B: %s.", label) if changed is not None else _("Sin cambios."))

    def _nexo_sync(self):
        """Consulta la orden y aplica los cambios. Devuelve el estado anterior si
        cambió, o None."""
        self.ensure_one()
        data = self.env['nexo.b2b.service']._call('GET', f'/store/ordenes/{self.nexo_orden_id}')
        return self._nexo_apply(data.get('orden') or {}, notify=True)

    def _nexo_apply(self, orden, notify=True):
        self.ensure_one()
        anterior = self.nexo_estado
        estado = orden.get('estado')
        vals = {
            'nexo_orden_id': orden.get('id') or self.nexo_orden_id,
            'nexo_numero': orden.get('numero') or self.nexo_numero,
            'nexo_sync_at': fields.Datetime.now(),
            'nexo_medio_pago': orden.get('medio_pago_nombre') or False,
            'nexo_transporte': orden.get('transporte_nombre') or False,
            'nexo_total': _num(orden.get('total')),
            'nexo_recargos': _num(orden.get('costo_medio_pago')) + _num(orden.get('costo_transporte')),
            'nexo_descuento': _num(orden.get('monto_descuento')),
            'nexo_bultos': int(_num(orden.get('cantidad_bultos'))),
            'nexo_peso_kg': _num(orden.get('peso_kg')),
            'nexo_guia': orden.get('numero_guia') or False,
            'nexo_mensaje': orden.get('mensaje_mayorista') or False,
            'nexo_pagada': bool(orden.get('is_pagada')),
            'nexo_facturada': bool(orden.get('is_facturada')),
        }
        if estado in dict(ESTADOS):
            vals['nexo_estado'] = estado
        if not self.partner_ref and orden.get('numero'):
            vals['partner_ref'] = orden['numero']
        self.write(vals)
        self._nexo_update_prices(orden.get('items') or [])
        if estado == anterior or estado not in dict(ESTADOS):
            if notify:
                self._nexo_new_documents()
            return None
        self._nexo_on_state(estado, notify)
        return anterior

    def _nexo_update_prices(self, items):
        """El precio de cada línea es el que fijó NexoB2B (lista del comercio
        aplicada), no el de Odoo."""
        precios = {i['presentacion_id']: _num(i['precio_unitario']) for i in items
                   if i.get('presentacion_id') and i.get('precio_unitario') not in (None, '')}
        if not precios or self.state not in ('draft', 'sent', 'to approve'):
            return
        for line in self.order_line:
            seller = line._nexo_seller()
            precio = precios.get(seller.nexo_presentacion_id) if seller else None
            if precio is not None and line.price_unit != precio:
                line.price_unit = precio

    def _nexo_refresh_prices(self):
        """Relee de NexoB2B el precio de cada presentación antes de enviar, para
        que el comprador vea el precio actual y no el de cuando armó el borrador.
        NexoB2B lo vuelve a calcular al crear la orden; esto solo lo adelanta.

        Devuelve (cambios, avisos), dos listas de textos. Si NexoB2B no
        responde, no bloquea: se envía con el último precio conocido."""
        self.ensure_one()
        service = self.env['nexo.b2b.service']
        cambios, avisos = [], []
        lines = self.order_line.filtered(lambda l: not l.display_type and l.product_id)
        for template, tmpl_lines in lines.grouped(lambda l: l.product_id.product_tmpl_id).items():
            if not template.nexo_b2b_id:
                continue
            try:
                producto = service._fetch_product(template.nexo_b2b_id, template.barcode, self.nexo_mayorista_id)
            except NexoError as e:
                if e.kind == 'auth':
                    raise UserError(e.user_message()) from e
                avisos.append(_("No se pudo releer el precio en NexoB2B (%s): se usa el último conocido.",
                                e.user_message()))
                break
            mayorista = next((m for m in (producto or {}).get('mayoristas') or []
                              if m.get('mayorista_id') == self.nexo_mayorista_id), None)
            presentaciones = {pr.get('id'): pr for pr in (mayorista or {}).get('presentaciones') or []}
            for line in tmpl_lines:
                seller = line._nexo_seller()
                if not seller:
                    continue
                pr = presentaciones.get(seller.nexo_presentacion_id)
                nombre = f"{line.product_id.name} · {seller.nexo_presentacion_nombre or seller.uom_id.name}"
                if not pr or pr.get('precio') in (None, ''):
                    avisos.append(_("%s ya no figura en NexoB2B para este mayorista: NexoB2B puede rechazar el pedido.",
                                    nombre))
                    continue
                precio = _num(pr['precio'])
                if seller.price != precio:
                    seller.price = precio
                if self.currency_id.compare_amounts(line.price_unit, precio):
                    cambios.append(_("%(nombre)s: %(antes)s → %(ahora)s", nombre=nombre,
                                     antes=self.currency_id.format(line.price_unit),
                                     ahora=self.currency_id.format(precio)))
                    line.price_unit = precio
        if cambios:
            self.message_post(body=_("Precios actualizados desde NexoB2B antes de enviar: %s", "; ".join(cambios)),
                              subtype_xmlid='mail.mt_note')
        return cambios, avisos

    def _nexo_on_state(self, estado, notify):
        label = dict(ESTADOS)[estado]
        if estado in ACEPTADOS and self.state in ('draft', 'sent'):
            self.with_context(nexo_from_sync=True).button_confirm()
        if estado == 'cancelado' and self.state != 'cancel':
            try:
                self.with_context(nexo_from_sync=True).button_cancel()
            except UserError as e:
                self.message_post(body=_("NexoB2B canceló el pedido pero Odoo no pudo cancelarlo: %s", e),
                                  subtype_xmlid='mail.mt_note')
        if not notify:
            return
        self._nexo_new_documents()
        detalle = []
        if estado == 'listo' and self.nexo_bultos:
            detalle.append(_("%s bultos", self.nexo_bultos) + (_(", %s kg", self.nexo_peso_kg) if self.nexo_peso_kg else ""))
        if estado == 'en_transporte':
            if self.nexo_transporte:
                detalle.append(self.nexo_transporte)
            if self.nexo_guia:
                detalle.append(_("guía %s", self.nexo_guia))
        if estado == 'devuelto' and self.nexo_mensaje:
            detalle.append(_("«%s»", self.nexo_mensaje))
        texto = _("%(numero)s: %(estado)s", numero=self.nexo_numero, estado=label)
        if detalle:
            texto += " — " + ", ".join(detalle)
        # Nota interna con mención al comprador: le llega a su bandeja de Odoo
        # y no se le manda un correo al mayorista (que sigue el pedido).
        partners = self.user_id.partner_id.ids
        self.message_post(body=texto, subtype_xmlid='mail.mt_note', partner_ids=partners)
        user = self.user_id or self.env.user
        if estado == 'devuelto':
            self.activity_schedule(
                'mail.mail_activity_data_todo', user_id=user.id,
                summary=_("El mayorista devolvió el pedido %s", self.nexo_numero),
                note=_("Revisá el mensaje del mayorista. NexoB2B solo deja reenviarlo cambiando precios a mano, "
                       "así que lo seguro es cancelarlo y hacer un pedido nuevo."))
        if estado == 'entregado' and 'picking_ids' in self._fields:
            pendientes = self.picking_ids.filtered(lambda p: p.state not in ('done', 'cancel'))
            if pendientes:
                self.activity_schedule(
                    'mail.mail_activity_data_todo', user_id=user.id,
                    summary=_("Llegó el pedido %s: validá la recepción", self.nexo_numero),
                    note=_("NexoB2B lo marca entregado. Contá lo que llegó y validá %s.",
                           ", ".join(pendientes.mapped('name'))))

    def _nexo_new_documents(self):
        """Remitos y facturas que subió el mayorista, como enlaces en el pedido."""
        try:
            data = self.env['nexo.b2b.service']._call('GET', f'/store/ordenes/{self.nexo_orden_id}/documentos')
        except NexoError as e:
            _logger.info("No se pudieron leer los documentos de %s: %s", self.nexo_numero, e.kind)
            return
        vistos = set(filter(None, (self.nexo_documentos_vistos or '').split(',')))
        service = self.env['nexo.b2b.service']
        nuevos = [d for d in data.get('documentos') or [] if d.get('id') and d['id'] not in vistos]
        if not nuevos:
            return
        links = Markup(", ").join(
            Markup('<a href="%s" target="_blank" rel="noopener">%s</a>') % (
                service._image_url(d.get('url')), d.get('nombre') or d.get('tipo') or _("documento"))
            for d in nuevos)
        self.message_post(body=Markup("%s %s") % (_("Documentos de NexoB2B:"), links),
                          subtype_xmlid='mail.mt_note', partner_ids=self.user_id.partner_id.ids)
        self.nexo_documentos_vistos = ",".join(sorted(vistos | {d['id'] for d in nuevos}))

    @api.model
    def _cron_nexo_sync(self):
        """Consulta los pedidos abiertos en NexoB2B, compañía por compañía."""
        orders = self.search([('nexo_orden_id', '!=', False), ('nexo_estado', 'not in', FINALES)])
        for company, company_orders in orders.grouped('company_id').items():
            env_company = company_orders.with_company(company)
            if not self.env['nexo.token'].with_company(company)._valid_token(SERVICE, company):
                _logger.info("NexoB2B: %s no tiene sesión activa; no se consultan sus pedidos.", company.name)
                continue
            for order in env_company:
                try:
                    with self.env.cr.savepoint():
                        order._nexo_sync()
                except NexoError as e:
                    _logger.info("NexoB2B: no se pudo consultar %s (%s)", order.name, e.kind)
                    if e.kind == 'auth':
                        break

    # ------------------------------------------------------------------
    # Cancelar desde Odoo
    # ------------------------------------------------------------------

    def button_cancel(self):
        if not self.env.context.get('nexo_from_sync'):
            for order in self.filtered(lambda o: o.nexo_orden_id and o.nexo_estado not in FINALES):
                if order.nexo_estado not in CANCELABLES:
                    raise UserError(_(
                        "%(mayorista)s ya está procesando el pedido %(numero)s (%(estado)s). NexoB2B no deja "
                        "cancelarlo desde el comercio: hablalo con el mayorista.",
                        mayorista=order.partner_id.name, numero=order.nexo_numero,
                        estado=dict(ESTADOS)[order.nexo_estado]))
                try:
                    data = self.env['nexo.b2b.service']._call('PUT', f'/store/ordenes/{order.nexo_orden_id}/cancelar')
                except NexoError as e:
                    raise UserError(_("NexoB2B no canceló el pedido: %s", e.user_message())) from e
                order._nexo_apply(data.get('orden') or {'estado': 'cancelado'}, notify=False)
                order.message_post(body=_("Cancelado también en NexoB2B."), subtype_xmlid='mail.mt_note')
        return super().button_cancel()

    # ------------------------------------------------------------------
    # Ayudas
    # ------------------------------------------------------------------

    def _nexo_summary(self, orden, titulo):
        partes = [_("%(titulo)s: %(numero)s", titulo=titulo, numero=orden.get('numero') or '')]
        if _num(orden.get('total')):
            partes.append(_("total %s", self.currency_id.format(_num(orden['total']))))
        if orden.get('medio_pago_nombre'):
            partes.append(orden['medio_pago_nombre'])
        if orden.get('transporte_nombre'):
            partes.append(orden['transporte_nombre'])
        return " · ".join(str(p) for p in partes)

    def _nexo_notify(self, message, kind='info'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': message, 'type': kind, 'sticky': kind == 'warning',
                       'next': {'type': 'ir.actions.act_window_close'}},
        }


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    # Dos presentaciones pueden tener la misma unidad en Odoo (por ejemplo,
    # «Botella» y «Caja» con factor 1 en NexoB2B): por eso la línea guarda cuál es.
    nexo_seller_id = fields.Many2one(
        'product.supplierinfo', 'Presentación NexoB2B', copy=True, index='btree_not_null',
        domain="[('product_tmpl_id.product_variant_ids', 'in', product_id), ('partner_id', '=', partner_id), "
               "('nexo_presentacion_id', '!=', False)]")

    @api.onchange('nexo_seller_id')
    def _onchange_nexo_seller_id(self):
        if self.nexo_seller_id:
            self.uom_id = self.nexo_seller_id.uom_id
            self.price_unit = self.nexo_seller_id.price

    def _nexo_candidates(self):
        self.ensure_one()
        if not self.product_id:
            return self.env['product.supplierinfo']
        return self.product_id.product_tmpl_id.seller_ids.filtered(
            lambda s: s.partner_id == self.order_id.partner_id and s.nexo_presentacion_id)

    def _nexo_seller(self):
        """La presentación de NexoB2B de esta línea: la elegida, o la única de
        ese mayorista con la unidad de la línea. Vacío si no hay o es ambigua."""
        self.ensure_one()
        if self.nexo_seller_id and self.nexo_seller_id.partner_id == self.order_id.partner_id:
            return self.nexo_seller_id
        by_uom = self._nexo_candidates().filtered(lambda s: s.uom_id == self.uom_id)
        return by_uom if len(by_uom) == 1 else self.env['product.supplierinfo']
