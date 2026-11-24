from markupsafe import escape

from odoo import http
from odoo.http import request

from ..models.payment import CODE, RETURN_ROUTE


class ClubpayPaymentController(http.Controller):

    @http.route(RETURN_ROUTE, type='http', auth='public', methods=['GET'])
    def clubpay_return(self, ref=None, **_kwargs):
        """Mercado Pago vuelve acá al aprobar, al quedar pendiente y al fallar.
        Lo que dice la URL no se cree: se le pregunta a ClubPay."""
        tx = request.env['payment.transaction'].sudo().search(
            [('reference', '=', ref or ''), ('provider_code', '=', CODE)], limit=1)
        payment = tx._clubpay_payment() if tx else None
        if payment:
            payment._refresh()
            tx._clubpay_record(payment)
        return request.redirect('/payment/status')

    # ------------------------------------------------------------------
    # Simulador: hace de la página de pago de Mercado Pago
    # ------------------------------------------------------------------

    def _sim_payment(self, payment_id):
        payment = request.env['nexo.clubpay.payment'].sudo().search(
            [('name', '=', payment_id), ('simulated', '=', True)], limit=1)
        if not payment:
            raise request.not_found()
        return payment

    @http.route('/nexo_clubpay/simulador/pago/<string:payment_id>', type='http', auth='public', methods=['GET'])
    def clubpay_sim_checkout(self, payment_id, **_kwargs):
        payment = self._sim_payment(payment_id)
        amount = payment.currency_id.format(payment.amount)
        base = f"/nexo_clubpay/simulador/pago/{escape(payment.name)}"
        html = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Simulador de Mercado Pago</title>
<style>body{{font-family:system-ui,sans-serif;max-width:420px;margin:40px auto;padding:0 16px;color:#222}}
.sim{{background:#fff3cd;border:1px solid #e0c36a;padding:10px;border-radius:6px;font-weight:600}}
a{{display:block;text-align:center;padding:14px;margin:12px 0;border-radius:6px;text-decoration:none;font-weight:600}}
.ok{{background:#009ee3;color:#fff}}.no{{background:#eee;color:#222}}</style></head><body>
<p class="sim">SIMULADOR: no es Mercado Pago y no se cobra nada.</p>
<h2>Pagar {escape(amount)}</h2>
<p>Cobro {escape(payment.name)}. Estado: {escape(dict(payment._fields['state'].selection)[payment.state])}.</p>
<a class="ok" href="{base}/pagar">Pagar (simulado)</a>
<a class="no" href="{base}/rechazar">Rechazar la tarjeta</a>
<a class="no" href="{base}/volver">Volver sin pagar</a>
</body></html>"""
        return request.make_response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])

    @http.route('/nexo_clubpay/simulador/pago/<string:payment_id>/<string:result>', type='http', auth='public',
                methods=['GET'])
    def clubpay_sim_result(self, payment_id, result, **_kwargs):
        payment = self._sim_payment(payment_id)
        if payment.state in ('pending', 'rejected'):
            if result == 'pagar':
                payment._sim_pay()
            elif result == 'rechazar':
                payment._apply({'status': 'rejected', 'error': "Simulador: la tarjeta fue rechazada."})
        return request.redirect(payment.sim_return_url or '/my', local=False)
