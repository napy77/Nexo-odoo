{
    'name': 'Nexo · Descuentos ClubPay',
    'version': '20.0.1.2.0',
    'summary': 'Descuento de socios de ClubPay, cobro con Mercado Pago vía ClubPay y libreta del cliente en la app',
    'description': """
El socio escanea un QR con la app de ClubPay y confirma; ClubPay decide el
descuento y Odoo aplica lo que contesta. Igual que en NexoPOS:

- Punto de Venta: forma de pago «Cupón ClubPay». El QR sale en pantalla o en la
  ticketera, y la caja pregunta cada 2 segundos hasta que el socio confirma.
- Ventas: el cliente toca «Usar mi descuento ClubPay» en el presupuesto que le
  llegó por mail, o el vendedor le muestra el QR desde el pedido. El descuento
  entra como cupón al facturar.

El descuento no achica la venta: es una forma de pago (DEC-023).

Con «Cobrar con Mercado Pago a través de ClubPay» (DEC-024), el resto se cobra
con el Mercado Pago que el comercio conectó en ClubPay: un QR en la caja y
«Pagar» en el portal. Odoo no guarda credenciales de Mercado Pago.

Con «Libreta en ClubPay» (DEC-025), el contacto tiene DNI y «Enviar invitación».
Si la persona acepta en la app, ve lo que debe (el saldo a cobrar de
Contabilidad) y lo puede pagar con Mercado Pago.
""",
    'category': 'Sales/Point of Sale',
    'author': 'Linware',
    'license': 'LGPL-3',
    'depends': ['nexo_core', 'point_of_sale', 'sale'],
    'data': [
        'security/ir.access.csv',
        'receipt/pos_clubpay_qr_receipt.xml',
        'report/clubpay_qr_report.xml',
        'wizard/nexo_clubpay_sale_qr_views.xml',
        'views/nexo_clubpay_charge_views.xml',
        'views/nexo_clubpay_payment_views.xml',
        'views/sale_order_views.xml',
        'views/sale_portal_templates.xml',
        'views/res_config_settings_views.xml',
        'views/libreta_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'nexo_clubpay/static/src/pos/**/*',
        ],
        'web.assets_frontend': [
            'nexo_clubpay/static/src/portal/**/*',
        ],
    },
    'installable': True,
}
