import json
import logging
from datetime import timedelta

from odoo import api, fields, models, release

_logger = logging.getLogger(__name__)

KEEP_DAYS = 30


class NexoLog(models.Model):
    """Registro de llamadas a Nexo, para soporte.

    Nunca guarda cabeceras, cuerpos, tokens ni contraseñas: solo lo que hace
    falta para seguir una llamada del lado de Nexo (el id de solicitud).
    """
    _name = 'nexo.log'
    _description = 'Registro de llamadas a Nexo'
    _order = 'id desc'

    service = fields.Char(required=True, index=True)
    company_id = fields.Many2one('res.company', ondelete='cascade')
    user_id = fields.Many2one('res.users', ondelete='set null')
    method = fields.Char()
    path = fields.Char()
    status = fields.Integer()
    error_kind = fields.Char()
    duration_ms = fields.Integer()
    request_id = fields.Char(index=True)

    @api.model
    def _record_call(self, service, entry):
        """Guarda la llamada en un cursor propio: si la operación que la hizo
        falla y se deshace, el registro de la llamada queda igual."""
        vals = {
            'service': service,
            'company_id': self.env.company.id,
            'user_id': self.env.uid,
            'method': entry.get('method'),
            'path': entry.get('path'),
            'status': entry.get('status') or 0,
            'error_kind': entry.get('error_kind'),
            'duration_ms': entry.get('duration_ms'),
            'request_id': entry.get('request_id'),
        }
        with self.env.registry.cursor() as cr:
            self.env(cr=cr, su=True)['nexo.log'].create(vals)

    @api.model
    def _on_call(self, service):
        return lambda entry: self._record_call(service, entry)

    @api.autovacuum
    def _gc_old_calls(self):
        limit = fields.Datetime.now() - timedelta(days=KEEP_DAYS)
        self.sudo().search([('create_date', '<', limit)]).unlink()

    # ------------------------------------------------------------------
    # Diagnóstico
    # ------------------------------------------------------------------

    @api.model
    def _diagnostic_sections(self):
        """Cada conector agrega su sección (configuración y prueba de conexión).

        Las claves y los tokens se informan solo como presentes o ausentes.
        """
        modules = self.env['ir.module.module'].sudo().search([('name', '=like', 'nexo\\_%'), ('state', '=', 'installed')])
        return {
            'odoo': {'version': release.version, 'serie': release.serie},
            'modulos': {m.name: m.latest_version for m in modules},
            'compania': self.env.company.name,
        }

    @api.model
    def action_download_diagnostic(self):
        calls = self.sudo().search([('company_id', '=', self.env.company.id)], limit=200)
        package = {
            'generado': fields.Datetime.to_string(fields.Datetime.now()),
            **self._diagnostic_sections(),
            'llamadas': [{
                'fecha': fields.Datetime.to_string(c.create_date),
                'servicio': c.service,
                'metodo': c.method,
                'ruta': c.path,
                'estado': c.status,
                'error': c.error_kind,
                'ms': c.duration_ms,
                'solicitud': c.request_id,
                'usuario': c.user_id.login,
            } for c in calls],
        }
        content = json.dumps(package, ensure_ascii=False, indent=2, default=str)
        attachment = self.env['ir.attachment'].sudo().create({
            'name': f"diagnostico-nexo-{fields.Date.today()}.json",
            'raw': content.encode(),
            'mimetype': 'application/json',
            'res_model': 'nexo.log',
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{attachment.id}?download=true',
            'target': 'self',
        }
