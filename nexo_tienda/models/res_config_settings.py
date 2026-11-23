from odoo import _, api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nexo_tienda_enabled = fields.Boolean('Atender los pedidos de NexoTienda en Odoo',
                                         compute='_compute_nexo_tienda_enabled', store=True, readonly=False)

    @api.depends('company_id')
    def _compute_nexo_tienda_enabled(self):
        for settings in self:
            settings.nexo_tienda_enabled = self.env['nexo.tienda.order']._enabled(settings.company_id)

    def set_values(self):
        super().set_values()
        self.env['nexo.tienda.order']._set_enabled(self.nexo_tienda_enabled, self.company_id)

    def action_nexo_tienda_orders(self):
        return self.env['ir.actions.act_window']._for_xml_id('nexo_tienda.nexo_tienda_order_action')
