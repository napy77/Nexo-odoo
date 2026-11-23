import logging
from datetime import timedelta

from odoo import _, api, fields, models

from odoo.addons.nexo_core.lib.client import NexoError

from .nexo_pos_service import BATCH, UNCERTAIN

_logger = logging.getLogger(__name__)


class NexoPosStockQueue(models.Model):
    """Lo que cambió el stock en Odoo y falta contarle a NexoPOS.

    Se manda en modo `ajuste` (suma o resta), nunca `absoluto`: NexoPOS
    también descuenta al aceptar un pedido de NexoTienda, y un absoluto pisaría
    esas unidades (docs/PROPUESTA-caja-odoo-y-nexotienda.md).
    """
    _name = 'nexo.pos.stock.queue'
    _description = 'Ajuste de stock para NexoPOS'
    _order = 'id'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company,
                                 ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', 'Producto', required=True, ondelete='cascade', index=True)
    move_id = fields.Many2one('stock.move', 'Movimiento', ondelete='set null')
    cantidad = fields.Float('Cantidad', digits='Product Unit', required=True,
                            help="Cuánto subió (positivo) o bajó (negativo) el stock en Odoo.")
    state = fields.Selection(string='Estado', selection=[
        ('pending', 'Pendiente'),
        ('sent', 'Enviado'),
        ('uncertain', 'Sin respuesta'),
        ('error', 'Rechazado'),
    ], default='pending', required=True, index=True)
    sent_at = fields.Datetime('Enviado', readonly=True)
    message = fields.Char('Aviso', readonly=True)

    @api.model
    def _send_pending(self):
        """Manda lo pendiente, sumado por producto, de a mil líneas."""
        pending = self.search([('state', '=', 'pending'), ('company_id', '=', self.env.company.id)])
        sent = 0
        by_product = pending.grouped('product_id')
        products = list(by_product)
        service = self.env['nexo.pos.service']
        for i in range(0, len(products), BATCH):
            chunk = products[i:i + BATCH]
            rows = self.browse().concat(*(by_product[p] for p in chunk))
            lineas, sin_cambio = [], self.browse()
            for product in chunk:
                total = sum(by_product[product].mapped('cantidad'))
                if not product.nexo_pos_id:
                    by_product[product].write({'state': 'error', 'message': _("El producto ya no está vinculado a NexoPOS")})
                elif product.uom_id.compare(total, 0.0) == 0:
                    sin_cambio |= by_product[product]
                else:
                    lineas.append({'id': product.nexo_pos_id, 'cantidad': product.uom_id.round(total)})
            sin_cambio.write({'state': 'sent', 'sent_at': fields.Datetime.now(), 'message': _("Se compensó en Odoo")})
            rows = rows.filtered(lambda r: r.state == 'pending')
            if not lineas:
                continue
            try:
                data = service._call('PUT', '/stock', json_body={'modo': 'ajuste', 'stock': lineas})
            except NexoError as e:
                if e.kind in UNCERTAIN:
                    # Puede haber llegado. Reintentar lo sumaría dos veces: lo
                    # resuelve la comparación de stock.
                    rows.write({'state': 'uncertain', 'message': e.user_message()})
                    continue
                if e.kind == 'client':
                    rows.write({'state': 'error', 'message': e.user_message()})
                    continue
                raise  # auth o servidor: queda pendiente para el próximo intento
            faltan = {n.get('id') for n in data.get('no_encontrados') or []}
            for row in rows:
                if row.product_id.nexo_pos_id in faltan:
                    row.write({'state': 'error', 'message': _("NexoPOS ya no tiene este producto: volvé a vincular")})
                else:
                    row.write({'state': 'sent', 'sent_at': fields.Datetime.now(), 'message': False})
                    sent += 1
        return sent

    def action_retry(self):
        """Solo después de revisar en NexoPOS que no llegó."""
        self.filtered(lambda r: r.state in ('uncertain', 'error')).write({'state': 'pending', 'message': False})

    def action_discard(self):
        self.filtered(lambda r: r.state != 'sent').write({'state': 'sent', 'message': _("Descartado a mano")})

    @api.autovacuum
    def _gc_sent(self):
        limit = fields.Datetime.now() - timedelta(days=30)
        self.search([('state', '=', 'sent'), ('sent_at', '<', limit)]).unlink()
