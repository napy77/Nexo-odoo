{
    'name': 'Nexo · NexoB2B',
    'version': '20.0.1.0.1',
    'summary': 'Buscar en el catálogo maestro de NexoB2B y agregar productos con sus mayoristas',
    'category': 'Supply Chain/Purchase',
    'author': 'Linware',
    'license': 'LGPL-3',
    'depends': ['nexo_core', 'purchase'],
    'data': [
        'security/ir.access.csv',
        'views/nexo_b2b_map_views.xml',
        'views/product_views.xml',
        'views/res_partner_views.xml',
        'views/nexo_b2b_catalog_action.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'nexo_b2b/static/src/catalog/*.js',
            'nexo_b2b/static/src/catalog/*.xml',
            'nexo_b2b/static/src/catalog/*.scss',
        ],
    },
    'installable': True,
    'application': True,
}
