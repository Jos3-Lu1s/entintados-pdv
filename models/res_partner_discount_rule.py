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
    # En las reglas por línea guarda el esquema de la línea y solo sirve para filtrarla:
    # los acuerdos por esquema se buscan siempre con applied_on == '2_scheme'.
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
    # El precio fijo está en la UdM del producto; en la venta se convierte a la de la línea.
    product_uom_id = fields.Many2one(
        comodel_name='uom.uom',
        string='UdM',
        related='product_id.uom_id',
        readonly=True,
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

    # _check_unique_rules da el mensaje legible; los índices cubren lo que escribe por SQL,
    # como la fusión de contactos.
    _partner_product_uniq = models.UniqueIndex(
        "(partner_id, product_id) WHERE applied_on = '0_product'",
        'Ya existe una regla comercial para este producto en el cliente.',
    )
    _partner_line_uniq = models.UniqueIndex(
        "(partner_id, line_id) WHERE applied_on = '1_line'",
        'Ya existe una regla comercial para esta línea de producto en el cliente.',
    )
    _partner_scheme_uniq = models.UniqueIndex(
        "(partner_id, scheme_id) WHERE applied_on = '2_scheme'",
        'Ya existe una regla comercial para este esquema en el cliente.',
    )

    @api.depends(
        'applied_on', 'rule_type', 'product_id', 'product_id.uom_id', 'line_id', 'scheme_id',
        'discount', 'fixed_price', 'company_id.currency_id',
    )
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
                # Precio en la moneda de la compañía: código ISO, porque `$` es MXN y también USD.
                currency = rule.company_id.currency_id or self.env.company.currency_id
                rule.name = f"{target}: {rule.fixed_price:,.2f} {currency.name} / {rule.product_id.uom_id.name}"
            else:
                rule.name = f"{target}: {rule.discount:.2f}%"

    @api.onchange('line_id')
    def _onchange_line_id(self):
        for rule in self.filtered('line_id'):
            rule.scheme_id = rule.line_id.scheme

    @api.onchange('scheme_id')
    def _onchange_scheme_id(self):
        for rule in self:
            if rule.line_id and rule.line_id.scheme != rule.scheme_id:
                rule.line_id = False

    @api.onchange('applied_on')
    def _onchange_applied_on(self):
        if self.applied_on == '0_product':
            self.line_id = False
            self.scheme_id = False
        elif self.applied_on == '1_line':
            # El esquema se conserva para filtrar las líneas.
            self.product_id = False
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

    _TARGET_FIELDS = {'0_product': 'product_id', '1_line': 'line_id', '2_scheme': 'scheme_id'}

    @api.model_create_multi
    def create(self, vals_list):
        # Los índices únicos rechazan el INSERT antes de que corra la restricción de Python:
        # se valida antes para conservar el mensaje que nombra el objetivo y el cliente.
        candidates = []
        for vals in vals_list:
            applied_on = vals.get('applied_on', '0_product')
            target_field = self._TARGET_FIELDS.get(applied_on)
            candidates.append((vals.get('partner_id'), applied_on, target_field and vals.get(target_field), []))
        self._check_unique_rule_values(candidates)
        rules = super().create(vals_list)
        rules._sync_line_scheme()
        return rules

    def write(self, vals):
        if {'partner_id', 'applied_on', *self._TARGET_FIELDS.values()} & vals.keys():
            candidates = []
            for rule in self:
                applied_on = vals.get('applied_on', rule.applied_on)
                target_field = self._TARGET_FIELDS.get(applied_on)
                candidates.append((
                    vals.get('partner_id', rule.partner_id.id),
                    applied_on,
                    vals.get(target_field, rule[target_field].id) if target_field else False,
                    self.ids,
                ))
            self._check_unique_rule_values(candidates)
        result = super().write(vals)
        if {'applied_on', 'line_id', 'scheme_id'} & vals.keys():
            self._sync_line_scheme()
        return result

    def _sync_line_scheme(self):
        # La línea manda: una regla por línea siempre guarda el esquema de su línea.
        for rule in self.filtered(lambda r: r.applied_on == '1_line' and r.scheme_id != r.line_id.scheme):
            super(ResPartnerDiscountRule, rule).write({'scheme_id': rule.line_id.scheme.id})

    @api.constrains('partner_id', 'applied_on', 'product_id', 'line_id', 'scheme_id')
    def _check_unique_rules(self):
        self._check_unique_rule_values([
            (rule.partner_id.id, rule.applied_on, rule[self._TARGET_FIELDS[rule.applied_on]].id, rule.ids)
            for rule in self
            if rule.applied_on in self._TARGET_FIELDS
        ])

    @api.model
    def _check_unique_rule_values(self, candidates):
        """:param candidates: tuplas (partner_id, applied_on, target_id, ids que no cuentan como duplicado)"""
        seen = set()
        for partner_id, applied_on, target_id, exclude_ids in candidates:
            target_field = self._TARGET_FIELDS.get(applied_on)
            if not partner_id or not target_field or not target_id:
                continue
            key = (partner_id, applied_on, target_id)
            if key in seen or self.search_count([
                ('id', 'not in', exclude_ids),
                ('partner_id', '=', partner_id),
                ('applied_on', '=', applied_on),
                (target_field, '=', target_id),
            ], limit=1):
                self._raise_duplicate_rule(partner_id, applied_on, target_id)
            seen.add(key)

    @api.model
    def _raise_duplicate_rule(self, partner_id, applied_on, target_id):
        partner = self.env['res.partner'].browse(partner_id)
        target = self.env[self._fields[self._TARGET_FIELDS[applied_on]].comodel_name].browse(target_id)
        if applied_on == '0_product':
            raise ValidationError(_(
                'Ya existe una regla comercial para el producto "%(product)s" en el cliente "%(partner)s".',
                product=target.display_name,
                partner=partner.name,
            ))
        if applied_on == '1_line':
            raise ValidationError(_(
                'Ya existe una regla comercial para la línea "%(line)s" en el cliente "%(partner)s".',
                line=target.display_name,
                partner=partner.name,
            ))
        raise ValidationError(_(
            'Ya existe una regla comercial para el esquema "%(scheme)s" en el cliente "%(partner)s".',
            scheme=target.name,
            partner=partner.name,
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
