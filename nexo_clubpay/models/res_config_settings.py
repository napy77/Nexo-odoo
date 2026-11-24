from odoo import _, api, fields, models
from odoo.tools import file_open
from odoo.tools.binary import BinaryBytes
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
    nexo_clubpay_mp = fields.Boolean('Cobrar con Mercado Pago a través de ClubPay', compute='_compute_nexo_clubpay_mp',
                                     store=True, readonly=False)
    nexo_clubpay_mp_journal_id = fields.Many2one('account.journal', 'Diario de Mercado Pago',
                                                 compute='_compute_nexo_clubpay_journal_id')

    nexo_clubpay_libreta = fields.Boolean('Libreta en ClubPay', compute='_compute_nexo_clubpay_libreta',
                                          store=True, readonly=False)

    @api.depends('company_id')
    def _compute_nexo_clubpay_libreta(self):
        for settings in self:
            settings.nexo_clubpay_libreta = self.env['nexo.clubpay.libreta.line']._enabled(settings.company_id)

    @api.depends('company_id')
    def _compute_nexo_clubpay_mp(self):
        for settings in self:
            settings.nexo_clubpay_mp = self.env['nexo.clubpay.payment']._mp_enabled(settings.company_id)

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
            settings.nexo_clubpay_mp_journal_id = self._clubpay_journal(settings.company_id, 'mp_journal_id')

    @api.model
    def _clubpay_journal(self, company, key='journal_id'):
        journal_id = int(self.env['ir.config_parameter'].sudo().get_str(f'nexo_clubpay.{key}.{company.id}') or 0)
        return self.env['account.journal'].sudo().browse(journal_id).exists()

    @api.model
    def _clubpay_get_journal(self, company, key, name, code):
        journal = self._clubpay_journal(company, key) or self.env['account.journal'].sudo().search([
            ('company_id', '=', company.id), ('code', '=', code)], limit=1)
        if not journal:
            journal = self.env['account.journal'].sudo().create({
                'name': name, 'type': 'bank', 'code': code, 'company_id': company.id})
        self.env['ir.config_parameter'].sudo().set_str(f'nexo_clubpay.{key}.{company.id}', str(journal.id))
        return journal

    def set_values(self):
        super().set_values()
        self.env['nexo.clubpay.charge']._set_simulator(self.nexo_clubpay_simulator, self.company_id)
        Payment = self.env['nexo.clubpay.payment']
        if self.nexo_clubpay_mp != Payment._mp_enabled(self.company_id):
            if self.nexo_clubpay_mp:
                # Antes de prenderlo: Mercado Pago conectado y el interruptor de
                # ClubPay prendido. Si falta algo, el error dice qué.
                Payment._check_ready('tienda', self.company_id)
            Payment._set_mp_enabled(self.nexo_clubpay_mp, self.company_id)
            self._clubpay_mp_provider(self.company_id, create=self.nexo_clubpay_mp)
        if self.nexo_clubpay_key:
            self.action_nexo_clubpay_set_key()
        Line = self.env['nexo.clubpay.libreta.line']
        if self.nexo_clubpay_libreta != Line._enabled(self.company_id):
            if self.nexo_clubpay_libreta and not self.env['nexo.clubpay.charge']._enabled(self.company_id):
                raise UserError(_("Para la libreta hace falta la clave de ClubPay del comercio (o el simulador)."))
            Line._set_enabled(self.nexo_clubpay_libreta, self.company_id)

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
        journal = self._clubpay_get_journal(company, 'journal_id', _("Cupones ClubPay"), 'CLUB')
        methods = self._clubpay_pos_method(company, 'clubpay', _("Cupón ClubPay"), journal)
        partes = [_("Diario «%(j)s» y forma de pago «%(m)s» listos", j=journal.name, m=methods.name)]
        if self.env['nexo.clubpay.payment']._mp_enabled(company):
            mp_journal = self._clubpay_get_journal(company, 'mp_journal_id', _("Mercado Pago (ClubPay)"), 'CPMP')
            mp_method = self._clubpay_pos_method(company, 'clubpay_mp', _("Mercado Pago (ClubPay)"), mp_journal)
            self._clubpay_mp_provider(company, create=True)
            methods |= mp_method
            partes.append(_("Diario «%(j)s», forma de pago «%(m)s» y pago en el portal listos",
                            j=mp_journal.name, m=mp_method.name))
        added, open_sessions = [], []
        for config in self.env['pos.config'].sudo().search([('company_id', '=', company.id)]):
            missing = methods - config.payment_method_ids
            if not missing:
                continue
            try:
                with self.env.cr.savepoint():
                    config.payment_method_ids = [(4, m.id) for m in missing]
                added.append(config.name)
            except UserError:
                # Odoo no deja cambiar las formas de pago con la caja abierta.
                open_sessions.append(config.name)
        if added:
            partes.append(_("Se sumó a: %s", ", ".join(added)))
        if open_sessions:
            partes.append(_("Cerrá la caja y volvé a tocar para sumarla a: %s", ", ".join(open_sessions)))
        return self._clubpay_notify(". ".join(partes) + ".", sticky=True)

    @api.model
    def _clubpay_pos_method(self, company, provider, name, journal):
        Method = self.env['pos.payment.method'].sudo().with_context(active_test=False)
        method = Method.search([('company_id', '=', company.id), ('payment_provider', '=', provider)], limit=1)
        if not method:
            method = Method.create({
                'name': name, 'company_id': company.id, 'journal_id': journal.id, 'type': 'bank',
                'payment_method_type': 'terminal', 'payment_provider': provider,
            })
        method.active = True
        return method

    @api.model
    def _clubpay_mp_provider(self, company, create=True):
        """El proveedor de pago «Mercado Pago (ClubPay)» de la compañía, para
        pagar desde el portal. Apagar el tilde lo despublica."""
        Provider = self.env['payment.provider'].sudo().with_context(active_test=False)
        provider = Provider.search([('code', '=', 'clubpay'), ('company_id', '=', company.id)], limit=1)
        if not create:
            provider.write({'is_published': False})
            return provider
        journal = self._clubpay_get_journal(company, 'mp_journal_id', _("Mercado Pago (ClubPay)"), 'CPMP')
        # El método de pago contable del proveedor (lo que hace account_payment
        # al instalar un proveedor): sin él el pago no se puede registrar.
        Provider._setup_payment_method('clubpay')
        if not provider:
            provider = Provider.create({
                'name': _("Mercado Pago (ClubPay)"), 'code': 'clubpay', 'company_id': company.id,
                'redirect_form_view_id': self.env.ref('payment.generic_redirect_form').id,
            })
        provider.write({'active': True, 'is_live': True, 'is_published': True, 'journal_id': journal.id})
        PaymentMethod = self.env['payment.method'].sudo().with_context(active_test=False)
        if not PaymentMethod.search([('provider_id', '=', provider.id), ('code', '=', 'clubpay_mercado_pago')]):
            with file_open('payment/static/img/mercado_livre.png', 'rb') as f:
                image = BinaryBytes(f.read())  # Odoo 20: BinaryValue, no base64
            PaymentMethod.create({'name': "Mercado Pago", 'code': 'clubpay_mercado_pago', 'provider_id': provider.id,
                                  'active': True, 'image': image})
        return provider

    def action_nexo_clubpay_payments(self):
        return self.env['ir.actions.act_window']._for_xml_id('nexo_clubpay.nexo_clubpay_payment_action')

    def action_nexo_clubpay_libreta(self):
        return self.env['ir.actions.act_window']._for_xml_id('nexo_clubpay.nexo_clubpay_libreta_line_action')

    def action_nexo_clubpay_charges(self):
        return self.env['ir.actions.act_window']._for_xml_id('nexo_clubpay.nexo_clubpay_charge_action')

    @staticmethod
    def _clubpay_notify(message, kind='success', sticky=False):
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': 'ClubPay', 'message': message, 'type': kind, 'sticky': sticky}}
