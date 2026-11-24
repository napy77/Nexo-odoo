from odoo import api, models


class IrUiView(models.Model):
    _inherit = 'ir.ui.view'

    @api.model
    def _get_xml_ids_to_load(self):
        # La caja arma los tickets con plantillas que trae al abrir.
        return super()._get_xml_ids_to_load() + ['nexo_clubpay.pos_clubpay_qr_receipt']
