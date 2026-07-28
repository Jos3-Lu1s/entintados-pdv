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
    'depends': ['base', 'contacts', 'account', 'portal', 'product', 'point_of_sale', 'sale', 'sale_management', 'purchase', 'crm', 'phone_validation'],

    "assets": {
        "point_of_sale._assets_pos": [
            "entintados_pdv/static/src/css/tint.css",
            "entintados_pdv/static/src/js/tint_control_button.js",
            "entintados_pdv/static/src/js/tint_formula_popup.js",
            "entintados_pdv/static/src/xml/tint_control_button.xml",
            "entintados_pdv/static/src/xml/tint_formula_popup.xml",
        ],
        "web.assets_backend": [
            "entintados_pdv/static/src/js/contact_type_selector_field.js",
            "entintados_pdv/static/src/xml/contact_type_selector_field.xml",
        ],
    },

    # always loaded
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/res_partner_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/product_supplierinfo_views.xml',
        'views/account_move_views.xml',
        'views/account_payment_views.xml',
        'views/crm_lead_views.xml',
        'views/crm_lead_to_opportunity_views.xml',
        'views/crm_menu_views.xml',
        'views/account_financial_risk_view.xml',
        'views/portal_templates.xml',
        'views/res_config_risk_view.xml',
        'views/res_partner_risk_view.xml',
        "wizards/partner_risk_exceeded_view.xml",
    ],
    "installable": True,
    "application": False,
}

