from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.nexo_core.lib.client import NexoError

from .purchase_order import _num


class NexoB2bOrderOption(models.TransientModel):
    """Un medio de pago o transporte que ofrece el mayorista, para elegirlo al
    enviar. Lo que devuelve NexoB2B se copia acá solo para la ventana."""
    _name = 'nexo.b2b.order.option'
    _description = 'Opción de envío de NexoB2B'
    _order = 'sequence, id'

    wizard_id = fields.Many2one('nexo.b2b.order.send', required=True, ondelete='cascade')
    kind = fields.Selection([('medio_pago', 'Medio de pago'), ('transporte', 'Transporte')], required=True)
    nexo_id = fields.Char(required=True)
    name = fields.Char(required=True)
    porcentaje = fields.Float(digits=(5, 2))
    sequence = fields.Integer()

    @api.depends('name', 'porcentaje')
    def _compute_display_name(self):
        for option in self:
            option.display_name = (f"{option.name} (+{option.porcentaje:g}%)" if option.porcentaje
                                   else option.name)


class NexoB2bOrderSend(models.TransientModel):
    _name = 'nexo.b2b.order.send'
    _description = 'Enviar pedido a NexoB2B'

    order_id = fields.Many2one('purchase.order', required=True, ondelete='cascade')
    partner_id = fields.Many2one(related='order_id.partner_id')
    partner_name = fields.Char(related='order_id.partner_id.name')
    amount_untaxed = fields.Monetary(related='order_id.amount_untaxed')
    currency_id = fields.Many2one(related='order_id.currency_id')
    line_count = fields.Integer(compute='_compute_line_count')
    option_ids = fields.One2many('nexo.b2b.order.option', 'wizard_id')
    medio_pago_id = fields.Many2one(
        'nexo.b2b.order.option', 'Medio de pago',
        domain="[('wizard_id', '=', id), ('kind', '=', 'medio_pago')]")
    transporte_id = fields.Many2one(
        'nexo.b2b.order.option', 'Transporte',
        domain="[('wizard_id', '=', id), ('kind', '=', 'transporte')]")
    notas = fields.Text('Notas para el mayorista')
    precio_cambios = fields.Text(readonly=True)
    precio_avisos = fields.Text(readonly=True)
    has_medios_pago = fields.Boolean(compute='_compute_has_options')
    has_transportes = fields.Boolean(compute='_compute_has_options')

    @api.depends('order_id.order_line')
    def _compute_line_count(self):
        for wizard in self:
            wizard.line_count = len(wizard.order_id.order_line.filtered(lambda l: not l.display_type and l.product_qty))

    @api.depends('option_ids')
    def _compute_has_options(self):
        for wizard in self:
            kinds = set(wizard.option_ids.mapped('kind'))
            wizard.has_medios_pago = 'medio_pago' in kinds
            wizard.has_transportes = 'transporte' in kinds

    @api.model
    def _open_for(self, order):
        service = self.env['nexo.b2b.service']
        cambios, avisos = order._nexo_refresh_prices()
        options = []
        for kind, path, key in (('medio_pago', 'medios-pago', 'medios_pago'),
                                ('transporte', 'transportes', 'transportes')):
            try:
                data = service._call('GET', f'/store/mayoristas/{order.nexo_mayorista_id}/{path}')
            except NexoError as e:
                if e.kind == 'auth':
                    raise UserError(e.user_message()) from e
                data = {}
            for seq, item in enumerate(data.get(key) or []):
                if item.get('id') and item.get('nombre'):
                    options.append((0, 0, {'kind': kind, 'nexo_id': item['id'], 'name': item['nombre'],
                                           'porcentaje': _num(item.get('porcentaje_costo')), 'sequence': seq}))
        wizard = self.create({'order_id': order.id, 'option_ids': options,
                              'precio_cambios': '\n'.join(cambios) or False,
                              'precio_avisos': '\n'.join(avisos) or False})
        return {
            'type': 'ir.actions.act_window',
            'name': _("Enviar a NexoB2B"),
            'res_model': self._name,
            'res_id': wizard.id,
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
        }

    def action_send(self):
        self.ensure_one()
        return self.order_id._nexo_send(
            medio_pago_id=self.medio_pago_id.nexo_id or None,
            transporte_id=self.transporte_id.nexo_id or None,
            notas=self.notas,
        )
