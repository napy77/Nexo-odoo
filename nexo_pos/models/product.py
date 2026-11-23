from odoo import api, fields, models

# Lo que cambia el precio que ve el consumidor en NexoTienda.
PRICE_FIELDS = {'list_price', 'lst_price', 'taxes_id', 'product_template_attribute_value_ids'}


class ProductProduct(models.Model):
    _inherit = 'product.product'

    nexo_pos_id = fields.Char('Id en NexoPOS', copy=False, index='btree_not_null', readonly=True)
    nexo_pos_price_dirty = fields.Boolean('Precio sin mandar a NexoPOS', copy=False)
    nexo_pos_sync_at = fields.Datetime('Último precio mandado a NexoPOS', copy=False, readonly=True)
    nexo_pos_error = fields.Char('Aviso de NexoPOS', copy=False, readonly=True)

    def write(self, vals):
        res = super().write(vals)
        if PRICE_FIELDS & set(vals):
            self.filtered('nexo_pos_id').write({'nexo_pos_price_dirty': True})
        return res


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    nexo_pos_linked = fields.Boolean('En NexoPOS', compute='_compute_nexo_pos_linked', search='_search_nexo_pos_linked')

    @api.depends('product_variant_ids.nexo_pos_id')
    def _compute_nexo_pos_linked(self):
        for template in self:
            template.nexo_pos_linked = any(template.product_variant_ids.mapped('nexo_pos_id'))

    def _search_nexo_pos_linked(self, operator, value):
        if operator not in ('=', '!=') or not isinstance(value, bool):
            return NotImplemented
        positive = (operator == '=') == value
        return [('product_variant_ids.nexo_pos_id', '!=' if positive else '=', False)]

    def write(self, vals):
        res = super().write(vals)
        if PRICE_FIELDS & set(vals):
            self.product_variant_ids.filtered('nexo_pos_id').write({'nexo_pos_price_dirty': True})
        return res
