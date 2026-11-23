from odoo import api, fields, models


class ProductSupplierinfo(models.Model):
    _inherit = 'product.supplierinfo'

    # Presentación del mayorista (producto_mayorista_presentacion): es la que
    # se usa para pedir y la clave para no duplicar la línea.
    nexo_presentacion_id = fields.Char('Presentación en NexoB2B', index='btree_not_null', copy=False)
    # Presentación del maestro (producto_maestro_presentacion).
    nexo_presentacion_maestro_id = fields.Char('Presentación maestra en NexoB2B', copy=False)
    nexo_presentacion_nombre = fields.Char('Presentación')
    nexo_factor = fields.Float('Unidades por presentación', digits=0)
    nexo_ean_propio = fields.Char('EAN de la presentación')

    @api.depends_context('nexo_presentacion_label', 'use_simplified_supplier_name')
    def _compute_display_name(self):
        super()._compute_display_name()
        # En la columna «Presentación NexoB2B» del pedido se ve la presentación,
        # no el proveedor (que es el mismo en todas). La vista manda también
        # `use_simplified_supplier_name`: con Inventario instalado,
        # purchase_stock arma su propio nombre y solo llama a super() con esa clave.
        if not self.env.context.get('nexo_presentacion_label'):
            return
        for seller in self.filtered('nexo_presentacion_id'):
            nombre = seller.nexo_presentacion_nombre or seller.uom_id.name
            seller.display_name = f"{nombre} · {seller.currency_id.format(seller.price)}"
