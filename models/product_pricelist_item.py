from odoo import api, fields, models


class ProductPricelistItem(models.Model):
    _inherit = 'product.pricelist.item'

    display_applied_on = fields.Selection(
        selection_add=[('3_esquema', 'Esquema')],
        ondelete={'3_esquema': 'set default'},
    )

    esquema_id = fields.Many2one(
        'tint.schema',
        string='Esquema',
        ondelete='restrict',
    )

    linea_id = fields.Many2one(
        'lines.product',
        string='Línea',
        ondelete='restrict',
    )

    @api.onchange('display_applied_on')
    def _onchange_applied_on_esquema(self):
        for item in self:
            if item.display_applied_on != '3_esquema':
                item.esquema_id = False
                item.linea_id = False
                continue
            # El producto o la categoría de antes quedarían ocultos y la búsqueda
            # de reglas del core descartaría la regla para el resto del esquema.
            item.update({
                'applied_on': '3_global',
                'product_tmpl_id': False,
                'product_id': False,
                'categ_id': False,
            })

    @api.onchange('esquema_id')
    def _onchange_esquema_id(self):
        for record in self:
            if record.linea_id and record.linea_id.scheme != record.esquema_id:
                record.linea_id = False

    @api.onchange('display_applied_on', 'esquema_id', 'linea_id')
    def _compute_name(self):
        super()._compute_name()
        for record in self:
            if record.display_applied_on != '3_esquema':
                continue
            if not record.esquema_id:
                record.name = "Sin Esquema"
            elif not record.linea_id:
                record.name = f"Esquema: {record.esquema_id.name}"
            else:
                record.name = f"Esquema: {record.esquema_id.name} - Linea: {record.linea_id.name}"

    # Con '3_global', el create/write del core vacía producto, variante y categoría.
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('display_applied_on') == '3_esquema':
                vals['applied_on'] = '3_global'
        return super().create(vals_list)

    def write(self, vals):
        if 'display_applied_on' in vals:
            scheme_items = self if vals['display_applied_on'] == '3_esquema' else self.browse()
        elif {'applied_on', 'product_tmpl_id', 'product_id', 'categ_id'} & vals.keys():
            scheme_items = self.filtered(lambda item: item.display_applied_on == '3_esquema')
        else:
            scheme_items = self.browse()

        if not scheme_items:
            return super().write(vals)
        others = self - scheme_items
        result = super(ProductPricelistItem, scheme_items).write(dict(vals, applied_on='3_global'))
        if others:
            super(ProductPricelistItem, others).write(vals)
        return result

    def _is_applicable_for(self, product, qty_in_product_uom):
        self.ensure_one()
        product.ensure_one()

        if self.display_applied_on != '3_esquema':
            return super()._is_applicable_for(product, qty_in_product_uom)

        if self.min_quantity and qty_in_product_uom < self.min_quantity:
            return False
        if not product.scheme_id or product.scheme_id != self.esquema_id:
            return False
        if self.linea_id and product.lines_product_id != self.linea_id:
            return False
        return True
