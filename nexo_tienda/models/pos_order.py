from odoo import fields, models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    nexo_tienda_order_id = fields.Many2one('nexo.tienda.order', 'Pedido de NexoTienda', readonly=True, copy=False,
                                          index='btree_not_null')

    def action_pos_order_paid(self):
        """Se cobró en la caja un pedido de NexoTienda: se le avisa a NexoPOS
        que se entregó, en la próxima vuelta del cron."""
        res = super().action_pos_order_paid()
        self.lines.sale_order_line_id.order_id.nexo_tienda_order_id.sudo()._mark_delivered()
        return res


class PosSession(models.Model):
    _inherit = 'pos.session'

    def close_session_from_ui(self, payment_method_closing={}):  # noqa: B006 — misma firma que Odoo
        """Odoo no deja cerrar la caja con órdenes sin cobrar. Un pedido de
        NexoTienda que se retira mañana no es un olvido del cajero: sale de
        esta caja (como los pedidos para más tarde de Odoo) y vuelve a la
        próxima que se abra."""
        for session in self:
            session.order_ids.filtered(
                lambda o: o.state == 'draft' and o.nexo_tienda_order_id
                and o.nexo_tienda_order_id.state in ('recibido', 'aceptado', 'listo', 'en_camino')
            ).write({'session_id': False})
        return super().close_session_from_ui(payment_method_closing)
