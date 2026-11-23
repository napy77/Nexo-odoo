from odoo import api, models

from .nexo_pos_service import SERVICE


class NexoLog(models.Model):
    _inherit = 'nexo.log'

    @api.model
    def _diagnostic_sections(self):
        sections = super()._diagnostic_sections()
        Service = self.env['nexo.pos.service']
        status = Service.get_status()
        sections[SERVICE] = {
            'url': Service._url(),
            'clave': '***' if status['connected'] else None,
            'estado': status['message'],
            'deposito': Service._warehouse().display_name,
            'cola': {state: self.env['nexo.pos.stock.queue'].search_count([('state', '=', state)])
                     for state in ('pending', 'uncertain', 'error')},
            'prueba_conexion': Service.test_connection() if status['connected'] else None,
        }
        return sections
