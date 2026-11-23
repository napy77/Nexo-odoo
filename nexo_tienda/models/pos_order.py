from odoo import models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def action_pos_order_paid(self):
        """Se cobró en la caja un pedido de NexoTienda: se le avisa a NexoPOS
        que se entregó, en la próxima vuelta del cron."""
        res = super().action_pos_order_paid()
        self.lines.sale_order_line_id.order_id.nexo_tienda_order_id.sudo()._mark_delivered()
        return res
