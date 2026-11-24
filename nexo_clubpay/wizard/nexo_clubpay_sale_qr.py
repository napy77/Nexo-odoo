from odoo import _, api, fields, models


class NexoClubpaySaleQr(models.TransientModel):
    """El QR de ClubPay para un pedido de venta, cuando el cliente está
    delante (en el mostrador o por videollamada). Por mail no va el QR, que
    vence a los 3 minutos: va el link al portal, donde el cliente lo genera
    cuando lo va a usar."""
    _name = 'nexo.clubpay.sale.qr'
    _description = 'QR de ClubPay para un pedido de venta'

    sale_order_id = fields.Many2one('sale.order', required=True, readonly=True)
    charge_id = fields.Many2one('nexo.clubpay.charge', required=True, readonly=True)
    currency_id = fields.Many2one(related='charge_id.currency_id')
    state = fields.Selection(related='charge_id.state')
    qr_image = fields.Binary('QR', compute='_compute_qr_image')
    qr_payload = fields.Char(related='charge_id.qr_payload')
    expires_at = fields.Datetime(related='charge_id.expires_at')
    expires_label = fields.Char(compute='_compute_expires_label')
    ticket_total = fields.Monetary(related='charge_id.ticket_total')
    member_name = fields.Char(related='charge_id.member_name')
    member_number = fields.Char(related='charge_id.member_number')
    club_name = fields.Char(related='charge_id.club_name')
    offer_description = fields.Char(related='charge_id.offer_description')
    discount = fields.Monetary(related='charge_id.discount')
    neto = fields.Monetary(related='charge_id.neto')
    recorte_texto = fields.Char(related='charge_id.recorte_texto')
    error = fields.Char(related='charge_id.error')
    simulated = fields.Boolean(related='charge_id.simulated')

    @api.depends('charge_id.qr_payload', 'charge_id.state')
    def _compute_qr_image(self):
        for wizard in self:
            charge = wizard.charge_id.sudo()
            wizard.qr_image = charge._qr_image().split(',', 1)[1] if charge.qr_payload and charge.state == 'pending' \
                else False

    @api.depends('expires_at')
    def _compute_expires_label(self):
        for wizard in self:
            wizard.expires_label = wizard.expires_at and \
                fields.Datetime.context_timestamp(wizard, wizard.expires_at).strftime('%H:%M') or ''

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'target': 'new', 'name': _("Descuento ClubPay"),
        }

    def action_refresh(self):
        self.charge_id.sudo()._refresh()
        return self._reopen()

    def action_cancel_charge(self):
        self.charge_id.sudo()._cancel()
        return {'type': 'ir.actions.act_window_close'}

    def action_new(self):
        self.charge_id = self.sale_order_id._clubpay_start()
        return self._reopen()

    def action_print(self):
        return self.env.ref('nexo_clubpay.action_report_clubpay_qr').report_action(self.charge_id)
