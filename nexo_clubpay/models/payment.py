"""«Mercado Pago (ClubPay)» como proveedor de pago de Odoo, para que el
cliente pague el presupuesto desde el portal (DEC-024).

Es el camino nativo de Odoo: «Pagar» → el link de pago de Mercado Pago que
arma ClubPay (vive 30 minutos) → vuelta a Odoo → la transacción queda hecha, el
pedido se confirma y el pago entra al diario del proveedor. No es el módulo
`payment_mercado_pago` de Odoo: no hay credenciales de Mercado Pago en Odoo.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.urls import urljoin

from odoo.addons.payment import utils as payment_utils

_logger = logging.getLogger(__name__)

CODE = 'clubpay'
PM_CODE = 'clubpay_mercado_pago'
RETURN_ROUTE = '/payment/clubpay/return'


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(selection_add=[(CODE, 'Mercado Pago (ClubPay)')], ondelete={CODE: 'set default'})

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == CODE).update({'support_refund': 'partial'})

    def _get_supported_currencies(self):
        supported = super()._get_supported_currencies()
        if self.code == CODE:
            # ClubPay cobra en pesos.
            supported = supported.filtered(lambda c: c.name == 'ARS')
        return supported

    def _get_default_payment_method_codes(self):
        if self.code != CODE:
            return super()._get_default_payment_method_codes()
        return {PM_CODE}


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    clubpay_payment_ids = fields.One2many('nexo.clubpay.payment', 'transaction_id', 'Cobros ClubPay')

    def _get_specific_rendering_values(self, processing_values):
        if self.provider_code != CODE:
            return super()._get_specific_rendering_values(processing_values)
        orders = self.sale_order_ids if 'sale_order_ids' in self._fields else self.env['sale.order']
        description = _("%(company)s · %(ref)s", company=self.company_id.name,
                        ref=orders[:1].name or self.reference)
        return_url = urljoin(self.provider_id.get_base_url(), f"{RETURN_ROUTE}?ref={self.reference}")
        try:
            payment = self.env['nexo.clubpay.payment'].sudo()._create_payment(
                self.amount, 'tienda', 'sale', company=self.company_id,
                reference=self.env['nexo.clubpay.payment']._reference('tx', self.reference),
                description=description, return_url=return_url,
                transaction_id=self.id, sale_order_id=orders[:1].id,
            )
        except UserError as e:
            self._set_error(str(e))
            return {}
        self.provider_reference = payment.name
        return {
            'api_url': payment.checkout_url,
            'http_method': 'get',
            'url_params': payment_utils.extract_url_params(payment.checkout_url),
        }

    def _clubpay_payment(self):
        self.ensure_one()
        return self.clubpay_payment_ids[:1]

    def _clubpay_record(self, payment):
        """El cobro cambió en ClubPay: se registra como dato de pago y Odoo lo
        procesa con su cron, como cualquier proveedor."""
        self.ensure_one()
        self._record(self._clubpay_payment_data(payment))

    def _clubpay_payment_data(self, payment):
        return {
            'reference': self.reference,
            'payment_id': payment.name,
            'status': payment.state,
            'amount_cents': round(payment.amount * 100),
            'refunded_cents': round(payment.refunded * 100),
            'currency': payment.currency_id.name,
            'error': payment.error or False,
        }

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        if provider_code != CODE:
            return super()._extract_reference(provider_code, payment_data)
        return payment_data.get('reference')

    def _apply_updates(self, payment_data):
        if self.provider_code != CODE:
            super()._apply_updates(payment_data)
            return
        if self.operation == 'refund':
            # ClubPay contestó la devolución con el cobro actualizado.
            self.provider_reference = payment_data.get('payment_id') or self.provider_reference
            if payment_data.get('status') == 'refunded':
                self._set_done()
            return
        self.provider_reference = payment_data.get('payment_id') or self.provider_reference
        status = payment_data.get('status')
        if status in ('paid', 'refunded'):
            # Un cancelado o vencido puede terminar pagado: el pago gana.
            self._set_done(extra_allowed_states=('cancel',))
        elif status == 'pending':
            self._set_pending()
        elif status == 'rejected':
            # No es final: con el mismo link puede probar otra tarjeta.
            self._set_pending(state_message=payment_data.get('error')
                              or _("Mercado Pago rechazó el pago. Podés probar con otro medio."),
                              extra_allowed_states=('pending',))
        elif status in ('cancelled', 'expired'):
            self._set_canceled(state_message=payment_data.get('error') or False)
        else:
            self._set_error(_("ClubPay mandó un estado desconocido: %s.", status))

    def _extract_amount_data(self, payment_data):
        if self.provider_code != CODE:
            return super()._extract_amount_data(payment_data)
        if self.operation == 'refund':
            return None
        return {
            'amount': payment_data.get('amount_cents', 0) / 100.0,
            'currency_code': payment_data.get('currency'),
            'precision_digits': 2,
        }

    def _send_refund_request(self):
        if self.provider_code != CODE:
            return super()._send_refund_request()
        payment = self.source_transaction_id._clubpay_payment()
        if not payment:
            raise ValidationError(_("No se encontró el cobro de ClubPay de esta transacción."))
        try:
            payment.sudo()._refund(amount=-self.amount, reference=payment._reference('dev', self.reference))
        except UserError as e:
            raise ValidationError(str(e)) from e
        self._record(dict(self._clubpay_payment_data(payment), reference=self.reference))
