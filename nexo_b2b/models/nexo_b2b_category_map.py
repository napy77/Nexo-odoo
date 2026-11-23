from odoo import api, fields, models

ROOT_NAME = 'NexoB2B'


class NexoB2bCategoryMap(models.Model):
    """Taxonomía de NexoB2B → categoría de Odoo, mapeada a medida que se usa (DEC-007).

    La clave es el nivel más específico que trae el producto (subrubro, si no
    rubro, si no pasillo).
    """
    _name = 'nexo.b2b.category.map'
    _description = 'Mapeo de categorías de NexoB2B'
    _order = 'pasillo, rubro, subrubro'

    nexo_key = fields.Char('Id en NexoB2B', required=True, index=True)
    nexo_level = fields.Selection(
        [('subrubro', 'Subrubro'), ('rubro', 'Rubro'), ('pasillo', 'Pasillo')], 'Nivel', required=True)
    pasillo = fields.Char()
    rubro = fields.Char()
    subrubro = fields.Char()
    categ_id = fields.Many2one('product.category', 'Categoría de Odoo', required=True, ondelete='cascade')

    _nexo_key_uniq = models.Constraint('unique (nexo_key)', 'Esa categoría de NexoB2B ya está mapeada.')

    @api.model
    def _taxonomy(self, producto):
        """(clave, nivel, [nombres de pasillo a subrubro]) o None si no trae taxonomía."""
        levels = [
            ('pasillo', producto.get('pasillo_id'), producto.get('pasillo_nombre')),
            ('rubro', producto.get('rubro_id'), producto.get('rubro_nombre')),
            ('subrubro', producto.get('subrubro_id'), producto.get('subrubro_nombre')),
        ]
        present = [(level, key, name) for level, key, name in levels if key]
        if not present:
            return None
        level, key, _name = present[-1]
        return key, level, [name or key for _level, key, name in present]

    @api.model
    def _for(self, producto):
        taxonomy = self._taxonomy(producto)
        if not taxonomy:
            return self.browse()
        return self.search([('nexo_key', '=', taxonomy[0])], limit=1)

    @api.model
    def _create_tree(self, names):
        """Busca o crea NexoB2B / Pasillo / Rubro / Subrubro y devuelve la hoja."""
        Category = self.env['product.category']
        parent = Category.browse()
        for name in [ROOT_NAME, *names]:
            category = Category.search([('name', '=', name), ('parent_id', '=', parent.id or False)], limit=1)
            parent = category or Category.create({'name': name, 'parent_id': parent.id or False})
        return parent

    @api.model
    def _remember(self, producto, categ):
        key, level, _names = self._taxonomy(producto)
        return self.create({
            'nexo_key': key,
            'nexo_level': level,
            'pasillo': producto.get('pasillo_nombre'),
            'rubro': producto.get('rubro_nombre'),
            'subrubro': producto.get('subrubro_nombre'),
            'categ_id': categ.id,
        })
