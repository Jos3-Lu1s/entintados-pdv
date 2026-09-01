# -*- coding: utf-8 -*-
{
    'name': "Entintados PDV",

    'summary': "Configurar entintado de pintura desde la caja del Punto de Venta",

    'description': """
Entintado de pintura en el Punto de Venta
=========================================

Permite configurar y vender pintura entintada directamente desde la caja,
sin pasar por cotización ni pedido de venta.

Catálogo de entintado
---------------------
* Tipos de base (White, Medium, Deep, Accent, Neutral, Yellow, Red) con su
  porcentaje de llenado de envase y sus puntos de colorante por litro.
* Presentaciones de envase (Litro, Galón, Cubeta) con su volumen nominal.
* Matriz de capacidad máxima de colorante por tipo de base y presentación,
  precargada con la tabla del fabricante y verificada automáticamente.
* Manejo nativo de la unidad de dispensado: el punto, con la onza como
  unidad derivada (una onza equivale a 48 puntos) y presentación en la
  notación mixta que usa la operación.
* Instrucciones operativas por tipo de base, como la extracción previa del
  10% requerida por las bases de color de línea.
    """,

    'author': "Tekuno",
    'website': "https://www.tekuno.mx",

    # Categories can be used to filter modules in modules listing
    # Check https://github.com/odoo/odoo/blob/19.0/odoo/addons/base/data/ir_module_category_data.xml
    # for the full list
    'category': 'Sales/Point of Sale',
    'version': '19.0.1.0.0',
    'license': 'LGPL-3',

    # any module necessary for this one to work correctly
    'depends': ['base', 'contacts', 'account', 'portal', 'product', 'point_of_sale', 'sale', 'sale_crm', 'sale_management', 'purchase', 'crm', 'phone_validation', 'hr', 'mail', 'calendar','sale_loyalty', 'approvals', 'stock',],

    "assets": {
        "point_of_sale._assets_pos": [
            # Estilos del POS
            "entintados_pdv/static/src/app/style/tint.scss",
            # Componentes reutilizables (js + xml co-localizados)
            "entintados_pdv/static/src/app/components/tint_table/tint_table.js",
            "entintados_pdv/static/src/app/components/tint_table/tint_table.xml",
            "entintados_pdv/static/src/app/components/tint_formula_popup/tint_formula_popup.js",
            "entintados_pdv/static/src/app/components/tint_formula_popup/tint_formula_popup.xml",
            # Modelos del POS
            "entintados_pdv/static/src/app/models/tint_models.js",
            # Utilidades
            "entintados_pdv/static/src/app/utils/tint_points.js",
            "entintados_pdv/static/src/app/utils/tint_order.js",
            "entintados_pdv/static/src/app/utils/tint_flow.js",
            # Pantalla de seleccion de color
            "entintados_pdv/static/src/app/screens/tint_color_screen/tint_color_screen.js",
            "entintados_pdv/static/src/app/screens/tint_color_screen/tint_color_screen.xml",
            "entintados_pdv/static/src/app/screens/tint_color_screen/tint_color_screen.scss",
            # Product screen: panel de entintado y pestanas
            "entintados_pdv/static/src/app/screens/product_screen/tint_panel.js",
            "entintados_pdv/static/src/app/screens/product_screen/tint_panel.xml",
            "entintados_pdv/static/src/app/screens/product_screen/tint_products_widget.js",
            "entintados_pdv/static/src/app/screens/product_screen/tint_products_widget.xml",
            # Overrides (patch) a componentes/servicios del core POS
            "entintados_pdv/static/src/app/overrides/pos_store.js",
            "entintados_pdv/static/src/app/overrides/control_buttons/tint_control_button.js",
            "entintados_pdv/static/src/app/overrides/control_buttons/tint_control_button.xml",
        ],
        "web.assets_backend": [
            # Widget de campo del backend (usado en la vista de res.partner)
            "entintados_pdv/static/src/fields/contact_type_selector/contact_type_selector_field.js",
            "entintados_pdv/static/src/fields/contact_type_selector/contact_type_selector_field.xml",
        ],
    },

    # always loaded
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        # Catálogo de entintado: datos maestros físicos
        'data/uom_data.xml',
        'data/tint_size_data.xml',
        'data/tint_base_type_data.xml',
        'data/tint_base_capacity_data.xml',
        'data/tint_gallery_data.xml',
        'data/mail_activity_type_data.xml',
        'data/type_operations_data.xml',
        'data/hr_departament_data.xml',
        'data/approval_request_data.xml',
        'views/tint_size_views.xml',
        'views/tint_base_type_views.xml',
        'views/tint_base_capacity_views.xml',
        'views/product_template_tint_views.xml',
        'views/tint_gallery_views.xml',
        'views/tint_color_views.xml',
        'views/tint_color_formula_views.xml',
        'views/tint_schema.xml',
        # lines_producto_views.xml debe cargarse antes que tint_menu_views.xml:
        # este último tiene un <menuitem action="lines_product_action"/> y esa
        # acción se define ahí.
        'views/lines_producto_views.xml',
        'views/tint_menu_views.xml',
        # Contactos, ventas, compras y riesgo financiero
        'views/res_partner_views.xml',
        'views/sale_order_views.xml',
        'views/purchase_order_views.xml',
        'views/product_supplierinfo_views.xml',
        'views/account_move_views.xml',
        'views/account_payment_views.xml',
        'views/crm_lead_views.xml',
        'views/crm_lead_to_opportunity_views.xml',
        'views/crm_menu_views.xml',
        'views/crm_stages_view.xml',
        'views/stock_picking_view.xml',
        'views/approval_request_views.xml',
        'views/res_users_views.xml',
        'views/account_financial_risk_view.xml',
        'views/portal_templates.xml',
        'views/res_config_risk_view.xml',
        'views/res_partner_risk_view.xml',
        'views/product_pricelist_item_views.xml',
        'views/product_pricelist_views.xml',
        'report/report_picking_action.xml',
        'report/report_picking_crm.xml',
        'wizards/partner_risk_exceeded_view.xml',
        # Reportes y vistas de actividades
        'views/mail_activity_views.xml',
        'views/mail_activity_menu_views.xml',
        'views/sale_loyalty_reward_wizard_views.xml',
    ],
    'demo': [],
    "installable": True,
    "application": False,
}
