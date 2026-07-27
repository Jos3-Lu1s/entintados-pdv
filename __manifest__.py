# -*- coding: utf-8 -*-
{
    'name': "Entintados PDV",

    'summary': "Configurar entintado desde la caja del POS",

    'description': """
Long description of module's purpose
    """,

    'author': "My Company",
    'website': "https://www.yourcompany.com",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/15.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Sales',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',

    # any module necessary for this one to work correctly
    'depends': ['base', 'account', 'point_of_sale', 'sale', 'sale_management', 'purchase', 'contacts',],

    "assets": {
        "point_of_sale._assets_pos": [
            "entintados_pdv/static/src/**/*",
        ],
    },

    # always loaded
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/views.xml',
        'views/templates.xml',
        'views/res_partner_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/account_financial_risk_view.xml',
        'views/portal_templates.xml',
        'views/res_config_risk_view.xml',
        'views/res_partner_risk_view.xml',
        "wizards/partner_risk_exceeded_view.xml",

    ],
    # only loaded in demonstration mode
    'demo': [
        'demo/demo.xml',
    ],
    "installable": True,
    "application": False,
}

