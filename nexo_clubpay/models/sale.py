from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    clubpay_charge_ids = fields.One2many('nexo.clubpay.charge', 'sale_order_id', 'Cobros de ClubPay', readonly=True)
    clubpay_charge_id = fields.Many2one('nexo.clubpay.charge', 'Descuento ClubPay',
                                        compute='_compute_clubpay', compute_sudo=True)
    clubpay_discount = fields.Monetary('Descuento ClubPay', compute='_compute_clubpay', compute_sudo=True)
    clubpay_neto = fields.Monetary('A pagar con ClubPay', compute='_compute_clubpay', compute_sudo=True)
    clubpay_total_changed = fields.Boolean(compute='_compute_clubpay', compute_sudo=True)
    clubpay_available = fields.Boolean(compute='_compute_clubpay_available')

    @api.depends('clubpay_charge_ids.state', 'clubpay_charge_ids.discount', 'amount_total')
    def _compute_clubpay(self):
        for order in self:
            charge = order.clubpay_charge_ids.filtered(lambda c: c.state == 'applied')[:1]
            order.clubpay_charge_id = charge
            order.clubpay_discount = charge.discount
            order.clubpay_neto = order.amount_total - charge.discount if charge else order.amount_total
            # ClubPay registró el descuento sobre otro total. No hay forma de
            # anularlo desde acá (ClubPay no tiene la ruta todavía).
            order.clubpay_total_changed = bool(charge) and float_compare(
                charge.ticket_total, order.amount_total, precision_rounding=order.currency_id.rounding or 0.01) != 0

    @api.depends('clubpay_charge_ids.state', 'clubpay_charge_ids.discount')
    def _compute_amount_paid(self):
        """El cupón de ClubPay cuenta como pagado. Así el portal cobra el neto
        con Mercado Pago, y el pedido se confirma y se da por pagado cuando el
        cupón más el pago cubren el total (DEC-024)."""
        super()._compute_amount_paid()
        for order in self:
            charge = order.sudo().clubpay_charge_ids.filtered(lambda c: c.state == 'applied')[:1]
            order.amount_paid += charge.discount

    @api.depends('company_id', 'state', 'invoice_status')
    def _compute_clubpay_available(self):
        Charge = self.env['nexo.clubpay.charge']
        for order in self:
            order.clubpay_available = (
                order.state in ('draft', 'sent', 'sale') and order.invoice_status != 'invoiced'
                and order.amount_total > 0 and Charge._enabled(order.company_id))

    def _clubpay_start(self):
        """Un QR nuevo por el total del pedido. Uno pendiente anterior se cancela:
        el socio no puede tener dos abiertos por la misma compra."""
        self.ensure_one()
        if not self.clubpay_available:
            raise UserError(_("Este pedido ya no admite el descuento de ClubPay."))
        if self.clubpay_charge_id:
            raise UserError(_("Este pedido ya tiene el descuento de ClubPay aplicado."))
        self.clubpay_charge_ids.filtered(lambda c: c.state == 'pending')._cancel()
        return self.env['nexo.clubpay.charge']._create_charge(self.amount_total, 'sale', company=self.company_id,
                                                               sale_order=self)

    def _clubpay_pending(self):
        self.ensure_one()
        return self.clubpay_charge_ids.filtered(lambda c: c.state == 'pending')[:1]

    def action_clubpay_qr(self):
        self.ensure_one()
        charge = self._clubpay_pending() or self._clubpay_start()
        wizard = self.env['nexo.clubpay.sale.qr'].create({'sale_order_id': self.id, 'charge_id': charge.id})
        return wizard._reopen()


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        """La factura de un pedido con descuento de ClubPay queda pagada en esa
        parte con el cupón. Así lo que el cliente ve para pagar es el neto, y la
        venta sigue siendo por el total (DEC-023)."""
        posted = super()._post(soft)
        for move in posted.filtered(lambda m: m.move_type == 'out_invoice'):
            for order in move.line_ids.sale_line_ids.order_id:
                charge = order.sudo().clubpay_charge_id
                if charge and not charge.invoice_payment_id and charge.discount > 0:
                    move._clubpay_pay_coupon(charge)
        return posted

    def _clubpay_pay_coupon(self, charge):
        self.ensure_one()
        journal = self.env['res.config.settings']._clubpay_journal(self.company_id)
        if not journal:
            self.message_post(body=_("Esta factura tiene un descuento de ClubPay de %s, pero no hay un diario de "
                                     "cupones. Crealo en Ajustes → ClubPay y registrá el pago a mano.",
                                     charge.discount))
            return
        amount = min(charge.discount, self.amount_residual)
        if amount <= 0:
            return
        wizard = self.env['account.payment.register'].sudo().with_context(
            active_model='account.move', active_ids=self.ids).create({
                'journal_id': journal.id,
                'amount': amount,
                'payment_date': self.invoice_date or fields.Date.context_today(self),
                'communication': _("Cupón ClubPay %(tx)s · %(socio)s", tx=charge.transaction_id or charge.name,
                                   socio=charge.member_name or ''),
            })
        payment = wizard._create_payments()
        charge.invoice_payment_id = payment[:1]
