{
    'name': 'Nexo · Pedidos de NexoTienda',
    'version': '20.0.1.0.0',
    'summary': 'Los pedidos de NexoTienda se atienden y se cobran en Odoo',
    'description': """
Los pedidos de la NexoTienda del comercio entran a NexoPOS, que sigue siendo su
dueño. Este módulo los trae a Odoo como pedidos de venta, para cobrarlos en el
Punto de Venta, y le avisa a NexoPOS cada paso (aceptado, listo, en camino,
entregado, cancelado). Ver docs/PROPUESTA-caja-odoo-y-nexotienda.md (DEC-016).
""",
    'category': 'Sales/Point of Sale',
    'author': 'Linware',
    'license': 'LGPL-3',
    'depends': ['nexo_pos', 'sale_stock', 'pos_sale_stock'],
    'data': [
        'security/ir.access.csv',
        'data/nexo_tienda_data.xml',
        'data/ir_cron.xml',
        'wizard/nexo_tienda_step_views.xml',
        'views/nexo_tienda_order_views.xml',
        'views/sale_order_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
}
