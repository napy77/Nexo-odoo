from odoo import models


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _nexo_pos_qty_to_send(self, qty, sale):
        """Lo que sale por un pedido de NexoTienda ya lo descontó NexoPOS al
        aceptarlo (o lo descuenta al avisarle que se entregó): mandarlo de
        nuevo lo contaría dos veces. Lo que entra (una devolución) sí se manda.
        """
        qty = super()._nexo_pos_qty_to_send(qty, sale)
        if not sale:
            return qty
        if self.sale_line_id.order_id.nexo_tienda_order_id:
            return 0.0
        picking = self.picking_id
        # La entrega del Punto de Venta: una por pedido, o una por sesión si la
        # caja mueve el stock al cerrar. En la misma puede haber productos del
        # mostrador, que sí se mandan.
        pos_orders = picking.pos_order_id or picking.pos_session_id.order_ids
        if not pos_orders:
            return qty
        tienda = sum(
            line.qty for line in pos_orders.lines
            if line.product_id == self.product_id and line.qty > 0
            and line.sale_order_line_id.order_id.nexo_tienda_order_id
        )
        return max(qty - tienda, 0.0)


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        res = super()._action_done()
        if self.env.context.get('nexo_tienda_sync'):
            return res
        for so in self.filtered(lambda p: p.picking_type_code == 'outgoing').sale_id:
            if so.nexo_tienda_order_id and all(p.state in ('done', 'cancel') for p in so.picking_ids
                                               if p.picking_type_code == 'outgoing'):
                so.nexo_tienda_order_id.sudo()._mark_delivered()
        return res
