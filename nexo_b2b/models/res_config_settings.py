from odoo import api, fields, models

from .nexo_b2b_service import SERVICE


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nexo_b2b_url = fields.Char('URL de NexoB2B', config_parameter='nexo_b2b.url')
    # Pública por diseño en Medusa, pero igual no va en git ni en el log.
    nexo_b2b_publishable_key = fields.Char('Clave publicable', config_parameter='nexo_b2b.publishable_key')
    nexo_b2b_status = fields.Char('Estado de la conexión', compute='_compute_nexo_b2b_status')

    @api.depends('company_id')
    def _compute_nexo_b2b_status(self):
        for settings in self:
            status = self.env['nexo.b2b.service'].with_company(settings.company_id).get_status()
            settings.nexo_b2b_status = status['message']

    def action_nexo_b2b_connect(self):
        return {
            'type': 'ir.actions.client',
            'tag': 'nexo_b2b.catalog',
            'name': 'Catálogo NexoB2B',
            'params': {'login': True},
        }

    def action_nexo_b2b_test(self):
        result = self.env['nexo.b2b.service'].test_connection()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'NexoB2B',
                'message': result['message'],
                'type': 'success' if result['ok'] else 'danger',
                'sticky': not result['ok'],
            },
        }

    def action_nexo_b2b_logout(self):
        self.env['nexo.token']._clear(SERVICE)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_nexo_b2b_tax_map(self):
        self.env['nexo.b2b.tax.map']._propose()
        return self.env['ir.actions.act_window']._for_xml_id('nexo_b2b.nexo_b2b_tax_map_action')

    def action_nexo_b2b_category_map(self):
        return self.env['ir.actions.act_window']._for_xml_id('nexo_b2b.nexo_b2b_category_map_action')

    def action_nexo_log(self):
        return self.env['ir.actions.act_window']._for_xml_id('nexo_core.nexo_log_action')
