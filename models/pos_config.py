# -*- coding: utf-8 -*-

from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = 'pos.config'

    tint_default_gallery_id = fields.Many2one(
        'tint.gallery',
        string="Galería de entintado por defecto",
        domain=[('active', '=', True)],
        help="Galería asignada automáticamente al registrar nuevos colores y fórmulas desde este Punto de Venta.",
    )
    sale_price_unit_editable = fields.Boolean(
        related='company_id.sale_price_unit_editable',
        readonly=True,
    )
    sale_discount_editable = fields.Boolean(
        related='company_id.sale_discount_editable',
        readonly=True,
    )
    user_can_edit_price = fields.Boolean(
        string="Puede editar precio de venta",
        compute='_compute_user_sale_permissions',
    )
    user_can_edit_discount = fields.Boolean(
        string="Puede editar descuento en venta",
        compute='_compute_user_sale_permissions',
    )

    def _compute_user_sale_permissions(self):
        can_edit_price = self.env.user.has_group('entintados_pdv.group_sale_edit_price_unit')
        can_edit_discount = self.env.user.has_group('entintados_pdv.group_sale_edit_discount')
        for config in self:
            company = config.company_id or self.env.company
            config.user_can_edit_price = bool(company.sale_price_unit_editable or can_edit_price)
            config.user_can_edit_discount = bool(company.sale_discount_editable or can_edit_discount)

    @api.model
    def _load_pos_data_fields(self, config):
        fields = super()._load_pos_data_fields(config)
        extra_fields = [
            'tint_default_gallery_id',
            'sale_price_unit_editable',
            'sale_discount_editable',
            'user_can_edit_price',
            'user_can_edit_discount',
        ]
        if config and config.module_pos_restaurant:
            extra_fields.remove('tint_default_gallery_id')
        for field_name in extra_fields:
            if fields and field_name not in fields:
                fields.append(field_name)
        return fields

    @api.model
    def _load_pos_data_read(self, records, config):
        read_records = super()._load_pos_data_read(records, config)
        if config and config.module_pos_restaurant:
            for r in read_records:
                r.pop('tint_default_gallery_id', None)
            return read_records
        if read_records:
            record = records.browse(read_records[0]['id'])
            if 'tint_default_gallery_id' not in read_records[0]:
                read_records[0]['tint_default_gallery_id'] = record.tint_default_gallery_id.id or False
            read_records[0]['user_can_edit_price'] = record.user_can_edit_price
            read_records[0]['user_can_edit_discount'] = record.user_can_edit_discount
            read_records[0]['sale_price_unit_editable'] = record.sale_price_unit_editable
            read_records[0]['sale_discount_editable'] = record.sale_discount_editable
        return read_records


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_tint_default_gallery_id = fields.Many2one(
        related='pos_config_id.tint_default_gallery_id',
        readonly=False,
        string="Galería por defecto para colores TPV",
        help="Galería asignada automáticamente al registrar nuevos colores y fórmulas desde este Punto de Venta.",
    )
    pos_module_pos_restaurant = fields.Boolean(
        related='pos_config_id.module_pos_restaurant',
        readonly=False,
    )
