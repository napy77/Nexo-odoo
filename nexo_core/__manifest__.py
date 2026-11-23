{
    'name': 'Nexo · Núcleo',
    'version': '20.0.1.0.0',
    'summary': 'Credenciales, cliente HTTP, errores y diagnóstico para los conectores del ecosistema Nexo',
    'category': 'Hidden/Tools',
    'author': 'Linware',
    'license': 'LGPL-3',
    'depends': ['base'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'security/ir.access.csv',
        'views/nexo_log_views.xml',
    ],
    'installable': True,
}
