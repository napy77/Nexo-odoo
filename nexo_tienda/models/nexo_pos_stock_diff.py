from odoo import api, models

from .nexo_tienda_order import DESCONTADO_EN_NEXOPOS


class NexoPosStockDiff(models.Model):
    _inherit = 'nexo.pos.stock.diff'

    @api.model
    def _nexo_pos_reserved(self, products):
        """Pedidos aceptados en NexoPOS que Odoo todavía no entregó."""
        res = super()._nexo_pos_reserved(products)
        lines = self.env['sale.order.line'].search([
            ('company_id', '=', self.env.company.id),
            ('product_id', 'in', products.ids),
            ('order_id.state', '=', 'sale'),
            ('order_id.nexo_tienda_order_id.state', 'in', DESCONTADO_EN_NEXOPOS),
        ])
        for line in lines:
            falta = line.product_uom_qty - line.qty_delivered
            if falta > 0:
                qty = line.product_uom_id._compute_quantity(falta, line.product_id.uom_id)
                res[line.product_id.id] = res.get(line.product_id.id, 0.0) + qty
        return res
