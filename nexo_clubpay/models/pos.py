from odoo import _, api, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    def _get_terminal_provider_selection(self):
        return super()._get_terminal_provider_selection() + [('clubpay', 'ClubPay')]

    def _check_clubpay(self):
        self.ensure_one()
        if self.payment_provider != 'clubpay':
            raise UserError(_("Esta forma de pago no es de ClubPay."))
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_("Hace falta ser usuario del Punto de Venta para cobrar con ClubPay."))

    def clubpay_create(self, data):
        """La caja pide el QR por el total de la orden. El importe del descuento
        lo dice ClubPay cuando el socio confirma."""
        self._check_clubpay()
        charge = self.env['nexo.clubpay.charge']._create_charge(
            float(data.get('amount') or 0), 'pos', company=self.company_id, pos_order_uuid=data.get('order_uuid'))
        return charge._to_ui()

    def clubpay_status(self, charge_id):
        self._check_clubpay()
        charge = self.env['nexo.clubpay.charge']._find(charge_id, self.company_id)
        charge._refresh()
        return charge._to_ui()

    def clubpay_cancel(self, charge_id):
        self._check_clubpay()
        charge = self.env['nexo.clubpay.charge']._find(charge_id, self.company_id)
        charge._cancel()
        return charge._to_ui()


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    @api.model_create_multi
    def create(self, vals_list):
        """El cupón se acepta solo por lo que ClubPay dijo. La caja guarda el
        charge_id en `transaction_id`; acá se busca el cobro, que Odoo leyó de
        ClubPay mientras mostraba el QR, y el importe tiene que ser ese."""
        Charge = self.env['nexo.clubpay.charge'].sudo()
        checked = []
        for vals in vals_list:
            method = self.env['pos.payment.method'].browse(vals.get('payment_method_id'))
            if method.payment_provider != 'clubpay' or not vals.get('amount'):
                checked.append(False)
                continue
            charge = Charge.search([('name', '=', vals.get('transaction_id') or ''),
                                    ('company_id', '=', method.company_id.id)], limit=1)
            charge._refresh()
            if not charge or charge.state != 'applied':
                raise UserError(_("El cupón de ClubPay no está confirmado. Sacá esa línea de pago y volvé a "
                                  "generar el QR."))
            if charge.pos_payment_id:
                raise UserError(_("Ese cupón de ClubPay ya se usó en otra venta."))
            if float_compare(vals['amount'], charge.discount, precision_rounding=method.company_id.currency_id.rounding):
                raise UserError(_("El cupón de ClubPay es de %(clubpay)s y la caja mandó %(caja)s.",
                                  clubpay=charge.discount, caja=vals['amount']))
            vals.setdefault('cardholder_name', charge.member_name)
            vals.setdefault('payment_ref_no', charge.transaction_id)
            checked.append(charge)
        payments = super().create(vals_list)
        for payment, charge in zip(payments, checked):
            if charge:
                charge.pos_payment_id = payment
        return payments
