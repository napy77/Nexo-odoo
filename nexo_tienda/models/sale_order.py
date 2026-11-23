from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    nexo_tienda_order_id = fields.Many2one('nexo.tienda.order', 'Pedido de NexoTienda', readonly=True, copy=False,
                                          index='btree_not_null')
    nexo_tienda_no_cobrar = fields.Boolean(related='nexo_tienda_order_id.no_cobrar')
    nexo_tienda_pago = fields.Char(related='nexo_tienda_order_id.pago_texto')
    nexo_tienda_state = fields.Selection(related='nexo_tienda_order_id.state', string='Estado en NexoTienda')
