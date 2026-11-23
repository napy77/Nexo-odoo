{
    'name': 'Nexo · NexoPOS y NexoTienda',
    'version': '20.0.1.0.0',
    'summary': 'Precios y stock de Odoo en NexoPOS, que es la base de NexoTienda',
    'category': 'Inventory/Inventory',
    'author': 'Linware',
    'license': 'LGPL-3',
    'depends': ['nexo_core', 'stock', 'account'],
    'data': [
        'security/ir.access.csv',
        'data/ir_cron.xml',
        'views/nexo_pos_views.xml',
        'views/product_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'installable': True,
}
