from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    nexo_mayorista_id = fields.Char(
        'Id de mayorista en NexoB2B', index='btree_not_null', copy=False,
        help="Vínculo con el mayorista de NexoB2B (may_…). Permite reconocerlo al agregar productos.")
