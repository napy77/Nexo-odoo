from odoo import http
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal


class ClubpayPortal(CustomerPortal):
    """El cliente pide el QR desde el presupuesto que le llegó por mail. Se
    genera en ese momento porque vence a los 3 minutos."""

    def _clubpay_order(self, order_id, access_token):
        try:
            return self._document_check_access('sale.order', order_id, access_token=access_token)
        except (AccessError, MissingError):
            return None

    @http.route('/my/orders/<int:order_id>/clubpay/start', type='jsonrpc', auth='public')
    def clubpay_start(self, order_id, access_token=None):
        order = self._clubpay_order(order_id, access_token)
        if not order:
            return {'error': "No se encontró el pedido."}
        try:
            charge = order._clubpay_pending() or order._clubpay_start()
        except UserError as e:
            return {'error': str(e)}
        return charge._to_ui()

    @http.route('/my/orders/<int:order_id>/clubpay/status', type='jsonrpc', auth='public')
    def clubpay_status(self, order_id, charge_id, access_token=None):
        order = self._clubpay_order(order_id, access_token)
        charge = order and order.clubpay_charge_ids.filtered(lambda c: c.name == charge_id)
        if not charge:
            return {'error': "No se encontró el cobro."}
        charge._refresh()
        return charge._to_ui()

    @http.route('/my/orders/<int:order_id>/clubpay/cancel', type='jsonrpc', auth='public')
    def clubpay_cancel(self, order_id, charge_id, access_token=None):
        order = self._clubpay_order(order_id, access_token)
        charge = order and order.clubpay_charge_ids.filtered(lambda c: c.name == charge_id)
        if charge:
            try:
                charge._cancel()
            except UserError as e:
                return {'error': str(e)}
        return {'ok': True}
