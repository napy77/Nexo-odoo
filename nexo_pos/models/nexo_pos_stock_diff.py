from odoo import _, api, fields, models
from odoo.exceptions import UserError


class NexoPosStockDiff(models.Model):
    """Productos donde Odoo y NexoPOS no dicen el mismo stock.

    No se corrige solo: puede ser un pedido de NexoTienda aceptado en NexoPOS
    que Odoo todavía no registró, un ajuste que se cortó sin respuesta o una
    venta en el mostrador de NexoPOS. Decide el comerciante.
    """
    _name = 'nexo.pos.stock.diff'
    _description = 'Diferencia de stock con NexoPOS'
    _order = 'abs_diferencia desc, id'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company,
                                 ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', 'Producto', required=True, ondelete='cascade', readonly=True)
    barcode = fields.Char('Código de barras', related='product_id.barcode')
    uom_id = fields.Many2one(related='product_id.uom_id')
    stock_odoo = fields.Float('Odoo', digits='Product Unit', readonly=True)
    stock_nexopos = fields.Float('NexoPOS', digits='Product Unit', readonly=True)
    pendiente = fields.Float('En camino a NexoPOS', digits='Product Unit', readonly=True,
                             help="Ajustes de Odoo que todavía no se mandaron.")
    sin_respuesta = fields.Float('Sin respuesta', digits='Product Unit', readonly=True,
                                 help="Ajustes que se mandaron y no se sabe si llegaron.")
    diferencia = fields.Float('Diferencia', digits='Product Unit', readonly=True,
                              help="Odoo menos lo que tendría NexoPOS con los ajustes pendientes.")
    abs_diferencia = fields.Float(readonly=True)
    compared_at = fields.Datetime('Comparado', readonly=True)

    @api.model
    def _compare(self):
        """Lee el stock de NexoPOS y lo compara con el del depósito elegido."""
        company = self.env.company
        service = self.env['nexo.pos.service']
        remotos = {r['id']: r for r in service._fetch_products()}
        warehouse = service._warehouse(company)
        products = self.env['product.product'].search([('nexo_pos_id', 'in', list(remotos))])
        Queue = self.env['nexo.pos.stock.queue']
        cola = Queue.search([('company_id', '=', company.id), ('state', 'in', ('pending', 'uncertain'))])
        pend = {}
        for row in cola:
            key = (row.product_id.id, row.state)
            pend[key] = pend.get(key, 0.0) + row.cantidad
        self.search([('company_id', '=', company.id)]).unlink()
        now = fields.Datetime.now()
        vals = []
        for product in products.with_context(warehouse_id=warehouse.id):
            odoo = product.qty_available
            nexopos = float(remotos[product.nexo_pos_id].get('stock') or 0)
            pendiente = pend.get((product.id, 'pending'), 0.0)
            diferencia = product.uom_id.round(odoo - nexopos - pendiente)
            if product.uom_id.compare(diferencia, 0.0) == 0:
                continue
            vals.append({
                'company_id': company.id, 'product_id': product.id, 'stock_odoo': odoo,
                'stock_nexopos': nexopos, 'pendiente': pendiente,
                'sin_respuesta': pend.get((product.id, 'uncertain'), 0.0),
                'diferencia': diferencia, 'abs_diferencia': abs(diferencia), 'compared_at': now,
            })
        self.create(vals)
        self.env['ir.config_parameter'].sudo().set_str(f'nexo_pos.compared_at.{company.id}', fields.Datetime.to_string(now))
        return len(vals)

    def action_compare_now(self):
        # Botón de la lista con display="always": llega con los registros
        # elegidos (o ninguno), no como método de modelo.
        self.env['nexo.pos.service']._check_user()
        n = self._compare()
        action = self.env['ir.actions.act_window']._for_xml_id('nexo_pos.nexo_pos_stock_diff_action')
        linked = self.env['product.product'].search_count([('nexo_pos_id', '!=', False)])
        if not linked:
            action['help'] = _('<p class="o_view_nocontent_empty_folder">No hay productos vinculados con NexoPOS</p>'
                               '<p>Tocá «Vincular productos» en Ajustes → NexoPOS. Se vinculan por código de barras.</p>')
        elif not n:
            action['help'] = _('<p class="o_view_nocontent_smiling_face">Odoo y NexoPOS dicen lo mismo</p>'
                               '<p>En los %s productos vinculados.</p>', linked)
        return action

    def _resolve_uncertain(self):
        self.env['nexo.pos.stock.queue'].search([
            ('company_id', 'in', self.company_id.ids), ('product_id', 'in', self.product_id.ids),
            ('state', '=', 'uncertain'),
        ]).write({'state': 'sent', 'message': _("Resuelto en la comparación de stock")})

    def action_use_odoo(self):
        """NexoPOS queda con el número de Odoo: se manda la diferencia como
        ajuste. Escribe en el stock del comercio en NexoPOS."""
        self.env['nexo.pos.service']._check_user()
        if not self.env['nexo.pos.service']._push_enabled():
            raise UserError(_("Odoo está en «solo lee». Prendé «Mandar precios y stock a NexoPOS» en Ajustes → NexoPOS."))
        self.env['nexo.pos.stock.queue'].create([{
            'company_id': d.company_id.id, 'product_id': d.product_id.id, 'cantidad': d.diferencia,
        } for d in self])
        self._resolve_uncertain()
        self.unlink()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_use_nexopos(self):
        """Odoo queda con el número de NexoPOS: ajuste de inventario en el
        depósito, que no se vuelve a mandar a NexoPOS."""
        self.env['nexo.pos.service']._check_user()
        Quant = self.env['stock.quant'].with_context(inventory_mode=True)
        service = self.env['nexo.pos.service']
        for diff in self:
            location = service._warehouse(diff.company_id).lot_stock_id
            if not location:
                raise UserError(_("No hay depósito elegido para NexoPOS."))
            # Lo que hay en el depósito mismo, sin lote ni paquete: ahí va el ajuste.
            actual = sum(Quant.search([
                ('product_id', '=', diff.product_id.id), ('location_id', '=', location.id),
                ('lot_id', '=', False), ('package_id', '=', False), ('owner_id', '=', False),
            ]).mapped('quantity'))
            quant = Quant.create({'product_id': diff.product_id.id, 'location_id': location.id,
                                  'inventory_quantity': actual - diff.diferencia})
            quant.with_context(nexo_pos_skip=True).action_apply_inventory()
        self._resolve_uncertain()
        self.unlink()
        return {'type': 'ir.actions.client', 'tag': 'reload'}
