"""Validate static POS template extensions against the actual Odoo templates."""
from copy import deepcopy
from lxml import etree
from odoo.tools.template_inheritance import apply_inheritance_specs
import odoo.addons.base.models.ir_ui_view  # Register Odoo's hasclass XPath helper.

custom = '/mnt/extra-addons/modules/entintados_pdv/static/src/app/overrides/'
native = '/usr/lib/python3/dist-packages/odoo/addons/point_of_sale/static/src/app/'
cases = [
    ('pos_financing.xml', 'entintados_pdv.CreditPaymentAvailability',
     'screens/payment_screen/payment_screen.xml', 'point_of_sale.PaymentScreenMethods'),
    ('advance_controls.xml', 'entintados_pdv.SelectionPopupDisabledItems',
     'components/popups/selection_popup/selection_popup.xml', 'point_of_sale.SelectionPopup'),
]
for filename, name, native_file, parent_name in cases:
    extension = etree.parse(custom + filename).xpath('//t[@t-name=$name]', name=name)[0]
    assert extension.get('t-inherit') == parent_name
    parent = etree.parse(native + native_file).xpath('//t[@t-name=$name]', name=parent_name)[0]
    combined = apply_inheritance_specs(deepcopy(parent), [deepcopy(node) for node in extension])
    if name.endswith('CreditPaymentAvailability'):
        assert combined.xpath('.//div[@t-att-aria-disabled]')
    else:
        assert combined.xpath('.//button[@t-att-disabled="item.disabled"]')
    print('OK:', name, '->', parent_name)
print('Herencias de las plantillas de botones y selección verificadas con Odoo.')
