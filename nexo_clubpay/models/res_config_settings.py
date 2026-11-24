from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .nexo_clubpay_charge import DEFAULT_URL, SERVICE


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nexo_clubpay_url = fields.Char('URL de ClubPay', config_parameter='nexo_clubpay.url', default=DEFAULT_URL)
    # Se pega acá, se prueba y pasa a nexo.token. No se guarda en ningún
    # parámetro y se borra al cargarla.
    nexo_clubpay_key = fields.Char('Clave de ClubPay')
    nexo_clubpay_status = fields.Char('Estado', compute='_compute_nexo_clubpay_status')
    nexo_clubpay_simulator = fields.Boolean('Simulador de ClubPay', compute='_compute_nexo_clubpay_simulator',
                                            store=True, readonly=False)
    nexo_clubpay_journal_id = fields.Many2one('account.journal', 'Diario de cupones',
                                              compute='_compute_nexo_clubpay_journal_id')

    @api.depends('company_id')
    def _compute_nexo_clubpay_status(self):
        for settings in self:
            token = self.env['nexo.token']._get(SERVICE, settings.company_id)
            settings.nexo_clubpay_status = _("Clave cargada") if token else _("Sin clave")

    @api.depends('company_id')
    def _compute_nexo_clubpay_simulator(self):
        for settings in self:
            settings.nexo_clubpay_simulator = self.env['nexo.clubpay.charge']._simulator(settings.company_id)

    @api.depends('company_id')
    def _compute_nexo_clubpay_journal_id(self):
        for settings in self:
            settings.nexo_clubpay_journal_id = self._clubpay_journal(settings.company_id)

    @api.model
    def _clubpay_journal(self, company):
        journal_id = int(self.env['ir.config_parameter'].sudo().get_str(f'nexo_clubpay.journal_id.{company.id}') or 0)
        return self.env['account.journal'].sudo().browse(journal_id).exists()

    def set_values(self):
        super().set_values()
        self.env['nexo.clubpay.charge']._set_simulator(self.nexo_clubpay_simulator, self.company_id)
        if self.nexo_clubpay_key:
            self.action_nexo_clubpay_set_key()

    def action_nexo_clubpay_set_key(self):
        key, self.nexo_clubpay_key = self.nexo_clubpay_key, False
        self.env['nexo.clubpay.charge'].with_company(self.company_id).set_key(key)
        return self._clubpay_notify(_("Clave de ClubPay cargada."))

    def action_nexo_clubpay_remove_key(self):
        self.env['nexo.token']._clear(SERVICE, self.company_id)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_nexo_clubpay_setup(self):
        """Crea el diario «Cupones ClubPay» y la forma de pago «Cupón ClubPay», y
        la suma a las cajas de la compañía. Se puede tocar de nuevo: no duplica."""
        company = self.company_id
        journal = self._clubpay_journal(company)
        if not journal:
            journal = self.env['account.journal'].sudo().search([
                ('company_id', '=', company.id), ('code', '=', 'CLUB')], limit=1)
        if not journal:
            journal = self.env['account.journal'].sudo().create({
                'name': _("Cupones ClubPay"), 'type': 'bank', 'code': 'CLUB', 'company_id': company.id})
        self.env['ir.config_parameter'].sudo().set_str(f'nexo_clubpay.journal_id.{company.id}', str(journal.id))

        Method = self.env['pos.payment.method'].sudo()
        method = Method.search([('company_id', '=', company.id), ('payment_provider', '=', 'clubpay')], limit=1)
        if not method:
            method = Method.create({
                'name': _("Cupón ClubPay"), 'company_id': company.id, 'journal_id': journal.id, 'type': 'bank',
                'payment_method_type': 'terminal', 'payment_provider': 'clubpay',
            })
        added, open_sessions = [], []
        for config in self.env['pos.config'].sudo().search([('company_id', '=', company.id)]):
            if method in config.payment_method_ids:
                continue
            try:
                with self.env.cr.savepoint():
                    config.payment_method_ids = [(4, method.id)]
                added.append(config.name)
            except UserError:
                # Odoo no deja cambiar las formas de pago con la caja abierta.
                open_sessions.append(config.name)
        partes = [_("Diario «%(j)s» y forma de pago «%(m)s» listos", j=journal.name, m=method.name)]
        if added:
            partes.append(_("Se sumó a: %s", ", ".join(added)))
        if open_sessions:
            partes.append(_("Cerrá la caja y volvé a tocar para sumarla a: %s", ", ".join(open_sessions)))
        return self._clubpay_notify(". ".join(partes) + ".", sticky=True)

    def action_nexo_clubpay_charges(self):
        return self.env['ir.actions.act_window']._for_xml_id('nexo_clubpay.nexo_clubpay_charge_action')

    @staticmethod
    def _clubpay_notify(message, kind='success', sticky=False):
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': 'ClubPay', 'message': message, 'type': kind, 'sticky': sticky}}
