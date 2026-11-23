from odoo import _, api, fields, models

from .nexo_pos_service import SERVICE, DEFAULT_URL


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    nexo_pos_url = fields.Char('URL de la API de NexoPOS', config_parameter='nexo_pos.url', default=DEFAULT_URL)
    # La clave se pega acá, se prueba y se pasa a nexo.token. Este campo no se
    # guarda en ningún parámetro y se borra al cargarla.
    nexo_pos_key = fields.Char('Clave de NexoPOS')
    nexo_pos_status = fields.Char('Estado', compute='_compute_nexo_pos_status')
    nexo_pos_warehouse_id = fields.Many2one(
        'stock.warehouse', 'Depósito que comparte stock con NexoPOS',
        domain="[('company_id', '=', company_id)]", compute='_compute_nexo_pos_warehouse_id', store=True, readonly=False)

    nexo_pos_push = fields.Boolean('Mandar precios y stock a NexoPOS', compute='_compute_nexo_pos_push',
                                   store=True, readonly=False)

    @api.depends('company_id')
    def _compute_nexo_pos_push(self):
        for settings in self:
            settings.nexo_pos_push = self.env['nexo.pos.service']._push_enabled(settings.company_id)

    @api.depends('company_id')
    def _compute_nexo_pos_status(self):
        for settings in self:
            settings.nexo_pos_status = self.env['nexo.pos.service'].with_company(settings.company_id).get_status()['message']

    @api.depends('company_id')
    def _compute_nexo_pos_warehouse_id(self):
        for settings in self:
            settings.nexo_pos_warehouse_id = self.env['nexo.pos.service']._warehouse(settings.company_id)

    def set_values(self):
        super().set_values()
        if self.nexo_pos_warehouse_id:
            self.env['ir.config_parameter'].sudo().set_str(
                f'nexo_pos.warehouse_id.{self.company_id.id}', str(self.nexo_pos_warehouse_id.id))
        self.env['nexo.pos.service']._set_push(self.nexo_pos_push, self.company_id)
        if self.nexo_pos_key:
            self.action_nexo_pos_set_key()

    def action_nexo_pos_set_key(self):
        key, self.nexo_pos_key = self.nexo_pos_key, False
        total = self.env['nexo.pos.service'].set_key(key)
        return self._nexo_pos_notify(_("Clave cargada. NexoPOS tiene %s productos de este comercio.", total))

    def action_nexo_pos_test(self):
        result = self.env['nexo.pos.service'].test_connection()
        return self._nexo_pos_notify(result['message'], 'success' if result['ok'] else 'danger')

    def action_nexo_pos_remove_key(self):
        self.env['nexo.token']._clear(SERVICE)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_nexo_pos_link(self):
        r = self.env['nexo.pos.service'].link_products()
        partes = [_("%s vinculados por código de barras", r['vinculados'])]
        if r['solo_nexopos']:
            partes.append(_("%s están en NexoPOS y no en Odoo", r['solo_nexopos']))
        if r['solo_odoo']:
            partes.append(_("%s se venden en Odoo y no están en NexoPOS", r['solo_odoo']))
        if r['ean_repetido']:
            partes.append(_("EAN repetido en NexoPOS, sin vincular: %s", ", ".join(r['ean_repetido'][:10])))
        if r['desvinculados']:
            partes.append(_("%s ya no están en NexoPOS y se desvincularon", r['desvinculados']))
        return self._nexo_pos_notify(". ".join(partes) + ".", sticky=True)

    def action_nexo_pos_import(self):
        r = self.env['nexo.pos.service'].import_from_nexopos()
        partes = [_("%s productos traídos de NexoPOS, con su stock", r['creados'])]
        if r['ya_estaban']:
            partes.append(_("%s ya estaban vinculados", r['ya_estaban']))
        if r['ean_repetido']:
            partes.append(_("no se trajeron, por EAN repetido en NexoPOS: %s", ", ".join(r['ean_repetido'][:10])))
        if r['ean_en_odoo']:
            partes.append(_("ya hay en Odoo un producto con el código %s que no se pudo vincular",
                            ", ".join(r['ean_en_odoo'][:10])))
        if r['insumos']:
            partes.append(_("%s son insumos y quedaron como no vendibles", r['insumos']))
        if r['precio_con_iva']:
            partes.append(_("%s quedaron con el precio con IVA: revisá el impuesto", r['precio_con_iva']))
        return self._nexo_pos_notify(". ".join(partes) + ".", sticky=True)

    def action_nexo_pos_diffs(self):
        return self.env['nexo.pos.stock.diff'].action_compare_now()

    def action_nexo_pos_queue(self):
        return self.env['ir.actions.act_window']._for_xml_id('nexo_pos.nexo_pos_stock_queue_action')

    def action_nexo_log(self):
        return self.env['ir.actions.act_window']._for_xml_id('nexo_core.nexo_log_action')

    @staticmethod
    def _nexo_pos_notify(message, kind='success', sticky=False):
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': 'NexoPOS', 'message': message, 'type': kind, 'sticky': sticky or kind != 'success'}}
