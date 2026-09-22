# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResPartnerDiscountRule(models.Model):
    _name = 'res.partner.discount.rule'
    _description = 'Regla de Acuerdo Comercial de Cliente'
    _order = 'partner_id, applied_on, id'
    _inherit = ['pos.load.mixin']

    partner_id = fields.Many2one(
        comodel_name='res.partner',
        string='Cliente',
        required=True,
        ondelete='cascade',
        index=True,
    )
    applied_on = fields.Selection(
        selection=[
            ('0_product', 'Producto'),
            ('1_line', 'Línea de producto'),
            ('2_scheme', 'Esquema'),
        ],
        string='Aplicar en',
        required=True,
        default='0_product',
    )
    rule_type = fields.Selection(
        selection=[
            ('discount', 'Descuento (%)'),
            ('fixed_price', 'Precio Fijo'),
        ],
        string='Tipo de acuerdo',
        required=True,
        default='discount',
    )
    product_id = fields.Many2one(
        comodel_name='product.product',
        string='Producto',
        index=True,
        ondelete='cascade',
    )
    line_id = fields.Many2one(
        comodel_name='lines.product',
        string='Línea de producto',
        index=True,
        ondelete='cascade',
    )
    scheme_id = fields.Many2one(
        comodel_name='tint.schema',
        string='Esquema',
        index=True,
        ondelete='cascade',
    )
    discount = fields.Float(
        string='Descuento (%)',
        digits=(16, 2),
        default=0.0,
    )
    fixed_price = fields.Float(
        string='Precio Fijo',
        digits='Product Price',
        default=0.0,
    )
    name = fields.Char(
        string='Descripción',
        compute='_compute_name',
        store=True,
    )
    company_id = fields.Many2one(
        comodel_name='res.company',
        string='Compañía',
        related='partner_id.company_id',
        store=True,
        readonly=True,
    )

    @api.depends('applied_on', 'rule_type', 'product_id', 'line_id', 'scheme_id', 'discount', 'fixed_price')
    def _compute_name(self):
        for rule in self:
            target = ""
            if rule.applied_on == '0_product':
                target = rule.product_id.display_name or _("Producto no especificado")
            elif rule.applied_on == '1_line':
                target = rule.line_id.display_name or _("Línea no especificada")
            elif rule.applied_on == '2_scheme':
                target = rule.scheme_id.name or _("Esquema no especificado")

            if rule.rule_type == 'fixed_price':
                rule.name = f"{target}: ${rule.fixed_price:,.2f}"
            else:
                rule.name = f"{target}: {rule.discount:.2f}%"

    @api.onchange('applied_on')
    def _onchange_applied_on(self):
        if self.applied_on == '0_product':
            self.line_id = False
            self.scheme_id = False
        elif self.applied_on == '1_line':
            self.product_id = False
            self.scheme_id = False
            if self.rule_type == 'fixed_price':
                self.rule_type = 'discount'
        elif self.applied_on == '2_scheme':
            self.product_id = False
            self.line_id = False
            if self.rule_type == 'fixed_price':
                self.rule_type = 'discount'

    @api.onchange('rule_type')
    def _onchange_rule_type(self):
        if self.rule_type == 'fixed_price':
            self.applied_on = '0_product'
            self.line_id = False
            self.scheme_id = False
            self.discount = 0.0
        else:
            self.fixed_price = 0.0

    @api.constrains('rule_type', 'applied_on')
    def _check_rule_type_and_applied_on(self):
        for rule in self:
            if rule.rule_type == 'fixed_price' and rule.applied_on != '0_product':
                raise ValidationError(_(
                    'El precio fijo solo puede ser configurado a nivel de Producto.'
                ))

    @api.constrains('applied_on', 'product_id', 'line_id', 'scheme_id')
    def _check_target_defined(self):
        for rule in self:
            if rule.applied_on == '0_product' and not rule.product_id:
                raise ValidationError(_(
                    'Debe seleccionar un producto para la regla comercial.'
                ))
            elif rule.applied_on == '1_line' and not rule.line_id:
                raise ValidationError(_(
                    'Debe seleccionar una línea de producto para la regla comercial.'
                ))
            elif rule.applied_on == '2_scheme' and not rule.scheme_id:
                raise ValidationError(_(
                    'Debe seleccionar un esquema para la regla comercial.'
                ))

    @api.constrains('discount', 'fixed_price', 'rule_type')
    def _check_values(self):
        for rule in self:
            if rule.rule_type == 'discount':
                if rule.discount < 0.0 or rule.discount > 100.0:
                    raise ValidationError(_(
                        'El porcentaje de descuento debe encontrarse entre 0%% y 100%%.'
                    ))
            elif rule.rule_type == 'fixed_price':
                if rule.fixed_price < 0.0:
                    raise ValidationError(_(
                        'El precio fijo no puede ser un valor negativo.'
                    ))

    @api.constrains('partner_id', 'applied_on', 'product_id', 'line_id', 'scheme_id')
    def _check_unique_rules(self):
        for rule in self:
            if not rule.partner_id:
                continue
            if rule.applied_on == '0_product' and rule.product_id:
                duplicate = self.search([
                    ('id', '!=', rule.id),
                    ('partner_id', '=', rule.partner_id.id),
                    ('applied_on', '=', '0_product'),
                    ('product_id', '=', rule.product_id.id),
                ], limit=1)
                if duplicate:
                    raise ValidationError(_(
                        'Ya existe una regla comercial para el producto "%(product)s" en el cliente "%(partner)s".',
                        product=rule.product_id.display_name,
                        partner=rule.partner_id.name,
                    ))
            elif rule.applied_on == '1_line' and rule.line_id:
                duplicate = self.search([
                    ('id', '!=', rule.id),
                    ('partner_id', '=', rule.partner_id.id),
                    ('applied_on', '=', '1_line'),
                    ('line_id', '=', rule.line_id.id),
                ], limit=1)
                if duplicate:
                    raise ValidationError(_(
                        'Ya existe una regla comercial para la línea "%(line)s" en el cliente "%(partner)s".',
                        line=rule.line_id.display_name,
                        partner=rule.partner_id.name,
                    ))
            elif rule.applied_on == '2_scheme' and rule.scheme_id:
                duplicate = self.search([
                    ('id', '!=', rule.id),
                    ('partner_id', '=', rule.partner_id.id),
                    ('applied_on', '=', '2_scheme'),
                    ('scheme_id', '=', rule.scheme_id.id),
                ], limit=1)
                if duplicate:
                    raise ValidationError(_(
                        'Ya existe una regla comercial para el esquema "%(scheme)s" en el cliente "%(partner)s".',
                        scheme=rule.scheme_id.name,
                        partner=rule.partner_id.name,
                    ))

    # --- Carga al POS ---------------------------------------------------

    @api.model
    def _load_pos_data_fields(self, config):
        return [
            'id',
            'partner_id',
            'applied_on',
            'rule_type',
            'product_id',
            'line_id',
            'scheme_id',
            'discount',
            'fixed_price',
        ]

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [('partner_id.active', '=', True)]
