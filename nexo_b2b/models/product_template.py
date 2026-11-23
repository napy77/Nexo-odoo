from odoo import fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    nexo_b2b_id = fields.Char(
        'Id en NexoB2B', index='btree_not_null', copy=False,
        help="Producto maestro de NexoB2B (pm_…) del que se agregó este producto.")
    nexo_marca = fields.Char('Marca')
