from datetime import datetime, timezone

from odoo import api, fields, models

from ..lib.client import jwt_expiration


class NexoToken(models.Model):
    """Sesión de la compañía contra un servicio Nexo.

    La contraseña nunca llega a este modelo (DEC-003): se guarda solo el token
    que devuelve el login. Solo lo leen los administradores; el resto del
    código accede con sudo() a través de los métodos de abajo.
    """
    _name = 'nexo.token'
    _description = 'Sesión de Nexo'

    service = fields.Char(required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, ondelete='cascade',
                                 default=lambda self: self.env.company)
    token = fields.Char(required=True)
    expires_at = fields.Datetime()
    account_id = fields.Char(help="Id de la cuenta en el servicio (por ejemplo, el comercio_id).")
    account_name = fields.Char()
    user_name = fields.Char(help="Usuario del equipo que inició sesión; vacío si fue el dueño.")
    login_email = fields.Char()

    _service_company_uniq = models.Constraint(
        'unique (service, company_id)',
        'Ya hay una sesión de este servicio para esta compañía.',
    )

    @api.model
    def _get(self, service, company=None):
        company = company or self.env.company
        return self.sudo().search([('service', '=', service), ('company_id', '=', company.id)], limit=1)

    @api.model
    def _store(self, service, token, company=None, **info):
        company = company or self.env.company
        exp = jwt_expiration(token)
        vals = {
            'token': token,
            'expires_at': datetime.fromtimestamp(exp, timezone.utc).replace(tzinfo=None) if exp else False,
            **info,
        }
        record = self._get(service, company)
        if record:
            record.write(vals)
        else:
            record = self.sudo().create({'service': service, 'company_id': company.id, **vals})
        return record

    @api.model
    def _valid_token(self, service, company=None):
        """El token si existe y no venció; si no, None."""
        record = self._get(service, company)
        if not record:
            return None
        if record.expires_at and record.expires_at <= fields.Datetime.now():
            return None
        return record.token

    @api.model
    def _clear(self, service, company=None):
        self._get(service, company).unlink()
