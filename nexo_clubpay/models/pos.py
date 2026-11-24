from odoo import _, api, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    def _get_terminal_provider_selection(self):
        return super()._get_terminal_provider_selection() + [
            ('clubpay', 'ClubPay'), ('clubpay_mp', 'Mercado Pago (ClubPay)')]

    def _check_clubpay(self, provider='clubpay'):
        self.ensure_one()
        if self.payment_provider != provider:
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


    # --- Mercado Pago a través de ClubPay (DEC-024) ---

    def clubpay_mp_create(self, data):
        """QR de mostrador por el importe de la línea. Lo lee Mercado Pago o
        cualquier billetera."""
        self._check_clubpay('clubpay_mp')
        Payment = self.env['nexo.clubpay.payment']
        Payment._check_ready('mostrador', self.company_id)
        payment = Payment._create_payment(
            float(data.get('amount') or 0), 'mostrador', 'pos', company=self.company_id,
            description=_("%(company)s · %(order)s", company=self.company_id.name, order=data.get('order_name') or ''),
            pos_order_uuid=data.get('order_uuid'))
        return payment._to_ui()

    def clubpay_mp_status(self, payment_id):
        self._check_clubpay('clubpay_mp')
        payment = self.env['nexo.clubpay.payment']._find(payment_id, self.company_id)
        payment._refresh()
        return payment._to_ui()

    def clubpay_mp_cancel(self, payment_id):
        """Si ClubPay contesta que ya se pagó, vuelve `paid` y la caja cierra
        la venta con ese pago."""
        self._check_clubpay('clubpay_mp')
        payment = self.env['nexo.clubpay.payment']._find(payment_id, self.company_id)
        payment._cancel()
        return payment._to_ui()


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
            if method.payment_provider == 'clubpay_mp' and vals.get('amount'):
                checked.append(self._clubpay_mp_check(vals, method))
                continue
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

    def _clubpay_mp_check(self, vals, method):
        """Un pago de Mercado Pago entra solo si ClubPay dice que se acreditó,
        por el mismo importe y una sola vez. Una devolución (importe negativo)
        no pasa por la caja: se hace desde el cobro, en el backend."""
        if vals['amount'] < 0:
            raise UserError(_("Las devoluciones de Mercado Pago se hacen desde Punto de Venta → Cobros con Mercado "
                              "Pago → «Devolver». Usá otra forma de pago para la devolución en la caja."))
        payment = self.env['nexo.clubpay.payment'].sudo().search([
            ('name', '=', vals.get('transaction_id') or ''), ('company_id', '=', method.company_id.id)], limit=1)
        if payment and payment.state != 'paid':
            payment._refresh()
        if not payment or payment.state != 'paid':
            raise UserError(_("El pago con Mercado Pago no está acreditado. Sacá esa línea y volvé a generar el QR."))
        if payment.pos_payment_id:
            raise UserError(_("Ese pago de Mercado Pago ya se usó en otra venta."))
        if float_compare(vals['amount'], payment.amount, precision_rounding=method.company_id.currency_id.rounding):
            raise UserError(_("Mercado Pago acreditó %(mp)s y la caja mandó %(caja)s.",
                              mp=payment.amount, caja=vals['amount']))
        vals.setdefault('payment_ref_no', payment.mp_payment_id)
        return payment
