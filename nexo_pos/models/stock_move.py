from odoo import models


class StockMove(models.Model):
    _inherit = 'stock.move'

    def _action_done(self, cancel_backorder=False):
        moves = super()._action_done(cancel_backorder=cancel_backorder)
        moves._nexo_pos_enqueue()
        return moves

    def _nexo_pos_enqueue(self):
        """Cada movimiento que entra o sale del depósito que comparte stock con
        NexoPOS queda en la cola como ajuste.

        `nexo_pos_skip` en el contexto es para lo que NexoPOS ya descontó por su
        cuenta (un pedido de NexoTienda aceptado allá): mandarlo de nuevo lo
        contaría dos veces.
        """
        if self.env.context.get('nexo_pos_skip'):
            return
        moves = self.filtered(lambda m: m.state == 'done' and m.product_id.nexo_pos_id and m.quantity)
        if not moves:
            return
        Queue = self.env['nexo.pos.stock.queue'].sudo()
        service = self.env['nexo.pos.service']
        vals = []
        for company, company_moves in moves.grouped('company_id').items():
            if not self.env['nexo.token']._get('nexopos', company) or not service._push_enabled(company):
                continue
            stock_loc = service._warehouse(company).lot_stock_id
            if not stock_loc:
                continue
            for move in company_moves:
                sale = move.location_id._child_of(stock_loc)
                entra = move.location_dest_id._child_of(stock_loc)
                if sale == entra:
                    continue
                qty = move.uom_id._compute_quantity(move.quantity, move.product_id.uom_id)
                vals.append({'company_id': company.id, 'product_id': move.product_id.id, 'move_id': move.id,
                             'cantidad': qty if entra else -qty})
        if vals:
            Queue.create(vals)
