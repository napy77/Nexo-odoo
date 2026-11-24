from odoo import _, api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nexo_tienda_enabled = fields.Boolean('Atender los pedidos de NexoTienda en Odoo',
                                         compute='_compute_nexo_tienda_enabled', store=True, readonly=False)

    nexo_tienda_pos_config_id = fields.Many2one(
        'pos.config', 'Caja que recibe los pedidos', domain="[('company_id', '=', company_id)]",
        compute='_compute_nexo_tienda_enabled', store=True, readonly=False)

    @api.depends('company_id')
    def _compute_nexo_tienda_enabled(self):
        Order = self.env['nexo.tienda.order']
        for settings in self:
            settings.nexo_tienda_enabled = Order._enabled(settings.company_id)
            settings.nexo_tienda_pos_config_id = Order._pos_config(settings.company_id)

    def set_values(self):
        super().set_values()
        self.env['nexo.tienda.order']._set_enabled(self.nexo_tienda_enabled, self.company_id)
        self.env['ir.config_parameter'].sudo().set_str(
            f'nexo_tienda.pos_config_id.{self.company_id.id}', str(self.nexo_tienda_pos_config_id.id or ''))

    def action_nexo_tienda_orders(self):
        return self.env['ir.actions.act_window']._for_xml_id('nexo_tienda.nexo_tienda_order_action')
