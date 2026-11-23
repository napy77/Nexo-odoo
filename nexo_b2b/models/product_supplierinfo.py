from odoo import fields, models


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
