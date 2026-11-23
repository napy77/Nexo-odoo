from odoo import api, models

from .nexo_b2b_service import SERVICE


class NexoLog(models.Model):
    _inherit = 'nexo.log'

    @api.model
    def _diagnostic_sections(self):
        sections = super()._diagnostic_sections()
        Service = self.env['nexo.b2b.service']
        url, key = Service._config()
        status = Service.get_status()
        sections[SERVICE] = {
            'url': url,
            'clave_publicable': '***' if key else None,
            'sesion': status['message'],
            'vence': status['expires_at'],
            'prueba_conexion': Service.test_connection() if status['connected'] else None,
        }
        return sections
